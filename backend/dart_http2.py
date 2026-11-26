"""requests adapter backed by the working app's Dart HTTP/2 transport.

Only the local compiled program receives request data over pipes. Python keeps
ownership of cookies and saved sessions; no HTTP listener or session export.
"""
from __future__ import annotations

import base64
from email.message import Message
from io import BytesIO
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlsplit

from requests import exceptions
from requests.adapters import HTTPAdapter
from requests.utils import select_proxy
from urllib3._collections import HTTPHeaderDict
from urllib3.response import HTTPResponse


class DartHttp2Adapter(HTTPAdapter):
    transport_name = 'dart-http2'

    def __init__(self, command):
        super().__init__(max_retries=0)
        self.command = [str(part) for part in command]
        self.process = None
        self.lock = threading.Lock()
        self.replies = queue.Queue()
        self.closed = False

    def _start(self):
        try:
            self.process = subprocess.Popen(self.command, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except OSError:
            raise exceptions.ConnectionError('Dart HTTP/2 transport could not start') from None
        process = self.process
        def read_replies():
            try:
                while line := process.stdout.readline(8 * 1024 * 1024 + 1):
                    if len(line) > 8 * 1024 * 1024:
                        break
                    self.replies.put(line)
            except (OSError, ValueError):
                pass
            finally:
                self.replies.put(None)
        threading.Thread(target=read_replies, daemon=True).start()

    def _stop(self):
        self.closed = True
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
            for pipe in (self.process.stdin, self.process.stdout):
                if pipe is not None:
                    pipe.close()

    def send(self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None):
        if urlsplit(request.url).scheme != 'https':
            raise exceptions.ConnectionError('Dart login transport requires HTTPS', request=request)
        if select_proxy(request.url, proxies):
            raise exceptions.ConnectionError('Dart direct transport cannot bypass a configured proxy', request=request)
        if verify is False or cert is not None or (isinstance(verify, str) and not Path(verify).is_file()):
            raise exceptions.ConnectionError('Unsupported Dart TLS configuration', request=request)
        values = timeout if isinstance(timeout, tuple) else (timeout,)
        seconds = max((value for value in values if isinstance(value, (int, float))), default=45)
        if not 0 < seconds <= 120:
            raise exceptions.ConnectionError('Unsupported Dart request timeout', request=request)
        raw_body = request.body
        if raw_body is not None and not isinstance(raw_body, str):
            if not isinstance(raw_body, bytes):
                raise exceptions.ConnectionError('Unsupported Dart request body', request=request)
        try:
            if isinstance(raw_body, bytes):
                raw_body = raw_body.decode('utf-8')
            pairs = parse_qsl(raw_body or '', keep_blank_values=True, strict_parsing=True)
        except (UnicodeError, ValueError):
            raise exceptions.ConnectionError('Invalid Dart form body', request=request) from None
        if len(dict(pairs)) != len(pairs):
            raise exceptions.ConnectionError('Duplicate form fields are not supported', request=request)
        message = {'url': request.url, 'method': request.method,
                   'body': dict(pairs), 'headers': dict(request.headers),
                   'timeout_ms': int(seconds * 1000),
                   'ca_file': verify if isinstance(verify, str) else None}
        with self.lock:
            if self.closed:
                raise exceptions.ConnectionError('Dart transport was closed; request was not repeated', request=request)
            if self.process is None:
                self._start()
            try:
                self.process.stdin.write(json.dumps(message).encode() + b'\n')
                self.process.stdin.flush()
                line = self.replies.get(timeout=seconds * 2 + 5)
                if line is None:
                    raise ValueError('closed')
                reply = json.loads(line)
                if not reply.get('ok'):
                    kind = reply.get('error')
                    kind = kind if kind in {'tls', 'timeout', 'connection', 'http2', 'format'} else 'transport'
                    raise exceptions.ConnectionError(f'Dart HTTP/2 transport failed ({kind}); request was not repeated', request=request)
                status = reply['status']
                transport = reply['transport']
                if (type(status) is not int or not 200 <= status <= 599 or
                        transport.get('http_version') != '2' or transport.get('alpn') != 'h2'):
                    raise ValueError('protocol')
                content = base64.b64decode(reply['body'], validate=True)
                # Dart already decoded gzip/deflate. Preserve each Set-Cookie
                # separately so requests keeps domain/path/expiry attributes.
                headers = [(key, value) for key, value in reply['headers'].items()
                           if key.lower() not in {'content-encoding', 'content-length', 'set-cookie'}]
                headers.extend(('set-cookie', value) for value in reply['set_cookies'])
                headers.append(('content-length', str(len(content))))
            except exceptions.ConnectionError:
                self._stop()
                raise
            except (OSError, ValueError, TypeError, KeyError, queue.Empty):
                self._stop()
                # Use ConnectionError, not Timeout: the dependency retries its
                # own timeout class even with retry_total=0.
                raise exceptions.ConnectionError('Dart HTTP/2 transport ended; request was not repeated', request=request) from None
        cookie_message = Message()
        for key, value in headers:
            cookie_message[key] = value
        raw = HTTPResponse(body=BytesIO(content), headers=HTTPHeaderDict(headers),
            status=status, version=20, request_method=request.method,
            preload_content=False, decode_content=False,
            original_response=SimpleNamespace(msg=cookie_message, close=lambda: None, isclosed=lambda: True))
        response = self.build_response(request, raw)
        response.ramos_transport = transport
        return response

    def close(self):
        with self.lock:
            self._stop()
        super().close()


def configure_login_transport(client):
    executable = os.environ.get('RAMOS_DART_TRANSPORT')
    if not executable:
        return
    # An explicitly configured proxy remains on the existing curl path. This
    # choice happens before sending anything; there is no fallback after failure.
    if client.private.proxies:
        return
    client.private.mount('https://', DartHttp2Adapter([executable]))
    client.private.max_redirects = 0

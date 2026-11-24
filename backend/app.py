"""Servidor local do Ramos Bot. Senhas e códigos ficam apenas em memória."""
from __future__ import annotations

import argparse
import base64
import hashlib
from collections import deque
import hmac
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie, CookieError
from urllib.parse import urlsplit
import uuid
from storage import read_json as storage_read, write_json

ROOT = Path(__file__).resolve().parent.parent
USERNAME = re.compile(r"[A-Za-z0-9_.]{1,30}")
COMMANDS = {"buscar", "perfil", "seguir", "curtir"}
BUSY = {"connecting", "running", "awaiting_input"}


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def username(value):
    value = str(value or "").strip().lstrip("@")
    if not USERNAME.fullmatch(value):
        raise ApiError("Informe o nome de usuário do Instagram, sem e-mail.")
    return value


def command_payload(body):
    command = body.get("command")
    target = str(body.get("target", "")).strip()
    if command not in COMMANDS or not target or len(target) > 500:
        raise ApiError("Comando ou alvo inválido.")
    limit = body.get("limit", 10)
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ApiError("O limite precisa estar entre 1 e 50.")
    by_id = bool(body.get("by_id", False))
    general = bool(body.get("general", False))
    if by_id and (command not in {"perfil", "seguir"} or not re.fullmatch(r"[1-9][0-9]{0,29}", target)):
        raise ApiError("Use --id com um ID numérico para perfil ou seguir.")
    if general and command != "buscar":
        raise ApiError("Use --geral somente em buscar.")
    if command in {"perfil", "seguir"} and not by_id:
        target = username(target)
    if command == "curtir":
        from instagram_login_apk import media_key
        try:
            media_key(target)
        except Exception:
            raise ApiError("Informe um link de publicação do Instagram ou um ID válido.") from None
    result = {"command": command, "target": target, "limit": limit}
    if by_id: result["by_id"] = True
    if general: result["general"] = True
    return result


def private_json(path, value):
    write_json(path, value)


class RamosService:
    def __init__(self, directory=None, worker_command=None, dependency_check=None):
        self.directory = Path(directory or os.environ.get("RAMOS_DATA_DIR", ROOT / "data")).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.csrf = secrets.token_urlsafe(32)
        self.worker_command = worker_command or [sys.executable, "-u", str(ROOT / "backend" / "worker.py")]
        self.dependency_check = dependency_check or (lambda: importlib.util.find_spec("instagrapi") is not None)
        self.jobs = {}
        self.logs = deque(maxlen=1000)
        self.sequence = 0
        self.state_file = self.directory / "state.json"
        try:
            self.data = storage_read(self.state_file)
        except FileNotFoundError:
            self.data = {"version": 1, "accounts": [], "sessions": [], "macros": [], "proxy": "", "settings": {"two_factor_method": "autenticador"}}
        except (ValueError, OSError):
            raise RuntimeError("Não foi possível abrir data/state.json. Preserve o arquivo e verifique seu conteúdo.") from None
        if self.data.get("version") != 1 or any(not isinstance(self.data.get(key), list) for key in ("accounts", "sessions", "macros")):
            raise RuntimeError("Formato de dados do Ramos Bot não reconhecido.")
        for session in self.data["sessions"]:
            session.update(status="disconnected", authenticated=False, challenge=None)
        self.data["settings"]["two_factor_method"] = "autenticador"
        if not self.data["sessions"]:
            self.data["sessions"].append(self.new_session())
        self.save()
        self.log(None, "info", "Ramos Bot iniciado. Selecione uma sessão para conectar sua conta.")

    @staticmethod
    def new_session(name="Sessão 1"):
        return {"id": str(uuid.uuid4()), "name": name, "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "account_id": None, "status": "disconnected", "authenticated": False, "challenge": None}

    def save(self):
        private_json(self.state_file, self.data)

    def log(self, session_id, tone, message, hidden=()):
        text = str(message)
        for value in hidden:
            if value:
                text = text.replace(str(value), "[oculto]")
        text = re.sub(r"(?i)(password|senha|sessionid|authorization|access_token|enc_password)\s*[=:]\s*\S+", r"\1=[oculto]", text)
        text = "".join(char for char in text if char.isprintable() or char == "\n")[:2000]
        with self.lock:
            self.sequence += 1
            self.logs.append({"id": self.sequence, "session_id": session_id, "time": time.strftime("%H:%M:%S"), "tone": tone if tone in {"info", "success", "warning", "error", "command"} else "info", "message": text})

    def session(self, session_id):
        value = next((item for item in self.data["sessions"] if item["id"] == session_id), None)
        if not value:
            raise ApiError("Sessão não encontrada.", 404)
        return value

    def account(self, account_id):
        value = next((item for item in self.data["accounts"] if item["id"] == account_id), None)
        if not value:
            raise ApiError("Conta não encontrada.", 404)
        return value

    def add_account(self, value):
        handle = username(value)
        with self.lock:
            existing = next((item for item in self.data["accounts"] if item["username"].casefold() == handle.casefold()), None)
            if existing:
                return existing
            if len(self.data["accounts"]) >= 30:
                raise ApiError("O limite de 30 contas foi atingido.")
            item = {"id": str(uuid.uuid4()), "username": handle, "last_connected": None}
            self.data["accounts"].append(item)
            self.save()
            return item

    def snapshot(self):
        with self.lock:
            data = json.loads(json.dumps(self.data))
            for account in data["accounts"]:
                account["has_session"] = (self.directory / "accounts" / account["id"] / "session.json").is_file()
            parsed = urlsplit(data.pop("proxy", ""))
            data["proxy"] = {"enabled": bool(parsed.hostname), "host": parsed.hostname or "", "port": parsed.port}
            data["logs"] = list(self.logs)
            data["csrf"] = self.csrf
            data["engine_ready"] = bool(self.dependency_check())
            data["server_time_ms"] = int(time.time() * 1000)
            return data

    def start(self, session_id, payload):
        with self.lock:
            session = self.session(session_id)
            if session_id in self.jobs:
                raise ApiError("Essa sessão já está executando uma operação.", 409)
            if not self.dependency_check():
                raise ApiError("O módulo de Instagram ainda não está instalado no servidor. Execute a instalação indicada no LEIA-ME.", 503)
            mode = payload.get("mode")
            if mode == "login":
                account = self.add_account(payload.get("username"))
                password = payload.get("password", "")
                if not isinstance(password, str) or len(password) > 1000:
                    raise ApiError("Senha inválida.")
                session_path = self.directory / "accounts" / account["id"] / "session.json"
                if not password and not session_path.is_file():
                    raise ApiError("Informe a senha para conectar esta conta pela primeira vez.")
                steps = []
            elif mode in {"command", "macro"}:
                if not session.get("authenticated"):
                    raise ApiError("Conecte sua conta antes de executar comandos.", 409)
                account = self.account(session["account_id"])
                password = ""
                steps = [command_payload(payload)] if mode == "command" else self.macro(payload.get("macro_id"))["steps"]
            else:
                raise ApiError("Operação inválida.")
            if any(job["account_id"] == account["id"] for job in self.jobs.values()):
                raise ApiError("Essa conta já está ocupada em outra sessão.", 409)
            if mode == "login" and any(item["id"] != session_id and item.get("account_id") == account["id"] and item.get("authenticated") for item in self.data["sessions"]):
                raise ApiError("Essa conta já está conectada em outra sessão.", 409)
            previous = dict(session)
            session.update(account_id=account["id"], status="connecting" if mode == "login" else "running", challenge=None)
            if mode == "login":
                session["authenticated"] = False
            job = {"id": str(uuid.uuid4()), "account_id": account["id"], "process": None, "hidden": [password], "cancelled": False}
            self.jobs[session_id] = job
            worker_payload = {"mode": mode, "username": account["username"], "password": password, "steps": steps,
                              "directory": str(self.directory / "accounts" / account["id"]), "proxy": self.data.get("proxy", ""),
                              "two_factor_method": "autenticador"}
            try:
                process = subprocess.Popen(self.worker_command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                           text=True, encoding="utf-8", bufsize=1, cwd=ROOT / "backend")
                job["process"] = process
                process.stdin.write(json.dumps(worker_payload) + "\n")
                process.stdin.flush()
            except (OSError, BrokenPipeError):
                if job["process"] is not None:
                    job["process"].terminate()
                self.jobs.pop(session_id, None)
                session.update(previous)
                raise ApiError("Não foi possível iniciar o módulo de Instagram.", 503) from None
            worker_payload["password"] = ""
            self.save()
            self.log(session_id, "info", "Inicializando módulo..." if mode == "login" else "Executando " + ("macro" if mode == "macro" else steps[0]["command"]) + "...")
            monitor = threading.Thread(target=self.monitor, args=(session_id, job), daemon=True)
            monitor.start()
            return {"ok": True, "job_id": job["id"]}

    def monitor(self, session_id, job):
        process = job["process"]
        timer = threading.Timer(600, lambda: self.cancel(session_id, timeout=True, expected=job))
        timer.daemon = True
        timer.start()
        completed = False
        try:
            for line in process.stdout:
                if len(line) > 200000:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                with self.lock:
                    if self.jobs.get(session_id) is not job:
                        break
                    session = self.session(session_id)
                    kind = event.get("type")
                    if kind == "log":
                        self.log(session_id, event.get("tone"), event.get("message", ""), job["hidden"])
                    elif kind == "input_required":
                        session["status"] = "awaiting_input"
                        session["challenge"] = {"id": str(uuid.uuid4()), "kind": event.get("kind", "code"), "message": str(event.get("message", "Informe o código solicitado."))[:300]}
                    elif kind == "result":
                        result = event.get("data")
                        if isinstance(result, dict):
                            session["last_result"] = result
                            self.log(session_id, "success", json.dumps(result, ensure_ascii=False), job["hidden"])
                    elif kind == "done":
                        completed = True
                        authenticated = bool(event.get("authenticated"))
                        session.update(authenticated=authenticated, status="connected" if authenticated else "error", challenge=None, last_ok=bool(event.get("ok")))
                        if authenticated:
                            self.account(session["account_id"])["last_connected"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                        self.save()
            process.wait(timeout=5)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            if process.poll() is None:
                process.terminate()
        finally:
            timer.cancel()
            if process.stdin:
                process.stdin.close()
            if process.stdout:
                process.stdout.close()
            with self.lock:
                if self.jobs.get(session_id) is job:
                    self.jobs.pop(session_id, None)
                    session = self.session(session_id)
                    if not completed:
                        session.update(status="error", authenticated=False, challenge=None)
                        self.log(session_id, "error", "O módulo foi encerrado antes de confirmar a operação. Conecte novamente para verificar a sessão.")
                    self.save()
            job["hidden"].clear()

    def cancel(self, session_id, disconnect=False, timeout=False, expected=None):
        with self.lock:
            session = self.session(session_id)
            job = self.jobs.get(session_id)
            if expected is not None and job is not expected:
                return
            self.jobs.pop(session_id, None)
            if job:
                job["cancelled"] = True
                process = job["process"]
                if process is not None and process.poll() is None:
                    process.terminate()
            if disconnect or session["status"] in {"connecting", "awaiting_input"}:
                session["authenticated"] = False
            session.update(status="connected" if session.get("authenticated") else "disconnected", challenge=None)
            self.save()
            self.log(session_id, "warning", "Tempo da operação esgotado." if timeout else "Sessão desconectada." if disconnect else "Operação interrompida. Confira o estado no Instagram antes de repetir uma ação.")

    def submit_input(self, session_id, body):
        with self.lock:
            session = self.session(session_id)
            challenge = session.get("challenge")
            job = self.jobs.get(session_id)
            if not challenge or not job or body.get("challenge_id") != challenge["id"]:
                raise ApiError("Essa verificação já terminou. Atualize a sessão.", 409)
            code = str(body.get("code", "")).replace(" ", "")
            if not re.fullmatch(r"[0-9]{6}" if challenge.get("kind") == "authenticator" else r"[0-9]{6,8}", code):
                raise ApiError("Informe o código numérico de 6 a 8 dígitos.")
            job["hidden"].append(code)
            try:
                job["process"].stdin.write(json.dumps({"code": code}) + "\n")
                job["process"].stdin.flush()
            except (OSError, BrokenPipeError):
                raise ApiError("A verificação foi encerrada. Conecte novamente.", 409) from None
            session.update(status="connecting", challenge=None)

    def macro(self, macro_id):
        item = next((m for m in self.data["macros"] if m["id"] == macro_id), None)
        if not item:
            raise ApiError("Macro não encontrada.", 404)
        return item

    def mutate(self, method, route, body):
        with self.lock:
            if route == ["sessions"] and method == "POST":
                if len(self.data["sessions"]) >= 20:
                    raise ApiError("O limite de 20 sessões foi atingido.")
                names = {s["name"] for s in self.data["sessions"]}
                number = 1
                while f"Sessão {number}" in names:
                    number += 1
                item = self.new_session(f"Sessão {number}")
                self.data["sessions"].append(item)
                self.save()
                return {"ok": True, "session_id": item["id"]}
            if len(route) >= 2 and route[0] == "sessions":
                session_id = route[1]
                self.session(session_id)
                if len(route) == 2 and method == "DELETE":
                    self.cancel(session_id, disconnect=True)
                    self.data["sessions"] = [s for s in self.data["sessions"] if s["id"] != session_id]
                elif len(route) == 3 and method == "POST":
                    action = route[2]
                    if action == "login":
                        return self.start(session_id, {**body, "mode": "login"})
                    if action == "command":
                        return self.start(session_id, {**body, "mode": "command"})
                    if action == "macro":
                        return self.start(session_id, {**body, "mode": "macro"})
                    if action in {"stop", "disconnect"}:
                        self.cancel(session_id, disconnect=action == "disconnect")
                    elif action == "input":
                        self.submit_input(session_id, body)
                    else:
                        raise ApiError("Operação não encontrada.", 404)
                else:
                    raise ApiError("Operação não encontrada.", 404)
            elif route == ["accounts"] and method == "POST":
                account = self.add_account(body.get("username"))
                return {"ok": True, "account_id": account["id"]}
            elif len(route) == 2 and route[0] == "accounts" and method == "DELETE":
                account = self.account(route[1])
                if any(job["account_id"] == account["id"] for job in self.jobs.values()):
                    raise ApiError("Encerre as operações desta conta antes de removê-la.", 409)
                for session in self.data["sessions"]:
                    if session.get("account_id") == account["id"]:
                        session.update(account_id=None, authenticated=False, status="disconnected", challenge=None)
                self.data["accounts"].remove(account)
                session_file = self.directory / "accounts" / account["id"] / "session.json"
                session_file.unlink(missing_ok=True)
            elif route == ["proxy"] and method == "POST":
                if self.jobs:
                    raise ApiError("Aguarde ou encerre as operações antes de alterar o proxy.", 409)
                value = str(body.get("url", "")).strip()
                if body.get("enabled") and not value:
                    value = self.data.get("proxy", "")
                    if not value:
                        raise ApiError("Informe o endereço do proxy.")
                if value and body.get("enabled"):
                    parsed = urlsplit(value)
                    try:
                        valid = parsed.scheme in {"http", "https", "socks5", "socks5h"} and parsed.hostname and parsed.port and parsed.path in {"", "/"} and not parsed.query and not parsed.fragment
                    except ValueError:
                        valid = False
                    if not valid or len(value) > 1000:
                        raise ApiError("Use http://host:porta ou socks5://host:porta.")
                self.data["proxy"] = value if body.get("enabled") else ""
                for session in self.data["sessions"]:
                    session.update(authenticated=False, status="disconnected", challenge=None)
                self.log(None, "info", "Proxy atualizado. Reconecte as contas para aplicar a configuração.")
            elif route == ["settings"] and method == "POST":
                value = body.get("two_factor_method")
                if value != "autenticador":
                    raise ApiError("Método de autenticação inválido.")
                self.data["settings"] = {"two_factor_method": value}
            elif route == ["macros"] and method == "POST":
                name = str(body.get("name", "")).strip()
                steps = body.get("steps")
                if not name or len(name) > 60 or not isinstance(steps, list) or not 1 <= len(steps) <= 20:
                    raise ApiError("Dê um nome à macro e informe de 1 a 20 comandos.")
                if len(self.data["macros"]) >= 30:
                    raise ApiError("O limite de 30 macros foi atingido.")
                self.data["macros"].append({"id": str(uuid.uuid4()), "name": name, "steps": [command_payload(step) for step in steps]})
            elif len(route) == 2 and route[0] == "macros" and method == "DELETE":
                self.data["macros"].remove(self.macro(route[1]))
            elif route == ["logs", "clear"] and method == "POST":
                session_id = body.get("session_id")
                if session_id:
                    self.session(session_id)
                    self.logs = deque((item for item in self.logs if item["session_id"] != session_id), maxlen=1000)
                else:
                    self.logs.clear()
            else:
                raise ApiError("Operação não encontrada.", 404)
            self.save()
            return {"ok": True}

    def close(self):
        with self.lock:
            for session_id in list(self.jobs):
                self.cancel(session_id, disconnect=True)


class RamosHandler(BaseHTTPRequestHandler):
    server_version = "RamosBot/1.0"

    def log_message(self, *_):
        pass

    def allowed_origin(self):
        origin = self.headers.get("Origin")
        same_host = urlsplit(origin or "").netloc == self.headers.get("Host", "")
        return origin is None or origin in self.server.allowed_origins or (bool(os.environ.get("RAMOS_ADMIN_PASSWORD")) and same_host)

    def reply(self, code, value, cookie=None):
        encoded = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(encoded)))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        if self.headers.get("Origin") in self.server.allowed_origins:
            self.send_header("Access-Control-Allow-Origin", self.headers["Origin"])
            self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(encoded)

    def access(self, mutation=False):
        host = urlsplit("http://" + self.headers.get("Host", "")).hostname
        api_key = os.environ.get("RAMOS_API_KEY", "")
        admin_password = os.environ.get("RAMOS_ADMIN_PASSWORD", "")
        panel_valid = False
        if admin_password:
            try:
                cookies = SimpleCookie(self.headers.get("Cookie", ""))
                token = cookies["ramos_access"].value
                expiry, nonce, signature = token.split(".")
                expected = self.access_signature(expiry + "." + nonce)
                panel_valid = int(expiry) > time.time() and hmac.compare_digest(signature, expected)
            except (ValueError, KeyError, CookieError):
                pass
        if api_key:
            if not panel_valid and not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + api_key):
                raise ApiError("Acesso não autorizado.", 401)
        elif admin_password:
            if not panel_valid:
                raise ApiError("Acesso não autorizado.", 401)
        elif host not in {"127.0.0.1", "localhost", "::1"}:
            raise ApiError("Origem não autorizada.", 403)
        if not self.allowed_origin():
            raise ApiError("Origem não autorizada.", 403)
        if mutation and not hmac.compare_digest(self.headers.get("X-Ramos-CSRF", ""), self.server.service.csrf):
            raise ApiError("Atualize a página antes de continuar.", 403)

    def access_signature(self, payload):
        key = (os.environ.get("RAMOS_ADMIN_PASSWORD", "") + os.environ.get("RAMOS_ENCRYPTION_KEY", "")).encode()
        return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()

    def panel_login(self):
        try:
            if not self.allowed_origin():
                raise ApiError("Origem não autorizada.", 403)
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                raise ApiError("Envie os dados no formato JSON.", 415)
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 4096:
                raise ApiError("Requisição inválida.")
            body = json.loads(self.rfile.read(length))
            expected = os.environ.get("RAMOS_ADMIN_PASSWORD", "")
            supplied = body.get("password", "") if isinstance(body, dict) else ""
            if not expected or not isinstance(supplied, str) or not hmac.compare_digest(supplied.encode(), expected.encode()):
                raise ApiError("Senha do painel inválida.", 401)
            payload = str(int(time.time()) + 43200) + "." + secrets.token_urlsafe(18)
            token = payload + "." + self.access_signature(payload)
            secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" or os.environ.get("RAMOS_HOST") == "0.0.0.0" else ""
            self.reply(200, {"ok": True}, cookie="ramos_access=" + token + "; Path=/; HttpOnly; SameSite=Strict; Max-Age=43200" + secure)
        except ApiError as error:
            self.reply(error.status, {"error": str(error), "locked": True})
        except (ValueError, UnicodeError):
            self.reply(400, {"error": "Requisição inválida.", "locked": True})

    def do_OPTIONS(self):
        if not self.allowed_origin():
            return self.reply(403, {"error": "Origem não autorizada."})
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", self.headers.get("Origin", "http://127.0.0.1:8765"))
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Ramos-CSRF")
        self.end_headers()

    def do_GET(self):
        try:
            if urlsplit(self.path).path == "/api/health":
                return self.reply(200, {"ok": True})
            path = urlsplit(self.path).path
            if path.startswith("/api/") or not os.environ.get("RAMOS_ADMIN_PASSWORD"):
                self.access()
            if path == "/api/state":
                return self.reply(200, self.server.service.snapshot())
            if path.startswith("/api/diagnostic/"):
                session = self.server.service.session(path.rsplit("/", 1)[-1])
                if not session.get("account_id"):
                    raise ApiError("Nenhuma tentativa nesta sessão.", 404)
                diagnostic = self.server.service.directory / "accounts" / session["account_id"] / "diagnostic.json"
                if not diagnostic.is_file():
                    raise ApiError("O diagnóstico ficará disponível ao concluir a tentativa.", 404)
                return self.reply(200, storage_read(diagnostic))
            if path.startswith("/api/"):
                raise ApiError("Operação não encontrada.", 404)
            dist = ROOT / "dist" / "client"
            requested = (dist / path.lstrip("/")).resolve()
            if not requested.is_relative_to(dist.resolve()):
                raise ApiError("Arquivo não encontrado.", 404)
            if not requested.is_file():
                requested = dist / "index.html"
            if not requested.is_file():
                raise ApiError("A interface ainda não foi compilada. Execute npm run build ou use o servidor de desenvolvimento.", 503)
            import mimetypes
            data = requested.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(requested.name)[0] or "application/octet-stream")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except ApiError as error:
            self.reply(error.status, {"error": str(error), "locked": error.status == 401 and bool(os.environ.get("RAMOS_ADMIN_PASSWORD"))})
        except (OSError, ValueError):
            self.reply(500, {"error": "Não foi possível ler os dados do servidor."})

    def mutation(self, method):
        try:
            self.access(mutation=True)
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                raise ApiError("Envie os dados no formato JSON.", 415)
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                raise ApiError("Tamanho da requisição inválido.") from None
            if not 0 < length <= 32000:
                raise ApiError("Requisição vazia ou muito grande.", 413)
            try:
                body = json.loads(self.rfile.read(length))
            except (ValueError, UnicodeDecodeError):
                raise ApiError("JSON inválido.") from None
            if not isinstance(body, dict):
                raise ApiError("Envie um objeto JSON.")
            path = urlsplit(self.path).path
            if not path.startswith("/api/"):
                raise ApiError("Operação não encontrada.", 404)
            result = self.server.service.mutate(method, path[5:].strip("/").split("/"), body)
            self.reply(200, result)
        except ApiError as error:
            self.reply(error.status, {"error": str(error)})
        except (OSError, ValueError):
            self.reply(500, {"error": "Não foi possível salvar esta alteração."})

    def do_POST(self):
        if urlsplit(self.path).path == "/api/access/login":
            return self.panel_login()
        self.mutation("POST")

    def do_DELETE(self):
        self.mutation("DELETE")


def make_server(service, port=8765):
    server = ThreadingHTTPServer((os.environ.get("RAMOS_HOST", "127.0.0.1"), port), RamosHandler)
    server.daemon_threads = True
    server.service = service
    actual_port = server.server_address[1]
    server.allowed_origins = {f"http://{host}:{number}" for host in ("localhost", "127.0.0.1") for number in (actual_port, 5178, 4178, 5173, 4173)}
    return server


def main():
    parser = argparse.ArgumentParser(description="Servidor local do Ramos Bot")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8765")))
    args = parser.parse_args()
    if (os.environ.get("RAMOS_API_KEY") or os.environ.get("RAMOS_ADMIN_PASSWORD")) and not os.environ.get("RAMOS_ENCRYPTION_KEY"):
        raise RuntimeError("Configure RAMOS_ENCRYPTION_KEY antes de publicar o servidor.")
    if os.environ.get("RAMOS_HOST", "127.0.0.1") not in {"127.0.0.1", "localhost", "::1"} and not (os.environ.get("RAMOS_API_KEY") or os.environ.get("RAMOS_ADMIN_PASSWORD")):
        raise RuntimeError("Configure RAMOS_ADMIN_PASSWORD para expor o painel com autenticação.")
    service = RamosService()
    server = make_server(service, args.port)
    print(f"Ramos Bot: http://127.0.0.1:{server.server_address[1]}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        service.close()
        server.server_close()


if __name__ == "__main__":
    main()

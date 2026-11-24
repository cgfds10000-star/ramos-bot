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
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
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


class NoViralizeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def viralize_http_error(error):
    with error:
        status = error.code
        headers = error.headers or {}
        challenge = headers.get("cf-mitigated", "").lower() == "challenge"
        try:
            raw = error.read(16385)
            payload = json.loads(raw) if len(raw) <= 16384 else None
        except (OSError, ValueError):
            payload = None
    edge = isinstance(payload, dict) and payload.get("cloudflare_error") is True
    if edge or challenge:
        code = str(payload.get("error_code", "")) if edge else ""
        reference = f" (Cloudflare {code})" if re.fullmatch(r"1[0-9]{3}", code) else " (Cloudflare)"
        ray = str(payload.get("ray_id") or "") if edge else ""
        ray = ray or headers.get("cf-ray", "")
        ray_info = f" ID da requisição: {ray}." if re.fullmatch(r"[A-Fa-f0-9]{16,32}(?:-[A-Z0-9]{3,8})?", ray) else ""
        return f"A proteção do Viralize bloqueou a conexão do Ramos Bot{reference}. Isso não confirma suspensão da sua conta. Peça ao suporte do Viralize para verificar o acesso da API.{ray_info}"
    return {
        401: "O token do Viralize não foi aceito.",
        403: "O Viralize recusou a chamada da API (HTTP 403). O motivo da recusa não foi confirmado.",
        404: "O Viralize não encontrou a conta, tarefa ou ação.",
        422: "O Viralize não aceitou o estado ou os parâmetros da operação.",
        429: "Limite do Viralize atingido. Aguarde antes de iniciar outra rodada.",
    }.get(status, f"O Viralize respondeu com HTTP {status}.")


class ViralizeApi:
    def __init__(self, token):
        self.token = token
        self.opener = build_opener(NoViralizeRedirect())

    def request(self, endpoint, fields, *, write=False):
        base = "https://viralizesociais.com/api/" + endpoint
        url = base if write else base + "?" + urlencode(fields)
        body = json.dumps(fields).encode() if write else None
        headers = {"Authorization": "Bearer " + self.token, "Accept": "application/json"}
        if write:
            headers["Content-Type"] = "application/json"
        request = Request(url, data=body, headers=headers, method="POST" if write else "GET")
        try:
            with self.opener.open(request, timeout=8) as response:
                raw = response.read(262145)
            payload = json.loads(raw) if len(raw) <= 262144 else None
        except HTTPError as error:
            raise ApiError(viralize_http_error(error), 502) from None
        except (URLError, OSError):
            raise ApiError("Sem resposta do Viralize. Confira a fila antes de repetir.", 502) from None
        except ValueError:
            raise ApiError("O Viralize devolveu uma resposta inválida.", 502) from None
        if not isinstance(payload, dict) or payload.get("success") is False:
            raise ApiError("O Viralize não confirmou a operação.", 502)
        nested = payload.get("data")
        value = {**payload, **nested} if isinstance(nested, dict) else payload
        if value.get("success") is False or (write and value.get("success") is not True):
            raise ApiError("O Viralize não confirmou a operação.", 502)
        return value


def viralize_id(value):
    if isinstance(value, bool) or not re.fullmatch(r"[1-9][0-9]{0,19}", str(value)):
        raise ApiError("O Viralize não informou um identificador válido.", 502)
    return int(value)


def viralize_offer(queue):
    state = queue.get("state")
    if state == "empty":
        return None, None
    completion = queue.get("completion") if state == "active" else None
    if state == "active" and isinstance(completion, dict):
        task = completion.get("task")
    elif state == "available":
        task = queue.get("task")
    else:
        raise ApiError("O Viralize retornou um estado de fila desconhecido.", 502)
    if not isinstance(task, dict) or type(task.get("is_refill", False)) is not bool:
        raise ApiError("O Viralize devolveu uma tarefa incompleta.", 502)
    offer = {"task_id": viralize_id(task.get("id")), "is_refill": task.get("is_refill", False), "state": state}
    if completion is not None:
        offer["completion_id"] = viralize_id(completion.get("id") or completion.get("completion_id") or queue.get("completion_id"))
        offer["completion_state"] = completion.get("state") or completion.get("status") or "pending"
    return offer, task


def viralize_step(task):
    kind = task.get("type") or task.get("task_type") or task.get("action_type")
    if kind not in ("follow", "follower", "followers", "like", "likes"):
        return None
    target = task.get("action_url") or task.get("instagram_url")
    if not target and kind in ("follow", "follower", "followers"):
        target = task.get("instagram_username")
        if isinstance(target, str) and USERNAME.fullmatch(target.removeprefix("@")):
            return command_payload({"command": "seguir", "target": target})
        return None
    if not isinstance(target, str) or len(target) > 500:
        return None
    try:
        parsed = urlsplit(target)
        if (parsed.scheme != "https" or parsed.hostname not in {"instagram.com", "www.instagram.com"}
                or parsed.port not in (None, 443) or parsed.username is not None):
            return None
        parts = parsed.path.strip("/").split("/")
        if kind in ("follow", "follower", "followers"):
            if len(parts) == 2 and parts[0] == "_u":
                parts = parts[1:]
            if len(parts) != 1 or parts[0] in {"p", "reel", "tv", "accounts", "explore", "stories"}:
                return None
            return command_payload({"command": "seguir", "target": parts[0]})
        if len(parts) != 2 or parts[0] not in {"p", "reel", "tv"}:
            return None
        return command_payload({"command": "curtir", "target": f"https://www.instagram.com/{parts[0]}/{parts[1]}/"})
    except (ApiError, ValueError):
        return None


class RamosService:
    def __init__(self, directory=None, worker_command=None, dependency_check=None, viralize_api=ViralizeApi):
        self.directory = Path(directory or os.environ.get("RAMOS_DATA_DIR", ROOT / "data")).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.csrf = secrets.token_urlsafe(32)
        self.worker_command = worker_command or [sys.executable, "-u", str(ROOT / "backend" / "worker.py")]
        self.dependency_check = dependency_check or (lambda: importlib.util.find_spec("instagrapi") is not None)
        self.jobs = {}
        self.viralize_api = viralize_api
        self.viralize_runs = {}
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
            data.pop("viralize_attempts", None)
            data["viralize"] = {session_id: {
                "status": run["status"], "message": run["message"], "can_skip": run["can_skip"]
            } for session_id, run in self.viralize_runs.items()}
            for account in data["accounts"]:
                account["has_session"] = (self.directory / "accounts" / account["id"] / "session.json").is_file()
            parsed = urlsplit(data.pop("proxy", ""))
            data["proxy"] = {"enabled": bool(parsed.hostname), "host": parsed.hostname or "", "port": parsed.port}
            data["logs"] = list(self.logs)
            data["csrf"] = self.csrf
            data["engine_ready"] = bool(self.dependency_check())
            data["server_time_ms"] = int(time.time() * 1000)
            return data

    def start(self, session_id, payload, viralize_run=None):
        with self.lock:
            session = self.session(session_id)
            run = self.viralize_runs.get(session_id)
            if run and run["status"] == "running" and run is not viralize_run:
                raise ApiError("A rodada do Viralize ainda está em andamento nesta sessão.", 409)
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
            job = {"id": str(uuid.uuid4()), "account_id": account["id"], "process": None, "hidden": [password], "cancelled": False,
                   "finished": threading.Event(), "result": None, "success": False}
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
                            job["result"] = result
                            session["last_result"] = result
                            self.log(session_id, "success", json.dumps(result, ensure_ascii=False), job["hidden"])
                    elif kind == "done":
                        completed = True
                        authenticated = bool(event.get("authenticated"))
                        job["success"] = event.get("ok") is True and authenticated
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
            job["finished"].set()

    def cancel(self, session_id, disconnect=False, timeout=False, expected=None):
        with self.lock:
            session = self.session(session_id)
            job = self.jobs.get(session_id)
            if expected is not None and job is not expected:
                return
            run = self.viralize_runs.get(session_id)
            if run and run["status"] == "running":
                run["stop"] = True
            self.jobs.pop(session_id, None)
            if job:
                job["cancelled"] = True
                process = job["process"]
                if process is not None and process.poll() is None:
                    process.terminate()
                job["finished"].set()
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

    def viralize_action(self, session_id, action, body):
        token = body.get("token")
        if not isinstance(token, str) or not re.fullmatch(r"vzs_[A-Za-z0-9_-]{1,480}", token):
            raise ApiError("Informe um token do Viralize no formato vzs_…")
        with self.lock:
            session = self.session(session_id)
            if not session.get("authenticated"):
                raise ApiError("Conecte a conta do Instagram nesta sessão antes de usar o Viralize.", 409)
            previous = self.viralize_runs.get(session_id)
            if session_id in self.jobs or (previous and previous["status"] == "running"):
                raise ApiError("Já há uma operação em andamento nesta sessão.", 409)
            account = self.account(session["account_id"])
            fields = {"social": "instagram", "username": account["username"]}
            if action != "link":
                task_type = body.get("type", "follow")
                if task_type not in ("follow", "like", "follow,like"):
                    raise ApiError("Selecione tarefas de seguir ou curtir.")
                skip_offer = None
                if action == "skip":
                    if not previous or not previous["can_skip"] or previous["account_id"] != account["id"]:
                        raise ApiError("Não há uma tarefa selecionada para pular.", 409)
                    skip_offer, task_type = previous["offer"], previous["type"]
                run = {"account_id": account["id"], "type": task_type, "status": "running", "stop": False,
                       "message": "Consultando a fila do Viralize…", "can_skip": False, "offer": None}
                self.viralize_runs[session_id] = run
                self.log(session_id, "info", run["message"])
                thread = threading.Thread(target=self.run_viralize, args=(session_id, run, token, fields, skip_offer), daemon=True)
                thread.start()
                return {"ok": True}
        self.viralize_api(token).request("account/ensure", fields, write=True)
        return {"ok": True}

    def viralize_update(self, session_id, run, status, message, can_skip=False):
        with self.lock:
            run.update(status=status, message=message, can_skip=can_skip)
            if self.viralize_runs.get(session_id) is run:
                self.log(session_id, "error" if status == "error" else "info", message)

    def viralize_require_current(self, session_id, run):
        if run["stop"] or self.viralize_runs.get(session_id) is not run:
            raise ApiError("A rodada foi interrompida.", 409)
        session = self.session(session_id)
        if not session.get("authenticated") or session.get("account_id") != run["account_id"]:
            raise ApiError("A conta da sessão mudou. Nenhuma ação adicional será enviada.", 409)

    def run_viralize(self, session_id, run, token, fields, skip_offer):
        def update(status, message, can_skip=False):
            self.viralize_update(session_id, run, status, message, can_skip)

        try:
            api = self.viralize_api(token)
            queue = api.request("task", {**fields, "type": run["type"]})
            with self.lock:
                self.viralize_require_current(session_id, run)
            offer, task = viralize_offer(queue)
            if offer is None:
                update("empty", "Fila vazia. Inicie outra rodada quando quiser consultar de novo.")
                return
            run["offer"] = offer
            if skip_offer is not None:
                if offer != skip_offer:
                    raise ApiError("A tarefa ou seu estado mudou. Nada foi pulado.", 409)
                endpoint = "task/skip-browse" if offer["state"] == "available" else "task/skip"
                api.request(endpoint, {**fields, "task_id": offer["task_id"], "is_refill": offer["is_refill"]}, write=True)
                update("done", "Tarefa pulada no Viralize.")
                return
            if offer.get("completion_state", "pending") != "pending":
                update("attention", "A ação existente não está pendente. Não foi executada novamente.")
                return
            step = viralize_step(task)
            if step is None or (step["command"] == "seguir" and step["target"].casefold() == fields["username"].casefold()):
                update("attention", "Tarefa incompatível com esta sessão do Instagram. Você pode pulá-la.", True)
                return
            with self.lock:
                self.viralize_require_current(session_id, run)
                if not self.dependency_check():
                    raise ApiError("O módulo Python do Instagram ainda não está instalado.", 503)
            if offer["state"] == "available":
                update("running", "Aceitando a tarefa no Viralize…")
                accepted = api.request("task/accept", {**fields, "task_id": offer["task_id"], "is_refill": offer["is_refill"]}, write=True)
                completion = accepted.get("completion")
                completion_id = viralize_id(accepted.get("completion_id") or (completion.get("id") if isinstance(completion, dict) else None))
                offer.update(state="active", completion_id=completion_id, completion_state="pending")
            with self.lock:
                self.viralize_require_current(session_id, run)
                key = f'{offer["task_id"]}:{offer["completion_id"]}:{int(offer["is_refill"])}'
                attempts = self.data.setdefault("viralize_attempts", {}).setdefault(run["account_id"], [])
                if key in attempts:
                    update("attention", "Esta ação já foi tentada pelo Ramos Bot. Confira o resultado; não será repetida.", True)
                    return
                attempts.append(key)
                del attempts[:-500]
                self.save()
                self.start(session_id, {**step, "mode": "command"}, viralize_run=run)
                job = self.jobs[session_id]
            update("running", "Executando o alvo da tarefa no Instagram…")
            if not job["finished"].wait(610):
                raise ApiError("O executor do Instagram não confirmou o fim da ação.", 504)
            with self.lock:
                self.viralize_require_current(session_id, run)
            expected = "seguindo" if step["command"] == "seguir" else "curtida"
            if not job["success"] or (job["result"] or {}).get("estado") != expected:
                update("attention", "A ação não foi confirmada como executada. Nada foi enviado para análise.", True)
                return
            update("running", "Ação confirmada pelo Instagram. Enviando para análise…")
            api.request("task/confirm-analysis", {**fields, "completion_id": offer["completion_id"], "is_refill": offer["is_refill"]}, write=True)
            update("done", "Enviada para análise no Viralize. O crédito depende da validação.")
        except ApiError as error:
            update("stopped" if run["stop"] else "error", str(error))
        except Exception:
            update("error", "A rodada não foi concluída. Confira a fila no Viralize antes de repetir.")

    def mutate(self, method, route, body):
        if method == "POST" and len(route) == 4 and route[0] == "sessions" and route[2] == "viralize" and route[3] in {"link", "start", "skip"}:
            return self.viralize_action(route[1], route[3], body)
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
                    self.viralize_runs.pop(session_id, None)
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
            for run in self.viralize_runs.values():
                run["stop"] = True
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

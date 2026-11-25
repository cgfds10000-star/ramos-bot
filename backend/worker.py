"""Um processo por operação; entrada JSON, eventos JSON e nenhuma senha em disco."""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sys
import time
from storage import read_json, write_json
from network import apply_instagram_proxy

OUTPUT = sys.stdout


def emit(event_type, **values):
    OUTPUT.write(json.dumps({"type": event_type, **values}, ensure_ascii=False) + "\n")
    OUTPUT.flush()


def ask_code(kind="code", message="Informe o código enviado pelo Instagram."):
    emit("input_required", kind=kind, message=message)
    line = sys.stdin.readline()
    if not line:
        raise EOFError("Verificação interrompida.")
    value = json.loads(line).get("code", "")
    if not isinstance(value, str) or not value.isdigit() or not (len(value) == 6 if kind == "authenticator" else 6 <= len(value) <= 8):
        raise ValueError("Código inválido.")
    return value


class EventConsole:
    def info(self, value):
        emit("log", tone="info", message=str(value))

    def success(self, value):
        emit("log", tone="success", message=str(value))

    def warning(self, value):
        emit("log", tone="warning", message=str(value))

    def error(self, value, **_):
        emit("log", tone="error", message=str(value))

    def detail(self, _):
        pass

    def section(self, value):
        self.info(value)

    def field(self, label, value):
        self.info(str(label) + ": " + str(value))


ERRORS = {
    "BadPassword": "O Instagram recusou a senha. Confira os dados e tente novamente.",
    "BadCredentials": "O Instagram recusou os dados de acesso.",
    "LoginRequired": "A sessão expirou. Conecte a conta novamente.",
    "ClientUnauthorizedError": "A sessão foi recusada pelo Instagram. Conecte novamente.",
    "PleaseWaitFewMinutes": "O Instagram pediu uma pausa. Aguarde antes de tentar novamente.",
    "FeedbackRequired": "O Instagram restringiu esta ação. Confira sua conta no aplicativo.",
    "ChallengeRequired": "O Instagram pediu uma verificação. Abra o aplicativo para concluir e depois conecte novamente.",
    "ChallengeUnknownStep": "Conclua a verificação solicitada no aplicativo do Instagram.",
    "ChallengeSelfieCaptcha": "Conclua a verificação de identidade no aplicativo do Instagram.",
    "RecaptchaChallengeForm": "Conclua o desafio no aplicativo do Instagram.",
    "TwoFactorRequired": "A verificação não foi concluída. Confira o método de autenticação nas configurações.",
    "UserNotFound": "Perfil não encontrado.",
    "MediaNotFound": "Publicação não encontrada ou indisponível para esta conta.",
}


def run(payload):
    import instagram_login_apk as core

    core.CONSOLE = EventConsole()
    core.read_json = read_json
    core.save_private_json = write_json
    core.read_authenticator_code = lambda: ask_code("authenticator", "Informe os 6 dígitos do aplicativo autenticador.")
    core.read_two_factor_code = lambda: ask_code("two_factor", "Digite o código do autenticador/SMS ou o código de backup solicitado pelo Instagram.")
    core.read_verification_code = lambda *_: ask_code()
    directory = Path(payload["directory"]).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    session_path = directory / "session.json"
    client = None
    authenticated = False
    password = payload.pop("password", "")
    try:
        # load_settings da biblioteca usa JSON puro; a sessão é descriptografada
        # em memória e set_settings restaura o mesmo perfil Android.
        client = core.configure_client(core.load_client_class(), directory / "new-session-unused.json", directory / "legacy-unused.json", 30)
        if session_path.is_file():
            settings = read_json(session_path)
            client.set_settings(settings)
            client._local_login_username = settings.get("_local_login_username")
        client.challenge_code_handler = core.read_verification_code
        client.two_factor_method = "auto"
        apply_instagram_proxy(client, payload.get("proxy", ""))
        handle = core.valid_username(payload["username"])
        owner = getattr(client, "_local_login_username", None)
        if owner and owner.casefold() != handle.casefold():
            raise core.FlowError("A sessão salva pertence a outra conta.")
        if payload["mode"] == "login":
            if client.user_id and (client.authorization or client.sessionid):
                try:
                    core.validate_action_session(client, handle)
                    authenticated = True
                    emit("log", tone="success", message="Sessão salva validada pelo Instagram.")
                except Exception:
                    if not password:
                        emit("log", tone="warning", message="A sessão salva não pôde ser validada. Informe a senha para conectar novamente.")
                        emit("done", ok=False, authenticated=False)
                        return
            if not authenticated:
                if not password:
                    raise core.FlowError("Informe a senha para iniciar esta sessão.")
                client._local_login_username = handle
                core.save_client(client, session_path)
                emit("log", tone="info", message="Autenticando conta...")
                from instagrapi.exceptions import TwoFactorRequired
                try:
                    accepted = client.login(handle, password)
                except TwoFactorRequired as error:
                    accepted = core.continue_two_factor(client, error)
                if not accepted or not client.user_id or not (client.authorization or client.sessionid):
                    raise core.FlowError("O Instagram ainda não confirmou esta sessão. Consulte o diagnóstico da tentativa.")
                client.action_session_validated = False
                core.validate_action_session(client, handle)
                authenticated = True
            core.save_client(client, session_path)
            emit("log", tone="success", message="Login realizado com sucesso.")
            emit("log", tone="success", message="Sessão conectada.")
        else:
            if not owner:
                raise core.FlowError("Conecte sua conta antes de executar comandos.")
            client.password = None
            client.action_session_validated = False
            core.validate_action_session(client, handle)
            authenticated = True
            for index, step in enumerate(payload["steps"]):
                if index:
                    time.sleep(2)
                command, target = step["command"], step["target"]
                emit("log", tone="command", message=command + " " + target)
                if command == "buscar":
                    result = core.search_profiles(client, target, step.get("limit", 10), step.get("general", False))
                elif command == "perfil":
                    result = core.get_profile(client, user_id=target) if step.get("by_id") else core.get_profile(client, username=target)
                elif command == "seguir":
                    target_id = target if step.get("by_id") else core.get_profile(client, username=target)["id"]
                    result = core.follow_profile(client, target_id)
                elif command == "curtir":
                    result = core.like_media(client, target)
                else:
                    raise core.FlowError("Comando não permitido.")
                core.save_client(client, session_path)
                emit("result", data=result)
            emit("log", tone="success", message="Operação concluída.")
        emit("done", ok=True, authenticated=authenticated)
    except Exception as error:
        name = type(error).__name__
        if name in {"LoginRequired", "ClientUnauthorizedError"}:
            authenticated = False
        message = ERRORS.get(name, "Não foi possível concluir a operação. Confira sua conexão e o estado da conta no Instagram.")
        if isinstance(error, core.FlowError):
            message = str(error)
        emit("log", tone="error", message=message)
        emit("done", ok=False, authenticated=authenticated)
    finally:
        password = ""
        if client is not None:
            client.password = None
            try:
                core.save_client(client, session_path)
                core.save_private_json(directory / "diagnostic.json", core.safe_diagnostic(client, "autenticado" if authenticated else "recusado"))
            except OSError:
                emit("log", tone="error", message="Não foi possível salvar a sessão no servidor.")


def main():
    try:
        payload = json.loads(sys.stdin.readline())
        # Saída arbitrária de dependências nunca vai para o terminal web.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            run(payload)
    except Exception:
        emit("log", tone="error", message="Não foi possível carregar o módulo de Instagram. Confira a instalação no servidor.")
        emit("done", ok=False, authenticated=False)


if __name__ == "__main__":
    main()

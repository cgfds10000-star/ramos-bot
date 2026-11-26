"""Um processo por operação; entrada JSON, eventos JSON e nenhuma senha em disco."""
from __future__ import annotations

import contextlib
import io
import json
import re
from pathlib import Path
import sys
import time
from storage import read_json, write_json
from network import apply_instagram_proxy
from login_diagnostics import runtime_diagnostic

OUTPUT = sys.stdout


def emit(event_type, **values):
    OUTPUT.write(json.dumps({"type": event_type, **values}, ensure_ascii=False) + "\n")
    OUTPUT.flush()


def ask_code(kind="code", message="Informe o código enviado pelo Instagram."):
    """Pedir o código pelo painel, aceitando nova tentativa em vez de derrubar o login."""
    for _ in range(3):
        emit("input_required", kind=kind, message=message)
        line = sys.stdin.readline()
        if not line:
            raise EOFError("Verificação interrompida.")
        try:
            payload = json.loads(line)
        except ValueError:
            payload = {}
        raw = payload.get("secret", payload.get("code", "")) if isinstance(payload, dict) else ""
        if not isinstance(raw, str):
            raw = ""
        if isinstance(payload, dict) and "secret" in payload:
            if kind not in {"authenticator", "two_factor"}:
                message = "Este metodo exige o codigo recebido por SMS ou e-mail."
                continue
            from instagram_login_apk import parse_two_factor_input
            try:
                _, secret = parse_two_factor_input(raw, allow_backup=False)
            except ValueError:
                secret = None
            except Exception:
                secret = None
            if secret:
                return secret
            message = "Chave Base32 invalida. Confira a chave ou digite o codigo numerico."
            continue
        value = re.sub(r"[\s-]+", "", raw)
        if kind == "authenticator":
            if re.fullmatch(r"[0-9]{6}", value):
                return value
            message = "O autenticador precisa de exatamente 6 dígitos. Digite novamente."
        elif re.fullmatch(r"(?:[0-9]{6}|[0-9]{8})", value):
            return value
        else:
            message = "Código inválido. Digite 6 números ou um backup de 8 números."
    raise ValueError("Código inválido.")


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


class _WebGetpass:
    """Adaptar input/getpass para eventos web sem substituir o fluxo do nucleo."""

    def __init__(self, initial_code=""):
        self.initial_code = initial_code

    def read(self, kind, message):
        if self.initial_code:
            code, self.initial_code = self.initial_code, ""
            pattern = r"[0-9]{6}" if kind == "authenticator" else r"(?:[0-9]{6}|[0-9]{8})"
            if not re.fullmatch(pattern, code):
                raise ValueError("Código 2FA inválido para o método solicitado.")
            return code
        return ask_code(kind, message)

    def getpass(self, prompt="", *_args, **_kwargs):
        low = str(prompt).strip().lower()
        if "depois de aprovar" in low:
            from instagram_login_apk import FlowError
            raise FlowError("O Instagram pediu aprovacao em outro aparelho. Conclua essa verificacao no aplicativo antes de conectar novamente.")
        if "app de autentica" in low or "codigo ou chave do autenticador" in low:
            return self.read("authenticator", "Digite o codigo de 6 digitos ou cole a chave 2FA com espacos.")
        if "autenticador" in low or "2fa" in low or "backup" in low:
            return self.read("two_factor", "Digite o codigo 2FA/backup ou cole a chave 2FA com espacos.")
        return self.read("code", "Digite o código que o Instagram enviou por SMS ou e-mail.")


@contextlib.contextmanager
def web_input(core, initial_code=""):
    """Adaptar somente entrada/saída; o núcleo usa a mesma lógica do script CLI."""
    reader = _WebGetpass(initial_code)
    overrides = {
        "CONSOLE": EventConsole(),
        "read_json": read_json,
        "save_private_json": write_json,
        # Substituir o nome no módulo do núcleo: não existe fallback para o terminal.
        "getpass": reader,
        "input": reader.getpass,
    }
    missing = object()
    previous = {name: getattr(core, name, missing) for name in overrides}
    try:
        for name, value in overrides.items():
            setattr(core, name, value)
        yield
    finally:
        reader.initial_code = ""
        for name, value in previous.items():
            if value is missing:
                delattr(core, name)
            else:
                setattr(core, name, value)


def run(payload):
    import instagram_login_apk as core

    with web_input(core, payload.pop("verification_code", "")):
        _run(payload, core)


def _run(payload, core):
    directory = Path(payload["directory"]).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    session_path = directory / "session.json"
    client = None
    authenticated = False
    failure = None
    diagnostic = None
    stage = "inicializacao"
    password = payload.pop("password", "")
    try:
        # load_settings da biblioteca usa JSON puro; a sessão é descriptografada
        # em memória e set_settings restaura o mesmo perfil Android.
        stage = "configurando_cliente"
        client = core.configure_client(core.load_client_class(), directory / "new-session-unused.json", directory / "legacy-unused.json", 30)
        if session_path.is_file():
            settings = read_json(session_path)
            if settings.get("schema") == 1:
                raise core.FlowError("O arquivo de sessão usa um formato antigo. Conecte novamente com uma sessão nova.")
            client.override_app_version = True
            client.set_settings(settings)
            client._local_login_username = settings.get("_local_login_username")
            # Como no CLI, aplicar estes ajustes somente após hidratar a sessão.
            client.read_timeout = 30
            client.set_retry_config(session_retry_total=0, public_request_retries_count=0)
        client.challenge_code_handler = core.read_verification_code
        # Match the CLI login flow: let the Instagram response determine whether
        # the same challenge is TOTP, SMS, email, or a backup code.
        client.two_factor_method = payload.get("two_factor_method", "auto")
        apply_instagram_proxy(client, payload.get("proxy", ""))
        handle = core.valid_username(payload["username"])
        owner = getattr(client, "_local_login_username", None)
        if owner and owner.casefold() != handle.casefold():
            raise core.FlowError("A sessão salva pertence a outra conta.")
        if payload["mode"] == "login":
            if not password and client.user_id and (client.authorization or client.sessionid):
                try:
                    stage = "validando_sessao_existente"
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
                stage = "login_instagram"
                from instagrapi.exceptions import TwoFactorRequired
                try:
                    accepted = client.login(handle, password)
                except TwoFactorRequired as error:
                    stage = "continuacao_2fa"
                    accepted = core.continue_two_factor(client, error)
                stage = "validando_resposta_login"
                if not accepted or not client.user_id or not (client.authorization or client.sessionid):
                    raise core.FlowError("O Instagram ainda não confirmou esta sessão. Consulte o diagnóstico da tentativa.")
                client.action_session_validated = False
                authenticated = True
            stage = "salvando_sessao"
            core.save_client(client, session_path)
            emit("log", tone="success", message="Login realizado com sucesso.")
            emit("log", tone="success", message="Sessão conectada.")
        else:
            stage = "validando_sessao_para_comando"
            if not owner:
                raise core.FlowError("Conecte sua conta antes de executar comandos.")
            client.password = None
            client.action_session_validated = False
            core.validate_action_session(client, handle)
            authenticated = True
            for index, step in enumerate(payload["steps"]):
                if index:
                    time.sleep(2)
                stage = "executando_comando"
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
        failure = error
        name = type(error).__name__
        if name in {"LoginRequired", "ClientUnauthorizedError"}:
            authenticated = False
        message = ERRORS.get(name, "Não foi possível concluir a operação. Confira sua conexão e o estado da conta no Instagram.")
        if isinstance(error, core.FlowError):
            message = str(error)
        elif name not in ERRORS:
            symbol = core.safe_symbol(name)
            if symbol:
                message += " Tipo do erro: " + symbol + "."
        if client is not None:
            diagnostic = core.safe_diagnostic(client, "autenticado" if authenticated else "recusado", error=failure)
            diagnostic.update(runtime_diagnostic(client))
            details = ["Etapa: " + stage]
            status = diagnostic.get("http")
            if status is not None:
                details.append("HTTP " + str(status))
            if status == 401 and payload.get("mode") == "login" and name in {"ClientError", "ClientUnauthorizedError"}:
                authenticated = False
                trace = diagnostic.get("etapas") or []
                message = ("O serviço de login não autorizou a solicitação. "
                           "Confira os avisos de segurança no aplicativo oficial do Instagram. "
                           "O painel não repetirá o login automaticamente.")
                try:
                    response_data = client.last_response.json()
                    provider_message = response_data.get("message", "") if isinstance(response_data, dict) else ""
                    if isinstance(provider_message, str) and provider_message.casefold().startswith(("aguarde alguns minutos", "please wait a few minutes")):
                        message = "O Instagram pediu para aguardar alguns minutos antes de tentar novamente. A tentativa parou antes da verificação 2FA."
                except (ValueError, AttributeError):
                    pass
                if trace and trace[-1].get("http") == status:
                    category = core.safe_symbol(trace[-1].get("etapa"))
                    if category:
                        details.append("Requisição: " + category)
            if diagnostic.get("erro"):
                details.append("Motivo: " + diagnostic["erro"])
            if diagnostic.get('ultima_rota') == 'chave_criptografia_senha':
                details.append('Falha ao obter a chave de criptografia, antes do envio da senha e do 2FA')
            message += " " + " · ".join(details) + "."
        emit("log", tone="error", message=message)
        emit("done", ok=False, authenticated=authenticated)
    finally:
        password = ""
        if client is not None:
            client.password = None
            try:
                core.save_client(client, session_path)
                if diagnostic is None:
                    diagnostic = core.safe_diagnostic(
                        client, "autenticado" if authenticated else "recusado", error=failure)
                diagnostic.update(runtime_diagnostic(client))
                diagnostic["etapa_worker"] = stage
                core.save_private_json(directory / "diagnostic.json", diagnostic)
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

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Instagram no terminal: login Android, perfis, seguir e curtir.

Instalacao (Python 3.11+):
    python -m pip install --upgrade instagrapi==3.2.19

Uso:
    python instagram_login_apk.py
    python instagram_login_apk.py login
    python instagram_login_apk.py login --2fa autenticador
    python instagram_login_apk.py buscar "carlos" --limite 10
    python instagram_login_apk.py perfil nomedousuario
    python instagram_login_apk.py seguir nomedousuario
    python instagram_login_apk.py seguir --id 123456789
    python instagram_login_apk.py curtir "https://www.instagram.com/p/CODIGO/"
    python instagram_login_apk.py buscar "carlos" --saida resultado.json

Arquivos:
    login.txt: user=SEU_USUARIO e senha=SUA_SENHA, em linhas separadas.
    instagram_sessao_caa.json: sessao reutilizada depois do login.
    instagram_diagnostico_acoes.json: diagnostico sem senha ou tokens.

Cores automaticas no CMD/PowerShell; --cor sempre forca e --sem-cor desliga.
O JSON de --saida continua livre de formatacao e sequencias de cor.

--2fa autenticador pede o TOTP de seis digitos quando o servidor devolver
two_step_verification_context ou two_factor_info com TOTP habilitado.
Se vier somente context_data de aprovacao, a troca ainda nao esta mapeada:
o programa para com diagnostico e nao envia TOTP no contexto errado.
Nesta versão web, o método padrão é autenticador; não há opção de aprovação por outro aparelho.

Login CAA/Bloks e perfil Android mantidos pela biblioteca nao oficial
instagrapi 3.2.19. O nucleo CAA e o mesmo que autenticou a conta; login e
busca foram confirmados pelo usuario. Codigo e aprovacao continuam manuais.
As acoes seguem as rotas REST analisadas no APK 458.0.0.0.45, validam o ID
autenticado e conferem o estado antes de confirmar seguir/curtir.
A continuacao accounts so e permitida uma vez quando o proprio CAA a solicita.
Senhas e codigos nao sao repetidos em ciclos de login.
Testes de regressao usam sessoes sinteticas e respostas simuladas.
"""
from __future__ import annotations
import argparse
import getpass
from importlib.metadata import version, PackageNotFoundError
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time
from typing import NamedTuple
import unicodedata
from urllib.parse import urlsplit

CLIENT_VERSION = "3.2.19"
REVISION = "caa-4-acoes-5-totp-continuacao"


# ---------------------------------------------------------------------------
# Apresentacao do terminal. Esta camada nao faz requisicoes nem altera sessoes.
# ---------------------------------------------------------------------------


class Terminal:
    """Cores ANSI sem dependencias extras; habilita suporte nativo no Windows."""

    PALETTE = {
        "brand": "95", "cyan": "96", "green": "92", "yellow": "93",
        "red": "91", "dim": "90", "bold": "1",
    }
    ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

    def __init__(self):
        self.mode = "auto"
        self._windows_modes = {}

    def setup(self, mode="auto"):
        self.mode = mode
        self._windows_modes.clear()
        for stream in (sys.stdout, sys.stderr):
            # Nao trocar a codificacao do usuario; evitar falha com nomes Unicode.
            if hasattr(stream, "reconfigure"):
                try:
                    stream.reconfigure(errors="replace")
                except (OSError, ValueError):
                    pass

    @classmethod
    def clean(cls, value):
        text = cls.ANSI.sub("", str(value))
        return "".join(char for char in text if char.isprintable())

    @staticmethod
    def _enable_windows_ansi(stream):
        try:
            import ctypes
            from ctypes import wintypes
            import msvcrt

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            kernel.GetConsoleMode.restype = wintypes.BOOL
            kernel.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel.SetConsoleMode.restype = wintypes.BOOL
            handle = wintypes.HANDLE(msvcrt.get_osfhandle(stream.fileno()))
            mode = wintypes.DWORD()
            return bool(kernel.GetConsoleMode(handle, ctypes.byref(mode))
                        and kernel.SetConsoleMode(handle, mode.value | 0x0004))
        except (ImportError, AttributeError, OSError, ValueError):
            return False

    def colors(self, stream):
        if self.mode == "nunca":
            return False
        tty = bool(getattr(stream, "isatty", lambda: False)())
        if self.mode == "auto":
            if (not tty or "NO_COLOR" in os.environ or os.environ.get("CLICOLOR") == "0"
                    or os.environ.get("TERM") == "dumb"):
                return False
        if os.name == "nt" and tty:
            key = id(stream)
            if key not in self._windows_modes:
                self._windows_modes[key] = self._enable_windows_ansi(stream)
            return self._windows_modes[key]
        return True

    def paint(self, text, tone, stream=None):
        stream = sys.stdout if stream is None else stream
        text = self.clean(text)
        if self.colors(stream):
            return "\x1b[" + self.PALETTE[tone] + "m" + text + "\x1b[0m"
        return text

    @staticmethod
    def width():
        return max(20, min(shutil.get_terminal_size(fallback=(100, 24)).columns - 4, 116))

    @staticmethod
    def text_width(text):
        return sum(0 if unicodedata.category(char) in {"Mn", "Me", "Cf"}
                   else 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
                   for char in text)

    @classmethod
    def cell(cls, text, width):
        text = cls.clean(text)
        if cls.text_width(text) > width:
            clipped, used = [], 0
            for char in text:
                size = cls.text_width(char)
                if used + size > width - 3:
                    break
                clipped.append(char)
                used += size
            text = "".join(clipped) + "..."
        return text + " " * max(0, width - cls.text_width(text))

    def banner(self, command):
        titles = {"login": "Autenticacao", "buscar": "Buscar perfis", "perfil": "Consultar perfil",
                  "seguir": "Seguir perfil", "curtir": "Curtir publicacao", "fluxo": "Comandos disponiveis"}
        print()
        print("  " + self.paint("INSTAGRAM", "brand") + "  " + self.paint("/ ANDROID", "dim"))
        print("  " + self.paint(titles[command], "bold"))
        print("  " + self.paint("-" * min(self.width(), 72), "dim"))

    def section(self, title):
        print()
        print("  " + self.paint(title, "brand"))

    def message(self, label, text, tone, stream=None):
        stream = sys.stdout if stream is None else stream
        print("  " + self.paint("[" + label + "]", tone, stream) + " " + self.clean(text), file=stream, flush=True)

    def info(self, text):
        self.message("INFO", text, "cyan")

    def success(self, text):
        self.message("OK", text, "green")

    def warning(self, text):
        self.message("AVISO", text, "yellow")

    def error(self, text, stream=None):
        self.message("ERRO", text, "red", stream)

    def detail(self, text):
        print("  " + self.paint(text, "dim"))

    def field(self, label, value):
        print("  " + self.paint(self.cell(label, 18), "cyan") + " " + self.clean(value))

    def profiles(self, result, query):
        self.section("Resultados da busca")
        self.detail("Termo: " + self.clean(query))
        profiles = result["perfis"]
        if not profiles:
            self.warning("Nenhum perfil encontrado para essa busca.")
            self.success("Perfis encontrados: 0")
            return
        number_width = max(2, len(str(len(profiles))))
        id_width = max(2, *(len(row["id"]) for row in profiles))
        user_width = max(8, *(self.text_width("@" + row["username"]) for row in profiles))
        name_width = min(44, self.width() - number_width - id_width - user_width - 6)
        print()
        if name_width < 12:
            # Em janela estreita, manter IDs/usernames completos em linhas separadas.
            for index, row in enumerate(profiles, 1):
                print("  " + self.paint(str(index) + ". @" + row["username"], "cyan"))
                self.detail("ID: " + row["id"])
                if row["nome"]:
                    self.detail("Nome: " + row["nome"])
                print()
        else:
            widths = (number_width, id_width, user_width, name_width)
            headings = ("#", "ID", "USERNAME", "NOME")
            print("  " + self.paint("  ".join(self.cell(label, size) for label, size in zip(headings, widths)), "brand"))
            print("  " + self.paint("-" * (sum(widths) + 6), "dim"))
            for index, row in enumerate(profiles, 1):
                values = (str(index), row["id"], "@" + row["username"], row["nome"] or "-")
                tones = ("dim", "dim", "cyan", None)
                cells = [self.paint(self.cell(value, size), tone) if tone else self.cell(value, size)
                         for value, size, tone in zip(values, widths, tones)]
                print("  " + "  ".join(cells).rstrip())
            print()
        self.success("Perfis encontrados: " + str(result["quantidade"]))

    def profile(self, profile):
        self.section("@" + profile["username"])
        for key, label in (("nome", "Nome"), ("id", "ID"), ("privado", "Conta privada"),
                           ("verificado", "Verificado"), ("seguidores", "Seguidores"),
                           ("seguindo", "Seguindo"), ("publicacoes", "Publicacoes")):
            if key not in profile:
                continue
            value = profile[key]
            if isinstance(value, bool):
                value = "Sim" if value else "Nao"
            elif isinstance(value, int):
                value = format(value, ",").replace(",", ".")
            self.field(label, value or "-")
        print()
        self.success("Perfil consultado.")


CONSOLE = Terminal()


# ---------------------------------------------------------------------------
# Interpretacao de respostas CAA/Bloks: somente dados e expressoes literais.
# ---------------------------------------------------------------------------

PROFILE_ENTRY = "com.bloks.www.ap.two_step_verification.entrypoint_async"
PROFILE_CODE = "com.bloks.www.ap.two_step_verification.code_entry"
PROFILE_SUBMIT = "com.bloks.www.ap.two_step_verification.code_entry_async"
PROFILE_APPROVE = "com.bloks.www.ap.two_step_verification.approve_from_another_device"
TOTP_ENTRY = "com.bloks.www.two_step_verification.entrypoint"
TOTP_PICKER = "com.bloks.www.two_step_verification.method_picker"
TOTP_SELECT = "com.bloks.www.two_step_verification.method_picker.navigation.async"
TOTP_SCREEN = "com.bloks.www.two_factor_login.enter_totp_code"
TOTP_VERIFY = "com.bloks.www.two_step_verification.verify_code.async"
TOTP_ROUTES = {
    "bloks/apps/" + app + "/" for app in (TOTP_ENTRY, TOTP_PICKER, TOTP_SCREEN)
} | {"bloks/async_action/" + app + "/" for app in (TOTP_SELECT, TOTP_VERIFY)}
CAA_REASONS = {
    "missing entrypoint context_data": "sem_contexto_entrada",
    "missing code_entry context_data": "sem_contexto_tela_codigo",
    "missing code_entry_async context_data": "sem_contexto_envio_codigo",
    "CAA preflight did not return account access and attestation data": "preparacao_incompleta",
    "CAA login did not return a session": "sem_sessao",
    "device approval pending": "aprovacao_pendente",
    "authenticator context unavailable": "sem_contexto_autenticador",
}


class _Expr(NamedTuple):
    operator: str
    args: tuple


class _Atom(str):
    pass


def response_values(result):
    """Expandir somente JSON literal; nunca executar codigo retornado."""
    pending = [(result, 0)]
    visited = 0
    while pending and visited < 60000:
        value, layers = pending.pop()
        visited += 1
        yield value
        if isinstance(value, dict):
            pending.extend((child, layers) for child in value.values())
        elif isinstance(value, list):
            pending.extend((child, layers) for child in value)
        elif isinstance(value, str) and layers < 3 and len(value) <= 524288:
            text = value.lstrip()
            if not text or text[0] not in '{["':
                continue
            try:
                decoded = json.loads(value)
            except (ValueError, RecursionError):
                continue
            if isinstance(decoded, (dict, list, str)) and decoded != value:
                pending.append((decoded, layers + 1))


def parameter_name(value):
    if (type(value) is str and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", value)
            and not re.fullmatch(r"[A-Fa-f0-9]{16,}", value)):
        return value
    return "<campo>"


def structure_only(value, depth=0):
    """Preservar operadores e nomes de campos; ocultar todos os valores."""
    if depth >= 6:
        return {"tipo": "estrutura_limitada"}
    if isinstance(value, _Expr):
        if len(value.args) == 2:
            keys, values = value.args
            if (isinstance(keys, _Expr) and isinstance(values, _Expr)
                    and keys.operator == values.operator and len(keys.args) == len(values.args)
                    and all(type(key) is str for key in keys.args)):
                return {"operador": value.operator, "campos": [
                    {"nome": parameter_name(key), "estrutura": structure_only(item, depth + 1)}
                    for key, item in list(zip(keys.args, values.args))[:24]
                ]}
        return {"operador": value.operator, "argumentos": [structure_only(item, depth + 1) for item in value.args[:12]]}
    if isinstance(value, dict):
        return {"tipo": "objeto", "campos": [
            {"nome": parameter_name(key), "estrutura": structure_only(item, depth + 1)}
            for key, item in list(value.items())[:12]
        ]}
    if isinstance(value, list):
        return {"tipo": "lista", "itens": [structure_only(item, depth + 1) for item in value[:6]]}
    if isinstance(value, _Atom):
        return {"tipo": "atomo"}
    if isinstance(value, str):
        return {"tipo": "texto"}
    if value is None:
        return {"tipo": "nulo"}
    if isinstance(value, bool):
        return {"tipo": "booleano"}
    return {"tipo": "numero"}


def continuation_structure(data):
    texts = [value for value in response_values(data) if isinstance(value, str)]
    app_pattern = re.compile(r'(?<![_a-zA-Z0-9])com\.bloks\.www\.[A-Za-z0-9_.]{1,160}')
    apps = sorted({match.group().rstrip(".") for text in texts for match in app_pattern.finditer(text)})[:40]
    examples = []
    for app in apps:
        anchor = json.dumps(app)
        for text in texts:
            position = text.find(anchor)
            if position < 0:
                continue
            position += len(anchor)
            following = app_pattern.search(text, position)
            limit = min(len(text), position + 131072, following.start() if following else len(text))
            section = text[position:limit]
            opening = section.find("(")
            if opening < 0:
                continue
            try:
                expression, _ = _literal_expression(section, opening)
            except (ValueError, RecursionError):
                continue
            examples.append({"app": app, "parametros": structure_only(expression)})
            break
        if len(examples) >= 8:
            break
    return {"apps_referenciados": apps, "mapas_de_parametros": examples,
            "formato_resposta": structure_only(data)}


def _literal_expression(text, position=0, depth=0, budget=None):
    """Ler estrutura, sem executar operadores nem resolver expressoes dinamicas."""
    if budget is None:
        budget = [10000]
    budget[0] -= 1
    if depth > 80 or budget[0] < 0:
        raise ValueError("Limite de estrutura Bloks")
    while position < len(text) and (text[position].isspace() or text[position] == ","):
        position += 1
    if position >= len(text):
        raise ValueError("Expressao incompleta")
    character = text[position]
    if character == '"':
        return json.JSONDecoder().raw_decode(text, position)
    if character == "(":
        items = []
        position += 1
        while position < len(text):
            while position < len(text) and (text[position].isspace() or text[position] == ","):
                position += 1
            if position >= len(text):
                break
            if text[position] == ")":
                if not items or not isinstance(items[0], _Atom):
                    raise ValueError("Operador nao literal")
                return _Expr(str(items[0]), tuple(items[1:])), position + 1
            value, position = _literal_expression(text, position, depth + 1, budget)
            items.append(value)
        raise ValueError("Expressao sem fechamento")
    match = re.match(r'[^\s,()"]+', text[position:])
    if not match:
        raise ValueError("Atomo invalido")
    return _Atom(match.group()), position + len(match.group())


def _context_from_static_map(expression, context_key):
    if not isinstance(expression, _Expr) or len(expression.args) != 2:
        return ""
    keys, values = expression.args
    if (not isinstance(keys, _Expr) or not isinstance(values, _Expr)
            or keys.operator != values.operator or len(keys.args) != len(values.args)
            or any(type(key) is not str for key in keys.args)
            or len(set(keys.args)) != len(keys.args)):
        return ""
    mapping = dict(zip(keys.args, values.args))
    if context_key in mapping:
        value = mapping[context_key]
        # Uma chamada como GetArg nao e um contexto, mesmo contendo strings.
        return value if type(value) is str else ""
    server_params = mapping.get("server_params")
    if isinstance(server_params, _Expr):
        return _context_from_static_map(server_params, context_key)
    if type(server_params) is str:
        try:
            server_params = json.loads(server_params)
        except ValueError:
            return ""
    if isinstance(server_params, dict):
        value = server_params.get(context_key)
        return value if type(value) is str else ""
    return ""


def extract_static_context(result, app_id, context_key="context_data"):
    """Aceitar aliases diferentes em mapas literais associados ao app exato."""
    candidates = set()
    texts = []
    for value in response_values(result):
        if isinstance(value, dict):
            if value.get("app_id") == app_id:
                params = value.get("params", value.get("server_params", {}))
                if isinstance(params, str):
                    try:
                        params = json.loads(params)
                    except ValueError:
                        params = {}
                if isinstance(params, dict):
                    params = params.get("server_params", params)
                    if isinstance(params, dict) and type(params.get(context_key)) is str:
                        candidates.add(params[context_key])
        elif isinstance(value, str):
            texts.append(value)
    anchor = json.dumps(app_id)
    another_app = re.compile(r'(?<![_a-zA-Z0-9])com\.bloks\.[_a-zA-Z0-9.]+')
    for text in texts:
        start = 0
        for _ in range(24):
            position = text.find(anchor, start)
            if position < 0:
                break
            position += len(anchor)
            start = position
            next_app = another_app.search(text, position)
            boundary = min(len(text), position + 131072,
                           next_app.start() if next_app else len(text))
            section = text[position:boundary]
            opening = section.find("(")
            if opening < 0:
                continue
            try:
                expression, _ = _literal_expression(section, opening)
            except (ValueError, RecursionError):
                continue
            context = _context_from_static_map(expression, context_key)
            if context:
                candidates.add(context)
    candidates.discard("")
    # Nunca escolher entre dois contextos diferentes sem conhecer o fluxo ativo.
    return next(iter(candidates)) if len(candidates) == 1 else ""


def bloks_shape_summary(client, data):
    strings = []
    client._bloks_collect_strings(data, strings)
    text = "\n".join(strings)
    return {
        "referencia_entrada": PROFILE_ENTRY in text,
        "referencia_tela_codigo": PROFILE_CODE + '"' in text,
        "referencia_envio_codigo": PROFILE_SUBMIT in text,
        "contexto_entrada_extraivel": bool(client.bloks_extract_context_data(data, PROFILE_ENTRY)),
        "contexto_tela_codigo_extraivel": bool(client.bloks_extract_context_data(data, PROFILE_CODE)),
        "contexto_envio_codigo_extraivel": bool(client.bloks_extract_context_data(data, PROFILE_SUBMIT)),
        "continuacao_retornada": continuation_structure(data),
    }

# ---------------------------------------------------------------------------
# Verificacao pelo app de autenticacao (TOTP).
# ---------------------------------------------------------------------------


# APK 458.0.0.0.45: X/0JPU.<clinit> associa AUTHENTICATOR_APP a 3 e
# TRUSTED_NOTIFICATION a 4. X/0FrI.A00 envia o metodo para X/0OOB.A00,
# que monta accounts/two_factor_login/ e verification_method.
# Os helpers Bloks sao os da dependencia 3.2.19. context_data (AP) nao
# e convertido em two_step_verification_context (2FA).
def authenticator_context(client, data):
    candidates = {
        value["two_step_verification_context"].strip()
        for value in response_values(data)
        if isinstance(value, dict) and isinstance(value.get("two_step_verification_context"), str)
        and value["two_step_verification_context"].strip()
    }
    for app in (TOTP_ENTRY, TOTP_PICKER, TOTP_SELECT, TOTP_SCREEN, TOTP_VERIFY):
        context = extract_static_context(data, app, "two_step_verification_context")
        if context:
            candidates.add(context.strip())
    if len(candidates) > 1:
        raise FlowError("A resposta trouxe contextos 2FA diferentes. Nenhum codigo foi solicitado ou enviado.")
    return next(iter(candidates), "")


def native_authenticator_info(data):
    found = {}
    for value in response_values(data):
        if not isinstance(value, dict):
            continue
        info = value.get("two_factor_info")
        if not isinstance(info, dict):
            continue
        identifier = info.get("two_factor_identifier")
        enabled = info.get("totp_two_factor_on", info.get("is_totp_two_factor_enabled"))
        if isinstance(identifier, str) and identifier and enabled in (True, 1, "1", "true"):
            found[identifier] = info
    if len(found) > 1:
        raise FlowError("A resposta trouxe identificadores 2FA diferentes. Nenhum codigo foi enviado.")
    return next(iter(found.values()), None)


def read_authenticator_code():
    CONSOLE.info("Abra seu app de autenticacao e use o codigo atual de seis digitos.")
    code = re.sub(r"\s+", "", getpass.getpass("Codigo do app de autenticacao: "))
    if not re.fullmatch(r"[0-9]{6}", code):
        raise FlowError("O autenticador precisa de seis digitos. O codigo nao foi enviado.")
    return code


def authenticator_login(client, data):
    """Continuar a tentativa existente; nao reenviar senha nem repetir o codigo."""
    context = authenticator_context(client, data)
    info = None if context else native_authenticator_info(data)
    if not context and info is None:
        client.authenticator_state = "sem_contexto_autenticador"
        client.caa_last_outcome = {"logged_in": False, "reason": "authenticator context unavailable"}
        raise FlowError(
            "O servidor nao forneceu o contexto 2FA nem um identificador nativo com TOTP habilitado. "
            "A troca da aprovacao para o autenticador ainda nao esta mapeada nessa resposta. "
            "Veja o diagnostico; nenhum codigo foi solicitado."
        )
    client.authenticator_in_progress = True
    client.authenticator_state = "preparando"
    code = None
    try:
        if context:
            client.authenticator_branch = "bloks_totp"
            CONSOLE.info("Selecionando App de autenticacao...")
            for step in (
                lambda: client.bloks_two_step_verification_entrypoint(context),
                lambda: client.bloks_two_step_verification_method_picker(context),
                lambda: client.bloks_two_step_verification_select_method(context, selected_method="totp"),
                lambda: client.bloks_two_step_verification_enter_totp_code(context),
            ):
                result = step()
                if client.bloks_apply_login_response(result) and client.user_id and (client.authorization or client.sessionid):
                    client.authenticator_state = "autenticado"
                    return result
                context = authenticator_context(client, result) or context
            client.authenticator_state = "aguardando_codigo"
            code = read_authenticator_code()
            client.authenticator_state = "codigo_enviado"
            result = client.bloks_two_step_verification_verify_code(context, code, challenge="totp", should_trust_device=False)
            accepted = client.bloks_apply_login_response(result)
        else:
            client.authenticator_branch = "nativo_metodo_3"
            client.authenticator_state = "aguardando_codigo"
            code = read_authenticator_code()
            payload = {
                "verification_code": code, "verification_method": "3",
                "two_factor_identifier": info["two_factor_identifier"],
                "username": info.get("username") or client.username,
                "phone_id": client.phone_id, "guid": client.uuid,
                "device_id": client.android_device_id, "trust_this_device": "0",
                "_csrftoken": client.token,
            }
            client.authenticator_state = "codigo_enviado"
            client.private_requests_count += 1
            result = client._send_private_request("accounts/two_factor_login/", data=payload, login=True)
            authorization = client.last_response.headers.get("ig-set-authorization", "")
            user = result.get("logged_in_user") if isinstance(result, dict) else None
            auth = client.parse_authorization(authorization)
            identifier = str(user.get("pk") or "") if isinstance(user, dict) else ""
            accepted = bool(isinstance(user, dict) and result.get("status") == "ok"
                            and re.fullmatch(r"[1-9][0-9]{0,29}", identifier)
                            and auth.get("sessionid") and str(auth.get("ds_user_id")) == identifier)
            if accepted:
                client.authorization_data = auth
                client.private.cookies.set("ds_user_id", str(user["pk"]))
                client.private.headers["Authorization"] = client.authorization
        if not accepted or not client.user_id or not (client.authorization or client.sessionid):
            client.authenticator_state = "sem_sessao_apos_codigo"
            raise FlowError("A resposta do 2FA nao confirmou uma sessao autenticada. O codigo nao foi repetido.")
        client.authenticator_state = "autenticado"
        return result
    finally:
        code = None
        client.authenticator_in_progress = False


# ---------------------------------------------------------------------------
# Credenciais e persistencia local.
# ---------------------------------------------------------------------------


class FlowError(Exception):
    """Erro operacional sem incluir senha ou resposta integral na mensagem."""

def read_credentials(path: Path, require_password: bool = True) -> tuple[str, str]:
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise FlowError("Nao foi possivel ler o TXT de credenciais. "
                        "Coloque login.txt junto do Python ou use --credenciais CAMINHO.") from exc
    credentials = {}
    aliases = {"user": "user", "usuario": "user", "username": "user", "senha": "senha"}
    for number, line in enumerate(text.split("\n"), 1):
        line = line.removesuffix("\r")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key = aliases.get(key.strip().lower())
        if not separator or key is None or key in credentials:
            raise FlowError(f"Formato invalido na linha {number} do TXT. Use user= e senha=.")
        # Nao remover espacos, #, : ou sinais = da senha.
        credentials[key] = value.strip() if key == "user" else value
    username = credentials.get("user", "")
    password = credentials.get("senha", "")
    if not username or username in {"SEU_USUARIO", "COLOQUE_SEU_USUARIO_AQUI"}:
        raise FlowError("Preencha user= no TXT antes de executar.")
    if require_password and (not password or password in {"SUA_SENHA", "COLOQUE_SUA_SENHA_AQUI"}):
        raise FlowError("Preencha user= e senha= no TXT antes de executar.")
    return username, password if require_password else ""

def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise FlowError(f"Nao foi possivel ler o JSON {path.name}.") from exc
    if not isinstance(value, dict):
        raise FlowError(f"{path.name} precisa conter um objeto JSON.")
    return value

def save_private_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".sessao-", delete=False) as handle:
            temp_name = handle.name
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_name, path)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


# ---------------------------------------------------------------------------
# Cliente Android e continuacoes de autenticacao.
# ---------------------------------------------------------------------------


def load_client_class():
    if sys.version_info < (3, 11):
        raise FlowError("Esta revisao requer Python 3.11 ou mais recente.")
    try:
        installed = version("instagrapi")
    except PackageNotFoundError as exc:
        raise FlowError("Instale a dependencia: python -m pip install --upgrade instagrapi==" + CLIENT_VERSION) from exc
    if installed != CLIENT_VERSION:
        raise FlowError("Esta revisao foi validada com instagrapi==" + CLIENT_VERSION +
                        ". Execute: python -m pip install --upgrade instagrapi==" + CLIENT_VERSION)
    try:
        from instagrapi import Client
        from instagrapi.exceptions import ChallengeRequired
    except ImportError as exc:
        raise FlowError("A instalacao do instagrapi esta incompleta. Reinstale instagrapi==" + CLIENT_VERSION) from exc

    class LoginClient(Client):
        def __init__(self, *args, **kwargs):
            self.login_trace = []
            self.caa_last_outcome = {}
            self.contexts_recovered = 0
            self.device_approval_state = None
            self.device_approval_checks = 0
            self.profile_session_completed = False
            self.two_factor_method = "autenticador"
            self.authenticator_in_progress = False
            self.authenticator_state = "nao_solicitado"
            self.authenticator_branch = None
            self.caa_legacy_continuation_used = False
            super().__init__(*args, **kwargs)

        def private_request(self, endpoint, *args, **kwargs):
            # A dependencia repete timeouts mesmo com retry_total=0. Nao repetir
            # etapas TOTP nem disparar outro challenge/login neste ramo.
            if self.authenticator_in_progress:
                if endpoint not in TOTP_ROUTES:
                    raise FlowError("Rota inesperada durante a verificacao do autenticador.")
                kwargs["login"] = True
                self.private_requests_count += 1
                return self._send_private_request(endpoint, *args, **kwargs)
            return super().private_request(endpoint, *args, **kwargs)

        def bloks_caa_resolve_two_step_verification(self, send_result, verification_code="", domain=None):
            if self.two_factor_method == "autenticador" and (
                    authenticator_context(self, send_result) or native_authenticator_info(send_result)):
                result = authenticator_login(self, send_result)
                self.profile_session_completed = True
                return {"logged_in": True, "reason": "", "result": result}
            return super().bloks_caa_resolve_two_step_verification(send_result, verification_code=verification_code, domain=domain)

        def _bloks_collect_strings(self, value, output):
            output.extend(item for item in response_values(value) if isinstance(item, str))

        def _bloks_extract_context_value(self, result, app_id, context_key):
            value = super()._bloks_extract_context_value(result, app_id, context_key)
            if value:
                return value
            value = extract_static_context(result, app_id, context_key)
            if value:
                self.contexts_recovered += 1
            return value

        def bloks_caa_login(self, *args, **kwargs):
            if self.caa_legacy_continuation_used:
                raise FlowError("O Instagram nao concluiu a continuacao do login. A senha nao foi reenviada em outra tentativa CAA.")
            outcome = super().bloks_caa_login(*args, **kwargs)
            self.caa_last_outcome = outcome
            # Uma resposta CAA pode conter o identificador TOTP nativo sem a
            # referencia AP que aciona o resolvedor da biblioteca.
            if (not outcome.get("logged_in") and self.two_factor_method == "autenticador"
                    and self.authenticator_state == "nao_solicitado"):
                data = {"resultado_caa": outcome, "resposta": self.last_json}
                if authenticator_context(self, data) or native_authenticator_info(data):
                    authenticator_login(self, data)
                    self.profile_session_completed = True
            # A entrada de verificacao tambem pode devolver a sessao diretamente,
            # sem passar pela tela de codigo que a biblioteca espera.
            if self.profile_session_completed and self.user_id and (self.authorization or self.sessionid):
                outcome["logged_in"] = True
                outcome["reason"] = ""
            self.caa_last_outcome = outcome
            return outcome

        def bloks_ap_two_step_verification_entrypoint(self, context_data, bloks_versioning_id="", domain=None):
            parent_entry = super().bloks_ap_two_step_verification_entrypoint
            result = parent_entry(context_data, bloks_versioning_id=bloks_versioning_id, domain=domain)
            if self.two_factor_method == "autenticador":
                if self.bloks_apply_login_response(result) and self.user_id and (self.authorization or self.sessionid):
                    self.profile_session_completed = True
                    return result
                result = authenticator_login(self, result)
                self.profile_session_completed = True
                return result
            raise FlowError("O Instagram exigiu uma verificação sem contexto TOTP disponível. A tentativa foi encerrada; nenhum código foi enviado.")

        def login_legacy(self, *args, **kwargs):
            # Respeitar a continuacao explicita do CAA; nao iniciar a rota
            # accounts por tentativa nem repetir um codigo ja enviado.
            markers = self._caa_result_action_markers(self.caa_last_outcome)
            if (not self.caa_legacy_continuation_used
                    and self.authenticator_state == "nao_solicitado"
                    and any(marker.startswith("CAA_LOGIN_FALLBACK:") for marker in markers)):
                self.caa_legacy_continuation_used = True
                CONSOLE.info("Continuando a etapa de autenticacao solicitada pelo Instagram...")
                return super().login_legacy(*args, **kwargs)
            reason = CAA_REASONS.get(self.caa_last_outcome.get("reason"), "continuacao_nao_reconhecida")
            raise FlowError("A verificacao CAA nao foi concluida: " + reason +
                            ". A rota antiga foi interrompida; veja o diagnostico.")

        def _send_private_request(self, endpoint, *args, **kwargs):
            # Registrar somente a categoria; nunca URL, corpo, headers ou IDs.
            category = endpoint_category(endpoint)
            started = time.monotonic()
            try:
                return super()._send_private_request(endpoint, *args, **kwargs)
            finally:
                response = getattr(self, "last_response", None)
                data = getattr(self, "last_json", None)
                error = safe_symbol(data.get("error_type")) if isinstance(data, dict) else None
                status = getattr(response, "status_code", None)
                entry = {
                    "etapa": category,
                    "http": status if isinstance(status, int) and 100 <= status <= 599 else None,
                    "erro": error,
                    "segundos": round(time.monotonic() - started, 2),
                }
                if category in {"caa_login", "verificacao_entrada", "verificacao_tela_codigo", "verificacao_envio_codigo"}:
                    try:
                        entry["estrutura_bloks"] = bloks_shape_summary(self, data)
                    except (ValueError, TypeError, RecursionError):
                        entry["estrutura_indisponivel"] = True
                self.login_trace.append(entry)

        def challenge_code_or_raised(self, choice, wait_seconds=5, attempts=24):
            # A implementacao original imprime o codigo. Aqui ele nao e registrado.
            code = self.challenge_code_handler(self.username, choice)
            if not code:
                raise ChallengeRequired("Codigo de verificacao nao informado.")
            return code

    return LoginClient


def endpoint_category(endpoint):
    endpoint = str(endpoint)
    if TOTP_VERIFY in endpoint or endpoint == "accounts/two_factor_login/":
        return "verificacao_autenticador_envio"
    if TOTP_SCREEN in endpoint:
        return "verificacao_autenticador_tela"
    if TOTP_PICKER in endpoint:
        return "verificacao_selecao_metodo"
    if TOTP_ENTRY in endpoint:
        return "verificacao_entrada_2fa"
    for pattern, category in (
        (r"accounts/current_user/", "validar_sessao"),
        (r"users/search/", "buscar_perfis"),
        (r"fbsearch/ig_typeahead/", "buscar_geral"),
        (r"users/[A-Za-z0-9_.]+/(?:usernameinfo|info)/", "consultar_perfil"),
        (r"friendships/create/[0-9]+/", "seguir_perfil"),
        (r"friendships/show/[0-9]+/", "consultar_relacao"),
        (r"media/[0-9_]+/info/", "consultar_midia"),
        (r"media/[0-9_]+/like/", "curtir_publicacao"),
    ):
        if re.fullmatch(pattern, endpoint):
            return category
    if "send_login_request" in endpoint:
        return "caa_login"
    if PROFILE_ENTRY in endpoint:
        return "verificacao_entrada"
    if PROFILE_SUBMIT in endpoint:
        return "verificacao_envio_codigo"
    if PROFILE_CODE in endpoint:
        return "verificacao_tela_codigo"
    if "two_step_verification" in endpoint or "two_factor_login" in endpoint:
        return "verificacao"
    if "caa" in endpoint or "bloks" in endpoint:
        return "caa_preparacao"
    if "accounts/login" in endpoint:
        return "login_legado_solicitado_pelo_fluxo"
    if "challenge" in endpoint:
        return "desafio"
    if "launcher" in endpoint or "qe/sync" in endpoint:
        return "configuracao"
    return "requisicao_auxiliar"


def requires_device_approval(result):
    return any(PROFILE_APPROVE in value for value in response_values(result) if isinstance(value, str))


def safe_symbol(value):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", value) else None


def read_verification_code(username, choice):
    name = getattr(choice, "name", str(choice)).upper()
    source = "e-mail" if name == "EMAIL" else "SMS" if name == "SMS" else "metodo solicitado pelo Instagram"
    return getpass.getpass("Digite o codigo recebido por " + source + ": ").strip()


def migrate_identity(client, legacy_path):
    if not legacy_path.is_file():
        return False
    legacy = read_json(legacy_path)
    if legacy.get("schema") != 1 or not isinstance(legacy.get("device"), dict):
        return False
    device = legacy["device"]
    uuids = dict(client.get_settings().get("uuids", {}))
    for source, target in {"guid": "uuid", "phone_id": "phone_id", "device_id": "android_device_id"}.items():
        value = device.get(source)
        if isinstance(value, str) and value:
            uuids[target] = value
    if device.get("adid"):
        uuids["advertising_id"] = device["adid"]
    client.set_uuids(uuids)
    return True


def configure_client(client_class, session_path, legacy_path, timeout):
    quiet = logging.getLogger("instagram_login_local")
    quiet.handlers = [logging.NullHandler()]
    quiet.propagate = False
    quiet.setLevel(logging.CRITICAL + 1)
    client = client_class(logger=quiet, override_app_version=True,
                          session_retry_total=0, public_request_retries_count=0)
    client._local_login_username = None
    if session_path.exists():
        settings = read_json(session_path)
        if settings.get("schema") == 1:
            raise FlowError("Esse e o arquivo da versao anterior. Use instagram_sessao_caa.json em --sessao.")
        client.load_settings(session_path, override_app_version=True)
        client._local_login_username = settings.get("_local_login_username")
    else:
        migrate_identity(client, legacy_path)
        client.set_locale("pt_BR")
        client.set_country("BR")
        client.set_country_code(55)
        client.set_timezone_offset(-10800, timezone_name="America/Sao_Paulo")
    client.read_timeout = timeout
    # Nao repetir automaticamente falhas HTTP de autenticacao ou restricoes.
    client.set_retry_config(session_retry_total=0, public_request_retries_count=0)
    client.challenge_code_handler = read_verification_code
    return client


def save_client(client, path):
    settings = client.get_settings()
    # get_settings nao inclui a senha; essas chaves nunca sao gravadas pelo wrapper.
    for name in ("password", "enc_password", "verification_code", "username"):
        settings.pop(name, None)
    if getattr(client, "_local_login_username", None):
        settings["_local_login_username"] = client._local_login_username
    save_private_json(path, settings)


def safe_diagnostic(client, outcome, error=None):
    response = getattr(error, "response", None) if error is not None else None
    if response is None:
        response = getattr(client, "last_response", None)
    data = getattr(client, "last_json", {})
    error_type = safe_symbol(getattr(error, "error_type", None)) if error is not None else None
    if not error_type and isinstance(data, dict):
        error_type = safe_symbol(data.get("error_type"))
    status = getattr(response, "status_code", None)
    trace = list(getattr(client, "login_trace", []))
    caa_outcome = getattr(client, "caa_last_outcome", {}) or {}
    reason = caa_outcome.get("reason")
    return {
        "revisao": REVISION,
        "python": ".".join(map(str, sys.version_info[:3])),
        "biblioteca": "instagrapi", "versao_biblioteca": CLIENT_VERSION,
        "app_version": str(client.device_settings.get("app_version", "")),
        "version_code": str(client.device_settings.get("version_code", "")),
        "resultado": outcome,
        "http": status if isinstance(status, int) and 100 <= status <= 599 else None,
        "erro": error_type,
        "excecao": safe_symbol(type(error).__name__) if error is not None else None,
        "sessao_recebida": bool(client.user_id and (client.authorization or client.sessionid)),
        "caa_iniciado": any(row.get("etapa") in {"caa_preparacao", "caa_login"} for row in trace),
        "motivo_caa": "autenticado" if caa_outcome.get("logged_in") else CAA_REASONS.get(reason, "indisponivel"),
        "leituras_alternativas_de_contexto": getattr(client, "contexts_recovered", 0),
        "aprovacao_outro_dispositivo": getattr(client, "device_approval_state", None),
        "consultas_apos_confirmacao_manual": getattr(client, "device_approval_checks", 0),
        "metodo_2fa": getattr(client, "two_factor_method", "auto"),
        "ramo_autenticador": getattr(client, "authenticator_branch", None),
        "estado_autenticador": getattr(client, "authenticator_state", "nao_solicitado"),
        "continuacao_accounts_solicitada": getattr(client, "caa_legacy_continuation_used", False),
        "etapas": trace[-100:],
    }


def continue_two_factor(client, error):
    if getattr(client, "two_factor_method", "auto") == "autenticador":
        if getattr(client, "authenticator_state", "nao_solicitado") != "nao_solicitado":
            raise FlowError("A tentativa do autenticador foi interrompida pelo servidor. Nenhum codigo ou etapa foi repetido.")
        data = {"resposta": client.last_json, "resultado_caa": client.caa_last_outcome}
        context = getattr(error, "two_step_verification_context", "")
        if isinstance(context, str) and context:
            data["two_step_verification_context"] = context
        info = getattr(error, "two_factor_info", None)
        if isinstance(info, dict):
            data["two_factor_info"] = info
        authenticator_login(client, data)
        return bool(client.user_id and (client.authorization or client.sessionid))
    # Continuar o contexto recebido, sem repetir o envio de usuario e senha.
    context = getattr(error, "two_step_verification_context", "")
    data = dict(client.last_json) if isinstance(client.last_json, dict) else {}
    if context:
        data["two_step_verification_context"] = context
    context = client._extract_two_step_verification_context(data)
    if not context:
        raise FlowError("O servidor pediu dois fatores, mas nao devolveu um contexto utilizavel. Conclua no aplicativo.")
    data["two_step_verification_context"] = context
    code = getpass.getpass("Digite o codigo do autenticador/SMS ou codigo de backup solicitado: ").strip()
    if not code:
        raise FlowError("Codigo nao informado.")
    return client._login_with_bloks_two_factor(code, data, error)


def show_failure(diagnostic):
    error = diagnostic.get("erro")
    kind = diagnostic.get("excecao")
    CONSOLE.error("Login nao concluido.")
    if diagnostic.get("http"):
        CONSOLE.detail("HTTP " + str(diagnostic["http"]))
    if error:
        CONSOLE.detail("error_type: " + error)
    if error == "needs_upgrade" or kind == "ClientNeedsUpgradeError":
        CONSOLE.warning("O servidor recusou uma etapa como needs_upgrade. O motivo exato nao pode ser determinado por esse erro sozinho.")
    elif kind in {"BadPassword", "BadCredentials"}:
        CONSOLE.warning("O servidor recusou as credenciais. Confira user= e senha= no TXT.")
    elif kind in {"ChallengeRequired", "ChallengeUnknownStep", "ChallengeSelfieCaptcha", "RecaptchaChallengeForm"}:
        CONSOLE.warning("Existe uma verificacao que precisa ser concluida no Instagram oficial.")
    elif diagnostic.get("http") == 429 or kind in {"PleaseWaitFewMinutes", "RateLimitError", "ClientThrottledError"}:
        CONSOLE.warning("O servidor limitou a tentativa. A execucao foi interrompida.")
    elif kind:
        CONSOLE.detail("Tipo: " + kind)


def show_flow():
    CONSOLE.section("Como usar")
    examples = (
        ("Entrar", "login"),
        ("Login com 2FA", "login --2fa autenticador"),
        ("Buscar perfis", 'buscar "carlos"'),
        ("Consultar perfil", "perfil nomedousuario"),
        ("Seguir perfil", "seguir nomedousuario"),
        ("Seguir por ID", "seguir --id 123456789"),
        ("Curtir publicacao", 'curtir "https://www.instagram.com/p/CODIGO/"'),
    )
    for label, example in examples:
        CONSOLE.field(label, "python instagram_login_apk.py " + example)
    CONSOLE.section("Arquivos")
    CONSOLE.detail("login.txt: user= e senha= para o comando login.")
    CONSOLE.detail("instagram_sessao_caa.json: sessao reaproveitada pelos outros comandos.")
    CONSOLE.section("Opcoes uteis")
    CONSOLE.detail('--limite 10  |  --saida resultado.json  |  --sem-cor  |  --help')
    CONSOLE.detail("Login Android CAA/Bloks; biblioteca nao oficial instagrapi==" + CLIENT_VERSION + ".")
    print()


# ---------------------------------------------------------------------------
# Acoes Android: consulta, assinatura e confirmacao do resultado no servidor.
# ---------------------------------------------------------------------------


ACTION_COMMANDS = {"buscar", "perfil", "seguir", "curtir"}
ACTION_READ = re.compile(r"(?:accounts/current_user/|users/search/|fbsearch/ig_typeahead/|"
                         r"users/[A-Za-z0-9_.]+/(?:usernameinfo|info)/|friendships/show/[0-9]+/|"
                         r"media/[0-9]+(?:_[0-9]+)?/info/)\Z")
ACTION_WRITE = re.compile(r"(?:friendships/create/[0-9]+/|media/[0-9]+(?:_[0-9]+)?/like/)\Z")


def public_text(value):
    return "".join(character for character in str(value or "") if character.isprintable())[:180]


def valid_user_id(value):
    value = str(value or "")
    if not re.fullmatch(r"[1-9][0-9]{0,29}", value):
        raise FlowError("Informe um ID numerico valido com --id.")
    return value


def valid_username(value):
    value = str(value or "").strip().removeprefix("@")
    if not re.fullmatch(r"[A-Za-z0-9_.]{1,30}", value):
        raise FlowError("Informe o username do perfil, por exemplo: perfil nomedousuario.")
    return value


def media_key(value):
    value = str(value or "").strip()
    if re.fullmatch(r"[1-9][0-9]{0,29}(?:_[1-9][0-9]{0,29})?", value):
        return value
    parsed = urlsplit(value)
    try:
        valid_port = parsed.port in {None, 443}
    except ValueError:
        valid_port = False
    if (parsed.scheme != "https" or parsed.hostname not in {"www.instagram.com", "instagram.com"}
            or parsed.username is not None or parsed.password is not None or not valid_port):
        raise FlowError("Use o ID da midia ou um link HTTPS do Instagram em /p/, /reel/ ou /tv/.")
    parts = [part for part in parsed.path.split("/") if part]
    if (len(parts) != 2 or parts[0] not in {"p", "reel", "tv"}
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", parts[1])):
        raise FlowError("Link de publicacao nao reconhecido. Use um link /p/, /reel/ ou o ID da midia.")
    # Mesma conversao pura usada por InstagramIdCodec na biblioteca 3.2.19.
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    number = 0
    for character in parts[1][:11]:
        number = (number << 6) | alphabet.index(character)
    if not number:
        raise FlowError("O link nao identifica uma midia valida.")
    return str(number)


def mobile_request(client, endpoint, *, params=None, payload=None, external=None):
    """Usar o transporte autenticado, sem login, fallback, desafio ou retries."""
    writing = payload is not None
    if not (ACTION_WRITE.fullmatch(endpoint) if writing else ACTION_READ.fullmatch(endpoint)):
        raise FlowError("Rota fora dos comandos Android implementados.")
    if not getattr(client, "action_session_validated", False) and endpoint != "accounts/current_user/":
        raise FlowError("A sessao precisa ser validada antes de executar uma acao.")
    kwargs = {"with_signature": False}
    if writing:
        values = dict(payload)
        values["_uid"] = str(client.user_id)
        values["_uuid"] = client.uuid
        outside = dict(external or {})
        if outside and (set(outside) != {"d"} or not endpoint.endswith("/like/") or outside["d"] not in {"0", "1"}):
            raise FlowError("Parametro externo ao signed_body invalido.")
        if set(values).intersection(outside):
            raise FlowError("Um parametro nao pode estar dentro e fora do signed_body.")
        # Montagem explicita evita o extra_sig da dependencia concatenar sem &.
        kwargs["data"] = {"signed_body": "SIGNATURE." + json.dumps(values, ensure_ascii=False, separators=(",", ":")), **outside}
    elif params is not None:
        kwargs["params"] = params
    client.private_requests_count += 1
    result = client._send_private_request(endpoint, **kwargs)
    if not isinstance(result, dict) or result.get("status") != "ok":
        raise FlowError("O servidor nao confirmou o resultado da etapa Android.")
    return result


def validate_action_session(client, owner):
    if not client.user_id or not (client.authorization or client.sessionid):
        raise FlowError("Nao ha sessao autenticada. Execute primeiro: python instagram_login_apk.py login")
    authenticated_id = str(client.user_id)
    result = mobile_request(client, "accounts/current_user/", params={"edit": "true"})
    user = result.get("user")
    if not isinstance(user, dict) or str(user.get("pk") or user.get("pk_id") or "") != authenticated_id:
        raise FlowError("O servidor nao confirmou a conta da sessao. Nenhuma acao foi executada.")
    name = user.get("username")
    # owner guarda o identificador usado no login, que pode ser um alias ou
    # username antigo. O ID autenticado acima e a referencia da conta.
    client.action_session_validated = True
    client.username = name if isinstance(name, str) and name else owner


def profile_record(user):
    if not isinstance(user, dict):
        return None
    identifier = str(user.get("pk") or user.get("pk_id") or user.get("id") or "")
    username = user.get("username")
    if not identifier.isdigit() or not isinstance(username, str) or not username:
        return None
    result = {"id": identifier, "username": public_text(username), "nome": public_text(user.get("full_name"))}
    for source, target in (("is_private", "privado"), ("is_verified", "verificado"),
                           ("follower_count", "seguidores"), ("following_count", "seguindo"), ("media_count", "publicacoes")):
        value = user.get(source)
        if isinstance(value, (bool, int)):
            result[target] = value
    return result


def get_profile(client, username=None, user_id=None):
    if user_id is not None:
        endpoint = "users/" + valid_user_id(user_id) + "/info/"
    else:
        username = valid_username(username)
        endpoint = "users/" + username + "/usernameinfo/"
    result = mobile_request(client, endpoint)
    profile = profile_record(result.get("user"))
    if profile is None:
        raise FlowError("O servidor nao devolveu um perfil identificavel.")
    if user_id is not None and profile["id"] != str(user_id):
        raise FlowError("O servidor devolveu um perfil diferente do ID solicitado.")
    if username is not None and profile["username"].casefold() != username.casefold():
        raise FlowError("O servidor devolveu um username diferente do solicitado.")
    return profile


def search_profiles(client, query, limit, general=False):
    params = {"count": str(limit), "timezone_offset": str(client.timezone_offset)}
    if general:
        params.update({"query": query, "context": "blended", "search_surface": "typeahead_search_page"})
        result = mobile_request(client, "fbsearch/ig_typeahead/", params=params)
    else:
        # Superficie presente nos chamadores do APK; sem tokens de ranking inventados.
        params.update({"q": query, "search_surface": "share_post_page", "return_unconnected_private": "true"})
        result = mobile_request(client, "users/search/", params=params)
    raw_users = result.get("users")
    recognized = isinstance(raw_users, list)
    raw = list(raw_users) if recognized else []
    if general:
        for key in ("list", "items"):
            rows = result.get(key)
            if not isinstance(rows, list):
                continue
            recognized = True
            for item in rows:
                if isinstance(item, dict) and isinstance(item.get("user"), dict):
                    raw.append(item["user"])
    if not recognized:
        raise FlowError("O servidor nao devolveu uma lista de resultados no formato esperado.")
    seen, profiles = set(), []
    for user in raw:
        if isinstance(user, dict) and isinstance(user.get("user"), dict):
            user = user["user"]
        profile = profile_record(user)
        if profile and profile["id"] not in seen:
            profiles.append(profile)
            seen.add(profile["id"])
        if len(profiles) >= limit:
            break
    return {"perfis": profiles, "quantidade": len(profiles)}


def follow_profile(client, user_id):
    identifier = valid_user_id(user_id)
    if identifier == str(client.user_id):
        raise FlowError("A conta logada nao pode seguir a si mesma.")
    result = mobile_request(client, "friendships/create/" + identifier + "/", payload={
        "user_id": identifier, "radio_type": "wifi-none", "is_offline_request": "false", "container_module": "profile"})
    relationship = result.get("friendship_status")
    if not isinstance(relationship, dict):
        check = mobile_request(client, "friendships/show/" + identifier + "/")
        relationship = check.get("friendship_status")
        if not isinstance(relationship, dict):
            relationship = check
    if relationship.get("following") is True:
        return {"id": identifier, "estado": "seguindo"}
    if relationship.get("outgoing_request") is True:
        return {"id": identifier, "estado": "solicitacao_pendente"}
    raise FlowError("O servidor nao confirmou seguir nem uma solicitacao pendente. O POST nao foi repetido.")


def get_media(client, key):
    result = mobile_request(client, "media/" + key + "/info/")
    items = result.get("items")
    if not isinstance(items, list) or not items or not isinstance(items[0], dict):
        raise FlowError("O servidor nao devolveu a publicacao solicitada.")
    media = items[0]
    identifier = str(media.get("id") or media.get("pk") or "")
    if (not re.fullmatch(r"[1-9][0-9]{0,29}(?:_[1-9][0-9]{0,29})?", identifier)
            or identifier.split("_", 1)[0] != key.split("_", 1)[0]):
        raise FlowError("O ID da publicacao retornada nao corresponde ao solicitado.")
    return media, identifier


def like_media(client, target):
    key = media_key(target)
    media, identifier = get_media(client, key)
    if media.get("has_liked") is True:
        return {"media_id": identifier, "estado": "ja_curtida"}
    mobile_request(client, "media/" + identifier + "/like/", payload={
        "media_id": identifier, "is_offline_request": "false", "module_name": "profile",
        "container_module": "profile", "radio_type": "wifi-none"}, external={"d": "0"})
    media, _ = get_media(client, identifier)
    if media.get("has_liked") is not True:
        raise FlowError("A chamada foi aceita, mas a curtida nao foi confirmada na consulta da midia. O POST nao foi repetido.")
    return {"media_id": identifier, "estado": "curtida"}


def action_plan(command):
    CONSOLE.section("Plano de execucao")
    CONSOLE.info("Reusar e validar instagram_sessao_caa.json, sem reenviar a senha.")
    if command == "buscar":
        CONSOLE.detail("GET users/search/ com q; --geral usa GET fbsearch/ig_typeahead/ com query.")
    elif command == "perfil":
        CONSOLE.detail("GET users/{username}/usernameinfo/ ou users/{id}/info/.")
    elif command == "seguir":
        CONSOLE.detail("Consultar o perfil; POST friendships/create/{id}/; verificar following/outgoing_request.")
    else:
        CONSOLE.detail("Resolver URL/ID; GET media/{id}/info/; POST media/{id}/like/ com d fora do signed_body; verificar has_liked.")


def run_action(args):
    if args.id_alvo and args.comando not in {"perfil", "seguir"}:
        raise FlowError("Use --id somente com perfil ou seguir.")
    if args.geral and args.comando != "buscar":
        raise FlowError("Use --geral somente com buscar.")
    if args.comando == "buscar":
        if not args.alvo or not args.alvo.strip() or len(args.alvo) > 200:
            raise FlowError('Informe a busca, por exemplo: buscar "nome do perfil"')
    elif args.comando in {"perfil", "seguir"}:
        if bool(args.alvo) == bool(args.id_alvo):
            raise FlowError("Informe um username OU --id ID, por exemplo: seguir nomedousuario.")
        valid_user_id(args.id_alvo) if args.id_alvo else valid_username(args.alvo)
    else:
        media_key(args.alvo)
    if not 1 <= args.limite <= 50:
        raise FlowError("Use --limite entre 1 e 50.")
    if not args.sessao.is_file():
        raise FlowError("Sessao nao encontrada. Execute primeiro: python instagram_login_apk.py login")
    settings = read_json(args.sessao)
    owner = settings.get("_local_login_username")
    if not isinstance(owner, str) or not owner:
        raise FlowError("Essa sessao nao identifica a conta local. Execute login antes das acoes.")
    expected = args.usuario.strip() if args.usuario else None
    credentials_path = args.credenciais or Path(__file__).resolve().with_name("login.txt")
    if expected is None and (args.credenciais is not None or credentials_path.exists()):
        expected, _ = read_credentials(credentials_path, require_password=False)
    if expected is not None and expected.casefold() != owner.casefold():
        raise FlowError("O usuario informado e diferente do dono da sessao. Escolha outro --sessao.")
    client = configure_client(load_client_class(), args.sessao, args.sessao_anterior, args.timeout)
    client.password = None
    client.action_session_validated = False
    outcome, error = "recusado", None
    started = time.monotonic()
    try:
        CONSOLE.info("Validando a sessao salva...")
        validate_action_session(client, owner)
        if args.comando == "buscar":
            CONSOLE.info("Buscando perfis no Instagram...")
            result = search_profiles(client, args.alvo.strip(), args.limite, args.geral)
            CONSOLE.profiles(result, args.alvo.strip())
        elif args.comando == "perfil":
            CONSOLE.info("Consultando o perfil...")
            result = get_profile(client, username=args.alvo if not args.id_alvo else None, user_id=args.id_alvo)
            CONSOLE.profile(result)
        elif args.comando == "seguir":
            CONSOLE.info("Consultando o perfil e enviando a acao de seguir...")
            target = valid_user_id(args.id_alvo) if args.id_alvo else get_profile(client, username=args.alvo)["id"]
            result = follow_profile(client, target)
            if result["estado"] == "seguindo":
                CONSOLE.success("Seguindo: confirmado pelo servidor.")
            else:
                CONSOLE.warning("Solicitacao para seguir: pendente no servidor.")
        else:
            CONSOLE.info("Consultando a publicacao e verificando a curtida...")
            result = like_media(client, args.alvo)
            CONSOLE.success("Essa publicacao ja estava curtida." if result["estado"] == "ja_curtida" else "Curtida confirmada pelo servidor.")
        save_client(client, args.sessao)
        if args.saida:
            save_private_json(args.saida, result)
            CONSOLE.success("Resultado salvo em: " + str(args.saida))
        CONSOLE.detail("Tempo: " + format(time.monotonic() - started, ".1f").replace(".", ",") + " s")
        print()
        outcome = "concluido"
        return 0
    except (KeyboardInterrupt, EOFError):
        outcome = "interrompido"
        raise
    except Exception as exc:
        error = exc
        CONSOLE.error("Comando nao concluido.")
        if isinstance(exc, FlowError):
            CONSOLE.warning(str(exc))
        elif type(exc).__name__ in {"LoginRequired", "ClientUnauthorizedError"}:
            CONSOLE.warning("A sessao foi recusada. O comando nao reenviou a senha nem iniciou um novo login.")
        elif type(exc).__name__ in {"ChallengeRequired", "TwoFactorRequired", "ChallengeSelfieCaptcha"}:
            CONSOLE.warning("O servidor pediu outra verificacao. O comando foi interrompido.")
        elif type(exc).__name__ in {"ClientThrottledError", "RateLimitError", "PleaseWaitFewMinutes", "FeedbackRequired"}:
            CONSOLE.warning("O servidor restringiu a acao. Nenhuma tentativa automatica foi repetida.")
        else:
            CONSOLE.detail("Tipo: " + type(exc).__name__)
        return 1
    finally:
        client.password = None
        diagnostic = safe_diagnostic(client, outcome, error)
        diagnostic.update({"comando": args.comando, "ramo_acoes": "rest_android_apk",
                           "sessao_validada_para_acao": client.action_session_validated})
        save_private_json(args.diagnostico, diagnostic)
        if outcome != "concluido":
            if diagnostic.get("http"):
                CONSOLE.detail("HTTP " + str(diagnostic["http"]))
            if diagnostic.get("erro"):
                CONSOLE.detail("error_type: " + diagnostic["erro"])
            CONSOLE.detail("Diagnostico sem senha ou tokens: " + str(args.diagnostico))


# ---------------------------------------------------------------------------
# Argumentos e execucao dos comandos.
# ---------------------------------------------------------------------------


def build_parser():
    parser = argparse.ArgumentParser(
        description="Instagram no terminal: login Android, perfis, seguir e curtir.",
        epilog='Exemplo: python instagram_login_apk.py buscar "carlos" --limite 10',
        add_help=False,
    )
    commands = parser.add_argument_group("Comandos")
    commands.add_argument("comando", nargs="?", choices=["login", "fluxo", "buscar", "perfil", "seguir", "curtir"], default="fluxo")
    commands.add_argument("alvo", nargs="?", help="Termo de busca, username ou URL/ID da publicacao.")
    account = parser.add_argument_group("Conta e arquivos")
    source = account.add_mutually_exclusive_group()
    source.add_argument("--usuario", help="Conta autenticada; no comando login, pede a senha no terminal.")
    source.add_argument("--credenciais", type=Path, help="TXT com user= e senha=.")
    account.add_argument("--sessao", type=Path, default=Path("instagram_sessao_caa.json"), help="Arquivo da sessao salva.")
    account.add_argument("--sessao-anterior", type=Path, default=Path("instagram_sessao.json"), help="Sessao antiga, para migrar IDs locais.")
    account.add_argument("--diagnostico", type=Path, help="Caminho do diagnostico sem senha ou tokens.")
    actions = parser.add_argument_group("Busca e resultados")
    actions.add_argument("--id", dest="id_alvo", help="ID numerico do perfil para perfil/seguir.")
    actions.add_argument("--limite", type=int, default=30, help="Quantidade maxima de perfis exibidos (1 a 50).")
    actions.add_argument("--geral", action="store_true", help="Em buscar, usar a busca geral do app.")
    actions.add_argument("--saida", type=Path, help="Salvar o resultado publico da acao em JSON.")
    terminal = parser.add_argument_group("Terminal")
    colors = terminal.add_mutually_exclusive_group()
    colors.add_argument("--cor", choices=["auto", "sempre", "nunca"], default="auto", help="Cores: automaticas, forcadas ou desligadas.")
    colors.add_argument("--sem-cor", dest="cor", action="store_const", const="nunca", help="Desativar as cores.")
    terminal.add_argument("--plano", action="store_true", help="Mostrar o fluxo, sem rede nem gravacao de arquivos.")
    terminal.add_argument("-h", "--help", action="help", help="Mostrar esta ajuda e sair.")
    account.add_argument("--timeout", type=float, default=30, help="Tempo limite das requisicoes em segundos.")
    account.add_argument("--2fa", dest="two_factor_method", choices=["autenticador"], default="autenticador",
                         help="Em login, escolher o app de autenticacao quando houver contexto 2FA valido.")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    CONSOLE.setup(args.cor)
    CONSOLE.banner(args.comando)
    args.diagnostico = args.diagnostico or Path("instagram_diagnostico_acoes.json" if args.comando in ACTION_COMMANDS else "instagram_diagnostico.json")
    if args.comando == "fluxo" or args.plano:
        if args.comando in ACTION_COMMANDS:
            action_plan(args.comando)
        else:
            show_flow()
        return 0
    if args.timeout <= 0:
        raise FlowError("O timeout precisa ser positivo.")
    credentials = args.credenciais or Path(__file__).resolve().with_name("login.txt")
    paths = [credentials.resolve(), args.sessao.resolve(), args.diagnostico.resolve()]
    if args.saida:
        paths.append(args.saida.resolve())
    if len(set(paths)) != len(paths) or args.diagnostico.resolve() == args.sessao_anterior.resolve():
        raise FlowError("Credenciais, sessao, diagnostico e saida precisam usar arquivos diferentes.")
    if args.comando in ACTION_COMMANDS:
        return run_action(args)
    if args.alvo or args.id_alvo or args.geral or args.saida:
        raise FlowError("Os argumentos de alvo e saida sao usados nos comandos de acao, nao em login.")
    client_class = load_client_class()
    if args.usuario is not None:
        username = args.usuario.strip()
        password = getpass.getpass("Senha: ")
        if not username or not password:
            raise FlowError("Usuario e senha precisam ser preenchidos.")
    else:
        username, password = read_credentials(args.credenciais or Path(__file__).resolve().with_name("login.txt"))
    client = configure_client(client_class, args.sessao, args.sessao_anterior, args.timeout)
    client.two_factor_method = args.two_factor_method
    owner = getattr(client, "_local_login_username", None)
    if owner and owner.casefold() != username.casefold():
        raise FlowError("Essa sessao pertence a outro usuario. Escolha outro arquivo --sessao.")
    if not owner and client.user_id:
        raise FlowError("A sessao ja autenticada nao tem identificacao local. Use outro arquivo --sessao.")
    client._local_login_username = username
    from instagrapi.exceptions import TwoFactorRequired
    outcome = "recusado"
    error = None
    try:
        # Persistir primeiro os IDs: um desafio deve continuar com o mesmo perfil.
        save_client(client, args.sessao)
        CONSOLE.info("Iniciando login Android. Aguarde a resposta do Instagram...")
        try:
            accepted = client.login(username, password)
        except TwoFactorRequired as exc:
            accepted = continue_two_factor(client, exc)
        if accepted and client.user_id and (client.authorization or client.sessionid):
            outcome = "autenticado"
            CONSOLE.success("Login aceito e credencial de sessao recebida.")
        else:
            outcome = "sem_sessao"
    except (KeyboardInterrupt, EOFError):
        outcome = "interrompido"
        raise
    except Exception as exc:
        error = exc
        if isinstance(exc, FlowError):
            CONSOLE.error(str(exc))
    finally:
        # A senha existe apenas durante esta execucao; get_settings nao a serializa.
        client.password = None
        diagnostic = safe_diagnostic(client, outcome, error)
        save_client(client, args.sessao)
        save_private_json(args.diagnostico, diagnostic)
    if outcome != "autenticado":
        show_failure(diagnostic)
        CONSOLE.detail("Diagnostico sem senha ou tokens: " + str(args.diagnostico))
        return 1
    CONSOLE.detail("Sessao salva em: " + str(args.sessao))
    print()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (FlowError, OSError) as exc:
        CONSOLE.error(str(exc), stream=sys.stderr)
        sys.exit(1)
    except (KeyboardInterrupt, EOFError):
        CONSOLE.warning("Operacao interrompida.")
        sys.exit(130)

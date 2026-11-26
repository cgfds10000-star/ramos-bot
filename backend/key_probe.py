"""Uma leitura de configuração pública; não recebe contas, senhas ou sessões."""
import json
import re
from pathlib import Path
import sys
import tempfile

import instagram_login_apk as core
from network import apply_instagram_proxy

MESSAGE_TERMS = frozenset('bad request user agent useragent mismatch version signature invalid app application id authorization token access denied missing unsupported required login authenticate authentication unauthorized challenge csrf update outdated device header headers url endpoint platform not allowed authenticated status fail wrong incorrect'.split())

def probe(proxy=""):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        client = core.configure_client(core.load_client_class(), root / "unused.json", root / "legacy.json", 8)
        apply_instagram_proxy(client, proxy)
        available = False
        error = None
        try:
            key_id, key = client.password_publickeys()
            available = core.password_key_from_headers({
                "ig-set-password-encryption-key-id": str(key_id),
                "ig-set-password-encryption-pub-key": key,
            }) is not None
        except Exception as failure:
            error = core.safe_symbol(type(failure).__name__)
        response = getattr(client, "last_response", None)
        terms = []
        anonymous_message = None
        try:
            data = response.json() if response is not None and len(response.content) <= 16384 else {}
            message = data.get('message') if isinstance(data, dict) else None
            if isinstance(message, str):
                terms = sorted(set(re.findall(r'[a-z]+', message.casefold())) & MESSAGE_TERMS)
                # Somente a resposta de uma chamada sem conta e sem proxy.
                if not proxy and re.fullmatch(r"[\w\s.,!?;:'()\-]{0,240}", message):
                    anonymous_message = message
        except (ValueError, AttributeError, TypeError):
            pass
        return {"available": available, "exception": error, "message_terms": terms,
                "anonymous_message": anonymous_message,
                **core.password_key_summary(response)}


if __name__ == "__main__":
    payload = json.loads(sys.stdin.readline())
    print(json.dumps(probe(payload.get("proxy", ""))), flush=True)

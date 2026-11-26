"""Uma leitura de configuração pública; não recebe contas, senhas ou sessões."""
import json
from pathlib import Path
import sys
import tempfile

import instagram_login_apk as core
from network import apply_instagram_proxy


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
        return {"available": available, "exception": error,
                **core.password_key_summary(getattr(client, "last_response", None))}


if __name__ == "__main__":
    payload = json.loads(sys.stdin.readline())
    print(json.dumps(probe(payload.get("proxy", ""))), flush=True)

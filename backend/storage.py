"""Persistência atômica; Fernet quando RAMOS_ENCRYPTION_KEY está configurada."""
import json
import os
from pathlib import Path
import tempfile

PREFIX = b"RAMOS-ENC1\n"

def read_json(path):
    raw = Path(path).read_bytes()
    if raw.startswith(PREFIX):
        from cryptography.fernet import Fernet
        raw = Fernet(os.environ["RAMOS_ENCRYPTION_KEY"].encode()).decrypt(raw[len(PREFIX):])
    return json.loads(raw.decode("utf-8-sig"))

def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, ensure_ascii=False, indent=2).encode()
    key = os.environ.get("RAMOS_ENCRYPTION_KEY")
    if key:
        from cryptography.fernet import Fernet
        raw = PREFIX + Fernet(key.encode()).encrypt(raw)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".ramos-")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

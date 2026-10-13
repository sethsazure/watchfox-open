"""Passwords, API keys, session tokens and encrypted credentials."""
import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any, Optional, Tuple

from cryptography.fernet import Fernet, InvalidToken

from . import config


def _fernet() -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(config.SECRET.encode() + b"|enc").digest()))


def encrypt(obj: Any) -> str:
    return _fernet().encrypt(json.dumps(obj).encode()).decode()


def decrypt(token: str) -> Any:
    try:
        return json.loads(_fernet().decrypt(token.encode()))
    except InvalidToken:
        raise ValueError("Stored credentials cannot be decrypted (was WATCHFOX_SECRET changed?)")


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${h.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, salt, h = stored.split("$")
        got = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(got.hex(), h)
    except Exception:
        return False


def new_api_key() -> Tuple[str, str, str]:
    """(full key, display prefix, sha256 of key). The full key is shown to the user once and never stored."""
    full = "wf_live_" + secrets.token_urlsafe(30)
    return full, full[:14], hashlib.sha256(full.encode()).hexdigest()


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def sign(payload: dict, ttl: int) -> str:
    body = base64.urlsafe_b64encode(json.dumps({**payload, "exp": int(time.time()) + ttl}).encode()).decode().rstrip("=")
    mac = hmac.new(config.SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{mac}"


def verify(token: str) -> Optional[dict]:
    try:
        body, mac = token.rsplit(".", 1)
        good = hmac.new(config.SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(mac, good):
            return None
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        return data if data.get("exp", 0) >= time.time() else None
    except Exception:
        return None


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"

"""Шифрование биометрических записей (AES-GCM, ключ на пользователя)."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

VAULT_VERSION = 1
_PBKDF2_ITERATIONS = 600_000


def _machine_fingerprint() -> str:
    for path in (Path("/etc/machine-id"), Path("/var/lib/dbus/machine-id")):
        if path.is_file():
            value = path.read_text(encoding="utf-8").strip()
            if value:
                return value
    return os.environ.get("HOSTNAME", "urblock-local")


def _vault_secret(user_id: str) -> bytes:
    extra = os.environ.get("URBLOCK_VAULT_KEY", "")
    material = f"urblock-vault:{user_id}:{_machine_fingerprint()}:{extra}"
    return hashlib.sha256(material.encode("utf-8")).digest()


def _user_salt_path(user_dir: Path) -> Path:
    return user_dir / ".vault_salt"


def _load_or_create_salt(user_dir: Path) -> bytes:
    path = _user_salt_path(user_dir)
    user_dir.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        raw = path.read_bytes()
        if len(raw) >= 16:
            return raw[:16]
    salt = os.urandom(16)
    path.write_bytes(salt)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return salt


def _derive_key(user_id: str, user_dir: Path) -> bytes:
    salt = _load_or_create_salt(user_dir)
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=_PBKDF2_ITERATIONS,
    )
    return kdf.derive(_vault_secret(user_id))


def encrypt_bytes(user_id: str, user_dir: Path, plaintext: bytes) -> bytes:
    key = _derive_key(user_id, user_dir)
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, None)
    return nonce + ciphertext


def decrypt_bytes(user_id: str, user_dir: Path, blob: bytes) -> bytes:
    if len(blob) < 13:
        raise ValueError("vault blob too short")
    key = _derive_key(user_id, user_dir)
    nonce, ciphertext = blob[:12], blob[12:]
    return AESGCM(key).decrypt(nonce, ciphertext, None)


def encrypt_record(user_id: str, user_dir: Path, payload: dict[str, Any]) -> bytes:
    plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return encrypt_bytes(user_id, user_dir, plaintext)


def decrypt_record(user_id: str, user_dir: Path, blob: bytes) -> dict[str, Any]:
    plaintext = decrypt_bytes(user_id, user_dir, blob)
    data = json.loads(plaintext.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("vault payload must be an object")
    return data


def pack_biometric_payload(
    *,
    bio_id: str,
    user_id: str,
    score: float,
    landmarks: list,
    image_jpeg: bytes,
    created_at: str,
) -> dict[str, Any]:
    return {
        "v": VAULT_VERSION,
        "id": bio_id,
        "user_id": user_id,
        "score": score,
        "landmarks": landmarks,
        "created_at": created_at,
        "image_b64": base64.b64encode(image_jpeg).decode("ascii"),
    }


def unpack_image_jpeg(record: dict[str, Any]) -> bytes:
    raw = record.get("image_b64")
    if not isinstance(raw, str):
        raise ValueError("missing image_b64 in vault record")
    return base64.b64decode(raw)

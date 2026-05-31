"""Проверка пароля ОС (для параллельного входа вместе с лицом)."""

from __future__ import annotations

import ctypes
import os
import ctypes.util

_lib = ctypes.CDLL(ctypes.util.find_library("crypt") or "libcrypt.so.1", use_errno=True)
_lib.crypt.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
_lib.crypt.restype = ctypes.c_char_p


def _shadow_hash(username: str) -> str | None:
    if os.geteuid() != 0:
        return None
    try:
        with open("/etc/shadow", encoding="utf-8") as f:
            for line in f:
                login, hashed, *_ = line.split(":", 2)
                if login == username:
                    return hashed or None
    except OSError:
        pass
    return None


def verify_password(username: str, password: str) -> bool:
    if not password:
        return False
    hashed = _shadow_hash(username)
    if not hashed or hashed[0] in ("!", "*"):
        return False
    computed = _lib.crypt(password.encode("utf-8"), hashed.encode("utf-8"))
    if not computed:
        return False
    return computed.decode("utf-8", errors="replace") == hashed

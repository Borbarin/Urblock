"""Идентификация текущего пользователя ОС для изоляции данных."""

from __future__ import annotations

import getpass
import os


def get_current_user_id() -> str:
    """Логин пользователя ОС, под которым запущено приложение."""
    for key in ("URBLOCK_USER", "PAM_USER", "USER", "USERNAME"):
        value = os.environ.get(key)
        if value:
            return value
    try:
        return getpass.getuser()
    except Exception:
        return os.environ.get("USER", os.environ.get("USERNAME", "default"))

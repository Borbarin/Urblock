from __future__ import annotations

from pathlib import Path

_AGREEMENT_PATH = Path(__file__).resolve().parent / "user_agreement.ru.txt"


def load_user_agreement() -> str:
    if _AGREEMENT_PATH.is_file():
        return _AGREEMENT_PATH.read_text(encoding="utf-8").strip()
    return "Текст пользовательского соглашения недоступен."

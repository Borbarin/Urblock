"""Состояние профиля: заблокирован / разблокирован по лицу."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class ProfileSession:
    locked: bool = True
    owner_name: str = ""
    owner_entry_id: str = ""
    match_score: float = 0.0
    unlocked_at: str = ""

    def unlock(self, name: str, entry_id: str, score: float) -> None:
        self.locked = False
        self.owner_name = name
        self.owner_entry_id = entry_id
        self.match_score = score
        self.unlocked_at = datetime.now().isoformat(timespec="seconds")

    def lock(self) -> None:
        self.locked = True
        self.owner_name = ""
        self.owner_entry_id = ""
        self.match_score = 0.0
        self.unlocked_at = ""

"""Состояние фоновой проверки лица между preflight и основным verify."""

from __future__ import annotations

import json
import os
import signal
import time
from pathlib import Path

SESSION_DIR = Path("/var/run/urblock-verify")
STALE_SEC = 90.0


def _session_path(user: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in user)
    return SESSION_DIR / f"{safe}.json"


def _runtime_session_path(user: str) -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        return _session_path(user)
    return Path(runtime) / "urblock-verify" / f"{user}.json"


def session_path(user: str) -> Path:
    if os.geteuid() == 0:
        return _session_path(user)
    return _runtime_session_path(user)


class VerifySession:
    def __init__(
        self,
        *,
        user: str,
        pid: int,
        pipe_path: str = "",
        started_at: float | None = None,
    ) -> None:
        self.user = user
        self.pid = pid
        self.status = "starting"
        self.result = ""
        self.pipe_path = pipe_path
        self.started_at = started_at or time.time()
        self.updated_at = self.started_at

    def to_dict(self) -> dict:
        return {
            "user": self.user,
            "pid": self.pid,
            "status": self.status,
            "result": self.result,
            "pipe_path": self.pipe_path,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> VerifySession:
        sess = cls(
            user=str(data.get("user", "")),
            pid=int(data.get("pid", 0)),
            pipe_path=str(data.get("pipe_path", "")),
            started_at=float(data.get("started_at", time.time())),
        )
        sess.status = str(data.get("status", "starting"))
        sess.result = str(data.get("result", ""))
        sess.updated_at = float(data.get("updated_at", sess.started_at))
        return sess

    def save(self) -> None:
        path = session_path(self.user)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.updated_at = time.time()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls, user: str) -> VerifySession | None:
        path = session_path(user)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if data.get("user") != user:
            return None
        age = time.time() - float(data.get("updated_at", 0))
        if age > STALE_SEC:
            cls.clear(user)
            return None
        return cls.from_dict(data)

    @classmethod
    def clear(cls, user: str) -> None:
        path = session_path(user)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    def is_process_alive(self) -> bool:
        if self.pid <= 0:
            return False
        try:
            os.kill(self.pid, 0)
        except OSError:
            return False
        return True

    def set_status(self, status: str) -> None:
        self.status = status
        self.save()

    def set_result(self, result: str) -> None:
        self.result = result
        self.save()

    def terminate(self, sig: int = signal.SIGTERM) -> None:
        if self.pid > 0:
            try:
                os.kill(self.pid, sig)
            except OSError:
                pass

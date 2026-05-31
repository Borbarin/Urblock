#!/usr/bin/env python3
"""Запуск проверки лица при блокировке экрана GNOME (Win+L)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from daemon_launcher import _log, start_daemon, stop_daemon

_login_dir = Path(__file__).resolve().parent
if str(_login_dir) not in sys.path:
    sys.path.insert(0, str(_login_dir))
from verify_session import VerifySession  # noqa: E402


def _user() -> str:
    return os.environ.get("USER") or os.environ.get("LOGNAME") or "ububu"


def _monitor_gnome() -> int:
    user = _user()
    _log(f"lock-agent: watching org.gnome.ScreenSaver for user={user}")
    proc = subprocess.Popen(
        ["gdbus", "monitor", "-e", "-d", "org.gnome.ScreenSaver"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )
    if proc.stdout is None:
        _log("lock-agent: gdbus monitor failed")
        return 1

    locked = False
    try:
        for line in proc.stdout:
            low = line.lower()
            if "activechanged" not in low:
                continue
            is_locked = "true" in low
            if is_locked and not locked:
                locked = True
                _log("lock-agent: screen locked")
                VerifySession.clear(user)
                start_daemon(user, log_prefix="lock-agent")
            elif not is_locked and locked:
                locked = False
                _log("lock-agent: screen unlocked")
                stop_daemon(user, log_prefix="lock-agent")
    except KeyboardInterrupt:
        pass
    finally:
        proc.terminate()
        stop_daemon(user, log_prefix="lock-agent")
    return 0


def main() -> int:
    if not shutil.which("gdbus"):
        _log("lock-agent: gdbus not found — установите glib2-utils")
        return 1
    return _monitor_gnome()


if __name__ == "__main__":
    raise SystemExit(main())

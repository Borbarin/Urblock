#!/usr/bin/env python3
"""Камера и распознавание на экране входа GDM (после разлогина)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from daemon_launcher import (
    _log,
    autonomous_enabled,
    daemon_running,
    load_install_env,
    start_daemon,
    stop_daemon,
)

try:
    from gdm_login import gdm_login_in_progress, login_confirmed_for_user
except ImportError:
    def gdm_login_in_progress() -> bool:
        return False

    def login_confirmed_for_user(username: str) -> bool:
        return False

_GDM_DEST = "org.gnome.DisplayManager"
_USER_RE = re.compile(r"'([a-z_][a-z0-9_-]*)'", re.IGNORECASE)
_POLL_SEC = 0.35


def _bootstrap_gui_path() -> None:
    install = load_install_env()
    if install.get("URBLOCK_DATA_DIR"):
        os.environ.setdefault("URBLOCK_DATA_DIR", install["URBLOCK_DATA_DIR"])
    if install.get("URBLOCK_GUI_ROOT"):
        gui = Path(install["URBLOCK_GUI_ROOT"])
    else:
        gui = Path(__file__).resolve().parents[2] / "urblock_gui"
    path = str(gui)
    if path not in sys.path:
        sys.path.insert(0, path)


def _users_with_gallery() -> list[str]:
    _bootstrap_gui_path()
    from config import USERS_DIR
    from storage import user_has_gallery

    if not USERS_DIR.is_dir():
        return []
    return sorted(
        entry.name
        for entry in USERS_DIR.iterdir()
        if entry.is_dir() and user_has_gallery(entry.name)
    )


def _loginctl_sessions() -> list[dict[str, str]]:
    if not shutil.which("loginctl"):
        return []
    try:
        proc = subprocess.run(
            ["loginctl", "list-sessions", "--no-legend"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    rows: list[dict[str, str]] = []
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        sid, uid, user = parts[0], parts[1], parts[2]
        sess_class = parts[5] if len(parts) > 5 else ""
        rows.append(
            {
                "id": sid,
                "uid": uid,
                "user": user,
                "class": sess_class,
            }
        )
    return rows


def _session_runtime(sid: str) -> tuple[str, str]:
    """State и Active из loginctl (closing-сессия после разлогина ≠ вход в систему)."""
    if not shutil.which("loginctl"):
        return "", ""
    try:
        proc = subprocess.run(
            [
                "loginctl",
                "show-session",
                sid,
                "-p",
                "State",
                "-p",
                "Active",
            ],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "", ""
    if proc.returncode != 0:
        return "", ""
    state, active = "", ""
    for line in proc.stdout.splitlines():
        if line.startswith("State="):
            state = line.split("=", 1)[1].strip()
        elif line.startswith("Active="):
            active = line.split("=", 1)[1].strip()
    return state, active


def _active_console_users() -> set[str]:
    logged_in: set[str] = set()
    for sess in _loginctl_sessions():
        if sess["class"] != "user":
            continue
        state, active = _session_runtime(sess["id"])
        if state == "active" and active == "yes":
            logged_in.add(sess["user"])
    return logged_in


def _greeter_visible() -> bool:
    """Экран входа GDM: greeter-сессия или нет активной пользовательской сессии."""
    sessions = _loginctl_sessions()
    if not sessions:
        return False
    if any(s["class"] == "greeter" for s in sessions):
        return True
    return not _active_console_users()


def _pick_greeter_user(hint: str | None) -> str | None:
    candidates = _users_with_gallery()
    if not candidates:
        return None
    if hint and hint in candidates:
        return hint
    if len(candidates) == 1:
        return candidates[0]
    return None


def _parse_gdm_monitor_line(line: str) -> str | None:
    low = line.lower()
    if "createuserdisplay" not in low and "openreauthenticationchannel" not in low:
        return None
    for match in _USER_RE.finditer(line):
        name = match.group(1)
        if name in ("gdm", "sddm", "lightdm", "org", "true", "false"):
            continue
        if name in _users_with_gallery():
            return name
    return None


def _monitor_gdm_signals(active_user: dict[str, float]) -> None:
    if not shutil.which("gdbus"):
        _log("greeter-agent: gdbus not found")
        return
    proc = subprocess.Popen(
        ["gdbus", "monitor", "--system", "--dest", _GDM_DEST],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )
    if proc.stdout is None:
        return
    try:
        for line in proc.stdout:
            user = _parse_gdm_monitor_line(line)
            if not user:
                continue
            if not _greeter_visible():
                continue
            now = time.monotonic()
            last = active_user.get(user, 0.0)
            if now - last < 2.0:
                continue
            active_user[user] = now
            _log(f"greeter-agent: GDM selected user={user}")
            for other in list(active_user):
                if other != user:
                    stop_daemon(other, log_prefix="greeter-agent")
                    active_user.pop(other, None)
            start_daemon(user, log_prefix="greeter-agent", greeter=True)
    except KeyboardInterrupt:
        pass
    finally:
        proc.terminate()


def _poll_greeter(active_user: dict[str, float]) -> None:
    last_greeter = False
    defer_stop_until = 0.0
    while True:
        greeter = _greeter_visible()
        console_users = _active_console_users()
        now = time.monotonic()

        if greeter and not last_greeter:
            _log("greeter-agent: login screen visible — start detection")
            user = _pick_greeter_user(None)
            if user:
                active_user[user] = time.monotonic()
                start_daemon(user, log_prefix="greeter-agent", greeter=True)
            else:
                for name in _users_with_gallery():
                    active_user[name] = time.monotonic()
                    start_daemon(name, log_prefix="greeter-agent", greeter=True)
                    break

        if not greeter and last_greeter:
            if gdm_login_in_progress():
                defer_stop_until = now + 25.0
                _log("greeter-agent: login in progress — defer stop")
            else:
                _log("greeter-agent: left login screen")
                for name in list(active_user):
                    stop_daemon(name, log_prefix="greeter-agent")
                active_user.clear()

        for name in list(active_user):
            if login_confirmed_for_user(name):
                _log(f"greeter-agent: user {name} logged in (PAM+seat0)")
                stop_daemon(name, log_prefix="greeter-agent")
                active_user.pop(name, None)
            elif name in console_users and not greeter:
                _log(f"greeter-agent: user {name} in loginctl, ждём PAM")
            elif not greeter:
                if gdm_login_in_progress() and now < defer_stop_until:
                    continue
                stop_daemon(name, log_prefix="greeter-agent")
                active_user.pop(name, None)
            elif not daemon_running(name) and greeter:
                start_daemon(name, log_prefix="greeter-agent", greeter=True)

        last_greeter = greeter
        time.sleep(_POLL_SEC)


def main() -> int:
    if not autonomous_enabled():
        _log("greeter-agent: auto_detect disabled in settings")
        return 0
    if not shutil.which("gdbus"):
        _log("greeter-agent: install glib2-utils (gdbus)")
        return 1

    _log("greeter-agent: watching GDM login screen (logout → greeter)")
    active: dict[str, float] = {}

    import threading

    t = threading.Thread(
        target=_monitor_gdm_signals,
        args=(active,),
        name="urblock-gdm-monitor",
        daemon=True,
    )
    t.start()
    try:
        _poll_greeter(active)
    except KeyboardInterrupt:
        pass
    finally:
        for name in list(active):
            stop_daemon(name, log_prefix="greeter-agent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

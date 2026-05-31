"""Разблокировка без Enter: GNOME ScreenSaver + loginctl."""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

_LOG = Path("/var/log/urblock-verify.log")

_SESSION_ENV_KEYS = (
    "DISPLAY",
    "XAUTHORITY",
    "XDG_RUNTIME_DIR",
    "WAYLAND_DISPLAY",
    "DBUS_SESSION_BUS_ADDRESS",
)

_GNOME_SCREENSAVER = (
    ("org.gnome.ScreenSaver", "/org/gnome/ScreenSaver"),
    ("org.gnome.DebianScreenSaver", "/org/gnome/ScreenSaver"),
)


def _log(msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    try:
        with _LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _session_env(username: str) -> dict[str, str]:
    from display_env import resolve_display_env

    return resolve_display_env(username)


def _run_as_user(
    username: str, cmd: list[str], env: dict[str, str]
) -> subprocess.CompletedProcess[str] | None:
    merged = os.environ.copy()
    merged.update(env)
    env_list = [f"{k}={merged[k]}" for k in _SESSION_ENV_KEYS if merged.get(k)]
    if os.geteuid() == 0 and username and shutil.which("runuser"):
        full = ["runuser", "-u", username, "--", "env", *env_list, *cmd]
    else:
        full = ["env", *env_list, *cmd] if env_list else cmd
    try:
        return subprocess.run(
            full,
            env=merged,
            capture_output=True,
            text=True,
            timeout=6,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _log(f"unlock: cmd error {exc!r}")
        return None


def _session_props(sid: str) -> dict[str, str]:
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
                "-p",
                "Class",
                "-p",
                "LockedHint",
            ],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if proc.returncode != 0:
        return {}
    props: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if "=" in line:
            key, val = line.split("=", 1)
            props[key] = val.strip()
    return props


def _session_ids_for_user(username: str) -> list[str]:
    if not Path("/usr/bin/loginctl").is_file():
        return []
    try:
        listed = subprocess.run(
            ["loginctl", "list-sessions", "--no-legend"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if listed.returncode != 0:
        return []

    ids: list[str] = []
    for line in listed.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[2] == username:
            ids.append(parts[0])
    return ids


def _active_user_session_ids(username: str) -> list[str]:
    """Активные user-сессии; заблокированные (LockedHint) — в начале списка."""
    locked: list[str] = []
    active: list[str] = []
    for sid in _session_ids_for_user(username):
        props = _session_props(sid)
        if props.get("Class") != "user":
            continue
        if props.get("State") != "active" or props.get("Active") != "yes":
            continue
        if props.get("LockedHint") == "yes":
            locked.append(sid)
        else:
            active.append(sid)
    return locked + active


def _gnome_screensaver_unlock(username: str, env: dict[str, str]) -> bool:
    if not shutil.which("gdbus"):
        return False

    for dest, path in _GNOME_SCREENSAVER:
        proc = _run_as_user(
            username,
            [
                "gdbus",
                "call",
                "--session",
                "--dest",
                dest,
                "--object-path",
                path,
                "--method",
                "org.gnome.ScreenSaver.SetActive",
                "false",
            ],
            env,
        )
        if proc is None:
            continue
        if proc.returncode == 0:
            _log(f"unlock: {dest} SetActive(false) ok")
            return True
        err = (proc.stderr or proc.stdout or "").strip()
        if err:
            _log(f"unlock: {dest} failed: {err[:120]}")

    return False


def _loginctl_unlock(username: str) -> bool:
    targets = _active_user_session_ids(username)
    if not targets:
        _log(f"unlock: нет активных user-сессий для {username}")
        return False

    ok = False
    for sid in targets:
        props = _session_props(sid)
        try:
            proc = subprocess.run(
                ["loginctl", "unlock-session", sid],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            _log(f"unlock: loginctl sid={sid} error {exc!r}")
            continue
        if proc.returncode == 0:
            hint = props.get("LockedHint", "?")
            _log(f"unlock: loginctl session {sid} ok (LockedHint={hint})")
            ok = True
        else:
            err = (proc.stderr or proc.stdout or "").strip()
            if err:
                _log(f"unlock: loginctl sid={sid}: {err[:80]}")

    return ok


def user_has_active_session(username: str) -> bool:
    """Есть ли уже открытая сессия (Win+L). На экране GDM после разлогина — нет."""
    if not Path("/usr/bin/loginctl").is_file():
        return False
    try:
        listed = subprocess.run(
            ["loginctl", "list-sessions", "--no-legend"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if listed.returncode != 0:
        return False
    for line in listed.stdout.splitlines():
        parts = line.split()
        if len(parts) < 6 or parts[2] != username or parts[5] != "user":
            continue
        props = _session_props(parts[0])
        if props.get("State") == "active" and props.get("Active") == "yes":
            return True
    return False


def unlock_user_session(username: str) -> bool:
    """Снимает блокировку GNOME / systemd после распознавания лица (только Win+L)."""
    if os.environ.get("URBLOCK_AUTO_UNLOCK", "1") == "0":
        return False
    if os.environ.get("URBLOCK_GREETER") == "1":
        return False
    if not user_has_active_session(username):
        _log(f"unlock: skip loginctl for {username} (экран входа GDM, не блокировка)")
        return False

    env = _session_env(username)

    # GNOME Win+L: сначала ScreenSaver (снимает экран блокировки), затем loginctl.
    if _gnome_screensaver_unlock(username, env):
        return True
    if _loginctl_unlock(username):
        return True

    _log(
        f"unlock: не удалось разблокировать {username} "
        "(GNOME ScreenSaver + loginctl)"
    )
    return False

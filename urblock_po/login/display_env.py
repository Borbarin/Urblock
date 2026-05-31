"""DISPLAY / Wayland / XAUTHORITY для экрана блокировки (сессия пользователя)."""

from __future__ import annotations

import os
import pwd
import shutil
import subprocess
from pathlib import Path

_PRINTENV_KEYS = (
    "DISPLAY",
    "XAUTHORITY",
    "WAYLAND_DISPLAY",
    "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS",
)


def _uid(username: str) -> int | None:
    try:
        return pwd.getpwnam(username).pw_uid
    except KeyError:
        return None


def _runuser_printenv(username: str) -> dict[str, str]:
    """Живые переменные из сессии пользователя (надёжно на Wayland)."""
    if not username or not shutil.which("runuser"):
        return {}
    try:
        proc = subprocess.run(
            ["runuser", "-u", username, "--", "printenv", *_PRINTENV_KEYS],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if proc.returncode != 0:
        return {}
    out: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        key, sep, val = line.partition("=")
        if sep and val:
            out[key] = val
    return out


def _loginctl_display(uid: int) -> str | None:
    if not Path("/usr/bin/loginctl").is_file():
        return None
    try:
        out = subprocess.run(
            ["loginctl", "list-sessions", "--no-legend"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        session_id, session_uid = parts[0], parts[1]
        try:
            if int(session_uid) != uid:
                continue
        except ValueError:
            continue
        try:
            show = subprocess.run(
                ["loginctl", "show-session", session_id, "-p", "Display", "--value"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if show.returncode == 0 and show.stdout.strip():
            return show.stdout.strip()
    return None


def _x11_socket_exists(display: str) -> bool:
    if not display.startswith(":"):
        return False
    num = display[1:].split(".", 1)[0]
    if not num.isdigit():
        return False
    return Path(f"/tmp/.X11-unix/X{num}").exists()


def resolve_display_env(username: str | None = None) -> dict[str, str]:
    """Переменные для UI на экране блокировки."""
    user = username or os.environ.get("PAM_USER") or os.environ.get("URBLOCK_USER") or ""
    env: dict[str, str] = {}

    if user and user not in ("gdm", "sddm", "lightdm"):
        env.update(_runuser_printenv(user))

    uid = _uid(user) if user else None
    if uid is not None:
        runtime = Path(f"/run/user/{uid}")
        if runtime.is_dir():
            env.setdefault("XDG_RUNTIME_DIR", str(runtime))
            bus = runtime / "bus"
            if bus.exists():
                env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={bus}")

            if "WAYLAND_DISPLAY" not in env:
                for wl in sorted(runtime.glob("wayland-*")):
                    if wl.is_socket() or wl.name.startswith("wayland-"):
                        env["WAYLAND_DISPLAY"] = wl.name
                        break

            if "XAUTHORITY" not in env:
                mutter = [
                    p
                    for p in runtime.iterdir()
                    if p.name.startswith(".mutter-Xwaylandauth") and p.is_file()
                ]
                if mutter:
                    mutter.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                    env["XAUTHORITY"] = str(mutter[0])

            if "DISPLAY" not in env:
                display = _loginctl_display(uid)
                if display:
                    env["DISPLAY"] = display

    display = env.get("DISPLAY", "")
    # На Wayland DISPLAY может быть без сокета в /tmp/.X11-unix — не сбрасываем,
    # иначе gdbus не находит session bus («Cannot autolaunch D-Bus without X11»).
    if display and not _x11_socket_exists(display) and not env.get("WAYLAND_DISPLAY"):
        env.pop("DISPLAY", None)

    if "DISPLAY" not in env:
        for candidate in (":1", ":0"):
            if _x11_socket_exists(candidate):
                env["DISPLAY"] = candidate
                break

    if "XAUTHORITY" not in env and uid is not None:
        runtime = Path(f"/run/user/{uid}")
        if runtime.is_dir():
            for p in runtime.iterdir():
                if p.name.startswith(".mutter-Xwaylandauth") and p.is_file():
                    env["XAUTHORITY"] = str(p)
                    break

    if "XAUTHORITY" not in env:
        for candidate in (
            Path("/var/lib/gdm/:0.Xauth"),
            Path("/var/lib/gdm3/:0.Xauth"),
            Path("/run/user/42/gdm/Xauthority"),
        ):
            if candidate.is_file():
                env["XAUTHORITY"] = str(candidate)
                break

    return env


def greeter_screen_active() -> bool:
    """Экран входа GDM (в т.ч. смена пользователя при активной сессии в фоне)."""
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
        if len(parts) >= 6 and parts[5] == "greeter":
            return True
    return False


def greeter_session_user() -> str | None:
    """Пользователь сессии GDM greeter (gdm-greeter), не целевой логин."""
    if not Path("/usr/bin/loginctl").is_file():
        return None
    try:
        listed = subprocess.run(
            ["loginctl", "list-sessions", "--no-legend"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if listed.returncode != 0:
        return None
    for line in listed.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 6 and parts[5] == "greeter":
            return parts[2]
    return None


def _greeter_session_display() -> dict[str, str]:
    """DISPLAY / Wayland / DBUS сессии GDM greeter (экран входа после разлогина)."""
    greeter_user = greeter_session_user()
    env: dict[str, str] = {}
    if greeter_user:
        env.update(_runuser_printenv(greeter_user))
        uid = _uid(greeter_user)
        if uid is not None:
            runtime = Path(f"/run/user/{uid}")
            if runtime.is_dir():
                env.setdefault("XDG_RUNTIME_DIR", str(runtime))
                bus = runtime / "bus"
                if bus.exists():
                    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={bus}")
                if "WAYLAND_DISPLAY" not in env:
                    for wl in sorted(runtime.glob("wayland-*")):
                        if wl.is_socket() or wl.name.startswith("wayland-"):
                            env["WAYLAND_DISPLAY"] = wl.name
                            break
                if "XAUTHORITY" not in env:
                    mutter = [
                        p
                        for p in runtime.iterdir()
                        if p.name.startswith(".mutter-Xwaylandauth") and p.is_file()
                    ]
                    if mutter:
                        mutter.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                        env["XAUTHORITY"] = str(mutter[0])
                if "DISPLAY" not in env:
                    disp = _loginctl_display(uid)
                    if disp:
                        env["DISPLAY"] = disp

    if not Path("/usr/bin/loginctl").is_file():
        return env
    try:
        listed = subprocess.run(
            ["loginctl", "list-sessions", "--no-legend"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return env
    if listed.returncode != 0:
        return env

    for line in listed.stdout.splitlines():
        parts = line.split()
        if len(parts) < 6 or parts[5] != "greeter":
            continue
        sid = parts[0]
        try:
            show = subprocess.run(
                ["loginctl", "show-session", sid, "-p", "Display", "--value"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if show.returncode == 0 and show.stdout.strip():
            env.setdefault("DISPLAY", show.stdout.strip())
        break

    for gdm_name in ("gdm", "Debian-gdm"):
        try:
            gdm_uid = pwd.getpwnam(gdm_name).pw_uid
        except KeyError:
            continue
        runtime = Path(f"/run/user/{gdm_uid}")
        if runtime.is_dir():
            env.setdefault("XDG_RUNTIME_DIR", str(runtime))
            bus = runtime / "bus"
            if bus.exists() and "DBUS_SESSION_BUS_ADDRESS" not in env:
                env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={bus}"
        if "XAUTHORITY" not in env:
            for path in (
                Path(f"/var/lib/gdm3/.config/monitors.xml"),
                Path("/var/lib/gdm/:0.Xauth"),
                Path("/var/lib/gdm3/:0.Xauth"),
            ):
                if path.suffix == "Xauth" and path.is_file():
                    env["XAUTHORITY"] = str(path)
                    break
        break

    return env


def resolve_greeter_overlay_env(target_username: str | None = None) -> dict[str, str]:
    """Окружение для окна/уведомлений на экране входа GDM."""
    env = _greeter_session_display()
    if target_username:
        env["URBLOCK_TARGET_USER"] = target_username
    return env


def daemon_child_env(user: str, base: dict[str, str] | None = None) -> dict[str, str]:
    """Окружение для фонового verify (PAM / lock-agent / greeter-agent)."""
    child = dict(base or os.environ)
    session = resolve_display_env(user)
    uid = _uid(user) if user else None
    runtime_gone = uid is not None and not Path(f"/run/user/{uid}").is_dir()
    on_greeter = greeter_screen_active()
    if runtime_gone or on_greeter:
        session.update(_greeter_session_display())
    child.update(session)
    child["URBLOCK_USER"] = user
    child["PAM_USER"] = user
    if runtime_gone or on_greeter:
        child["URBLOCK_GREETER"] = "1"
        child["URBLOCK_OVERLAY_MODE"] = "notify"
    return child


def overlay_run_user(username: str | None = None) -> str | None:
    user = (
        username
        or os.environ.get("PAM_USER")
        or os.environ.get("URBLOCK_USER")
        or ""
    )
    if os.environ.get("URBLOCK_GREETER") == "1":
        greeter = greeter_session_user()
        if greeter:
            return greeter
        for name in ("gdm-greeter", "gdm", "Debian-gdm", "sddm", "lightdm"):
            if _uid(name) is not None:
                return name
    if user and user not in ("gdm", "sddm", "lightdm") and _uid(user) is not None:
        uid = _uid(user)
        if uid is not None and Path(f"/run/user/{uid}").is_dir():
            return user
    for name in ("gdm", "Debian-gdm", "sddm", "lightdm"):
        if _uid(name) is not None:
            return name
    return None


def prefer_notify_overlay(env: dict[str, str], *, has_tkinter: bool) -> bool:
    """False = графическое окно (tkinter) на экране GDM/блокировки; True = только уведомления."""
    mode = os.environ.get("URBLOCK_OVERLAY_MODE", "tk").lower()
    if mode == "notify":
        return True
    if mode == "tk":
        return False
    if not has_tkinter:
        return True
    # Ubuntu GNOME (Wayland): XWayland даёт DISPLAY + XAUTHORITY — показываем окно, не терминал.
    if env.get("DISPLAY") and env.get("XAUTHORITY"):
        return False
    if env.get("WAYLAND_DISPLAY") and not env.get("DISPLAY"):
        return True
    disp = env.get("DISPLAY", "")
    if disp and not _x11_socket_exists(disp) and not env.get("WAYLAND_DISPLAY"):
        return True
    return False

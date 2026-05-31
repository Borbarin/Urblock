"""Общий запуск фонового verify --daemon (lock-agent, greeter-agent, PAM)."""

from __future__ import annotations

import fcntl
import os
import subprocess
import sys
from pathlib import Path

_LOG = Path("/var/log/urblock-verify.log")


def _log(msg: str) -> None:
    from datetime import datetime

    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}\n"
    try:
        with _LOG.open("a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


def verify_script() -> Path:
    return Path(__file__).resolve().parent / "verify.py"


def python_bin() -> str:
    if os.environ.get("URBLOCK_PYTHON"):
        return os.environ["URBLOCK_PYTHON"]
    conf = Path("/etc/urblock/install.conf")
    if conf.is_file():
        for line in conf.read_text(encoding="utf-8").splitlines():
            if line.startswith("URBLOCK_PYTHON="):
                val = line.split("=", 1)[1].strip().strip('"')
                if val:
                    return val
    return sys.executable


def load_install_env() -> dict[str, str]:
    out: dict[str, str] = {}
    conf = Path("/etc/urblock/install.conf")
    if not conf.is_file():
        return out
    for line in conf.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.startswith("#"):
            continue
        key, _, val = line.partition("=")
        out[key.strip()] = val.strip().strip('"')
    return out


def autonomous_enabled() -> bool:
    try:
        po = Path(__file__).resolve().parents[1]
        gui = po.parent / "urblock_gui"
        if str(gui) not in sys.path:
            sys.path.insert(0, str(gui))
        from storage import load_settings

        return bool(load_settings().get("auto_detect_enabled", False))
    except Exception:
        return False


def verify_lock_held() -> bool:
    lock = Path("/var/run/urblock-verify.lock")
    if not lock.is_file():
        return False
    try:
        fd = os.open(lock, os.O_RDWR)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except OSError:
        return True
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def daemon_running(user: str) -> bool:
    try:
        login_dir = Path(__file__).resolve().parent
        if str(login_dir) not in sys.path:
            sys.path.insert(0, str(login_dir))
        from verify_session import VerifySession

        sess = VerifySession.load(user)
        if sess and sess.is_process_alive():
            return True
        if sess and sess.result == "ok":
            return True
        return verify_lock_held()
    except Exception:
        return verify_lock_held()


def start_daemon(user: str, *, log_prefix: str = "agent", greeter: bool = False) -> None:
    if not autonomous_enabled():
        _log(f"{log_prefix}: auto_detect disabled — skip daemon for {user}")
        return
    if daemon_running(user):
        return
    if verify_lock_held():
        _log(f"{log_prefix}: verify busy — skip daemon for {user}")
        return

    login_dir = Path(__file__).resolve().parent
    if str(login_dir) not in sys.path:
        sys.path.insert(0, str(login_dir))
    from display_env import daemon_child_env

    if greeter:
        try:
            from overlay import greeter_push_notify

            greeter_push_notify(user, "scanning")
        except Exception as exc:
            _log(f"{log_prefix}: greeter notify: {exc!r}")

    env = daemon_child_env(user, load_install_env())
    if greeter:
        env["URBLOCK_GREETER"] = "1"
        env["URBLOCK_OVERLAY_MODE"] = "notify"
    script = verify_script()
    try:
        proc = subprocess.Popen(
            [python_bin(), str(script), "--daemon", "--user", user],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        from verify_session import VerifySession

        VerifySession(user=user, pid=proc.pid).save()
        _log(f"{log_prefix}: started daemon pid={proc.pid} user={user}")
    except OSError as exc:
        _log(f"{log_prefix}: cannot start daemon for {user}: {exc!r}")


def stop_daemon(user: str, *, log_prefix: str = "agent") -> None:
    try:
        login_dir = Path(__file__).resolve().parent
        if str(login_dir) not in sys.path:
            sys.path.insert(0, str(login_dir))
        from verify_session import VerifySession

        sess = VerifySession.load(user)
        if sess and sess.is_process_alive():
            sess.terminate()
            _log(f"{log_prefix}: stopped daemon pid={sess.pid} user={user}")
        VerifySession.clear(user)
    except Exception as exc:
        _log(f"{log_prefix}: stop daemon {user}: {exc!r}")

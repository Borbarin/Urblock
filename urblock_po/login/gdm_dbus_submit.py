#!/usr/bin/env python3
"""Запуск PAM-входа GDM через org.gnome.DisplayManager (без синтетического Enter)."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

_LOG = Path("/var/log/urblock-verify.log")
_TIMEOUT_SEC = 28


def _log(msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    try:
        with _LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print("usage: gdm_dbus_submit.py USERNAME", file=sys.stderr)
        return 2
    username = args[0]

    try:
        import gi

        gi.require_version("Gdm", "1.0")
        from gi.repository import Gdm, GLib
    except (ImportError, ValueError) as exc:
        _log(f"gdm-dbus: Gdm bindings unavailable ({exc!r})")
        return 1

    client = Gdm.Client()
    try:
        verifier = client.open_reauthentication_channel_sync(username)
    except Exception as exc:
        _log(f"gdm-dbus: open_reauthentication_channel failed: {exc!r}")
        return 1

    state = {"done": False, "failed": False}

    def on_secret(verifier, service: str, query: str) -> None:
        _log(f"gdm-dbus: secret query service={service!r} q={query!r}")
        verifier.call_answer_query_sync(service, "")

    def on_info(verifier, service: str, query: str) -> None:
        _log(f"gdm-dbus: info query service={service!r} q={query!r}")
        verifier.call_answer_query_sync(service, "")

    def on_complete(verifier, *_) -> None:
        _log("gdm-dbus: verification_complete")
        state["done"] = True
        loop.quit()

    def on_failed(verifier, msg: str) -> None:
        _log(f"gdm-dbus: verification_failed: {msg}")
        state["failed"] = True
        loop.quit()

    def on_problem(verifier, msg: str) -> None:
        _log(f"gdm-dbus: problem: {msg}")

    verifier.connect("secret-info-query", on_secret)
    verifier.connect("info-query", on_info)
    verifier.connect("verification-complete", on_complete)
    verifier.connect("verification-failed", on_failed)
    verifier.connect("problem", on_problem)

    try:
        verifier.call_begin_verification_for_user_sync("gdm-password", username)
    except Exception as exc:
        _log(f"gdm-dbus: begin_verification_for_user failed: {exc!r}")
        return 1

    loop = GLib.MainLoop()

    def on_timeout() -> bool:
        if not state["done"] and not state["failed"]:
            _log("gdm-dbus: timeout waiting for verification")
        loop.quit()
        return False

    GLib.timeout_add_seconds(_TIMEOUT_SEC, on_timeout)
    loop.run()
    return 0 if state["done"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

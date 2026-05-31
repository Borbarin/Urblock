from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable

import dearpygui.dearpygui as dpg

import i18n.ru as ru
from legal import load_user_agreement
from storage import patch_settings

if TYPE_CHECKING:
    from main import UrblockApp

EULA_WINDOW = "eula_window"
EULA_SCROLL = "eula_scroll"
EULA_TEXT_WRAP = 660
EULA_WARN = "eula_warn_window"
EULA_STATUS = "eula_status"
EULA_BTN_AGREE = "eula_btn_agree"
EULA_BTN_DISAGREE = "eula_btn_disagree"
EULA_BTN_CLOSE = "eula_btn_close"


class EulaGate:
    def __init__(self, app: UrblockApp, main_window_tag: str) -> None:
        self.app = app
        self._main_window = main_window_tag
        self._agreement = load_user_agreement()
        self._on_accepted: Callable[[], None] | None = None

    @property
    def is_accepted(self) -> bool:
        return bool(self.app.settings.get("eula_accepted"))

    def build(self) -> None:
        with dpg.window(
            tag=EULA_WINDOW,
            label=ru.EULA_WINDOW_TITLE,
            modal=True,
            no_close=True,
            no_collapse=True,
            width=720,
            height=560,
            show=False,
        ):
            dpg.add_text(ru.EULA_INTRO, wrap=EULA_TEXT_WRAP)
            dpg.add_spacer(height=6)
            with dpg.child_window(
                tag=EULA_SCROLL,
                width=-1,
                height=360,
                border=True,
                horizontal_scrollbar=False,
            ):
                for line in self._agreement.splitlines():
                    if line.strip():
                        dpg.add_text(line, wrap=EULA_TEXT_WRAP)
                    else:
                        dpg.add_spacer(height=6)
            dpg.add_spacer(height=8)
            dpg.add_text("", tag=EULA_STATUS, color=(255, 180, 100), wrap=EULA_TEXT_WRAP)
            with dpg.group(horizontal=True):
                dpg.add_button(
                    tag=EULA_BTN_AGREE,
                    label=ru.EULA_BTN_AGREE,
                    width=160,
                    callback=self._on_agree,
                )
                dpg.add_button(
                    tag=EULA_BTN_DISAGREE,
                    label=ru.EULA_BTN_DISAGREE,
                    width=160,
                    callback=self._on_disagree,
                )
                dpg.add_button(
                    tag=EULA_BTN_CLOSE,
                    label=ru.EULA_BTN_CLOSE,
                    width=160,
                    show=False,
                    callback=self.hide,
                )

        with dpg.window(
            tag=EULA_WARN,
            label=ru.EULA_WARN_TITLE,
            modal=True,
            no_close=True,
            width=480,
            height=180,
            show=False,
        ):
            dpg.add_text(ru.EULA_DISAGREE_WARNING, wrap=440)
            dpg.add_spacer(height=12)
            dpg.add_button(
                label=ru.EULA_WARN_OK,
                width=120,
                callback=lambda: dpg.configure_item(EULA_WARN, show=False),
            )

    def set_on_accepted(self, callback: Callable[[], None]) -> None:
        self._on_accepted = callback

    def show_blocking(self) -> None:
        dpg.configure_item(EULA_WINDOW, label=ru.EULA_WINDOW_TITLE, show=True)
        dpg.configure_item(EULA_STATUS, default_value="")
        dpg.configure_item(EULA_BTN_AGREE, show=True)
        dpg.configure_item(EULA_BTN_DISAGREE, show=True)
        dpg.configure_item(EULA_BTN_CLOSE, show=False)
        self._apply_main_access(False)

    def show_readonly(self) -> None:
        dpg.configure_item(EULA_WINDOW, label=ru.EULA_VIEW_TITLE, show=True)
        dpg.configure_item(EULA_STATUS, default_value="")
        dpg.configure_item(EULA_BTN_AGREE, show=False)
        dpg.configure_item(EULA_BTN_DISAGREE, show=False)
        dpg.configure_item(EULA_BTN_CLOSE, show=True)

    def hide(self) -> None:
        dpg.configure_item(EULA_WINDOW, show=False)
        if self.is_accepted:
            self._apply_main_access(True)

    def apply_startup_state(self) -> None:
        if self.is_accepted:
            self.hide()
            self._apply_main_access(True)
        else:
            self.show_blocking()

    def _apply_main_access(self, enabled: bool) -> None:
        if dpg.does_item_exist(self._main_window):
            dpg.configure_item(self._main_window, show=enabled)

    def _on_agree(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.app.settings = patch_settings(
            {"eula_accepted": True, "eula_accepted_at": now},
        )
        dpg.configure_item(EULA_STATUS, default_value=ru.EULA_ACCEPTED)
        self.hide()
        if self._on_accepted:
            self._on_accepted()

    def _on_disagree(self) -> None:
        dpg.configure_item(EULA_STATUS, default_value=ru.EULA_MUST_ACCEPT)
        dpg.configure_item(EULA_WARN, show=True)

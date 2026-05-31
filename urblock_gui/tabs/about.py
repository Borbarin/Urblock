from __future__ import annotations

from typing import TYPE_CHECKING

import dearpygui.dearpygui as dpg

import i18n.ru as ru
from config import APP_VERSION
from ui.fonts import TITLE_FONT_TAG

if TYPE_CHECKING:
    from main import UrblockApp

ABOUT_SCROLL = "about_scroll"


class AboutController:
    def __init__(self, app: UrblockApp) -> None:
        self.app = app

    def open_agreement(self) -> None:
        self.app.eula.show_readonly()

    def revoke_and_exit(self) -> None:
        from storage import patch_settings

        self.app.settings = patch_settings(
            {"eula_accepted": False, "eula_accepted_at": None},
        )
        self.app.camera.release()
        self.app.autonomous.release()
        self.app.eula.show_blocking()


def build_about_tab(ctrl: AboutController) -> None:
    with dpg.child_window(tag=ABOUT_SCROLL, border=False, height=-1):
        with dpg.group():
            dpg.bind_item_font(dpg.add_text("URBLOCK", color=(120, 180, 255)), TITLE_FONT_TAG)
            dpg.add_text(
                ru.ABOUT_VERSION.format(version=APP_VERSION),
                color=(200, 200, 200),
            )
            dpg.add_spacer(height=16)
            dpg.add_text(ru.ABOUT_PURPOSE_TITLE, color=(180, 200, 255))
            dpg.add_text(ru.ABOUT_PURPOSE, wrap=700)
            dpg.add_spacer(height=12)
            dpg.add_text(ru.ABOUT_TRUST_TITLE, color=(180, 200, 255))
            for line in ru.ABOUT_TRUST_LINES:
                dpg.add_text(f"• {line}", wrap=700)
            dpg.add_spacer(height=12)
            dpg.add_text(ru.ABOUT_WARNING, color=(255, 200, 120), wrap=700)
            dpg.add_spacer(height=16)
            dpg.add_text(ru.ABOUT_EULA_HINT, color=(200, 200, 200), wrap=700)
            dpg.add_spacer(height=8)
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label=ru.ABOUT_BTN_OPEN_EULA,
                    callback=lambda: ctrl.open_agreement(),
                )
                dpg.add_button(
                    label=ru.ABOUT_BTN_REVOKE,
                    callback=lambda: ctrl.revoke_and_exit(),
                )

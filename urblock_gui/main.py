"""Оконное приложение Urblock на Dear PyGui."""

from __future__ import annotations

import dearpygui.dearpygui as dpg

import i18n.ru as ru
from app_profile import ProfileSession
from autonomous import AutonomousUnlockService
from storage import (
    load_settings,
    migrate_legacy_biometrics,
    migrate_plaintext_biometrics,
    repair_biometric_storage,
)
from tabs.about import AboutController, build_about_tab
from tabs.camera import TAB_CAMERA_TAG, CameraController, build_camera_tab
from tabs.faces import FacesController, build_faces_tab
from tabs.settings import SettingsController, build_settings_tab
from ui.eula_gate import EulaGate
from ui.fonts import setup_cyrillic_font

WINDOW_TAG = "main_window"
TAB_SETTINGS = "tab_settings"
TAB_FACES = "tab_faces"
TAB_ABOUT = "tab_about"


class UrblockApp:
    def __init__(self) -> None:
        migrate_legacy_biometrics()
        migrate_plaintext_biometrics()
        repair_biometric_storage()
        self.settings = load_settings()
        self.profile = ProfileSession()
        self.active_tab = TAB_CAMERA_TAG
        self.camera = CameraController(self)
        self.settings_ctrl = SettingsController(self)
        self.faces = FacesController(self)
        self.about = AboutController(self)
        self.autonomous = AutonomousUnlockService(self)
        self.eula: EulaGate | None = None

    def run(self) -> None:
        dpg.create_context()
        setup_cyrillic_font()
        dpg.create_viewport(
            title=ru.WINDOW_TITLE,
            width=960,
            height=720,
            min_width=640,
            min_height=480,
        )
        self.camera.init_texture()

        with dpg.window(tag=WINDOW_TAG, label=ru.APP_TITLE, no_close=True):
            with dpg.tab_bar(callback=self._on_tab_changed):
                with dpg.tab(label=ru.TAB_CAMERA, tag=TAB_CAMERA_TAG):
                    build_camera_tab(self.camera)
                with dpg.tab(label=ru.TAB_SETTINGS, tag=TAB_SETTINGS):
                    build_settings_tab(self.settings_ctrl)
                with dpg.tab(label=ru.TAB_FACES, tag=TAB_FACES):
                    build_faces_tab(self.faces)
                with dpg.tab(label=ru.TAB_ABOUT, tag=TAB_ABOUT):
                    build_about_tab(self.about)

        self.eula = EulaGate(self, WINDOW_TAG)
        self.eula.build()
        self.eula.set_on_accepted(self._on_eula_accepted)

        dpg.set_primary_window(WINDOW_TAG, True)
        dpg.setup_dearpygui()
        dpg.show_viewport()

        self.eula.apply_startup_state()
        if self.eula.is_accepted:
            self._on_eula_accepted()

        while dpg.is_dearpygui_running():
            if self.eula.is_accepted:
                if self.camera.is_running:
                    self.camera.tick(update_preview=self.active_tab == TAB_CAMERA_TAG)
                self.autonomous.tick()
                self.camera.refresh_profile_label()
            dpg.render_dearpygui_frame()

        self.autonomous.release()
        self.camera.release()
        dpg.destroy_context()

    def _on_eula_accepted(self) -> None:
        self.settings_ctrl.apply_to_ui()
        self.faces.refresh_table()

    def _on_tab_changed(self, sender, app_data) -> None:
        if not self.eula or not self.eula.is_accepted:
            return
        self.active_tab = app_data
        if app_data == TAB_CAMERA_TAG:
            self.camera.restart_preview()
        elif app_data == TAB_SETTINGS:
            self.settings_ctrl.refresh_camera_list(status_message="")


def main() -> None:
    UrblockApp().run()


if __name__ == "__main__":
    main()

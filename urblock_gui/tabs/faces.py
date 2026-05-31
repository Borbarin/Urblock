from __future__ import annotations

from typing import TYPE_CHECKING

import dearpygui.dearpygui as dpg

import i18n.ru as ru
from storage import add_face, delete_face_entry, load_faces
if TYPE_CHECKING:
    from main import UrblockApp

TABLE_TAG = "faces_table"
STATUS_TAG = "faces_status"
INPUT_NAME = "faces_new_name"


class FacesController:
    def __init__(self, app: UrblockApp) -> None:
        self.app = app

    def refresh_table(self) -> None:
        if not dpg.does_item_exist(TABLE_TAG):
            return
        rows = dpg.get_item_children(TABLE_TAG, slot=1)
        if rows:
            for row in rows:
                dpg.delete_item(row)
        for face in load_faces():
            with dpg.table_row(parent=TABLE_TAG):
                dpg.add_text(face.get("name", ru.FACES_EMPTY))
                dpg.add_text(face.get("created_at", ru.FACES_EMPTY))
                dpg.add_button(
                    label=ru.FACES_BTN_DELETE,
                    callback=self._make_delete_callback(face["id"]),
                )

    def _make_delete_callback(self, face_id: str):
        def _delete() -> None:
            delete_face_entry(face_id)
            self.app.camera._faces.reload_gallery()
            self.refresh_table()
            dpg.set_value(STATUS_TAG, ru.FACES_DELETED)

        return _delete

    def add_manual(self) -> None:
        name = dpg.get_value(INPUT_NAME).strip()
        if not name:
            dpg.set_value(STATUS_TAG, ru.FACES_NAME_REQUIRED)
            return
        add_face(name)
        dpg.set_value(INPUT_NAME, "")
        dpg.set_value(STATUS_TAG, ru.FACES_ADDED.format(name=name))
        self.refresh_table()


def build_faces_tab(ctrl: FacesController) -> None:
    dpg.add_text(ru.FACES_TITLE, color=(180, 200, 255))
    dpg.add_spacer(height=8)
    with dpg.table(
        tag=TABLE_TAG,
        header_row=True,
        borders_innerH=True,
        borders_outerH=True,
        borders_innerV=True,
        borders_outerV=True,
        resizable=True,
        policy=dpg.mvTable_SizingStretchProp,
    ):
        dpg.add_table_column(label=ru.FACES_COL_NAME)
        dpg.add_table_column(label=ru.FACES_COL_ADDED)
        dpg.add_table_column(label=ru.FACES_COL_ACTIONS, width_fixed=True, init_width_or_weight=100)
    dpg.add_spacer(height=12)
    dpg.add_text(ru.FACES_ADD_MANUAL, color=(200, 200, 200))
    with dpg.group(horizontal=True):
        dpg.add_input_text(hint=ru.FACES_NAME_HINT, tag=INPUT_NAME, width=200)
        dpg.add_button(label=ru.FACES_BTN_ADD, callback=lambda: ctrl.add_manual())
    dpg.add_spacer(height=8)
    dpg.add_text("", tag=STATUS_TAG, color=(160, 160, 160))

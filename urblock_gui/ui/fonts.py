from __future__ import annotations

from pathlib import Path

import dearpygui.dearpygui as dpg

FONT_TAG = "app_font_ru"
TITLE_FONT_TAG = "app_font_title"
FONT_SIZE = 18
TITLE_FONT_SIZE = 42

FONT_CANDIDATES: tuple[Path, ...] = (
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
    Path("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"),
    Path("/usr/share/fonts/truetype/ubuntu/Ubuntu-R.ttf"),
    Path("/usr/share/fonts/TTF/DejaVuSans.ttf"),
    Path("C:/Windows/Fonts/segoeui.ttf"),
    Path("C:/Windows/Fonts/arial.ttf"),
)


def _resolve_font_path() -> str | None:
    for path in FONT_CANDIDATES:
        if path.is_file():
            return str(path)
    return None


def setup_cyrillic_font() -> None:
    """Подключает TTF с кириллицей — стандартный шрифт Dear PyGui её не показывает."""
    font_path = _resolve_font_path()
    if font_path is None:
        return

    with dpg.font_registry():
        default_font = dpg.add_font(font_path, FONT_SIZE, tag=FONT_TAG)
        dpg.add_font(font_path, TITLE_FONT_SIZE, tag=TITLE_FONT_TAG)
        dpg.bind_font(default_font)

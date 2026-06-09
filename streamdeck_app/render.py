from __future__ import annotations

from io import BytesIO
from pathlib import Path

import cairosvg
from PIL import Image, ImageDraw, ImageFont

from .client import Position
from .layout import KeyAction, VAC_OFF_COLOR, VAC_ON_COLOR

_ICON_DIR = Path(__file__).resolve().parent / "assets" / "icons"
_ICON_CACHE: dict[tuple[str, int, str], Image.Image] = {}

# OpenPnP FlatDarkLaf toolbar/icon colors
_OPENPNP_ICON_COLOR = "#adadad"


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _load_svg_icon(name: str, size: int, fill_override: str | None = None) -> Image.Image:
    cache_key = (name, size, fill_override or "")
    cached = _ICON_CACHE.get(cache_key)
    if cached is not None:
        return cached

    svg_path = _ICON_DIR / f"{name}.svg"
    svg_data = svg_path.read_text(encoding="utf-8")
    if fill_override:
        svg_data = svg_data.replace('fill="#000000"', f'fill="{fill_override}"')

    png_data = cairosvg.svg2png(bytestring=svg_data.encode("utf-8"), output_width=size, output_height=size)
    icon = Image.open(BytesIO(png_data)).convert("RGBA")
    _ICON_CACHE[cache_key] = icon
    return icon


def render_icon_button(
    width: int,
    height: int,
    action: KeyAction,
    pressed: bool = False,
    disabled: bool = False,
    machine_enabled: bool | None = None,
    machine_homed: bool | None = None,
    job_state: str | None = None,
) -> Image.Image:
    base = _hex_to_rgb(action.accent)
    if disabled:
        base = tuple(int(c * 0.35) for c in base)
    if pressed:
        image = Image.new("RGB", (width, height), tuple(max(0, c - 40) for c in base))
    else:
        image = Image.new("RGB", (width, height), base)

    icon_size = int(min(width, height) * 0.62)
    if action.icon == "home":
        if machine_homed is False:
            icon = _load_svg_icon("home_warning", icon_size)
        else:
            icon = _load_svg_icon("home", icon_size, fill_override=_OPENPNP_ICON_COLOR)
    elif action.icon == "power":
        icon_name = "power_button_off" if machine_enabled else "power_button_on"
        icon = _load_svg_icon(icon_name, icon_size)
    elif action.icon == "job":
        if job_state in ("Running", "Pausing"):
            icon_name = "job_pause"
        else:
            icon_name = "job_start"
        icon = _load_svg_icon(icon_name, icon_size)
    elif action.icon == "job_step":
        icon = _load_svg_icon("job_step", icon_size)
    elif action.icon == "center_camera":
        icon = _load_svg_icon("center_camera", icon_size)
    elif action.icon == "center_tool":
        icon = _load_svg_icon("center_tool", icon_size)
    else:
        return image

    if disabled:
        icon = icon.copy()
        alpha = icon.getchannel("A")
        alpha = alpha.point(lambda value: int(value * 0.4))
        icon.putalpha(alpha)

    paste_x = (width - icon.width) // 2
    paste_y = (height - icon.height) // 2
    image.paste(icon, (paste_x, paste_y), icon)
    return image


def render_tool_button(
    width: int,
    height: int,
    action: KeyAction,
    pressed: bool = False,
    disabled: bool = False,
    selected_tool: str | None = None,
    nozzle_tip: str | None = None,
) -> Image.Image:
    base = _hex_to_rgb(action.accent)
    if disabled:
        base = tuple(int(c * 0.35) for c in base)
    if pressed:
        image = Image.new("RGB", (width, height), tuple(max(0, c - 40) for c in base))
    else:
        image = Image.new("RGB", (width, height), base)

    draw = ImageDraw.Draw(image)
    text_color = "#6b7280" if disabled else "white"
    title_font = _load_font(20)
    sub_font = _load_font(28)
    status = selected_tool or "---"

    title_bbox = draw.textbbox((0, 0), action.label, font=title_font)
    sub_bbox = draw.textbbox((0, 0), status, font=sub_font)
    title_w = title_bbox[2] - title_bbox[0]
    sub_w = sub_bbox[2] - sub_bbox[0]
    draw.text(
        ((width - title_w) / 2, height * 0.18),
        action.label,
        fill=text_color,
        font=title_font,
    )
    sub_y = height * 0.42 if nozzle_tip else height * 0.48
    draw.text(
        ((width - sub_w) / 2, sub_y),
        status,
        fill=text_color,
        font=sub_font,
    )
    if nozzle_tip:
        hint_font = _load_font(18)
        hint_bbox = draw.textbbox((0, 0), nozzle_tip, font=hint_font)
        hint_w = hint_bbox[2] - hint_bbox[0]
        draw.text(
            ((width - hint_w) / 2, height * 0.72),
            nozzle_tip,
            fill="#bfdbfe" if not disabled else "#6b7280",
            font=hint_font,
        )
    return image


def render_toggle_button(
    width: int,
    height: int,
    action: KeyAction,
    pressed: bool = False,
    disabled: bool = False,
    actuator_on: bool | None = None,
    vacuum_value: str | None = None,
) -> Image.Image:
    if actuator_on is True:
        accent = VAC_ON_COLOR
        if vacuum_value:
            status = vacuum_value
            sub_font = _load_font(28)
        else:
            status = "ON"
            sub_font = _load_font(24)
    elif actuator_on is False:
        accent = VAC_OFF_COLOR
        status = "OFF"
        sub_font = _load_font(24)
    else:
        accent = action.accent
        status = "---"
        sub_font = _load_font(24)

    base = _hex_to_rgb(accent)
    if disabled:
        base = tuple(int(c * 0.35) for c in base)
    if pressed:
        image = Image.new("RGB", (width, height), tuple(max(0, c - 40) for c in base))
    else:
        image = Image.new("RGB", (width, height), base)

    draw = ImageDraw.Draw(image)
    text_color = "#6b7280" if disabled else "white"
    title_font = _load_font(22)

    title_bbox = draw.textbbox((0, 0), action.label, font=title_font)
    sub_bbox = draw.textbbox((0, 0), status, font=sub_font)
    title_w = title_bbox[2] - title_bbox[0]
    sub_w = sub_bbox[2] - sub_bbox[0]
    draw.text(
        ((width - title_w) / 2, height * 0.18),
        action.label,
        fill=text_color,
        font=title_font,
    )
    draw.text(
        ((width - sub_w) / 2, height * 0.48),
        status,
        fill=text_color,
        font=sub_font,
    )
    if actuator_on is True and vacuum_value:
        hint = "vac"
        hint_font = _load_font(14)
        hint_bbox = draw.textbbox((0, 0), hint, font=hint_font)
        hint_w = hint_bbox[2] - hint_bbox[0]
        draw.text(
            ((width - hint_w) / 2, height * 0.78),
            hint,
            fill="#bfdbfe",
            font=hint_font,
        )
    return image


def render_button_image(
    width: int,
    height: int,
    action: KeyAction,
    pressed: bool = False,
    disabled: bool = False,
) -> Image.Image:
    base = _hex_to_rgb(action.accent)
    if disabled:
        base = tuple(int(c * 0.35) for c in base)
    if pressed:
        image = Image.new("RGB", (width, height), tuple(max(0, c - 40) for c in base))
    else:
        image = Image.new("RGB", (width, height), base)

    draw = ImageDraw.Draw(image)
    text_color = "#6b7280" if disabled else "white"

    if action.sublabel:
        title_font = _load_font(20)
        sub_font = _load_font(26)
        title_bbox = draw.textbbox((0, 0), action.label, font=title_font)
        sub_bbox = draw.textbbox((0, 0), action.sublabel, font=sub_font)
        title_w = title_bbox[2] - title_bbox[0]
        sub_w = sub_bbox[2] - sub_bbox[0]
        draw.text(
            ((width - title_w) / 2, height * 0.18),
            action.label,
            fill=text_color,
            font=title_font,
        )
        draw.text(
            ((width - sub_w) / 2, height * 0.48),
            action.sublabel,
            fill=text_color,
            font=sub_font,
        )
    else:
        font = _load_font(28 if action.label in ("Y+", "Y-", "X+", "X-", "Z+", "Z-") else 22)
        bbox = draw.textbbox((0, 0), action.label, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        draw.text(
            ((width - text_w) / 2, (height - text_h) / 2 - 4),
            action.label,
            fill=text_color,
            font=font,
        )

    return image


def render_lock_button(
    width: int,
    height: int,
    locked: bool,
    pressed: bool = False,
    warning_pulse: bool = False,
    disabled: bool = False,
) -> Image.Image:
    if locked:
        accent = "#8b1e1e"
        label = "UNLOCK"
        sublabel = "bridge offline" if disabled else "tap here"
    else:
        accent = "#2dba6a" if warning_pulse else "#1f8f4e"
        label = "LOCK"
        sublabel = "tap here"

    base = _hex_to_rgb(accent)
    if disabled:
        base = tuple(int(c * 0.35) for c in base)
    if pressed:
        image = Image.new("RGB", (width, height), tuple(max(0, c - 40) for c in base))
    else:
        image = Image.new("RGB", (width, height), base)

    draw = ImageDraw.Draw(image)
    title_font = _load_font(24)
    sub_font = _load_font(14)

    bbox = draw.textbbox((0, 0), label, font=title_font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    text_color = "#6b7280" if disabled else "white"
    sub_color = "#6b7280" if disabled else "#e8edf7"
    draw.text(
        ((width - text_w) / 2, (height - text_h) / 2 - 10),
        label,
        fill=text_color,
        font=title_font,
    )

    sub_bbox = draw.textbbox((0, 0), sublabel, font=sub_font)
    sub_w = sub_bbox[2] - sub_bbox[0]
    draw.text(
        ((width - sub_w) / 2, (height + text_h) / 2 - 2),
        sublabel,
        fill=sub_color,
        font=sub_font,
    )

    return image


# Touch-strip layout for Stream Deck + XL (1200×100 logical).
_AXIS_CENTERS = (70, 180, 290, 400)
_AXIS_LABEL_Y = 30
_AXIS_VALUE_Y = 50
_AXIS_STEP_Y = 72
_INFO_X = 504
_INFO_COLUMN_PAD = 10
_PROGRESS_Y = 52
_PROGRESS_H = 14
_PARTS_Y = 78


def _truncate_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_width: int,
) -> str:
    if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
        return text
    trimmed = text
    while trimmed and draw.textbbox((0, 0), trimmed + "…", font=font)[2] > max_width:
        trimmed = trimmed[:-1]
    return (trimmed + "…") if trimmed else "…"


def _draw_header(
    draw: ImageDraw.ImageDraw,
    title_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    meta_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    jog_step_mm: float,
    locked: bool,
    status_note: str | None = None,
    speed_pct: float | None = None,
    jog_dial_locked: bool = False,
    speed_dial_locked: bool = False,
) -> None:
    draw.text((24, 8), "LumenPNP", fill="#8fa2c5", font=title_font)
    title_bbox = draw.textbbox((24, 8), "LumenPNP", font=title_font)
    status_x = title_bbox[2] + 16

    if status_note is not None:
        draw.text((status_x, 8), status_note, fill="#ff6b6b", font=meta_font)
        return

    ready_color = "#ff8a80" if locked else "#7dcea0"
    prefix = "LOCKED  |  " if locked else "READY   |  "
    jog_text = f"Jog {jog_step_mm:g} mm"
    jog_color = "#ff8a80" if jog_dial_locked else ready_color

    x = status_x
    draw.text((x, 8), prefix, fill=ready_color, font=meta_font)
    x += draw.textbbox((0, 0), prefix, font=meta_font)[2]
    draw.text((x, 8), jog_text, fill=jog_color, font=meta_font)
    x += draw.textbbox((0, 0), jog_text, font=meta_font)[2]

    if speed_pct is not None:
        speed_color = "#ff8a80" if speed_dial_locked else ready_color
        speed_text = f"  |  Speed {speed_pct:.0f}%"
        max_w = _INFO_X - _INFO_COLUMN_PAD - x
        if max_w > 0:
            speed_text = _truncate_text(draw, speed_text, meta_font, max_w)
            if speed_text:
                draw.text((x, 8), speed_text, fill=speed_color, font=meta_font)


def _job_state_color(job_state: str | None) -> str:
    if job_state in ("Running", "Pausing"):
        return "#7dcea0"
    if job_state == "Paused":
        return "#f6c453"
    if job_state in ("Stopping",):
        return "#ff8a80"
    return "#8fa2c5"


def _draw_progress_bar(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    width: int,
    height: int,
    completed: int,
    total: int,
) -> None:
    draw.rectangle((x, y, x + width, y + height), fill="#1a2030", outline="#3a4a66")
    if total > 0:
        fill_w = int(round(width * (float(completed) / float(total))))
        if fill_w > 0:
            draw.rectangle((x + 1, y + 1, x + fill_w - 1, y + height - 1), fill="#2563eb")


def _draw_job_panel(
    draw: ImageDraw.ImageDraw,
    position: Position,
    meta_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    small_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    width: int,
) -> None:
    status = position.machine_status or "Idle"
    status = _truncate_text(draw, status, meta_font, width - _INFO_X - 16)
    draw.text((_INFO_X, 8), status, fill="#e8edf7", font=meta_font)

    job_state = position.job_state or "Stopped"
    job_label = f"Job: {job_state}"
    draw.text((_INFO_X, 28), job_label, fill=_job_state_color(job_state), font=small_font)

    total = position.placements_total or 0
    remaining = position.placements_remaining or 0
    completed = position.placements_completed or 0
    if total > 0:
        counts = f"{remaining} / {total} remaining"
    else:
        counts = "No placements"
    counts_x = _INFO_X + draw.textbbox((0, 0), job_label, font=small_font)[2] + 20
    draw.text((counts_x, 28), counts, fill="#8fa2c5", font=small_font)

    bar_width = width - _INFO_X - 16
    _draw_progress_bar(draw, _INFO_X, _PROGRESS_Y, bar_width, _PROGRESS_H, completed, total)

    parts = position.nozzle_parts or {}
    if not parts:
        draw.text((_INFO_X, _PARTS_Y), "No nozzles", fill="#bfdbfe", font=small_font)
        return

    half_width = max(bar_width // 2 - 8, 80)
    mid_x = _INFO_X + bar_width // 2

    for name, x in (("N1", _INFO_X), ("N2", mid_x)):
        if name not in parts:
            continue
        part_id = parts.get(name)
        label = f"{name}: {part_id if part_id else '---'}"
        label = _truncate_text(draw, label, small_font, half_width)
        draw.text((x, _PARTS_Y), label, fill="#bfdbfe", font=small_font)


def _draw_axis_column(
    draw: ImageDraw.ImageDraw,
    center_x: int,
    axis: str,
    value: float | None,
    meta_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    value_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    step_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    dial_step_mm: float | None = None,
) -> None:
    label_bbox = draw.textbbox((0, 0), axis, font=meta_font)
    label_w = label_bbox[2] - label_bbox[0]
    draw.text((center_x - label_w / 2, _AXIS_LABEL_Y), axis, fill="#8fa2c5", font=meta_font)

    text = "---" if value is None else f"{value:.2f}"
    value_bbox = draw.textbbox((0, 0), text, font=value_font)
    value_w = value_bbox[2] - value_bbox[0]
    draw.text((center_x - value_w / 2, _AXIS_VALUE_Y), text, fill="#f4f7ff", font=value_font)

    if dial_step_mm is not None:
        step_text = f"{dial_step_mm:g} mm"
        step_bbox = draw.textbbox((0, 0), step_text, font=step_font)
        step_w = step_bbox[2] - step_bbox[0]
        draw.text(
            (center_x - step_w / 2, _AXIS_STEP_Y),
            step_text,
            fill="#7dcea0",
            font=step_font,
        )


def render_touchscreen_status(
    width: int,
    height: int,
    position: Position,
    connected: bool,
    jog_step_mm: float,
    locked: bool = True,
    dial_steps_mm: dict[str, float] | None = None,
    jog_dial_locked: bool = False,
    speed_dial_locked: bool = False,
    display_speed_pct: float | None = None,
) -> Image.Image:
    image = Image.new("RGB", (width, height), "#10131a")
    draw = ImageDraw.Draw(image)

    title_font = _load_font(18)
    value_font = _load_font(22)
    meta_font = _load_font(16)

    if not connected:
        _draw_header(draw, title_font, meta_font, jog_step_mm, locked, "OpenPnP bridge offline")
        return image

    if not position.ok:
        _draw_header(
            draw,
            title_font,
            meta_font,
            jog_step_mm,
            locked,
            position.error or "Position unavailable",
        )
        return image

    speed_pct = display_speed_pct if display_speed_pct is not None else position.speed_pct
    _draw_header(
        draw,
        title_font,
        meta_font,
        jog_step_mm,
        locked,
        speed_pct=speed_pct,
        jog_dial_locked=jog_dial_locked,
        speed_dial_locked=speed_dial_locked,
    )
    small_font = _load_font(14)
    step_font = _load_font(12)
    _draw_job_panel(draw, position, meta_font, small_font, width)

    steps = dial_steps_mm or {}
    for center_x, axis, value in zip(
        _AXIS_CENTERS,
        ("X", "Y", "Z", "C"),
        (position.x, position.y, position.z, position.c),
    ):
        _draw_axis_column(
            draw,
            center_x,
            axis,
            value,
            meta_font,
            value_font,
            step_font,
            dial_step_mm=steps.get(axis),
        )

    return image


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))
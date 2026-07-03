"""Rendering utilities for printable BCH thermal stamps."""

from __future__ import annotations

import textwrap
from dataclasses import asdict, dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

try:
    from .qrgen import make_qr_image
except ImportError:
    from qrgen import make_qr_image


PAPER_WIDTH_PX = 384
DEFAULT_MARGIN = 18


@dataclass
class StampDesign:
    title_enabled: bool = True
    wallet_enabled: bool = True
    claim_enabled: bool = True
    instructions_enabled: bool = True
    details_enabled: bool = True
    title: str = "Recibiste Bitcoin Cash"
    wallet_label: str = "1. Instalar wallet"
    wallet_qr_data: str = "https://selene.cash/"
    claim_label: str = "2. Cobrar esta estampa"
    claim_qr_data: str = "bitcoincash:qp000000000000000000000000000000000000000000"
    instructions: str = "Escaneá el QR de cobro con tu wallet BCH. Si necesitás una wallet, usá primero el QR superior."
    amount: str = "0.0001"
    expiry: str = "Sin vencimiento"
    footer_note: str = "BCH Thermal Stamps"
    separator_enabled: bool = True
    section_order: list[str] = field(default_factory=lambda: ["title", "wallet", "claim", "instructions", "details", "separator"])
    custom_blocks: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "StampDesign":
        values = cls().to_dict()
        values.update({key: value for key, value in data.items() if key in values})
        return cls(**values)


def render_stamp(design: StampDesign, stamp_id: str | None = None, scale: int = 1) -> Image.Image:
    width = PAPER_WIDTH_PX * scale
    margin = DEFAULT_MARGIN * scale
    draw_font_scale = scale
    title_font = _font(28 * draw_font_scale, bold=True)
    label_font = _font(18 * draw_font_scale, bold=True)
    body_font = _font(16 * draw_font_scale)
    small_font = _font(13 * draw_font_scale)

    renderers = {
        "title": lambda: _render_title(width, margin, design.title, title_font, body_font, scale),
        "wallet": lambda: _render_qr_block(width, margin, design.wallet_label, design.wallet_qr_data, label_font, small_font, scale),
        "claim": lambda: _render_qr_block(width, margin, design.claim_label, design.claim_qr_data, label_font, small_font, scale, subtitle="Escaneá para cobrar"),
        "instructions": lambda: _render_text_block(width, margin, design.instructions, body_font, scale),
        "details": lambda: _render_details_block(width, margin, design, stamp_id, small_font, scale),
        "separator": lambda: _render_separator(width, margin, scale),
    }
    enabled = {
        "title": design.title_enabled, "wallet": design.wallet_enabled,
        "claim": design.claim_enabled, "instructions": design.instructions_enabled,
        "details": design.details_enabled, "separator": design.separator_enabled,
    }

    blocks: list[Image.Image] = []
    for key in design.section_order:
        if key in renderers:
            if enabled.get(key, False):
                blocks.append(renderers[key]())
        else:
            bd = next((b for b in design.custom_blocks if b["id"] == key), None)
            if bd and bd.get("enabled", True):
                if bd["type"] == "text":
                    blocks.append(_render_text_block(width, margin, bd.get("content", ""), body_font, scale))
                elif bd["type"] == "qr":
                    blocks.append(_render_qr_block(width, margin, bd.get("qr_label", ""), bd.get("qr_data", ""), label_font, small_font, scale))
                elif bd["type"] == "separator":
                    blocks.append(_render_separator(width, margin, scale))

    total_height = margin
    for block in blocks:
        total_height += block.height + (10 * scale)
    total_height += margin

    image = Image.new("RGB", (width, max(total_height, 220 * scale)), "white")
    y = margin
    for block in blocks:
        image.paste(block, (0, y))
        y += block.height + (10 * scale)

    return _to_thermal(image)


def save_stamp_image(path: Path, design: StampDesign, stamp_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = render_stamp(design, stamp_id=stamp_id, scale=2)
    image.save(path)


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        "arialbd.ttf" if bold else "arial.ttf",
        "segoeuib.ttf" if bold else "segoeui.ttf",
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _render_title(width: int, margin: int, title: str, title_font: ImageFont.ImageFont, body_font: ImageFont.ImageFont, scale: int) -> Image.Image:
    max_text_width = width - (margin * 2) - (14 * scale)
    title_lines = _wrap_by_pixels(title, title_font, max_text_width)
    title_line_height = int(_text_size("Ag", title_font)[1] * 1.18)
    subtitle_height = int(_text_size("Ag", body_font)[1] * 1.25)
    height = max(84 * scale, (24 * scale) + (title_line_height * len(title_lines)) + subtitle_height + (18 * scale))
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((margin, 4 * scale, width - margin, height - 4 * scale), radius=0, outline="black", width=2 * scale)
    y = 14 * scale
    for line in title_lines:
        _center_text(draw, line, width, y, title_font, max_width=max_text_width)
        y += title_line_height
    _center_text(draw, "Vale BCH imprimible", width, y + (5 * scale), body_font, max_width=max_text_width)
    return image


def _render_qr_block(width: int, margin: int, label: str, data: str, label_font: ImageFont.ImageFont, small_font: ImageFont.ImageFont, scale: int, subtitle: str | None = None) -> Image.Image:
    qr_size = min(260 * scale, width - (margin * 4))
    label_height = 30 * scale
    bottom_text_height = 28 * scale
    height = label_height + qr_size + bottom_text_height + 22 * scale
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    _center_text(draw, label, width, 4 * scale, label_font)
    qr_image = _qr_image(data, qr_size)
    qr_size = qr_image.width  # actual size after integer scaling
    x = (width - qr_size) // 2
    y = label_height + 6 * scale
    image.paste(qr_image, (x, y))
    draw.rectangle((x - 5 * scale, y - 5 * scale, x + qr_size + 5 * scale, y + qr_size + 5 * scale), outline="black", width=2 * scale)
    bottom = subtitle if subtitle is not None else _short_data(data)
    if bottom:
        _center_text(draw, bottom, width, y + qr_size + 14 * scale, small_font)
    return image


def _render_text_block(width: int, margin: int, text: str, font: ImageFont.ImageFont, scale: int) -> Image.Image:
    chars = 37 if scale == 1 else 42
    lines = []
    for paragraph in text.splitlines():
        lines.extend(textwrap.wrap(paragraph, width=chars) or [""])
    line_height = int(_text_size("Ag", font)[1] * 1.35)
    height = (line_height * len(lines)) + (26 * scale)
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    y = 10 * scale
    for line in lines:
        _center_text(draw, line, width, y, font)
        y += line_height
    return image


def _render_details_block(width: int, margin: int, design: StampDesign, stamp_id: str | None, font: ImageFont.ImageFont, scale: int) -> Image.Image:
    height = 74 * scale
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    y = 8 * scale
    lines = [
        f"Monto: {design.amount} BCH",
        f"Vence: {design.expiry}",
    ]
    if stamp_id:
        lines.append(f"ID: {stamp_id[:8]}")
    if design.footer_note:
        lines.append(design.footer_note)
    for line in lines:
        _center_text(draw, line, width, y, font)
        y += 17 * scale
    return image


def _render_separator(width: int, margin: int, scale: int) -> Image.Image:
    height = 16 * scale
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    _draw_cut_line(draw, width, height // 2, scale)
    return image


def _qr_image(data: str, target_size: int) -> Image.Image:
    # Claim QRs carry a private key, so use high error correction to survive
    # smudgy thermal printing. The funding/wallet URL is short either way.
    return make_qr_image(data or " ", target_size, high_ec=True)


def _to_thermal(image: Image.Image) -> Image.Image:
    grayscale = image.convert("L")
    return grayscale.point(lambda value: 0 if value < 180 else 255, mode="1").convert("RGB")


def _center_text(draw: ImageDraw.ImageDraw, text: str, width: int, y: int, font: ImageFont.ImageFont, max_width: int | None = None) -> None:
    if max_width is not None:
        text = _ellipsize_to_width(text, font, max_width)
    text_width, _ = _text_size(text, font)
    draw.text(((width - text_width) // 2, y), text, font=font, fill="black")


def _text_size(text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    box = font.getbbox(text)
    return box[2] - box[0], box[3] - box[1]


def _wrap_by_pixels(text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if _text_size(candidate, font)[0] <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    fitted: list[str] = []
    for line in lines:
        fitted.extend(_split_long_word(line, font, max_width))
    return fitted


def _split_long_word(text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    if _text_size(text, font)[0] <= max_width:
        return [text]
    pieces: list[str] = []
    current = ""
    for char in text:
        candidate = current + char
        if current and _text_size(candidate, font)[0] > max_width:
            pieces.append(current)
            current = char
        else:
            current = candidate
    if current:
        pieces.append(current)
    return pieces


def _ellipsize_to_width(text: str, font: ImageFont.ImageFont, max_width: int) -> str:
    if _text_size(text, font)[0] <= max_width:
        return text
    suffix = "..."
    available = max_width - _text_size(suffix, font)[0]
    if available <= 0:
        return suffix
    result = ""
    for char in text:
        if _text_size(result + char, font)[0] > available:
            break
        result += char
    return result + suffix


def _short_data(data: str) -> str:
    if len(data) <= 34:
        return data
    return f"{data[:15]}...{data[-15:]}"


def _draw_cut_line(draw: ImageDraw.ImageDraw, width: int, y: int, scale: int) -> None:
    dash = 8 * scale
    gap = 5 * scale
    x = 18 * scale
    while x < width - (18 * scale):
        draw.line((x, y, min(x + dash, width - (18 * scale)), y), fill="black", width=max(1, scale))
        x += dash + gap

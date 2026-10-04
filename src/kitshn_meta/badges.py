"""Shields-style flat badges: 20px high, Verdana 11px, one or more colored segments."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from html import escape

from .verdana import VERDANA_11

HEIGHT = 20
PAD = 6
GAP = 4
LOGO_SIZE = 14
DOT_SIZE = 7
BAR_STEP = 3

LABEL_COLOR = "#2F3532"
LIVE = "#2E9F5B"
DEPLOYING = "#0B7BC6"
WARN = "#D9822B"
DOWN = "#D6453D"
UNKNOWN = "#8A918C"
REF = "#47524B"
BASIL = "#3E6B55"

# The KitSHn pot on a 14-unit grid, drawn in white.
POT_SHAPES = (
    (6, 1.2, 2, 1.6, 0.5),
    (2.2, 3.4, 9.6, 1.5, 0.75),
    (3, 5.6, 8, 6.6, 1.6),
    (0.8, 6.8, 2.4, 1.4, 0.7),
    (10.8, 6.8, 2.4, 1.4, 0.7),
)
POT_RECTS = "".join(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}"/>' for x, y, w, h, r in POT_SHAPES)
POT_SVG = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 14 14"><g fill="#fff">{POT_RECTS}</g></svg>'


@dataclass(frozen=True, slots=True)
class Segment:
    text: str = ""
    color: str = LABEL_COLOR
    logo: bool = False
    dot: bool = False
    pulse: bool = False
    dots: tuple[str, ...] = ()
    bars: tuple[int, ...] = field(default=())


def text_width(text: str) -> int:
    fallback = VERDANA_11["m"]
    return math.ceil(sum(VERDANA_11.get(char, fallback) for char in text))


def _parts(segment: Segment) -> list[tuple[str, int]]:
    parts: list[tuple[str, int]] = []
    if segment.logo:
        parts.append(("logo", LOGO_SIZE))
    if segment.dot:
        parts.append(("dot", DOT_SIZE))
    if segment.dots:
        parts.append(("dots", len(segment.dots) * (DOT_SIZE + 2) - 2))
    if segment.bars:
        parts.append(("bars", len(segment.bars) * BAR_STEP - 1))
    if segment.text:
        parts.append(("text", text_width(segment.text)))
    return parts


def render(segments: list[Segment], title: str) -> str:
    """Render segments left to right as one badge. `title` is the accessible name."""

    if not segments:
        msg = "a badge needs at least one segment"
        raise ValueError(msg)
    x = 0
    rects: list[str] = []
    content: list[str] = []
    for segment in segments:
        parts = _parts(segment)
        inner = sum(width for _, width in parts) + GAP * max(0, len(parts) - 1)
        width = inner + PAD * 2
        rects.append(f'<rect x="{x}" width="{width}" height="{HEIGHT}" fill="{segment.color}"/>')
        cursor = x + PAD
        for kind, part_width in parts:
            content.append(_draw(kind, cursor, segment))
            cursor += part_width + GAP
        x += width
    label = escape(title, quote=True)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{x}" height="{HEIGHT}" role="img" aria-label="{label}">'
        f"<title>{label}</title>"
        '<linearGradient id="s" x2="0" y2="100%"><stop offset="0" stop-color="#bbb" stop-opacity=".1"/>'
        '<stop offset="1" stop-opacity=".1"/></linearGradient>'
        f'<clipPath id="r"><rect width="{x}" height="{HEIGHT}" rx="3" fill="#fff"/></clipPath>'
        f'<g clip-path="url(#r)">{"".join(rects)}<rect width="{x}" height="{HEIGHT}" fill="url(#s)"/></g>'
        '<g font-family="Verdana,Geneva,DejaVu Sans,sans-serif" font-size="11" text-rendering="geometricPrecision">'
        f"{''.join(content)}</g></svg>"
    )


def _draw(kind: str, x: int, segment: Segment) -> str:
    if kind == "logo":
        return f'<g transform="translate({x} 3)" fill="#fff">{POT_RECTS}</g>'
    if kind == "dot":
        pulse = (
            '<animate attributeName="opacity" values="1;0.25;1" dur="1.4s" repeatCount="indefinite"/>'
            if segment.pulse
            else ""
        )
        return f'<circle cx="{x + 3.5}" cy="10" r="3.5" fill="#fff">{pulse}</circle>'
    if kind == "dots":
        return "".join(
            f'<circle cx="{x + index * (DOT_SIZE + 2) + 3.5}" cy="10" r="3.5" fill="{color}"/>'
            for index, color in enumerate(segment.dots)
        )
    if kind == "bars":
        peak = max(max(segment.bars), 1)
        bars = []
        for index, value in enumerate(segment.bars):
            height = 1 if value == 0 else max(2, round(value / peak * 11))
            opacity = "0.35" if value == 0 else "0.95"
            bars.append(
                f'<rect x="{x + index * BAR_STEP}" y="{15 - height}" width="2" height="{height}" fill="#fff" fill-opacity="{opacity}"/>'
            )
        return "".join(bars)
    text = escape(segment.text)
    return (
        f'<text x="{x}" y="15" fill="#010101" fill-opacity=".3">{text}</text>'
        f'<text x="{x}" y="14" fill="#fff">{text}</text>'
    )

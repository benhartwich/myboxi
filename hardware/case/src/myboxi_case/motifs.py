"""Figures for the standee ("Figur gestalten", docs/gehaeuse.md): front views in millimetres.

Drawn in x/y with x centred and y up; y = 0 is where the figure meets the base (the tab below it
goes into the slot). Each motif has an outline and details (eyes, nose, …) for the accent
colour, at least 1 mm inside the outline. Tips stay round (children).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from manifold3d import CrossSection

from myboxi_case.geom import circle, polygon, rect, rounded_rect, section_union

Motif = Literal["bear", "cat", "bunny", "frog", "unicorn", "person"]
MOTIFS: tuple[Motif, ...] = ("bear", "cat", "bunny", "frog", "unicorn", "person")
LABELS: dict[str, str] = {
    "bear": "Bär",
    "cat": "Katze",
    "bunny": "Hase",
    "frog": "Frosch",
    "unicorn": "Einhorn",
    "person": "Männchen",
    "drawing": "Zeichnung",
}
# Suggested figure colour per motif (palette keys).
COLOURS: dict[str, str] = {
    "bear": "braun",
    "cat": "apricot",
    "bunny": "flieder",
    "frog": "moos",
    "unicorn": "weiss",
    "person": "himmel",
    "drawing": "creme",  # light, so the strokes show
}
# Suggested accent per motif (the name, and on a round figure belly, snout, ears, horn).
ACCENTS: dict[str, str] = {
    "bear": "creme",
    "cat": "creme",
    "bunny": "weiss",
    "frog": "sonne",
    "unicorn": "rosa",
    "person": "braun",
}


@dataclass(frozen=True)
class Drawing:
    outline: CrossSection
    details: CrossSection


def _capsule(x0: float, y0: float, x1: float, y1: float, w: float) -> CrossSection:
    return CrossSection.batch_hull([circle(w / 2, x0, y0), circle(w / 2, x1, y1)])


def _ellipse(rx: float, ry: float, x: float, y: float) -> CrossSection:
    r = max(rx, ry)  # drawn at full size, so it stays smooth
    return CrossSection.circle(r).scale((rx / r, ry / r)).translate((x, y))


def _poly(points: list[tuple[float, float]]) -> CrossSection:
    """A polygon in either direction (the fill rule needs counter-clockwise)."""
    area = sum(
        x0 * y1 - x1 * y0
        for (x0, y0), (x1, y1) in zip(points, [*points[1:], points[0]], strict=True)
    )
    return polygon(points if area > 0 else list(reversed(points)))


def _arc(cx: float, cy: float, r: float, a0: float, a1: float, w: float) -> CrossSection:
    """A stroke along a circle from angle a0 to a1 (degrees)."""
    steps = max(4, int(abs(a1 - a0) / 12))
    pts = [
        (cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / steps)),
         cy + r * math.sin(math.radians(a0 + (a1 - a0) * i / steps)))
        for i in range(steps + 1)
    ]  # fmt: skip
    return section_union([_capsule(*pts[i], *pts[i + 1], w) for i in range(len(pts) - 1)])


def _round(section: CrossSection, r: float = 1.5) -> CrossSection:
    """Opening: every convex tip becomes round."""
    return section.offset(-r).offset(r)


def _body() -> CrossSection:
    """Flat at the bottom (it stands on the base), round above."""
    return CrossSection.batch_hull(
        [rounded_rect(-14.0, 0.0, 14.0, 10.0, 4.0), circle(12.0, 0, 21.0)]
    )


def _face(y: float, *, eyes: float = 1.9, spread: float = 5.0) -> list[CrossSection]:
    return [
        circle(eyes, -spread, y + 2.5),
        circle(eyes, spread, y + 2.5),
        _ellipse(2.4, 1.7, 0, y - 1.2),
        _arc(-1.4, y - 2.6, 1.4, 180, 360, 0.9),
        _arc(1.4, y - 2.6, 1.4, 180, 360, 0.9),
    ]


def drawing(motif: Motif) -> Drawing:
    head_y = 35.0
    match motif:
        case "bear":
            outline = section_union(
                [_body(), circle(13.0, 0, head_y),
                 circle(5.5, -10.0, head_y + 10.0), circle(5.5, 10.0, head_y + 10.0)]
            )  # fmt: skip
            details = [
                *_face(head_y),
                circle(2.6, -10.0, head_y + 10.3),
                circle(2.6, 10.0, head_y + 10.3),
            ]
        case "cat":
            ear = _round(_poly([(-12.5, head_y + 4), (-9.5, head_y + 17), (-2.5, head_y + 10)]))
            outline = section_union([_body(), circle(13.0, 0, head_y), ear, ear.mirror((1, 0))])
            details = [
                _ellipse(1.5, 2.5, -5.0, head_y + 2.6),
                _ellipse(1.5, 2.5, 5.0, head_y + 2.6),
                _round(_poly([(-1.8, head_y - 0.4), (1.8, head_y - 0.4), (0, head_y - 2.3)]), 0.5),
                _arc(-1.3, head_y - 3.4, 1.3, 180, 360, 0.9),
                _arc(1.3, head_y - 3.4, 1.3, 180, 360, 0.9),
            ]
            for side in (-1, 1):
                for k, dy in enumerate((0.8, -1.2)):
                    details.append(
                        _capsule(side * 3.8, head_y - 1.0 + dy, side * 10.5,
                                 head_y + 0.2 + dy * 2.2 - k * 0.6, 0.9)
                    )  # fmt: skip
        case "bunny":
            outline = section_union(
                [_body(), circle(12.5, 0, head_y),
                 _capsule(-5.5, head_y + 9.0, -7.0, head_y + 25.0, 8.4),
                 _capsule(5.5, head_y + 9.0, 7.0, head_y + 25.0, 8.4)]
            )  # fmt: skip
            details = [
                *_face(head_y),
                _capsule(-5.8, head_y + 13.0, -6.8, head_y + 24.0, 3.6),
                _capsule(5.8, head_y + 13.0, 6.8, head_y + 24.0, 3.6),
            ]
        case "frog":
            outline = section_union(
                [CrossSection.batch_hull([rounded_rect(-15.0, 0.0, 15.0, 9.0, 4.0),
                                          circle(13.5, 0, 17.0)]),
                 _ellipse(17.5, 11.0, 0, 29.0),
                 circle(6.8, -9.5, 39.0), circle(6.8, 9.5, 39.0)]
            )  # fmt: skip
            details = [
                circle(3.0, -9.5, 39.5),
                circle(3.0, 9.5, 39.5),
                _arc(0, 35.0, 10.0, 220, 320, 1.4),
                circle(0.8, -2.2, 31.0),
                circle(0.8, 2.2, 31.0),
            ]
        case "unicorn":
            horn = _round(
                _poly([(-4.4, head_y + 10.0), (4.4, head_y + 10.0), (0, head_y + 25.0)]), 1.2
            )
            ear = _round(_poly([(-11.5, head_y + 5), (-8.5, head_y + 14), (-3.5, head_y + 9.5)]))
            outline = section_union(
                [_body(), circle(12.5, 0, head_y), ear, ear.mirror((1, 0)), horn]
            )
            details = [
                _arc(-5.0, head_y + 3.2, 2.6, 200, 340, 0.9),
                _arc(5.0, head_y + 3.2, 2.6, 200, 340, 0.9),
                circle(0.8, -1.8, head_y - 2.2),
                circle(0.8, 1.8, head_y - 2.2),
                _arc(0, head_y - 1.0, 4.2, 230, 310, 0.9),
                _capsule(-2.2, head_y + 12.6, 2.0, head_y + 14.2, 0.9),
                _capsule(-1.3, head_y + 16.4, 1.2, head_y + 17.4, 0.9),
            ]
        case "person":
            body = _poly([(-18, 25), (-7, 33.5), (7, 33.5), (18, 25), (18, 19.5),
                          (8.5, 18.5), (13.5, 0), (2.5, 0), (0, 5), (-2.5, 0),
                          (-13.5, 0), (-8.5, 18.5), (-18, 19.5)])  # fmt: skip
            outline = section_union([_round(body, 2.4), circle(8.5, 0, 40.5)])
            details = [
                circle(1.5, -3.2, 42.0),
                circle(1.5, 3.2, 42.0),
                _arc(0, 40.0, 3.6, 215, 325, 0.9),
                circle(1.3, 0, 26.5),
                circle(1.3, 0, 21.0),
            ]
    outline = outline ^ rect(-50.0, 0.0, 50.0, 100.0)  # nothing below where it meets the base
    # Details stay inside the outline with a rim, also where a shape gets narrow (horn tip).
    return Drawing(outline, section_union(details) ^ outline.offset(-1.0))

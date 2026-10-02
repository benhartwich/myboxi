"""A child's drawing becomes a figure ("Figur gestalten", docs/gehaeuse.md).

Two steps:

1. ``ink(gray)`` at upload: the dark strokes of a photographed drawing as polygons, in pixels.
   The photo itself is never kept; only these strokes are (the server, for 7 days).
2. ``drawing(strokes, turn)`` at every build: the strokes scaled to the figure, enclosed areas
   filled and a 2.5 mm border around everything, like a die-cut sticker. The border keeps thin
   lines printable and leaves no sharp tips; the strokes become the figure's front details.
"""

from __future__ import annotations

import json
from typing import Any, Literal, cast

import numpy as np
import numpy.typing as npt
from manifold3d import CrossSection, FillRule, OpType

from myboxi_case.geom import bounds, polygons, rect, section_union
from myboxi_case.motifs import Drawing

SIZE = 400  # the server decodes photos to SIZE x SIZE grey pixels (white padding)
MAX_WIDTH = 40.0  # fits the 40 mm base
MAX_HEIGHT = 52.0
BORDER = 2.5
FOOT_WIDTH, FOOT_HEIGHT = 24.0, 3.0  # stands flat on the base, over the slot
MIN_STROKE = 0.8  # printable details
MIN_PART = 40.0  # pixels: smaller specks are dust or noise
Turn = Literal[0, 90, 180, 270]
Rings = list[list[tuple[float, float]]]


class TraceError(ValueError):
    """German, shown to the user."""


NOT_FOUND = (
    "Auf dem Foto ist keine Zeichnung zu erkennen. Am besten mit dunklem Stift auf hellem "
    "Papier malen und gerade von oben fotografieren, das Blatt bildfüllend."
)


def _box_blur(a: npt.NDArray[np.float64], radius: int) -> npt.NDArray[np.float64]:
    """Mean over a (2r+1)² window, via an integral image; edges repeat."""
    padded = np.pad(a, radius + 1, mode="edge")
    s = padded.cumsum(0).cumsum(1)
    n = 2 * radius + 1
    total = s[n:, n:] - s[:-n, n:] - s[n:, :-n] + s[:-n, :-n]
    return total[: a.shape[0], : a.shape[1]] / (n * n)


def ink_mask(gray: npt.NDArray[np.uint8]) -> npt.NDArray[np.bool_]:
    """Dark compared with the paper around it, so shadows and uneven light do not count."""
    g = gray.astype(np.float64) / 255.0
    paper = _box_blur(g, 20)
    return (g / np.maximum(paper, 0.05) < 0.72) & (g < 0.8)


def _section(mask: npt.NDArray[np.bool_]) -> CrossSection:
    """Pixels as a cross section (one rectangle per run in a row), y up."""
    h = mask.shape[0]
    rects: list[CrossSection] = []
    for y in range(h):
        row = mask[y]
        if not row.any():
            continue
        edges = np.diff(np.concatenate(([0], row.astype(np.int8), [0])))
        for a, b in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1), strict=True):
            rects.append(rect(float(a), float(h - y - 1), float(b), float(h - y)))
    if not rects:
        return CrossSection()
    return CrossSection.batch_boolean(rects, OpType.Add)


def _polygons(section: CrossSection) -> list[npt.NDArray[np.float64]]:
    """Every ring (outer counter-clockwise, holes clockwise); typed access to manifold3d."""
    raw: Any = section
    return [np.asarray(ring, dtype=np.float64) for ring in raw.to_polygons()]


def _rings(section: CrossSection) -> Rings:
    return [
        [(round(float(x), 2), round(float(y), 2)) for x, y in pts.tolist()]
        for pts in _polygons(section)
    ]


def ink(gray: npt.NDArray[np.uint8]) -> Rings:
    """The strokes of a drawing (``SIZE`` x ``SIZE`` grey pixels) as rings, in pixels.

    Specks and anything touching the image border (the paper's edge, the table) are dropped.
    Raises TraceError when nothing is left.
    """
    if gray.ndim != 2 or gray.shape[0] < 32 or gray.shape[1] < 32:
        raise TraceError(NOT_FOUND)
    strokes = _section(ink_mask(gray))
    h, w = gray.shape
    keep: list[CrossSection] = []
    for part in strokes.decompose():
        x0, y0, x1, y1 = bounds(part)
        touches = x0 <= 1 or y0 <= 1 or x1 >= w - 1 or y1 >= h - 1
        if part.area() >= MIN_PART and not touches:
            keep.append(part)
    if not keep:
        raise TraceError(NOT_FOUND)
    found = section_union(keep).simplify(0.5)
    x0, y0, x1, y1 = bounds(found)
    if max(x1 - x0, y1 - y0) < 0.15 * max(h, w):
        raise TraceError(NOT_FOUND)  # a few dots, not a drawing
    return _rings(found)


def dumps(rings: Rings) -> str:
    return json.dumps({"version": 1, "ink": rings}, separators=(",", ":"))


def loads(data: str) -> Rings:
    raw: Any = json.loads(data)
    if not isinstance(raw, dict) or cast(dict[str, Any], raw).get("version") != 1:
        raise ValueError("unknown drawing format")
    rings: list[list[list[float]]] = cast(dict[str, Any], raw)["ink"]
    return [[(float(x), float(y)) for x, y in ring] for ring in rings]


def _filled(section: CrossSection) -> CrossSection:
    """Holes closed: a drawn circle becomes a disc (outer rings only)."""
    outer: list[list[tuple[float, float]]] = []
    for pts in _polygons(section):
        x, y = pts[:, 0], pts[:, 1]
        if float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y)) > 0:
            outer.append([(float(a), float(b)) for a, b in pts.tolist()])
    return polygons(outer, FillRule.Positive) if outer else CrossSection()


def drawing(rings: Rings, turn: Turn = 0) -> Drawing:
    """The figure from stored strokes: outline (filled, bordered, on a foot) and details."""
    strokes = polygons(rings, FillRule.Positive) if rings else CrossSection()
    if strokes.is_empty():
        raise TraceError(NOT_FOUND)
    if turn:
        strokes = strokes.rotate(-float(turn))  # clockwise, like turning the photo
    filled = _filled(strokes)
    x0, y0, x1, y1 = bounds(filled)
    scale = min(
        (MAX_WIDTH - 2 * BORDER) / (x1 - x0),
        (MAX_HEIGHT - BORDER - FOOT_HEIGHT) / (y1 - y0),
    )

    def place(section: CrossSection) -> CrossSection:
        moved = section.translate((-(x0 + x1) / 2, -y0)).scale((scale, scale))
        return moved.translate((0.0, FOOT_HEIGHT))

    filled_mm, strokes_mm = place(filled), place(strokes)
    outline = filled_mm.offset(BORDER)
    # Separate parts (a sun next to a house) join where the borders touch; otherwise the
    # largest part is the figure.
    parts = sorted(outline.decompose(), key=lambda p: p.area(), reverse=True)
    outline = parts[0]
    foot = rect(-FOOT_WIDTH / 2, 0.0, FOOT_WIDTH / 2, FOOT_HEIGHT + BORDER)
    outline = section_union([outline, foot]).offset(-1.5).offset(1.5)  # round every tip
    outline = outline ^ rect(-60.0, 0.0, 60.0, 120.0)
    widened = strokes_mm.offset(MIN_STROKE / 2)
    details = widened ^ outline.offset(-1.0)
    details = section_union([p for p in details.decompose() if p.area() > 0.3])
    return Drawing(outline, details)

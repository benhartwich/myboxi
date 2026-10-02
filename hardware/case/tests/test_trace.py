"""A child's drawing becomes a figure ("Figur gestalten", stage 3): strokes found on a photo,
the paper's edge ignored, a printable sticker-like outline with the strokes as details."""

from __future__ import annotations

import numpy as np
import numpy.typing as npt
import pytest

from myboxi_case import figures, geom, trace
from myboxi_case.checks import check_piece


def stick_figure() -> npt.NDArray[np.uint8]:
    """Pencil lines on paper that is lit unevenly, with the dark table at the left edge."""
    size = trace.SIZE
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float64)
    img = 235.0 - 40.0 * (xx / size)

    def line(x0: float, y0: float, x1: float, y1: float, width: float = 3.0) -> None:
        length2 = (x1 - x0) ** 2 + (y1 - y0) ** 2
        d = np.abs((y1 - y0) * xx - (x1 - x0) * yy + x1 * y0 - y1 * x0) / np.sqrt(length2)
        t = ((xx - x0) * (x1 - x0) + (yy - y0) * (y1 - y0)) / length2
        img[(d < width / 2) & (t >= 0) & (t <= 1)] = 40.0

    img[np.abs(np.hypot(xx - 200, yy - 110) - 45) < 2] = 40.0  # head
    img[(np.hypot(xx - 185, yy - 100) < 6) | (np.hypot(xx - 215, yy - 100) < 6)] = 40.0
    line(180, 125, 220, 125)
    line(200, 155, 200, 280)
    line(130, 200, 270, 190)
    line(200, 280, 150, 360)
    line(200, 280, 250, 360)
    img[:, 0:6] = 30.0  # the table
    return img.clip(0, 255).astype(np.uint8)


def test_strokes_are_found_and_the_table_is_ignored() -> None:
    rings = trace.ink(stick_figure())
    xs = [x for ring in rings for x, _ in ring]
    assert min(xs) > 100  # nothing near the left edge, where the table was
    assert trace.loads(trace.dumps(rings)) == rings


@pytest.mark.parametrize(
    "gray",
    [
        np.full((trace.SIZE, trace.SIZE), 230, dtype=np.uint8),  # an empty page
        np.pad(np.full((6, 6), 20, dtype=np.uint8), 197, constant_values=230),  # one dot
    ],
)
def test_no_drawing_no_figure(gray: npt.NDArray[np.uint8]) -> None:
    with pytest.raises(trace.TraceError, match="keine Zeichnung"):
        trace.ink(gray)


def test_the_figure_is_one_printable_piece() -> None:
    drawing = trace.drawing(trace.ink(stick_figure()))
    x0, y0, x1, y1 = geom.bounds(drawing.outline)
    assert y0 == pytest.approx(0.0)  # stands on the base
    assert x1 - x0 <= trace.MAX_WIDTH + 1e-6
    assert y1 - y0 <= trace.MAX_HEIGHT + 1e-6
    assert len(drawing.outline.decompose()) == 1
    assert (drawing.details - drawing.outline.offset(-0.99)).area() < 1e-3
    assert drawing.details.area() > 20  # the strokes are on the front


def test_turning_the_photo() -> None:
    strokes = trace.ink(stick_figure())
    upright = geom.bounds(trace.drawing(strokes).outline)
    turned = geom.bounds(trace.drawing(strokes, 90).outline)
    assert (upright[3] - upright[1]) > (upright[2] - upright[0])  # taller than wide
    assert (turned[3] - turned[1]) < (turned[2] - turned[0]) + 8.0  # on its side


def test_a_drawing_stands_in_the_base() -> None:
    cfg = figures.FigureConfig(top="standee", motif="drawing", drawing="0123456789abcdef")
    assert cfg.query()["drawing"] == "0123456789abcdef"
    model = figures.build_figure(cfg, trace.ink(stick_figure()))
    assert model.tile is not None
    for piece in model.pieces:
        assert check_piece(piece) == [], piece.key
    with pytest.raises(figures.FigureError, match="hochladen"):
        figures.build_figure(cfg)
    # the drawing only counts for a figure drawn by hand
    assert (
        "drawing"
        not in figures.FigureConfig(top="standee", motif="cat", drawing="0123456789abcdef").query()
    )

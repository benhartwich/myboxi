"""Round figures on the NFC base ("Figur gestalten", docs/gehaeuse.md): printed upright in one
piece with the base, without supports, nothing starting in mid-air, the name in front."""

from __future__ import annotations

import io
import itertools
import zipfile
from xml.etree import ElementTree as ET

import pytest
from manifold3d import Manifold
from pydantic import ValidationError

from myboxi_case import figures, figures3d, motifs
from myboxi_case.build import Piece
from myboxi_case.checks import _overhangs, check_piece  # pyright: ignore[reportPrivateUsage]
from myboxi_case.figures import FigureConfig, build_figure
from myboxi_case.geom import bbox, bounds, union

SHAPES = ("round", "square", "heart", "star")


def islands(m: Manifold, step: float = 0.2) -> list[tuple[float, float, float]]:
    """Layers that start in mid-air: farther than a quarter millimetre from the layer below
    (a printed line holds on to its neighbour that far)."""
    _, _, z0, _, _, z1 = bbox(m)
    found: list[tuple[float, float, float]] = []
    below = m.slice(z0 + 0.05)
    z = z0 + step
    while z < z1:
        here = m.slice(z)
        for part in here.decompose():
            if part.area() > 0.01 and (part ^ below.offset(0.25)).area() < 1e-4:
                x0, y0, x1, y1 = bounds(part)
                found.append((round(z, 1), round((x0 + x1) / 2, 1), round((y0 + y1) / 2, 1)))
        below = here
        z += step
    return found


@pytest.mark.parametrize(("motif", "size"), list(itertools.product(motifs.MOTIFS, (40, 50))))
def test_every_figure_prints_without_supports(motif: motifs.Motif, size: int) -> None:
    fig = figures3d.figure(motif, size / 40)
    whole = union([fig.body, fig.accent, fig.details])
    assert len([p for p in whole.decompose() if p.volume() > 1.0]) == 1
    # no face steeper than 45° that is wider than a printed line, nothing in mid-air
    assert _overhangs(whole, 2.0) == []
    assert islands(whole) == []
    assert not fig.accent.is_empty()
    assert not fig.details.is_empty()  # a face


@pytest.mark.parametrize("motif", motifs.MOTIFS)
def test_every_figure_stands_on_every_base(motif: str) -> None:
    for shape, size in itertools.product(SHAPES, (40, 50)):
        cfg = FigureConfig.model_validate(
            {"shape": shape, "size": size, "motif": motif, "name": "Mia"}
        )
        model = build_figure(cfg)
        assert len(model.extras) == 3
        figure = union([e.solid for e in model.extras])
        # one object: base and figure, checked as printed (without the zero-volume seams
        # that joining the colours leaves; a slicer ignores them too)
        whole = model.piece.solid + figure
        printed = Piece(
            key="figure", label="", color="", tool=1, rotation=(0, 0, 0), explode=(0, 0, 0),
            solid=union([p for p in whole.decompose() if p.volume() > 0.1]),
        )  # fmt: skip
        assert check_piece(printed) == [], (cfg.query(), check_piece(printed))
        # the figure stands on the flat top of the base, the name in front of its feet
        root = figures3d.ROOT * size / 40  # reaches into the base: one solid when printed
        assert bbox(figure)[2] == pytest.approx(figures.HEIGHT - root)
        feet = figure.slice(figures.HEIGHT + 0.1)
        assert (feet - model.outline.offset(-2.4)).area() < 1e-3
        assert bounds(model.label)[3] < bounds(feet)[1] - 1.0


def test_three_colours_and_the_base_in_one_object() -> None:
    model = build_figure(FigureConfig(motif="frog", name="Ida"))
    with zipfile.ZipFile(io.BytesIO(figures.threemf(model))) as zf:
        settings = ET.fromstring(zf.read("Metadata/model_settings.config"))  # noqa: S314
        modelxml = zf.read("3D/3dmodel.model").decode()
        pause = zf.read("Metadata/custom_gcode_per_layer.xml").decode()
    extruders = {m.get("value") for m in settings.iter("metadata") if m.get("key") == "extruder"}
    assert extruders == {"1", "2", "3", "4"}  # base, name and belly, frog, face
    assert modelxml.count("<item ") == 1  # printed in one piece
    assert 'top_z="2.21"' in pause  # the chip still goes in below
    bundle = zipfile.ZipFile(io.BytesIO(figures.bundle_zip(model)))
    readme = bundle.read("LIESMICH.txt").decode()
    assert "Kopf 2: Name und Akzente – Sonnengelb" in readme  # the frog's belly
    assert "Kopf 4: Gesicht – Anthrazit" in readme
    assert "ZUSAMMENSETZEN" not in readme  # nothing to glue
    stem = figures.file_stem(model.config)
    assert stem == "myboxi-figur-frog-ida"
    assert {f"stl/{stem}-figur.stl", f"stl/{stem}-figur-gesicht.stl"} <= set(bundle.namelist())
    assert figures.title(model.config) == "Myboxi 3D-Figur Frosch „Ida“"


def test_one_colour_engraves_the_face() -> None:
    multi = build_figure(FigureConfig(motif="bear"))
    mono = build_figure(FigureConfig(motif="bear", colors="mono"))
    assert [e.tool for e in mono.extras] == [figures.TOOL_BASE]
    full = sum(e.solid.volume() for e in multi.extras)
    assert mono.extras[0].solid.volume() < full - 10.0  # eyes and nose are recessed


def test_a_larger_base_carries_a_larger_figure() -> None:
    small = union([e.solid for e in build_figure(FigureConfig(motif="cat")).extras])
    large = union([e.solid for e in build_figure(FigureConfig(motif="cat", size=50)).extras])
    assert bbox(large)[5] - bbox(large)[2] == pytest.approx(
        1.25 * (bbox(small)[5] - bbox(small)[2]), rel=0.01
    )


def test_colours_and_choices() -> None:
    assert FigureConfig().top == "figure"  # the page opens with a figure
    assert FigureConfig(motif="unicorn").color_key("accent") == "rosa"  # horn, mane, muzzle
    assert FigureConfig(top="flat").color_key("accent") == "creme"  # the shape's suggestion
    with pytest.raises(ValidationError, match="nur als Aufsteller"):
        FigureConfig(motif="drawing", drawing="0123456789abcdef")
    with pytest.raises(figures.FigureError, match="passt nicht vor diese Figur"):
        build_figure(FigureConfig(shape="heart", size=50, name="Maximilian"))

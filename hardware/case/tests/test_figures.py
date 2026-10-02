"""Figure bases ("Figur gestalten", docs/gehaeuse.md): printable without supports, the NFC chip
enclosed close to the reader, a print pause the slicer understands, child-safe sizes."""

from __future__ import annotations

import io
import itertools
import math
import zipfile
from xml.etree import ElementTree as ET

import pytest
from pydantic import ValidationError

from myboxi_case import export, figures, geom, motifs
from myboxi_case.checks import check_piece
from myboxi_case.figures import FigureConfig, FigureError, build_figure
from myboxi_case.geom import bbox, circle

SHAPES = ("round", "square", "heart", "star")


@pytest.mark.parametrize(
    ("shape", "size", "top", "tag"),
    list(itertools.product(SHAPES, (40, 50), ("flat", "bricks"), ("coin", "sticker"))),
)
def test_every_choice_prints_without_supports(shape: str, size: int, top: str, tag: str) -> None:
    for name in ("", "Lotta", "Maximilian"):
        cfg = FigureConfig.model_validate(
            {"shape": shape, "size": size, "top": top, "tag": tag, "name": name}
        )
        model = build_figure(cfg)
        assert check_piece(model.piece) == [], cfg.query()
        if top == "bricks":
            assert model.studs >= 2  # a minifigure needs two
        x0, y0, z0, x1, y1, _ = bbox(model.piece.solid)
        assert z0 == pytest.approx(0)
        # never small enough for the EN 71-1 small parts cylinder (31.7 mm)
        assert min(x1 - x0, y1 - y0) > figures.SMALL_PARTS


def test_the_chip_is_enclosed_close_to_the_bottom() -> None:
    model = build_figure(FigureConfig(top="flat"))
    px, py, r, z0, z1 = model.pocket
    assert z0 == figures.SKIN == 0.8  # close to the box's reader
    assert z1 == pytest.approx(2.2)  # coin 1.2 mm + 0.2 mm play
    assert r == pytest.approx(13.0)  # 25.6 mm + 2 x 0.2 mm play
    solid = model.piece.solid
    middle = solid.slice((z0 + z1) / 2)
    hole = model.outline.area() - middle.area()
    assert hole == pytest.approx(math.pi * r * r, rel=0.03)
    # closed below and above: no way in except the print pause
    for z in (0.4, z1 + 0.3):
        assert (circle(r - 0.5, px, py) - solid.slice(z)).area() < 1e-3


def test_the_print_pause_comes_before_the_first_layer_above_the_pocket() -> None:
    model = build_figure(FigureConfig(shape="heart", top="bricks", name="Mia"))
    data = figures.threemf(model)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        pause = ET.fromstring(zf.read("Metadata/custom_gcode_per_layer.xml"))  # noqa: S314
        ET.fromstring(zf.read("3D/3dmodel.model"))  # noqa: S314 - our own output
        settings = ET.fromstring(zf.read("Metadata/model_settings.config"))  # noqa: S314
    layer = pause.find("plate/layer")
    assert layer is not None
    assert float(layer.get("top_z", "0")) == pytest.approx(model.pause_z + 0.01)
    assert layer.get("type") == "1"  # PausePrint in Orca and Bambu Studio
    mode = pause.find("plate/mode")
    assert mode is not None
    assert mode.get("value") == "MultiExtruder"  # name in the second colour
    extruders = [m.get("value") for m in settings.iter("metadata") if m.get("key") == "extruder"]
    assert set(extruders) == {"1", "2"}
    mono = figures.threemf(build_figure(FigureConfig(colors="mono", name="Mia")))
    with zipfile.ZipFile(io.BytesIO(mono)) as zf:
        assert b"SingleExtruder" in zf.read("Metadata/custom_gcode_per_layer.xml")


def test_studs_follow_the_tolerance() -> None:
    tight = build_figure(FigureConfig(top="bricks", tolerance=0.1))
    loose = build_figure(FigureConfig(top="bricks", tolerance=0.4))
    assert tight.piece.solid.volume() > loose.piece.solid.volume()


def test_a_name_that_does_not_fit_is_refused() -> None:
    with pytest.raises(FigureError, match="passt nicht"):
        figures._place_label("Maximilian", circle(6.0), 40)  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(ValidationError):
        FigureConfig(name="Maximiliane")  # more than 10 characters
    with pytest.raises(ValidationError):
        FigureConfig(name="Lotta ☃")


def test_files_are_complete_and_reproducible() -> None:
    cfg = FigureConfig(shape="star", top="bricks", name="Ömer")
    a = figures.bundle_zip(build_figure(cfg), "https://app.myboxi.eu/gestalten/figur?shape=star")
    b = figures.bundle_zip(build_figure(cfg), "https://app.myboxi.eu/gestalten/figur?shape=star")
    assert a == b
    with zipfile.ZipFile(io.BytesIO(a)) as zf:
        names = set(zf.namelist())
        readme = zf.read("LIESMICH.txt").decode()
    stem = figures.file_stem(cfg)
    assert stem == "myboxi-figur-star-oemer"
    assert {
        f"{stem}.3mf",
        f"stl/{stem}.stl",
        f"stl/{stem}-details.stl",
        "konfiguration.json",
    } <= names
    assert "Druckpause bei 2,2 mm" in readme
    assert "NFC-Münze" in readme


def test_query_round_trip_and_digest() -> None:
    cfg = FigureConfig.from_query({"shape": "heart", "size": "50", "tolerance": "0.25"})
    assert cfg.query() == {"shape": "heart", "size": "50", "tolerance": "0.25"}
    assert FigureConfig.from_query(cfg.query()) == cfg
    assert cfg.digest() != FigureConfig().digest()
    assert cfg.color("base") == "#F2B8C6"  # suggested for hearts: rosa


def test_preview_uses_the_case_format() -> None:
    model = build_figure(FigureConfig(top="flat", name="Lotta"))
    info, meshes = export.read_preview(figures.preview(model))
    assert [m["key"] for m in info["meshes"]] == ["figure", "figure_inlay"]  # type: ignore[index]
    assert len(meshes) == 2
    assert figures.png(model)[:8] == b"\x89PNG\r\n\x1a\n"


# --- a figure from the collection, standing in the base ------------------------------------------


@pytest.mark.parametrize("motif", motifs.MOTIFS)
def test_every_figure_stands_in_every_base(motif: str) -> None:
    for shape, size in itertools.product(SHAPES, (40, 50)):
        cfg = FigureConfig.model_validate(
            {"shape": shape, "size": size, "top": "standee", "motif": motif, "name": "Lotta"}
        )
        model = build_figure(cfg)
        assert model.tile is not None
        for piece in model.pieces:
            assert check_piece(piece) == [], (cfg.query(), piece.key)
        # the tab sits in the slot, the figure rests on the base, nothing overlaps
        _, _, tz0, _, _, _ = bbox(model.tile.solid)
        assert tz0 == pytest.approx(figures.HEIGHT - figures.SLOT_DEPTH + 0.2, abs=0.05)
        assert (model.piece.solid ^ model.tile.solid).volume() < 1.0
        # printed lying flat, front up: no supports, at most 6 mm high
        flat = model.tile.printed(model.tile.solid)
        assert bbox(flat)[5] == pytest.approx(figures.TILE)


def test_figure_details_stay_inside_with_a_rim() -> None:
    for motif in motifs.MOTIFS:
        drawing = motifs.drawing(motif)
        assert (drawing.details - drawing.outline.offset(-0.99)).area() < 1e-3, motif
        assert len(drawing.outline.decompose()) == 1, motif
        x0, y0, x1, _ = geom.bounds(drawing.outline)
        assert y0 == pytest.approx(0.0)  # stands on the base
        assert x1 - x0 <= 40.0  # fits the 40 mm base


def test_a_figure_prints_with_four_colours_on_one_plate() -> None:
    model = build_figure(FigureConfig(top="standee", motif="unicorn", shape="heart", name="Mia"))
    with zipfile.ZipFile(io.BytesIO(figures.threemf(model))) as zf:
        settings = ET.fromstring(zf.read("Metadata/model_settings.config"))  # noqa: S314
        modelxml = zf.read("3D/3dmodel.model").decode()
    extruders = {m.get("value") for m in settings.iter("metadata") if m.get("key") == "extruder"}
    assert extruders == {"1", "2", "3", "4"}  # base, name, figure, face
    assert modelxml.count("<item ") == 2  # base and figure, side by side
    readme = zipfile.ZipFile(io.BytesIO(figures.bundle_zip(model))).read("LIESMICH.txt").decode()
    assert "ZUSAMMENSETZEN" in readme
    assert "Kopf 4 Gesicht" in readme
    assert figures.title(model.config) == "Myboxi Figur Einhorn „Mia“"


def test_the_figure_only_counts_for_standees() -> None:
    assert FigureConfig(top="flat", motif="cat", color_motif="rot") == FigureConfig(top="flat")
    assert FigureConfig(top="flat").query() == {"top": "flat"}
    assert FigureConfig(motif="cat").query() == {"motif": "cat"}  # a figure is the default
    unicorn = FigureConfig(top="standee", motif="unicorn")
    assert unicorn.color_key("motif") == "weiss"
    assert unicorn.color_key("details") == "anthrazit"  # a face stays visible on white

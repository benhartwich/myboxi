"""Sculpted snouts for the animal boxes (faces.py): printable lying on their back, not a small
part, in the window, with enough sound getting through."""

from __future__ import annotations

import io
import math
import zipfile
from xml.etree import ElementTree as ET

import pytest

from myboxi_case import export, faces
from myboxi_case.build import TOOL_BODY, TOOL_MUZZLE, build
from myboxi_case.characters import CHARACTERS, Character
from myboxi_case.checks import check, check_piece
from myboxi_case.config import CaseConfig
from myboxi_case.geom import bbox
from myboxi_case.layout import layout_for

SMALL_PARTS = 31.7  # EN 71-1 small parts cylinder (diameter)


@pytest.mark.parametrize("form", CHARACTERS)
def test_every_snout_prints_and_fits(form: str) -> None:
    cfg = CaseConfig.model_validate({"form": form, "name": "Mia"})
    model = build(cfg)
    assert check(model) == []
    snout = next(p for p in model.pieces if p.key == "snout")
    assert check_piece(snout) == []
    # not a small part: even its two smaller sides do not fit through the cylinder
    x0, y0, z0, x1, y1, z1 = bbox(snout.solid + snout.inlay)
    a, b, _ = sorted([x1 - x0, y1 - y0, z1 - z0])
    assert math.hypot(a, b) > SMALL_PARTS
    # inside the window of the front, standing out of it towards the viewer
    lay = layout_for(cfg)
    wx0, wx1 = lay.window_x
    assert wx0 + 1.0 < x0
    assert x1 < wx1 - 1.0
    assert z1 < lay.window_top - 1.0
    assert y0 < 0.0  # in front of the case


@pytest.mark.parametrize("form", CHARACTERS)
def test_sound_gets_through(form: Character) -> None:
    face = faces.face(form, 20.0)
    holes = (face.footprint - (face.snout + face.details).slice(0.1)).area()
    assert holes > 100.0  # mm² of holes in the snout
    assert face.opening.area() > 500.0  # the panel's opening behind it


def test_the_snout_has_its_own_head_and_colour() -> None:
    model = build(CaseConfig(form="bear", name="Mia"))
    snout = next(p for p in model.pieces if p.key == "snout")
    assert snout.tool == TOOL_MUZZLE
    assert snout.color == CaseConfig(form="bear").color("muzzle")  # creme
    data = export.bundle_zip(model)
    bundle = zipfile.ZipFile(io.BytesIO(data))
    readme = bundle.read("LIESMICH.txt").decode()
    assert "Kopf 4: Schnauze – Creme" in readme
    assert "Schnauze in die Vertiefung" in readme
    plate = next(n for n in bundle.namelist() if n.endswith(".3mf") and "/" not in n)
    settings = ET.fromstring(  # noqa: S314 - our own output
        zipfile.ZipFile(io.BytesIO(bundle.read(plate))).read("Metadata/model_settings.config")
    )
    tools = {m.get("value") for m in settings.iter("metadata") if m.get("key") == "extruder"}
    assert {"1", "2", "3", "4"} <= tools
    # one colour: the snout in the body's, nose and mouth engraved
    mono = build(CaseConfig(form="bear", colors="mono"))
    snout_mono = next(p for p in mono.pieces if p.key == "snout")
    assert snout_mono.tool == TOOL_BODY
    assert snout_mono.inlay.is_empty()


def test_plain_boxes_have_no_snout() -> None:
    for form in ("radio", "cube"):
        assert all(p.key != "snout" for p in build(CaseConfig(form=form)).pieces)

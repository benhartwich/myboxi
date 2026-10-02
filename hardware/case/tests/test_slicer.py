"""The ready project for the Snapmaker U1: the chosen colours reach the slicer (slicer.py)."""

from __future__ import annotations

import io
import json
import zipfile
from typing import Any

from myboxi_case import export, figures
from myboxi_case.build import build
from myboxi_case.config import PALETTE, CaseConfig
from myboxi_case.figures import FigureConfig, build_figure
from myboxi_case.slicer import U1_FOLDER, u1_project_settings


def _project(threemf: bytes) -> dict[str, Any] | None:
    with zipfile.ZipFile(io.BytesIO(threemf)) as zf:
        if "Metadata/project_settings.config" not in zf.namelist():
            return None
        return json.loads(zf.read("Metadata/project_settings.config"))


def test_the_project_names_the_system_presets_and_only_sets_colours() -> None:
    config = json.loads(u1_project_settings(["#2e6b5c", "#F2B8C6"]))
    assert config["printer_settings_id"] == "Snapmaker U1 (0.4 nozzle)"
    assert config["print_settings_id"] == "0.20mm Standard @Snapmaker U1 (0.4 nozzle)"
    assert config["filament_settings_id"] == ["Snapmaker PLA Basic @U1"] * 4
    # nothing differs from the system presets: Orca takes the installed (newer) values
    assert set(config["different_settings_to_system"]) == {""}
    assert config["filament_colour"] == ["#2E6B5C", "#F2B8C6", "#F2B8C6", "#F2B8C6"]


def test_a_figure_comes_with_its_colours_for_the_u1() -> None:
    cfg = FigureConfig(motif="unicorn", name="Mia", color_base="himmel")
    model = build_figure(cfg)
    bundle = zipfile.ZipFile(io.BytesIO(figures.bundle_zip(model)))
    stem = figures.file_stem(cfg)
    assert _project(bundle.read(f"{stem}.3mf")) is None  # neutral, for any printer
    project = _project(bundle.read(f"{U1_FOLDER}/{stem}.3mf"))
    assert project is not None
    expected = [PALETTE[k][1] for k in ("himmel", "rosa", "weiss", "anthrazit")]
    assert project["filament_colour"] == expected  # base, name and horn, figure, face
    readme = bundle.read("LIESMICH.txt").decode()
    assert "Kopf 2: Name und Akzente – Rosa" in readme
    assert f"{U1_FOLDER}/{stem}.3mf in Snapmaker Orca öffnen" in readme


def test_a_case_comes_with_its_colours_for_the_u1() -> None:
    cfg = CaseConfig(form="radio", name="Ida")
    model = build(cfg)
    bundle = zipfile.ZipFile(io.BytesIO(export.bundle_zip(model)))
    neutral = [n for n in bundle.namelist() if n.endswith(".3mf") and "/" not in n]
    u1 = [n for n in bundle.namelist() if n.startswith(f"{U1_FOLDER}/")]
    assert len(u1) == len(neutral) >= 1  # one project per plate
    for name in u1:
        project = _project(bundle.read(name))
        assert project is not None
        assert project["filament_colour"][:3] == export.head_colours(cfg)
    assert all(_project(bundle.read(name)) is None for name in neutral)

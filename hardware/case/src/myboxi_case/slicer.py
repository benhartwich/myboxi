"""A ready project for the Snapmaker U1 in Snapmaker Orca: the chosen colours per tool head.

Orca shows filament colours from a 3MF only when it carries a complete project configuration
(``Metadata/project_settings.config``); colours in the 3MF's materials only assign the heads.
A partial configuration is not an option: opened as a project, Orca fills the missing values
with its defaults and would replace the printer's settings.

``profiles/snapmaker-u1-project.json`` is that configuration as Snapmaker Orca 2.4.0 exports
it for the system presets "Snapmaker U1 (0.4 nozzle)", "0.20mm Standard @Snapmaker U1
(0.4 nozzle)" and four times "Snapmaker PLA Basic @U1" (``--export-3mf``). It lists no
setting as different from the system presets, so Orca takes every value from the presets
installed on the computer that opens it (newer profiles win), except the filament colours,
which are project options. The profile values come from Snapmaker Orca (AGPL-3.0, REUSE.toml).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from importlib.resources import files
from typing import Any

U1_HEADS = 4
U1_FOLDER = "snapmaker-u1"  # in the download: the projects for the U1


def u1_project_settings(colours: Sequence[str]) -> str:
    """``Metadata/project_settings.config`` with ``colours`` (#RRGGBB) for heads 1 to 4."""
    template = files("myboxi_case").joinpath("profiles", "snapmaker-u1-project.json")
    config: dict[str, Any] = json.loads(template.read_text(encoding="utf-8"))
    chosen = [c.upper() for c in colours[:U1_HEADS]]
    chosen += [chosen[-1] if chosen else "#FFFFFF"] * (U1_HEADS - len(chosen))
    config["filament_colour"] = chosen
    return json.dumps(config, indent=4, ensure_ascii=False) + "\n"

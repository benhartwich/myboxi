"""Figure bases, "Figur gestalten" (docs/gehaeuse.md): a base with an enclosed NFC chip turns any
figure into a box figure (glued on top), or carries studs for building bricks.

Printed upright, without supports. The chip pocket is closed: the 3MF pauses the print right
above it (Orca, Snapmaker Orca, Bambu Studio), the chip goes in, the print continues. The chip
sits 0.8 mm above the bottom, close to the box's reader. Sizes stay well above the EN 71-1 small
parts cylinder; tips and corners are rounded.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Literal, Self
from xml.sax.saxutils import escape, quoteattr

from manifold3d import CrossSection, Manifold
from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, field_validator

from myboxi_case import export, patterns, text
from myboxi_case.build import Piece
from myboxi_case.config import PALETTE, ColorKey, Colors
from myboxi_case.geom import (
    bbox,
    bounds,
    circle,
    cylinder_z,
    prism,
    rounded_rect,
    section_union,
    union,
)
from myboxi_case.render import Item, render

# Part of every digest. Bump on any change that alters generated geometry.
FIGURE_VERSION = "1"
MAX_LABEL = 10

Shape = Literal["round", "square", "heart", "star"]
Top = Literal["flat", "bricks"]
Tag = Literal["coin", "sticker"]

HEIGHT = 8.0
SKIN = 0.8  # below the chip: as close to the reader as the first layers allow
WALL = 2.0  # around the chip pocket
ENGRAVE = 0.6
EDGE = 1.2  # radius of the rounded top edge
ROUND = 2.0  # radius for tips and corners
STUD_PITCH = 8.0  # building bricks (system dimensions)
STUD_HEIGHT = 1.8
STUD_MARGIN = 1.6
LABEL_GAP = 1.5
SMALL_PARTS = 31.7  # EN 71-1 small parts cylinder (diameter)
PLATE_CENTRE = (135.0, 135.0)  # Snapmaker U1, 270 x 270

# diameter, thickness, what to buy
TAGS: dict[str, tuple[float, float, str]] = {
    "coin": (25.6, 1.2, "NFC-Münze NTAG213 oder NTAG215, Ø 25 mm, etwa 1 mm dick"),
    "sticker": (25.0, 0.3, "NFC-Aufkleber NTAG213 oder NTAG215, Ø 25 mm"),
}
SUGGESTED: dict[str, tuple[str, str]] = {
    "round": ("moos", "creme"),
    "square": ("himmel", "weiss"),
    "heart": ("rosa", "weiss"),
    "star": ("sonne", "anthrazit"),
}
TOOL_BASE, TOOL_ACCENT = 1, 2


class FigureError(ValueError):
    """A choice that does not fit (German, shown to the user)."""


def _squash(value: object) -> object:
    return " ".join(value.split()) if isinstance(value, str) else value


def _label(value: str) -> str:
    missing = text.supported(value)
    if missing:
        raise ValueError(f"Diese Zeichen gibt es in der Schrift nicht: {' '.join(sorted(missing))}")
    return value


class FigureConfig(BaseModel):
    """One figure base. Frozen and canonical: equal choices give equal files and digests."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    shape: Shape = "round"
    size: Literal[40, 50] = 40
    top: Top = "flat"
    name: Annotated[
        str, BeforeValidator(_squash), Field(max_length=MAX_LABEL), AfterValidator(_label)
    ] = ""
    tag: Tag = "coin"
    colors: Colors = "multi"
    color_base: ColorKey | None = None
    color_accent: ColorKey | None = None
    tolerance: Annotated[float, Field(ge=0.1, le=0.4)] = 0.2

    @field_validator("tolerance")
    @classmethod
    def _tolerance_steps(cls, value: float) -> float:
        return round(round(value / 0.05) * 0.05, 2)

    @classmethod
    def from_query(cls, query: Mapping[str, str]) -> Self:
        data: dict[str, object] = {}
        for key, value in query.items():
            if key == "size":
                data[key] = int(value) if value.isdigit() else value
            elif key == "tolerance":
                try:
                    data[key] = float(value)
                except ValueError:
                    data[key] = value
            else:
                data[key] = value
        return cls.model_validate(data)

    def query(self) -> dict[str, str]:
        default = FigureConfig()
        return {
            key: str(getattr(self, key))
            for key in type(self).model_fields
            if getattr(self, key) is not None and getattr(self, key) != getattr(default, key)
        }

    def canonical_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    def digest(self) -> str:
        data = f"figure {FIGURE_VERSION}\n{self.canonical_json()}".encode()
        return hashlib.sha256(data).hexdigest()

    def color_key(self, role: Literal["base", "accent"]) -> str:
        chosen = self.color_base if role == "base" else self.color_accent
        return chosen or SUGGESTED[self.shape][0 if role == "base" else 1]

    def color(self, role: Literal["base", "accent"]) -> str:
        return PALETTE[self.color_key(role)][1]


@dataclass(frozen=True)
class FigureModel:
    config: FigureConfig
    piece: Piece
    outline: CrossSection
    pocket: tuple[float, float, float, float, float]  # x, y, radius, z0, z1
    studs: int
    label: CrossSection

    @property
    def pause_z(self) -> float:
        """Top of the chip pocket: the print pauses before the first layer above it."""
        return self.pocket[4]


# --- geometry ----------------------------------------------------------------------------------


def _rounded(section: CrossSection, r: float = ROUND) -> CrossSection:
    """No sharp tips: an opening rounds every convex corner to ``r``."""
    return section.offset(-r).offset(r)


def _outline(shape: Shape, size: float, core: float) -> CrossSection:
    """The footprint, centred on the origin. ``core``: a circle that must fit (chip and wall)."""
    half = size / 2
    match shape:
        case "round":
            return circle(half)
        case "square":
            return rounded_rect(-half, -half, half, half, 6.0)
        case "heart":
            heart = patterns.heart(size * 1.25)
            return _rounded(section_union([heart, circle(core, 0, 1.0)]))
        case "star":
            star = patterns.star(half + 3.0, half * 0.62)
            return _rounded(section_union([star, circle(core)]))


def _inside(inner: CrossSection, outer: CrossSection) -> bool:
    return (inner - outer).area() < 1e-3


def _pocket_centre(outline: CrossSection, r: float) -> tuple[float, float]:
    """On the symmetry axis (x = 0), as close to the middle as the wall allows."""
    _, y0, _, y1 = bounds(outline)
    middle = (y0 + y1) / 2
    for step in range(int((y1 - y0) / 0.5)):
        for y in (middle + step * 0.5, middle - step * 0.5):
            if _inside(circle(r + WALL, 0, y), outline):
                return (0.0, y)
    raise FigureError("Der NFC-Chip passt nicht in diese Form. Bitte Größe 50 wählen.")


def _place_label(name: str, region: CrossSection, size: int) -> CrossSection:
    """The name on the top face, as far to the front as it fits."""
    if not name:
        return CrossSection()
    _, y0, _, _ = bounds(region)
    largest = 4.5 if size == 40 else 5.5
    cap = largest
    while cap >= 3.2 - 1e-9:
        section = text.outline(name, cap)
        _, ty0, _, _ = bounds(section)
        y = y0 + 0.2 - ty0
        while y <= 0.0:
            placed = section.translate((0, y))
            if _inside(placed, region):
                return placed
            y += 0.5
        cap -= 0.3
    raise FigureError(
        f"„{name}“ passt nicht auf diese Form. Bitte einen kürzeren Namen oder Größe 50 wählen."
    )


def _studs(region: CrossSection, keep_out: CrossSection, r: float) -> list[tuple[float, float]]:
    """A rectangular block of studs on the system grid, centred left to right: bricks need
    whole rows. Blocks at least two studs deep win (a brick holds on them), then the largest,
    then the squarer one, then the one nearest the middle."""
    allowed = region.offset(-(STUD_MARGIN + r))
    if not keep_out.is_empty():
        allowed = allowed - keep_out.offset(LABEL_GAP + r)
    x0, y0, x1, y1 = bounds(region)
    max_n = int((x1 - x0) / STUD_PITCH) + 1
    max_m = int((y1 - y0) / STUD_PITCH) + 1
    best: list[tuple[float, float]] = []
    best_key: tuple[bool, int, int, float] = (False, 0, 0, 0.0)
    for n in range(max_n, 0, -1):
        for m in range(max_m, 0, -1):
            if (min(n, m) >= 2, n * m) < best_key[:2]:
                continue
            xs = [(i - (n - 1) / 2) * STUD_PITCH for i in range(n)]
            half = (m - 1) / 2 * STUD_PITCH
            dy = 0.0
            while dy <= (y1 - y0) / 2:
                for cy in (dy, -dy) if dy else (0.0,):
                    centres = [(x, cy + (j * STUD_PITCH - half)) for j in range(m) for x in xs]
                    if all(_inside(circle(0.05, x, y), allowed) for x, y in centres):
                        key = (min(n, m) >= 2, n * m, -abs(n - m), -abs(cy))
                        if key > best_key:
                            best, best_key = centres, key
                dy += 0.5
    return best


def build_figure(cfg: FigureConfig) -> FigureModel:
    """Raises FigureError for choices that do not fit."""
    tag_d, tag_t, _ = TAGS[cfg.tag]
    pocket_r = tag_d / 2 + cfg.tolerance
    pocket_z1 = SKIN + tag_t + (0.2 if cfg.tag == "coin" else 0.1)
    outline = _outline(cfg.shape, cfg.size, pocket_r + WALL + 0.2)
    x0, y0, x1, y1 = bounds(outline)
    if min(x1 - x0, y1 - y0) <= SMALL_PARTS:  # never for the offered sizes; a guard for changes
        raise FigureError("Der Sockel wäre zu klein für Kinder.")
    px, py = _pocket_centre(outline, pocket_r)

    # A small chamfer at the bed (against the elephant foot) and a rounded top edge, built
    # from 0.2 mm steps, one per layer, so it prints smooth.
    steps = [prism(outline.offset(-0.4), 0.0, 0.4), prism(outline, 0.4, HEIGHT - EDGE)]
    z = HEIGHT - EDGE
    while z < HEIGHT - 1e-6:
        dz = min(z + 0.2, HEIGHT) - (HEIGHT - EDGE)
        inset = EDGE - math.sqrt(max(EDGE * EDGE - dz * dz, 0.0))
        steps.append(prism(outline.offset(-inset), z, min(z + 0.2, HEIGHT)))
        z += 0.2
    body = union(steps)
    pocket = cylinder_z(pocket_r, px, py, SKIN, pocket_z1)
    top_face = outline.offset(-2.4)
    label = _place_label(cfg.name, top_face, cfg.size)
    studs: list[tuple[float, float]] = []
    if cfg.top == "bricks":
        stud_r = (5.0 - cfg.tolerance) / 2  # 4.8 mm at the default tolerance
        studs = _studs(top_face, label, stud_r)
        if not studs:
            raise FigureError("Auf diese Form passen keine Noppen. Bitte Größe 50 wählen.")
        body = union(
            [
                body,
                *(cylinder_z(stud_r, x, y, HEIGHT - 0.01, HEIGHT + STUD_HEIGHT) for x, y in studs),
            ]
        )
    solid = body - pocket
    inlay = Manifold()
    if not label.is_empty():
        cut = prism(label, HEIGHT - ENGRAVE, HEIGHT + 0.01)
        solid = solid - cut
        if cfg.colors == "multi":
            inlay = prism(label, HEIGHT - ENGRAVE, HEIGHT)
    piece = Piece(
        key="figure",
        label="Figurensockel",
        solid=solid,
        color=cfg.color("base"),
        tool=TOOL_BASE,
        rotation=(0, 0, 0),
        explode=(0, 0, 0),
        inlay=inlay,
        inlay_color=cfg.color("accent"),
        inlay_tool=TOOL_ACCENT,
    )
    return FigureModel(
        cfg, piece, outline, (px, py, pocket_r, SKIN, round(pocket_z1, 2)), len(studs), label
    )


# --- files -------------------------------------------------------------------------------------

SHAPE_LABELS = {"round": "rund", "square": "eckig", "heart": "Herz", "star": "Stern"}


def title(cfg: FigureConfig) -> str:
    name = f" „{cfg.name}“" if cfg.name else ""
    return f"Myboxi Figurensockel {SHAPE_LABELS[cfg.shape]}{name}"


def file_stem(cfg: FigureConfig) -> str:
    table = str.maketrans(
        {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"}
    )
    name = cfg.name.translate(table).lower()
    slug = "".join(ch if ch.isascii() and ch.isalnum() else "-" for ch in name).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return "-".join(part for part in ("myboxi-figur", cfg.shape, slug) if part)


def _centred(model: FigureModel, m: Manifold) -> Manifold:
    x0, y0, _, x1, y1, _ = bbox(model.piece.solid)
    return m.translate((PLATE_CENTRE[0] - (x0 + x1) / 2, PLATE_CENTRE[1] - (y0 + y1) / 2, 0))


def pause_xml(model: FigureModel, multi: bool) -> str:
    """Orca / Bambu Studio "custom G-code per layer": pause (type 1) before the first layer
    above the pocket, whatever the layer height."""
    mode = "MultiExtruder" if multi else "SingleExtruder"
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<custom_gcodes_per_layer>\n<plate>\n'
        '<plate_info id="1"/>\n'
        f'<layer top_z="{model.pause_z + 0.01:.2f}" type="1" extruder="1" color="" '
        'extra="NFC-Chip einlegen" gcode="M601"/>\n'
        f'<mode value="{mode}"/>\n</plate>\n</custom_gcodes_per_layer>\n'
    )


def threemf(model: FigureModel) -> bytes:
    p = model.piece
    multi = not p.inlay.is_empty()
    members = [(p.label, _centred(model, p.solid), p.color, p.tool)]
    if multi:
        members.append((f"{p.label} Name", _centred(model, p.inlay), p.inlay_color, p.inlay_tool))
    colors = list(dict.fromkeys(color for _, _, color, _ in members))
    objects = "".join(
        f'<object id="{i + 2}" type="model" name={quoteattr(label)} pid="1" '
        f'pindex="{colors.index(color)}">{export._mesh_xml(solid)}</object>'  # pyright: ignore[reportPrivateUsage]
        for i, (label, solid, color, _) in enumerate(members)
    )
    group = len(members) + 2
    comps = "".join(f'<component objectid="{i + 2}"/>' for i in range(len(members)))
    parts = "".join(
        f'<part id="{i + 2}" subtype="normal_part"><metadata key="name" value={quoteattr(label)}/>'
        f'<metadata key="extruder" value="{tool}"/></part>'
        for i, (label, _, _, tool) in enumerate(members)
    )
    bases = "".join(f'<base name="{escape(c)}" displaycolor="{c}FF"/>' for c in colors)
    model_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<model unit="millimeter" xml:lang="de-DE" '
        'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
        f'<metadata name="Title">{escape(title(model.config))}</metadata>'
        '<metadata name="Designer">Myboxi (myboxi.eu)</metadata>'
        '<metadata name="LicenseTerms">CC BY-SA 4.0</metadata>'
        f'<resources><basematerials id="1">{bases}</basematerials>{objects}'
        f'<object id="{group}" type="model" name={quoteattr(p.label)}>'
        f"<components>{comps}</components></object></resources>"
        f'<build><item objectid="{group}"/></build></model>'
    )
    settings = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<config>'
        f'<object id="{group}"><metadata key="name" value={quoteattr(p.label)}/>'
        f'<metadata key="extruder" value="{p.tool}"/>{parts}</object></config>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="model" '
        'ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
        '<Default Extension="config" ContentType="text/xml"/>'
        '<Default Extension="xml" ContentType="text/xml"/></Types>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
        'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>'
    )
    return export._zip(  # pyright: ignore[reportPrivateUsage]
        [
            ("[Content_Types].xml", content_types),
            ("_rels/.rels", rels),
            ("3D/3dmodel.model", model_xml),
            ("Metadata/model_settings.config", settings),
            ("Metadata/custom_gcode_per_layer.xml", pause_xml(model, multi)),
        ]
    )


def _readme(model: FigureModel, files: list[str], url: str | None) -> str:
    cfg = model.config
    multi = not model.piece.inlay.is_empty()
    pause = f"{model.pause_z:.1f}".replace(".", ",")
    lines = [
        title(cfg),
        "=" * len(title(cfg)),
        "",
        "Druckvorlage für einen Myboxi-Figurensockel, erzeugt mit „Figur gestalten“"
        + (f":\n{url}" if url else "."),
        "",
        "DATEIEN",
        *[f"  {name}" for name in files],
        "",
        "DU BRAUCHST",
        f"  {TAGS[cfg.tag][2]}",
        "  Filament: PLA oder PETG"
        + (
            f", zwei Farben ({PALETTE[cfg.color_key('base')][0]} und "
            f"{PALETTE[cfg.color_key('accent')][0]})"
            if multi
            else ""
        ),
        *(
            ["  Für eine aufgeklebte Figur: 2K-Kleber oder Sekundenkleber (Gel)"]
            if cfg.top == "flat"
            else []
        ),
        "",
        "DRUCKEN",
        "  Die 3MF öffnen (Orca Slicer, Snapmaker Orca, Bambu Studio), Schichthöhe 0,2 mm,",
        "  Füllung 20 %, keine Stützen. Der Sockel liegt richtig herum auf der Platte.",
        *(["  Snapmaker U1: Kopf 1 Sockel, Kopf 2 Name."] if multi else []),
        f"  Druckpause bei {pause} mm: Sie steckt schon in der 3MF. Wenn der Drucker anhält,",
        "  den NFC-Chip flach in die runde Vertiefung legen (Schrift egal) und fortsetzen.",
        "  Mit STL statt 3MF: die Pause im Slicer selbst setzen, auf die erste Schicht über",
        f"  {pause} mm.",
        "  Tipp: Den Chip vorher in der Myboxi-App unter „Figuren“ mit dem Handy scannen,",
        "  oder später einfach auf die Box legen und unter „Unbekannte Figuren“ übernehmen.",
        "",
        "SICHERHEIT",
        "  Der Chip ist ganz eingeschlossen. Den Sockel nur verwenden, wenn er heil ist; eine",
        "  aufgeklebte Figur muss fest sitzen. Für Kinder unter 3 Jahren nur mit Figuren, die",
        "  selbst für dieses Alter geeignet sind. Keine Magnete einbauen.",
        "",
        "LIZENZ",
        "  Erzeugte Druckdateien: CC BY-SA 4.0 (Myboxi, myboxi.eu).",
        "",
    ]
    return "\n".join(lines)


def bundle_zip(model: FigureModel, url: str | None = None) -> bytes:
    stem = file_stem(model.config)
    entries: list[tuple[str, str | bytes]] = [(f"{stem}.3mf", threemf(model))]
    p = model.piece
    entries.append((f"stl/{stem}.stl", export.stl(p.solid, p.label)))
    if not p.inlay.is_empty():
        entries.append((f"stl/{stem}-name.stl", export.stl(p.inlay, f"{p.label} Name")))
    config: dict[str, object] = {
        "generator": f"figure {FIGURE_VERSION}",
        "digest": model.config.digest(),
        "config": model.config.model_dump(mode="json"),
        "pause_mm": model.pause_z,
    }
    if url:
        config["url"] = url
    entries.append(("konfiguration.json", json.dumps(config, indent=2, ensure_ascii=False) + "\n"))
    files = [name for name, _ in entries]
    entries.insert(0, ("LIESMICH.txt", _readme(model, files, url)))
    return export._zip(entries)  # pyright: ignore[reportPrivateUsage]


def preview(model: FigureModel) -> bytes:
    """Same format as the case preview (export.preview), so the page can reuse the viewer."""
    p = model.piece
    meshes: list[tuple[dict[str, object], Manifold]] = [
        (
            {
                "key": "figure",
                "label": p.label,
                "kind": "part",
                "color": p.color,
                "explode": (0, 0, 0),
            },
            p.solid,
        ),
    ]
    if not p.inlay.is_empty():
        meshes.append(
            (
                {
                    "key": "figure_inlay",
                    "label": p.label,
                    "kind": "inlay",
                    "color": p.inlay_color,
                    "explode": (0, 0, 0),
                },
                p.inlay,
            )
        )
    x0, y0, _, x1, y1, z1 = bbox(p.solid)
    moved = [(meta, m.translate((-x0, -y0, 0))) for meta, m in meshes]
    return export.encode_preview(moved, (x1 - x0, y1 - y0, z1), f"figure {FIGURE_VERSION}")


def png(model: FigureModel, size: tuple[int, int] = (480, 360)) -> bytes:
    p = model.piece
    items = [Item(p.solid, p.color)]
    if not p.inlay.is_empty():
        items.append(Item(p.inlay, p.inlay_color))
    return render(items, size=size, yaw=-25.0, pitch=35.0)

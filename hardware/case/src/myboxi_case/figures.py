"""Figure bases, "Figur gestalten" (docs/gehaeuse.md): a base with an enclosed NFC chip turns any
figure into a box figure. It carries a round figure printed with it in one piece (figures3d), a
flat figure in a slot, studs for building bricks, or nothing (glue a figure on top).

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
from typing import Annotated, Literal, Self, cast
from xml.sax.saxutils import escape, quoteattr

from manifold3d import CrossSection, Manifold
from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from myboxi_case import export, figures3d, motifs, patterns, text, trace
from myboxi_case.build import Piece
from myboxi_case.config import PALETTE, ColorKey, Colors
from myboxi_case.geom import (
    bbox,
    bounds,
    box,
    circle,
    cylinder_z,
    prism,
    rect,
    rounded_rect,
    section_union,
    union,
)
from myboxi_case.motifs import Motif
from myboxi_case.render import Item, render
from myboxi_case.slicer import U1_FOLDER, u1_project_settings

# Part of every digest. Bump on any change that alters generated geometry.
FIGURE_VERSION = "4"
MAX_LABEL = 10

Shape = Literal["round", "square", "heart", "star"]
Top = Literal["figure", "standee", "flat", "bricks"]
FIGURE_TOPS: tuple[str, ...] = ("figure", "standee")  # carry a figure from the collection
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
# Suggested base and accent per shape: white bases, like toy figures on the box.
SUGGESTED: dict[str, tuple[str, str]] = {
    "round": ("weiss", "anthrazit"),
    "square": ("weiss", "himmel"),
    "heart": ("weiss", "rosa"),
    "star": ("weiss", "sonne"),
}
TOOL_BASE, TOOL_ACCENT, TOOL_MOTIF, TOOL_DETAILS = 1, 2, 3, 4
TILE = 6.0  # thickness of the standing figure
TAB_WIDTH = 20.0  # the tab under the figure that goes into the base's slot
SLOT_DEPTH = 5.0
SLOT_Y = 2.0  # the figure stands just behind the middle of the base
FIGURE_Y = 2.0  # a round figure: where its feet start, behind the name
FIGURE_SCALE = {40: 1.0, 50: 1.15}  # the larger base: a larger figure and room for a name
FIGURE_SHRINK = (1.0, 0.93, 0.86)


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
    top: Top = "figure"  # the page is "Figur gestalten": a figure first
    # The standing figure (top == "standee"); otherwise dropped, so equal bases stay equal.
    motif: Motif | Literal["drawing"] = "bear"
    # motif == "drawing": the stored strokes (id from the upload) and how to turn them
    drawing: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{16}$")] | None = None
    turn: Literal[0, 90, 180, 270] = 0
    name: Annotated[
        str, BeforeValidator(_squash), Field(max_length=MAX_LABEL), AfterValidator(_label)
    ] = ""
    tag: Tag = "coin"
    colors: Colors = "multi"
    color_base: ColorKey | None = None
    color_accent: ColorKey | None = None
    color_motif: ColorKey | None = None
    color_details: ColorKey | None = None
    tolerance: Annotated[float, Field(ge=0.1, le=0.4)] = 0.2

    @model_validator(mode="before")
    @classmethod
    def _standee_only(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        values = cast(dict[str, object], data)
        top = values.get("top", "figure")
        if top not in FIGURE_TOPS:
            dropped = ("motif", "color_motif", "color_details", "drawing", "turn")
            return {k: v for k, v in values.items() if k not in dropped}
        if values.get("motif", "bear") != "drawing":
            return {k: v for k, v in values.items() if k not in ("drawing", "turn")}
        if top != "standee":
            raise ValueError("Eine eigene Zeichnung gibt es nur als Aufsteller.")
        return values

    @field_validator("tolerance")
    @classmethod
    def _tolerance_steps(cls, value: float) -> float:
        return round(round(value / 0.05) * 0.05, 2)

    @classmethod
    def from_query(cls, query: Mapping[str, str]) -> Self:
        data: dict[str, object] = {}
        for key, value in query.items():
            if key in ("size", "turn"):
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

    def color_key(self, role: Literal["base", "accent", "motif", "details"]) -> str:
        if role == "motif":
            return self.color_motif or motifs.COLOURS[self.motif]
        if role == "details":  # the figure's face
            return self.color_details or "anthrazit"
        if role == "accent" and self.color_accent is None and self.top in FIGURE_TOPS:
            # a round figure's clothes: suggested per figure
            return motifs.ACCENTS.get(self.motif) or SUGGESTED[self.shape][1]
        chosen = self.color_base if role == "base" else self.color_accent
        return chosen or SUGGESTED[self.shape][0 if role == "base" else 1]

    def color(self, role: Literal["base", "accent", "motif", "details"]) -> str:
        return PALETTE[self.color_key(role)][1]


@dataclass(frozen=True)
class Extra:
    """A part printed together with the base, in its frame (the round figure's colours)."""

    key: str
    label: str
    solid: Manifold
    color: str
    tool: int


@dataclass(frozen=True)
class FigureModel:
    config: FigureConfig
    piece: Piece
    outline: CrossSection
    pocket: tuple[float, float, float, float, float]  # x, y, radius, z0, z1
    studs: int
    label: CrossSection
    tile: Piece | None = None  # the standing figure, printed lying flat
    extras: tuple[Extra, ...] = ()  # the round figure, printed on the base in one piece

    @property
    def pieces(self) -> tuple[Piece, ...]:
        return (self.piece,) if self.tile is None else (self.piece, self.tile)

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


def _rounded_slab(section: CrossSection, height: float) -> Manifold:
    """``section`` from z=0 to ``height``: a small chamfer at the bed (against the elephant
    foot) and a rounded top edge from 0.2 mm steps, one per layer, so it prints smooth."""
    steps = [prism(section.offset(-0.4), 0.0, 0.4), prism(section, 0.4, height - EDGE)]
    z = height - EDGE
    while z < height - 1e-6:
        top = min(z + 0.2, height)
        dz = top - (height - EDGE)
        inset = EDGE - math.sqrt(max(EDGE * EDGE - dz * dz, 0.0))
        steps.append(prism(section.offset(-inset), z, top))
        z += 0.2
    return union(steps)


def _slot_y(top_face: CrossSection, label: CrossSection, half_w: float, half_t: float) -> float:
    """Where the figure stands: just behind the middle, behind the name."""
    for step in range(40):
        y = SLOT_Y + step * 0.5
        slot = rect(-half_w - 1.0, y - half_t - 1.0, half_w + 1.0, y + half_t + 1.0)
        clear = label.is_empty() or (slot ^ label.offset(LABEL_GAP)).area() < 1e-3
        if clear and _inside(slot, top_face):
            return y
    raise FigureError(
        "Für die Figur ist auf diesem Sockel kein Platz. Bitte eine andere Form wählen."
    )


def _figure_parts(figure: figures3d.Figure3D) -> list[Manifold]:
    return [figure.body, figure.clothes, figure.white, figure.details]


def _figure_place(
    footprint: CrossSection, top_face: CrossSection, name: str, size: int
) -> tuple[float, CrossSection]:
    """Where a round figure stands (its feet on the flat top) and its name: the figure as far
    to the front as the name in front of it allows."""
    fits = False
    for step in range(40):
        y = FIGURE_Y + (step - 10) * 0.5
        feet = footprint.translate((0.0, y))
        if not _inside(feet.offset(0.5), top_face):
            continue
        fits = True
        front = y + bounds(footprint)[1] - LABEL_GAP
        region = top_face ^ rect(-60.0, -60.0, 60.0, front)
        if name and region.is_empty():
            continue  # no room in front: further back
        try:
            return y, _place_label(name, region, size)
        except FigureError:
            continue
    if fits:
        other = "Größe 50" if size == 40 else "die Form rund oder eckig"
        raise FigureError(
            f"„{name}“ passt nicht vor diese Figur. Bitte einen kürzeren Namen oder {other} wählen."
        )
    raise FigureError(
        "Diese Figur passt nicht auf den Sockel. Bitte Größe 50 oder eine andere Form wählen."
    )


def _tile(cfg: FigureConfig, strokes: trace.Rings | None) -> tuple[Manifold, Manifold, Manifold]:
    """The standing figure in print orientation (lying on its back, front up): solid, details
    as inlay (two colours) and the cut for them."""
    if cfg.motif == "drawing":
        if not strokes:
            raise FigureError("Bitte zuerst eine Zeichnung hochladen.")
        try:
            drawing = trace.drawing(strokes, cfg.turn)
        except trace.TraceError as exc:
            raise FigureError(str(exc)) from exc
    else:
        drawing = motifs.drawing(cfg.motif)
    tab = rect(-TAB_WIDTH / 2, -(SLOT_DEPTH - 0.2), TAB_WIDTH / 2, 1.0)
    outline = section_union([drawing.outline, tab])
    solid = _rounded_slab(outline, TILE)
    cut = prism(drawing.details, TILE - ENGRAVE, TILE + 0.01)
    inlay = prism(drawing.details, TILE - ENGRAVE, TILE) if cfg.colors == "multi" else Manifold()
    return solid - cut, inlay, cut


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


def build_figure(cfg: FigureConfig, strokes: trace.Rings | None = None) -> FigureModel:
    """Raises FigureError for choices that do not fit. ``strokes``: the uploaded drawing
    (trace.ink) when ``cfg.motif == "drawing"``."""
    tag_d, tag_t, _ = TAGS[cfg.tag]
    pocket_r = tag_d / 2 + cfg.tolerance
    pocket_z1 = SKIN + tag_t + (0.2 if cfg.tag == "coin" else 0.1)
    outline = _outline(cfg.shape, cfg.size, pocket_r + WALL + 0.2)
    x0, y0, x1, y1 = bounds(outline)
    if min(x1 - x0, y1 - y0) <= SMALL_PARTS:  # never for the offered sizes; a guard for changes
        raise FigureError("Der Sockel wäre zu klein für Kinder.")
    px, py = _pocket_centre(outline, pocket_r)

    body = _rounded_slab(outline, HEIGHT)
    pocket = cylinder_z(pocket_r, px, py, SKIN, pocket_z1)
    top_face = outline.offset(-2.4)
    label_region = top_face
    figure: figures3d.Figure3D | None = None
    footprint = CrossSection()
    label = CrossSection()
    y_fig = FIGURE_Y
    if cfg.top == "figure" and cfg.motif != "drawing":  # a drawing only stands flat
        # A little smaller where the name would not fit otherwise (a star's narrow front).
        for shrink in FIGURE_SHRINK:
            figure = figures3d.figure(cfg.motif, FIGURE_SCALE[cfg.size] * shrink)
            footprint = union(_figure_parts(figure)).slice(0.1)
            try:
                y_fig, label = _figure_place(footprint, top_face, cfg.name, cfg.size)
                break
            except FigureError:
                if shrink == FIGURE_SHRINK[-1]:
                    raise
    if cfg.top == "standee":
        # the name in front of the figure: its slot starts just behind the middle
        front = SLOT_Y - (TILE / 2 + cfg.tolerance) - 1.0 - LABEL_GAP
        label_region = top_face ^ rect(-60.0, -60.0, 60.0, front)
    if figure is None:
        label = _place_label(cfg.name, label_region, cfg.size)
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
    extras: tuple[Extra, ...] = ()
    if figure is not None:
        title_name = motifs.LABELS[cfg.motif]

        def place(m: Manifold) -> Manifold:
            return m.translate((0.0, y_fig, HEIGHT))

        if cfg.colors == "multi":
            parts = (
                ("figure3d", f"Figur {title_name}", figure.body, cfg.color("motif"), TOOL_MOTIF),
                ("figure3d_clothes", f"Figur {title_name} Kleidung", figure.clothes,
                 cfg.color("accent"), TOOL_ACCENT),
                ("figure3d_white", f"Figur {title_name} Hell", figure.white,
                 cfg.color("base"), TOOL_BASE),
                ("figure3d_details", f"Figur {title_name} Gesicht", figure.details,
                 cfg.color("details"), TOOL_DETAILS),
            )  # fmt: skip
        else:  # one filament: the face is engraved, the figure has the base's colour
            parts = (
                ("figure3d", f"Figur {title_name}", figure.body + figure.clothes + figure.white,
                 cfg.color("base"), TOOL_BASE),
            )  # fmt: skip
        extras = tuple(
            Extra(key, label_text, place(m), colour, tool)
            for key, label_text, m, colour, tool in parts
            if not m.is_empty()
        )
    tile: Piece | None = None
    if cfg.top == "standee":
        play = cfg.tolerance
        half_w, half_t = TAB_WIDTH / 2 + play, TILE / 2 + play
        y_slot = _slot_y(top_face, label, half_w, half_t)
        body = body - box(-half_w, y_slot - half_t, HEIGHT - SLOT_DEPTH,
                          half_w, y_slot + half_t, HEIGHT + 0.01)  # fmt: skip
        flat, details, _ = _tile(cfg, strokes)

        # Standing in the slot: rotate((90, 0, 0)) turns y (up in the drawing) into z and the
        # front (z = TILE) towards the viewer (-y).
        def stand(m: Manifold) -> Manifold:
            return m.rotate((90, 0, 0)).translate((0, y_slot + TILE / 2, HEIGHT))

        tile = Piece(
            key="figure_tile",
            label=f"Figur {motifs.LABELS[cfg.motif]}",
            solid=stand(flat),
            color=cfg.color("motif"),
            tool=TOOL_MOTIF,
            rotation=(-90, 0, 0),
            explode=(0, 0, 0),
            inlay=stand(details) if not details.is_empty() else Manifold(),
            inlay_color=cfg.color("details"),
            inlay_tool=TOOL_DETAILS,
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
        # With a figure the name is dark like its face (readable on a white base).
        inlay_color=cfg.color("details" if cfg.top in FIGURE_TOPS else "accent"),
        inlay_tool=TOOL_DETAILS if cfg.top in FIGURE_TOPS else TOOL_ACCENT,
    )
    return FigureModel(
        cfg, piece, outline, (px, py, pocket_r, SKIN, round(pocket_z1, 2)), len(studs), label,
        tile, extras,
    )  # fmt: skip


# --- files -------------------------------------------------------------------------------------

SHAPE_LABELS = {"round": "rund", "square": "eckig", "heart": "Herz", "star": "Stern"}


def title(cfg: FigureConfig) -> str:
    name = f" „{cfg.name}“" if cfg.name else ""
    if cfg.top == "standee" and cfg.motif == "drawing":
        return f"Myboxi Figur aus einer Zeichnung{name}"
    if cfg.top == "standee":
        return f"Myboxi Figur {motifs.LABELS[cfg.motif]}{name}"
    if cfg.top == "figure":
        return f"Myboxi 3D-Figur {motifs.LABELS[cfg.motif]}{name}"
    return f"Myboxi Figurensockel {SHAPE_LABELS[cfg.shape]}{name}"


def file_stem(cfg: FigureConfig) -> str:
    table = str.maketrans(
        {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"}
    )
    name = cfg.name.translate(table).lower()
    slug = "".join(ch if ch.isascii() and ch.isalnum() else "-" for ch in name).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    kind = cfg.motif if cfg.top in FIGURE_TOPS else cfg.shape
    return "-".join(part for part in ("myboxi-figur", kind, slug) if part)


GAP = 8.0


Member = tuple[str, Manifold, str, int]  # label, mesh, colour, tool


def _members(model: FigureModel, piece: Piece) -> list[Member]:
    """The parts of one printed object, in print orientation: the piece, its inlay and, for
    the base, a round figure printed with it."""
    members: list[Member] = [(piece.label, piece.printed(piece.solid), piece.color, piece.tool)]
    if not piece.inlay.is_empty():
        members.append(
            (f"{piece.label} Details", piece.printed(piece.inlay), piece.inlay_color,
             piece.inlay_tool)
        )  # fmt: skip
    if piece is model.piece:
        members += [(e.label, piece.printed(e.solid), e.color, e.tool) for e in model.extras]
    return members


def _plate(model: FigureModel) -> list[tuple[Piece, list[Member]]]:
    """Every piece in print orientation, side by side around the plate centre."""
    printed = [(p, _members(model, p)) for p in model.pieces]
    boxes = [bbox(union([m for _, m, _, _ in members])) for _, members in printed]
    widths = [b[3] - b[0] for b in boxes]
    x = PLATE_CENTRE[0] - (sum(widths) + GAP * (len(widths) - 1)) / 2
    placed: list[tuple[Piece, list[Member]]] = []
    for (piece, members), b, width in zip(printed, boxes, widths, strict=True):
        shift = (x - b[0], PLATE_CENTRE[1] - (b[1] + b[4]) / 2, 0.0)
        placed.append((piece, [(lb, m.translate(shift), c, t) for lb, m, c, t in members]))
        x += width + GAP
    return placed


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


def _multi(model: FigureModel) -> bool:
    return model.config.colors == "multi" and (
        any(not p.inlay.is_empty() or p.tool != TOOL_BASE for p in model.pieces)
        or any(e.tool != TOOL_BASE for e in model.extras)
    )


def threemf(model: FigureModel, *, u1: bool = False) -> bytes:
    """One plate: the base (with the pause for the chip) and, for a standee, the figure lying
    next to it. Parts carry their tool head for Orca and the Snapmaker U1."""
    colors: list[str] = []
    objects: list[str] = []
    builds: list[str] = []
    config: list[str] = []
    next_id = 2  # id 1: base materials
    for piece, members in _plate(model):
        ids: list[int] = []
        for label, mesh, color, _ in members:
            if color not in colors:
                colors.append(color)
            objects.append(
                f'<object id="{next_id}" type="model" name={quoteattr(label)} pid="1" '
                f'pindex="{colors.index(color)}">{export._mesh_xml(mesh)}</object>'  # pyright: ignore[reportPrivateUsage]
            )
            ids.append(next_id)
            next_id += 1
        comps = "".join(f'<component objectid="{i}"/>' for i in ids)
        objects.append(
            f'<object id="{next_id}" type="model" name={quoteattr(piece.label)}>'
            f"<components>{comps}</components></object>"
        )
        builds.append(f'<item objectid="{next_id}"/>')
        parts = "".join(
            f'<part id="{i}" subtype="normal_part"><metadata key="name" value={quoteattr(label)}/>'
            f'<metadata key="extruder" value="{tool}"/></part>'
            for i, (label, _, _, tool) in zip(ids, members, strict=True)
        )
        config.append(
            f'<object id="{next_id}"><metadata key="name" value={quoteattr(piece.label)}/>'
            f'<metadata key="extruder" value="{piece.tool}"/>{parts}</object>'
        )
        next_id += 1
    bases = "".join(f'<base name="{escape(c)}" displaycolor="{c}FF"/>' for c in colors)
    model_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<model unit="millimeter" xml:lang="de-DE" '
        'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
        f'<metadata name="Title">{escape(title(model.config))}</metadata>'
        '<metadata name="Designer">Myboxi (myboxi.eu)</metadata>'
        '<metadata name="LicenseTerms">CC BY-SA 4.0</metadata>'
        f'<resources><basematerials id="1">{bases}</basematerials>{"".join(objects)}</resources>'
        f"<build>{''.join(builds)}</build></model>"
    )
    settings = f'<?xml version="1.0" encoding="UTF-8"?>\n<config>{"".join(config)}</config>'
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
            ("Metadata/custom_gcode_per_layer.xml", pause_xml(model, _multi(model))),
            *(
                [("Metadata/project_settings.config", u1_project_settings(_head_colours(model)))]
                if u1
                else []
            ),
        ]
    )


def _colours(model: FigureModel) -> str:
    cfg = model.config
    roles = ["base", "accent"] + (["motif", "details"] if cfg.top in FIGURE_TOPS else [])
    if cfg.top == "figure" and cfg.colors != "multi":
        roles = ["base"]
    names = list(dict.fromkeys(PALETTE[cfg.color_key(r)][0] for r in roles))  # type: ignore[arg-type]
    return ", ".join(names[:-1]) + " und " + names[-1] if len(names) > 1 else names[0]


def _heads(model: FigureModel) -> list[tuple[int, str, str]]:
    """Which tool head prints what, and in which colour (palette key)."""
    cfg = model.config
    if cfg.top == "figure":
        return [
            (1, "Sockel und Helles (Schnauze, Stern oder Herz)", cfg.color_key("base")),
            (2, "Kleidung", cfg.color_key("accent")),
            (3, "Figur", cfg.color_key("motif")),
            (4, "Gesicht, Schuhe und Name", cfg.color_key("details")),
        ]
    if cfg.top == "standee":
        return [
            (1, "Sockel", cfg.color_key("base")),
            (3, "Figur", cfg.color_key("motif")),
            (4, "Gesicht und Name", cfg.color_key("details")),
        ]
    return [(1, "Sockel", cfg.color_key("base")), (2, "Name", cfg.color_key("accent"))]


def _head_colours(model: FigureModel) -> list[str]:
    """Heads 1 to 4, for the U1 project (an unused head keeps a neighbour's colour)."""
    by_head = {head: PALETTE[key][1] for head, _, key in _heads(model)}
    first = by_head[1]
    return [by_head.get(h, first) for h in range(1, 5)]


def _readme(model: FigureModel, files: list[str], url: str | None) -> str:
    cfg = model.config
    multi = _multi(model)
    pause = f"{model.pause_z:.1f}".replace(".", ",")
    standee = cfg.top == "standee"
    round_figure = cfg.top == "figure"
    stem = file_stem(cfg)
    heads = [f"    Kopf {i}: {what} – {PALETTE[key][0]}" for i, what, key in _heads(model)]
    lines = [
        title(cfg),
        "=" * len(title(cfg)),
        "",
        "Druckvorlage für Myboxi, erzeugt mit „Figur gestalten“" + (f":\n{url}" if url else "."),
        "",
        "DATEIEN",
        *[f"  {name}" for name in files],
        "",
        "DU BRAUCHST",
        f"  {TAGS[cfg.tag][2]}",
        "  Filament: PLA oder PETG" + (f" ({_colours(model)})" if multi else ""),
        *(["  Für eine aufgeklebte Figur: 2K-Kleber oder Sekundenkleber (Gel)"]
          if cfg.top == "flat" else []),
        *(["  Ein paar Tropfen Sekundenkleber für die Figur im Sockel"] if standee else []),
        "",
        "DRUCKEN",
        f"  Snapmaker U1: {U1_FOLDER}/{stem}.3mf in Snapmaker Orca öffnen. Die Farben je Kopf",
        "  und die Einstellungen (0,4-mm-Düse, 0,20 mm Standard, Snapmaker PLA Basic) sind",
        "  gesetzt; die Filamente bei Bedarf an die eingelegten anpassen.",
        f"  Andere Drucker: {stem}.3mf öffnen (Orca Slicer, Bambu Studio), Schichthöhe 0,2 mm,",
        "  Füllung 20 %, keine Stützen. Die Farben im Slicer für die Köpfe selbst wählen.",
        "  Alles liegt schon richtig auf der Platte"
        + (", die Figur flach daneben." if standee else "."),
        *(["  Die Figur druckt aufrecht mit dem Sockel in einem Stück. Unter Kinn, Armen und",
           "  Ohren hat sie kleine Schrägen, damit nichts in der Luft hängt."]
          if round_figure else []),
        *(["  Köpfe und Farben:", *heads] if multi else []),
        f"  Druckpause bei {pause} mm: Sie steckt schon in der 3MF. Wenn der Drucker anhält,",
        "  den NFC-Chip flach in die runde Vertiefung legen (Schrift egal) und fortsetzen.",
        "  Mit STL statt 3MF: die Pause im Slicer selbst setzen, auf die erste Schicht über",
        f"  {pause} mm.",
        "  Tipp: Den Chip vorher in der Myboxi-App unter „Figuren“ mit dem Handy scannen,",
        "  oder später einfach auf die Box legen und unter „Unbekannte Figuren“ übernehmen.",
        *(["", "ZUSAMMENSETZEN",
           "  Die Figur mit dem Steg unten in den Schlitz des Sockels stecken, Gesicht nach vorn",
           "  (zum Namen). Sitzt sie, mit ein paar Tropfen Sekundenkleber festkleben."]
          if standee else []),
        "",
        "SICHERHEIT",
        "  Der Chip ist ganz eingeschlossen. Den Sockel nur verwenden, wenn er heil ist; eine",
        "  aufgeklebte oder gesteckte Figur muss fest sitzen. Für Kinder unter 3 Jahren nur mit",
        "  Figuren, die selbst für dieses Alter geeignet sind. Keine Magnete einbauen.",
        "",
        "LIZENZ",
        "  Erzeugte Druckdateien: CC BY-SA 4.0 (Myboxi, myboxi.eu).",
        f"  Die Druckeinstellungen in {U1_FOLDER}/ stammen aus den Profilen von Snapmaker Orca",
        "  (AGPL-3.0).",
        "",
    ]  # fmt: skip
    return "\n".join(lines)


EXTRA_FILES = {
    "figure3d": "-figur",
    "figure3d_clothes": "-figur-kleidung",
    "figure3d_white": "-figur-hell",
    "figure3d_details": "-figur-gesicht",
}


def bundle_zip(model: FigureModel, url: str | None = None) -> bytes:
    stem = file_stem(model.config)
    entries: list[tuple[str, str | bytes]] = [
        (f"{stem}.3mf", threemf(model)),
        (f"{U1_FOLDER}/{stem}.3mf", threemf(model, u1=True)),
    ]
    for piece in model.pieces:
        suffix = "" if piece.key == "figure" else "-figur"
        entries.append(
            (f"stl/{stem}{suffix}.stl", export.stl(piece.printed(piece.solid), piece.label))
        )
        if not piece.inlay.is_empty():
            entries.append(
                (f"stl/{stem}{suffix}-details.stl",
                 export.stl(piece.printed(piece.inlay), f"{piece.label} Details"))
            )  # fmt: skip
    for extra in model.extras:  # in the base's frame: load together, as parts of one object
        name = EXTRA_FILES[extra.key]
        entries.append(
            (f"stl/{stem}{name}.stl", export.stl(model.piece.printed(extra.solid), extra.label))
        )
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


def _assembled(model: FigureModel) -> list[tuple[dict[str, object], Manifold]]:
    meshes: list[tuple[dict[str, object], Manifold]] = []
    for piece in model.pieces:
        meta: dict[str, object] = {"key": piece.key, "label": piece.label, "kind": "part",
                                   "color": piece.color, "explode": (0, 0, 0)}  # fmt: skip
        meshes.append((meta, piece.solid))
        if not piece.inlay.is_empty():
            meshes.append(
                (meta | {"key": f"{piece.key}_inlay", "kind": "inlay", "color": piece.inlay_color},
                 piece.inlay)
            )  # fmt: skip
    for extra in model.extras:
        meshes.append(
            ({"key": extra.key, "label": extra.label, "kind": "part", "color": extra.color,
              "explode": (0, 0, 0)}, extra.solid)
        )  # fmt: skip
    return meshes


def preview(model: FigureModel) -> bytes:
    """Same format as the case preview (export.preview), so the page can reuse the viewer."""
    meshes = _assembled(model)
    x0, y0, _, x1, y1, z1 = bbox(union([m for _, m in meshes]))
    moved = [(meta, m.translate((-x0, -y0, 0))) for meta, m in meshes]
    return export.encode_preview(moved, (x1 - x0, y1 - y0, z1), f"figure {FIGURE_VERSION}")


def png(model: FigureModel, size: tuple[int, int] = (480, 360)) -> bytes:
    items = [Item(m, str(meta["color"])) for meta, m in _assembled(model)]
    upright = model.tile is not None or bool(model.extras)
    return render(items, size=size, yaw=-25.0, pitch=18.0 if upright else 35.0)

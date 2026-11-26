"""The printed parts in the assembly frame (see __init__). Print orientation lives in build.py.

- body: top, back and sides; printed upside down with the top on the bed (smooth, no supports)
- front: panel behind the window frame; printed face down, so any grille prints cleanly
- base: bottom plate with the board standoffs; screwed into the corner columns
- speaker_ring: clamps the speaker to the front panel
- chamber_left, chamber_right (stereo radio): closed boxes behind the speakers that clamp them
- ear (bear only), figure: base for a figure with a pocket for an NFC coin tag
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from manifold3d import CrossSection, Manifold

from myboxi_case import characters, faces, patterns, text
from myboxi_case.components import (
    AMP,
    BOARDS,
    FRS5X_FRAME_R,
    FRS5X_LUG,
    FRS5X_T,
    MIC_GAP,
    MIC_HOLES,
    MIC_PCB,
    MIC_R,
    PN532,
    board_footprint,
    board_holes,
)
from myboxi_case.config import CaseConfig
from myboxi_case.geom import (
    bounds,
    box,
    circle,
    cylinder_y,
    cylinder_z,
    prism,
    profile,
    rect,
    rounded_prism,
    rounded_rect,
    section_union,
    union,
    xz_slab,
    yz_slab,
)
from myboxi_case.layout import (
    BASE,
    BOTTOM_ROUND,
    COLUMN_R,
    ENGRAVE,
    FIELD_R,
    FRAME,
    PANEL_T,
    RAIL_OVERLAP,
    RAIL_T,
    RING_R,
    RING_W,
    SOCKET_HOLE_DX,
    SOCKET_ROUND,
    SPEAKER_RIM,
    STANDOFF_H,
    STANDOFF_R,
    SYMBOLS,
    TOP,
    TOP_ROUND,
    WALL,
    WINDOW_R,
    Layout,
)

PILOT_M3 = {"self_tap": 1.25, "insert": 2.0}  # radius: self-tapping M3, or M3 heat-set insert
PILOT_M25 = {"self_tap": 1.1, "insert": 1.75}
EAR_T = 5.0
EAR_ROUND = 2.0
DIMPLE = 1.2  # depth of the grille's dimples beyond the speaker
EAR_TENON = (16.0, 4.6, 6.0)  # width, thickness, depth below the top


@dataclass(frozen=True)
class Shape:
    solid: Manifold
    inlay: Manifold  # engraved areas, for a second colour (empty if none)
    panel: Manifold = field(default_factory=Manifold)  # coloured fields (the front's colour)


class Geometry:
    """Derived positions shared by the parts."""

    def __init__(self, cfg: CaseConfig, layout: Layout) -> None:
        self.cfg = cfg
        self.lay = layout
        self.tol = cfg.tolerance
        self.outline = profile(layout.width, layout.depth, layout.r_front, layout.r_back)
        self.inner = self.outline.offset(-WALL)
        self.panel_back = WALL + PANEL_T
        self.rail_y0 = self.panel_back + self.tol
        self.columns = self._columns()

    def _columns(self) -> list[tuple[float, float]]:
        lay = self.lay
        front_y = self.rail_y0 + RAIL_T + 1.0 + COLUMN_R
        fx = WALL + COLUMN_R - 0.4
        ri = lay.r_back - WALL
        k = (ri - COLUMN_R + 0.4) / math.sqrt(2)
        back_y = lay.depth - lay.r_back + k
        return [
            (fx, front_y),
            (lay.width - fx, front_y),
            (lay.r_back - k, back_y),
            (lay.width - lay.r_back + k, back_y),
        ]

    @property
    def panel_z(self) -> tuple[float, float]:
        return (BASE + self.tol, self.lay.height - TOP - self.tol)

    @property
    def outer_solid(self) -> Manifold:
        return rounded_prism(self.outline, self.lay.height, TOP_ROUND, BOTTOM_ROUND)

    def pilot(self, table: dict[str, float]) -> float:
        return table[self.cfg.fastening]


def _engrave_top(g: Geometry, section: CrossSection) -> Manifold:
    return prism(section, g.lay.height - ENGRAVE, g.lay.height + 0.01)


def _engrave_front(section: CrossSection) -> Manifold:
    """Engraving into the visible face of the front panel (y = WALL)."""
    return xz_slab(section, WALL - 0.01, WALL + ENGRAVE)


def body(g: Geometry) -> Shape:
    lay, tol = g.lay, g.tol
    top = lay.height - TOP
    outer = g.outer_solid
    # The inside follows the rounded top edge, so the wall keeps its thickness along it.
    cavity = rounded_prism(g.inner, top + 1.0, TOP_ROUND - WALL).translate((0, 0, -1.0))
    shell = outer - cavity

    # U-shaped window, open at the bottom: nothing to bridge when printed upside down.
    wx0, wx1 = lay.window_x
    window = rounded_rect(wx0, -20.0, wx1, lay.window_top, WINDOW_R)
    shell -= xz_slab(window, -1.0, WALL + 0.01)

    px0, px1 = lay.panel_x
    rails = [
        box(0.0, g.rail_y0, BASE, px0 + RAIL_OVERLAP, g.rail_y0 + RAIL_T, top + 0.01),
        box(px1 - RAIL_OVERLAP, g.rail_y0, BASE, lay.width, g.rail_y0 + RAIL_T, top + 0.01),
        box(px0, g.rail_y0, top - 6.0, px1, g.rail_y0 + RAIL_T, top + 0.01),
    ]
    pilot = g.pilot(PILOT_M3)
    columns = [cylinder_z(COLUMN_R, x, y, BASE, top + 0.01) for x, y in g.columns]
    features = union([*rails, *columns, _pn532_frame(g, tol), *_mic_frame(g)]) ^ outer
    shell = union([shell, features])
    shell -= union([cylinder_z(pilot, x, y, BASE - 1.0, BASE + 14.0) for x, y in g.columns])

    # Buttons, ear slots and the socket go through the walls.
    holes = [
        cylinder_z(g.cfg.button / 2 + tol, b.x, b.y, top - 1.0, lay.height + 1.0)
        for b in lay.buttons
    ]
    if lay.character is not None:
        tw = characters.topper(lay.character).tenon
        tt = EAR_TENON[1]
        for ex, ey in ear_positions(lay):
            ty0 = ey - EAR_T / 2  # the tenon is flush with the topper's front face
            holes.append(
                box(
                    ex - tw / 2 - tol,
                    ty0 - tol,
                    top - 1.0,
                    ex + tw / 2 + tol,
                    ty0 + tt + tol,
                    lay.height + 1.0,
                )
            )
    if lay.character == "unicorn":
        hx, hy = horn_position(lay)
        holes.append(cylinder_z(1.7, hx, hy, top - 1.0, lay.height + 1.0))  # M3 from inside
    sx, sz = lay.socket
    if lay.socket_round:
        socket = circle(SOCKET_ROUND / 2 + tol, sx, sz)
    else:
        socket_hole = rounded_rect(sx - 6.5, sz - 3.5, sx + 6.5, sz + 3.5, 3.0)
        screw_holes = [circle(1.6, sx + dx, sz) for dx in (-SOCKET_HOLE_DX, SOCKET_HOLE_DX)]
        socket = section_union([socket_hole, *screw_holes])
    holes.append(xz_slab(socket, lay.depth - WALL - 1.0, lay.depth + 1.0))
    if g.cfg.board == "pi4":
        vx = _vents_x(lay)
        vents = section_union(
            [rounded_rect(vx + 6 * i, 20.0, vx + 3 + 6 * i, 40.0, 1.5) for i in range(5)]
        )
        holes.append(xz_slab(vents, lay.depth - WALL - 1.0, lay.depth + 1.0))
    holes += _mic_holes(g)
    shell -= union(holes)

    # Engravings on the top face (on the print bed): figure spot and button symbols.
    fx, fy = lay.figure
    marks = [circle(RING_R + RING_W / 2, fx, fy) - circle(RING_R - RING_W / 2, fx, fy)]
    for b in lay.buttons:
        marks.append(patterns.symbol(SYMBOLS[b.action]).translate((b.x, b.y + b.symbol_dy)))
    marks_top = section_union(marks)
    inlay = _engrave_top(g, marks_top) ^ outer
    panel = _fields(g, marks_top) ^ outer
    return Shape(shell - inlay - panel, inlay, panel)


def _vents_x(lay: Layout) -> float:
    """Left edge of the Pi 4's vents in the back wall: beside the socket, or behind the Pi."""
    return 50.0 if lay.stereo else lay.socket[0] + 20.0


def _mic_points(lay: Layout) -> list[tuple[float, float]]:
    if lay.mic is None:
        return []
    mx, my = lay.mic
    return [(mx + dx, my + dy) for dx, dy in MIC_HOLES]


def _mic_holes(g: Geometry) -> list[Manifold]:
    """Sound holes over each microphone: a small cluster, so a few millimetres of offset on the
    real board do not matter."""
    top = g.lay.height - TOP
    holes: list[Manifold] = []
    for x, y in _mic_points(g.lay):
        spots = [(x, y)] + [
            (x + 2.8 * math.cos(math.radians(a)), y + 2.8 * math.sin(math.radians(a)))
            for a in range(0, 360, 60)
        ]
        holes += [cylinder_z(0.8, px, py, top - 1.0, g.lay.height + 1.0) for px, py in spots]
    return holes


MIC_POSTS = (30.0, 150.0, 270.0)  # degrees; clear of the microphones (45° + n·90°) and ports


def _radial_hull(
    cx: float, cy: float, u: tuple[float, float], radii: list[tuple[float, float]],
    side: tuple[float, float], z0: float, z1: float,
) -> Manifold:  # fmt: skip
    """Hull of thin rods at the given radii along direction ``u`` from (cx, cy), offset
    tangentially by ``side``, from z0 to z1."""
    ux, uy = u
    rods = [
        cylinder_z(0.01, cx + ux * r - uy * s, cy + uy * r + ux * s, z0, z1)
        for pair in radii
        for r in pair
        for s in side
    ]
    return Manifold.batch_hull(rods)


def _mic_frame(g: Geometry) -> list[Manifold]:
    """Three posts with snap lips that hold the microphone board under the top, and spacer pads
    that keep it MIC_GAP below the top plate. Lips as in _pn532_frame: 45° wedges."""
    lay, tol = g.lay, g.tol
    if lay.mic is None:
        return []
    mx, my = lay.mic
    top = lay.height - TOP
    board_bottom = top - MIC_GAP - MIC_PCB - tol
    t, half, lip = 2.0, 4.0, 1.2
    out: list[Manifold] = []
    for a in MIC_POSTS:
        u = (math.cos(math.radians(a)), math.sin(math.radians(a)))
        r0 = MIC_R + tol
        post = _radial_hull(
            mx, my, u, [(r0, r0 + t)], (-half, half), board_bottom - lip - 1.2, top + 0.01
        )
        flat = _radial_hull(
            mx, my, u, [(r0 - lip, r0)], (-half, half), board_bottom - 0.01, board_bottom
        )
        edge = _radial_hull(
            mx, my, u, [(r0, r0)], (-half, half), board_bottom - lip, board_bottom - lip + 0.01
        )
        pad = _radial_hull(
            mx, my, u, [(r0 - 4.0, r0 - 1.0)], (-2.0, 2.0), top - MIC_GAP, top + 0.01
        )
        out += [post, Manifold.batch_hull([flat, edge]), pad]
    return out


def _rounded_field(x0: float, y0: float, x1: float, y1: float) -> CrossSection:
    return rounded_rect(x0, y0, x1, y1, FIELD_R)


def _fields(g: Geometry, marks_top: CrossSection) -> Manifold:
    """Coloured fields set into the top and the side and back walls, inside a frame of the
    body's colour (FRAME from the edge), like panels in a wooden frame. They leave out every
    hole, slot and mark, with a gap of the body's colour around them."""
    lay, tol = g.lay, g.tol
    keep = [marks_top.offset(0.8)]
    keep += [circle(g.cfg.button / 2 + tol + 1.2, b.x, b.y) for b in lay.buttons]
    if lay.character is not None:
        tw = characters.topper(lay.character).tenon
        for ex, ey in ear_positions(lay):
            ty0 = ey - EAR_T / 2
            keep.append(
                rect(ex - tw / 2 - 1.5, ty0 - 1.5, ex + tw / 2 + 1.5, ty0 + EAR_TENON[1] + 1.5)
            )
    if lay.character == "unicorn":
        hx, hy = horn_position(lay)
        keep.append(circle(HORN_R + 1.0, hx, hy))
    keep += [circle(6.0, x, y) for x, y in _mic_points(lay)]
    top_field = g.outline.offset(-FRAME - FIELD_R).offset(FIELD_R) - section_union(keep)
    fields = [_engrave_top(g, top_field)]

    z0, z1 = FRAME, lay.height - TOP_ROUND - 1.0  # the walls: above the foot, below the round
    side = _rounded_field(max(lay.r_front, FRAME), z0, lay.depth - max(lay.r_back, FRAME), z1)
    fields.append(yz_slab(side, -0.01, ENGRAVE))
    fields.append(yz_slab(side, lay.width - ENGRAVE, lay.width + 0.01))
    sx, sz = lay.socket
    if lay.socket_round:
        back_keep = [circle(SOCKET_ROUND / 2 + 5.0, sx, sz)]
    else:
        back_keep = [rounded_rect(sx - 10.0, sz - 6.0, sx + 10.0, sz + 6.0, 3.0)]
        back_keep += [circle(3.6, sx + dx, sz) for dx in (-SOCKET_HOLE_DX, SOCKET_HOLE_DX)]
    if g.cfg.board == "pi4":
        vx = _vents_x(lay)
        back_keep.append(rect(vx - 2.0, 18.0, vx + 31.0, 42.0))
    back = _rounded_field(max(lay.r_back, FRAME), z0, lay.width - max(lay.r_back, FRAME), z1)
    fields.append(xz_slab(back - section_union(back_keep), lay.depth - ENGRAVE, lay.depth + 0.01))
    return union(fields)


def _pn532_frame(g: Geometry, tol: float) -> Manifold:
    """Corner posts that hold the NFC board right under the top plate, with small snap lips.

    The lips are 45° wedges: the board slides over them and they print upside down.
    """
    lay = g.lay
    fx, fy = lay.figure
    w, d = PN532
    top = lay.height - TOP
    x0, x1 = fx - w / 2 - tol, fx + w / 2 + tol
    y0, y1 = fy - d / 2 - tol, fy + d / 2 + tol
    board_bottom = top - 1.6 - tol
    z0 = board_bottom - 1.2
    leg, t, lip = 7.0, 1.6, 1.2
    posts: list[Manifold] = []
    for cx, sx in ((x0, -1), (x1, 1)):
        for cy, sy in ((y0, -1), (y1, 1)):
            # L-shaped post outside the board corner; sx/sy point away from the board.
            xa, xb = sorted((cx - sx * leg, cx + sx * t))
            ya, yb = sorted((cy - sy * leg, cy + sy * t))
            xo = sorted((cx, cx + sx * t))
            yo = sorted((cy, cy + sy * t))
            posts.append(box(xo[0], ya, z0, xo[1], yb, top + 0.01))
            posts.append(box(xa, yo[0], z0, xb, yo[1], top + 0.01))
            inner_x = sorted((cx, cx - sx * lip))
            inner_y = sorted((cy, cy - sy * lip))
            flat = box(
                inner_x[0], inner_y[0], board_bottom - 0.01, inner_x[1], inner_y[1], board_bottom
            )
            edge = box(
                cx - 0.01,
                cy - 0.01,
                board_bottom - lip,
                cx + 0.01,
                cy + 0.01,
                board_bottom - lip + 0.01,
            )
            posts.append(Manifold.batch_hull([flat, edge]))
    return union(posts)


def front(g: Geometry) -> Shape:
    if g.lay.stereo:
        return _front_stereo(g)
    lay, cfg = g.lay, g.cfg
    px0, px1 = lay.panel_x
    z0, z1 = g.panel_z
    tol = g.tol
    panel = xz_slab(rect(px0 + tol, z0, px1 - tol, z1), WALL, g.panel_back)
    sx, sz = lay.speaker
    r = cfg.speaker / 2
    openings = patterns.grille(cfg.grille, r - 2.0)
    snout = _snout(g)
    if snout is not None:
        # Behind the snout: one opening for the sound, and a recess that holds the snout.
        openings = (openings - snout.footprint.offset(1.0)) + snout.opening
        seat = snout.footprint.offset(0.2).translate((sx, sz))
        panel -= xz_slab(seat, WALL - 0.01, WALL + faces.RECESS)
    panel -= xz_slab(openings.translate((sx, sz)), WALL - 1.0, g.panel_back + 1.0)
    # The grille as a large disc like on a radio speaker: through holes in front of the speaker,
    # the same pattern as shallow dimples beyond it (the speaker seat is behind).
    disc_r = _disc_radius(g) if snout is None else None
    dimples = CrossSection()
    if disc_r is not None:
        dimples = _dimples(patterns.grille(cfg.grille, disc_r - 2.0), openings)
        if not dimples.is_empty():
            panel -= xz_slab(dimples.translate((sx, sz)), WALL - 0.01, WALL + DIMPLE)

    # Speaker seat on the back: centring ring and three bosses for the clamp ring.
    back = g.panel_back
    seat = circle(r + 0.3 + 1.6, sx, sz) - circle(r + 0.3, sx, sz)
    parts = [panel, xz_slab(seat, back - 0.01, back + 2.0)]
    pilot = g.pilot(PILOT_M25)
    boss_holes: list[Manifold] = []
    for x, z in speaker_bosses(lay, cfg.speaker):
        parts.append(cylinder_y(3.0, x, z, back - 0.01, back + SPEAKER_RIM - 0.3))
        boss_holes.append(cylinder_y(pilot, x, z, back - 1.0, back + SPEAKER_RIM))
    solid = union(parts) - union(boss_holes)

    marks: list[CrossSection] = []
    nx0, nz0, nx1, nz1 = lay.name_box
    if cfg.name:
        label = text.fitted(cfg.name, nx1 - nx0, min(nz1 - nz0, 16.0) * 0.72, 5.0)
        _, by0, _, by1 = bounds(label)
        marks.append(label.translate(((nx0 + nx1) / 2, (nz0 + nz1) / 2 - (by0 + by1) / 2)))
    if disc_r is not None:
        disc = circle(disc_r) - dimples - openings
        marks.append(disc.translate((sx, sz)))
        marks += _notes(g, disc_r)
    if lay.character is not None:
        face = characters.face_marks(lay.character, r, snout is not None).translate((sx, sz))
        # Keep a bar between the face and the grille openings; nothing under the snout.
        face -= patterns.grille(cfg.grille, r - 2.0).translate((sx, sz)).offset(1.0)
        if snout is not None:
            face -= snout.footprint.offset(1.5).translate((sx, sz))
        marks.append(face)
    if not marks:
        return Shape(solid, Manifold())
    inlay = _engrave_front(section_union(marks))
    return Shape(solid - inlay, inlay)


def _dimples(pattern: CrossSection, openings: CrossSection) -> CrossSection:
    """The grille pattern where it is not already open: dimples on top of a hole are left
    out, a slot that runs into an opening stops a bar's width before it."""
    near = openings.offset(1.0)
    kept: list[CrossSection] = []
    for part in pattern.decompose():
        if (part ^ openings).is_empty():
            kept.append(part)
            continue
        for rest in (part - near).decompose():
            if rest.area() > 3.0 and not rest.offset(-0.6).is_empty():
                kept.append(rest)
    return section_union(kept)


def _disc_radius(g: Geometry) -> float:
    """The grille disc: as large as the window allows, clear of the name, at least the
    speaker."""
    lay = g.lay
    sx, sz = lay.speaker
    wx0, wx1 = lay.window_x
    nx0, _, nx1, nz1 = lay.name_box
    room = [sx - wx0 - 5.0, wx1 - sx - 5.0, lay.window_top - sz - 5.0, sz - g.panel_z[0] - 6.0]
    if nx0 < sx < nx1:  # name below
        room.append(sz - nz1 - 4.0)
    else:  # name beside
        room.append((nx0 - sx - 4.0) if nx0 > sx else (sx - nx1 - 4.0))
    return max(min(room), g.cfg.speaker / 2)


def _notes(g: Geometry, disc_r: float) -> list[CrossSection]:
    """Music notes in the upper corners of the window, where they are clear of the grille and
    the name."""
    lay = g.lay
    sx, sz = lay.speaker
    wx0, wx1 = lay.window_x
    nx0, nz0, nx1, nz1 = lay.name_box
    clear = section_union([circle(disc_r + 4.0, sx, sz), rect(nx0, nz0, nx1, nz1).offset(3.0)])
    notes: list[CrossSection] = []
    for kind, x in (("eighth", wx0 + 5.0), ("beamed", wx1 - 12.0)):
        note = patterns.note(kind).translate((x, lay.window_top - 17.0))  # type: ignore[arg-type]
        if (note ^ clear).is_empty():
            notes.append(note)
    return notes


def _snout(g: Geometry) -> faces.Face | None:
    if g.lay.character is None:
        return None
    return faces.face(g.lay.character, g.cfg.speaker / 2)


def snout(g: Geometry) -> Shape:
    """The sculpted face of an animal box, glued into its recess on the front panel. Drawn
    around the speaker centre with z towards the viewer (faces.py); here turned onto the panel:
    its back on the recess floor, its front towards -y."""
    face = _snout(g)
    if face is None:
        raise ValueError("only animal boxes have a snout")
    sx, sz = g.lay.speaker

    def place(m: Manifold) -> Manifold:
        return m.rotate((90, 0, 0)).translate((sx, WALL + faces.RECESS, sz))

    return Shape(place(face.snout), place(face.details))


CHAMBER_R = 35.0  # inside, clear of the speaker's lugs (34 mm)
CHAMBER_WALL = 2.4
CHAMBER_LEN = 40.0  # cylinder behind the panel, then a cone that prints without supports
CHAMBER_BOSS_R = 41.0  # screw bosses around each speaker, at 45° between the lugs
CHAMBER_BOSS_H = 6.0
GRILLE_R = 21.0  # sound openings: inside the 46 mm cutout


def chamber_bosses(sx: float, sz: float) -> list[tuple[float, float]]:
    return [
        (
            sx + CHAMBER_BOSS_R * math.cos(math.radians(a)),
            sz + CHAMBER_BOSS_R * math.sin(math.radians(a)),
        )
        for a in (45, 135, 225, 315)
    ]


def _front_stereo(g: Geometry) -> Shape:
    """Front of the stereo radio: two grilles with a disc each, the name above them. Behind
    each speaker a centring ring (open at the lugs) and four bosses for its chamber."""
    lay, cfg, tol = g.lay, g.cfg, g.tol
    px0, px1 = lay.panel_x
    z0, z1 = g.panel_z
    panel = xz_slab(rect(px0 + tol, z0, px1 - tol, z1), WALL, g.panel_back)
    back = g.panel_back
    openings = patterns.grille(cfg.grille, GRILLE_R)
    disc_r = 30.0
    dimples = _dimples(patterns.grille(cfg.grille, disc_r - 2.0), openings)
    parts: list[Manifold] = [panel]
    cuts: list[Manifold] = []
    marks: list[CrossSection] = []
    pilot = g.pilot(PILOT_M25)
    lug_gap = rect(-FRS5X_LUG[0] / 2 - 1.0, -FRS5X_LUG[1] / 2 - 0.5, FRS5X_LUG[0] / 2 + 1.0,
                   FRS5X_LUG[1] / 2 + 0.5)  # fmt: skip
    for sx, sz in lay.speakers:
        cuts.append(xz_slab(openings.translate((sx, sz)), WALL - 1.0, back + 1.0))
        if not dimples.is_empty():
            cuts.append(xz_slab(dimples.translate((sx, sz)), WALL - 0.01, WALL + DIMPLE))
        ring = circle(FRS5X_FRAME_R + 0.3 + 1.6) - circle(FRS5X_FRAME_R + 0.3) - lug_gap
        parts.append(xz_slab(ring.translate((sx, sz)), back - 0.01, back + FRS5X_T - 0.3))
        for x, z in chamber_bosses(sx, sz):
            parts.append(cylinder_y(3.0, x, z, back - 0.01, back + CHAMBER_BOSS_H))
            cuts.append(cylinder_y(pilot, x, z, back - 1.0, back + CHAMBER_BOSS_H + 0.1))
        marks.append((circle(disc_r) - dimples - openings).translate((sx, sz)))
    solid = union(parts) - union(cuts)
    nx0, nz0, nx1, nz1 = lay.name_box
    if cfg.name:
        label = text.fitted(cfg.name, nx1 - nx0, (nz1 - nz0) * 0.8, 5.0)
        _, by0, _, by1 = bounds(label)
        marks.append(label.translate(((nx0 + nx1) / 2, (nz0 + nz1) / 2 - (by0 + by1) / 2)))
    inlay = _engrave_front(section_union(marks))
    return Shape(solid - inlay, inlay)


def chamber(g: Geometry, index: int) -> Shape:
    """Closed box behind one speaker (about 0.2 l): it clamps the speaker's lugs against the
    front and is screwed to its bosses. A cylinder and a cone, printed open side down."""
    sx, sz = g.lay.speakers[index]
    y0 = g.panel_back  # on the panel's back, around the speaker
    r_in, r_out = CHAMBER_R, CHAMBER_R + CHAMBER_WALL
    cone = r_out * 1.2  # steeper than 45°: the inside prints without supports
    y1 = y0 + CHAMBER_LEN

    def disc(r: float, ya: float, yb: float) -> Manifold:
        return cylinder_y(r, sx, sz, ya, yb)

    outer = Manifold.batch_hull([disc(r_out, y0, y1), disc(0.6, y1 + cone - 0.6, y1 + cone)])
    tip = y1 + cone - CHAMBER_WALL * 1.6
    inner = Manifold.batch_hull([disc(r_in, y0 - 1.0, y1), disc(0.01, tip - 0.01, tip)])
    solid = outer - inner
    # Ribs that press the lugs flat; the frame sits in the centring ring on the panel.
    rib_y0 = y0 + FRS5X_T
    for side in (-1, 1):
        xa, xb = sorted((sx + side * (FRS5X_FRAME_R + 1.5), sx + side * (r_in + 0.5)))
        solid += box(xa, rib_y0, sz - 4.0, xb, y0 + 12.0, sz + 4.0)
    # Lugs for the screws, above the bosses on the panel.
    ly0 = y0 + CHAMBER_BOSS_H + 0.2
    holes: list[Manifold] = []
    for x, z in chamber_bosses(sx, sz):
        mid = ((x + sx) / 2, (z + sz) / 2)
        lug = Manifold.batch_hull(
            [cylinder_y(3.5, x, z, ly0, ly0 + 3.0), cylinder_y(3.5, *mid, ly0, ly0 + 3.0)]
        )
        solid += lug - disc(r_in, y0 - 1.0, y1)
        holes.append(cylinder_y(1.45, x, z, ly0 - 1.0, ly0 + 4.0))
    # The speaker cable leaves at the bottom; seal the hole with hot glue or putty.
    holes.append(box(sx - 2.0, y0 + 18.0, sz - r_out - 1.0, sx + 2.0, y0 + 22.0, sz - r_in + 1.0))
    return Shape(solid - union(holes), Manifold())


def speaker_bosses(lay: Layout, diameter: float) -> list[tuple[float, float]]:
    sx, sz = lay.speaker
    rr = diameter / 2 + 4.0
    return [
        (sx + rr * math.cos(math.radians(a)), sz + rr * math.sin(math.radians(a)))
        for a in (90, 210, 330)
    ]


def speaker_ring(g: Geometry) -> Shape:
    lay, d = g.lay, g.cfg.speaker
    sx, sz = lay.speaker
    y0 = g.panel_back + SPEAKER_RIM
    ring = circle(d / 2 + 4.0, sx, sz) - circle(d / 2 - 3.5, sx, sz)
    lugs = [circle(3.0, x, z) for x, z in speaker_bosses(lay, d)]
    holes = [circle(1.45, x, z) for x, z in speaker_bosses(lay, d)]
    shape = section_union([ring, *lugs]) - section_union(holes)
    return Shape(xz_slab(shape, y0, y0 + 2.4), Manifold())


def base(g: Geometry) -> Shape:
    lay, cfg, tol = g.lay, g.cfg, g.tol
    wx0, wx1 = lay.window_x
    footprint = section_union([g.inner.offset(-tol), rect(wx0 + tol, 0.0, wx1 - tol, WALL + 1.0)])
    plate = prism(footprint, 0.0, BASE)
    parts = [plate]
    cuts: list[Manifold] = []
    for x, y in g.columns:
        cuts.append(cylinder_z(1.7, x, y, -1.0, BASE + 1.0))
        cuts.append(Manifold.cylinder(1.6, 3.3, 1.7).translate((x, y, -0.01)))  # countersink, 45°
    spec = BOARDS[cfg.board]
    pilot = g.pilot(PILOT_M25)
    for hx, hy in board_holes(lay.board, spec):
        parts.append(cylinder_z(STANDOFF_R, hx, hy, BASE - 0.01, BASE + STANDOFF_H))
        cuts.append(cylinder_z(pilot, hx, hy, BASE - 1.0, BASE + STANDOFF_H + 1.0))
    if not lay.stereo:
        # Amplifier holder: groove plus two posts with slots; the board slides in from above
        # and sits tight (no glue).
        ax, ay = lay.amp
        aw = AMP[0] / 2
        parts.append(box(ax - aw - 3.0, ay - 3.0, BASE - 0.01, ax + aw + 3.0, ay + 3.0, BASE + 3.0))
        for side in (-1, 1):
            inner, outer = sorted((ax + side * (aw - 1.5), ax + side * (aw + 3.0)))
            parts.append(box(inner, ay - 3.0, BASE - 0.01, outer, ay + 3.0, BASE + 14.0))
        slot = 0.8 + tol / 2
        cuts.append(
            box(ax - aw - tol, ay - slot, BASE + 1.0, ax + aw + tol, ay + slot, BASE + 14.1)
        )
    for x, y in lay.anchors:
        parts.append(_tie_anchor(x, y))
    if lay.powerbank is not None:
        px, py = lay.powerbank
        w, d = 93.0, 61.0
        for cx, cy, sx, sy in (
            (px, py, 1, 1),
            (px + w, py, -1, 1),
            (px, py + d, 1, -1),
            (px + w, py + d, -1, -1),
        ):
            ox = cx - 2.0 if sx > 0 else cx
            oy = cy - 2.0 if sy > 0 else cy
            parts.append(
                box(
                    ox,
                    min(cy, cy + sy * 12),
                    BASE - 0.01,
                    ox + 2.0,
                    max(cy, cy + sy * 12),
                    BASE + 12.0,
                )
            )
            parts.append(
                box(
                    min(cx, cx + sx * 12),
                    oy,
                    BASE - 0.01,
                    max(cx, cx + sx * 12),
                    oy + 2.0,
                    BASE + 12.0,
                )
            )
        # Slots for a hook-and-loop strap over the powerbank (front to back).
        for y0 in (py - 4.5, py + d + 1.5):
            cuts.append(box(px + w / 2 - 11.0, y0, -1.0, px + w / 2 + 11.0, y0 + 3.0, BASE + 1.0))
    if cfg.board == "pi4":
        bx0, by0, bx1, by1 = board_footprint(lay.board, spec)
        for i in range(6):
            x = bx0 + 12.0 + i * (bx1 - bx0 - 24.0) / 5
            cuts.append(box(x - 1.5, by0 + 12.0, -1.0, x + 1.5, by1 - 12.0, BASE + 1.0))
    return Shape(union(parts) - union(cuts), Manifold())


def _tie_anchor(x: float, y: float) -> Manifold:
    """Mount for a 3.6 mm cable tie: bridge over a 4.5 mm gap, the tie runs along y."""
    posts = [
        box(x + sx, y - 2.0, BASE - 0.01, x + sx + 2.5, y + 2.0, BASE + 5.0) for sx in (-4.75, 2.25)
    ]
    bridge = box(x - 4.75, y - 2.0, BASE + 3.0, x + 4.75, y + 2.0, BASE + 5.0)
    return union([*posts, bridge])


def ear_positions(lay: Layout) -> list[tuple[float, float]]:
    if lay.character is None:
        return []
    offset = characters.topper(lay.character).offset
    return [(offset, 11.0), (lay.width - offset, 11.0)]


def ear(g: Geometry, x: float, y: float) -> Shape:
    """Ear (or eye) standing on the top, parallel to the front; its tenon is glued into a slot.

    Printed lying on its front face, so the tenon is flush with that face (nothing floats).
    """
    if g.lay.character is None:
        raise ValueError("ears only belong to character forms")
    top = characters.topper(g.lay.character)
    h = g.lay.height
    _, tt, td = EAR_TENON
    outline = top.outline.translate((x, h))
    tenon = rect(x - top.tenon / 2, h - TOP - td + 2.0, x + top.tenon / 2, h + 0.5)
    y0 = y - EAR_T / 2
    # Rounded along the back, which is on top when printed; flat front on the print bed.
    plate = rounded_prism(outline, EAR_T, 0.0, EAR_ROUND)  # rounded at z = 0: the back
    plate = plate.rotate((90, 0, 0)).translate((0, y0 + EAR_T, 0))
    solid = union([plate, xz_slab(tenon, y0, y0 + tt)])
    inlay = xz_slab(top.inlay.translate((x, h)), y0 - 0.01, y0 + ENGRAVE)
    return Shape(solid - inlay, inlay)


HORN_R = 9.0
HORN_H = 40.0


def horn_position(lay: Layout) -> tuple[float, float]:
    return (lay.width / 2, 13.0)


def horn(g: Geometry) -> Shape:
    """Twisted unicorn horn; stands on its flat base and is screwed on from inside (M3)."""
    hx, hy = horn_position(g.lay)
    h = g.lay.height
    star = patterns.star(HORN_R - 1.2, HORN_R - 3.2).offset(1.2)
    spiral = star.extrude(HORN_H, n_divisions=40, twist_degrees=-300.0, scale_top=(0.08, 0.08))
    solid = spiral.translate((hx, hy, h)) - cylinder_z(g.pilot(PILOT_M3), hx, hy, h - 1.0, h + 9.0)
    return Shape(solid, Manifold())


def figure_base(g: Geometry) -> Shape:
    """Ø40 base for a figure, standing on the ring; closed pocket for a 25 mm NTAG213 coin tag.

    The pocket is closed: pause the print at 2.2 mm and drop the tag in.
    """
    fx, fy = g.lay.figure
    h = g.lay.height
    disc = Manifold.batch_hull(
        [
            cylinder_z(19.4, fx, fy, h, h + 0.6),
            cylinder_z(20.0, fx, fy, h + 0.6, h + 5.0),
            cylinder_z(19.0, fx, fy, h + 5.0, h + 6.0),
        ]
    )
    pocket = cylinder_z(12.8, fx, fy, h + 1.0, h + 2.2)
    return Shape(disc - pocket, Manifold())

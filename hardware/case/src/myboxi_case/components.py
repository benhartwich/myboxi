"""Bought parts inside the case: dimensions and keep-out volumes (mm, assembly frame).

Sources: Raspberry Pi mechanical drawings (Zero 2 W, Pi 4 B), Elechouse PN532 V3 module,
Adafruit MAX98357A breakout, Visaton FRS 5 X data sheet (2013-07-24), Seeed reSpeaker XVF3800
2D drawing. Values marked "measure" vary between vendors; the docs ask to
measure the real part before printing (docs/gehaeuse.md).
"""

from __future__ import annotations

from dataclasses import dataclass

from manifold3d import Manifold

from myboxi_case.config import Board
from myboxi_case.geom import box, cylinder_y, cylinder_z, rounded_rect, union, xz_slab
from myboxi_case.layout import (
    BASE,
    SPEAKER_DEPTH,
    SPEAKER_RIM,
    STANDOFF_H,
    TOP,
    WALL,
    BoardPlace,
    Layout,
)

PCB = 1.6


@dataclass(frozen=True)
class BoardSpec:
    label: str
    length: float
    width: float
    holes: tuple[tuple[float, float], ...]  # board-local, ports edge at y=0
    power_x: float  # centre of the power connector on the ports edge
    height: float  # keep-out above the board: parts, GPIO header and Dupont plugs (measure)
    plug: float  # room for the power plug in front of the ports edge
    overhang: float  # connectors sticking out beyond the far short edge


BOARDS: dict[Board, BoardSpec] = {
    "zero2w": BoardSpec(
        "Raspberry Pi Zero 2 W", 65.0, 30.0,
        ((3.5, 3.5), (61.5, 3.5), (3.5, 26.5), (61.5, 26.5)), 54.0, 25.0, 12.0, 0.0,
    ),
    "pi4": BoardSpec(
        "Raspberry Pi 4", 85.0, 56.0,
        ((3.5, 3.5), (61.5, 3.5), (3.5, 52.5), (61.5, 52.5)), 11.2, 27.0, 14.0, 3.0,
    ),
}  # fmt: skip

PN532 = (42.7, 40.4)  # Elechouse V3 outline
PN532_BELOW = 15.0  # parts and header wiring below the board (measure)
AMP = (20.0, 7.6, 30.0)  # MAX98357A standing in its holder: width, thickness with parts, height
AMP_BOARD_H = 19.4
BUTTON_BODY = {16: (10.0, 30.0), 24: (12.5, 35.0)}  # radius, depth below the top
BUTTON_NUT = {16: 12.0, 24: 17.0}  # radius of nut and spanner room directly under the top
SOCKET_BODY = (14.0, 9.0, 22.0)  # USB-C panel socket incl. cable exit: width, height, depth
PLUG = (12.4, 6.6)  # USB-C plug overmould outside the case (USB-C spec max 12.35 x 6.5)
POWERBANK = (93.0, 61.0, 23.0)  # 10 000 mAh class plus 1 mm (measure)

# Visaton FRS 5 X: round frame Ø 52.5 (1.5 thick), two lugs 68 mm tip to tip with Ø 4.2 holes
# 60 mm apart, basket Ø 50, magnet Ø 45, 33.5 deep including the frame; cutout Ø 46.
FRS5X_FRAME_R = 26.25
FRS5X_LUG = (68.0, 12.0)  # tip to tip, width at the holes
FRS5X_T = 1.5
FRS5X_DEPTH = 33.5
# reSpeaker XVF3800 (USB): Ø 100 board, 1.2 thick; parts up to 6.2 below it (its component
# side faces down), microphones on the diagonals ±33 mm, ports on one edge.
MIC_R = 50.0
MIC_PCB = 1.2
MIC_BELOW = 6.2
MIC_GAP = 1.0  # between the board and the top plate (spacer pads)
MIC_HOLES = ((-33.0, -33.0), (33.0, -33.0), (-33.0, 33.0), (33.0, 33.0))
LINE_AMP = (45.0, 26.0, 22.0)  # analog stereo amplifier module (PAM8403 class), measure
ROUND_SOCKET = (15.5, 28.0)  # round USB-C panel socket inside: radius with nut, depth


@dataclass(frozen=True)
class Component:
    key: str
    label: str
    solid: Manifold
    mounts: tuple[str, ...]  # parts it is fastened to: touching allowed, penetrating not
    color: str


def _board_xy(place: BoardPlace, spec: BoardSpec, bx: float, by: float) -> tuple[float, float]:
    """Board-local point to assembly x/y for the placement's rotation."""
    length, width = spec.length, spec.width
    match place.rotation:
        case 0:
            x, y = bx, by
        case 90:
            x, y = width - by, bx
        case 180:
            x, y = length - bx, width - by
        case 270:
            x, y = by, length - bx
    return (place.x + x, place.y + y)


def board_footprint(place: BoardPlace, spec: BoardSpec) -> tuple[float, float, float, float]:
    corners = [_board_xy(place, spec, bx, by) for bx in (0, spec.length) for by in (0, spec.width)]
    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]
    return (min(xs), min(ys), max(xs), max(ys))


def board_holes(place: BoardPlace, spec: BoardSpec) -> list[tuple[float, float]]:
    return [_board_xy(place, spec, hx, hy) for hx, hy in spec.holes]


def _local_box(
    place: BoardPlace, spec: BoardSpec, bx0: float, by0: float, bx1: float, by1: float,
    z0: float, z1: float,
) -> Manifold:  # fmt: skip
    ax0, ay0 = _board_xy(place, spec, bx0, by0)
    ax1, ay1 = _board_xy(place, spec, bx1, by1)
    return box(min(ax0, ax1), min(ay0, ay1), z0, max(ax0, ax1), max(ay0, ay1), z1)


def pcb_z() -> float:
    return BASE + STANDOFF_H


def board(layout: Layout, board_key: Board) -> list[Component]:
    spec = BOARDS[board_key]
    place = layout.board
    z = pcb_z()
    margin = 0.5
    body = _local_box(
        place, spec, -margin, -margin, spec.length + spec.overhang + margin, spec.width + margin,
        z, z + PCB + spec.height,
    )  # fmt: skip
    px = spec.power_x
    plug = _local_box(place, spec, px - 6.0, -spec.plug, px + 6.0, -margin, z, z + PCB + 8.0)
    return [
        Component("board", spec.label, body, ("base",), "#3f7d4f"),
        Component("board_plug", "Stromstecker", plug, (), "#555a58"),
    ]


def pn532(layout: Layout) -> Component:
    fx, fy = layout.figure
    w, d = PN532
    top = layout.height - TOP
    board = box(fx - w / 2, fy - d / 2, top - PCB, fx + w / 2, fy + d / 2, top)
    # Parts and header wiring below the board keep clear of its corners (snap lips there).
    below = box(
        fx - w / 2 + 2,
        fy - d / 2 + 2,
        top - PCB - PN532_BELOW,
        fx + w / 2 - 2,
        fy + d / 2 - 2,
        top - PCB + 0.01,
    )
    return Component("nfc", "NFC-Leser PN532", union([board, below]), ("body",), "#c0392b")


def frs5x(layout: Layout) -> list[Component]:
    """Both speakers of the stereo radio, lugs horizontal."""
    back = WALL + 3.0
    out: list[Component] = []
    for i, (sx, sz) in enumerate(layout.speakers):
        lw, lh = FRS5X_LUG
        frame = cylinder_y(FRS5X_FRAME_R, sx, sz, back, back + FRS5X_T)
        lugs = box(sx - lw / 2, back, sz - lh / 2, sx + lw / 2, back + FRS5X_T, sz + lh / 2)
        basket = cylinder_y(25.0, sx, sz, back + FRS5X_T, back + FRS5X_DEPTH)
        key = ("speaker_left", "speaker_right")[i]
        out.append(
            Component(
                key,
                "Lautsprecher Visaton FRS 5 X",
                union([frame, lugs, basket]),
                ("front", f"chamber_{key.removeprefix('speaker_')}"),
                "#2b2b2b",
            )
        )
    return out


def mic_board(layout: Layout) -> list[Component]:
    """reSpeaker XVF3800 under the top, ports towards the back, and the room for its angled
    USB-C and audio plugs."""
    if layout.mic is None:
        return []
    mx, my = layout.mic
    top = layout.height - TOP
    z1 = top - MIC_GAP
    z0 = z1 - MIC_PCB
    board_ = cylinder_z(MIC_R, mx, my, z0, z1)
    below = cylinder_z(MIC_R - 3.0, mx, my, z0 - MIC_BELOW, z0 + 0.01)
    usb = box(mx - 8.0, my + MIC_R - 6.0, z0 - 12.0, mx + 8.0, my + MIC_R + 15.0, z1)
    jack = box(mx - 41.0, my + 34.0, z0 - 12.0, mx - 19.0, my + 56.0, z1)
    return [
        Component(
            "mic", "Mikrofon reSpeaker XVF3800", union([board_, below]), ("body",), "#2f3a3a"
        ),
        Component("mic_plugs", "Stecker am Mikrofon", union([usb, jack]), (), "#555a58"),
    ]


def line_amp(layout: Layout) -> Component | None:
    if layout.line_amp is None:
        return None
    x, y = layout.line_amp
    w, d, h = LINE_AMP
    return Component(
        "line_amp", "Verstärker PAM8403", box(x, y, BASE, x + w, y + d, BASE + h), ("base",),
        "#1f6f4f",
    )  # fmt: skip


def round_socket(layout: Layout) -> tuple[Component, Manifold]:
    """Round USB-C panel socket: nut and cable inside, the plug path outside."""
    sx, sz = layout.socket
    r, depth = ROUND_SOCKET
    inner = layout.depth - WALL
    solid = cylinder_y(r, sx, sz, inner - depth, inner)
    pw, ph = PLUG
    plug = xz_slab(
        rounded_rect(sx - pw / 2, sz - ph / 2, sx + pw / 2, sz + ph / 2, ph / 2),
        layout.depth - WALL - 0.5,
        layout.depth + 25.0,
    )
    return Component("socket", "USB-C-Einbaubuchse rund", solid, ("body",), "#555a58"), plug


def speaker(layout: Layout, diameter: float) -> Component:
    sx, sz = layout.speaker
    back = WALL + 3.0  # back face of the front panel (PANEL_T)
    rim = cylinder_y(diameter / 2, sx, sz, back, back + SPEAKER_RIM)
    basket = cylinder_y(diameter / 2 - 4.0, sx, sz, back + SPEAKER_RIM, back + SPEAKER_DEPTH)
    return Component(
        "speaker", f"Lautsprecher {diameter:.0f} mm", union([rim, basket]),
        ("front", "speaker_ring"), "#2b2b2b",
    )  # fmt: skip


def buttons(layout: Layout, size: int) -> list[Component]:
    top = layout.height - TOP
    body_r, depth = BUTTON_BODY[size]
    out: list[Component] = []
    for b in layout.buttons:
        nut = cylinder_z(BUTTON_NUT[size], b.x, b.y, top - 4.0, top)
        body = cylinder_z(body_r, b.x, b.y, top - depth, top - 4.0)
        out.append(
            Component(
                f"button_{b.action}", f"Taster {size} mm", union([nut, body]), ("body",), "#d9d4c7"
            )
        )
    return out


def amp(layout: Layout) -> Component:
    ax, ay = layout.amp
    w, t, h = AMP
    # The board (1.6 mm) stands in a groove and two slotted posts; its parts keep 2.5 mm from
    # the side edges, the wires leave at the top.
    board = box(ax - w / 2, ay - 0.8, BASE + 1.0, ax + w / 2, ay + 0.8, BASE + 1.0 + AMP_BOARD_H)
    parts = box(
        ax - w / 2 + 2.5, ay - t / 2, BASE + 4.5, ax + w / 2 - 2.5, ay + t / 2, BASE + 1.0 + h
    )
    solid = union([board, parts])
    return Component("amp", "Verstärker MAX98357A", solid, ("base",), "#1f4f8f")


def socket(layout: Layout) -> tuple[Component, Manifold]:
    """The socket inside the case and the plug path outside it (must stay free)."""
    sx, sz = layout.socket
    w, h, d = SOCKET_BODY
    inner = layout.depth - WALL
    solid = box(sx - w / 2, inner - d, sz - h / 2, sx + w / 2, inner, sz + h / 2)
    pw, ph = PLUG
    plug = xz_slab(
        rounded_rect(sx - pw / 2, sz - ph / 2, sx + pw / 2, sz + ph / 2, ph / 2),
        layout.depth - WALL - 0.5,
        layout.depth + 25.0,
    )
    return Component("socket", "USB-C-Einbaubuchse", solid, ("body",), "#555a58"), plug


def powerbank(layout: Layout) -> Component | None:
    if layout.powerbank is None:
        return None
    x, y = layout.powerbank
    w, d, h = POWERBANK
    solid = box(x, y, BASE, x + w, y + d, BASE + h)
    return Component("powerbank", "Powerbank", solid, ("base",), "#44484a")

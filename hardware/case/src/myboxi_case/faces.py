"""Sculpted faces for the animal boxes ("Box gestalten", docs/gehaeuse.md): a snout in front of
the speaker, glued into a recess on the front panel.

Modelled like the round figures (figures3d): soft shapes blended into each other, meshed from
their distance field. Drawn in the panel's plane, x to the right and y up from the speaker centre,
z out of the panel towards the viewer. The back is flat (z = 0): printed lying on it, the snout
is a relief that never hangs over. Sound goes through holes that continue the panel's grille.

Each snout is large enough not to be a small part (EN 71-1: it does not fit the 31.7 mm
cylinder), and glued in a recess that holds it in place.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from itertools import pairwise
from typing import Any

import numpy as np
from manifold3d import CrossSection, Manifold, OpType

from myboxi_case import patterns
from myboxi_case.characters import Character
from myboxi_case.figures3d import (
    STEP,
    Cone,
    Ell,
    Shape,
    blend,
    both,
    drop_slivers,
    grid_axes,
    mesh_field,
    region_mesh,
)
from myboxi_case.geom import bounds, prism

RECESS = 0.6  # depth of the recess in the panel that holds the snout
RIM = 1.5  # no sound hole closer to the edge than this
GLUE_RIM = 3.0  # panel left around the opening behind the snout, to glue it on
SPEAKER_RIM = 3.0  # the speaker's frame: the opening stays inside it
MAX_LEAN = 35.0  # degrees: sound holes lean along the surface normal, at most this much


@dataclass(frozen=True)
class Design:
    shape: Shape  # the snout, z >= 0
    details: tuple[Shape, ...]  # nose and mouth (the accent colour)
    closed: tuple[Shape, ...] = ()  # no sound holes here (teeth)


@dataclass(frozen=True)
class Face:
    snout: Manifold  # the snout's colour, in the panel frame (see the module docstring)
    details: Manifold  # nose and mouth
    footprint: CrossSection  # where it sits on the panel (x, y)
    opening: CrossSection  # the panel's opening behind it, for the sound


def _height(shape: Shape, x: float, y: float) -> float:
    """How far ``shape`` stands out of the panel at (x, y): its surface, found on its field."""
    lo, hi = 0.0, 30.0
    for _ in range(30):
        mid = (lo + hi) / 2
        inside = float(shape.sdf((np.array([x]), np.array([y]), np.array([mid])))[0]) < 0
        lo, hi = (mid, hi) if inside else (lo, mid)
    return lo


def _on(shape: Shape, x: float, y: float, lift: float = 0.0) -> tuple[float, float, float]:
    """The point on the front of ``shape`` above (x, y)."""
    return (x, y, _height(shape, x, y) + lift)


def _stroke(shape: Shape, points: list[tuple[float, float]], width: float) -> list[Shape]:
    """A line drawn on the surface of ``shape`` through ``points`` (x, y)."""
    pts = [_on(shape, x, y) for x, y in points]
    return [Cone(a, b, width / 2, width / 2) for a, b in pairwise(pts)]


def bear() -> Design:
    muzzle = Ell((0, -2.0, 0), (23.0, 18.0, 10.0))
    nose = Ell(_on(muzzle, 0, 7.0, -1.2), (6.6, 4.4, 3.2))
    mouth = [
        *_stroke(muzzle, [(0, 4.0), (0, -3.5)], 1.6),
        *_stroke(muzzle, [(-6.5, -4.8), (-3.2, -6.0), (0, -3.5), (3.2, -6.0), (6.5, -4.8)], 1.6),
    ]
    return Design(blend(muzzle, nose, k=1.2), (nose, *mouth))


def cat() -> Design:
    cheeks = both(lambda s: Ell((s * 9.0, -2.0, 0), (13.0, 12.0, 9.0)))
    bridge = Ell((0, 7.0, 0), (8.0, 10.0, 7.0))
    chin = Ell((0, -11.0, 0), (7.0, 7.0, 6.0))
    face = blend(*cheeks, bridge, chin, k=3.0)
    nose = Ell(_on(face, 0, 7.5, -1.0), (3.8, 2.8, 2.2))
    mouth = [
        *_stroke(face, [(0, 5.0), (0, 1.5)], 1.4),
        *_stroke(face, [(-5.6, 1.4), (-2.8, -0.6), (0, 1.5), (2.8, -0.6), (5.6, 1.4)], 1.4),
    ]
    return Design(blend(face, nose, k=1.0), (nose, *mouth))


def bunny() -> Design:
    cheeks = both(lambda s: Ell((s * 8.5, -1.0, 0), (12.5, 12.0, 9.5)))
    bridge = Ell((0, 8.0, 0), (8.0, 9.0, 7.0))
    chin = Ell((0, -12.0, 0), (6.0, 6.0, 4.0))
    face = blend(*cheeks, bridge, chin, k=3.0)
    teeth = both(lambda s: Ell((s * 2.0, -12.5, 0), (2.6, 5.5, 5.0)))
    nose = Ell(_on(face, 0, 8.5, -1.0), (3.4, 2.6, 2.2))
    mouth = [
        *_stroke(face, [(0, 6.4), (0, 3.0)], 1.3),
        *_stroke(face, [(-5.0, 2.8), (-2.5, 0.8), (0, 3.0), (2.5, 0.8), (5.0, 2.8)], 1.3),
    ]
    return Design(blend(face, *teeth, nose, k=1.0), (nose, *mouth), tuple(teeth))


def unicorn() -> Design:
    muzzle = Ell((0, -3.0, 0), (23.0, 17.0, 10.0))
    nostrils = both(lambda s: Ell(_on(muzzle, s * 4.2, 4.5, -0.5), (1.0, 1.5, 0.9)))
    smile = _stroke(muzzle, [(-5.0, -11.0), (-2.0, -12.6), (2.0, -12.6), (5.0, -11.0)], 1.4)
    return Design(muzzle, (*nostrils, *smile))


def frog() -> Design:
    lips = Ell((0, -4.0, 0), (26.0, 17.0, 8.0))
    points = [(-17.0 + 34.0 * i / 8, -4.0 - 7.0 * math.sin(math.pi * i / 8)) for i in range(9)]
    smile = _stroke(lips, points, 1.8)
    nostrils = both(lambda s: Ell(_on(lips, s * 4.5, 7.0, -0.6), (1.6, 1.4, 1.2)))
    return Design(lips, (*smile, *nostrils))


DESIGNS: dict[str, Callable[[], Design]] = {
    "bear": bear,
    "cat": cat,
    "bunny": bunny,
    "unicorn": unicorn,
    "frog": frog,
}


def _relief(shape: Shape) -> Manifold:
    """The snout with a flat back at z = 0 and nothing steeper than 45° (it is a relief)."""
    b = shape.bounds()
    lo = [b[0] - 2.0, b[1] - 2.0, -STEP]
    hi = [b[3] + 2.0, b[4] + 2.0, b[5] + 2.0]
    axes = grid_axes(lo, hi, STEP)
    x, y, z = np.meshgrid(*axes, indexing="ij")
    field = np.minimum(-shape.sdf((x, y, z)), z)
    for k in range(field.shape[2] - 2, -1, -1):
        field[:, :, k] = np.maximum(field[:, :, k], field[:, :, k + 1] - STEP)
    field = np.minimum(field, z)
    return mesh_field(field, axes, STEP).simplify(0.03)


@cache  # five snouts; the solids are immutable
def _sculpt(character: Character) -> tuple[Design, Manifold, Manifold, CrossSection]:
    """The design, the snout, its details and where no sound hole may go."""
    design = DESIGNS[character]()
    solid = _relief(design.shape)
    details = drop_slivers(solid ^ region_mesh(design.details))
    closed = details.project().offset(1.0)
    if design.closed:
        closed = closed + region_mesh(design.closed).project().offset(0.6)
    return design, solid, details, closed


def _slope(shape: Shape, x: float, y: float) -> tuple[float, float]:
    """How steeply the surface rises along x and y at (x, y)."""
    d = 0.5
    gx = (_height(shape, x + d, y) - _height(shape, x - d, y)) / (2 * d)
    gy = (_height(shape, x, y + d) - _height(shape, x, y - d)) / (2 * d)
    return gx, gy


def _sound_hole(shape: Shape, hole: CrossSection) -> Manifold | None:
    """``hole`` through the snout, leaning along the surface normal: at the back it lines up
    with the panel's grille, at the front it comes out square to the surface (no knife edge).
    None where the surface is steeper than ``MAX_LEAN``: the hole's walls would hang over."""
    x0, y0, x1, y1 = bounds(hole)
    gx, gy = _slope(shape, (x0 + x1) / 2, (y0 + y1) / 2)
    if math.hypot(gx, gy) > math.tan(math.radians(MAX_LEAN)):
        return None
    straight = prism(hole, -1.0, 40.0)
    # sideways per millimetre up, along the normal (-gx, -gy, 1)
    shear: Any = [[1.0, 0.0, -gx, 0.0], [0.0, 1.0, -gy, 0.0], [0.0, 0.0, 1.0, 0.0]]
    return straight.transform(np.array(shear))


def face(character: Character, speaker_r: float) -> Face:
    """The snout for ``character`` in front of a speaker of radius ``speaker_r``.

    Behind the snout the panel has one opening (``Face.opening``), inside the speaker's rim and
    with a rim of panel left to glue on; the snout's own sound holes lie in front of it, round
    holes on rings, wherever the surface is flat enough and away from nose and mouth."""
    design, solid, details, closed = _sculpt(character)
    footprint = solid.slice(0.05)
    opening = CrossSection.circle(speaker_r - SPEAKER_RIM, 64) ^ footprint.offset(-GLUE_RIM)
    allowed = (footprint.offset(-RIM) ^ opening.offset(-0.5)) - closed
    holes = [
        h for h in patterns.grille("dots", speaker_r).decompose() if (h - allowed).area() < 1e-3
    ]
    cuts = [c for c in (_sound_hole(design.shape, h) for h in holes) if c is not None]
    if cuts:
        cut = Manifold.batch_boolean(cuts, OpType.Add)
        solid, details = solid - cut, details - cut
    return Face(drop_slivers(solid - details), details, footprint, opening)

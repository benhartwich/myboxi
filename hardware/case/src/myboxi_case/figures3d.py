"""Round figures for the NFC base ("Figur gestalten", docs/gehaeuse.md): a little bear, cat, …
modelled in 3D and printed upright in one piece with the base.

Modelled from soft shapes (ellipsoids, limbs) in millimetres for the 40 mm base: x to the
right, y to the back (the face looks to -y, towards the name), z up from the top of the base.
Shapes that hang over carry a smooth 45° cone below them (``blob``, ``limb``); whatever still
hangs over gets a stepped 45° fillet (``supported``). So the figure prints without supports.
Colours are flush regions of the surface: accents (belly, snout, inner ears) print with the
name's head, the face (eyes, nose, mouth) with its own.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from itertools import pairwise

import numpy as np
from manifold3d import CrossSection, Manifold

from myboxi_case.geom import bbox, box, prism, union
from myboxi_case.motifs import Motif

FACET = 0.7  # mm: facets this long keep curves and colour edges smooth
LAYER = 0.4  # fillet steps: 45°, two 0.2 mm layers each
ROOT = 0.4  # how far the figure reaches into the base
ROOT2 = math.sqrt(2.0)

Vec = tuple[float, float, float]
Shapes = tuple[list[Manifold], list[Manifold], list[Manifold]]  # solid, accents, details


@dataclass(frozen=True)
class Figure3D:
    body: Manifold  # the figure's colour
    accent: Manifold  # belly, snout, inner ears: the name's colour
    details: Manifold  # eyes, nose, mouth


# --- shapes ------------------------------------------------------------------------------------


def _sphere(r: float) -> Manifold:
    return Manifold.sphere(r, min(112, max(24, round(2 * math.pi * r / FACET / 4) * 4)))


def ellipsoid(c: Vec, r: Vec) -> Manifold:
    big = max(r)
    return _sphere(big).scale((r[0] / big, r[1] / big, r[2] / big)).translate(c)


def ball(c: Vec, r: float) -> Manifold:
    return _sphere(r).translate(c)


def _below(c: Vec, depth: float) -> Manifold:
    return ball((c[0], c[1], c[2] - depth), 0.05)


def blob(c: Vec, r: Vec) -> Manifold:
    """An ellipsoid on a 45° cone: its tip lies inside whatever carries it."""
    depth = math.sqrt(max(r[0], r[1]) ** 2 + r[2] ** 2)
    return Manifold.batch_hull([ellipsoid(c, r), _below(c, depth)])


def limb(a: Vec, b: Vec, r: float, r_b: float | None = None, *, cones: bool = True) -> Manifold:
    """A rounded limb from ``a`` to ``b`` (tapering to ``r_b``), on 45° cones (``cones``) or
    left to the fillets (an arm along the body)."""
    rb = r if r_b is None else r_b
    ends = [ball(a, r), ball(b, rb)]
    if cones:
        ends += [_below(a, r * ROOT2), _below(b, rb * ROOT2)]
    return Manifold.batch_hull(ends)


def surface(c: Vec, r: Vec, direction: Vec, inset: float = 0.0) -> Vec:
    """The point on the ellipsoid (centre ``c``, radii ``r``) in ``direction`` from its centre,
    moved ``inset`` towards the centre."""
    d = np.array(direction, dtype=np.float64)
    d /= np.linalg.norm(d)
    t = 1.0 / math.sqrt(float(np.sum((d / np.array(r)) ** 2))) - inset
    p = np.array(c) + t * d
    return (float(p[0]), float(p[1]), float(p[2]))


def line(c: Vec, r: Vec, directions: list[Vec], width: float) -> list[Manifold]:
    """A stroke on the ellipsoid's surface through ``directions`` (a mouth, whiskers)."""
    pts: list[Vec] = []
    for a, b in pairwise(directions):
        for k in range(4):
            t = k / 4
            pts.append(surface(c, r, tuple(x + (y - x) * t for x, y in zip(a, b, strict=True))))  # type: ignore[arg-type]
    pts.append(surface(c, r, directions[-1]))
    return [Manifold.batch_hull([ball(p, width / 2), ball(q, width / 2)]) for p, q in pairwise(pts)]


def supported(m: Manifold) -> Manifold:
    """``m`` plus stepped 45° fillets under whatever still hangs over.

    Walks down in ``LAYER`` steps: a layer needs the layer above it below, shrunk by one step
    (45°). Where the shape does not provide that, a step of fillet is added.
    """
    _, _, z0, _, _, z1 = bbox(m)
    above = CrossSection()
    steps: list[Manifold] = []
    z = z1 - LAYER
    while z > z0 - 1e-9:
        here = m.slice(z + LAYER / 2)
        if not above.is_empty():
            extra = above.offset(-LAYER) - here
            if extra.area() > 1e-3:
                steps.append(prism(extra, z - 0.01, z + LAYER + 0.01))
                here = here + extra
        above = here
        z -= LAYER
    return union([m, *steps])


# --- the figures -------------------------------------------------------------------------------

HEAD_C: Vec = (0.0, -0.5, 30.0)
HEAD_R: Vec = (11.0, 10.0, 10.5)


def _head(c: Vec = HEAD_C, r: Vec = HEAD_R) -> Manifold:
    return blob(c, r)


def _eyes(
    c: Vec = HEAD_C, r: Vec = HEAD_R, spread: float = 0.36, up: float = 0.12,
    size: Vec = (1.6, 1.6, 1.9),
) -> list[Manifold]:  # fmt: skip
    """Slightly domed: centred a little inside the surface."""
    return [
        ellipsoid(surface(c, r, (s * spread, -1.0, up), inset=size[1] - 0.6), size) for s in (-1, 1)
    ]


def _smile(
    c: Vec = HEAD_C, r: Vec = HEAD_R, z: float = -0.3, width: float = 0.17
) -> list[Manifold]:
    """A small "w" under the nose."""
    return [
        *line(c, r, [(0.0, -1.0, z), (-width / 2, -1.0, z - 0.1), (-width, -1.0, z - 0.02)], 1.0),
        *line(c, r, [(0.0, -1.0, z), (width / 2, -1.0, z - 0.1), (width, -1.0, z - 0.02)], 1.0),
    ]


def _body(feet: Vec = (4.0, 5.2, 3.6)) -> tuple[Manifold, Manifold]:
    """Round body, two feet in front, arms along the sides; returns the solid and the arms.

    The body is cut well above the bottom of its ellipsoid, so its sides stay steep enough."""
    torso = ellipsoid((0.0, 0.0, 8.0), (9.0, 7.6, 13.5))
    foot = [ellipsoid((s * 4.6, -4.2, 0.0), feet) for s in (-1, 1)]
    arms = union(
        [limb((s * 7.8, -0.6, 17.5), (s * 6.3, -3.2, 10.2), 3.0, cones=False) for s in (-1, 1)]
    )
    return union([torso, *foot, arms]), arms


def bear() -> Shapes:
    body, arms = _body()
    belly = ellipsoid((0.0, -7.4, 9.0), (5.0, 3.0, 6.2)) - arms
    ears = [blob((s * 7.8, 0.5, 38.0), (4.0, 4.0, 4.0)) for s in (-1, 1)]
    inner = [ellipsoid((s * 7.9, -3.4, 38.6), (2.6, 2.0, 2.6)) for s in (-1, 1)]
    snout = ellipsoid((0.0, -7.6, 28.5), (4.4, 3.0, 3.0))
    nose = ellipsoid((0.0, -10.4, 29.7), (1.8, 1.2, 1.1))
    mouth = [limb((0.0, -10.8, 28.8), (0.0, -10.5, 27.3), 0.5, cones=False)]
    return (
        [body, _head(), *ears, snout, nose],
        [belly, *inner, snout],
        [*_eyes(), nose, *mouth],
    )


def cat() -> Shapes:
    body, arms = _body()
    belly = ellipsoid((0.0, -7.4, 9.0), (5.0, 3.0, 6.2)) - arms
    ears = [
        Manifold.batch_hull([ball((s * 6.4, 0.0, 37.0), 3.8), ball((s * 8.6, 0.6, 44.2), 1.2)])
        for s in (-1, 1)
    ]
    inner = [
        Manifold.batch_hull([ball((s * 6.6, -3.1, 38.4), 1.9), ball((s * 8.3, -1.2, 42.6), 0.5)])
        for s in (-1, 1)
    ]
    tail = [
        limb((2.0, 5.0, 5.0), (7.0, 8.2, 9.0), 2.2),
        limb((7.0, 8.2, 9.0), (9.0, 7.2, 17.5), 2.2, 1.9),
    ]
    eyes = _eyes(size=(1.5, 1.5, 2.2))
    nose = ellipsoid(surface(HEAD_C, HEAD_R, (0.0, -1.0, -0.14), inset=0.6), (1.4, 1.0, 1.0))
    whiskers = [
        w
        for s in (-1, 1)
        for dz in (-0.18, -0.28)
        for w in line(
            HEAD_C,
            HEAD_R,
            [(s * 0.3, -1.0, dz), (s * 0.62, -0.8, dz + 0.04 * (1 if dz > -0.2 else -1))],
            0.8,
        )
    ]
    return (
        [body, _head(), *ears, *tail, *eyes, nose],
        [belly, *inner],
        [*eyes, nose, *_smile(z=-0.26, width=0.12), *whiskers],
    )


def bunny() -> Shapes:
    body, arms = _body()
    belly = ellipsoid((0.0, -7.4, 9.0), (5.0, 3.0, 6.2)) - arms
    ears = [limb((s * 4.2, 0.6, 37.0), (s * 5.6, 1.4, 52.5), 3.4, 3.0) for s in (-1, 1)]
    inner = [limb((s * 4.3, -2.6, 40.0), (s * 5.6, -1.7, 51.5), 1.7, 1.4) for s in (-1, 1)]
    tail = blob((0.0, 6.8, 7.5), (3.6, 3.6, 3.6))
    eyes = _eyes(spread=0.34, up=0.14, size=(1.8, 1.8, 2.1))
    nose = ellipsoid(surface(HEAD_C, HEAD_R, (0.0, -1.0, -0.12), inset=0.7), (1.5, 1.1, 1.0))
    teeth = [
        ellipsoid(surface(HEAD_C, HEAD_R, (s * 0.05, -1.0, -0.4), inset=0.5), (0.9, 0.8, 1.3))
        for s in (-1, 1)
    ]
    return (
        [body, _head(), *ears, tail, *eyes, nose, *teeth],
        [belly, *inner, tail, *teeth],
        [*eyes, nose, *_smile(z=-0.24, width=0.12)],
    )


FROG_C: Vec = (0.0, -0.5, 28.0)
FROG_R: Vec = (12.5, 10.5, 8.5)


def frog() -> Shapes:
    body, arms = _body(feet=(4.8, 5.8, 3.2))
    belly = ellipsoid((0.0, -7.4, 10.0), (5.6, 3.0, 7.0)) - arms
    bumps = [blob((s * 6.2, -3.0, 34.8), (4.4, 4.4, 4.4)) for s in (-1, 1)]
    whites = [ellipsoid((s * 6.2, -6.2, 36.4), (3.0, 2.4, 3.0)) for s in (-1, 1)]
    pupils = [ellipsoid((s * 6.2, -7.0, 36.8), (1.5, 1.2, 1.7)) for s in (-1, 1)]
    mouth = line(FROG_C, FROG_R, [(-0.62, -0.8, -0.12), (-0.3, -1.0, -0.3), (0.0, -1.0, -0.36),
                                  (0.3, -1.0, -0.3), (0.62, -0.8, -0.12)], 1.1)  # fmt: skip
    nostrils = [ball(surface(FROG_C, FROG_R, (s * 0.1, -1.0, 0.05)), 0.6) for s in (-1, 1)]
    return (
        [body, _head(FROG_C, FROG_R), *bumps, *pupils],
        [belly, *whites],
        [*pupils, *mouth, *nostrils],
    )


def unicorn() -> Shapes:
    body, arms = _body()
    belly = ellipsoid((0.0, -7.4, 9.0), (5.0, 3.0, 6.2)) - arms
    muzzle = ellipsoid((0.0, -7.4, 28.3), (5.4, 3.6, 3.4))
    nostrils = [ball((s * 1.8, -10.8, 28.9), 0.7) for s in (-1, 1)]
    ears = [
        Manifold.batch_hull([ball((s * 6.0, 1.2, 37.4), 2.8), ball((s * 7.4, 1.6, 42.8), 1.0)])
        for s in (-1, 1)
    ]
    horn = Manifold.batch_hull([ball((0.0, -5.4, 38.0), 2.9), ball((0.0, -7.6, 47.5), 1.1)])
    mane = [blob((0.0, 1.6 + 2.1 * k, 40.2 - 3.6 * k), (3.0, 2.6, 2.6)) for k in range(4)]
    eyes = [
        e
        for s in (-1, 1)
        for e in line(
            HEAD_C,
            HEAD_R,
            [(s * 0.46, -1.0, 0.16), (s * 0.36, -1.0, 0.1), (s * 0.26, -1.0, 0.16)],
            1.0,
        )
    ]  # fmt: skip — closed eyes, like the flat unicorn
    return (
        [body, _head(), muzzle, *ears, horn, *mane],
        [belly, muzzle, horn, *mane],
        [
            *eyes,
            *nostrils,
            *line(
                HEAD_C, HEAD_R, [(-0.1, -1.0, -0.42), (0.0, -1.0, -0.46), (0.1, -1.0, -0.42)], 0.9
            ),
        ],
    )


def person() -> Shapes:
    body, _ = _body(feet=(3.6, 5.0, 3.2))
    hair = ellipsoid((0.0, 3.0, 37.0), (14.0, 13.0, 10.5)) - ellipsoid(
        (0.0, -8.0, 30.0), (12.0, 7.0, 7.2)
    )
    eyes = _eyes(spread=0.33, up=0.06, size=(1.5, 1.5, 1.6))
    buttons = [ellipsoid((0.0, -7.2, z), (1.2, 1.0, 1.2)) for z in (13.0, 8.0)]
    return (
        [body, _head(), *eyes, *buttons],
        [hair],
        [*eyes, *buttons, *_smile(z=-0.3, width=0.2)],
    )


BUILDERS: dict[str, Callable[[], Shapes]] = {
    "bear": bear,
    "cat": cat,
    "bunny": bunny,
    "frog": frog,
    "unicorn": unicorn,
    "person": person,
}


def _clean(m: Manifold) -> Manifold:
    """Without the slivers that splitting into colours leaves behind."""
    return union([part for part in m.decompose() if part.volume() > 0.1])


@cache  # a few figures and sizes; the solids are immutable
def figure(motif: Motif, scale: float = 1.0) -> Figure3D:
    """The figure standing on z = 0 with a ``ROOT`` reaching into the base below (one solid
    when printed), colours as flush regions; ``scale`` for larger bases."""
    shapes, accents, details = BUILDERS[motif]()
    solid = supported(union(shapes)) ^ box(-60.0, -60.0, -ROOT, 60.0, 60.0, 100.0)
    detail = _clean(solid ^ union(details))
    accent = _clean((solid ^ union(accents)) - detail)
    parts = (_clean(solid - accent - detail), accent, detail)
    if scale != 1.0:
        parts = tuple(p.scale((scale, scale, scale)) for p in parts)  # type: ignore[assignment]
    return Figure3D(*parts)

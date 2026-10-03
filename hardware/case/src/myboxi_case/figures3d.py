"""Round figures for the NFC base ("Figur gestalten", docs/gehaeuse.md): a little bear, cat, …
standing on the base like a toy figure, modelled in 3D and printed upright in one piece with it.

The figures are soft shapes: signed distance fields of ellipsoids and round cones, blended into
each other like a sculpt (smooth minimum), sampled on a grid and turned into a mesh
(manifold3d level_set). Millimetres for the 40 mm base: x to the right, y to the back (the face
looks to -y, towards the name), z up from the top of the base.

Printed without supports: before meshing, every layer of the field is grown by the layer above
it, shrunk by one grid step per step down (45°). Wherever the shape hangs over more steeply,
this adds a smooth fillet. The figures are drawn so that every fillet lands on the figure below
it (tests: no layer starts in mid-air).

Standing in clothes, holding something against the belly. Colours are regions of the solid,
cut from it with the same soft shapes: white like the base (snout, what it holds), the clothes,
the fur, and dark details (eyes, nose, mouth, shoes, hair). Building a figure takes a
few seconds; the meshes are kept in memory and, with ``CACHE_DIR`` set, on disk.
"""

from __future__ import annotations

import hashlib
import math
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, cast

import numpy as np
import numpy.typing as npt
from manifold3d import Manifold, Mesh, OpType

from myboxi_case.motifs import Motif

STEP = 0.45  # grid of the figure's field (mm)
REGION_STEP = 0.2  # grid of the colour regions: fine, so their edges stay smooth
ROOT = 0.4  # how far the figure reaches into the base
SIMPLIFY = 0.03  # mm: flat areas need fewer triangles
CACHE_DIR: Path | None = None  # set by the server: built meshes survive restarts

Vec = tuple[float, float, float]
Box = tuple[float, float, float, float, float, float]
Field = npt.NDArray[np.float64]
Points = tuple[Field, Field, Field]


@dataclass(frozen=True)
class Figure3D:
    body: Manifold  # fur or skin: the figure's colour
    clothes: Manifold  # the accent colour
    white: Manifold  # snout, what it holds: the base's colour
    details: Manifold  # eyes, nose, mouth, shoes, hair: dark


# --- soft shapes ---------------------------------------------------------------------------------


class Shape:
    """A signed distance field, negative inside, with its bounds."""

    def sdf(self, p: Points) -> Field:
        raise NotImplementedError

    def bounds(self) -> Box:
        raise NotImplementedError


@dataclass(frozen=True)
class Ell(Shape):
    c: Vec
    r: Vec

    def sdf(self, p: Points) -> Field:
        x, y, z = p[0] - self.c[0], p[1] - self.c[1], p[2] - self.c[2]
        rx, ry, rz = self.r
        k0 = np.sqrt((x / rx) ** 2 + (y / ry) ** 2 + (z / rz) ** 2)
        k1 = np.sqrt((x / rx**2) ** 2 + (y / ry**2) ** 2 + (z / rz**2) ** 2)
        return k0 * (k0 - 1.0) / np.maximum(k1, 1e-9)

    def bounds(self) -> Box:
        c, r = self.c, self.r
        return (c[0] - r[0], c[1] - r[1], c[2] - r[2], c[0] + r[0], c[1] + r[1], c[2] + r[2])


def ball(c: Vec, r: float) -> Ell:
    return Ell(c, (r, r, r))


@dataclass(frozen=True)
class Cone(Shape):
    """A round cone: from ``a`` with radius ``r1`` to ``b`` with radius ``r2`` (a limb, an ear)."""

    a: Vec
    b: Vec
    r1: float
    r2: float

    def sdf(self, p: Points) -> Field:
        a, b = np.array(self.a), np.array(self.b)
        ba = b - a
        l2 = float(ba @ ba)
        rr = self.r1 - self.r2
        a2 = l2 - rr * rr
        il2 = 1.0 / l2
        px, py, pz = p[0] - a[0], p[1] - a[1], p[2] - a[2]
        y = px * ba[0] + py * ba[1] + pz * ba[2]
        z = y - l2
        cx, cy, cz = px * l2 - ba[0] * y, py * l2 - ba[1] * y, pz * l2 - ba[2] * y
        x2 = cx * cx + cy * cy + cz * cz
        y2, z2 = y * y * l2, z * z * l2
        k = math.copysign(rr * rr, rr) * x2
        middle = (np.sqrt(np.maximum(x2 * a2 * il2, 0.0)) + y * rr) * il2 - self.r1
        start = cast(
            Field, np.where(np.sign(y) * a2 * y2 < k, np.sqrt(x2 + y2) * il2 - self.r1, middle)
        )
        return cast(
            Field, np.where(np.sign(z) * a2 * z2 > k, np.sqrt(x2 + z2) * il2 - self.r2, start)
        )

    def bounds(self) -> Box:
        r = max(self.r1, self.r2)
        lo = [min(self.a[i], self.b[i]) - r for i in range(3)]
        hi = [max(self.a[i], self.b[i]) + r for i in range(3)]
        return (lo[0], lo[1], lo[2], hi[0], hi[1], hi[2])


@dataclass(frozen=True)
class Flat(Shape):
    """``inner`` squashed towards the plane y = ``cy`` (a plush heart held in front)."""

    inner: Shape
    cy: float
    f: float

    def sdf(self, p: Points) -> Field:
        return self.inner.sdf((p[0], self.cy + (p[1] - self.cy) / self.f, p[2])) * self.f

    def bounds(self) -> Box:
        b = self.inner.bounds()
        y0, y1 = self.cy + (b[1] - self.cy) * self.f, self.cy + (b[4] - self.cy) * self.f
        return (b[0], y0, b[2], b[3], y1, b[5])


@dataclass(frozen=True)
class Blend(Shape):
    """The union of ``parts``, blended over ``k`` mm like modelling clay (0: plain union)."""

    parts: tuple[Shape, ...]
    k: float = 0.0

    def sdf(self, p: Points) -> Field:
        d = self.parts[0].sdf(p)
        for part in self.parts[1:]:
            e = part.sdf(p)
            if self.k > 0:
                h = np.maximum(self.k - np.abs(d - e), 0.0) / self.k
                d = np.minimum(d, e) - h * h * self.k * 0.25
            else:
                d = np.minimum(d, e)
        return d

    def bounds(self) -> Box:
        boxes = [part.bounds() for part in self.parts]
        lo = [min(b[i] for b in boxes) for i in range(3)]
        hi = [max(b[i] for b in boxes) for i in range(3, 6)]
        return (lo[0], lo[1], lo[2], hi[0], hi[1], hi[2])


@dataclass(frozen=True)
class Cut(Shape):
    """``shape`` without ``away`` (a hairline)."""

    shape: Shape
    away: Shape

    def sdf(self, p: Points) -> Field:
        return np.maximum(self.shape.sdf(p), -self.away.sdf(p))

    def bounds(self) -> Box:
        return self.shape.bounds()


def blend(*parts: Shape, k: float = 0.0) -> Blend:
    return Blend(tuple(parts), k)


def both(make: Callable[[int], Shape]) -> list[Shape]:
    """Left and right: ``make(-1)`` and ``make(1)``."""
    return [make(-1), make(1)]


def heart(c: Vec, width: float, thick: float) -> Shape:
    """A plush heart standing upright, its face towards -y."""
    r = width / 4
    tip = (c[0], c[1], c[2] - width * 0.56)
    lobes = both(lambda s: Cone((c[0] + s * r * 1.02, c[1], c[2] + r * 0.6), tip, r, 0.8))
    return Flat(blend(*lobes, k=0.8), c[1], thick / (2 * r))


def star(c: Vec, r: float, thick: float, arm: float = 2.2) -> Shape:
    """A plush five-pointed star standing upright, one point up."""
    arms: list[Shape] = []
    for i in range(5):
        a = math.radians(90 + 72 * i)
        arms.append(Cone(c, (c[0] + r * math.cos(a), c[1], c[2] + r * math.sin(a)), arm, 0.95))
    return Flat(blend(*arms, k=1.6), c[1], thick / (2 * arm))


# --- the figures ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Design:
    shape: Shape
    white: tuple[Shape, ...]  # snout, cheeks, what it holds: the base's colour (white)
    clothes: tuple[Shape, ...]  # jacket, dress, mane: the accent colour
    details: tuple[Shape, ...]  # eyes, nose, mouth, shoes, hair: dark


HEAD_C: Vec = (0.0, -0.6, 34.5)
HEAD_R: Vec = (10.4, 9.4, 9.4)
HELD: Vec = (0.0, -8.9, 17.4)  # what the figure holds, against its belly


def _standing(
    hands: Vec = (4.0, -7.0, 16.2), dress: bool = False, jacket: bool = True
) -> tuple[Shape, list[Shape], list[Shape]]:
    """Legs, shoes, a round belly and arms reaching to ``hands`` in front of it; and the shoes
    and the clothes (a top open at the front, with sleeves; with a ``dress``, a skirt).

    The belly is round and low, so what the figure holds leans on it: nothing hangs in mid-air
    below its hands."""
    shoes = both(lambda s: Ell((s * 4.3, -2.2, 1.6), (3.9, 6.0, 2.8)))
    legs = both(lambda s: Cone((s * 3.7, 0.6, 3.0), (s * 3.5, 0.8, 13.0), 3.2, 3.6))
    torso = blend(
        Ell((0, 0.8, 19.0), (8.0, 6.8, 8.6)),
        Ell((0, -3.2, 14.8), (6.8, 5.6, 7.2)),
        Ell((0, -4.6, 25.4), (5.6, 4.6, 3.8)),  # the chest: carries the snout above it
        k=3,
    )
    hx, hy, hz = hands
    arms = both(lambda s: Cone((s * 7.2, 0.4, 24.5), (s * hx, hy + 0.4, hz + 0.3), 2.8, 2.5))
    hand = both(lambda s: ball((s * hx, hy, hz), 2.9))
    parts: list[Shape] = [torso, *legs, *arms, *hand]
    if dress:  # a skirt over the legs, widening down towards the shoes
        parts.append(Cone((0, 0.4, 14.0), (0, 0.4, 5.0), 7.4, 9.0))
    body = blend(blend(*parts, k=2.2), *shoes, k=1.2)
    shirt = blend(Ell((0, 0.8, 19.5), (8.8, 7.6, 8.8)), Ell((0, -4.6, 24.0), (6.4, 5.4, 4.6)))
    # a jacket stays open at the front, a shirt or a dress is closed
    top = Cut(shirt, Ell((0, -7.0, 16.0), (3.6, 5.0, 9.0))) if jacket else shirt
    sleeves = both(lambda s: Cone((s * 7.2, 0.4, 24.5), (s * 5.0, -4.6, 18.6), 3.4, 3.1))
    clothes: list[Shape] = [top, *sleeves]
    if dress:
        clothes.append(Cone((0, 0.4, 14.5), (0, 0.4, 4.6), 8.2, 9.8))
    return body, shoes, clothes


def _eyes(spread: float = 3.8, y: float = -8.6, z: float = 36.0, r: float = 1.5) -> list[Shape]:
    return both(lambda s: ball((s * spread, y, z), r))


def _hold(shape: Shape, held: Shape) -> Shape:
    """``held`` against the belly, blended into it so its lowest points hold on."""
    return blend(shape, held, k=2.2)


def bear() -> Design:
    body, shoes, clothes = _standing()
    ears = both(lambda s: Ell((s * 7.6, 0.6, 42.0), (4.0, 2.8, 4.0)))
    inner = both(lambda s: Ell((s * 7.6, -1.2, 42.2), (2.5, 2.2, 2.5)))
    muzzle = Ell((0, -8.6, 31.8), (4.6, 4.2, 3.6))
    nose = Ell((0, -12.4, 33.0), (2.2, 1.4, 1.5))
    eyes = _eyes()
    mouth = Cone((0, -12.6, 31.6), (0, -12.1, 30.2), 0.45, 0.45)
    held = star(HELD, 5.6, 3.4, arm=1.7)
    shape = blend(blend(body, Ell(HEAD_C, HEAD_R), k=3.0), *ears, muzzle, nose, k=1.4)
    white = (Ell((0, -7.4, 31.5), (4.8, 5.4, 3.8)), held, *inner)
    return Design(blend(_hold(shape, held), *eyes, k=0.6), white, tuple(clothes),
                  (*shoes, nose, *eyes, mouth))  # fmt: skip


def cat() -> Design:
    body, shoes, clothes = _standing()
    tail = blend(
        Cone((3.0, 6.0, 10.0), (8.0, 8.0, 16.0), 2.0, 1.8),
        Cone((8.0, 8.0, 16.0), (9.0, 6.0, 24.0), 1.8, 1.5),
        k=1.0,
    )
    head = Ell(HEAD_C, (10.8, 9.4, 9.2))
    ears = both(lambda s: Cone((s * 6.4, 0.2, 40.5), (s * 8.2, 0.6, 46.4), 4.2, 1.1))
    inner = both(lambda s: Cone((s * 6.6, -1.6, 41.2), (s * 7.9, -1.1, 45.2), 2.2, 0.6))
    cheeks = both(lambda s: Ell((s * 1.8, -9.0, 31.6), (2.4, 2.0, 1.9)))
    nose = Ell((0, -10.4, 33.2), (1.3, 0.9, 1.0))
    eyes = both(lambda s: Ell((s * 4.0, -8.4, 36.0), (1.4, 1.3, 2.0)))
    whiskers = [
        Cone((s * 4.0, -9.6, 32.0 + dz), (s * 9.0, -6.8, 32.6 + dz * 1.6), 0.45, 0.4)
        for s in (-1, 1)
        for dz in (-0.9, 0.9)
    ]
    held = heart((0, -9.3, 18.2), 10.6, 3.6)
    shape = blend(blend(blend(body, tail, k=2.0), head, k=3.0), *ears, *cheeks, nose, k=1.3)
    return Design(blend(_hold(shape, held), *eyes, k=0.6), (*cheeks, held, *inner),
                  tuple(clothes), (*shoes, nose, *eyes, *whiskers))  # fmt: skip


def bunny() -> Design:
    body, shoes, clothes = _standing(dress=True, jacket=False)
    ears = both(
        lambda s: Flat(Cone((s * 3.6, 0.4, 41.0), (s * 5.0, 1.4, 53.5), 3.2, 2.8), 1.0, 0.7)
    )
    inner = both(
        lambda s: Flat(Cone((s * 3.7, -1.6, 43.0), (s * 5.0, -0.8, 52.4), 1.8, 1.5), -1.6, 1.1)
    )
    cheeks = both(lambda s: Ell((s * 2.0, -8.8, 31.6), (2.6, 2.2, 2.1)))
    teeth = Ell((0, -10.0, 29.8), (1.4, 1.6, 1.2))
    nose = Ell((0, -10.6, 33.2), (1.3, 0.9, 1.0))
    eyes = _eyes(r=1.6)
    held = heart((0, -9.3, 18.2), 10.6, 3.6)
    shape = blend(blend(body, Ell(HEAD_C, (10.0, 9.2, 9.4)), k=3.0), *ears, *cheeks, nose, k=1.3)
    return Design(blend(_hold(shape, held), *eyes, k=0.6), (*cheeks, held, *inner, teeth),
                  tuple(clothes), (*shoes, nose, *eyes))  # fmt: skip


def frog() -> Design:
    body, shoes, clothes = _standing(hands=(4.2, -7.2, 15.4))
    head = Ell((0, -0.6, 32.6), (11.6, 9.6, 7.6))
    bumps = both(lambda s: ball((s * 5.8, -2.4, 38.4), 4.2))
    whites = both(lambda s: Ell((s * 5.8, -4.2, 39.0), (3.0, 3.2, 3.0)))
    pupils = both(lambda s: Ell((s * 5.8, -6.4, 39.4), (1.4, 1.0, 1.7)))
    mouth = [
        Cone((-5.0, -8.6, 31.0), (0, -10.2, 30.0), 0.5, 0.5),
        Cone((0, -10.2, 30.0), (5.0, -8.6, 31.0), 0.5, 0.5),
    ]
    belly = Ell((0, -4.0, 15.5), (4.0, 6.0, 7.5))  # a vest open over a light belly
    shape = blend(blend(body, head, k=3.0), *bumps, k=2.0)
    return Design(blend(shape, *pupils, k=0.6), (*whites, belly), tuple(clothes),
                  (*shoes, *pupils, *mouth))  # fmt: skip


def unicorn() -> Design:
    body, shoes, clothes = _standing()
    muzzle = Ell((0, -8.2, 31.4), (5.4, 4.6, 4.0))
    ears = both(lambda s: Cone((s * 5.8, 0.4, 41.6), (s * 7.2, 1.0, 46.6), 2.6, 0.9))
    horn = Cone((0, -4.6, 42.2), (0, -6.8, 51.4), 2.6, 0.8)
    mane = blend(
        Cone((0, -0.6, 44.0), (0, 4.6, 42.0), 2.8, 2.8),
        Cone((0, 4.6, 42.0), (0, 8.2, 36.0), 2.8, 2.4),
        Cone((0, 8.2, 36.0), (0, 6.8, 30.0), 2.4, 2.2),
        k=1.0,
    )
    nostrils = both(lambda s: Ell((s * 1.8, -12.4, 32.2), (0.7, 0.5, 0.8)))
    eyes = _eyes(spread=3.9, y=-8.4, z=36.2, r=1.4)
    held = star(HELD, 5.6, 3.4, arm=1.7)
    shape = blend(body, Ell(HEAD_C, (10.0, 9.2, 9.4)), k=3.0)
    shape = blend(shape, muzzle, *ears, horn, mane, k=1.4)
    return Design(blend(_hold(shape, held), *eyes, k=0.6), (muzzle, held), (*clothes, mane, horn),
                  (*shoes, *nostrils, *eyes))  # fmt: skip


def person() -> Design:
    body, shoes, clothes = _standing(jacket=False)
    head = ball((0, -0.4, 34.2), 9.8)
    nose = ball((0, -10.2, 33.0), 1.0)
    eyes = _eyes(spread=3.6, y=-9.0, z=34.6, r=1.3)
    smile = [  # on the head's surface, well below the nose
        Cone((-2.6, -9.05, 30.4), (0, -9.05, 29.6), 0.5, 0.5),
        Cone((0, -9.05, 29.6), (2.6, -9.05, 30.4), 0.5, 0.5),
    ]
    # hair: all of the head without the face and below the ears; both cuts cross it steeply
    hair = Cut(Cut(ball((0, -0.4, 34.2), 11.6), Ell((0, -8.6, 32.0), (8.6, 8.6, 7.8))),
               Ell((0, 0, 12.0), (40.0, 40.0, 17.0)))  # fmt: skip
    held = heart((0, -9.3, 18.2), 10.6, 3.6)
    shape = blend(blend(body, head, k=3.0), nose, k=1.0)
    return Design(blend(_hold(shape, held), *eyes, k=0.6), (held,), tuple(clothes),
                  (*shoes, *eyes, *smile, hair))  # fmt: skip


DESIGNS: dict[str, Callable[[], Design]] = {
    "bear": bear,
    "cat": cat,
    "bunny": bunny,
    "frog": frog,
    "unicorn": unicorn,
    "person": person,
}


# --- meshing -------------------------------------------------------------------------------------


def grid_axes(lo: Sequence[float], hi: Sequence[float], step: float) -> list[Field]:
    return [np.arange(lo[i], hi[i] + step, step) for i in range(3)]


def mesh_field(field: Field, axes: list[Field], step: float) -> Manifold:
    """The surface where ``field`` (positive inside, sampled on ``axes``) crosses zero."""
    nx, ny, nz = (int(n) for n in field.shape)
    values: list[float] = field.ravel().tolist()
    x0, y0, z0 = float(axes[0][0]), float(axes[1][0]), float(axes[2][0])
    inv = 1.0 / step
    sy, sx = nz, ny * nz

    def f(x: float, y: float, z: float) -> float:  # trilinear, called by manifold3d
        fx, fy, fz = (x - x0) * inv, (y - y0) * inv, (z - z0) * inv
        i, j, k = int(fx), int(fy), int(fz)
        if i < 0 or j < 0 or k < 0 or i >= nx - 1 or j >= ny - 1 or k >= nz - 1:
            return -1.0
        tx, ty, tz = fx - i, fy - j, fz - k
        q = i * sx + j * sy + k
        v: list[float] = values
        c00 = v[q] + (v[q + 1] - v[q]) * tz
        c01 = v[q + sy] + (v[q + sy + 1] - v[q + sy]) * tz
        c10 = v[q + sx] + (v[q + sx + 1] - v[q + sx]) * tz
        c11 = v[q + sx + sy] + (v[q + sx + sy + 1] - v[q + sx + sy]) * tz
        c0 = c00 + (c01 - c00) * ty
        return c0 + (c10 + (c11 - c10) * ty - c0) * tx

    hi = [float(axes[i][-1]) for i in range(3)]
    return Manifold.level_set(f, [x0, y0, z0, hi[0], hi[1], hi[2]], step)


def _solid(shape: Shape) -> Manifold:
    """The figure on the base, with 45° fillets under everything that hangs over."""
    b = shape.bounds()
    lo = [b[0] - 2.0, b[1] - 2.0, -ROOT - STEP]
    hi = [b[3] + 2.0, b[4] + 2.0, b[5] + 2.0]
    axes = grid_axes(lo, hi, STEP)
    x, y, z = np.meshgrid(*axes, indexing="ij")
    field = np.minimum(-shape.sdf((x, y, z)), z + ROOT)
    for k in range(field.shape[2] - 2, -1, -1):  # top down: a layer carries the one above
        field[:, :, k] = np.maximum(field[:, :, k], field[:, :, k + 1] - STEP)
    field = np.minimum(field, z + ROOT)
    return mesh_field(field, axes, STEP).simplify(SIMPLIFY)


def region_mesh(shapes: Sequence[Shape], margin: float = 0.25) -> Manifold:
    parts: list[Manifold] = []
    for shape in shapes:
        b = shape.bounds()
        lo = [b[i] - 1.0 - margin for i in range(3)]
        hi = [b[i + 3] + 1.0 + margin for i in range(3)]
        axes = grid_axes(lo, hi, REGION_STEP)
        x, y, z = np.meshgrid(*axes, indexing="ij")
        region = mesh_field(margin - shape.sdf((x, y, z)), axes, REGION_STEP)
        parts.append(region.simplify(SIMPLIFY))  # before cutting: colours share exact faces
    parts = [p for p in parts if not p.is_empty()]
    return Manifold.batch_boolean(parts, OpType.Add) if parts else Manifold()


def drop_slivers(m: Manifold) -> Manifold:
    """Without the slivers that splitting into colours leaves behind."""
    parts = [part for part in m.decompose() if part.volume() > 0.1]
    return Manifold.batch_boolean(parts, OpType.Add) if parts else Manifold()


def _build(motif: Motif) -> Figure3D:
    design = DESIGNS[motif]()
    solid = _solid(design.shape)
    detail = drop_slivers(solid ^ region_mesh(design.details))
    white = drop_slivers((solid ^ region_mesh(design.white)) - detail)
    clothes = drop_slivers((solid ^ region_mesh(design.clothes)) - detail - white)
    return Figure3D(drop_slivers(solid - clothes - white - detail), clothes, white, detail)


# --- cache ---------------------------------------------------------------------------------------

_SOURCE = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]


def _to_arrays(m: Manifold) -> tuple[npt.NDArray[np.float32], npt.NDArray[np.uint32]]:
    mesh: Any = m.to_mesh()
    verts = np.array(np.asarray(mesh.vert_properties)[:, :3], dtype=np.float32, order="C")
    return verts, np.array(np.asarray(mesh.tri_verts), dtype=np.uint32, order="C")


def _from_arrays(verts: npt.NDArray[np.float32], tris: npt.NDArray[np.uint32]) -> Manifold:
    if len(tris) == 0:
        return Manifold()
    return Manifold(Mesh(vert_properties=verts, tri_verts=tris))


def _cached(motif: Motif) -> Figure3D:
    path = CACHE_DIR / f"{motif}-{_SOURCE}.npz" if CACHE_DIR is not None else None
    if path is not None and path.exists():
        try:
            with np.load(path) as data:
                parts = [_from_arrays(data[f"{n}_v"], data[f"{n}_t"]) for n in ("b", "c", "w", "d")]
            return Figure3D(*parts)
        except (OSError, ValueError, KeyError):
            pass  # rebuilt below
    figure = _build(motif)
    if path is not None:
        arrays: dict[str, Any] = {}
        named = (("b", figure.body), ("c", figure.clothes), ("w", figure.white),
                 ("d", figure.details))  # fmt: skip
        for name, part in named:
            arrays[f"{name}_v"], arrays[f"{name}_t"] = _to_arrays(part)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.stem}.{os.getpid()}.npz")  # several server processes
        np.savez_compressed(tmp, **arrays)
        tmp.replace(path)
        for old in path.parent.glob(f"{motif}-*.npz"):  # built by an older version
            if old != path:
                old.unlink(missing_ok=True)
    return figure


@cache  # six figures; the solids are immutable
def _figure(motif: Motif) -> Figure3D:
    return _cached(motif)


def figure(motif: Motif, scale: float = 1.0) -> Figure3D:
    """The figure standing on z = 0 with a ``ROOT`` reaching into the base below (one solid
    when printed), colours as regions; ``scale`` for larger bases."""
    fig = _figure(motif)
    if scale == 1.0:
        return fig
    s = (scale, scale, scale)
    return Figure3D(*(part.scale(s) for part in (fig.body, fig.clothes, fig.white, fig.details)))

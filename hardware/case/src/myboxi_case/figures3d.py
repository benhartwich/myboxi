"""Round figures for the NFC base ("Figur gestalten", docs/gehaeuse.md): a little bear, cat, …
sitting on the base, modelled in 3D and printed upright in one piece with it.

The figures are soft shapes: signed distance fields of ellipsoids and round cones, blended into
each other like a sculpt (smooth minimum), sampled on a grid and turned into a mesh
(manifold3d level_set). Millimetres for the 40 mm base: x to the right, y to the back (the face
looks to -y, towards the name), z up from the top of the base.

Printed without supports: before meshing, every layer of the field is grown by the layer above
it, shrunk by one grid step per step down (45°). Wherever the shape hangs over more steeply,
this adds a smooth fillet. The figures are drawn so that every fillet lands on the figure below
it (tests: no layer starts in mid-air).

Colours are regions of the solid (accents: snout, soles, inner ears, what the figure holds;
details: eyes, nose, mouth), cut from it with the same soft shapes. Building a figure takes a
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
    body: Manifold  # the figure's colour
    accent: Manifold  # snout, soles, inner ears, what it holds: the name's colour
    details: Manifold  # eyes, nose, mouth


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
    lobes = both(lambda s: Cone((c[0] + s * r * 0.95, c[1], c[2] + r * 0.55), tip, r, 0.8))
    return Flat(blend(*lobes, k=1.2), c[1], thick / (2 * r))


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
    accents: tuple[Shape, ...]
    details: tuple[Shape, ...]


HEAD_C: Vec = (0.0, -1.0, 31.0)


def _sitting(
    hands: Vec = (5.0, -9.4, 15.0), feet: Vec = (4.2, 2.8, 4.4), holds: bool = True
) -> tuple[Shape, list[Shape]]:
    """Body, legs to the front and arms reaching to ``hands``; and the soles (accents), which
    reach deep into the feet so their edge is crisp. A figure that ``holds`` something gets a
    lap for it to rest on and a chest that carries the snout above it."""
    body = blend(
        Ell((0, 0.6, 12.0), (9.2, 8.3, 10.6)),
        Ell((0, 0.6, 6.4), (10.0, 8.9, 6.6)),
        *([Ell((0, -7.5, 5.0), (6.8, 6.2, 5.4)), Ell((0, -5.5, 20.0), (6.0, 6.0, 5.0))]
          if holds else []),
        k=3,
    )  # fmt: skip
    legs = both(
        lambda s: blend(
            Cone((s * 5.6, -1.0, 5.0), (s * 7.0, -9.0, 4.2), 5.0, 4.3),
            Ell((s * 7.3, -11.4, 4.5), feet),
            k=1.5,
        )
    )
    arms = both(lambda s: Cone((s * 8.5, -1.0, 21.0), (s * hands[0], hands[1], hands[2]), 3.9, 3.3))
    soles = both(lambda s: Ell((s * 7.3, -11.2, 4.5), (feet[0] - 0.4, 4.2, feet[2] - 0.4)))
    return blend(body, *legs, *arms, k=3), soles


def bear() -> Design:
    body, soles = _sitting()
    head = Ell(HEAD_C, (12.0, 10.6, 11.0))
    ears = both(lambda s: Ell((s * 8.9, 0.4, 40.0), (4.7, 3.1, 4.7)))
    inner = both(lambda s: Ell((s * 8.9, -1.6, 40.2), (3.0, 2.4, 3.0)))
    muzzle = Ell((0, -10.4, 28.0), (5.4, 4.9, 4.2))
    nose = Ell((0, -15.0, 29.6), (2.5, 1.6, 1.8))
    eyes = both(lambda s: ball((s * 4.6, -10.0, 32.6), 1.7))
    mouth = [
        Cone((0, -15.1, 28.2), (0, -14.6, 26.6), 0.45, 0.45),
        *both(lambda s: Cone((0, -14.6, 26.6), (s * 1.6, -14.0, 26.1), 0.45, 0.45)),
    ]
    held = star((0, -12.0, 15.55), 7.4, 4.2)  # from the feet up to the chin
    shape = blend(blend(body, head, k=4), *ears, muzzle, nose, k=1.6)
    return Design(
        blend(shape, *eyes, held, k=0.6),
        (*soles, *inner, Ell((0, -9.0, 28.0), (5.6, 6.6, 4.4)), held),
        (nose, *eyes, *mouth),
    )


def cat() -> Design:
    body, soles = _sitting()
    tail = blend(
        Cone((5.0, 7.0, 3.0), (9.4, 1.0, 2.8), 2.4, 2.2),
        Cone((9.4, 1.0, 2.8), (9.6, -6.6, 2.8), 2.2, 1.9),
        k=1.0,
    )
    head = Ell((0, -0.6, 30.4), (12.8, 10.4, 10.4))
    ears = both(lambda s: Cone((s * 7.4, 0.2, 37.2), (s * 9.4, 0.8, 43.4), 4.8, 1.3))
    inner = both(lambda s: Cone((s * 7.6, -1.8, 38.2), (s * 9.1, -1.2, 42.4), 2.5, 0.7))
    cheeks = both(lambda s: Ell((s * 2.0, -10.4, 27.0), (2.6, 2.2, 2.1)))
    nose = Ell((0, -12.1, 28.9), (1.5, 1.0, 1.1))
    eyes = both(lambda s: Ell((s * 4.9, -9.4, 32.0), (1.6, 1.5, 2.3)))
    whiskers = [
        Cone((s * 4.6, -11.0, 27.4 + dz), (s * 10.4, -7.6, 28.0 + dz * 1.8), 0.5, 0.45)
        for s in (-1, 1)
        for dz in (-1.0, 1.0)
    ]
    held = heart((0, -12.0, 16.3), 13.0, 4.4)
    shape = blend(blend(body, tail, k=3), head, k=4)
    shape = blend(shape, *ears, *cheeks, nose, k=1.5)
    return Design(
        blend(shape, *eyes, held, k=0.6),
        (*soles, *inner, Ell((0, -8.6, 26.6), (5.0, 4.4, 3.2)), held),
        (nose, *eyes, *whiskers),
    )


def bunny() -> Design:
    body, soles = _sitting()
    tail = ball((0, 8.6, 5.0), 3.8)
    head = Ell((0, -0.8, 30.4), (11.6, 10.4, 10.8))
    ears = both(
        lambda s: Flat(Cone((s * 4.2, 0.4, 38.0), (s * 6.0, 1.6, 51.0), 3.6, 3.2), 1.0, 0.7)
    )
    inner = both(
        lambda s: Flat(Cone((s * 4.3, -2.0, 40.5), (s * 6.0, -1.0, 50.0), 2.0, 1.7), -2.0, 1.1)
    )
    cheeks = both(lambda s: Ell((s * 2.2, -10.4, 27.2), (3.0, 2.6, 2.5)))
    nose = Ell((0, -12.6, 29.0), (1.6, 1.0, 1.1))
    teeth = Ell((0, -11.0, 25.6), (1.6, 2.0, 1.4))  # a patch of colour below the nose
    eyes = both(lambda s: ball((s * 4.6, -9.6, 32.4), 1.8))
    held = heart((0, -12.0, 16.3), 13.0, 4.4)
    shape = blend(blend(body, tail, k=3), head, k=4)
    shape = blend(shape, *ears, *cheeks, nose, k=1.5)
    return Design(
        blend(shape, *eyes, held, k=0.6),
        (*soles, *inner, *both(lambda s: Ell((s * 2.2, -9.4, 27.2), (3.2, 3.6, 2.7))), teeth,
         tail, held),
        (nose, *eyes),
    )  # fmt: skip


def frog() -> Design:
    body, soles = _sitting(hands=(7.4, -7.4, 12.0), feet=(5.0, 3.0, 3.8), holds=False)
    head = Ell((0, -0.8, 27.6), (13.4, 10.8, 8.6))
    bumps = both(lambda s: ball((s * 6.6, -3.2, 34.2), 4.8))
    whites = both(lambda s: Ell((s * 6.6, -5.0, 35.0), (3.4, 3.6, 3.4)))
    pupils = both(lambda s: Ell((s * 6.6, -7.8, 35.6), (1.7, 1.1, 2.0)))
    mouth = [
        Cone((-6.0, -9.2, 25.4), (0, -11.4, 24.2), 0.55, 0.55),
        Cone((0, -11.4, 24.2), (6.0, -9.2, 25.4), 0.55, 0.55),
    ]
    nostrils = both(lambda s: ball((s * 1.4, -11.6, 27.6), 0.6))
    belly = Ell((0, -3.0, 10.8), (7.2, 7.2, 8.0))
    shape = blend(blend(body, head, k=4), *bumps, k=2)
    return Design(
        blend(shape, *pupils, k=0.6), (*soles, *whites, belly), (*pupils, *mouth, *nostrils)
    )


def unicorn() -> Design:
    body, soles = _sitting()
    head = Ell((0, -0.4, 31.0), (11.4, 10.4, 10.8))
    muzzle = Ell((0, -9.6, 28.2), (6.4, 5.4, 4.4))
    ears = both(lambda s: Cone((s * 6.6, 0.4, 38.6), (s * 8.4, 1.0, 44.4), 3.0, 0.9))
    horn = Cone((0, -5.6, 39.6), (0, -8.2, 50.0), 3.0, 0.9)
    mane = blend(
        Cone((0, -1.0, 42.4), (0, 5.0, 40.2), 3.2, 3.2),
        Cone((0, 5.0, 40.2), (0, 9.4, 34.0), 3.2, 2.8),
        Cone((0, 9.4, 34.0), (0, 7.5, 27.0), 2.8, 2.4),
        k=1.2,
    )
    nostrils = both(lambda s: Ell((s * 2.0, -14.8, 28.2), (0.8, 0.6, 0.9)))
    lashes = both(lambda s: Cone((s * 3.0, -10.2, 32.4), (s * 6.6, -9.4, 32.6), 0.55, 0.55))
    smile = Cone((-1.6, -14.6, 25.4), (1.6, -14.6, 25.4), 0.45, 0.45)
    held = star((0, -12.0, 15.55), 7.4, 4.2)
    shape = blend(body, head, k=4)
    shape = blend(shape, muzzle, *ears, horn, mane, k=1.6)
    return Design(
        blend(shape, held, k=0.6),
        (muzzle, horn, mane, held, *soles),
        (*nostrils, *lashes, smile),
    )


def person() -> Design:
    body, soles = _sitting(feet=(3.8, 3.2, 4.0))
    head = ball((0, -0.6, 30.8), 11.0)
    nose = ball((0, -11.6, 29.0), 1.2)
    eyes = both(lambda s: ball((s * 4.0, -10.6, 31.0), 1.5))
    smile = [
        Cone((-2.4, -10.8, 26.4), (0, -11.4, 25.4), 0.5, 0.5),
        Cone((0, -11.4, 25.4), (2.4, -10.8, 26.4), 0.5, 0.5),
    ]
    # all of the head above the neck, without the face: both cuts cross the head steeply
    hair = Cut(Cut(ball((0, -0.6, 30.8), 13.0), Ell((0, -9.5, 28.0), (9.0, 9.0, 8.4))),
               Ell((0, 0.0, 6.0), (40.0, 40.0, 19.0)))  # fmt: skip
    held = heart((0, -12.0, 16.3), 13.0, 4.4)
    shape = blend(blend(body, head, k=4), nose, k=1.0)
    return Design(blend(shape, *eyes, held, k=0.6), (hair, held, *soles), (*eyes, *smile))


DESIGNS: dict[str, Callable[[], Design]] = {
    "bear": bear,
    "cat": cat,
    "bunny": bunny,
    "frog": frog,
    "unicorn": unicorn,
    "person": person,
}


# --- meshing -------------------------------------------------------------------------------------


def _grid(lo: Sequence[float], hi: Sequence[float], step: float) -> list[Field]:
    return [np.arange(lo[i], hi[i] + step, step) for i in range(3)]


def _mesh(field: Field, axes: list[Field], step: float) -> Manifold:
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
    axes = _grid(lo, hi, STEP)
    x, y, z = np.meshgrid(*axes, indexing="ij")
    field = np.minimum(-shape.sdf((x, y, z)), z + ROOT)
    for k in range(field.shape[2] - 2, -1, -1):  # top down: a layer carries the one above
        field[:, :, k] = np.maximum(field[:, :, k], field[:, :, k + 1] - STEP)
    field = np.minimum(field, z + ROOT)
    return _mesh(field, axes, STEP).simplify(SIMPLIFY)


def _region(shapes: Sequence[Shape], margin: float = 0.25) -> Manifold:
    parts: list[Manifold] = []
    for shape in shapes:
        b = shape.bounds()
        lo = [b[i] - 1.0 - margin for i in range(3)]
        hi = [b[i + 3] + 1.0 + margin for i in range(3)]
        axes = _grid(lo, hi, REGION_STEP)
        x, y, z = np.meshgrid(*axes, indexing="ij")
        region = _mesh(margin - shape.sdf((x, y, z)), axes, REGION_STEP)
        parts.append(region.simplify(SIMPLIFY))  # before cutting: colours share exact faces
    parts = [p for p in parts if not p.is_empty()]
    return Manifold.batch_boolean(parts, OpType.Add) if parts else Manifold()


def _clean(m: Manifold) -> Manifold:
    """Without the slivers that splitting into colours leaves behind."""
    parts = [part for part in m.decompose() if part.volume() > 0.1]
    return Manifold.batch_boolean(parts, OpType.Add) if parts else Manifold()


def _build(motif: Motif) -> Figure3D:
    design = DESIGNS[motif]()
    solid = _solid(design.shape)
    detail = _clean(solid ^ _region(design.details))
    accent = _clean((solid ^ _region(design.accents)) - detail)
    return Figure3D(_clean(solid - accent - detail), accent, detail)


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
                parts = [_from_arrays(data[f"{n}_v"], data[f"{n}_t"]) for n in ("b", "a", "d")]
            return Figure3D(*parts)
        except (OSError, ValueError, KeyError):
            pass  # rebuilt below
    figure = _build(motif)
    if path is not None:
        arrays: dict[str, Any] = {}
        for name, part in (("b", figure.body), ("a", figure.accent), ("d", figure.details)):
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
    return Figure3D(fig.body.scale(s), fig.accent.scale(s), fig.details.scale(s))

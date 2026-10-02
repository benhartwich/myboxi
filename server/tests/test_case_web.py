"""Box gestalten: the public configurator page, preview mesh and print files (docs/gehaeuse.md)."""

from __future__ import annotations

import io
import re
import struct
import time
import zipfile
import zlib
from urllib.parse import parse_qsl

import httpx
import numpy as np
import pytest
from fastapi import FastAPI

from myboxi_case.config import CaseConfig
from myboxi_case.export import read_preview
from myboxi_server.auth import ratelimit
from myboxi_server.auth.ratelimit import Limit
from myboxi_server.domain import drawings
from myboxi_server.settings import Settings

from .helpers import login, make_tenant


async def test_page_is_public_and_keeps_the_choices(client: httpx.AsyncClient) -> None:
    r = await client.get("/gestalten?form=bear&name=Mia&color_body=braun")
    assert r.status_code == 200
    assert "Box gestalten" in r.text
    assert 'value="Mia"' in r.text
    assert 'name="form" value="bear" checked' in r.text
    assert 'name="color_body" value="braun" checked' in r.text
    assert (
        'href="/gestalten/druckdateien.zip?form=bear&amp;name=Mia&amp;color_body=braun"' in r.text
    )
    script = re.search(r'<script type="module" src="(/static/case\.js\?v=[0-9a-f]{10})">', r.text)
    assert script
    assert "default-src 'self'" in r.headers["content-security-policy"]
    # Without an order address the request form stays off.
    assert "/gestalten/anfrage" not in r.text
    assert 'href="/login"' in r.text  # public header


async def test_colours_follow_the_form_until_chosen(client: httpx.AsyncClient) -> None:
    r = await client.get("/gestalten?form=unicorn")
    assert 'name="color_body" value="weiss" checked' in r.text
    assert 'name="color_accent" value="sonne" checked' in r.text
    assert CaseConfig(form="unicorn").color("accent") == "#F2C66D"
    assert CaseConfig(form="unicorn", color_accent="rot").color("accent") == "#D64541"


async def test_page_for_signed_in_people(app: FastAPI, client: httpx.AsyncClient) -> None:
    t = await make_tenant(app)
    await login(client, t.owner_email)
    r = await client.get("/gestalten")
    assert r.status_code == 200
    assert "Abmelden" in r.text


async def test_page_explains_bad_choices(client: httpx.AsyncClient) -> None:
    r = await client.get("/gestalten?name=" + "x" * 20)
    assert r.status_code == 422
    assert "höchstens 14 Zeichen" in r.text
    r = await client.get("/gestalten?form=castle")
    assert r.status_code == 422
    assert "Diese Auswahl gibt es nicht" in r.text
    r = await client.get("/gestalten?form=cube&power=powerbank")
    assert r.status_code == 200
    assert "Powerbank passt nur ins Radio" in r.text
    assert 'aria-disabled="true"' in r.text


async def test_preview_mesh_with_etag(client: httpx.AsyncClient) -> None:
    r = await client.get("/gestalten/vorschau?form=cube&name=Jonas")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/octet-stream"
    assert r.headers["content-encoding"] == "gzip"
    digest = CaseConfig(form="cube", name="Jonas").digest()
    assert r.headers["etag"] == f'"{digest}"'
    info, meshes = read_preview(r.content)  # httpx has decoded the gzip body
    assert info["size"] == [110.0, 110.0, 100.0]
    assert len(meshes) > 5
    again = await client.get(
        "/gestalten/vorschau?form=cube&name=Jonas", headers={"If-None-Match": f'"{digest}"'}
    )
    assert again.status_code == 304


async def test_preview_refuses_bad_input(client: httpx.AsyncClient) -> None:
    r = await client.get("/gestalten/vorschau?form=cube&power=powerbank")
    assert r.status_code == 422
    assert "Powerbank" in r.text
    r = await client.get("/gestalten/vorschau?speaker=33")
    assert r.status_code == 422


async def test_download_zip(client: httpx.AsyncClient) -> None:
    r = await client.get("/gestalten/druckdateien.zip?form=bear&name=Mäxi")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert r.headers["content-disposition"] == 'attachment; filename="myboxi-bear-maexi.zip"'
    # never from the browser's cache: the files change with the generator, the address not
    assert r.headers["cache-control"] == "no-store"
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert "myboxi-bear-maexi.3mf" in names
    assert "snapmaker-u1/myboxi-bear-maexi.3mf" in names  # the colours for the U1
    # links already sent keep working
    old = await client.get("/gestalten/download.zip?form=bear&name=Mäxi")
    assert old.status_code == 200
    assert old.headers["cache-control"] == "no-store"
    readme = zipfile.ZipFile(io.BytesIO(r.content)).read("LIESMICH.txt").decode()
    assert "/gestalten?form=bear&name=M%C3%A4xi" in readme


async def test_builds_are_rate_limited_but_cache_hits_are_free(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ratelimit, "CASE_BUILD_PER_IP", (Limit(2, 3600),))
    assert (await client.get("/gestalten/vorschau?name=A")).status_code == 200
    assert (await client.get("/gestalten/vorschau?name=B")).status_code == 200
    blocked = await client.get("/gestalten/vorschau?name=C")
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers
    assert (await client.get("/gestalten/vorschau?name=A")).status_code == 200  # cached


async def test_static_files_are_versioned(client: httpx.AsyncClient) -> None:
    """A stale cached stylesheet once made the preview canvas grow without end."""
    page = (await client.get("/gestalten")).text
    css = re.search(r'href="(/static/app\.css\?v=[0-9a-f]{10})"', page)
    assert css
    r = await client.get(css.group(1))
    assert r.headers["cache-control"] == "public, max-age=31536000, immutable"
    plain = await client.get("/static/app.css")
    assert plain.headers["cache-control"] == "no-cache"
    vendor = await client.get("/static/vendor/three-0.186.1/OrbitControls.js")
    assert "immutable" in vendor.headers["cache-control"]


async def test_public_header_links_to_the_configurator(client: httpx.AsyncClient) -> None:
    r = await client.get("/login")
    assert 'href="/gestalten"' in r.text


# --- Figur gestalten --------------------------------------------------------------------------


async def test_figure_page_is_public_and_keeps_the_choices(client: httpx.AsyncClient) -> None:
    r = await client.get("/gestalten/figur?shape=heart&top=bricks&name=Mia")
    assert r.status_code == 200
    assert 'name="shape" value="heart" checked' in r.text
    assert 'name="top" value="bricks" checked' in r.text
    assert 'value="Mia"' in r.text
    assert 'data-page="/gestalten/figur"' in r.text
    assert "/gestalten/figur/druckdateien.zip?shape=heart&amp;top=bricks&amp;name=Mia" in r.text
    assert "/static/figure/heart.png?v=" in r.text
    bad = await client.get("/gestalten/figur?shape=dragon")
    assert bad.status_code == 422
    assert "Diese Auswahl gibt es nicht" in bad.text


async def test_figure_preview_and_download(client: httpx.AsyncClient) -> None:
    r = await client.get("/gestalten/figur/vorschau?shape=star&top=bricks")
    assert r.status_code == 200
    assert r.headers["content-encoding"] == "gzip"
    info, meshes = read_preview(r.content)
    assert info["version"] == "figure 2"
    assert len(meshes) == 1
    r = await client.get("/gestalten/figur/download.zip?top=flat&shape=round&name=Lotta")
    assert r.status_code == 200
    assert r.headers["content-disposition"] == (
        'attachment; filename="myboxi-figur-round-lotta.zip"'
    )
    archive = zipfile.ZipFile(io.BytesIO(r.content))
    assert "myboxi-figur-round-lotta.3mf" in archive.namelist()
    readme = archive.read("LIESMICH.txt").decode()
    assert "Druckpause bei 2,2 mm" in readme
    assert "/gestalten/figur?top=flat&name=Lotta" in readme
    too_long = await client.get("/gestalten/figur/vorschau?name=Maximiliane")
    assert too_long.status_code == 422
    assert "höchstens 10 Zeichen" in too_long.text


async def test_figures_and_boxes_link_to_the_figure_designer(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    assert 'href="/gestalten/figur"' in (await client.get("/gestalten")).text
    t = await make_tenant(app)
    await login(client, t.owner_email)
    assert 'href="/gestalten/figur"' in (await client.get(f"/t/{t.tenant_id}/figures")).text
    assert 'href="/gestalten/figur"' in (await client.get(f"/t/{t.tenant_id}/boxes")).text


async def test_figure_from_the_collection(client: httpx.AsyncClient) -> None:
    page = await client.get("/gestalten/figur?top=standee&motif=frog")
    assert page.status_code == 200
    assert 'name="motif" value="frog" checked' in page.text
    assert 'data-show-when="top=figure|standee">' in page.text  # visible without JavaScript
    assert "/static/figure/motif-frog.png?v=" in page.text
    # the page starts with a round figure, so the collection is the first thing to see
    plain = await client.get("/gestalten/figur")
    assert 'name="top" value="figure" checked' in plain.text
    assert 'data-show-when="top=figure|standee">' in plain.text
    assert "/static/figure/motif3d-bear.png?v=" in plain.text
    assert 'data-show-when="top=standee" hidden' in plain.text  # the drawing: flat only
    flat = await client.get("/gestalten/figur?top=flat")
    assert 'data-show-when="top=figure|standee" hidden' in flat.text
    r = await client.get("/gestalten/figur/vorschau?top=standee&motif=frog")
    info, meshes = read_preview(r.content)
    assert [m["key"] for m in info["meshes"]] == [  # type: ignore[index]
        "figure", "figure_tile", "figure_tile_inlay",
    ]  # fmt: skip
    assert len(meshes) == 3
    r = await client.get("/gestalten/figur/download.zip?top=standee&motif=frog&name=Ida")
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert "stl/myboxi-figur-frog-ida-figur.stl" in names


def _png(gray: np.ndarray) -> bytes:
    """A grey PNG, enough for the upload (no extra dependency)."""
    h, w = gray.shape
    raw = b"".join(b"\x00" + gray[row].tobytes() for row in range(h))

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw))
        + (chunk(b"IEND", b""))
    )


async def _csrf(client: httpx.AsyncClient) -> str:
    page = await client.get("/gestalten/figur?top=standee&motif=drawing")
    m = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert m is not None
    return m.group(1)


async def test_a_drawing_becomes_a_figure(client: httpx.AsyncClient, settings: Settings) -> None:
    from myboxi_case.trace import SIZE

    yy, xx = np.mgrid[0:SIZE, 0:SIZE]
    gray = np.full((SIZE, SIZE), 230, dtype=np.uint8)
    gray[np.abs(np.hypot(xx - 200, yy - 150) - 60) < 3] = 30  # a face …
    gray[(np.hypot(xx - 180, yy - 140) < 7) | (np.hypot(xx - 220, yy - 140) < 7)] = 30
    gray[210:330, 197:203] = 30  # … on a body
    csrf = await _csrf(client)
    r = await client.post(
        "/gestalten/figur/zeichnung",
        data={"csrf_token": csrf, "query": "shape=heart&name=Ida"},
        files={"file": ("zeichnung.png", _png(gray), "image/png")},
    )
    assert r.status_code == 303, r.text
    location = r.headers["location"]
    assert location.startswith("/gestalten/figur?")
    query = dict(parse_qsl(location.split("?", 1)[1]))
    assert query["shape"] == "heart"  # the other choices stay
    assert query["name"] == "Ida"
    assert query["motif"] == "drawing"
    drawing_id = query["drawing"]
    stored = settings.data_dir / "drawings" / f"{drawing_id}.json"
    assert stored.exists()  # only the strokes …
    assert b"PNG" not in stored.read_bytes()  # … never the photo
    page = await client.get(location)
    assert 'name="turn"' in page.text
    preview = await client.get(location.replace("/gestalten/figur?", "/gestalten/figur/vorschau?"))
    assert preview.status_code == 200
    archive = await client.get(
        location.replace("/gestalten/figur?", "/gestalten/figur/download.zip?")
    )
    assert archive.status_code == 200
    # after 7 days the strokes are gone, and the page says so
    assert drawings.purge(settings.data_dir, now=time.time() + 8 * 86400) >= 1
    gone = await client.get(location.replace("/gestalten/figur?", "/gestalten/figur/vorschau?"))
    assert gone.status_code == 422
    assert "nicht mehr gespeichert" in gone.text


async def test_only_photos_are_accepted(client: httpx.AsyncClient) -> None:
    csrf = await _csrf(client)
    for name, data in (
        ("a.gif", b"GIF89a" + b"\0" * 100),
        ("leer.png", _png(np.full((64, 64), 230, dtype=np.uint8))),
    ):
        r = await client.post(
            "/gestalten/figur/zeichnung",
            data={"csrf_token": csrf},
            files={"file": (name, data, "application/octet-stream")},
        )
        assert r.status_code == 422
        assert "JPEG, PNG oder WebP" in r.text or "keine Zeichnung" in r.text


async def test_a_round_figure_in_one_piece(client: httpx.AsyncClient) -> None:
    r = await client.get("/gestalten/figur/vorschau?motif=unicorn&name=Ida")
    assert r.status_code == 200
    info, _ = read_preview(r.content)
    keys = [m["key"] for m in info["meshes"]]  # type: ignore[index]
    assert keys == ["figure", "figure_inlay", "figure3d", "figure3d_accent", "figure3d_details"]
    r = await client.get("/gestalten/figur/druckdateien.zip?motif=unicorn&name=Ida")
    assert r.headers["cache-control"] == "no-store"
    archive = zipfile.ZipFile(io.BytesIO(r.content))
    assert "myboxi-figur-unicorn-ida.3mf" in archive.namelist()
    assert "Kopf 2: Name und Akzente – Rosa" in archive.read("LIESMICH.txt").decode()
    assert "snapmaker-u1/myboxi-figur-unicorn-ida.3mf" in archive.namelist()
    # a drawing only stands flat
    r = await client.get("/gestalten/figur/vorschau?motif=drawing&drawing=0123456789abcdef")
    assert r.status_code == 422
    assert "nur als Aufsteller" in r.text

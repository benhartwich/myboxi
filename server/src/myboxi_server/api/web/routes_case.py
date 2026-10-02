"""Box gestalten: public case configurator with 3D preview and print files (docs/gehaeuse.md).

Outside the device protocol (SPEC scope). No login needed; the configuration lives in the URL.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Annotated, Any
from urllib.parse import parse_qsl, urlencode

import anyio
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import PlainTextResponse, RedirectResponse, Response
from markupsafe import Markup, escape
from pydantic import ValidationError

from myboxi_case import motifs, trace
from myboxi_case.config import MAX_NAME, PALETTE, ROLES, SUGGESTED, CaseConfig
from myboxi_case.export import file_stem
from myboxi_case.figures import MAX_LABEL, FigureConfig, FigureError
from myboxi_case.figures import SUGGESTED as FIGURE_SUGGESTED
from myboxi_case.figures import file_stem as figure_stem
from myboxi_case.layout import LayoutError, layout_for
from myboxi_server.api.web.deps import (
    DbSession,
    OptionalSession,
    SettingsDep,
    client_ip,
    csrf_protect,
)
from myboxi_server.api.web.render import render
from myboxi_server.auth import ratelimit
from myboxi_server.auth.mail import log_mail
from myboxi_server.auth.sessions import SessionInfo
from myboxi_server.domain import case_requests, drawings
from myboxi_server.domain.case_builds import CaseBuilds
from myboxi_server.domain.case_mails import confirm_mail, notify_mails
from myboxi_server.domain.errors import DomainError, NotFoundError
from myboxi_server.jobs.mail import send_case_confirmation, send_case_notification
from myboxi_server.settings import Settings

router = APIRouter(dependencies=[Depends(csrf_protect)])

FORMS = (
    ("radio", "Radio", "Breit, Lautsprecher links, Name rechts."),
    ("cube", "Würfel", "Kompakt und schlicht, 11 cm Kantenlänge."),
    ("bear", "Bär", "Runde Ohren, Schnauze um den Lautsprecher."),
    ("unicorn", "Einhorn", "Gedrehtes Horn, spitze Ohren, schlafende Augen."),
    ("cat", "Katze", "Spitze Ohren und Schnurrhaare."),
    ("bunny", "Hase", "Lange Ohren und kleine Zähne."),
    ("frog", "Frosch", "Glubschaugen oben und ein breites Lächeln."),
)
GRILLES = (("dots", "Punkte"), ("stars", "Sterne"), ("hearts", "Herzen"), ("lines", "Streifen"))
COLOR_ROLES = (
    ("color_body", "body", "Gehäuse"),
    ("color_front", "front", "Front"),
    ("color_accent", "accent", "Name und Symbole"),
)


def _message(exc: ValidationError) -> str:
    for err in exc.errors():
        field = str(err["loc"][0]) if err["loc"] else ""
        if field == "name":
            if err["type"] in ("string_too_long", "too_long"):
                return f"Der Name darf höchstens {MAX_NAME} Zeichen haben."
            return str(err["msg"]).removeprefix("Value error, ")
    return "Diese Auswahl gibt es nicht. Bitte wähle aus den Optionen."


def _parse(request: Request) -> CaseConfig:
    """Raises ValidationError."""
    return CaseConfig.from_query(dict(request.query_params))


def _query(cfg: CaseConfig) -> str:
    return urlencode(cfg.query())


def _page_url(request: Request, cfg: CaseConfig) -> str:
    base = request.app.state.settings.base_url.rstrip("/")
    query = _query(cfg)
    return f"{base}/gestalten" + (f"?{query}" if query else "")


@router.get("/gestalten")
async def case_page(request: Request, session: OptionalSession, settings: SettingsDep) -> Response:
    error: str | None = None
    try:
        cfg = _parse(request)
    except ValidationError as exc:
        cfg, error = CaseConfig(), _message(exc)
    layout_error: str | None = None
    try:
        layout_for(cfg)
    except LayoutError as exc:
        layout_error = str(exc)
    defaults = CaseConfig().model_dump(mode="json")
    context: dict[str, Any] = {
        "cfg": cfg,
        "query": _query(cfg),
        "defaults_json": json.dumps(defaults, separators=(",", ":")),
        "suggested_json": json.dumps(
            {
                form: dict(zip(("color_body", "color_front", "color_accent"), colors, strict=True))
                for form, colors in SUGGESTED.items()
            },
            separators=(",", ":"),
        ),
        "chosen": {role: cfg.color_key(role) for role in ROLES},
        "forms": FORMS,
        "form_label": next(label for key, label, _ in FORMS if key == cfg.form),
        "grilles": GRILLES,
        "color_roles": COLOR_ROLES,
        "palette": PALETTE,
        "max_name": MAX_NAME,
        "error": error,
        "layout_error": layout_error,
        "orders": settings.order_notify_email is not None,
        "docs_url": settings.docs_url.rstrip("/"),
    }
    return render(request, "case.html", context, session=session, status_code=422 if error else 200)


async def _count_build(request: Request, settings: SettingsDep) -> Response | None:
    try:
        await ratelimit.hit(
            request.app.state.engine,
            f"case_build:ip:{client_ip(request, settings)}",
            ratelimit.CASE_BUILD_PER_IP,
        )
    except ratelimit.RateLimitedError as exc:
        return PlainTextResponse(
            "Zu viele Vorschauen in kurzer Zeit. Bitte warte einen Moment.",
            status_code=429,
            headers={"Retry-After": str(exc.retry_after)},
        )
    return None


@router.get("/gestalten/vorschau")
async def case_preview(request: Request, settings: SettingsDep) -> Response:
    """Binary preview mesh (hardware/case export.preview), gzip-encoded, cached by digest."""
    try:
        cfg = _parse(request)
    except ValidationError as exc:
        return PlainTextResponse(_message(exc), status_code=422)
    etag = f'"{cfg.digest()}"'
    headers = {"ETag": etag, "Cache-Control": "private, max-age=86400", "Vary": "Accept-Encoding"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    builds: CaseBuilds = request.app.state.case_builds
    if not builds.cached("preview", cfg) and (limited := await _count_build(request, settings)):
        return limited
    try:
        data = await builds.preview(cfg)
    except LayoutError as exc:
        return PlainTextResponse(str(exc), status_code=422)
    return Response(
        data,
        media_type="application/octet-stream",
        headers=headers | {"Content-Encoding": "gzip"},
    )


@router.get("/gestalten/download.zip")
async def case_download(request: Request, settings: SettingsDep) -> Response:
    try:
        cfg = _parse(request)
    except ValidationError as exc:
        return PlainTextResponse(_message(exc), status_code=422)
    builds: CaseBuilds = request.app.state.case_builds
    if not builds.cached("bundle", cfg) and (limited := await _count_build(request, settings)):
        return limited
    try:
        data = await builds.bundle(cfg, _page_url(request, cfg))
    except LayoutError as exc:
        return PlainTextResponse(str(exc), status_code=422)
    return Response(
        data,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{file_stem(cfg)}.zip"',
            "Cache-Control": "private, max-age=86400",
        },
    )


# --- Figur gestalten: figure bases with an enclosed NFC chip --------------------------------------

FIGURE_SHAPES = (
    ("round", "Rund", "Der Klassiker, passt auf jede Box."),
    ("square", "Eckig", "Viel Platz für Noppen."),
    ("heart", "Herz", "Mit abgerundeter Spitze."),
    ("star", "Stern", "Mit runden Zacken."),
)
# key, label, text, picture
FIGURE_TOPS = (
    ("figure", "3D-Figur", "Rund, aus einem Stück mit dem Sockel gedruckt.", "motif3d-bear"),
    ("standee", "Aufsteller", "Flach zum Einstecken, auch nach einer Kinderzeichnung.",
     "motif-bear"),
    ("flat", "Flach", "Eine eigene Figur daraufkleben, z. B. ein Spieltier.", "round"),
    ("bricks", "Noppen", "Klemmbausteine aufstecken, z. B. eine Minifigur.", "top-bricks"),
)  # fmt: skip
# field, role, label, shown when (data-show-when)
FIGURE_COLOR_ROLES = (
    ("color_base", "base", "Sockel", None),
    ("color_accent", "accent", "Name", None),
    ("color_motif", "motif", "Figur", "top=figure|standee"),
    ("color_details", "details", "Gesicht", "top=figure|standee"),
)


def _figure_message(exc: ValidationError) -> str:
    for err in exc.errors():
        if err["loc"] and str(err["loc"][0]) == "name":
            if err["type"] in ("string_too_long", "too_long"):
                return f"Der Name darf höchstens {MAX_LABEL} Zeichen haben."
            return str(err["msg"]).removeprefix("Value error, ")
        if not err["loc"] and err["type"] == "value_error":  # a combination, e.g. a drawing
            return str(err["msg"]).removeprefix("Value error, ")
    return "Diese Auswahl gibt es nicht. Bitte wähle aus den Optionen."


def _shown(cfg: FigureConfig) -> Callable[[str], Markup]:
    """``data-show-when="top=figure|standee"`` for case.js, hidden already when it does not
    apply, so the page is right without JavaScript."""

    def show(rule: str) -> Markup:
        key, values = rule.split("=", 1)
        hidden = "" if str(getattr(cfg, key)) in values.split("|") else " hidden"
        return Markup(f'data-show-when="{escape(rule)}"{hidden}')  # noqa: S704 - escaped

    return show


def _figure_query(cfg: FigureConfig) -> str:
    return urlencode(cfg.query())


@router.get("/gestalten/figur")
async def figure_page(
    request: Request, session: OptionalSession, settings: SettingsDep
) -> Response:
    return _figure_page(request, session, settings)


def _figure_page(
    request: Request,
    session: SessionInfo | None,
    settings: Settings,
    upload_error: str | None = None,
) -> Response:
    error: str | None = upload_error
    try:
        cfg = FigureConfig.from_query(dict(request.query_params))
    except ValidationError as exc:
        cfg, error = FigureConfig(), _figure_message(exc)
    context: dict[str, Any] = {
        "cfg": cfg,
        "query": _figure_query(cfg),
        "defaults_json": json.dumps(FigureConfig().model_dump(mode="json"), separators=(",", ":")),
        "suggested_json": json.dumps(
            {shape: {"color_base": base, "color_accent": accent}
             for shape, (base, accent) in FIGURE_SUGGESTED.items()}
            | {motif: {"color_motif": colour} | (
                {"color_accent": motifs.ACCENTS[motif]} if motif in motifs.ACCENTS else {})
               for motif, colour in motifs.COLOURS.items()},
            separators=(",", ":"),
        ),
        "chosen": {role: cfg.color_key(role) for role in ("base", "accent", "motif", "details")},
        "shapes": FIGURE_SHAPES,
        "shape_label": next(label for key, label, _ in FIGURE_SHAPES if key == cfg.shape),
        "motifs": [
            *((m, motifs.LABELS[m]) for m in motifs.MOTIFS), ("drawing", "Eigene Zeichnung")
        ],
        "tops": FIGURE_TOPS,
        "show": _shown(cfg),
        "color_roles": FIGURE_COLOR_ROLES,
        "palette": PALETTE,
        "max_label": MAX_LABEL,
        "error": error,
        "docs_url": settings.docs_url.rstrip("/"),
    }  # fmt: skip
    drawing_gone = cfg.motif == "drawing" and cfg.drawing is not None
    if drawing_gone and not error and drawings.load(settings.data_dir, cfg.drawing) is None:
        error = DRAWING_GONE
        context["error"] = error
    return render(request, "figure_design.html", context, session=session,
                  status_code=422 if error else 200)  # fmt: skip


@router.get("/gestalten/figur/vorschau")
async def figure_preview(request: Request, settings: SettingsDep) -> Response:
    try:
        cfg = FigureConfig.from_query(dict(request.query_params))
    except ValidationError as exc:
        return PlainTextResponse(_figure_message(exc), status_code=422)
    etag = f'"{cfg.digest()}"'
    headers = {"ETag": etag, "Cache-Control": "private, max-age=86400", "Vary": "Accept-Encoding"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    builds: CaseBuilds = request.app.state.case_builds
    if not builds.cached("figure_preview", cfg) and (
        limited := await _count_build(request, settings)
    ):
        return limited
    strokes = drawings.load(settings.data_dir, cfg.drawing) if cfg.motif == "drawing" else None
    if cfg.motif == "drawing" and cfg.drawing is not None and strokes is None:
        return PlainTextResponse(DRAWING_GONE, status_code=422)
    try:
        data = await builds.figure_preview(cfg, strokes)
    except FigureError as exc:
        return PlainTextResponse(str(exc), status_code=422)
    return Response(data, media_type="application/octet-stream",
                    headers=headers | {"Content-Encoding": "gzip"})  # fmt: skip


@router.get("/gestalten/figur/download.zip")
async def figure_download(request: Request, settings: SettingsDep) -> Response:
    try:
        cfg = FigureConfig.from_query(dict(request.query_params))
    except ValidationError as exc:
        return PlainTextResponse(_figure_message(exc), status_code=422)
    builds: CaseBuilds = request.app.state.case_builds
    if not builds.cached("figure_bundle", cfg) and (
        limited := await _count_build(request, settings)
    ):
        return limited
    base = settings.base_url.rstrip("/")
    query = _figure_query(cfg)
    url = f"{base}/gestalten/figur" + (f"?{query}" if query else "")
    strokes = drawings.load(settings.data_dir, cfg.drawing) if cfg.motif == "drawing" else None
    if cfg.motif == "drawing" and cfg.drawing is not None and strokes is None:
        return PlainTextResponse(DRAWING_GONE, status_code=422)
    try:
        data = await builds.figure_bundle(cfg, url, strokes)
    except FigureError as exc:
        return PlainTextResponse(str(exc), status_code=422)
    return Response(
        data,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{figure_stem(cfg)}.zip"',
            "Cache-Control": "private, max-age=86400",
        },
    )


DRAWING_GONE = (
    "Die Zeichnung ist nicht mehr gespeichert (nach 7 Tagen). Bitte noch einmal hochladen."
)


@router.post("/gestalten/figur/zeichnung")
async def figure_drawing(
    request: Request,
    session: OptionalSession,
    settings: SettingsDep,
    file: Annotated[UploadFile, File()],
    query: Annotated[str, Form(max_length=2000)] = "",
) -> Response:
    """A photo of a drawing: decoded and traced in memory, only the strokes are kept (7 days).
    Then back to the page with the new drawing chosen."""
    params = dict(parse_qsl(query))
    params |= {"top": "standee", "motif": "drawing"}
    params.pop("drawing", None)
    params.pop("turn", None)

    async def page_with(error: str) -> Response:
        request.scope["query_string"] = urlencode(params).encode()
        return _figure_page(request, session, settings, upload_error=error)

    try:
        await ratelimit.hit(
            request.app.state.engine,
            f"drawing:ip:{client_ip(request, settings)}",
            ratelimit.DRAWING_PER_IP,
        )
    except ratelimit.RateLimitedError:
        return await page_with("Zu viele Zeichnungen in kurzer Zeit. Bitte später noch einmal.")
    data = await file.read(drawings.MAX_BYTES + 1)
    if len(data) > drawings.MAX_BYTES:
        return await page_with("Das Foto ist zu groß (höchstens 16 MB).")
    try:
        gray = await anyio.to_thread.run_sync(drawings.decode, data, settings.ffmpeg_path)
        strokes = await anyio.to_thread.run_sync(trace.ink, gray)
    except (drawings.DrawingError, trace.TraceError) as exc:
        return await page_with(str(exc))
    params["drawing"] = drawings.store(settings.data_dir, strokes)
    return RedirectResponse(f"/gestalten/figur?{urlencode(params)}", status_code=303)


# --- order requests (off unless order_notify_email is set) -------------------------------------


def _orders_on(settings: Settings) -> None:
    if not settings.order_notify_email:
        raise NotFoundError()


def _request_context(cfg: CaseConfig, form: dict[str, str]) -> dict[str, Any]:
    return {
        "cfg": cfg,
        "query": _query(cfg),
        "rows": case_requests.describe(cfg),
        "countries": case_requests.COUNTRIES,
        "max_quantity": case_requests.MAX_QUANTITY,
        "form": form,
    }


def _done(request: Request, session: SessionInfo | None, email: str) -> Response:
    return render(
        request,
        "case_message.html",
        {
            "heading": "Fast geschafft",
            "text": f"Wir haben eine E-Mail an {email} geschickt. Bitte bestätige deine Anfrage "
            "über den Link darin, dann melden wir uns mit einem Angebot.",
        },
        session=session,
    )


@router.get("/gestalten/anfrage")
async def request_form(
    request: Request, session: OptionalSession, settings: SettingsDep
) -> Response:
    _orders_on(settings)
    try:
        cfg = _parse(request)
        layout_for(cfg)
    except (ValidationError, LayoutError):
        return RedirectResponse("/gestalten", status_code=303)
    return render(request, "case_request.html", _request_context(cfg, {}), session=session)


@router.post("/gestalten/anfrage")
async def request_submit(
    request: Request,
    db: DbSession,
    session: OptionalSession,
    settings: SettingsDep,
    config: Annotated[str, Form(max_length=2000)],
    name: Annotated[str, Form(max_length=200)] = "",
    email: Annotated[str, Form(max_length=254)] = "",
    country: Annotated[str, Form(max_length=2)] = "",
    quantity: Annotated[int, Form(ge=1, le=99)] = 1,
    message: Annotated[str, Form(max_length=4000)] = "",
    consent: Annotated[str, Form()] = "",
    website: Annotated[str, Form(max_length=200)] = "",  # honeypot: people leave it empty
) -> Response:
    _orders_on(settings)
    try:
        cfg = CaseConfig.from_query(dict(parse_qsl(config)))
        layout_for(cfg)
    except (ValidationError, LayoutError):
        return RedirectResponse("/gestalten", status_code=303)
    fields = {
        "name": name,
        "email": email,
        "country": country,
        "quantity": str(quantity),
        "message": message,
    }
    if website:
        return _done(request, session, email.strip())
    ip = client_ip(request, settings)
    try:
        await ratelimit.hit(
            request.app.state.engine, f"case_request:ip:{ip}", ratelimit.CASE_REQUEST_PER_IP
        )
    except ratelimit.RateLimitedError:
        context = _request_context(cfg, fields) | {
            "error": "Zu viele Anfragen. Bitte versuch es später."
        }
        return render(request, "case_request.html", context, session=session, status_code=429)
    if not consent:
        context = _request_context(cfg, fields) | {
            "error": "Bitte bestätige, dass wir deine Angaben für die Anfrage verwenden dürfen."
        }
        return render(request, "case_request.html", context, session=session, status_code=400)
    try:
        req = await case_requests.create_request(
            db, cfg, email=email, name=name, country=country, quantity=quantity, message=message
        )
    except DomainError as exc:
        await db.rollback()
        context = _request_context(cfg, fields) | {"error": exc.message}
        return render(request, "case_request.html", context, session=session, status_code=400)
    try:
        await ratelimit.hit(
            request.app.state.engine,
            f"case_request:mail:{req.email}",
            ratelimit.CASE_REQUEST_PER_EMAIL,
        )
    except ratelimit.RateLimitedError:
        await db.rollback()
        return _done(request, session, req.email)  # same answer: no hint about earlier requests
    await db.commit()
    if settings.mail_backend == "smtp":
        await request.app.state.job_app.configure_task(send_case_confirmation.name).defer_async(
            request_id=str(req.id)
        )
    else:
        token = await case_requests.rotate_token(db, req.id)
        await db.commit()
        if token:
            log_mail(settings, confirm_mail(settings, req, token))
    return _done(request, session, req.email)


# The confirmation token travels in the query string: never logged (app and nginx log paths only).
@router.get("/gestalten/anfrage/bestaetigen")
async def confirm_form(
    request: Request,
    db: DbSession,
    session: OptionalSession,
    settings: SettingsDep,
    token: str = "",
) -> Response:
    _orders_on(settings)
    req = await case_requests.open_request(db, token)
    if req is None:
        return _expired(request, session)
    cfg = CaseConfig.model_validate(req.config)
    context = {"token": token, "req": req, "rows": case_requests.describe(cfg)}
    return render(request, "case_confirm.html", context, session=session)


@router.post("/gestalten/anfrage/bestaetigen")
async def confirm_submit(
    request: Request,
    db: DbSession,
    session: OptionalSession,
    settings: SettingsDep,
    token: Annotated[str, Form(max_length=200)],
) -> Response:
    _orders_on(settings)
    req = await case_requests.confirm(db, token)
    if req is None:
        return _expired(request, session)
    await db.commit()
    if settings.mail_backend == "smtp":
        await request.app.state.job_app.configure_task(send_case_notification.name).defer_async(
            request_id=str(req.id)
        )
    else:
        for mail in notify_mails(settings, req):
            log_mail(settings, mail)
    return render(
        request,
        "case_message.html",
        {
            "heading": "Danke!",
            "text": "Deine Anfrage ist bestätigt. Wir melden uns per E-Mail mit einem Angebot: "
            "Preis, Versand und Lieferzeit. Bestellt ist erst, wenn du das Angebot annimmst.",
        },
        session=session,
    )


def _expired(request: Request, session: SessionInfo | None) -> Response:
    return render(
        request,
        "case_message.html",
        {
            "heading": "Link nicht mehr gültig",
            "text": "Dieser Bestätigungslink ist abgelaufen oder wurde schon benutzt. "
            "Unbestätigte Anfragen löschen wir nach 48 Stunden; du kannst jederzeit neu anfragen.",
        },
        session=session,
        status_code=404,
    )

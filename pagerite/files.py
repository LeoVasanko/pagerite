"""Content-addressed file store, image derivatives, and file routes.

``FileStore`` keeps uploads, seed assets and fetched favicons on disk under
hash-prefixed names, fully cached in RAM (uncompressed plus a zstd copy
when compression shrinks the body), served immutable at ``/_f/``. Raster
images and SVGs are recompressed into AVIF/WebP/JPEG derivatives
(``store_image`` and helpers); the untouched original is kept alongside as
``<hash>.orig<ext>`` (never served). Routes: upload/delete under
``/_api/files``, the favicon settings endpoints, the /favicon.ico
redirect to the configured icon, the ``/_f/`` server with
Accept-negotiated formats, and the user assets (``/_themes/``, ``/_fonts/``).
"""

import asyncio
import logging
import mimetypes
import tempfile
from contextlib import suppress
from pathlib import Path

import blake3
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from mediapreview import dispatch

from pagerite import views
from pagerite.state import (
    FAVICON_MAXSIZE,
    FILES_DIR,
    IMAGE_JPG_QUALITY,
    IMAGE_MAXSIZE,
    IMAGE_QUALITY,
    IMAGE_WEBP_QUALITY,
    _invalidate_pages,
    _zstd,
    data,
    kanta,
)

logger = logging.getLogger(__name__)

# mediapreview logs pyvips noise ("VipsForeignSaveJpegTarget argument strip is
# deprecated", "threadpool completed with N workers") at INFO; keep warnings.
logging.getLogger("mediapreview").setLevel(logging.WARNING)

router = APIRouter()


class FileStore:
    """Content-addressed files on disk, fully cached in RAM.

    Every file is kept in RAM uncompressed and zstd-compressed (the
    compressed copy only when it actually shrinks the body), so ``/_f``
    serves both encodings without touching disk or re-compressing.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        #: name -> (uncompressed body, zstd body or None)
        self._cache: dict[str, tuple[bytes, bytes | None]] = {}

    @staticmethod
    def _entry(body: bytes) -> tuple[bytes, bytes | None]:
        compressed = _zstd.compress(body)
        return body, compressed if len(compressed) < len(body) else None

    def load(self) -> None:
        """Read every stored file into the RAM cache (startup)."""
        try:
            entries = sorted(self.path.iterdir())
        except FileNotFoundError:
            return
        for f in entries:
            if f.is_file() and not f.name.startswith("."):
                self._cache.setdefault(f.name, self._entry(f.read_bytes()))

    def get(self, name: str) -> tuple[bytes, bytes | None] | None:
        return self._cache.get(name)

    def put(self, name: str, body: bytes) -> None:
        """Store ``body`` under ``name`` on disk and in the RAM cache."""
        if name in self._cache:
            return
        self.path.mkdir(parents=True, exist_ok=True)
        (self.path / name).write_bytes(body)
        self._cache[name] = self._entry(body)

    def delete(self, name: str) -> None:
        """Delete a file plus its derivatives/original counterparts, if any.

        An image upload is stored as a group sharing the hash prefix
        (``<hash>.orig.<ext>`` + ``<hash>.avif/.webp/.jpg``); deleting any
        of the names removes them all.
        """
        stem = name.partition(".")[0]
        for key in [k for k in self._cache if k.partition(".")[0] == stem]:
            self._cache.pop(key, None)
            with suppress(FileNotFoundError):
                (self.path / key).unlink()

    def __contains__(self, name: str) -> bool:
        return name in self._cache


file_store = FileStore(FILES_DIR)


def _ext(orig: str) -> str:
    """Sanitized lowercase extension (with dot) of an original file name."""
    return "".join(c for c in Path(orig).suffix.lower() if c.isalnum() or c == ".")


def _hash_name(body: bytes, orig: str) -> str:
    """Content-addressed file name: blake3 hash prefix + original extension."""
    return blake3.blake3(body).hexdigest()[:12] + _ext(orig)


def _to_avif(body: bytes, ext: str, maxsize: int = IMAGE_MAXSIZE) -> bytes | None:
    """Recompress an image body to a thumbnailed AVIF via mediapreview's
    dispatch (pyvips for common formats, ffmpeg for HEIC/HEIF/AVIF), or
    None if the body is not a decodable image (stored as-is by the caller).
    Dispatch needs a real file for format routing, so the body goes
    through a temp file.
    """
    with tempfile.NamedTemporaryFile(suffix=ext) as tmp:
        tmp.write(body)
        tmp.flush()
        try:
            avif, _resp = dispatch(
                Path(tmp.name),
                quality=IMAGE_QUALITY,
                maxsize=maxsize,
                maxzoom=1,
            )
        except Exception:
            return None
        return avif


def _svg_to_png(body: bytes, maxsize: int) -> bytes | None:
    """Rasterize an SVG to PNG via pyvips, scaled so the long side is
    ``maxsize`` — SVGs often carry no meaningful intrinsic resolution, so
    we rasterize at full image size rather than the tiny nominal one."""
    import pyvips

    try:
        img = pyvips.Image.new_from_buffer(body, "")
        scale = (
            maxsize / max(img.width, img.height)
            if img.width and img.height
            else maxsize
        )
        if scale != 1:
            img = pyvips.Image.new_from_buffer(body, "", scale=scale)
        return img.write_to_buffer(".png")
    except pyvips.Error:
        return None


def _avif_to_format(avif: bytes, suffix: str, quality: int) -> bytes:
    """Re-encode the AVIF derivative into a fallback format (WebP/JPEG)
    via pyvips. JPEG has no alpha, so it is flattened onto white;
    ``strip`` keeps metadata (EXIF) out of the fallbacks."""
    import pyvips

    img = pyvips.Image.new_from_buffer(avif, "")
    if suffix == ".jpg" and img.hasalpha():
        img = img.flatten(background=[255, 255, 255])
    return img.write_to_buffer(suffix, Q=quality, strip=True)


def _image_derivatives(
    body: bytes, ext: str, maxsize: int = IMAGE_MAXSIZE
) -> dict[str, bytes] | None:
    """The served variants of an uploaded image: ``avif`` (primary,
    thumbnailed to ``maxsize``) plus ``webp`` and ``jpg`` fallbacks
    re-encoded from it. SVGs are rasterized first (they are vector, so
    the raster replaces nothing — the .svg itself stays servable).
    Returns None for non-decodable content (stored as-is by the caller).
    """
    if ext == ".svg":
        png = _svg_to_png(body, maxsize)
        if png is None:
            return None
        body, ext = png, ".png"
    avif = _to_avif(body, ext, maxsize)
    if avif is None:
        return None
    return {
        "avif": avif,
        "webp": _avif_to_format(avif, ".webp", IMAGE_WEBP_QUALITY),
        "jpg": _avif_to_format(avif, ".jpg", IMAGE_JPG_QUALITY),
    }


def store_image(
    body: bytes, ext: str, maxsize: int = IMAGE_MAXSIZE, *, derive: bool = True
) -> str:
    """Store an image body content-addressed and return its file name.

    Decodable images get AVIF/WebP/JPEG derivatives thumbnailed to
    ``maxsize``; the original is kept as ``<hash>.orig<ext>`` (SVG
    originals as ``<hash>.svg``, still servable) and the bare ``<hash>``
    name is returned (the server negotiates the format by Accept header).
    Anything else — undecodable content, or ``derive=False`` (GIFs, whose
    animation recompression would lose) — is stored as-is and returned with
    its extension. Blocking (pyvips/ffmpeg); call via ``asyncio.to_thread``
    from async code.
    """
    digest = blake3.blake3(body).hexdigest()[:12]
    derivatives = _image_derivatives(body, ext, maxsize) if derive else None
    if derivatives is None:  # store the body as-is
        file_store.put(digest + ext, body)
        return digest + ext
    file_store.put(f"{digest}.svg" if ext == ".svg" else f"{digest}.orig{ext}", body)
    for fmt, variant in derivatives.items():
        file_store.put(f"{digest}.{fmt}", variant)
    return digest


@router.put("/_api/files/{name}")
async def upload_file(name: str, request: Request) -> dict[str, str]:
    """Store an upload (image, video...) in the content-addressed store.

    The stored name is a blake3 hash prefix + the original extension,
    served immutable at "/_f/{name}"; returns {"path": "/_f/..."}.

    Raster images and SVGs are recompressed (SVGs rasterized) into AVIF
    (primary) plus WebP and JPEG fallbacks: the original goes to
    ``<hash>.orig<ext>`` (kept for reprocessing, never served — it may
    carry EXIF data; SVG originals stay servable as ``<hash>.svg`` since
    vector carries no EXIF) and pages link the bare ``/_f/<hash>``, the
    server picking the format from the request's Accept header. GIFs are
    stored as-is (animation would be lost), as is other non-decodable
    content.
    """
    if "/" in name or name in {".", ".."}:
        raise HTTPException(400, "bad file name")
    body = await request.body()
    if not body:
        raise HTTPException(400, "empty file")
    ext = _ext(name)
    stored = await asyncio.to_thread(store_image, body, ext, derive=ext != ".gif")
    return {"path": f"/_f/{stored}"}


@router.delete("/_api/files/{name}", status_code=204)
async def delete_file(name: str) -> None:
    """Remove a file from the content-addressed store (no refcounting:
    other pages referencing the same content will 404)."""
    if name not in file_store:
        raise HTTPException(404, "no such file")
    file_store.delete(name)


@router.get("/favicon.ico", include_in_schema=False)
async def favicon_ico() -> Response:
    """The conventional /favicon.ico: redirect to the configured site icon.

    Browsers request this path on their own (tabs, bookmarks, feeds and
    other non-HTML contexts) regardless of the <link rel="icon"> pages
    carry. Redirect to the icon's store URL, which negotiates the format
    and caches immutably; 404 when no custom icon is configured.
    """
    if not data.favicon:
        raise HTTPException(404)
    return RedirectResponse(f"/_f/{data.favicon}")


@router.put("/_api/settings/favicon")
async def put_favicon(request: Request) -> dict[str, str]:
    """Upload a favicon into the content-addressed store and activate it.

    Raw image body (ico/png/svg...). Decodable images are thumbnailed to
    FAVICON_MAXSIZE (192px — browsers scale down from there themselves)
    and stored as AVIF/WebP/JPEG derivatives linked extension-less; SVG
    originals also stay servable under their ``.svg`` name. Undecodable
    bodies are stored as-is. Pages link it as <link rel="icon">. Returns
    {"path": "/_f/..."}.
    """
    body = await request.body()
    if not body:
        raise HTTPException(400, "empty file")
    ext = _ext(request.headers.get("x-filename", "favicon.ico"))
    stored = await asyncio.to_thread(store_image, body, ext, FAVICON_MAXSIZE)
    with kanta.transaction("settings", user=request.headers.get("remote-user")):
        data.favicon = stored
        _invalidate_pages()
    return {"path": f"/_f/{stored}"}


@router.delete("/_api/settings/favicon", status_code=204)
async def delete_favicon(request: Request) -> None:
    """Clear the custom favicon (/favicon.ico goes back to 404, pages drop
    the <link rel="icon">).

    The blob stays in the content-addressed store; only the reference goes.
    """
    with kanta.transaction("settings", user=request.headers.get("remote-user")):
        data.favicon = ""
        _invalidate_pages()


async def _serve_user_file(path: Path | None, request: Request) -> Response:
    """Serve a user-asset file resolved on disk, with mtime etag.

    Read from disk on every request (etag by mtime+size): user assets are
    never built or content-hashed, so edits on disk show on the next page
    load, in prod as well as dev.
    """
    if path is None:
        raise HTTPException(404)
    stat = path.stat()
    etag = f'"{stat.st_mtime_ns:x}-{stat.st_size:x}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304)
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return Response(
        path.read_bytes(),
        media_type=mime,
        headers={"etag": etag, "cache-control": "no-cache"},
    )


@router.get("/_themes/{name}/{filename}")
async def theme_file(name: str, filename: str, request: Request) -> Response:
    """Serve a theme/banner-design file, resolved across views.THEME_DIRS.

    Stylesheets plus any extra assets the CSS references (like summer's
    grass.svg).
    """
    return await _serve_user_file(views.theme_file(name, filename), request)


@router.get("/_fonts/{name}/{filename}")
async def user_font_file(name: str, filename: str, request: Request) -> Response:
    """Serve a user font file, resolved across views.FONT_DIRS.

    The folder's font.css (@font-face rules + --font-{name} stack variable)
    is linked on every page; the woff2 files it references come from here.
    """
    return await _serve_user_file(views.font_file(name, filename), request)


@router.get("/_f/{name}")
async def stored_file(name: str, request: Request) -> Response:
    """Serve a file from the content-addressed store (immutable: the name
    is its own hash, so cache forever).  Bodies are served from the RAM
    cache, zstd-compressed when the client accepts it and compression
    actually shrank the file.

    A bare ``/_f/{hash}`` (no extension, how pages link uploaded images)
    content-negotiates between the stored derivatives: a format is served
    only when the Accept header lists it explicitly — ``image/avif`` →
    AVIF, ``image/webp`` → WebP, anything else (including ``image/*`` and
    ``*/*``) → JPEG. An explicit extension pins the format. ``.orig.``
    originals are internal (they may carry EXIF data) and never served."""
    if ".orig." in name:
        raise HTTPException(404)
    etag = name
    vary = ""
    entry = file_store.get(name)
    if entry is None and "." not in name:
        # Extension-less image link: negotiate avif/webp/jpg by Accept.
        vary = "accept"
        accept = request.headers.get("accept", "")
        if "image/avif" in accept:
            order = ("avif", "webp", "jpg")
        elif "image/webp" in accept:
            order = ("webp", "jpg", "avif")
        else:
            order = ("jpg", "webp", "avif")
        for ext in order:
            etag = f"{name}.{ext}"
            entry = file_store.get(etag)
            if entry is not None:
                break
    if entry is None:
        raise HTTPException(404)
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304)
    body, compressed = entry
    headers = {"etag": etag, "cache-control": "public, max-age=31536000, immutable"}
    if compressed is not None and "zstd" in request.headers.get("accept-encoding", ""):
        headers["content-encoding"] = "zstd"
        vary = f"{vary}, accept-encoding".lstrip(", ")
        body = compressed
    if vary:
        headers["vary"] = vary
    mime = mimetypes.guess_type(etag)[0] or "application/octet-stream"
    return Response(body, media_type=mime, headers=headers)

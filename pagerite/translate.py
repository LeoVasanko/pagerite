"""Translator service protocol, dispatcher and its transport-independent core.

The external machine-translation service connects over WebSocket
(``/_translate/<key>``, the route itself is in api.py) and exchanges JSON
frames decoded into the tagged msgspec structs below (``bytes`` fields ride
as base64 — no manual encoding anywhere). This module holds everything
else: the message structs, the connected-client dispatcher (``Dispatcher``
— one job at a time per connection, wanted ∩ capable language matching,
requeue on disconnect), which fragments are pending for a language
(``pending_items``) and storing a result (``store_results``).

Fragments cross the wire as **prose segments**: the model only ever
receives plain text runs (Job.texts) plus per-segment context surrounds
(Job.contexts) and returns their translations (Result.texts, same order);
markup never leaves the server — reassembly is offset splicing
(``pagerite/segments.py``).
"""

import asyncio
import logging

import msgspec
from fastapi import WebSocket, WebSocketDisconnect
from kanta import Kanta

from pagerite import i18n
from pagerite.chunks import chunk_key, needs_translation
from pagerite.data import Data, Node, sorted_nodes
from pagerite.segments import Span, join, split

logger = logging.getLogger(__name__)


class Hello(msgspec.Struct, tag="hello"):
    """Client greeting on connect: the language codes its model CAN produce
    (capabilities). The server offers jobs only in the intersection with
    the wanted target languages (``Data.translate_langs``)."""

    langs: list[str]


class TransItem(msgspec.Struct):
    """One fragment to translate: original Markdown (or a node title)."""

    key: bytes  #: 9-byte chunk hash (base64 in the JSON frame)
    text: str
    path: str  #: article it came from ("" = front page), no leading slash
    kind: str  #: "chunk" | "title"
    #: Title jobs only: the article's opening prose, so the model sees the
    #: title as a heading in context, not a lone sentence.
    context: str = ""


class Job(msgspec.Struct, tag="job"):
    """Server push: ONE fragment to translate.

    Exactly one job is in flight per connection — the next is sent only
    after this one's Result. Clients wanting parallelism open multiple
    connections."""

    lang: str
    key: bytes  #: 9-byte chunk hash (base64 in the JSON frame)
    #: The fragment's prose segments (pagerite/segments.py): plain text
    #: runs only — no markup, URLs, code or placeholders ever cross the
    #: wire. Translate each element independently.
    texts: list[str]
    path: str  #: article it came from ("" = front page), no leading slash
    kind: str  #: "chunk" | "title"
    #: Per segment (parallel to texts; "" = none): the surround to
    #: translate it in — a carved-out segment (link text, partial run)
    #: carries its block's plain text, a title the article's opening.
    #: Reference client behavior (scripts/translator.py): translate
    #: segment+context together, keep the segment's part (its own line /
    #: paragraph); fall back to the segment alone when the output holds no
    #: separator. Contexts are not part of the result.
    contexts: list[str] = msgspec.field(default_factory=list)


class TransResult(msgspec.Struct):
    """One translated fragment (storage level, see store_results)."""

    key: bytes
    text: str


class Result(msgspec.Struct, tag="result"):
    """Client reply: the translation of the connection's current Job
    (must match its lang and key exactly)."""

    lang: str
    key: bytes
    #: The job's segments, translated, same order and count. Each must be
    #: pure prose — the server rejects the result otherwise.
    texts: list[str]


#: Union of the client -> server frames (the "type" tag selects).
ClientMsg = Hello | Result


def pending_items(data: Data, lang: str) -> list[TransItem]:
    """Fragments of the site still untranslated for ``lang``, deduped by key.

    Every page node (published or not) contributes its title and each chunk
    that needs translation (``needs_translation``), is not editor-flagged
    no-translate (``node.no_trans``) and has no ``trans`` entry for ``lang``
    yet. Content-addressed text (shared paragraphs, repeated titles) appears
    once, under the first page in menu order that has it.
    """
    items: list[TransItem] = []
    seen: set[bytes] = set()

    def emit(key: bytes, text: str, path: str, kind: str, context: str = "") -> None:
        if key in seen or lang in data.trans.get(key, {}):
            return
        seen.add(key)
        items.append(
            TransItem(key=key, text=text, path=path, kind=kind, context=context)
        )

    def opening(node: Node) -> str:
        """The article's opening prose (first segment, capped): the title
        job's context — a lone word like "About" reads as a heading on top
        of an article, not as a sentence. Empty when there's no prose."""
        for h in node.chunks or ():
            text = data.chunks.get(h)
            if text and (segs := split(text)[1]):
                return segs[0][:400]
        return ""

    def walk(nodes: dict[str, Node], prefix: str, inherited: str) -> None:
        for slug, node in sorted_nodes(nodes):
            path = f"{prefix}/{slug}" if prefix else slug
            # An article whose primary language IS the target needs no
            # translation into it — skip its title and chunks entirely.
            node_lang = node.language or inherited
            if node.chunks is not None and node_lang != lang:
                if node.title:
                    emit(
                        chunk_key(node.title),
                        node.title,
                        path,
                        "title",
                        context=opening(node),
                    )
                for h in node.chunks:
                    text = data.chunks.get(h)
                    if (
                        text is not None
                        and h not in node.no_trans
                        and needs_translation(text)
                    ):
                        emit(h, text, path, "chunk")
            walk(node.children, path, node_lang)

    walk(data.menu, "", i18n.ORIGINAL_LANGUAGE)
    return items


def store_results(data: Data, lang: str, items: list[TransResult]) -> list[str]:
    """Store machine translations for ``lang``; return the paths of the
    articles that gained at least one entry.

    Pure data operations: the caller wraps this in a kanta transaction and
    invalidates pages. Unknown keys are stored anyway (unreferenced hashes
    are never read, and the content may simply have moved on since the job
    was pushed); re-storing an existing key overwrites, last wins. Every
    article that gained an entry gets ``node.langs[lang]`` set (the
    availability index, docs/migrate.md) — because chunks are
    content-addressed, that includes pages merely sharing a fragment.
    """
    stored = {item.key for item in items}
    for item in items:
        data.trans.setdefault(item.key, {})[lang] = item.text
    pages: list[str] = []

    def walk(nodes: dict[str, Node], prefix: str, inherited: str) -> None:
        for slug, node in sorted_nodes(nodes):
            path = f"{prefix}/{slug}" if prefix else slug
            node_lang = node.language or inherited
            if node.chunks is not None and node_lang != lang:
                keys = set(node.chunks)
                if node.title:
                    keys.add(chunk_key(node.title))
                if keys & stored:
                    node.langs[lang] = True
                    pages.append(path)
            walk(node.children, path, node_lang)

    walk(data.menu, "", i18n.ORIGINAL_LANGUAGE)
    return pages


class _Connection:
    """One connected translator socket: the language codes it announced as
    capabilities (Hello) and the (lang, chunk-key) job currently in flight
    on it, with the segment spans to splice its Result into
    (pagerite/segments.py) — one at a time, the next is sent only after its
    Result.

    Per-connection only: in-flight lives solely here, so on disconnect the
    item simply becomes pending again and is re-offered to any free capable
    connection."""

    def __init__(self, capable: set[str]) -> None:
        self.capable = capable
        self.inflight: tuple[str, bytes] | None = None
        #: Source spans of the in-flight job's segments (splice offsets
        #: and link marks).
        self.spans: list[Span] = []
        self.original: str = ""  # its full source text (for the splicing)


class Dispatcher:
    """The translator dispatcher: connected client sockets and the job
    pipeline (docs/localization.md).

    One single-item job at a time per connection, offered in the
    intersection of the wanted languages (``Data.translate_langs``) and the
    connection's announced capabilities. Pending work is derived from the
    ``trans`` store (``pending_items``) minus the items in flight on any
    connection, so a dropped connection's in-flight item is simply
    re-offered. Results are matched to content by chunk key alone. A
    (lang, key) whose Result fails segment validation is skipped for the
    rest of the run — generation is near-deterministic, so an immediate
    retry would just re-fail.
    """

    def __init__(self, data: Data, db: Kanta, invalidate) -> None:
        self.data = data
        self.db = db
        #: Sync content-change hook (state._invalidate_pages), called inside
        #: transactions; schedules the next dispatch pass.
        self.invalidate = invalidate
        #: Connected translator sockets and their per-connection state.
        self.clients: dict[WebSocket, _Connection] = {}
        #: (lang, chunk key) of fragments whose result failed validation
        #: (segment count, empty or non-prose segments, segments.py) this run.
        self.validation_failures: set[tuple[str, bytes]] = set()

    def reset_validation_failures(self) -> None:
        """Clear the skip list of fragments rejected this run (segment
        validation): a translations refresh is precisely the "another
        chance" for them."""
        self.validation_failures.clear()

    def schedule(self) -> None:
        """Schedule a dispatch pass, if any translator is connected.

        The invalidate hook is sync and called inside transactions: the
        task first runs once the current coroutine awaits again, i.e. after
        the transaction has committed. No-op without a running loop (CLI
        use)."""
        if not self.clients:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        asyncio.create_task(self._dispatch())

    async def _dispatch(self) -> None:
        """Offer one pending item to every free capable connection."""
        wanted = {
            tag for lang in self.data.translate_langs if (tag := i18n.base_tag(lang))
        }
        if not wanted:
            return
        for ws, state in list(self.clients.items()):
            if state.inflight is not None:
                continue
            langs = wanted & state.capable
            if not langs:
                continue
            inflight = {s.inflight for s in self.clients.values() if s.inflight}
            job = None
            spans: list[Span] = []
            original = ""
            for lang in sorted(langs):
                for item in pending_items(self.data, lang):
                    if (lang, item.key) in inflight or (
                        lang,
                        item.key,
                    ) in self.validation_failures:
                        continue
                    spans, texts, contexts = split(item.text)
                    if not texts:
                        continue  # prose that could not be located for splicing
                    original = item.text
                    if item.kind == "title" and item.context:
                        # A title's surround is the article's opening prose
                        # (TransItem.context), not its own one-word block.
                        contexts = [item.context] * len(texts)
                    job = Job(
                        lang=lang,
                        key=item.key,
                        texts=texts,
                        path=item.path,
                        kind=item.kind,
                        contexts=contexts,
                    )
                    break
                if job is not None:
                    break
            if job is None:
                continue
            state.inflight = (job.lang, job.key)  # before the await: no double-assign
            state.spans = spans
            state.original = original
            try:
                await ws.send_text(msgspec.json.encode(job).decode())
            except Exception:  # send failed: the receive loop cleans up
                self.clients.pop(ws, None)

    async def handle_ws(self, ws: WebSocket, clientkey: str) -> None:
        """The /_translate/<key> channel (docs/localization.md).

        A wrong/empty key rejects the handshake (closing before accept
        makes Starlette answer HTTP 403). Protocol (JSON frames): the
        client opens with Hello(langs) announcing its CAPABILITIES — the
        language codes its model can produce (normalized to translation
        tags; "en"/empty dropped) — then answers each Job with its
        Result(lang, key, texts). A Result without an in-flight job or with
        a different (lang, key), a duplicate Hello, or any malformed frame
        closes the socket with a protocol error.
        """
        if clientkey not in self.data.translate_keys:
            await ws.close(code=1008)  # policy violation; pre-accept = HTTP 403
            return
        await ws.accept()
        state: _Connection | None = None
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    msg = msgspec.json.decode(raw.encode(), type=ClientMsg)
                except msgspec.DecodeError:
                    await ws.close(code=1002)  # protocol error
                    return
                if isinstance(msg, Hello):
                    if state is not None:  # one Hello per connection
                        await ws.close(code=1002)
                        return
                    state = _Connection(
                        {tag for lang in msg.langs if (tag := i18n.base_tag(lang))}
                    )
                    self.clients[ws] = state
                    self.schedule()
                else:  # Result
                    lang = i18n.base_tag(msg.lang)
                    if (
                        state is None  # results before Hello
                        or state.inflight is None  # no job in flight
                        or (lang, msg.key) != state.inflight  # wrong job
                    ):
                        await ws.close(code=1002)
                        return
                    texts, spans, original = msg.texts, state.spans, state.original
                    state.inflight = None
                    state.spans = []
                    state.original = ""
                    text = (
                        join(original, spans, texts)
                        if len(texts) == len(spans)
                        else None
                    )
                    if text is None:
                        # The model broke the segment contract (count
                        # mismatch, empty or non-prose segment): drop the
                        # result and skip the fragment for this run (it
                        # stays pending; a restart, a refresh or a model
                        # change gets another chance).
                        self.validation_failures.add((lang, msg.key))
                        logger.warning(
                            "[%s] result for chunk %s rejected: invalid segments",
                            lang,
                            msg.key.hex(),
                        )
                        self.schedule()
                        continue
                    with self.db.transaction(
                        "translator results", user=clientkey, extra=lang
                    ):
                        paths = store_results(
                            self.data, lang, [TransResult(key=msg.key, text=text)]
                        )
                        self.invalidate()  # schedules the next dispatch
                    if paths:
                        logger.info(
                            "[%s] now available for %d page(s): %s",
                            lang,
                            len(paths),
                            ", ".join(sorted(paths)),
                        )
        except WebSocketDisconnect:
            pass
        finally:
            if self.clients.pop(ws, None) is not None:
                # The in-flight item (if any) is pending again; offer it around.
                self.schedule()

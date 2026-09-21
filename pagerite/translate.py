"""Translator service protocol, dispatcher and its transport-independent core.

The external machine-translation service connects over WebSocket
(``/_translate/<key>``, the route itself is in api.py) and exchanges JSON
frames decoded into the tagged msgspec structs below (``bytes`` fields ride
as base64 — no manual encoding anywhere). This module holds everything
else: the message structs, the connected-client dispatcher (``Dispatcher``
— one job at a time per connection, wanted ∩ capable language matching,
requeue on disconnect), which fragments are pending for a language
(``pending_items``) and storing results (``store_results``).

Four job modes (Hello.modes announces which a connection accepts;
docs/llm-translation.md):

- ``segments`` (default) — fragments cross as prose segments; markup never
  leaves the server and translations are spliced back by offset
  (``pagerite/segments.py``). For text-to-text models (Seed-X).
- ``markdown`` — one fragment as full Markdown (a body chunk or a title),
  with the surrounding blocks of the served hybrid as context. For
  Markdown-native instruct LLMs; the result must re-chunk to exactly one
  block with anchor constructs (link destinations, placeholders) intact.
- ``article`` — a whole page's Markdown at once (only while a page is
  mostly pending); the result is decomposed back into per-chunk
  translations (``align_article``), anchor-aligned and validated.
- ``nav`` — the whole navigation hierarchy as one nested Markdown list of
  titles; the result is decomposed back into per-title fragments by list
  structure (``align_nav``). One round trip names the entire menu, and
  sibling titles translate consistently.
"""

import asyncio
import difflib
import itertools
import logging
import re

import msgspec
from fastapi import WebSocket, WebSocketDisconnect
from kanta import Kanta

from pagerite import i18n
from pagerite.chunks import chunk_key, chunk_markdown, needs_translation
from pagerite.data import Data, Node, node_markdown, resolve, sorted_nodes
from pagerite.markdown import has_h1
from pagerite.segments import Span, join, pure_prose, split

logger = logging.getLogger(__name__)

#: Job granularities a translator connection may announce (Hello.modes).
MODES = frozenset({"segments", "markdown", "article", "nav"})


class Hello(msgspec.Struct, tag="hello"):
    """Client greeting on connect: the language codes its model CAN produce
    (capabilities). The server offers jobs only in the intersection with
    the wanted target languages (``Data.translate_langs``)."""

    langs: list[str]
    model: str = ""  #: free-form model string (logging, debugging)
    #: Job granularities accepted (default: segments only).
    modes: list[str] = msgspec.field(default_factory=lambda: ["segments"])


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
    #: segments mode: the fragment's prose segments (pagerite/segments.py)
    #: — plain text runs only, no markup. markdown/article/nav modes: a
    #: single element — the fragment's, the whole page's resp. the whole
    #: navigation tree's Markdown.
    texts: list[str]
    path: str  #: article it came from ("" = front page), no leading slash
    kind: str  #: "chunk" | "title" | "article" | "nav"
    #: The job granularity (the connection's mode this job was built for).
    mode: str = "segments"
    #: segments mode: per segment (parallel to texts; "" = none) the
    #: surround to translate it in. markdown mode: for chunks the previous
    #: and next block of the served hybrid (target language, patches
    #: applied; "" where none), for titles the article's opening. article
    #: mode with an injected title: the menu title's and the parent node's
    #: existing translations ("" where none). Contexts are reference only,
    #: never part of the result.
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
    #: The job's texts, translated: same order and count for segments jobs
    #: (each pure prose, or the result is rejected); a single element —
    #: the translated block, the whole translated article resp. the whole
    #: translated navigation list — for markdown/article/nav jobs.
    texts: list[str]


#: Union of the client -> server frames (the "type" tag selects).
ClientMsg = Hello | Result

#: A dispatchable offer: the job, its segment spans (segments mode), the
#: full source text, the (lang, key) pairs it covers, and the page title's
#: chunk key when an article job carries an injected title heading.
_Offer = tuple["Job", list[Span], str, set[tuple[str, bytes]], "bytes | None"]

#: Constructs a translation must preserve verbatim inside a prose block:
#: link/image destinations and {...} placeholders (sorted multisets are
#: compared, so additions and drops both fail validation).
_DEST = re.compile(r"\]\(([^)\s]+)")
_BRACES = re.compile(r"\{[^{}\n]*\}")

#: Cap for a markdown-mode context block (previous/next hybrid block).
_CONTEXT_CHARS = 1500


def _marks(text: str) -> list[str]:
    return sorted(_DEST.findall(text) + _BRACES.findall(text))


def clean_block(source: str, translated: str, kind: str) -> str | None:
    """The translated block of a markdown-mode result, or None when it
    fails validation: the result must re-chunk to exactly one block with
    the source's anchor constructs (destinations, placeholders) intact;
    titles must stay a single prose line."""
    blocks = chunk_markdown(translated)
    if len(blocks) != 1:
        return None
    block = blocks[0]
    if kind == "title" and ("\n" in block or not pure_prose(block)):
        return None
    return block if _marks(source) == _marks(block) else None


def align_article(source: str, translated: str) -> list[tuple[bytes, str]] | None:
    """Decompose a whole-article translation into (source chunk key,
    translated block) pairs (also the import path for human-made
    translations, scripts/import_translation.py).

    Blocks that must not change (code fences, container fences, raw HTML —
    everything ``needs_translation`` rejects) anchor the alignment: they
    must appear verbatim (chunk_key equality) and in order, or the whole
    result is rejected. Between two anchors the regions pair positionally;
    a region whose block count changed stores nothing (its chunks stay
    pending and fall back to scoped jobs), as does a paired block whose
    anchor constructs did not survive.
    """
    src, tgt = chunk_markdown(source), chunk_markdown(translated)
    tgt_keys = [chunk_key(c) for c in tgt]
    locs: list[tuple[int, int]] = []  # (source index, target index) of anchors
    pos = 0
    for i, chunk in enumerate(src):
        if needs_translation(chunk):
            continue
        want = chunk_key(chunk)
        while pos < len(tgt) and tgt_keys[pos] != want:
            pos += 1
        if pos == len(tgt):
            return None
        locs.append((i, pos))
        pos += 1
    pairs: list[tuple[bytes, str]] = []
    ends = [(-1, -1), *locs, (len(src), len(tgt))]
    for (s0, t0), (s1, t1) in itertools.pairwise(ends):
        sregion, tregion = src[s0 + 1 : s1], tgt[t0 + 1 : t1]
        if len(sregion) != len(tregion):
            continue
        pairs.extend(
            (chunk_key(s), t)
            for s, t in zip(sregion, tregion)
            if _marks(s) == _marks(t)
        )
    return pairs


#: One item line of a nested Markdown navigation list (nav mode).
_NAV_LINE = re.compile(r"^([ \t]*)-\s+(.*\S)\s*$")


def _nav_lines(md: str) -> list[tuple[int, str]] | None:
    """(depth, text) per item of a nested Markdown list, or None when a
    non-blank line is not a "- " item. Depths are the indent strings in
    order of first appearance, so any consistent indent width maps."""
    indents: list[str] = []
    items: list[tuple[int, str]] = []
    for line in md.split("\n"):
        if not line.strip():
            continue
        m = _NAV_LINE.match(line)
        if not m:
            return None
        indent, text = m.groups()
        if indent not in indents:
            indents.append(indent)
        items.append((indents.index(indent), text))
    return items


def align_nav(
    source: str, translated: str
) -> tuple[list[tuple[bytes, str]], list[bytes]] | None:
    """Decompose a whole-navigation translation into (title chunk key,
    translated title) pairs, plus the keys of titles that failed
    item-level validation (they stay pending for scoped title jobs).

    The result must be the same nested list item for item — same count,
    same nesting depth at every position — or the whole job is rejected
    (None) and every title falls back to scoped title jobs. A paired item
    that came back empty, marked-up or with its anchor constructs (link
    destinations, placeholders) lost is skipped individually.
    """
    src, tgt = _nav_lines(source), _nav_lines(translated)
    if src is None or tgt is None or len(src) != len(tgt):
        return None
    pairs: list[tuple[bytes, str]] = []
    skipped: list[bytes] = []
    for (sdepth, stitle), (tdepth, ttitle) in zip(src, tgt):
        if sdepth != tdepth:
            return None
        key = chunk_key(stitle)
        if not ttitle or not pure_prose(ttitle) or _marks(stitle) != _marks(ttitle):
            skipped.append(key)
            continue
        pairs.append((key, ttitle))
    return pairs, skipped


def pending_items(data: Data, lang: str) -> list[TransItem]:
    """Fragments of the site still untranslated for ``lang``, deduped by key.

    Every node (published or not, pages and pure category labels alike)
    contributes its title; pages also contribute each chunk
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
            # Category labels (chunks is None) contribute only their title:
            # it is their nav-menu label.
            node_lang = node.language or inherited
            if node_lang != lang:
                if node.title:
                    emit(
                        chunk_key(node.title),
                        node.title,
                        path,
                        "title",
                        context=opening(node),
                    )
                for h in node.chunks or ():
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
            if node_lang != lang:
                keys = set(node.chunks or ())
                if node.title:
                    keys.add(chunk_key(node.title))
                if keys & stored:
                    node.langs[lang] = True
                    pages.append(path)
            walk(node.children, path, node_lang)

    walk(data.menu, "", i18n.ORIGINAL_LANGUAGE)
    return pages


class _Connection:
    """One connected translator socket: the language codes and job modes it
    announced (Hello), its model string, and the job currently in flight on
    it — one at a time, the next is sent only after its Result.

    Per-connection only: in-flight lives solely here, so on disconnect the
    item simply becomes pending again and is re-offered to any free capable
    connection."""

    def __init__(self, capable: set[str], modes: set[str], model: str) -> None:
        self.capable = capable
        self.modes = modes
        self.model = model
        self.inflight: tuple[str, bytes] | None = None
        self.mode: str = ""  #: the in-flight job's mode
        #: (lang, chunk key) pairs the in-flight job covers (an article job
        #: covers its page's pending chunks).
        self.items: set[tuple[str, bytes]] = set()
        #: segments mode: source spans of the in-flight job's segments
        #: (splice offsets and link marks).
        self.spans: list[Span] = []
        self.original: str = ""  # its full source text (splicing / alignment)
        self.kind: str = ""  # "chunk" | "title" | "article" | "nav"
        #: Article jobs with an injected title heading: the page title's
        #: chunk key (its translation is extracted from the result's first
        #: block, never stored as a body chunk).
        self.title_key: bytes | None = None

    def take(self) -> tuple[str, str, str, list[Span], bytes | None]:
        """Snapshot and clear the in-flight job's working state."""
        out = (self.mode, self.kind, self.spans, self.original, self.title_key)
        self.inflight = None
        self.items = set()
        self.mode = self.kind = ""
        self.spans = []
        self.original = ""
        self.title_key = None
        return out


class Dispatcher:
    """The translator dispatcher: connected client sockets and the job
    pipeline (docs/localization.md, docs/llm-translation.md).

    One single-item job at a time per connection, offered in the
    intersection of the wanted languages (``Data.translate_langs``), the
    connection's announced capabilities and its accepted job modes:
    ``article`` jobs (a whole page) only to article-capable connections and
    only while a page is mostly pending, steady-state follow-up as scoped
    ``markdown``/``segments`` jobs, and ``nav`` jobs (the whole menu tree
    as one nested list) only to nav-capable connections, ahead of any
    per-title jobs. Pending work is derived from the
    ``trans`` store (``pending_items``) minus the items in flight on any
    connection, so a dropped connection's in-flight item is simply
    re-offered. Results are matched to content by chunk key alone (an
    article job's key is its page's first chunk, a nav job's the hash of
    its list Markdown). A (lang, key, mode)
    whose Result fails validation is skipped for the rest of the run —
    generation is near-deterministic per model, so an immediate retry in
    the same mode would just re-fail, while other modes stay offerable.
    """

    def __init__(self, data: Data, db: Kanta, invalidate) -> None:
        self.data = data
        self.db = db
        #: Sync content-change hook (state._invalidate_pages), called inside
        #: transactions; schedules the next dispatch pass.
        self.invalidate = invalidate
        #: Connected translator sockets and their per-connection state.
        self.clients: dict[WebSocket, _Connection] = {}
        #: (lang, chunk key, mode) of jobs whose result failed validation
        #: this run.
        self.validation_failures: set[tuple[str, bytes, str]] = set()

    def reset_validation_failures(self) -> None:
        """Clear the skip list of fragments rejected this run: a
        translations refresh is precisely the "another chance" for them."""
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

    def _scoped_job(self, item: TransItem, lang: str, mode: str) -> _Offer | None:
        """A title/chunk job for one pending item, in segments or markdown
        mode."""
        if mode == "segments":
            spans, texts, contexts = split(item.text)
            if not texts:
                return None  # prose that could not be located for splicing
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
        else:  # markdown: the fragment crosses whole, as Markdown
            if item.kind == "title":
                contexts = [item.context] if item.context else []
            else:
                contexts = self._block_contexts(lang, item)
            job = Job(
                lang=lang,
                key=item.key,
                texts=[item.text],
                path=item.path,
                kind=item.kind,
                mode="markdown",
                contexts=contexts,
            )
        return (
            job,
            spans if mode == "segments" else [],
            item.text,
            {(lang, item.key)},
            None,
        )

    def _block_contexts(self, lang: str, item: TransItem) -> list[str]:
        """The previous and next block of the served hybrid around a pending
        chunk (current machine translation with user overrides applied, so
        human corrections propagate into fresh translations)."""
        chain = resolve(self.data.menu, item.path)
        node = chain[-1] if chain else None
        if node is None or not node.chunks or item.key not in node.chunks:
            return []
        served = [
            self.data.trans.get(h, {}).get(lang) or self.data.chunks.get(h, "")
            for h in node.chunks
        ]
        blocks = chunk_markdown(i18n.hybrid_markdown(self.data, node, item.path, lang))
        i = node.chunks.index(item.key)
        # Map the chunk's served-list position onto the patched block list
        # (patches may merge, split or drop blocks).
        j = min(i, len(blocks))
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
            None, served, blocks, autojunk=False
        ).get_opcodes():
            if i1 <= i < i2:
                j = j1 + (i - i1) if tag == "equal" else j1
                break
        prev = blocks[j - 1] if 0 < j <= len(blocks) else ""
        next_ = blocks[j + 1] if j + 1 < len(blocks) else ""
        return [prev[-_CONTEXT_CHARS:], next_[:_CONTEXT_CHARS]]

    def _nav_job(self, lang: str, inflight: set[tuple[str, bytes]]) -> _Offer | None:
        """The whole navigation hierarchy as one nested-Markdown-list job
        (nav-capable connections only): every node title still pending for
        ``lang``, in menu order, indented by depth — pages and pure
        category labels alike, duplicates included (repeated titles keep
        the tree shape faithful and store under one key anyway).

        One round trip names the entire menu, and sibling titles translate
        in sight of each other. The result is decomposed back into
        per-title fragments by ``align_nav``; a structurally mangled list
        is rejected wholesale and the titles fall back to scoped title
        jobs. A lone pending title is served directly by a scoped job."""
        titles: list[tuple[int, str, bytes]] = []

        def walk(nodes: dict[str, Node], depth: int, inherited: str) -> None:
            for slug, node in sorted_nodes(nodes):
                node_lang = node.language or inherited
                if node_lang != lang and node.title and "\n" not in node.title:
                    key = chunk_key(node.title)
                    if (
                        lang not in self.data.trans.get(key, {})
                        and (lang, key) not in inflight
                        and (lang, key, "nav") not in self.validation_failures
                    ):
                        titles.append((depth, node.title, key))
                walk(node.children, depth + 1, node_lang)

        walk(self.data.menu, 0, i18n.ORIGINAL_LANGUAGE)
        if len(titles) < 2:
            return None
        md = "\n".join(f"{'  ' * depth}- {title}" for depth, title, _ in titles)
        key = chunk_key(md)
        if (lang, key, "nav") in self.validation_failures:
            return None
        job = Job(lang=lang, key=key, texts=[md], path="", kind="nav", mode="nav")
        return job, [], md, {(lang, k) for _, _, k in titles}, None

    def _article_job(
        self, lang: str, items: list[TransItem], inflight: set[tuple[str, bytes]]
    ) -> _Offer | None:
        """A whole-page job for the first page that is mostly pending for
        ``lang`` (a whole new article or a full refresh; steady-state edits
        stay scoped jobs). The job's key is the page's first chunk.

        When the render would inject the page title as an h1 (the body has
        none of its own), the job text carries the same ``# {title}`` line:
        the title translates in document context, and the opening
        paragraphs see the heading. The menu title's and parent node's
        existing translations (from a nav job or earlier work) ride along
        as contexts, so the heading can match the menu — or deliberately
        deviate where the content calls for it."""
        by_path: dict[str, list[TransItem]] = {}
        for item in items:
            if item.kind == "chunk":
                by_path.setdefault(item.path, []).append(item)
        for path, page_items in by_path.items():
            chain = resolve(self.data.menu, path)
            node = chain[-1] if chain else None
            if node is None or not node.chunks:
                continue
            total = {
                h
                for h in node.chunks
                if h not in node.no_trans
                and (text := self.data.chunks.get(h)) is not None
                and needs_translation(text)
            }
            pend = {item.key for item in page_items}
            if (
                not pend
                or len(pend) * 2 < len(total)
                or any((lang, key) in inflight for key in pend)
            ):
                continue
            key = node.chunks[0]
            if (lang, key, "article") in self.validation_failures:
                continue
            md = node_markdown(self.data, node) or ""
            title_key = None
            contexts: list[str] = []
            if node.title and not has_h1(md):
                md = f"# {node.title}\n\n{md}"
                title_key = chunk_key(node.title)
                parent = chain[-2] if len(chain) >= 2 else None
                contexts = [
                    self.data.trans.get(title_key, {}).get(lang, ""),
                    self.data.trans.get(chunk_key(parent.title), {}).get(lang, "")
                    if parent is not None and parent.title
                    else "",
                ]
            covered = {(lang, k) for k in pend}
            if title_key is not None:
                covered.add((lang, title_key))
            job = Job(
                lang=lang,
                key=key,
                texts=[md],
                path=path,
                kind="article",
                mode="article",
                contexts=contexts,
            )
            return job, [], md, covered, title_key
        return None

    def _pick(
        self, state: _Connection, langs: list[str], inflight: set[tuple[str, bytes]]
    ) -> _Offer | None:
        """The next job for a free connection: the navigation tree before
        titles before articles before chunks — across languages too, so
        every menu is named before any article body is worked on (a page's
        name is its most visible string). pending_items emits in menu
        order, a page's title before its chunks."""
        pending = {lang: pending_items(self.data, lang) for lang in langs}
        scoped = (
            "markdown"
            if "markdown" in state.modes
            else "segments"
            if "segments" in state.modes
            else ""
        )
        for kind in ("nav", "title", "article", "chunk"):
            for lang in langs:
                if kind == "nav":
                    if "nav" in state.modes and (
                        offer := self._nav_job(lang, inflight)
                    ):
                        return offer
                    continue
                if kind == "article":
                    if "article" in state.modes and (
                        offer := self._article_job(lang, pending[lang], inflight)
                    ):
                        return offer
                    continue
                if not scoped:
                    continue
                for item in pending[lang]:
                    if (
                        item.kind != kind
                        or (lang, item.key) in inflight
                        or (lang, item.key, scoped) in self.validation_failures
                    ):
                        continue
                    if offer := self._scoped_job(item, lang, scoped):
                        return offer
        return None

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
            langs = sorted(wanted & state.capable)
            if not langs:
                continue
            inflight = {item for s in self.clients.values() for item in s.items}
            offer = self._pick(state, langs, inflight)
            if offer is None:
                continue
            job, spans, original, items, title_key = offer
            state.inflight = (job.lang, job.key)  # before the await: no double-assign
            state.mode = job.mode
            state.kind = job.kind
            state.spans = spans
            state.original = original
            state.items = items
            state.title_key = title_key
            try:
                await ws.send_text(msgspec.json.encode(job).decode())
            except Exception:  # send failed: the receive loop cleans up
                logger.exception("Job send failed; dropping translator client")
                self.clients.pop(ws, None)

    def _results(
        self,
        mode: str,
        kind: str,
        key: bytes,
        original: str,
        spans: list[Span],
        texts: list[str],
        title_key: bytes | None = None,
    ) -> tuple[list[TransResult], list[bytes]] | None:
        """Validate a Result against its in-flight job and turn it into
        storable fragments plus the title keys a nav result failed at item
        level (empty for other modes); None when it fails validation (the
        caller skips the (lang, key, mode) for this run and the work stays
        pending)."""
        if mode == "segments":
            text = join(original, spans, texts) if len(texts) == len(spans) else None
            return ([TransResult(key=key, text=text)], []) if text is not None else None
        if mode == "markdown":
            block = clean_block(original, texts[0], kind) if len(texts) == 1 else None
            return ([TransResult(key=key, text=block)], []) if block else None
        if mode == "nav":
            out = align_nav(original, texts[0]) if len(texts) == 1 else None
            if out is None:
                return None
            pairs, skipped = out
            return [TransResult(key=k, text=t) for k, t in pairs], skipped
        pairs = align_article(original, texts[0]) if len(texts) == 1 else None
        if not pairs:
            return None
        if title_key is not None:
            # The job carried an injected "# {title}" heading: its pair
            # becomes the title fragment (heading text only, never a body
            # chunk). A demoted/merged heading just skips the title — it
            # stays pending for a scoped title job.
            heading = chunk_key(chunk_markdown(original)[0])
            title = ""
            kept = []
            for k, t in pairs:
                if k == heading and not title:
                    m = re.fullmatch(r"# (.+)", t)
                    if m and pure_prose(m.group(1)):
                        title = m.group(1)
                    continue
                kept.append((k, t))
            pairs = ([(title_key, title)] if title else []) + kept
        return [TransResult(key=k, text=t) for k, t in pairs], []

    async def handle_ws(self, ws: WebSocket, clientkey: str) -> None:
        """The /_translate/<key> channel (docs/localization.md).

        A wrong/empty key rejects the handshake (closing before accept
        makes Starlette answer HTTP 403). Protocol (JSON frames): the
        client opens with Hello(langs, model, modes) announcing its
        CAPABILITIES — the language codes its model can produce (normalized
        to translation tags; "en"/empty dropped) and the job modes it
        accepts — then answers each Job with its Result(lang, key, texts).
        A Result without an in-flight job or with a different (lang, key),
        a duplicate Hello, or any malformed frame closes the socket with a
        protocol error.
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
                        {tag for lang in msg.langs if (tag := i18n.base_tag(lang))},
                        set(msg.modes) & MODES or {"segments"},
                        msg.model,
                    )
                    self.clients[ws] = state
                    logger.info(
                        "translator connected: model=%r, modes=%s, langs=%s",
                        state.model,
                        sorted(state.modes),
                        sorted(state.capable),
                    )
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
                    mode, kind, spans, original, title_key = state.take()
                    out = self._results(
                        mode, kind, msg.key, original, spans, msg.texts, title_key
                    )
                    if out is None:
                        # The model broke the contract (bad segment count,
                        # markup in a segment, a merged/split block, a lost
                        # anchor): drop the result and skip the (lang, key,
                        # mode) for this run — the work stays pending and a
                        # restart, a refresh, another mode or a model change
                        # gets another chance.
                        self.validation_failures.add((lang, msg.key, mode))
                        logger.warning(
                            "[%s] %s result for %s rejected: failed validation",
                            lang,
                            mode,
                            msg.key.hex(),
                        )
                        self.schedule()
                        continue
                    results, skipped = out
                    if skipped:
                        # Titles a nav result mangled individually: skip
                        # them in future nav jobs, leaving them to scoped
                        # title jobs (which track their own failures).
                        self.validation_failures.update(
                            (lang, k, "nav") for k in skipped
                        )
                        logger.warning(
                            "[%s] nav result: %d title(s) failed validation, "
                            "left for scoped title jobs",
                            lang,
                            len(skipped),
                        )
                    with self.db.transaction(
                        f"translate:{lang}{':' + kind if kind != 'chunk' else ''}",
                        user=clientkey,
                    ):
                        paths = store_results(self.data, lang, results)
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

"""Segmented translation round trip: prose out, translations back in.

A translator model mangles anything that is not plain prose — sentinels get
renumbered, ``![`` becomes sentence punctuation, stray ``<br>`` tags appear.
So the model is never shown any of it: a fragment (a Markdown chunk or a
node title) is parsed with the project's own markdown-it setup
(``markdown.make_md(verbatim=True)`` — extensions included, so container,
attrs, footnote and tasklist syntax never leaks into text tokens) and split
into **prose segments**: the merged text runs, plus image alt texts and
link/image titles. Only those cross the wire, as a plain list of strings
(Job.texts / Result.texts in translate.py) — accompanied, per segment, by
a CONTEXT (Job.contexts): a segment carved out of a larger block (a link
text, a partial run) carries the block's plain text, so the model sees the
sentence it lives in; whole-block segments are self-contextualizing and
carry "". Title fragments carry the article's opening instead (assigned by
the dispatcher from TransItem.context).

Reassembly is server-side offset splicing, not text the model produced:
each segment's source span was located at dispatch (``split``), and
``join`` swaps in the translations. Markup therefore cannot break — it
never left the server. A returned segment must still be pure prose itself
(the model could inject markup INTO a segment); anything else — count
mismatch, empty segment, markup tokens — rejects the whole result and the
fragment stays pending.

A block of plain text, prose links and paired text formatting
(strong/em/s) crosses as ONE segment — link texts and formatted text
inline, in sentence context, with the Markdown stripped (the model
mangles it: sentinels get renumbered, ``**`` gets dropped or moved) —
because a label translated apart from its sentence comes back
grammatically incompatible with it (case government, particles, word
order). ``join`` re-inserts the link/formatting markdown into the
translated block at weight-mapped positions (``_place_marks``): no
markers on the wire, the boundaries are found by text processing alone —
each mark's word/CJK-char weight ratio in the source applied to the
translation's units. Placement is approximate and CJK-safe: better a
coherent sentence with a slightly shifted link than separately translated
snippets that don't fit together. Blocks with any other inline markup
(code, images, HTML) still split into runs at those boundaries.

Locating is best effort: a run that is not a verbatim source substring
(entity-decoded text, backslash escapes) is skipped — it simply stays in
the original language. So is any piece containing "<": "<" is the
prose/markup boundary on the wire — translators cut their output there,
so such pieces could not survive the round trip.
"""

import re
from typing import NamedTuple

from pagerite.markdown import make_md

#: The segmentation parser: the project's own markdown-it, verbatim flavor
#: (see make_md). Never used for rendering.
_MD = make_md(verbatim=True)

#: Any Unicode letter (digits and underscore are not prose).
_LETTER = re.compile(r"[^\W\d_]")

#: A GFM alert marker ([!NOTE] etc.) at the start of a blockquote's first
#: paragraph: syntax, not prose — stripped from the first segment.
_ALERT = re.compile(r"^\[![A-Za-z]+\][ \t]*")

#: Any {...} span: {placeholders} and attrs that ended up inside prose
#: (inline attrs are consumed by the parser; a lone {dates} is not).
_BRACES = re.compile(r"\{[^{}\n]*\}")

#: A link's tail after its text: "](dest)", "](dest \"title\")", "][ref]",
#: "[]" or a bare "]" (shortcut reference); the destination may nest one
#: level of parens. Best effort — a mis-scan fails the span-reconstruction
#: check in _linked_block and the block falls back to per-run segments.
_LINK_TAIL = re.compile(r"\](?:\((?:\\.|[^()\\]|\([^()]*\))*\)|\[(?:\\.|[^\]])*\])?")

#: Weight units for mapping link boundaries from source to translation:
#: a word counts 1 and so does every single CJK ideograph (kana runs count
#: as one) — CJK has no spaces to count words by. Punctuation and
#: whitespace count nothing, so mapped boundaries always land on unit
#: starts.
_UNIT = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]"  # CJK ideographs: one unit each
    r"|[\u3040-\u309f\u30a0-\u30ff]+"  # kana runs: one unit each
    r"|\w+"  # anything else word-like (Latin, Cyrillic, Hangul, digits)
)


class Mark(NamedTuple):
    """One inline link or paired formatting (strong/em/s) inside a
    whole-block segment: the source weight (unit count, see _UNIT) at the
    inner text's start and end for mapping the boundaries into the
    translation, the exact source syntax around the text ("[" / "](url)",
    "**" / "**", ...) and the source text itself, used as the fallback when
    the mapped slice comes out empty (better an untranslated label than a
    broken "[](url)")."""

    w_start: int
    w_end: int
    pre: str
    post: str
    inner: str


class Span(NamedTuple):
    """A segment's source span in the fragment: offsets for splicing the
    translation back, the segment's source weight and the links to
    re-insert into its translation (empty = a plain prose segment)."""

    start: int
    end: int
    weight: int
    marks: list[Mark]


def _weight(text: str) -> int:
    """The text's weight in translation-mapping units (see _UNIT)."""
    return len(_UNIT.findall(text))


def _unit_bounds(text: str) -> list[int]:
    """Unit-start offsets of the text, plus its end as the last bound."""
    return [m.start() for m in _UNIT.finditer(text)] + [len(text)]


def _runs(children: list) -> list[str]:
    """Prose runs of an inline token's children, in order.

    Text tokens merge across soft breaks into one run; every markup token
    (emphasis, links, code, images, HTML, footnote refs, hard breaks) is a
    run boundary. Link and image *text* is prose; autolink text (the URL
    itself) is not. Image tokens contribute their alt-text children and
    their title attribute.
    """
    runs: list[str] = []
    cur: list[str] = []

    def flush() -> None:
        if cur:
            s = "".join(cur)
            cur.clear()
            if _LETTER.search(s):
                runs.append(s)

    skip = 0  # inside an autolink (its text is the URL — not prose)
    for t in children:
        if skip:
            if t.type == "link_close":
                skip -= 1
            continue
        if t.type == "text":
            cur.append(t.content)
        elif t.type == "softbreak":
            cur.append("\n")
        elif t.type == "link_open" and t.markup == "autolink":
            flush()
            skip = 1
        elif t.type == "image":
            flush()
            if t.children:
                runs.extend(_runs(t.children))
            title = t.attrGet("title")
            if title and _LETTER.search(title):
                runs.append(title)
        else:
            flush()
            if t.children:
                runs.extend(_runs(t.children))
    flush()
    return runs


def _block_text(children: list) -> str:
    """The block's text as a reader sees it: text runs and link texts
    merged (softbreaks as newlines); image alts, autolink URLs, code and
    other markup content excluded. Used as the translation CONTEXT for
    segments carved out of the block (link texts, partial runs): a lone
    word translates differently than the same word inside its sentence."""
    parts: list[str] = []
    skip = 0  # inside an autolink (its text is the URL)
    for t in children:
        if skip:
            if t.type == "link_close":
                skip -= 1
            continue
        if t.type == "text":
            parts.append(t.content)
        elif t.type == "softbreak":
            parts.append("\n")
        elif t.type == "link_open" and t.markup == "autolink":
            skip = 1
        elif t.type == "image":
            continue
        elif t.children:
            parts.append(_block_text(t.children))
    return "".join(parts)


def _locate(source: str, needle: str, cursor: int) -> int:
    """The needle's offset in source at/after cursor, -1 when absent.

    An occurrence preceded by a backslash is an escaped character, not the
    token's source: keep looking (failing that, the run is skipped — it
    stays in the original language).
    """
    pos = source.find(needle, cursor)
    while pos > 0 and source[pos - 1] == "\\":
        pos = source.find(needle, pos + 1)
    return pos


def _linked_block(
    source: str, kids: list, cursor: int, strip_alert: bool
) -> tuple[Span, str] | None:
    """A whole-block segment for an inline of plain text, prose links and
    paired text formatting (strong/em/s): (Span, wire text) with the links
    and formatting as marks, or None when the block has any other shape —
    the caller then falls back to per-run segments.

    The block crosses the wire as one prose piece, link texts and formatted
    text inline (the model is never shown any Markdown — it mangles it),
    so a translation that inflects or reorders around them stays coherent;
    join re-inserts the link/formatting syntax at weight-mapped positions.
    The source span is located piece by piece and verified by
    reconstruction; anything not byte-exact (entities, escapes, an odd
    link tail) bails to the fallback.
    """
    pieces: list[
        tuple[str, str]
    ] = []  # (text, mark): "" plain, "link", else the delimiter
    buf: list[str] = []  # current plain piece
    link: list[str] | None = None  # current mark's text parts
    mark_kind = ""  # the current mark's opener ("link" or the delimiter)
    for tok in kids:
        if tok.type in ("link_open", "strong_open", "em_open", "s_open"):
            if link is not None or tok.markup == "autolink":
                return None
            if buf:
                pieces.append(("".join(buf), ""))
                buf = []
            link = []
            mark_kind = "link" if tok.type == "link_open" else tok.markup
        elif tok.type in ("link_close", "strong_close", "em_close", "s_close"):
            if (
                link is None
                or ("link" if tok.type == "link_close" else tok.markup) != mark_kind
            ):
                return None
            inner = "".join(link)
            if not _LETTER.search(inner):
                return None
            pieces.append((inner, mark_kind))
            link = None
        elif tok.type in ("text", "softbreak"):
            (link if link is not None else buf).append(
                "\n" if tok.type == "softbreak" else tok.content
            )
        else:  # code, images, HTML, footnote refs: run boundaries
            return None
    if link is not None:
        return None  # unbalanced (the parser should not do this)
    if buf:
        pieces.append(("".join(buf), ""))
    if not any(mark for _, mark in pieces):
        return None
    if strip_alert and pieces and not pieces[0][1]:
        # A GFM alert marker leading the blockquote's first paragraph is
        # syntax; strip it from the wire text (it stays out of the span).
        first = _ALERT.sub("", pieces[0][0], count=1)
        if first.strip():
            pieces[0] = (first, "")
        else:
            pieces.pop(0)
            if not pieces:
                return None
    raw = "".join(text for text, _ in pieces)
    lead = len(raw) - len(raw.lstrip())
    wire = raw.strip()
    if not _LETTER.search(wire) or "<" in wire or _BRACES.search(wire):
        return None
    # Locate each piece verbatim, in order; the source slices between the
    # located pieces are then the link syntax, exact by construction.
    located: list[tuple[int, int]] = []
    pos = cursor
    for text_, _ in pieces:
        at = _locate(source, text_, pos)
        if at == -1:
            return None
        located.append((at, at + len(text_)))
        pos = at + len(text_)
    span_start, span_end = located[0][0], located[-1][1]
    marks: list[Mark] = []
    offset = 0  # raw (pre-strip) plain-text offset of the current piece
    for i, ((text_, kind), (s, e)) in enumerate(zip(pieces, located)):
        if not kind:
            offset += len(text_)
            continue
        # The syntax around the text: the gap between pieces goes to the
        # mark on its left as post (so between two marks the whole "](u)["
        # or "**" is the first's post); a block-leading mark takes its
        # opener in front of its text ("[" or the delimiter), a
        # block-trailing one the scanned link tail or the close delimiter.
        if i == 0:
            opener = "[" if kind == "link" else kind
            if s < len(opener) or source[s - len(opener) : s] != opener:
                return None
            pre, span_start = opener, s - len(opener)
        elif pieces[i - 1][1]:
            pre = ""  # the previous mark's post covers the whole gap
        else:
            pre = source[located[i - 1][1] : s]
        if i + 1 < len(pieces):
            post = source[e : located[i + 1][0]]
        elif kind == "link":
            m = _LINK_TAIL.match(source, e)
            if m is None:
                return None
            post, span_end = m.group(), m.end()
        else:
            if source[e : e + len(kind)] != kind:
                return None
            post, span_end = kind, e + len(kind)
        ps = min(max(offset - lead, 0), len(wire))
        pe = min(max(offset + len(text_) - lead, 0), len(wire))
        if pe <= ps:
            return None
        marks.append(
            Mark(_weight(wire[:ps]), _weight(wire[:pe]), pre, post, wire[ps:pe])
        )
        offset += len(text_)
    # Verify: the marks must reconstruct the source span exactly (the only
    # real risk is the guessed tail of a trailing link).
    rec: list[str] = []
    mi = 0
    for text_, kind in pieces:
        if kind:
            mark = marks[mi]
            mi += 1
            rec += [mark.pre, text_, mark.post]
        else:
            rec.append(text_)
    if source[span_start:span_end] != "".join(rec):
        return None
    return Span(span_start, span_end, _weight(wire), marks), wire


def split(text: str) -> tuple[list[Span], list[str], list[str]]:
    """Split a fragment into (spans, segments, contexts): prose segments to
    translate, their source spans in ``text`` for splicing the translations
    back, and per-segment translation context.

    A block of plain text, prose links and paired formatting (strong/em/s)
    becomes ONE segment (link/formatted text inline, in context, Markdown
    stripped), the links and formatting recorded as marks on its Span for
    weight-mapped re-insertion in join. Other blocks split into text runs
    at markup boundaries; runs containing {...} spans are carved further —
    the braces stay out of the wire text. A run that cannot be located
    verbatim in the source contributes no segment. A segment's context is
    its block's plain text when the segment was carved OUT of a larger
    block (a partial run); a segment that IS the whole block (a plain
    paragraph, a heading, a linked block) is self-contextualizing and gets
    "".
    """
    spans: list[Span] = []
    segments: list[str] = []
    contexts: list[str] = []
    cursor = 0
    blockquote_fresh = 0  # blockquote depth whose first inline is upcoming

    def emit(run: str, at: int, ctx: str) -> None:
        """Carve {...} spans out of the located run; emit the prose pieces,
        stripped — padding whitespace stays in the template, off the wire.
        Pieces containing "<" are never emitted: translators cut output at
        the first "<" (the prose/markup boundary, scripts/translator.py),
        so such a piece could not survive the round trip — it stays in the
        original language instead."""
        pieces = []
        pos = 0
        for m in _BRACES.finditer(run):
            pieces.append((pos, m.start()))
            pos = m.end()
        pieces.append((pos, len(run)))
        for p0, p1 in pieces:
            raw = run[p0:p1]
            piece = raw.strip()
            if _LETTER.search(piece) and "<" not in piece:
                start = at + p0 + (len(raw) - len(raw.lstrip()))
                spans.append(Span(start, start + len(piece), 0, []))
                segments.append(piece)
                contexts.append(ctx)

    tokens = _MD.parse(text)
    for t in tokens:
        if t.type == "blockquote_open":
            blockquote_fresh += 1
        elif t.type == "blockquote_close":
            blockquote_fresh -= 1
        elif t.type == "inline":
            kids = t.children or []
            # An alert marker ([!NOTE]) leading a blockquote's first
            # paragraph is syntax; both paths strip it. (Only the first
            # inline of the blockquote can carry it — the flag clears on
            # the first inline seen.)
            alert = bool(blockquote_fresh)
            blockquote_fresh = 0
            linked = _linked_block(text, kids, cursor, strip_alert=alert)
            if linked is not None:
                span, wire = linked
                spans.append(span)
                segments.append(wire)
                contexts.append("")
                cursor = span.end
                continue
            runs = _runs(kids)
            block = _block_text(kids).strip()
            if alert and runs:
                run = _ALERT.sub("", runs[0], count=1)
                if _LETTER.search(run):
                    runs[0] = run
                else:
                    runs.pop(0)
            for run in runs:
                ctx = block if block and run.strip() != block else ""
                pos = _locate(text, run, cursor)
                if pos != -1:
                    emit(run, pos, ctx)
                    cursor = pos + len(run)
                elif "\n" in run:
                    # Indented continuation lines etc. break the verbatim
                    # match: locate each line separately instead.
                    for part in run.split("\n"):
                        if not _LETTER.search(part):
                            continue
                        pos = _locate(text, part, cursor)
                        if pos != -1:
                            emit(part, pos, ctx)
                            cursor = pos + len(part)
    return spans, segments, contexts


def pure_prose(text: str) -> bool:
    """True when the text parses as nothing but prose (text and softbreak
    tokens) — the acceptance test for a translated segment: the model may
    not return markup of its own (a `<br>` here would splice live HTML into
    the fragment)."""
    children = _MD.parseInline(text)[0].children or []
    return all(t.type in ("text", "softbreak") for t in children)


def _place_marks(translation: str, weight: int, marks: list[Mark]) -> str | None:
    """Re-insert a whole-block segment's links into its translation.

    Each mark's source weight ratio (units before the boundary / total) is
    applied to the translation's units — a rough bilingual alignment that
    needs no markers in the wire text (sentinels never survived the model)
    and works for CJK, where exact placement matters less. A boundary
    landing empty degrades to the source link text: better an untranslated
    label than a broken "[](url)". None when the translation has no units
    to map onto (the caller rejects the result).
    """
    bounds = _unit_bounds(translation)
    total = len(bounds) - 1
    if not total or not weight:
        return None
    out: list[str] = []
    cur = 0
    for mark in marks:
        x1 = bounds[min(round(mark.w_start / weight * total), total)]
        x2 = bounds[min(round(mark.w_end / weight * total), total)]
        x1 = max(x1, cur)  # monotonic: never before the previous mark's end
        x2 = max(x2, x1)
        # The slice ends at the next unit's start, so the whitespace and
        # punctuation before that unit is inside it — but it belongs
        # BETWEEN the mark and the following word, not in the inner text:
        # end the inner text at its last unit and leave the rest for the
        # following slice (the cursor stays ahead of it).
        raw = translation[x1:x2]
        units = list(_UNIT.finditer(raw))
        inner_end = x1 + units[-1].end() if units else x1
        inner = translation[x1:inner_end].strip() or mark.inner
        out += [translation[cur:x1], mark.pre, inner, mark.post]
        cur = inner_end
    out.append(translation[cur:])
    return "".join(out)


def join(original: str, spans: list[Span], texts: list[str]) -> str | None:
    """Splice translated segments back into the original fragment; None on
    any validation failure (count mismatch, empty or non-prose segment) —
    the caller drops the result and the fragment stays pending. Segments
    with marks (a block that crossed as one piece) get their links
    re-inserted at weight-mapped positions after the prose check."""
    if len(texts) != len(spans):
        return None
    out: list[str] = []
    cursor = 0
    for span, translation in zip(spans, texts):
        if not translation.strip() or not pure_prose(translation):
            return None
        if span.marks:
            translation = _place_marks(translation, span.weight, span.marks)
            if translation is None:
                return None
        out.append(original[cursor : span.start])
        out.append(translation)
        cursor = span.end
    out.append(original[cursor:])
    return "".join(out)


def has_prose(text: str) -> bool:
    """True when the fragment yields at least one translatable segment.
    Chunks that are all markup, code, placeholders or reference definitions
    have no business reaching the model: every language renders them from
    the original chunk."""
    return bool(split(text)[1])

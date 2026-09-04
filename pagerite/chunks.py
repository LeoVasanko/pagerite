"""Block-level Markdown chunking for content-addressed storage.

A page's Markdown is split into deterministic block-level chunks, each
stored once under its content hash in ``Data.chunks`` (docs/migrate.md).
Shared by the render/save pipeline (app.py, views.py, i18n.py) and the
schema migration (migrations.py), so a chunk's key is stable no matter
where the split happens.
"""

import re

import blake3

from pagerite.segments import has_prose

#: Fenced code block opener/closer: up to 3 spaces indent, then 3+
#: backticks or tildes (CommonMark).
_FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})")

#: A container fence line (mdit-py-plugins container): the "::: aside"
#: opener and the ":::" closer alike. Always its own block, even with no
#: blank line around it: folded into a prose paragraph it would cross to
#: the translator as part of the text run, where the model can drop it —
#: the rest of the page then renders inside the container.
_CONTAINER = re.compile(r"^ {0,3}:{3,}(?:[ \t]|$)")

#: HTML block openers that may span blank lines (CommonMark types 1-5:
#: script/pre/style/textarea, comments, processing instructions,
#: declarations, CDATA) with their closing condition. Other HTML blocks
#: end at the first blank line, which the generic blank-line split
#: already does.
_HTML_ATOMIC = (
    (
        re.compile(r"^ {0,3}<(?:script|pre|style|textarea)(?:\s|>|$)", re.I),
        re.compile(r"</(?:script|pre|style|textarea)\s*>", re.I),
    ),
    (re.compile(r"^ {0,3}<!--"), re.compile(r"-->")),
    (re.compile(r"^ {0,3}<\?"), re.compile(r"\?>")),
    (re.compile(r"^ {0,3}<!\[CDATA\["), re.compile(r"\]\]>")),
    (re.compile(r"^ {0,3}<![A-Za-z]"), re.compile(r">")),
)

#: First line of a generic HTML block (a block-level tag).
_HTML_TAG = re.compile(r"^ {0,3}</?[A-Za-z][^>]*>")


def _fence_close(line: str, opener: str) -> bool:
    """True when ``line`` closes a code fence opened by ``opener``: the
    same marker char, at least as many, and nothing else on the line."""
    stripped = line.strip()
    return (
        len(stripped) >= len(opener)
        and stripped[0] == opener[0]
        and set(stripped) == {opener[0]}
    )


def chunk_markdown(markdown: str) -> list[str]:
    """Split Markdown into block-level chunks, deterministically.

    Blocks are separated by blank lines; fenced code blocks and the
    multi-line HTML blocks (comments, script/pre/style, CDATA...) are
    kept atomic, even across blank lines, and end at their closing
    condition. Container fence lines (:::, open and close alike) are
    always their own block, blank lines or not (see _CONTAINER). Chunks
    carry no surrounding blank lines and no trailing newline; rejoining
    with ``join_chunks`` reproduces the source modulo blank-line
    normalization.
    """
    chunks: list[str] = []
    buf: list[str] = []
    fence = ""  # opener marker of the code fence we are in ("" = outside)
    html_end: re.Pattern | None = None  # closes the atomic HTML block we are in

    def flush() -> None:
        text = "\n".join(buf).strip("\n")
        if text.strip():
            chunks.append(text)
        buf.clear()

    for line in markdown.split("\n"):
        if fence:
            buf.append(line)
            if _fence_close(line, fence):
                fence = ""
                flush()
            continue
        if html_end is not None:
            buf.append(line)
            if html_end.search(line):
                html_end = None
                flush()
            continue
        if not line.strip():
            flush()
            continue
        if m := _FENCE_OPEN.match(line):
            # Fences interrupt paragraphs (CommonMark): start a new block.
            flush()
            fence = m.group(1)
            buf.append(line)
            continue
        if _CONTAINER.match(line):
            # Container fence lines (open and close alike) are their own
            # block — never part of a prose chunk (see _CONTAINER).
            flush()
            buf.append(line)
            flush()
            continue
        if not buf:
            for open_re, close_re in _HTML_ATOMIC:
                if open_re.match(line):
                    buf.append(line)
                    if close_re.search(line):  # opens and closes on one line
                        flush()
                    else:
                        html_end = close_re
                    break
            else:
                buf.append(line)
            continue
        buf.append(line)
    flush()  # an unterminated fence/HTML block runs to EOF, kept as code/HTML
    return chunks


def _normalize(text: str) -> str:
    """Whitespace-insensitive chunk identity: strip trailing whitespace
    per line and collapse surrounding blank lines, so whitespace-only
    source edits don't invalidate translations."""
    return "\n".join(line.rstrip() for line in text.split("\n")).strip("\n")


def chunk_key(text: str) -> bytes:
    """Content key of a chunk: the first 9 bytes of the blake3 digest of
    the normalized text (72 bits — a site's chunk count stays far below
    the birthday bound), using the same hasher as app.py's file store.

    Keys are bytes: kanta/msgspec base64-encode them at the JSON
    persistence level, so the raw database dicts carry 12-char strings.
    """
    return blake3.blake3(_normalize(text).encode()).digest(9)


def needs_translation(chunk: str) -> bool:
    """False for chunks without prose: pure code fences, HTML blocks, and
    anything that yields no translatable segments (pagerite/segments.py) —
    container fences, lone {placeholders}, reference definitions.

    These are inherently no-translate (docs/migrate.md): derived from the
    chunk text itself, nothing is stored. Every language renders them from
    the original chunk via the hybrid fallback.
    """
    if _FENCE_OPEN.match(chunk):
        return False
    first = chunk.split("\n", 1)[0]
    if any(open_re.match(first) for open_re, _ in _HTML_ATOMIC):
        return False
    if _HTML_TAG.match(first):
        return False
    return has_prose(chunk)


def join_chunks(chunks: list[str]) -> str:
    """The stored page form of chunks: blocks joined by a blank line,
    with a trailing newline ("" for no chunks)."""
    return "\n\n".join(chunks) + "\n" if chunks else ""


def store_chunks(store: dict[bytes, str], markdown: str) -> list[bytes]:
    """Chunk ``markdown`` into ``store`` (hash -> text); return the ordered
    hashes. Unchanged chunks keep their hashes, so only genuinely new text
    lands in the kanta change diff. First writer wins: variants sharing a
    key differ only in insignificant whitespace (see chunk_key)."""
    hashes = []
    for chunk in chunk_markdown(markdown):
        key = chunk_key(chunk)
        store.setdefault(key, chunk)
        hashes.append(key)
    return hashes

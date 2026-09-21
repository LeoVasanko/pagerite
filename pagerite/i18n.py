"""Localization: language selection, translation storage and assembly.

See docs/localization.md and docs/migrate.md. Each article's primary
language is ``Node.language``, inherited down the hierarchy (front page =
site default, ORIGINAL_LANGUAGE as the final fallback). The database holds
the original language as content-addressed chunks (``Data.chunks``); per
target language there are machine-translated fragments (``Data.trans``)
and user overrides (``Data.overrides``), assembled into the served
Markdown at render time, with per-node fallback to the original titles.
"""

import secrets
from collections.abc import Callable
from difflib import SequenceMatcher

import msgspec

from pagerite.chunks import chunk_key, chunk_markdown, join_chunks
from pagerite.data import ChunkEdit, Data, LangEdits, Node, resolve

#: Final fallback for a page's primary language when neither it nor any
#: ancestor (up to the front page) sets one (Node.language, "" = inherit).
ORIGINAL_LANGUAGE = "en"

#: Languages written right-to-left; pages served in one get dir="rtl" on
#: <html> (views._layout).
RTL_LANGUAGES = frozenset({"ar", "fa", "he", "ur"})


def primary_lang(menu: dict[str, Node], path: str) -> str:
    """The primary language of the article at ``path``: its own
    ``language`` setting, else the nearest ancestor's (the front page
    last — it doubles as the site default), falling back to
    ORIGINAL_LANGUAGE. Missing tail segments (a page being created)
    resolve to the nearest existing ancestor."""
    p = path.strip("/")
    while True:
        chain = resolve(menu, p)
        if chain:
            for node in reversed(chain):
                if node.language:
                    return node.language
        if not p:
            return ORIGINAL_LANGUAGE
        p = p.rpartition("/")[0]


class Translation(msgspec.Struct, omit_defaults=True):
    """Translated content for one page and language.

    ``markdown`` is the translated page source in the same format as the
    original (None = keep the original Markdown); ``titles`` maps node paths
    (top-level slug, then slash-joined) to translated navigation titles, so a
    partially translated tree still renders with per-node English fallback.
    """

    markdown: str | None = None
    titles: dict[str, str] = {}


def base_tag(tag: str) -> str:
    """The lowercase base subtag of a language tag (fi-FI -> fi)."""
    return tag.strip().lower().partition("-")[0]


def parse_accept_language(header: str) -> list[str]:
    """Accept-Language header as an ordered, deduped list of base subtags.

    q-values are deliberately ignored: all known implementations send the
    header in order of preference. Region tags normalize to their base
    subtag (fi-FI -> fi); "*" and empties are dropped.
    """
    langs = []
    for part in header.split(","):
        tag = base_tag(part.split(";", 1)[0])
        if tag and tag != "*" and tag not in langs:
            langs.append(tag)
    return langs


def select_language(
    query_lang: str | None,
    accept_language: str | None,
    is_available: Callable[[str], bool],
    original: str = ORIGINAL_LANGUAGE,
) -> str:
    """The language to serve (see docs/localization.md).

    1. ``?lang=`` wins when a translation exists for it (otherwise falls
       through to the header logic).
    2. Otherwise the first header language that can be served — the
       original, or one with an available translation.
    3. Fall back to the original.
    """
    if query_lang:
        tag = base_tag(query_lang)
        if tag == original or (tag and is_available(tag)):
            return tag
    for lang in parse_accept_language(accept_language or ""):
        if lang == original or is_available(lang):
            return lang
    return original


def hybrid_items(data: Data, node: Node, path: str, lang: str) -> list[tuple[bytes | None, str]]:
    """The served hybrid as (anchor, block text) pairs: the anchor is the
    ORIGINAL chunk hash behind the block (None for translation-only
    addition blocks), in article order.

    User overrides (``Data.overrides``) are structural: walking the
    article's own chunk order, each original chunk contributes its
    before-addition, the chunk itself (dropped, or its text replaced
    wholesale by the edit's ``replace``), and its after-addition. An
    override for a hash the article no longer contains never applies; an
    addition id referenced from two neighbors is emitted once, at the
    first live referrer.
    """
    le = (data.overrides.get(path) or {}).get(lang)
    items: list[tuple[bytes | None, str]] = []
    emitted: set[str] = set()

    def emit_add(add_id: str) -> None:
        if le and add_id not in emitted and (md := le.adds.get(add_id)):
            emitted.add(add_id)
            items.extend((None, block) for block in chunk_markdown(md))

    for h in node.chunks or []:
        edit = le.chunks.get(h) if le else None
        if edit is not None:
            emit_add(edit.before)
        if edit is None or not edit.drop:
            text = (
                data.chunks.get(h, "")
                if h in node.no_trans
                else data.trans.get(h, {}).get(lang) or data.chunks.get(h, "")
            )
            if edit is not None and edit.replace:
                text = edit.replace
            items.extend((h, block) for block in chunk_markdown(text))
        if edit is not None:
            emit_add(edit.after)
    return items


def hybrid_markdown(data: Data, node: Node, path: str, lang: str) -> str:
    """The served Markdown for ``lang``: per chunk the translation from
    ``Data.trans``, unless missing or marked no-translate (fallback to the
    original chunk), with the language's user overrides applied
    structurally (hybrid_items).

    Not gated on ``node.langs`` (get_translation is the gated view): the
    editor save path diffs against this even for a language's first edit.
    """
    return join_chunks([text for _, text in hybrid_items(data, node, path, lang)])


#: Minimum block similarity for two blocks in a shrunk replace region to
#: pair as a text edit (a per-chunk replace patch) rather than a
#: drop + insertion (_refine_replace).
_PAIR_MIN = 0.5


def _refine_replace(
    a: list[str], i1: int, i2: int, b: list[str], j1: int, j2: int
) -> list[tuple[str, int, int, int, int]]:
    """Split a ``replace`` opcode that removed blocks (more source than
    edited blocks) into single-block sub-opcodes: greedily pair the most
    similar source/edited blocks as text edits — a sentence fixed in the
    paragraph above a deleted paragraph must not drag the deletion into
    the same replace pair — leaving unpaired source blocks as deletions
    and any unpaired edited blocks as insertions.

    Only shrunk regions are refined: 1:1 replacements (up to a full
    paragraph rewrite) and paragraph splits stay single replace pairs by
    design. Regions are a handful of blocks, so the O(n*m) pairing with a
    character-level ratio per candidate is cheap, and pages are small, so
    the greedy best-first order is deterministic enough.
    """
    paired: list[tuple[int, int]] = []
    left_a = list(range(i1, i2))
    left_b = list(range(j1, j2))
    while left_a and left_b:
        ratio, ai, bj = max(
            (SequenceMatcher(None, a[x], b[y], autojunk=False).ratio(), x, y)
            for x in left_a
            for y in left_b
        )
        if ratio < _PAIR_MIN:
            break
        paired.append((ai, bj))
        left_a.remove(ai)
        left_b.remove(bj)
    ops = []
    for ai, bj in paired:
        ops.append((ai, bj, ("replace", ai, ai + 1, bj, bj + 1)))
    for ai in left_a:
        ops.append((ai, j1, ("delete", ai, ai + 1, j1, j1)))
    for bj in left_b:
        # Anchor an unpaired insertion just after the nearest preceding
        # paired source block (the region start when none).
        pos = max((ai + 1 for ai, prev in paired if prev < bj), default=i1)
        ops.append((pos, bj, ("insert", pos, pos, bj, bj + 1)))
    return [op for _, _, op in sorted(ops, key=lambda e: (e[0], e[1]))]


def record_override(
    data: Data, node: Node, path: str, lang: str, edited: str, base: str | None = None
) -> bool:
    """Record a translated-view edit as user overrides (``Data.overrides``):
    the block-level diff of ``edited`` against ``base`` (default: the
    currently served hybrid), classified per original chunk (docs/
    localization.md):

    - a changed block becomes its chunk's full-text ``replace`` patch — a
      re-edit composes into the patch;
    - a removed block becomes its chunk's ``drop``;
    - new blocks become an addition in ``adds``, anchored from the
      neighboring chunks' ``before``/``after`` (inserts next to existing
      addition text splice into that addition instead).

    Each save touches only the keys of the chunks actually edited. Every
    classification is best effort: a diff position whose base text no
    longer matches what the hybrid serves there (the original or the
    machine translation moved under an open editor) is skipped rather than
    recorded against the wrong chunk. Overrides alone make the translated
    version exist, so ``node.langs`` is set. Returns True when anything
    was recorded. Pure data ops — the caller wraps in a transaction and
    invalidates.

    The callers reject pages without original chunks (there is nothing to
    anchor a translation to); should one slip through, the diff finds no
    anchors and nothing is recorded.
    """
    le = (data.overrides.get(path) or {}).get(lang)
    items = hybrid_items(data, node, path, lang)
    a = chunk_markdown(base) if base is not None else [text for _, text in items]
    b = chunk_markdown(edited)
    aligned = len(a) == len(items)
    changed = False

    def edits() -> LangEdits:
        nonlocal le
        if le is None:
            le = data.overrides.setdefault(path, {}).setdefault(lang, LangEdits())
        return le

    def anchor_at(i: int) -> bytes | None:
        return items[i][0] if aligned else None

    def verified(i: int) -> bool:
        """The diff position still holds the text the hybrid serves there
        (False when the original or the translation moved under an open
        editor — structural ops against a shifted position are skipped)."""
        return aligned and a[i] == items[i][1]

    def served(h: bytes) -> str:
        return (
            data.chunks.get(h, "")
            if h in node.no_trans
            else data.trans.get(h, {}).get(lang) or data.chunks.get(h, "")
        )

    def find_add(block: str) -> tuple[str, list[str]] | None:
        """(id, blocks) of the addition containing ``block`` (exact block
        match — addition text is stable, user-written)."""
        if le:
            for add_id, md in le.adds.items():
                blocks = chunk_markdown(md)
                if block in blocks:
                    return add_id, blocks
        return None

    def do_delete(i: int) -> None:
        nonlocal changed
        h = anchor_at(i)
        if h is not None:
            if not verified(i):
                return
            ce = edits().chunks.setdefault(h, ChunkEdit())
            ce.drop = True
            ce.replace = ""
        elif found := find_add(a[i]):
            add_id, blocks = found
            blocks.remove(a[i])
            if blocks:
                edits().adds[add_id] = "\n\n".join(blocks)
            else:
                del edits().adds[add_id]
        else:
            return
        changed = True

    def do_insert(i1: int, new_blocks: list[str]) -> None:
        nonlocal changed
        left, right = i1 > 0, i1 < len(a)
        # Next to existing addition text: splice into that addition.
        if left and anchor_at(i1 - 1) is None and (found := find_add(a[i1 - 1])):
            add_id, blocks = found
            idx = blocks.index(a[i1 - 1]) + 1
            blocks[idx:idx] = new_blocks
            edits().adds[add_id] = "\n\n".join(blocks)
        elif right and anchor_at(i1) is None and (found := find_add(a[i1])):
            add_id, blocks = found
            idx = blocks.index(a[i1])
            blocks[idx:idx] = new_blocks
            edits().adds[add_id] = "\n\n".join(blocks)
        else:
            # An inter-chunk gap: anchor on the neighboring original
            # chunks (both, when both verify — the first live referrer
            # wins at apply time).
            after_h = anchor_at(i1) if right and verified(i1) else None
            before_h = anchor_at(i1 - 1) if left and verified(i1 - 1) else None
            if after_h is None and before_h is None:
                return  # no live anchor (drifted base): skip
            add_id = ""
            for h, field in ((after_h, "before"), (before_h, "after")):
                if h is not None and (ce := le.chunks.get(h) if le else None):
                    add_id = add_id or getattr(ce, field)
            if add_id and add_id in edits().adds:
                edits().adds[add_id] += "\n\n" + "\n\n".join(new_blocks)
            else:
                add_id = secrets.token_hex(6)
                edits().adds[add_id] = "\n\n".join(new_blocks)
            if after_h is not None:
                edits().chunks.setdefault(after_h, ChunkEdit()).before = add_id
            if before_h is not None:
                edits().chunks.setdefault(before_h, ChunkEdit()).after = add_id
        changed = True

    def do_replace(i: int, new_blocks: list[str]) -> None:
        nonlocal changed
        h = anchor_at(i)
        if h is None:
            if not (found := find_add(a[i])):
                return
            add_id, blocks = found
            blocks[blocks.index(a[i]) : blocks.index(a[i]) + 1] = new_blocks
            edits().adds[add_id] = "\n\n".join(blocks)
        else:
            if not verified(i):
                return
            ce = le.chunks.get(h) if le else None
            # The base shows the live patch when one exists, else the
            # served text: splice the edit into its blocks, so the patch
            # always covers the chunk's whole text (a patch may hold
            # several blocks — a paragraph split). a[i] not in the blocks
            # = the base doesn't reflect this chunk (drifted): skip.
            base_text = ce.replace if ce is not None and ce.replace else served(h)
            blocks = chunk_markdown(base_text)
            if a[i] not in blocks:
                return
            blocks[blocks.index(a[i]) : blocks.index(a[i]) + 1] = new_blocks
            if ce is None:
                ce = edits().chunks.setdefault(h, ChunkEdit())
            ce.replace = "\n\n".join(blocks)
            ce.drop = False
        changed = True

    def emit(tag: str, i1: int, i2: int, j1: int, j2: int) -> None:
        if tag == "delete":
            for i in range(i1, i2):
                do_delete(i)
        elif tag == "insert":
            do_insert(i1, list(b[j1:j2]))
        elif i2 - i1 == 1:  # replace of one block, possibly into several
            do_replace(i1, list(b[j1:j2]))
        else:  # a grown region: pair positionally, insert the surplus
            for k in range(i2 - i1):
                do_replace(i1 + k, [b[j1 + k]])
            do_insert(i2, list(b[j1 + i2 - i1 : j2]))

    for tag, i1, i2, j1, j2 in SequenceMatcher(
        None, a, b, autojunk=False
    ).get_opcodes():
        if tag == "equal":
            continue
        if tag == "replace" and i2 - i1 > j2 - j1:
            for sub in _refine_replace(a, i1, i2, b, j1, j2):
                emit(*sub)
        else:
            emit(tag, i1, i2, j1, j2)
    if not changed:
        return False
    node.langs[lang] = True
    return True


def set_title_translation(data: Data, node: Node, lang: str, title: str) -> bool:
    """Record (or drop) a per-language title override: a fragment in
    ``Data.trans`` keyed by the ORIGINAL title's chunk hash — the same
    storage machine title translations use, overriding them. Sending the
    original's text drops the override. Returns True when anything changed.
    Pure data ops — the caller wraps in a transaction and invalidates."""
    key = chunk_key(node.title)
    current = data.trans.get(key, {}).get(lang)
    if title == node.title:
        if current is None:
            return False
        del data.trans[key][lang]
        return True
    if current == title:
        return False
    data.trans.setdefault(key, {})[lang] = title
    node.langs[lang] = True
    return True


def clear_translations(data: Data) -> None:
    """Drop all machine translations (``Data.trans``) and rebuild the
    availability index (``node.langs``) from the surviving user overrides —
    overrides alone make a language exist on a page. Pure data ops — the
    caller wraps in a transaction and invalidates."""
    data.trans.clear()

    def walk(nodes: dict[str, Node], prefix: str) -> None:
        for slug, node in nodes.items():
            path = f"{prefix}/{slug}" if prefix else slug
            node.langs = {lang: True for lang in data.overrides.get(path, ())}
            walk(node.children, path)

    walk(data.menu, "")


def title_map(data: Data, lang: str) -> dict[str, str]:
    """path -> translated title for every node that has one.

    Titles are chunks too (docs/migrate.md): keyed by the hash of the
    title text, so editing a title invalidates its translations. Nodes
    without an entry fall back to their original title in views — as do
    nodes whose primary language IS ``lang`` (their original title already
    is in that language).
    """
    titles = {}

    def walk(nodes: dict[str, Node], prefix: str, inherited: str) -> None:
        for slug, node in nodes.items():
            path = f"{prefix}/{slug}" if prefix else slug
            node_lang = node.language or inherited
            if node.title and node_lang != lang:
                t = data.trans.get(chunk_key(node.title), {}).get(lang)
                if t:
                    titles[path] = t
            walk(node.children, path, node_lang)

    walk(data.menu, "", ORIGINAL_LANGUAGE)
    return titles


def subtree_languages(node: Node) -> set[str]:
    """Languages available anywhere in the node's subtree (the union of the
    ``langs`` indexes). Category placeholder pages select their language
    from this: they have no chunks of their own, but their title,
    navigation and card text localize wherever a translation exists."""
    langs = set(node.langs)
    for child in node.children.values():
        langs |= subtree_languages(child)
    return langs


def get_translation(data: Data, path: str, lang: str) -> Translation | None:
    """The translation of the page at ``path`` for ``lang``, or None.

    None when the page does not exist or is not available in ``lang``:
    ``node.langs`` is the availability index (a stale key is benign — the
    "translation" then just renders as the original).
    """
    chain = resolve(data.menu, path)
    node = chain[-1] if chain else None
    if node is None or node.chunks is None or lang not in node.langs:
        return None
    return Translation(
        markdown=hybrid_markdown(data, node, path, lang),
        titles=title_map(data, lang),
    )

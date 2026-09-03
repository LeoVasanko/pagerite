"""Localization: language selection, translation storage and assembly.

See docs/localization.md and docs/migrate.md. Each article's primary
language is ``Node.language``, inherited down the hierarchy (front page =
site default, ORIGINAL_LANGUAGE as the final fallback). The database holds
the original language as content-addressed chunks (``Data.chunks``); per
target language there are machine-translated fragments (``Data.trans``)
and user override patches (``Data.patches``), assembled into the served
Markdown at render time, with per-node fallback to the original titles.
"""

from collections.abc import Callable
from difflib import SequenceMatcher

import msgspec

from pagerite.chunks import chunk_key, chunk_markdown, join_chunks
from pagerite.data import Data, Node, Patch, resolve

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
    2. The original language anywhere in the header list wins — an AI
       translation is strictly worse than the original for anyone who has
       English configured at all.
    3. Otherwise the first header language with an available translation.
    4. Fall back to the original.
    """
    if query_lang:
        tag = base_tag(query_lang)
        if tag == original or (tag and is_available(tag)):
            return tag
    langs = parse_accept_language(accept_language or "")
    if original in langs:
        return original
    for lang in langs:
        if lang != original and is_available(lang):
            return lang
    return original


def apply_patch(hybrid: str, patch: Patch) -> str:
    """Apply one patch to the hybrid Markdown, best effort, each hunk
    independently: a hunk whose search text no longer exists is stale and
    silently skipped (docs/localization.md)."""
    for search, replace in patch.hunks:
        if search and search in hybrid:
            hybrid = hybrid.replace(search, replace, 1)
    return hybrid


def make_patch(base: str, edited: str) -> Patch:
    """The minimal diff of ``edited`` against the served ``base`` hybrid as
    (search, replace) hunks at block granularity (docs/localization.md).

    Blocks are the chunk_markdown split, so hunks align with translation
    units and code fences never straddle a hunk boundary. Pure inserts
    anchor on the preceding block (an empty search would never match);
    inserts at the very top anchor on the first block. autojunk is off:
    the diff must be deterministic, and pages are small.
    """
    a, b = chunk_markdown(base), chunk_markdown(edited)
    hunks: list[tuple[str, str]] = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        search = "\n\n".join(a[i1:i2])
        replace = "\n\n".join(b[j1:j2])
        if tag == "insert":
            if i1:
                search = a[i1 - 1]
                replace = f"{a[i1 - 1]}\n\n{replace}"
            elif a:
                search = a[0]
                replace = f"{replace}\n\n{a[0]}"
            # else: base is empty — the hunk is inert (empty search is
            # skipped by apply_patch); saving a translation of an empty
            # page records nothing applicable.
        hunks.append((search, replace))
    return Patch(hunks=hunks)


def hybrid_markdown(data: Data, node: Node, path: str, lang: str) -> str:
    """The served Markdown for ``lang``: per chunk the translation from
    ``Data.trans``, unless missing or marked no-translate (fallback to the
    original chunk), then the language's user patches applied in order.

    Not gated on ``node.langs`` (get_translation is the gated view): the
    editor save path diffs against this even for a language's first patch.
    """
    hybrid = join_chunks([
        data.chunks.get(h, "")
        if h in node.no_trans
        else data.trans.get(h, {}).get(lang) or data.chunks.get(h, "")
        for h in node.chunks or []
    ])
    for patch in data.patches.get(f"{path}:{lang}", []):
        hybrid = apply_patch(hybrid, patch)
    return hybrid


def add_patch(
    data: Data, node: Node, path: str, lang: str, edited: str, base: str | None = None
) -> bool:
    """Record a translated-view edit as a user Patch: the minimal diff of
    ``edited`` against ``base`` (default: the currently served hybrid),
    appended to the language's patch list. Patches alone make the
    translated version exist, so ``node.langs`` is set. Returns True when
    a patch was stored. Pure data ops — the caller wraps in a transaction
    and invalidates."""
    patch = make_patch(base if base is not None else hybrid_markdown(data, node, path, lang), edited)
    if not patch.hunks:
        return False
    data.patches.setdefault(f"{path}:{lang}", []).append(patch)
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
    availability index (``node.langs``) from the surviving user patches —
    patches alone make a language exist on a page. Pure data ops — the
    caller wraps in a transaction and invalidates."""
    data.trans.clear()
    patch_langs: dict[str, set[str]] = {}
    for key in data.patches:
        path, _, lang = key.rpartition(":")
        patch_langs.setdefault(path, set()).add(lang)

    def walk(nodes: dict[str, Node], prefix: str) -> None:
        for slug, node in nodes.items():
            path = f"{prefix}/{slug}" if prefix else slug
            node.langs = {lang: True for lang in patch_langs.get(path, ())}
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

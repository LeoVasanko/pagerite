"""Markdown rendering.

Raw HTML (including inline scripts) is passed through unfiltered: the
single author is trusted. Extensions: tables and strikethrough (from the
"default" preset), footnotes, definition lists, task lists,
brace-attributes (`{.class width=300}` on any element, images in
particular), admonitions (``!!! note Title`` with an indented body —
note/tip/warning/etc., the title optional) and GitHub-style alerts
(``> [!NOTE]`` / TIP / IMPORTANT / WARNING / CAUTION, rendered in the
same callout styling). ``::: name`` opens a generic container rendered
as ``<div class="name">`` and closed by a matching ``:::`` (nest by
giving the outer container more colons, e.g. `::::`); the name may be
followed by brace attributes (``::: aside {.right}``). ``::: aside``
floats as a muted side box, floating in the side zone at the article's
left on all but phone widths — the same margin float ``{.margin}`` (or
``::: margin``) gives any block — and ``::: nocols`` opts its section out
of the column layout. A brace-attribute
line as a block's last line (no blank line between) applies to the whole
block, e.g. a paragraph ending with ``{.wide}`` breaks out of the column
layout as a full-width element; written after a block (code fence,
heading, container, ...) it applies to that preceding block. Bare URLs autolink (GFM), with
the ``https://`` scheme hidden in the link text (``http://`` and other
schemes stay visible; manually labelled links are untouched), and
``H~2~O`` / ``x^2^`` give sub/superscripts.

render() also builds the layout structure: the top-level blocks are
segmented for the column layout — h1/h2 headings, ``.wide`` blocks and
margin-breakout blocks (``.margin``, ``::: aside``) stand on their own,
the runs between them are wrapped in ``<div class="colseg">`` (tagged
``.cols`` when the segment holds enough text, unless a ``::: nocols``
container opts it out). The result carries ``multicol`` when the whole
body justifies columns (views.py puts the class on the article); how
many columns (never more than two), whether the margin breakout applies
and every other viewport adaptation is then pagerite.css's call. The
thresholds measure visible text, code blocks excluded.

markdown-it's typographer is enabled, so body text gets SmartyPants-style
replacements: straight quotes become curly, ``--`` / ``---`` become en / em
dashes, ``...`` becomes an ellipsis, ``(c)`` becomes ©, and so on. Single
line breaks inside paragraphs become ``<br>`` (``breaks: True``). Code
spans/blocks and raw HTML are left untouched.

Images get special treatment: a relative `src` is resolved against the
page's own path (so `![alt](photo.avif)` in `/docs/design` is served from
`/docs/design/photo.avif`), and an image standing alone in its paragraph
becomes a block `<figure>` — with `<figcaption>` when it has a title.
Images inline with other content stay plain inline `<img>`, as does raw
`<img>` HTML written by the author. Positioning is done with attribute
classes, e.g. `![alt](photo.avif "Caption"){.right}`.
"""

import re
from datetime import datetime, timedelta
from typing import NamedTuple

from markdown_it import MarkdownIt
from markdown_it.common.utils import escapeHtml
from markdown_it.renderer import RendererHTML
from markdown_it.token import Token
from mdit_py_plugins.admon import admon_plugin
from mdit_py_plugins.attrs import attrs_plugin
from mdit_py_plugins.attrs.parse import ParseError, parse as parse_attrs
from mdit_py_plugins.container import container_plugin
from mdit_py_plugins.deflist import deflist_plugin
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.gfm_autolink import gfm_autolink_plugin
from mdit_py_plugins.subscript import sub_plugin
from mdit_py_plugins.superscript import superscript_plugin
from mdit_py_plugins.tasklists import tasklists_plugin
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound
from slugify import slugify

# Styles in /_assets/pygments-*.css match this formatter (regenerate:
# HtmlFormatter(style="github-dark").get_style_defs("pre code"))
_formatter = HtmlFormatter(style="github-dark", nowrap=True)


_TASK_MARKER_RE = re.compile(r"^(\s*(?:>\s*)*(?:[-*+]|\d+\.)\s+)\[( |x|X)\](\s+|$)")


def _highlight(text: str, lang: str, _attrs: str) -> str:
    """Syntax-highlight a fenced code block with Pygments.

    Returns bare spans (nowrap): markdown-it adds the <pre><code> wrapper,
    and the stylesheet is scoped to "pre code" to match.
    """
    try:
        lexer = get_lexer_by_name(lang)
    except ClassNotFound:
        return ""  # fall back to default <pre><code>
    return highlight(text, lexer, _formatter)


def _fence_rule(
    self: RendererHTML,
    tokens,
    idx: int,
    options,
    env: dict,
) -> str:
    """Render a fenced code block.

    Like the default fence rule, but block attributes (a trailing `{...}`
    line, applied to the fence token by _block_attrs) go on the <pre> — the
    block element — instead of the <code>, which keeps only the language
    class. This is what makes e.g. `{.wide}` or `{style="..."}` after a
    code fence style the block itself.
    """
    token = tokens[idx]
    info = token.info.strip() if token.info else ""
    lang = info.split(maxsplit=1)[0] if info else ""
    highlighted = _highlight(token.content, lang, "") or escapeHtml(token.content)
    code_class = f' class="{options.langPrefix}{lang}"' if lang else ""
    return (
        f"<pre{self.renderAttrs(token)}><code{code_class}>{highlighted}</code></pre>\n"
    )


def _image_rule(
    self: RendererHTML,
    tokens,
    idx: int,
    options,
    env: dict,
) -> str:
    """Render images, resolving relative srcs against the page path."""
    token = tokens[idx]
    src = token.attrs["src"]
    if not src.startswith(("/", "http://", "https://", "data:")):
        page = env.get("page_path", "")
        token.attrs["src"] = f"/{page}/{src}" if page else f"/{src}"
    token.attrs["alt"] = self.renderInlineAsText(token.children, options, env)
    img = self.renderToken(tokens, idx, options, env)
    if len(tokens) == 1:
        # The only inline content of its paragraph: render as a block
        # figure, captioned when titled. (The <p> wrapper is dropped by
        # _unwrap_lone_figures below.)
        title = token.attrs.get("title")
        caption = f"<figcaption>{escapeHtml(title)}</figcaption>" if title else ""
        return f"<figure>{img}{caption}</figure>"
    # Inline with other content: a plain inline image.
    return img


def _unwrap_lone_figures(state) -> None:
    """Drop the <p> wrapper around a lone image.

    markdown-it wraps inline content in a paragraph, but our image rule
    turns lone images into <figure> — a block element that is invalid
    inside <p>. Browsers hoist it out, leaving an empty paragraph whose
    margins disturb the layout.
    """
    tokens = state.tokens
    for i, token in enumerate(tokens):
        if token.type != "inline" or not token.children:
            continue
        [child] = token.children if len(token.children) == 1 else [None]
        if child and child.type == "image":
            if (
                tokens[i - 1].type == "paragraph_open"
                and tokens[i + 1].type == "paragraph_close"
            ):
                # A lone image becomes a <figure> (see _image_rule); block
                # attrs on the paragraph (e.g. a trailing {.wide} line) move
                # onto the image so they survive the unwrap.
                _apply_attrs(child, tokens[i - 1].attrs or {})
                tokens[i - 1].hidden = True
                tokens[i + 1].hidden = True


def _tag_task_checkboxes(state) -> None:
    """Tag rendered task-list checkboxes with a stable index.

    The public page and the editor preview use the index to identify which
    `[ ]`/`[x]` marker in the Markdown source to toggle when a visitor
    clicks the checkbox.
    """
    index = 0
    for token in state.tokens:
        if token.type != "inline" or not token.children:
            continue
        for child in token.children:
            if child.type == "html_inline" and 'type="checkbox"' in child.content:
                child.content = child.content.replace(
                    'type="checkbox"',
                    f'data-task-index="{index}" type="checkbox"',
                    1,
                )
                index += 1


def _shorten_autolinks(state) -> None:
    """Hide the https:// scheme in the text of bare autolinked URLs.

    GFM linkify sets the link text to the URL itself; only those links
    (markup "autolink") are shortened. http:// and other schemes stay
    visible, and manually labelled links keep whatever label was written.
    """
    for token in state.tokens:
        if token.type != "inline" or not token.children:
            continue
        for i, child in enumerate(token.children):
            if child.type == "link_open" and child.markup == "autolink":
                text = token.children[i + 1]
                if text.type == "text" and text.content.startswith("https://"):
                    text.content = text.content.removeprefix("https://")


_CONTAINER_NAME_RE = re.compile(r"[a-zA-Z][\w-]*")


def _apply_attrs(token, attrs: dict) -> None:
    """Join/set parsed brace attributes (`{.class key=value}`) on a token."""
    for key, value in attrs.items():
        if key == "class":
            token.attrJoin("class", value)
        else:
            token.attrSet(key, value)


def _container_validate(params: str, _markup: str) -> bool:
    """`::: name`, optionally followed by brace attrs (`::: aside {.right}`)."""
    name, _, rest = params.strip().partition(" ")
    if not _CONTAINER_NAME_RE.fullmatch(name):
        return False
    rest = rest.strip()
    if not rest:
        return True
    try:
        pos, _ = parse_attrs(rest)
    except ParseError:
        return False
    # parse() stops at (returns the index of) the closing brace.
    return pos == len(rest) - 1


def _container_attrs(state) -> None:
    """Apply `::: name {attrs}` classes to container tokens at parse time.

    The container plugin's default render is a plain renderToken, so the
    name and brace attributes must live on the token itself — and being a
    core rule (rather than a render rule) lets the segmentation in
    render() see the classes (::: aside's margin breakout, the ::: nocols
    opt-out, {.wide} containers).
    """
    for token in state.tokens:
        if token.type != "container_block_open":
            continue
        name, _, rest = token.info.strip().partition(" ")
        token.attrJoin("class", name)
        if rest.strip():
            _, attrs = parse_attrs(rest.strip())
            _apply_attrs(token, attrs)


def _block_attrs(state) -> None:
    """Apply `{.class key=value}` on a block's last line to the block.

    The inline attrs plugin only covers attributes right after an image,
    code span or link; this extends the same brace syntax to whole blocks,
    e.g. a paragraph ending with a `{.wide}` line (no blank line between)
    gets the `wide` class and thereby breaks out of the column layout.
    A lone `{...}` paragraph applies to the previous block instead (this
    is how headings take attributes, since a heading's next line always
    starts a new paragraph). Runs before the typographer so quotes inside
    attributes stay straight.
    """
    tokens = state.tokens
    for i, token in enumerate(tokens):
        if token.type != "inline" or not token.children:
            continue
        text = token.children[-1]
        if (
            text.type != "text"
            or not text.content.startswith("{")
            or not text.content.endswith("}")
        ):
            continue
        try:
            _, attrs = parse_attrs(text.content.strip())
        except ParseError:
            continue
        standalone = len(token.children) == 1
        if not standalone and token.children[-2].type != "softbreak":
            continue
        # The target: the enclosing block for a trailing attrs line, or the
        # previous same-level block for a standalone attrs paragraph —
        # including self-contained blocks like code fences and <hr>. Never
        # a hidden token (tight-list paragraphs render no tag to hold the
        # attributes) — in that case leave the text untouched instead of
        # silently swallowing it.
        own = i - 1  # standalone: the attrs paragraph's own opening token
        j = i - 1
        while j >= 0:
            target = tokens[j]
            if target.hidden:
                pass
            elif standalone:
                if (
                    j != own
                    and target.level == tokens[own].level
                    and (
                        target.nesting == 1
                        or target.type in ("fence", "code_block", "hr")
                    )
                ):
                    break
            elif target.nesting == 1:
                break
            j -= 1
        if j < 0:
            continue
        _apply_attrs(tokens[j], attrs)
        if standalone:
            tokens[own].hidden = True
            token.children = []
            tokens[i + 1].hidden = True
        else:
            del token.children[-2:]


#: Minimum number of in-body h1/h2 headings for section anchors to be
#: useful — shorter articles get no ids/self-links at all.
ANCHOR_MIN_HEADINGS = 3


def _heading_ids(state) -> None:
    """Anchor the in-body h1/h2 headings of long-enough articles.

    The markdown body's own h1 and h2 headings get a slug id and their
    text is wrapped in a self-link (``<a class="anchor" href="#id">``) so
    section links are copyable by click or right-click — but only when the
    body has at least ANCHOR_MIN_HEADINGS of them; shorter articles stay
    anchor-free. The FIRST h1 is the article title: like the implicit
    page-title h1 it gets no id, does not count toward the threshold, and
    its self-link is ``href=""`` (back to the top of the page). An
    author-set `{#id}` always wins; auto ids slugify the heading text
    (python-slugify, mirroring the editor's slugify.js) and dedupe with
    -2/-3 suffixes per render. Headings that already contain a link are
    ``data-line`` records the heading's markdown source line (0-based, after
    undoing the render(title=...) injection offset via ``env``) — the page
    editor uses it for section pens and piecewise-linear scroll sync.
    """
    tokens = state.tokens
    line_offset = state.env.get("line_offset", 0)

    def wrap(i: int, token, href: str) -> None:
        inline = tokens[i + 1]
        if not inline.children or any(c.type == "link_open" for c in inline.children):
            return
        anchor = Token("link_open", "a", 1)
        anchor.attrs = {"href": href, "class": "anchor"}
        inline.children = [anchor, *inline.children, Token("link_close", "a", -1)]

    # The first in-body h1 is the title: href="" self-link, never an id.
    # Only TOP-LEVEL headings participate — h1/h2 nested in ::: containers
    # or asides (level > 0) get no anchors, data-lines or pens.
    first_h1 = next(
        (
            i
            for i, t in enumerate(tokens)
            if t.type == "heading_open" and t.tag == "h1" and t.level == 0
        ),
        None,
    )
    if first_h1 is not None:
        wrap(first_h1, tokens[first_h1], "")

    heads = [
        (i, token)
        for i, token in enumerate(tokens)
        if token.type == "heading_open" and token.tag in ("h1", "h2") and token.level == 0 and i != first_h1
    ]
    if len(heads) < ANCHOR_MIN_HEADINGS:
        return
    seen: set[str] = set()
    for i, token in heads:
        inline = tokens[i + 1]
        hid = token.attrGet("id")
        if not isinstance(hid, str) or not hid:
            # Slug the visible text, not the raw markdown (`## [a](url)`).
            text = "".join(
                c.content for c in inline.children if c.type in ("text", "code_inline")
            )
            base = slugify(text) or "section"
            hid, n = base, 2
            while hid in seen:
                hid = f"{base}-{n}"
                n += 1
            token.attrSet("id", hid)
        seen.add(hid)
        if token.map:
            token.attrSet("data-line", str(max(0, token.map[0] - line_offset)))
        wrap(i, token, f"#{hid}")


md = (
    MarkdownIt(
        "default",
        {
            "html": True,
            "highlight": _highlight,
            "typographer": True,
            "breaks": True,
        },
    )
    .use(attrs_plugin)
    .use(admon_plugin)
    .use(container_plugin, "block", validate=_container_validate)
    .use(footnote_plugin)
    .use(deflist_plugin)
    # label_after: the item text is wrapped in <label for> after the
    # checkbox, so clicking the text toggles it.
    .use(tasklists_plugin, enabled=True, label=True, label_after=True)
    .use(gfm_autolink_plugin)
    .use(sub_plugin)
    .use(superscript_plugin)
)
md.add_render_rule("image", _image_rule)
md.add_render_rule("fence", _fence_rule)
# GFM alerts (`> [!NOTE]` etc.), built into markdown-it-py's blockquote rule.
md.options["alerts"] = True
# Block attrs must be stripped before the typographer curlifies their quotes.
md.core.ruler.before("replacements", "block_attrs", _block_attrs)
md.core.ruler.push("container_attrs", _container_attrs)
md.core.ruler.push("unwrap_lone_figures", _unwrap_lone_figures)
md.core.ruler.push("tag_task_checkboxes", _tag_task_checkboxes)
md.core.ruler.push("shorten_autolinks", _shorten_autolinks)
md.core.ruler.push("heading_ids", _heading_ids)


# Text-length thresholds (visible characters, code blocks excluded) for the
# column layout: the article goes .multicol past MULTICOL_TEXT, and a column
# segment gets .cols past COLS_TEXT.
MULTICOL_TEXT = 1800
COLS_TEXT = 600

_PRE_BLOCK_RE = re.compile(r"<pre\b.*?</pre>", re.S)
_TAG_RE = re.compile(r"<[^>]+>")

# Classes that take their block out of the column flow: .wide is a
# full-width separator, .margin/.aside float in the side zone at the
# article's left (they must be direct article children for that — the zone
# rules key off it — never inside a column).
_WIDE = "wide"
_BREAKOUT = ("margin", "aside")


class Rendered(NamedTuple):
    """render() result: the segmented body HTML, and whether the article
    should carry .multicol (enough visible text to justify columns)."""

    html: str
    multicol: bool


def _classes(token) -> set[str]:
    return set((token.attrGet("class") or "").split())


def _text_len(html: str) -> int:
    """Visible-text length of rendered HTML, code blocks excluded."""
    return len(_TAG_RE.sub("", _PRE_BLOCK_RE.sub("", html)).strip())


def _top_level_blocks(tokens: list) -> list[list]:
    """Split the token stream into its top-level blocks.

    A new block starts at each level-0 opening/self-contained token;
    closing and nested tokens (inline children, sub-containers) belong to
    the current block, so every slice is balanced and renders on its own.
    """
    blocks = []
    for token in tokens:
        if token.level == 0 and token.nesting >= 0:
            blocks.append([token])
        elif blocks:
            blocks[-1].append(token)
    return blocks


def _is_boundary(block: list) -> bool:
    """True for blocks that never go inside a column segment (see the
    _WIDE/_BREAKOUT comment above): h1/h2 headings, anything carrying
    .wide, and blocks whose own element carries .margin/.aside — for a
    lone-image paragraph (which renders as a <figure>) the image's classes
    count as the block's own."""
    first = block[0]
    if first.type == "heading_open" and first.tag in ("h1", "h2"):
        return True
    own = _classes(first)
    for token in block:
        if _WIDE in _classes(token):
            return True
        if token.type == "inline":
            children = token.children or []
            if any(_WIDE in _classes(c) for c in children):
                return True
            if len(children) == 1 and children[0].type == "image":
                own |= _classes(children[0])
    return bool(own & set(_BREAKOUT))


def render(
    text: str,
    page_path: str = "",
    created: datetime | None = None,
    modified: datetime | None = None,
    title: str | None = None,
) -> Rendered:
    """Render Markdown text to the article body's HTML and layout flags.

    ``title`` injects a ``# {title}`` line at the top when the markdown has
    no h1 of its own, so the implicit page title goes through the exact
    same pipeline as an explicit one (first-h1 anchor treatment included).

    The top-level blocks are grouped into column segments: boundary blocks
    (h1/h2 headings, .wide, margin-breakout blocks — see _is_boundary) are
    rendered bare, the runs between them wrapped in <div class="colseg">.
    A segment is tagged .cols when it holds enough text (COLS_TEXT) and no
    ::: nocols container; the article is .multicol when the whole body
    exceeds MULTICOL_TEXT. pagerite.css keys all column and margin-breakout
    layout off these classes.

    A ``{dates}`` line expands to the article's published/updated dateline
    (needs ``created``/``modified``; left as-is in contexts without them,
    e.g. the editor preview). Position is the author's choice — typically
    right after the article's h1.
    """
    env = {"page_path": page_path, "line_offset": 0}
    if title and not has_h1(text):
        text = f"# {title}\n\n{text}"
        # The injected title shifts source lines by two; _heading_ids
        # subtracts this from its data-line attributes.
        env["line_offset"] = 2
    blocks = _top_level_blocks(md.parse(text, env))
    # Group consecutive non-boundary blocks into segments (is_segment,
    # flat tokens); boundary blocks stand on their own between them.
    groups: list[tuple[bool, list]] = []
    for block in blocks:
        if _is_boundary(block):
            groups.append((False, block))
        elif groups and groups[-1][0]:
            groups[-1][1].extend(block)
        else:
            groups.append((True, list(block)))

    parts = []
    total = 0
    for is_segment, group in groups:
        html = md.renderer.render(group, md.options, env)
        if not html.strip():
            continue  # e.g. a consumed standalone-attrs paragraph
        text_len = _text_len(html)
        total += text_len
        if not is_segment:
            parts.append(html)
            continue
        nocols = any(
            "nocols" in _classes(t) for t in group if t.type == "container_block_open"
        )
        cols = " cols" if text_len > COLS_TEXT and not nocols else ""
        parts.append(f'<div class="colseg{cols}">{html}</div>')
    html = "".join(parts)
    if created is not None and "<p>{dates}</p>" in html:
        html = html.replace("<p>{dates}</p>", _dateline(created, modified))
    return Rendered(html, total > MULTICOL_TEXT)


def _dateline(created: datetime, modified: datetime | None) -> str:
    """Dateline for the ``{dates}`` tag: "1 Jan 2026", plus
    " – edited 3 Jan 2026" when the last edit came >= 48h after
    publishing (quick fixes right after posting stay unmentioned)."""
    out = f'<time datetime="{created.isoformat()}">{created.day} {created:%b %Y}</time>'
    if modified is not None and modified - created >= timedelta(hours=48):
        out += f' – edited <time datetime="{modified.isoformat()}">{modified.day} {modified:%b %Y}</time>'
    return f'<p class="dateline">{out}</p>'


def has_h1(text: str) -> bool:
    """True if the Markdown source itself contains an h1 heading.

    When it does, the article owns its heading and render(title=...) does
    not inject the page title as an h1 (the title is still used for the
    document <title> and navigation labels).
    """
    return any(t.type == "heading_open" and t.tag == "h1" for t in md.parse(text))


def toggle_task(text: str, index: int) -> str | None:
    """Toggle the Nth task-list checkbox marker in ``text``.

    Returns the modified Markdown source, or ``None`` if the index is out
    of range or the marker could not be found.
    """
    tokens = md.parse(text, {"page_path": ""})
    checkbox_lines: list[int | None] = []
    for token in tokens:
        if token.type == "inline" and token.children:
            for child in token.children:
                if child.type == "html_inline" and 'type="checkbox"' in child.content:
                    checkbox_lines.append(token.map[0] if token.map else None)
                    break

    if not (0 <= index < len(checkbox_lines)):
        return None
    line_idx = checkbox_lines[index]
    if line_idx is None or line_idx < 0:
        return None

    lines = text.splitlines(keepends=True)
    if line_idx >= len(lines):
        return None
    line = lines[line_idx]

    def repl(m: re.Match[str]) -> str:
        prefix = m.group(1)
        marker = m.group(2)
        new_marker = "x" if marker.strip() == "" else " "
        return f"{prefix}[{new_marker}]{m.group(3)}"

    new_line = _TASK_MARKER_RE.sub(repl, line, count=1)
    if new_line == line:
        return None

    lines[line_idx] = new_line
    return "".join(lines)

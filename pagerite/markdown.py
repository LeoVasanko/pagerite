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
giving the outer container more colons, e.g. `::::`); ``::: aside``
floats as a side box beside the text and ``::: nocols`` opts its
section out of the column layout. A brace-attribute
line as a block's last line (no blank line between) applies to the whole
block, e.g. a paragraph ending with ``{.wide}`` breaks out of the column
layout as a full-width element. Bare URLs autolink (GFM), with
the ``https://`` scheme hidden in the link text (``http://`` and other
schemes stay visible; manually labelled links are untouched), and
``H~2~O`` / ``x^2^`` give sub/superscripts.

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

from markdown_it import MarkdownIt
from markdown_it.common.utils import escapeHtml
from markdown_it.renderer import RendererHTML
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
            if (tokens[i - 1].type == "paragraph_open"
                    and tokens[i + 1].type == "paragraph_close"):
                # A lone image becomes a <figure> (see _image_rule); block
                # attrs on the paragraph (e.g. a trailing {.wide} line) move
                # onto the image so they survive the unwrap.
                for key, value in (tokens[i - 1].attrs or {}).items():
                    if key == "class":
                        child.attrJoin("class", value)
                    else:
                        child.attrSet(key, value)
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


_CONTAINER_NAME_RE = re.compile(r"\s*[a-zA-Z][\w-]*\s*$")


def _container_render(self, tokens, idx, options, env):
    """Render `::: name` containers as `<div class="name">`."""
    token = tokens[idx]
    if token.nesting == 1:
        token.attrJoin("class", token.info.strip())
    return self.renderToken(tokens, idx, options, env)


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
        if (text.type != "text" or not text.content.startswith("{")
                or not text.content.endswith("}")):
            continue
        try:
            _, attrs = parse_attrs(text.content.strip())
        except ParseError:
            continue
        standalone = len(token.children) == 1
        if not standalone and token.children[-2].type != "softbreak":
            continue
        # The target: the enclosing block for a trailing attrs line, or the
        # previous same-level block for a standalone attrs paragraph. Never
        # a hidden token (tight-list paragraphs render no tag to hold the
        # attributes) — in that case leave the text untouched instead of
        # silently swallowing it.
        own = i - 1  # standalone: the attrs paragraph's own opening token
        j = i - 1
        while j >= 0:
            if (tokens[j].nesting == 1 and not tokens[j].hidden
                    and (not standalone or (j != own
                                            and tokens[j].level == tokens[own].level))):
                break
            j -= 1
        if j < 0:
            continue
        for key, value in attrs.items():
            if key == "class":
                tokens[j].attrJoin("class", value)
            else:
                tokens[j].attrSet(key, value)
        if standalone:
            tokens[own].hidden = True
            token.children = []
            tokens[i + 1].hidden = True
        else:
            del token.children[-2:]


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
    .use(container_plugin, "block", validate=lambda params, _markup:
         bool(_CONTAINER_NAME_RE.fullmatch(params)), render=_container_render)
    .use(footnote_plugin)
    .use(deflist_plugin)
    .use(tasklists_plugin, enabled=True)
    .use(gfm_autolink_plugin)
    .use(sub_plugin)
    .use(superscript_plugin)
)
md.add_render_rule("image", _image_rule)
# GFM alerts (`> [!NOTE]` etc.), built into markdown-it-py's blockquote rule.
md.options["alerts"] = True
# Block attrs must be stripped before the typographer curlifies their quotes.
md.core.ruler.before("replacements", "block_attrs", _block_attrs)
md.core.ruler.push("unwrap_lone_figures", _unwrap_lone_figures)
md.core.ruler.push("tag_task_checkboxes", _tag_task_checkboxes)
md.core.ruler.push("shorten_autolinks", _shorten_autolinks)


def render(
    text: str,
    page_path: str = "",
    created: datetime | None = None,
    modified: datetime | None = None,
) -> str:
    """Render Markdown text to an HTML string.

    A ``{dates}`` line expands to the article's published/updated dateline
    (needs ``created``/``modified``; left as-is in contexts without them,
    e.g. the editor preview). Position is the author's choice — typically
    right after the article's h1.
    """
    html = md.render(text, {"page_path": page_path})
    if created is not None and "<p>{dates}</p>" in html:
        html = html.replace("<p>{dates}</p>", _dateline(created, modified))
    return html


def _dateline(created: datetime, modified: datetime | None) -> str:
    """Dateline for the ``{dates}`` tag: "1 Jan 2026", plus
    " – edited 3 Jan 2026" when the last edit came >= 24h after
    publishing (quick fixes right after posting stay unmentioned)."""
    out = f'<time datetime="{created.isoformat()}">{created.day} {created:%b %Y}</time>'
    if modified is not None and modified - created >= timedelta(hours=24):
        out += f' – edited <time datetime="{modified.isoformat()}">{modified.day} {modified:%b %Y}</time>'
    return f'<p class="dateline">{out}</p>'


def has_h1(text: str) -> bool:
    """True if the Markdown source itself contains an h1 heading.

    When it does, the article owns its heading and the page title is not
    rendered as an additional h1 (the title is still used for the document
    <title> and navigation labels).
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

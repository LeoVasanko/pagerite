"""Markdown rendering.

Raw HTML (including inline scripts) is passed through unfiltered: the
single author is trusted. Extensions: tables and strikethrough (from the
"default" preset), footnotes, definition lists, task lists and
brace-attributes (`{.class width=300}` on any element, images in
particular).

Images get special treatment: a relative `src` is resolved against the
page's own path (so `![alt](photo.avif)` in `/docs/design` is served from
`/docs/design/photo.avif`), and an image with a title becomes a
`<figure>` with `<figcaption>`. Positioning is done with attribute
classes, e.g. `![alt](photo.avif "Caption"){.right}`.
"""

from markdown_it import MarkdownIt
from markdown_it.common.utils import escapeHtml
from markdown_it.renderer import RendererHTML
from mdit_py_plugins.attrs import attrs_plugin
from mdit_py_plugins.deflist import deflist_plugin
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.tasklists import tasklists_plugin
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

# Styles in /static/pygments.css match this formatter (regenerate:
# HtmlFormatter(style="github-dark").get_style_defs("pre code"))
_formatter = HtmlFormatter(style="github-dark", nowrap=True)


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
    if title := token.attrs.get("title"):
        return f"<figure>{img}<figcaption>{escapeHtml(title)}</figcaption></figure>"
    return img


def _unwrap_lone_figures(state) -> None:
    """Drop the <p> wrapper around a lone titled image.

    markdown-it wraps inline content in a paragraph, but our image rule
    turns titled images into <figure> — a block element that is invalid
    inside <p>. Browsers hoist it out, leaving an empty paragraph whose
    margins disturb the layout.
    """
    tokens = state.tokens
    for i, token in enumerate(tokens):
        if token.type != "inline" or not token.children:
            continue
        [child] = token.children if len(token.children) == 1 else [None]
        if child and child.type == "image" and child.attrs.get("title"):
            if (tokens[i - 1].type == "paragraph_open"
                    and tokens[i + 1].type == "paragraph_close"):
                tokens[i - 1].hidden = True
                tokens[i + 1].hidden = True


md = (
    MarkdownIt("default", {"html": True, "highlight": _highlight})
    .use(attrs_plugin)
    .use(footnote_plugin)
    .use(deflist_plugin)
    .use(tasklists_plugin)
)
def _checkbox_emojis(state) -> None:
    """Render task-list checkboxes as emoji instead of disabled inputs.

    A disabled <input> renders grey and washed out; a colored emoji
    shows the state without any styling.
    """
    for token in state.tokens:
        if token.type != "inline" or not token.children:
            continue
        for child in token.children:
            if child.type == "html_inline" and 'type="checkbox"' in child.content:
                child.type = "text"
                child.content = "✅" if "checked" in child.content else "⬜"


md.add_render_rule("image", _image_rule)
md.core.ruler.push("unwrap_lone_figures", _unwrap_lone_figures)
md.core.ruler.push("checkbox_emojis", _checkbox_emojis)


def render(text: str, page_path: str = "") -> str:
    """Render Markdown text to an HTML string."""
    return md.render(text, {"page_path": page_path})


def has_h1(text: str) -> bool:
    """True if the Markdown source itself contains an h1 heading.

    When it does, the article owns its heading and the page title is not
    rendered as an additional h1 (the title is still used for the document
    <title> and navigation labels).
    """
    return any(t.type == "heading_open" and t.tag == "h1" for t in md.parse(text))

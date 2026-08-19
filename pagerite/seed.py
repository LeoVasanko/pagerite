"""Seed content written to the database on first creation only
(``@kanta.bootstrap`` in ``app.py``).

A "welcome to your new site" starter: a structured docs section (three
menu levels deep) covering editing and the full Markdown feature set —
each feature shown as its Markdown source in a code block followed by
the rendered result — and a showcase section with image positioning,
long-form layout and a simple custom banner.
"""

WELCOME = """\
Welcome to your new **Pagerite** site. Everything you see is a page written in Markdown, served from a pretty URL, and editable right here in the browser.

Where to go next:

- The [docs](/docs/editing) section explains how to edit this site and shows every supported Markdown feature, source and result side by side.
- The [showcase](/showcase/gallery) section shows what finished pages can look like: image positioning, banners, a long read.
- Click the 🖊️ pen on any page to open the editor, and the ⚙️ pen for site settings and the structure tree.

![Abstract waves](waves.svg "Generated SVG artwork, attached to this page")

*Delete or rewrite any of these pages — they are only here to get you started.*
"""

EDITING = """\
Everything on the site is editable in place. Log in, and pens appear: 🖊️ on the page and banner, ⚙️ in the banner corner for site settings.

## The editor

The 🖊️ pens open a tabbed editor over the page you are viewing:

- **Article** — the page's title and Markdown, with a live preview. The format bar inserts the harder-to-remember syntax (links, tables, images); Ctrl/Cmd-B, I and S do what you expect. Saving is explicit: 💾 or Ctrl+S.
- **Banner** — per-page banner HTML and a banner design picker. Banners are raw HTML (an image, a styled div, a canvas with a script) and subpages inherit the nearest banner up their path.
- **Site** — brand, theme, fonts, favicon and custom CSS, all applied immediately.
- **Structure** — the page tree. Drag rows to reorder or nest, rename titles and slugs inline, ➕ adds a page, ✕ deletes one.

## URLs and structure

The URL is the structure: a page at `docs/markdown/basics` lives under `docs` and `markdown`, and the menus are derived from that. Slugs are lowercase ASCII (`a-z 0-9 - _`). A node without content is a category label — it renders a placeholder and its menu link points at its first child page.

Images and files uploaded anywhere land in a content-addressed store served from `/_f/{hash}.ext`, so links survive page moves. The article editor's format bar and copy-paste both upload images for you.

{dates}
"""

MD_BASICS = """\
Every feature below is shown twice: first the Markdown source, then how it renders.

## Headings and text

```markdown
## A section heading
### A subsection

*Emphasis*, **strong**, ~~strikethrough~~, `inline code`, and a
[link to the front page](/). Plain URLs become links automatically:
https://example.com — and a hard line break
is just a newline.
```

## A section heading
### A subsection

*Emphasis*, **strong**, ~~strikethrough~~, `inline code`, and a [link to the front page](/). Plain URLs become links automatically: https://example.com — and a hard line break
is just a newline.

## Lists and quotes

```markdown
- One
- Two
  - Nested

1. First
2. Second

> A blockquote. The URL space is the author's:
> pretty slugs at the root, nesting only where
> the content is genuinely structured.
```

- One
- Two
  - Nested

1. First
2. Second

> A blockquote. The URL space is the author's:
> pretty slugs at the root, nesting only where
> the content is genuinely structured.

## Code

Fenced blocks get server-side syntax highlighting:

````markdown
```python
def greet(name: str) -> str:
    return f"Hello, {name}!"
```
````

```python
def greet(name: str) -> str:
    return f"Hello, {name}!"
```

## Tables

```markdown
| Feature | Status |
|---------|--------|
| Pages   | done   |
| Images  | done   |
```

| Feature | Status |
|---------|--------|
| Pages   | done   |
| Images  | done   |
"""

MD_EXTENSIONS = """\
Markdown extensions enabled on this site, source first, then rendered.

## Footnotes

```markdown
Footnotes work inline.[^1]

[^1]: Rendered at the bottom of the page, with a back-reference.
```

Footnotes work inline.[^1]

[^1]: Rendered at the bottom of the page, with a back-reference.

## Definition lists

```markdown
Term
: A definition list entry.

Another term
: With its definition.
```

Term
: A definition list entry.

Another term
: With its definition.

## Task lists

```markdown
- [x] Write content in Markdown
- [x] Attach images to pages
- [x] Make tasks clickable on the rendered page
```

- [x] Write content in Markdown
- [x] Attach images to pages
- [x] Make tasks clickable on the rendered page

## Admonitions

```markdown
!!! note
    An admonition block for notes, warnings, tips...
```

!!! note
    An admonition block for notes, warnings, tips...

## Sub- and superscript

```markdown
H~2~O and x^2^ + y^2^ = z^2^.
```

H~2~O and x^2^ + y^2^ = z^2^.

## Raw HTML

HTML passes through untouched — useful for `<kbd>` keys, `<details>` sections, embedded media:

```html
<details><summary>Click to expand</summary>Hidden content.</details>
```

<details><summary>Click to expand</summary>Hidden content.</details>

## Smart typography

The typographer is on, so straight quotes become curly, `--` becomes -- and `...` becomes ...
"""

MD_LAYOUT = """\
## Images and figures

An image standing alone in its paragraph becomes a `<figure>`; its title becomes the caption. Inline images within text stay plain.

```markdown
![Abstract shapes](shapes.svg "A captioned figure")
```

![Abstract shapes](shapes.svg "A captioned figure")

## Positioning with attributes

Brace attributes (the attrs plugin) control placement: `{.right}` and `{.left}` float, `{.wide}` breaks out of the text column, and plain attributes like `width=280` pass through.

```markdown
![Abstract shapes](shapes.svg "Floated right"){.right width=280}
```

![Abstract shapes](shapes.svg "Floated right — text wraps around it"){.right width=280}

Floated images let the text wrap around them, like this paragraph does. Relative image paths resolve against the page's own path, so attached files travel with the page. Uploaded files get content-addressed `/_f/` URLs that never break, no matter where the page moves.

{.wide} artwork spans the full content width:

```markdown
![Dunes](dunes.svg "Full-width artwork"){.wide}
```

![Dunes](dunes.svg "Full-width artwork between sections"){.wide}

## Datelines

A `{dates}` line on its own expands to the article's published/updated dateline:

```markdown
{dates}
```

{dates}

## The page title

If your Markdown contains its own `# heading`, the page title is not repeated as a second h1 — it still supplies the `<title>` and the menu labels.
"""

GALLERY = """\
Pages can attach images and position them freely. All artwork here is generated SVG, stored content-addressed and served from `/_f/`.

![Shapes](shapes.svg "Floated left with {.left}"){.left width=240}

This text wraps around a left-floated figure. The caption comes from the image title, the float from `{.left width=240}` — brace attributes on the image itself.

![Waves](waves.svg "Floated right with {.right}"){.right width=240}

Mixing floats in one article is fine. Both images were uploaded to this page and referenced by relative path, so the whole page (images included) can be moved in the structure tree without breaking anything.

![Dunes](dunes.svg "Full-bleed with {.wide}"){.wide}

A wide image escapes the text column for emphasis between sections. No HTML needed — just Markdown and an attribute.
"""

NIGHT_SKY = """\
This page's banner is not an image — it's a few lines of HTML stored with the page:

```html
<div style="background: linear-gradient(100deg, #14243d, #3d2b6b 45%,
     #7c5cff 75%, #ff5c8a); height: 100%"></div>
```

Banners are arbitrary trusted markup: a styled div, an `<img>`, even a canvas with a script. Subpages inherit the nearest banner up their path, so a whole section can share one look — this one is set on a leaf page, so nothing else inherits it.

For animated banners, pick a **banner design** in the banner editor instead of writing code: this site ships `stars` (a drifting starfield) and `eyes` (a critter in the grass), and themes can bring their own. Designs are folders in `pagerite/themes/{name}/` — a `banner.css` plus a `banner.html` or `banner.svg` — and are selectable on any page via the UI.
"""

SMALL_RELEASES = """\
Software wants to be shipped. The longer a change sits unmerged, the more it rots: context fades, conflicts accumulate, and the diff grows teeth.

1. Cut the scope until it fits in a day.
2. Ship it behind whatever door you like.
3. Let real use argue with your assumptions.

A release is a conversation with reality. Small releases keep the conversation lively.
"""

ABOUT = """\
This site runs on **Pagerite**: FastAPI + html5tagger + kanta, with content written in Markdown and rendered on the fly.

- [How to edit this site](/docs/editing)
- [Markdown features](/docs/markdown/basics)
- [The showcase](/showcase/gallery)

*Replace this page with whatever your site is about.*
"""

BANNER_DIV = (
    '<div style="background: linear-gradient(100deg, #14243d, #3d2b6b 45%,'
    ' #7c5cff 75%, #ff5c8a); height: 100%"></div>'
)

WAVES_SVG = """\
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 400">
  <defs>
    <linearGradient id="g1" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#7c5cff"/>
      <stop offset="1" stop-color="#00d4c8"/>
    </linearGradient>
    <linearGradient id="g2" x1="0" y1="1" x2="1" y2="0">
      <stop offset="0" stop-color="#ff5c8a"/>
      <stop offset="1" stop-color="#7c5cff"/>
    </linearGradient>
  </defs>
  <rect width="800" height="400" fill="#12101c"/>
  <path d="M0 260 Q 200 180 400 260 T 800 260 V400 H0 Z" fill="url(#g1)" opacity="0.85"/>
  <path d="M0 310 Q 200 240 400 310 T 800 310 V400 H0 Z" fill="url(#g2)" opacity="0.75"/>
  <circle cx="600" cy="110" r="70" fill="#ffd75c" opacity="0.9"/>
</svg>
"""

SHAPES_SVG = """\
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 400">
  <rect width="400" height="400" fill="#0f1a1c"/>
  <circle cx="140" cy="150" r="90" fill="#00d4c8" opacity="0.85"/>
  <rect x="180" y="180" width="150" height="150" rx="24" fill="#ffb35c" opacity="0.9"/>
  <path d="M140 60 L 220 200 L 60 200 Z" fill="#ff5c8a" opacity="0.8"/>
</svg>
"""

DUNES_SVG = """\
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 300">
  <defs>
    <linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#2b1b4d"/>
      <stop offset="1" stop-color="#ff8a5c"/>
    </linearGradient>
  </defs>
  <rect width="1200" height="300" fill="url(#sky)"/>
  <path d="M0 210 Q 300 150 600 210 T 1200 210 V300 H0 Z" fill="#3d2b6b"/>
  <path d="M0 250 Q 300 200 600 250 T 1200 250 V300 H0 Z" fill="#241842"/>
</svg>
"""

#: path -> (title, markdown, {filename: bytes}, banner HTML, menu order,
#: banner design). Banners are deliberately set only on one leaf page
#: (showcase/night-sky), so nothing else inherits a custom banner.
#: Note there are deliberately no "docs" or "showcase" landing pages:
#: those labels are created without content, so they render a placeholder
#: page and their nav links point at the first child (see
#: views.first_leaf).
PAGES: dict[str, tuple[str, str, dict[str, bytes], str, float, str | None]] = {
    "": ("Welcome", WELCOME, {"waves.svg": WAVES_SVG.encode()}, "", 1, None),
    "about": ("About", ABOUT, {}, "", 3, None),
    "docs/editing": ("Editing This Site", EDITING, {}, "", 1, None),
    "docs/markdown/basics": (
        "Markdown Basics",
        MD_BASICS,
        {},
        "",
        1,
        None,
    ),
    "docs/markdown/extensions": ("Markdown Extensions", MD_EXTENSIONS, {}, "", 2, None),
    "docs/markdown/images-and-layout": (
        "Images and Layout",
        MD_LAYOUT,
        {"shapes.svg": SHAPES_SVG.encode(), "dunes.svg": DUNES_SVG.encode()},
        "",
        3,
        None,
    ),
    "showcase/gallery": (
        "Gallery",
        GALLERY,
        {
            "shapes.svg": SHAPES_SVG.encode(),
            "waves.svg": WAVES_SVG.encode(),
            "dunes.svg": DUNES_SVG.encode(),
        },
        "",
        1,
        None,
    ),
    "showcase/night-sky": ("Night Sky", NIGHT_SKY, {}, BANNER_DIV, 2, None),
    "showcase/small-releases": ("Small Releases", SMALL_RELEASES, {}, "", 3, None),
}

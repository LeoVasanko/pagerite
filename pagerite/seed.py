"""Seed content written to the database on first creation only
(``@kanta.bootstrap`` in ``app.py``).

A "welcome to your new site" starter: a structured docs section (three
menu levels deep) covering editing and one long article that walks the
full Markdown feature set — each feature shown as its Markdown source in
a code block followed by the rendered result — and a showcase section
with image positioning, long-form layout and banner designs.

Binary seed images live in ``seed-assets/`` (public domain, from
Wikimedia Commons: the two whale engravings are Augustus Burnham
Shute's illustrations for an 1892 edition of Moby-Dick; the wave is
Hokusai's "The Great Wave off Kanagawa").
"""

from pathlib import Path

ASSETS = Path(__file__).with_name("seed-assets")


def _asset(name: str) -> bytes:
    return (ASSETS / name).read_bytes()

WELCOME = """\
Welcome to your new **Pagerite** site. Everything you see is a page written in Markdown, served from a pretty URL, and editable right here in the browser.

Where to go next:

- The [docs](/docs/editing) section explains how to edit this site and walks through every supported Markdown feature, source and result side by side.
- The [showcase](/showcase/gallery) section shows what finished pages can look like: image positioning, banners, a long read.
- Click the 🖊️ pen on any page to open the editor, and the ⚙️ pen for site settings and the structure tree.

![Abstract waves](waves.svg "Generated SVG artwork, attached to this page"){width=420}

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

The URL is the structure: a page at `docs/markdown` lives under `docs`, and the menus are derived from that. Slugs are lowercase ASCII (`a-z 0-9 - _`). A node without content is a category label — it renders a placeholder and its menu link points at its first child page. This site's own `docs` label demonstrates that, and the sidebar on this page shows the two submenu levels below it.

Images and files uploaded anywhere land in a content-addressed store served from `/_f/{hash}`, so links survive page moves. The server picks AVIF, WebP or JPEG from your browser's Accept header. The article editor's format bar and copy-paste both upload images for you.

{dates}
"""

# The full feature walkthrough: every supported extension in one long
# article, each shown as Markdown source followed by the rendered result.
MD_ARTICLE = """\
# Markdown

Everything Pagerite's renderer supports, on one long page — each feature shown first as Markdown source, then rendered. This page is also the live demo of the reading layout: on a wide screen the text flows in columns, and side boxes lean into the margin.

## Text and headings

```markdown
*Emphasis*, **strong**, ~~strikethrough~~, `inline code`, and a
[link to the front page](/). A hard line break
is just a newline.

Straight quotes become "curly", dashes -- and --- come out
properly, and ... becomes an ellipsis, all automatically.
```

*Emphasis*, **strong**, ~~strikethrough~~, `inline code`, and a [link to the front page](/). A hard line break
is just a newline.

Straight quotes become "curly", dashes -- and --- come out properly, and ... becomes an ellipsis, all automatically.

Headings from `##` down organize the article. On pages with at least three of them, each h1/h2 gets an anchor id and a self-link, so sections are linkable (try hovering a heading here) — and the editor's section pens and scroll sync key off the same anchors.

## Lists

```markdown
- One
- Two
  - Nested

1. First
2. Second

- [x] Task lists with real checkboxes
- [x] Clickable on the rendered page
- [ ] Like this one
```

- One
- Two
  - Nested

1. First
2. Second

- [x] Task lists with real checkboxes
- [x] Clickable on the rendered page
- [ ] Like this one

## Quotes and alerts

```markdown
> A blockquote. Newlines inside it are kept,
> and a blank `>` line starts a new paragraph.

> [!NOTE]
> GitHub-style alerts — `NOTE`, `TIP`, `IMPORTANT`, `WARNING`, `CAUTION` —
> render as callout boxes.
```

> A blockquote. Newlines inside it are kept,
> and a blank `>` line starts a new paragraph.

> [!NOTE]
> GitHub-style alerts — `NOTE`, `TIP`, `IMPORTANT`, `WARNING`, `CAUTION` —
> render as callout boxes.

## Code

Fenced blocks get server-side syntax highlighting, and a copy button on hover:

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

## Sub- and superscript

```markdown
H~2~O and x^2^ + y^2^ = z^2^.
```

H~2~O and x^2^ + y^2^ = z^2^.

## Admonitions

```markdown
!!! note
    An admonition block for notes, warnings, tips...

!!! warning "Mind the whale"
    With an optional custom title.
```

!!! note
    An admonition block for notes, warnings, tips...

!!! warning "Mind the whale"
    With an optional custom title.

## Containers and margin notes

`::: name` wraps its contents in a `<div class="name">` — brace attributes allowed. Three names are built in: `aside` floats a muted side box, `margin` marks a block as a margin note, and `nocols` opts its section out of the column layout. The `{.margin}` attribute does the same for a single block, written on its last line:

````markdown
::: aside
A side box. On all but phone widths it floats in the side zone at
the article's left, and the text never moves.
:::

This paragraph is a margin note.
{.margin}

::: nocols
This section never flows into columns, however long the article.
:::
````

::: aside
A side box. On all but phone widths it floats in the side zone at the article's left, and the text never moves.
:::

This paragraph is a margin note.
{.margin}

::: nocols
This section never flows into columns, however long the article.
:::

## Raw HTML

HTML passes through untouched — useful for `<kbd>` keys, `<details>` sections, embedded media:

```html
<details><summary>Click to expand</summary>Hidden content.</details>
```

<details><summary>Click to expand</summary>Hidden content.</details>

## Datelines

A `{dates}` line on its own expands to the article's published/updated dateline:

```markdown
{dates}
```

{dates}

## Images and layout

An image standing alone in its paragraph becomes a `<figure>`; its title becomes the caption; brace attributes control placement — `{.right}`, `{.left}`, `{.margin}`, `{.wide}`, or plain ones like `width=280`. That deserves its own page: [Images and Layout](/docs/markdown/images-and-layout).

## The page title

If your Markdown contains its own `# heading`, the page title is not repeated as a second h1 — it still supplies the `<title>` and the menu labels. This page is an example: its `# Markdown` heading *is* the title.
"""

MD_LAYOUT = """\
## Images and figures

An image standing alone in its paragraph becomes a `<figure>`; its title becomes the caption. Inline images within text stay plain.

```markdown
![Abstract shapes](shapes.svg "A captioned figure")
```

![Abstract shapes](shapes.svg "A captioned figure")

## Positioning with attributes

Brace attributes (the attrs plugin) control placement: `{.right}` and `{.left}` float, `{.margin}` moves a figure into the side zone, `{.wide}` breaks out of the text column, and plain attributes like `width=280` pass through.

```markdown
![Abstract shapes](shapes.svg "Floated right"){.right width=280}
```

![Abstract shapes](shapes.svg "Floated right — text wraps around it"){.right width=280}

Floated images let the text wrap around them, like this paragraph does. Relative image paths resolve against the page's own path, so attached files travel with the page. Uploaded files get content-addressed `/_f/` URLs that never break, no matter where the page moves.

```markdown
![Abstract waves](waves.svg "A figure in the margin"){.margin}
```

![Abstract waves](waves.svg "A figure in the margin, with {.margin}"){.margin}

The same figure as a margin note: it leans into the side zone left of the text on all but phone widths, alongside the text it belongs to.

{.wide} artwork spans the full content width:

```markdown
![Dunes](dunes.svg "Full-width artwork"){.wide}
```

![Dunes](dunes.svg "Full-width artwork between sections"){.wide}
"""

GALLERY = """\
This page's banner is the **eyes** design — a critter in the grass in — picked from banner menu (⚙️ in the top right corner). The selection applies to current page and all its children, allowing differently themed sections be created. [Night Sky](night-sky) picked its own. You should also find the theme settings, which allow choosing overall site theme, fonts and transitions. You may wish to try the more playful **summer** theme which the eyes theme builds on.

## Break out of the box!

![Dunes](dunes.svg){.wide}

::: aside
![Abstract waves](waves.svg)

## Aside boxes

When you have to sideline a bit with something important to say, use `::: aside` and end with `:::`, markdown between.

On larger screens they break outside the normal page bounds. `{.margin}` can be used to a similar effect without a box.
:::

Images and text boxes can also be positioned for a more lively layout.

![Shapes](shapes.svg "Floated with {.right}"){.right}

This text wraps around a left or right floated figure. The caption comes from the image title, with additional styling like `{.left width=240}` — brace attributes on the image itself.

Note how the layout may take different forms from a phone in portrait to widest of desktop browsers, not leaving large empty areas nor being constrained to a classic container box model.

Lifting off elements here and there makes a great difference to how your site is received!

### Design matters

Good graphical design gives a website a clear visual structure and makes information easy to understand at a glance. Layout, spacing, typography, color, and imagery should work together to establish hierarchy and guide attention naturally through the page. Consistency between sections also helps users quickly learn how the interface is organized.

A strong website layout balances visual character with usability. Content should have enough space to remain readable, while navigation and important actions should be easy to find without dominating the design. Responsive layouts should preserve these relationships across different screen sizes rather than simply shrinking the desktop arrangement.
"""

NIGHT_SKY = """\
This page's banner is not an image or a code snippet — it's the **stars** banner design, picked from a dropdown in the banner editor (🖊️ in the banner corner). Nothing is stored in the page beyond that choice.

Banner designs are folders in `pagerite/themes/{name}/` — a `banner.css` plus a `banner.html` or `banner.svg` — so a design can be anything from a static gradient to an animated canvas like the starfield above. This site ships `stars` and `eyes` (a critter in the grass, seen on the [gallery](/showcase/gallery)), and themes can bring their own.

Subpages inherit the nearest banner and design up their path, so a whole section can share one look — set one on a category and every page under it gets it, until a page overrides with its own. This page is a leaf: the design chosen here affects nothing else.

A page can also carry its own banner HTML — an `<img>`, a styled div, a canvas with a script — which renders *on top of* the design's artwork, so author code always wins. But most of the time, picking a design is all you need.
"""

# Moby-Dick; or, The Whale (1851), Herman Melville — public domain.
# Chapter 1, abridged and headed. A real long-read: flowing sections,
# figures, a list, side notes — not a feature showcase. (Engravings:
# Augustus Burnham Shute's illustrations for the 1892 edition, public
# domain; the wave is Hokusai, public domain.)
LOOMINGS = """\
*The opening of Herman Melville's Moby-Dick (1851), abridged — here to show what a longer article feels like: the multi-column layout on wide screens, images breaking up the text, and the gentle reveal as sections scroll into view.*

{dates}

![The Great Wave off Kanagawa](great-wave.jpg "Hokusai, c. 1831 — the sea, full-bleed with {.wide}"){.wide}

## The watery part of the world

Call me Ishmael. Some years ago — never mind how long precisely — having little or no money in my purse, and nothing particular to interest me on shore, I thought I would sail about a little and see the watery part of the world. It is a way I have of driving off the spleen and regulating the circulation. Whenever I find myself growing grim about the mouth; whenever it is a damp, drizzly November in my soul; whenever I find myself involuntarily pausing before coffin warehouses, and bringing up the rear of every funeral I meet; and especially whenever my hypos get such an upper hand of me, that it requires a strong moral principle to prevent me from deliberately stepping into the street, and methodically knocking people's hats off — then, I account it high time to get to sea as soon as I can. This is my substitute for pistol and ball. With a philosophical flourish Cato throws himself upon his sword; I quietly take to the ship. There is nothing surprising in this. If they but knew it, almost all men in their degree, some time or other, cherish very nearly the same feelings towards the ocean with me.

::: aside
Melville interrupts his story often — whole chapters on cetology, rope and chowder. Abridgments drop most of them, but notes like this one are where they would have gone.
:::

There now is your insular city of the Manhattoes, belted round by wharves as Indian isles by coral reefs — commerce surrounds it with her surf. Right and left, the streets take you waterward. Its extreme downtown is the battery, where that noble mole is washed by waves, and cooled by breezes, which a few hours previous were out of sight of land. Look at the crowds of water-gazers there.

Circumambulate the city of a dreamy Sabbath afternoon. Go from Corlears Hook to Coenties Slip, and from thence, by Whitehall, northward. What do you see? — Posted like silent sentinels all around the town, stand thousands upon thousands of mortal men fixed in ocean reveries. Some leaning against the spiles; some seated upon the pier-heads; some looking over the bulwarks of ships from China; some high aloft in the rigging, as if striving to get a still better seaward peep. But these are all landsmen; of week days pent up in lath and plaster — tied to counters, nailed to benches, clinched to desks. How then is this? Are the green fields gone? What do they here?

But look! here come more crowds, pacing straight for the water, and seemingly bound for a dive. Strange! Nothing will content them but the extremest limit of the land; loitering under the shady lee of yonder warehouses will not suffice. No. They must get just as nigh the water as they possibly can without falling in. And there they stand — miles of them — leagues. Inlanders all, they come from lanes and alleys, streets and avenues — north, east, south, and west. Yet here they all unite. Tell me, does the magnetic virtue of the needles of the compasses of all those ships attract them thither?

## Meditation and water

Once more. Say you are in the country; in some high land of lakes. Take almost any path you please, and ten to one it carries you down in a dale, and leaves you there by a pool in the stream. There is magic in it. Let the most absent-minded of men be plunged in his deepest reveries — stand that man on his legs, set his feet a-going, and he will infallibly lead you to water, if water there be in all that region. Should you ever be athirst in the great American desert, try this experiment, if your caravan happen to be supplied with a metaphysical professor. Yes, as every one knows, meditation and water are wedded for ever.

Ishmael sails from New Bedford, the whaling port south of Boston — Nantucket was the older, prouder whaling town, and he briefly considers it first.
{.margin}

### The artist's problem

But here is an artist. He desires to paint you the dreamiest, shadiest, quietest, most enchanting bit of romantic landscape in all the valley of the Saco. What is the chief element he employs? There stand his trees, each with a hollow trunk, as if a hermit and a crucifix were within; and here sleeps his meadow, and there sleep his cattle; and up from yonder cottage goes a sleepy smoke. Deep into distant woodlands winds a mazy way, reaching to overlapping spurs of mountains bathed in their hill-side blue. But though the picture lies thus tranced, and though this pine-tree shakes down its sighs like leaves upon this shepherd's head, yet all were vain, unless the shepherd's eye were fixed upon the magic stream before him.

Why did the poor poet of Tennessee, upon suddenly receiving two handfuls of silver, deliberate whether to buy him a coat, which he sadly needed, or invest his money in a pedestrian trip to Rockaway Beach? Why is almost every robust healthy boy with a robust healthy soul in him, at some time or other crazy to go to sea? Why upon your first voyage as a passenger, did you yourself feel such a mystical vibration, when you were first told that you and your ship were now out of sight of land? Why did the old Persians hold the sea holy? Why did the Greeks give it a separate deity, and own brother of Jove? Surely all this is not without meaning. And still deeper the meaning of that story of Narcissus, who because he could not grasp the tormenting, mild image he saw in the fountain, plunged into it and was drowned. But that same image, we ourselves see in all rivers and oceans. It is the image of the ungraspable phantom of life; and this is the key to it all.

## A simple sailor, right before the mast

Now, when I say that I am in the habit of going to sea whenever I begin to grow hazy about the eyes, and begin to be over conscious of my lungs, I do not mean to have it inferred that I ever go to sea as a passenger. For to go as a passenger you must needs have a purse, and a purse is but a rag unless you have something in it. Besides, passengers get sea-sick — grow quarrelsome — don't sleep of nights — do not enjoy themselves much, as a general thing; — no, I never go as a passenger; nor, though I am something of a salt, do I ever go to sea as a Commodore, or a Captain, or a Cook. I abandon the glory and distinction of such offices to those who like them. For my part, I abominate all honorable respectable toils, trials, and tribulations of every kind whatsoever. It is quite as much as I can do to take care of myself, without taking care of ships, barques, brigs, schooners, and what not.

![Moby Dick breeches a whaleboat](md-whale.jpg "Augustus Burnham Shute, 1892"){.right width=400}

No, when I go to sea, I go as a simple sailor, right before the mast, plumb down into the forecastle, aloft there to the royal mast-head. True, they rather order me about some, and make me jump from spar to spar, like a grasshopper in a May meadow. And at first, this sort of thing is unpleasant enough. It touches one's sense of honor, particularly if you come of an old established family in the land, the Van Rensselaers, or Randolphs, or Hardicanutes. And more than all, if just previous to putting your hand into the tar-pot, you have been lording it as a country schoolmaster, making the tallest boys stand in awe of you. The transition is a keen one, I assure you, from a schoolmaster to a sailor, and requires a strong decoction of Seneca and the Stoics to enable you to grin and bear it. But even this wears off in time.

What of it, if some old hunks of a sea-captain orders me to get a broom and sweep down the decks? What does that indignity amount to, weighed, I mean, in the scales of the New Testament? Do you think the archangel Gabriel thinks anything the less of me, because I promptly and respectfully obey that old hunks in that particular instance? Who ain't a slave? Tell me that. Well, then, however the old sea-captains may order me about — however they may thump and punch me about, I have the satisfaction of knowing that it is all right; that everybody else is one way or other served in much the same way — either in a physical or metaphysical point of view, that is; and so the universal thump is passed round, and all hands should rub each other's shoulder-blades, and be content.

And finally, what shall I say of the reasons for going a-whaling? Chief among them:

- The overwhelming idea of the great whale himself — such a portentous and mysterious monster roused all my curiosity.
- The undeliverable, nameless perils of the whale, and the attendants of the wondrous world of waters.
- The tormenting, mild image of the ungraspable phantom of life, seen in all rivers and oceans.

These were the things that finally drew me to the sea — and if they but knew it, almost all men cherish very nearly the same feelings towards the ocean with me.
"""

SMALL_RELEASES = """\
Software wants to be shipped. The longer a change sits unmerged, the more it rots: context fades, conflicts accumulate, and the diff grows teeth.

1. Cut the scope until it fits in a day.
2. Ship it behind whatever door you like.
3. Let real use argue with your assumptions.

A release is a conversation with reality. Small releases keep the conversation lively — and small *pieces* keep the whole thing standing, as [the comic on the About page](/about) illustrates all too well.
"""

ABOUT = """\
This site runs on **Pagerite**: FastAPI + html5tagger + kanta, with content written in Markdown and rendered on the fly.

- [How to edit this site](/docs/editing)
- [Everything Markdown can do](/docs/markdown)
- [The showcase](/showcase/gallery)

Pagerite keeps its dependency list short and knows every entry on it. Modern software in general builds on taller towers of other people's work:

[![xkcd 2347: Dependency](https://imgs.xkcd.com/comics/dependency.png "xkcd 2347: Dependency"){width=280}](https://xkcd.com/2347/)

*Replace this page with whatever your site is about.*
"""

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
#: banner design). Designs demonstrate per-page choice: the gallery
#: picks "eyes" (its page alone), night-sky picks "stars"; everything
#: else inherits the active theme's own design.
#: Note there are deliberately no "docs" or "showcase" landing pages:
#: those labels are created without content, so they render a placeholder
#: page and their nav links point at the first child (see
#: views.first_leaf). "showcase" is seeded explicitly (empty markdown,
#: which the seeder leaves as content=None) purely to fix its menu order.
PAGES: dict[str, tuple[str, str, dict[str, bytes], str, float, str | None]] = {
    "": ("Welcome", WELCOME, {"waves.svg": WAVES_SVG.encode()}, "", 1, None),
    "about": ("About", ABOUT, {}, "", 3, None),
    "docs/editing": ("Editing This Site", EDITING, {}, "", 1, None),
    "docs/markdown": ("Markdown", MD_ARTICLE, {}, "", 2, None),
    "docs/markdown/images-and-layout": (
        "Images and Layout",
        MD_LAYOUT,
        {
            "shapes.svg": SHAPES_SVG.encode(),
            "waves.svg": WAVES_SVG.encode(),
            "dunes.svg": DUNES_SVG.encode(),
        },
        "",
        1,
        None,
    ),
    "showcase": ("Showcase", "", {}, "", 4, None),
    "showcase/gallery": (
        "Gallery",
        GALLERY,
        {
            "dunes.svg": DUNES_SVG.encode(),
            "shapes.svg": SHAPES_SVG.encode(),
            "waves.svg": WAVES_SVG.encode(),
        },
        "",
        1,
        "eyes",
    ),
    "showcase/loomings": (
        "Loomings — a Long Read",
        LOOMINGS,
        {
            "great-wave.jpg": _asset("great-wave.jpg"),
            "md-whale.jpg": _asset("md-whale.jpg"),
        },
        "",
        2,
        None,
    ),
    "showcase/night-sky": ("Night Sky", NIGHT_SKY, {}, "", 3, "stars"),
    "showcase/small-releases": ("Small Releases", SMALL_RELEASES, {}, "", 4, None),
}

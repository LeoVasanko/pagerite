"""Seed content written to the database on first creation only
(``@kanta.bootstrap`` in ``app.py``).

A "welcome to your new site" starter: a structured docs section (three
menu levels deep) covering editing and the full Markdown feature set —
each feature shown as its Markdown source in a code block followed by
the rendered result — and a showcase section with image positioning,
long-form layout and a simple custom banner.

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

- The [docs](/docs/editing) section explains how to edit this site and shows every supported Markdown feature, source and result side by side.
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

The URL is the structure: a page at `docs/markdown/basics` lives under `docs` and `markdown`, and the menus are derived from that. Slugs are lowercase ASCII (`a-z 0-9 - _`). A node without content is a category label — it renders a placeholder and its menu link points at its first child page.

Images and files uploaded anywhere land in a content-addressed store served from `/_f/{hash}.ext`, so links survive page moves. The article editor's format bar and copy-paste both upload images for you.

{dates}
"""

MD_BASICS = """\
# Markdown Basics

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
# Markdown Extensions

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
Pages can attach images and position them freely. The vector artwork here is generated SVG; the woodblock print is Hokusai's *The Great Wave off Kanagawa* (public domain, via Wikimedia Commons).

![The Great Wave off Kanagawa](great-wave.jpg "Hokusai, c. 1831 — full-bleed with {.wide}"){.wide}

A wide image escapes the text column for emphasis between sections. No HTML needed — just Markdown and an attribute.

![Shapes](shapes.svg "Floated left with {.left}"){.left width=240}

This text wraps around a left-floated figure. The caption comes from the image title, the float from `{.left width=240}` — brace attributes on the image itself.

![Abstract waves](waves.svg "Floated right with {.right}"){.right width=240}

Mixing floats in one article is fine. Both images were uploaded to this page and referenced by relative path, so the whole page (images included) can be moved in the structure tree without breaking anything.
"""

NIGHT_SKY = """\
This page's banner is not an image or a code snippet — it's the **stars** banner design, picked from a dropdown in the banner editor (🖊️ in the banner corner). Nothing is stored in the page beyond that choice.

Banner designs are folders in `pagerite/themes/{name}/` — a `banner.css` plus a `banner.html` or `banner.svg` — so a design can be anything from a static gradient to an animated canvas like the starfield above. This site ships `stars` and `eyes` (a critter in the grass), and themes can bring their own.

Subpages inherit the nearest banner and design up their path, so a whole section can share one look. This page is a leaf: set a design here and nothing else is affected.

A page can also carry its own banner HTML — an `<img>`, a styled div, a canvas with a script — which renders *on top of* the design's artwork, so author code always wins. But most of the time, picking a design is all you need.
"""

# Moby-Dick; or, The Whale (1851), Herman Melville — public domain.
# Chapter 1, abridged and headed. A real long-read: flowing sections,
# figures, a list — not a feature showcase. (Engravings: Augustus
# Burnham Shute's illustrations for the 1892 edition, public domain.)
LOOMINGS = """\
*The opening of Herman Melville's Moby-Dick (1851), abridged — here to show what a longer article feels like: the multi-column layout on wide screens, images breaking up the text, and the gentle reveal as sections scroll into view.*

{dates}

![Dunes at dusk](dunes.svg "Full-width artwork between sections"){.wide}

## The watery part of the world

Call me Ishmael. Some years ago — never mind how long precisely — having little or no money in my purse, and nothing particular to interest me on shore, I thought I would sail about a little and see the watery part of the world. It is a way I have of driving off the spleen and regulating the circulation. Whenever I find myself growing grim about the mouth; whenever it is a damp, drizzly November in my soul; whenever I find myself involuntarily pausing before coffin warehouses, and bringing up the rear of every funeral I meet; and especially whenever my hypos get such an upper hand of me, that it requires a strong moral principle to prevent me from deliberately stepping into the street, and methodically knocking people's hats off — then, I account it high time to get to sea as soon as I can. This is my substitute for pistol and ball. With a philosophical flourish Cato throws himself upon his sword; I quietly take to the ship. There is nothing surprising in this. If they but knew it, almost all men in their degree, some time or other, cherish very nearly the same feelings towards the ocean with me.

![Moby Dick breeches a whaleboat](md-whale.jpg "Augustus Burnham Shute, 1892 — public domain"){.right width=320}

There now is your insular city of the Manhattoes, belted round by wharves as Indian isles by coral reefs — commerce surrounds it with her surf. Right and left, the streets take you waterward. Its extreme downtown is the battery, where that noble mole is washed by waves, and cooled by breezes, which a few hours previous were out of sight of land. Look at the crowds of water-gazers there.

Circumambulate the city of a dreamy Sabbath afternoon. Go from Corlears Hook to Coenties Slip, and from thence, by Whitehall, northward. What do you see? — Posted like silent sentinels all around the town, stand thousands upon thousands of mortal men fixed in ocean reveries. Some leaning against the spiles; some seated upon the pier-heads; some looking over the bulwarks of ships from China; some high aloft in the rigging, as if striving to get a still better seaward peep. But these are all landsmen; of week days pent up in lath and plaster — tied to counters, nailed to benches, clinched to desks. How then is this? Are the green fields gone? What do they here?

But look! here come more crowds, pacing straight for the water, and seemingly bound for a dive. Strange! Nothing will content them but the extremest limit of the land; loitering under the shady lee of yonder warehouses will not suffice. No. They must get just as nigh the water as they possibly can without falling in. And there they stand — miles of them — leagues. Inlanders all, they come from lanes and alleys, streets and avenues — north, east, south, and west. Yet here they all unite. Tell me, does the magnetic virtue of the needles of the compasses of all those ships attract them thither?

## Meditation and water

Once more. Say you are in the country; in some high land of lakes. Take almost any path you please, and ten to one it carries you down in a dale, and leaves you there by a pool in the stream. There is magic in it. Let the most absent-minded of men be plunged in his deepest reveries — stand that man on his legs, set his feet a-going, and he will infallibly lead you to water, if water there be in all that region. Should you ever be athirst in the great American desert, try this experiment, if your caravan happen to be supplied with a metaphysical professor. Yes, as every one knows, meditation and water are wedded for ever.

### The artist's problem

But here is an artist. He desires to paint you the dreamiest, shadiest, quietest, most enchanting bit of romantic landscape in all the valley of the Saco. What is the chief element he employs? There stand his trees, each with a hollow trunk, as if a hermit and a crucifix were within; and here sleeps his meadow, and there sleep his cattle; and up from yonder cottage goes a sleepy smoke. Deep into distant woodlands winds a mazy way, reaching to overlapping spurs of mountains bathed in their hill-side blue. But though the picture lies thus tranced, and though this pine-tree shakes down its sighs like leaves upon this shepherd's head, yet all were vain, unless the shepherd's eye were fixed upon the magic stream before him.

Why did the poor poet of Tennessee, upon suddenly receiving two handfuls of silver, deliberate whether to buy him a coat, which he sadly needed, or invest his money in a pedestrian trip to Rockaway Beach? Why is almost every robust healthy boy with a robust healthy soul in him, at some time or other crazy to go to sea? Why upon your first voyage as a passenger, did you yourself feel such a mystical vibration, when you were first told that you and your ship were now out of sight of land? Why did the old Persians hold the sea holy? Why did the Greeks give it a separate deity, and own brother of Jove? Surely all this is not without meaning. And still deeper the meaning of that story of Narcissus, who because he could not grasp the tormenting, mild image he saw in the fountain, plunged into it and was drowned. But that same image, we ourselves see in all rivers and oceans. It is the image of the ungraspable phantom of life; and this is the key to it all.

## A simple sailor, right before the mast

Now, when I say that I am in the habit of going to sea whenever I begin to grow hazy about the eyes, and begin to be over conscious of my lungs, I do not mean to have it inferred that I ever go to sea as a passenger. For to go as a passenger you must needs have a purse, and a purse is but a rag unless you have something in it. Besides, passengers get sea-sick — grow quarrelsome — don't sleep of nights — do not enjoy themselves much, as a general thing; — no, I never go as a passenger; nor, though I am something of a salt, do I ever go to sea as a Commodore, or a Captain, or a Cook. I abandon the glory and distinction of such offices to those who like them. For my part, I abominate all honorable respectable toils, trials, and tribulations of every kind whatsoever. It is quite as much as I can do to take care of myself, without taking care of ships, barques, brigs, schooners, and what not.

No, when I go to sea, I go as a simple sailor, right before the mast, plumb down into the forecastle, aloft there to the royal mast-head. True, they rather order me about some, and make me jump from spar to spar, like a grasshopper in a May meadow. And at first, this sort of thing is unpleasant enough. It touches one's sense of honor, particularly if you come of an old established family in the land, the Van Rensselaers, or Randolphs, or Hardicanutes. And more than all, if just previous to putting your hand into the tar-pot, you have been lording it as a country schoolmaster, making the tallest boys stand in awe of you. The transition is a keen one, I assure you, from a schoolmaster to a sailor, and requires a strong decoction of Seneca and the Stoics to enable you to grin and bear it. But even this wears off in time.

![The final chase](md-chase.jpg "A. Burnham Shute's illustration of the final chase, 1892 — public domain")

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

A release is a conversation with reality. Small releases keep the conversation lively.
"""

ABOUT = """\
This site runs on **Pagerite**: FastAPI + html5tagger + kanta, with content written in Markdown and rendered on the fly.

- [How to edit this site](/docs/editing)
- [Markdown features](/docs/markdown/basics)
- [The showcase](/showcase/gallery)

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
#: banner design). Designs demonstrate inheritance: the "showcase" label
#: picks "eyes" (all its pages show the critter), and the leaf
#: "showcase/night-sky" overrides that with "stars". Elsewhere the active
#: theme's own design shows.
#: Note there are deliberately no "docs" or "showcase" landing pages:
#: those labels are created without content, so they render a placeholder
#: page and their nav links point at the first child (see
#: views.first_leaf). "showcase" is seeded explicitly (empty markdown,
#: which the seeder leaves as content=None) just to carry the design.
PAGES: dict[str, tuple[str, str, dict[str, bytes], str, float, str | None]] = {
    "": ("Welcome", WELCOME, {"waves.svg": WAVES_SVG.encode()}, "", 1, None),
    "about": ("About", ABOUT, {}, "", 3, None),
    "docs/editing": ("Editing This Site", EDITING, {}, "", 1, None),
    "docs/markdown/basics": (
        "Basics",
        MD_BASICS,
        {},
        "",
        1,
        None,
    ),
    "docs/markdown/extensions": ("Extensions", MD_EXTENSIONS, {}, "", 2, None),
    "docs/markdown/images-and-layout": (
        "Images and Layout",
        MD_LAYOUT,
        {"shapes.svg": SHAPES_SVG.encode(), "dunes.svg": DUNES_SVG.encode()},
        "",
        3,
        None,
    ),
    "showcase": ("Showcase", "", {}, "", 4, "eyes"),
    "showcase/gallery": (
        "Gallery",
        GALLERY,
        {
            "great-wave.jpg": _asset("great-wave.jpg"),
            "shapes.svg": SHAPES_SVG.encode(),
            "waves.svg": WAVES_SVG.encode(),
        },
        "",
        1,
        None,
    ),
    "showcase/loomings": (
        "Loomings — a Long Read",
        LOOMINGS,
        {
            "dunes.svg": DUNES_SVG.encode(),
            "md-whale.jpg": _asset("md-whale.jpg"),
            "md-chase.jpg": _asset("md-chase.jpg"),
        },
        "",
        2,
        None,
    ),
    "showcase/night-sky": ("Night Sky", NIGHT_SKY, {}, "", 3, "stars"),
    "showcase/small-releases": ("Small Releases", SMALL_RELEASES, {}, "", 4, None),
}

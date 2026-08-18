"""Seed content written to the database on first run (when it is empty).

Demonstrates the formatting options: images attached to pages and served
from the page path, figures with captions, attribute classes for
positioning, footnotes, definition lists, task lists, tables and raw HTML.
"""

WELCOME = """\
Welcome to your new **Pagerite** site. Pages are written in Markdown — including raw HTML — and served from pretty URLs.

Have a look around:

- The [docs](/docs) section explains [how to write content](/docs/editing),
  including images and positioning.
- [The Long Read](/blog/the-long-read) demonstrates a longer article with
  scroll effects.
- The [about](/about) page shows off assorted formatting.

![Abstract waves](waves.svg "Generated SVG artwork, attached to this page")
"""

ABOUT = """\
This site runs on **Pagerite**: FastAPI + html5tagger + kanta, with content written in Markdown.

Some formatting samples:

- [x] Write content in Markdown
- [x] Attach images to pages
- [ ] Add editing UI

Term
: A definition list entry, rendered by the deflist plugin.

And a table:

| Feature | Status |
|---------|--------|
| Pages   | done   |
| Images  | done   |
| Comments| later  |

Footnotes work too.[^1]

[^1]: Rendered at the bottom of the page, with a back-reference.
"""

EDITING = """\
Pages are written in Markdown with extensions. Everything below is plain Markdown source — no special support from the article is needed for the site's layout or scroll effects.

## Images

Upload a file (`PUT /_api/files/{filename}`) and it lands in the content-addressed store, served immutable from `/_f/{hash}.ext` — an absolute URL that survives page moves:

```
![Abstract shapes](/_f/....svg "A captioned figure"){.right width=280}
```

![Abstract shapes](shapes.svg "A captioned figure, floated right with an attribute class"){.right width=280}

The title becomes a `<figcaption>`, and brace attributes (the attrs plugin) control positioning: `{.right}`, `{.left}`, `{.wide}`, plus plain attributes like `width=280`. Absolute and external URLs pass through unchanged.

## Text

*Emphasis*, **strong**, ~~strikethrough~~, `inline code`, and [links](/about) as usual. Blockquotes:

> The URL space is the author's. Pretty slugs at the root, nesting only
> where the content is genuinely structured.

## Code

```python
def render(text: str, page_path: str) -> str:
    return md.render(text, {"page_path": page_path})
```
"""

LONG_READ = """\
*An essay long enough to scroll, to demonstrate the gentle reveal of headings, figures and code blocks as they enter the viewport.*

![Layered dunes](dunes.svg "Full-width artwork between sections"){.wide}

## Chapter one

The distinction between a blog and a website is largely an accident of history. Early content management systems filed everything under "posts", stamped them with a date, and arranged them in reverse chronological order under a `/blog/` prefix. Anything else was a "page", which lived somewhere else entirely, often in a separate editing interface with separate rules.

But readers do not think in these terms. A reader follows a link, reads what is there, and follows another link. The URL is a promise about where something lives, not about which database table it came from. Pagerite therefore treats every piece of content as a page: named, addressable, and rendered on the fly.

## Chapter two

Consider what happens to URLs when the tooling leads the design. You get addresses like `/cms/frontpage` or `/blog/post1` — the name of the machine leaking into the name of the thing. The slug should be chosen by the author, the way a book's title is chosen, and it should sit at the root of the site like the title sits on the cover.

Nesting still has its place. Structured content — documentation, a series, a portfolio — benefits from paths that mirror the structure. The navigation on this very site is derived from the paths: open a section, and you see what it contains. No menu editor, no duplication of structure in two places.

Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat. Duis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla pariatur. Excepteur sint occaecat cupidatat non proident, sunt in culpa qui officia deserunt mollit anim id est laborum.

Sed ut perspiciatis unde omnis iste natus error sit voluptatem accusantium doloremque laudantium, totam rem aperiam, eaque ipsa quae ab illo inventore veritatis et quasi architecto beatae vitae dicta sunt explicabo. Nemo enim ipsam voluptatem quia voluptas sit aspernatur aut odit aut fugit, sed quia consequuntur magni dolores eos qui ratione voluptatem sequi nesciunt.

## Chapter three

On the reading experience itself: motion on the web is usually either absent or obnoxious. The interesting middle ground is motion that acknowledges the reader's own movement — the scroll. Elements that fade in as they enter the viewport give the page a sense of depth, as if the content were arriving just in time.

Crucially, none of this may depend on the article. The author writes Markdown; the effects come from the layout. And when the reader prefers reduced motion, everything must hold still.

Neque porro quisquam est, qui dolorem ipsum quia dolor sit amet, consectetur, adipisci velit, sed quia non numquam eius modi tempora incidunt ut labore et dolore magnam aliquam quaerat voluptatem. Ut enim ad minima veniam, quis nostrum exercitationem ullam corporis suscipit laboriosam, nisi ut aliquid ex ea commodi consequatur?

```text
Quis autem vel eum iure reprehenderit
qui in ea voluptate velit esse quam nihil
molestiae consequatur, vel illum qui
dolorem eum fugiat quo voluptas nulla pariatur?
```

At vero eos et accusamus et iusto odio dignissimos ducimus qui blanditiis praesentium voluptatum deleniti atque corrupti quos dolores et quas molestias excepturi sint occaecati cupiditate non provident, similique sunt in culpa qui officia deserunt mollitia animi, id est laborum et dolorum fuga. Et harum quidem rerum facilis est et expedita distinctio.

## Chapter four

Nam libero tempore, cum soluta nobis est eligendi optio cumque nihil impedit quo minus id quod maxime placeat facere possimus, omnis voluptas assumenda est, omnis dolor repellendus. Temporibus autem quibusdam et aut officiis debitis aut rerum necessitatibus saepe eveniet ut et voluptates repudiandae sint et molestiae non recusandae.

Itaque earum rerum hic tenetur a sapiente delectus, ut aut reiciendis voluptatibus maiores alias consequatur aut perferendis doloribus asperiores repellat. And so we arrive back where we started: the blog and the website were one thing all along. [Return to the front page](/).
"""

NOTES_ON_URLS = """\
A URL is part of the content. A few rules of thumb I keep coming back to:

- Pick slugs like book titles, not like database keys.
- Nest only when the structure is real.
- Once published, a URL is a promise. Redirect if you must break it.

> Cool URIs don't change; uncool ones at least apologise.

That's all. Short posts are posts too.
"""

CANVAS_NIGHTS = """\
This post's banner is not an image at all — it's a `<canvas>` animated by a few lines of JavaScript embedded in the page's banner HTML.

Banners on this site are arbitrary markup: an image, a gradient div, or a small animated scene like the one above. Subpages inherit the nearest banner up their path, so a whole section can share one look.

```js
// the essence of the banner above
stars.forEach(s => { s.x = (s.x + s.speed * dt) % 1 })
```

No build step, no framework — the snippet is stored with the page and dropped into the header as-is.
"""

SMALL_RELEASES = """\
Software wants to be shipped. The longer a change sits unmerged, the more it rots: context fades, conflicts accumulate, and the diff grows teeth.

1. Cut the scope until it fits in a day.
2. Ship it behind whatever door you like.
3. Let real use argue with your assumptions.

A release is a conversation with reality. Small releases keep the conversation lively.
"""

CANVAS_BANNER = """\
<canvas id="stars"></canvas>
<script>
(() => {
  const c = document.getElementById("stars");
  const ctx = c.getContext("2d");
  const fit = () => { c.width = c.clientWidth; c.height = c.clientHeight; };
  fit();
  addEventListener("resize", fit);
  const stars = Array.from({ length: 110 }, () => ({
    x: Math.random(), y: Math.random(),
    r: Math.random() * 1.4 + 0.3, v: Math.random() * 0.05 + 0.01,
  }));
  let prev = performance.now();
  (function frame(now) {
    if (!c.isConnected) return;
    const dt = Math.min(now - prev, 100); prev = now;
    ctx.fillStyle = "#0b0e1d";
    ctx.fillRect(0, 0, c.width, c.height);
    ctx.fillStyle = "#cdd6ff";
    for (const s of stars) {
      s.x = (s.x + s.v * dt / 1000) % 1;
      ctx.beginPath();
      ctx.arc(s.x * c.width, s.y * c.height, s.r, 0, 7);
      ctx.fill();
    }
    requestAnimationFrame(frame);
  })(prev);
})();
</script>
"""

EYES_BANNER = """\
<canvas id="eyes"></canvas>
<script>
(() => {
  const c = document.getElementById("eyes");
  const ctx = c.getContext("2d");
  const BG = "#f3e9d7";
  const fit = () => { c.width = c.clientWidth; c.height = c.clientHeight; };
  fit();
  addEventListener("resize", fit);

  // Mouse in canvas coordinates; pupils wander idly when it goes stale.
  let mx = 0, my = 0, lastMove = 0;
  addEventListener("mousemove", (e) => {
    const r = c.getBoundingClientRect();
    mx = e.clientX - r.left;
    my = e.clientY - r.top;
    lastMove = performance.now();
  });

  // The pair of eyes is one critter: it wanders around the banner, and
  // every so often ducks below the bottom edge, then pops back up.
  let gx = 0.5, gy = 0.5;   // group position (fractions of the canvas)
  let tx = 0.5, ty = 0.5;   // wander target
  let yoff = 0, vy = 0;     // vertical hide/pop spring (px)
  let hidePhase = 0;        // 0 = up, 1 = ducking, 2 = down, waiting
  let nextMove = 0, nextHide = 4000 + Math.random() * 5000, resurfaceAt = 0;

  // Per-eye pupil state: spring physics for goofy lag and overshoot.
  const eyes = [{ x: 0, y: 0, vx: 0, vy: 0, pr: 0.3 }, { x: 0, y: 0, vx: 0, vy: 0, pr: 0.3 }];

  let prev = performance.now();
  (function frame(now) {
    if (!c.isConnected) return;
    const dt = Math.min(now - prev, 100) / 16.7; prev = now;
    ctx.fillStyle = BG;
    ctx.fillRect(0, 0, c.width, c.height);
    const R = Math.min(c.height * 0.32, 70);

    // Wander: ease toward a spot, pick a new one every few seconds.
    if (now > nextMove && !hidePhase) {
      tx = 0.15 + Math.random() * 0.7;
      ty = 0.3 + Math.random() * 0.4;
      nextMove = now + 2500 + Math.random() * 3500;
    }
    gx += (tx - gx) * 0.02 * dt;
    gy += (ty - gy) * 0.02 * dt;

    // Duck down, wait hidden, then spring back (underdamped = pops past
    // the resting point and wobbles). Resurfaces at a new spot.
    if (hidePhase === 0 && now > nextHide) hidePhase = 1;
    if (hidePhase === 1 && yoff > c.height * 0.9) {
      hidePhase = 2;
      resurfaceAt = now + 500 + Math.random() * 900;
    }
    if (hidePhase === 2 && now > resurfaceAt) {
      hidePhase = 0;
      nextHide = now + 5000 + Math.random() * 7000;
      tx = 0.15 + Math.random() * 0.7;
      gx = tx;
      nextMove = now + 3000 + Math.random() * 3000;
    }
    const yTarget = hidePhase ? c.height : 0;
    vy += (yTarget - yoff) * 0.06 * dt;
    vy *= 0.85;
    yoff += vy * dt;

    const cy0 = gy * c.height + yoff;
    const cx0 = gx * c.width;
    const watching = now - lastMove < 4000;
    eyes.forEach((e, i) => {
      const cx = cx0 + (i ? 1.3 : -1.3) * R;
      // Pupil target: toward the cursor, or a slow idle drift.
      let ptx, pty;
      if (watching) {
        const dx = mx - cx, dy = my - cy0;
        const d = Math.hypot(dx, dy) || 1;
        const reach = R * 0.45 * Math.min(1, d / 200);
        ptx = (dx / d) * reach; pty = (dy / d) * reach;
      } else {
        ptx = Math.sin(now / 900 + i * 2) * R * 0.3;
        pty = Math.cos(now / 1300 + i * 3) * R * 0.2;
      }
      // Spring toward the target (underdamped: overshoots, wobbles).
      e.vx += (ptx - e.x) * 0.08 * dt; e.vy += (pty - e.y) * 0.08 * dt;
      e.vx *= 0.82; e.vy *= 0.82;
      e.x += e.vx * dt; e.y += e.vy * dt;
      // Pupils dilate when the cursor comes close to the eye.
      const near = Math.hypot(mx - cx, my - cy0) < R * 2.5;
      e.pr += ((near ? 0.42 : 0.3) - e.pr) * 0.1 * dt;
      // Sclera.
      ctx.fillStyle = "#fff";
      ctx.beginPath();
      ctx.ellipse(cx, cy0, R, R * 1.15, 0, 0, 7);
      ctx.fill();
      // Iris + pupil + glint, clipped to the sclera.
      ctx.save();
      ctx.beginPath();
      ctx.ellipse(cx, cy0, R, R * 1.15, 0, 0, 7);
      ctx.clip();
      ctx.fillStyle = "#7c5cff";
      ctx.beginPath();
      ctx.arc(cx + e.x, cy0 + e.y, R * 0.55, 0, 7);
      ctx.fill();
      ctx.fillStyle = "#1d1730";
      ctx.beginPath();
      ctx.arc(cx + e.x, cy0 + e.y, R * e.pr, 0, 7);
      ctx.fill();
      ctx.fillStyle = "#fff";
      ctx.beginPath();
      ctx.arc(cx + e.x - R * 0.15, cy0 + e.y - R * 0.18, R * 0.09, 0, 7);
      ctx.fill();
      ctx.restore();
      ctx.strokeStyle = "#2b2440";
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.ellipse(cx, cy0, R, R * 1.15, 0, 0, 7);
      ctx.stroke();
    });
    requestAnimationFrame(frame);
  })(prev);
})();
</script>
"""

BLOG_BANNER = '<div style="background: linear-gradient(100deg, #14243d, #3d2b6b 45%, #7c5cff 75%, #ff5c8a)"></div>'

FRONT_BANNER = '<img src="/waves.svg" alt="">'

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

#: path -> (title, markdown, {filename: bytes}, banner HTML, menu order).
#: Note there are deliberately no "docs" or "blog" landing pages: those
#: labels are created without content, so they render a placeholder page
#: and their nav links point at the first child (see views.first_leaf).
PAGES: dict[str, tuple[str, str, dict[str, bytes], str, float]] = {
    "": ("Welcome", WELCOME, {"waves.svg": WAVES_SVG.encode()}, FRONT_BANNER, 1),
    "about": ("About", ABOUT, {}, "", 2),
    "docs/editing": (
        "Writing Content",
        EDITING,
        {"shapes.svg": SHAPES_SVG.encode()},
        "",
        1,
    ),
    "blog/the-long-read": (
        "The Long Read",
        LONG_READ,
        {"dunes.svg": DUNES_SVG.encode()},
        BLOG_BANNER,
        1,
    ),
    "blog/notes-on-urls": ("Notes on URLs", NOTES_ON_URLS, {}, EYES_BANNER, 2),
    "blog/canvas-nights": ("Canvas Nights", CANVAS_NIGHTS, {}, CANVAS_BANNER, 3),
    "blog/small-releases": ("Small Releases", SMALL_RELEASES, {}, "", 4),
}

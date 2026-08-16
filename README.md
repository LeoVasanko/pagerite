# Pagerite

A single-user CMS/blog. FastAPI serves HTML rendered in Python with
html5tagger, content is persisted in a kanta database and rendered on
the fly per request. Vue is used only for interactive bits (the editing
tools), not for the public pages.

## Running

```sh
uv run pagerite          # serves the built frontend
uv run scripts/devserver.py   # dev mode with auto reloads (no build needed)
```

The database lives in `pagerite.kantadb` in the working directory
(`PAGERITE_DB` overrides). On startup, demo pages from `pagerite/seed.py`
are added only if missing.

## Editing

Click the 🖊️ pen on any page: the article pen opens the page editor
(Markdown with live server-rendered preview over a WebSocket), the banner
pen opens the site editor (site brand, banner HTML, and the page
structure tree). Everything saves immediately — no save buttons.

See `docs/design-principles.md` for the design and `AGENTS.md` for the
development conventions.

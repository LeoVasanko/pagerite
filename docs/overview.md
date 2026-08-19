# Pagerite overview

Pagerite is a single-user CMS/blog. FastAPI serves HTML rendered in Python with html5tagger; content is persisted in a kanta database and rendered on the fly per request. Vue is used only for interactive bits (editing tools), not for the public pages.

See `docs/design-principles.md` for the high-level design and the other `docs/*.md` files for implementation details.

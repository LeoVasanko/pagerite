#!/usr/bin/env -S uv run
"""Import a human-made whole-article translation into the fragment store.

A full translation produced outside the pipeline (e.g. by ChatGPT, pasted
into a file) is decomposed with the same alignment and validation as an
article-mode LLM result (pagerite.translate.align_article): blocks are
stored as proper Data.trans fragments, so later source edits invalidate
and re-translate per chunk instead of letting one monolithic user patch
silently go stale hunk by hunk.

Run with the Pagerite server STOPPED (the script opens the same kanta
database). Blocks that fail validation stay untranslated — the translator
service picks them up as scoped jobs on the next run.

Usage:
    scripts/import_translation.py PATH LANG FILE.md [--db DB]

Run from the repository root (the script runs in the project environment).

PATH is the page path without leading slash ("" = front page), LANG the
target language base tag (e.g. fi), FILE.md the translated Markdown.
"""

import argparse
import asyncio
import sys
from pathlib import Path

from kanta import Kanta

from pagerite.data import Data, node_markdown, resolve
from pagerite.i18n import base_tag, primary_lang
from pagerite.translate import TransResult, align_article, store_results


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("path", help="page path without leading slash ('' = front page)")
    p.add_argument("lang", help="target language base tag (e.g. fi)")
    p.add_argument("file", help="Markdown file holding the translation")
    p.add_argument(
        "--db",
        default="localhost/content.kantadb",
        help="kanta database (default: localhost/content.kantadb)",
    )
    args = p.parse_args()
    translated = Path(args.file).read_text()
    asyncio.run(import_translation(args, translated))


async def import_translation(args: argparse.Namespace, translated: str) -> None:
    path = args.path.strip("/")
    lang = base_tag(args.lang)

    data = Data()
    kanta = Kanta(args.db, data, migrations="pagerite.migrations")
    await kanta.open(create=False, log=False)
    try:
        chain = resolve(data.menu, path)
        node = chain[-1] if chain else None
        if node is None or node.chunks is None:
            sys.exit(f"no such page: {args.path!r}")
        if primary_lang(data.menu, path) == lang:
            sys.exit(f"{args.path!r} is already in {lang} (its primary language)")
        pairs = align_article(node_markdown(data, node) or "", translated)
        if pairs is None:
            sys.exit(
                "rejected: an anchor block (code fence, raw HTML, container fence) "
                "is missing or altered — the translation does not preserve the "
                "page structure"
            )
        if not pairs:
            sys.exit("nothing to import: no blocks aligned")
        with kanta.transaction(f"translate:{lang}:import", user="import"):
            pages = store_results(
                data, lang, [TransResult(key=k, text=t) for k, t in pairs]
            )
        print(f"imported {len(pairs)} blocks for [{lang}]; pages: {', '.join(pages)}")
        print("untranslated blocks stay pending for the translator service")
    finally:
        await kanta.close()


if __name__ == "__main__":
    main()

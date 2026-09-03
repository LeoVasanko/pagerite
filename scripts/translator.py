#!/usr/bin/env python3
# /// script
# requires-python = ">=3.14"
# dependencies = [
#     "accelerate>=1.14.0",
#     "msgspec>=0.19.0",
#     "torch>=2.13.0",
#     "tracerite>=2.6.5",
#     "transformers>=5.16.1",
#     "websockets>=15.0.1",
# ]
# ///
"""Pagerite translator service: translate site content with Seed-X-PPO-7B.

Connects to a Pagerite server's translator WebSocket — the full URL
including the access key (printed at server startup; the admin also finds
the key in the site settings, GET /_api/settings -> ``translate_keys``) —
and announces the languages the
model CAN translate (capabilities). The server dispatches one single-item
job at a time per connection, offered only in its configured target
languages (``Data.translate_langs``) ∩ the announced capabilities; a
dropped connection's in-flight item is simply re-offered
(docs/localization.md). For parallelism, run multiple instances.

Seed-X-PPO-7B (bf16, ~15 GB) is the only supported model. The script stays
running and connected full time; the model loads at startup (a backlog is
likely after a downtime) and is unloaded after 60 s idle, re-loading on the
next job — the GPU is held only while actually translating.

Usage:
    uv run scripts/translator.py ws://localhost:8410/_translate/KEY
    uv run scripts/translator.py wss://example.com/_translate/KEY
"""

import argparse
import asyncio
import gc
import sys
import time

import msgspec
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
import tracerite
import websockets

tracerite.load()

SEED_X = "ByteDance-Seed/Seed-X-PPO-7B"

# Seed-X language tags (appended to the prompt; required by its PPO training)
SEED_X_TAGS = {
    "arabic": "ar",
    "chinese": "zh",
    "czech": "cs",
    "danish": "da",
    "dutch": "nl",
    "english": "en",
    "finnish": "fi",
    "french": "fr",
    "german": "de",
    "greek": "el",
    "hungarian": "hu",
    "indonesian": "id",
    "italian": "it",
    "japanese": "ja",
    "korean": "ko",
    "malay": "ms",
    "norwegian": "no",
    "persian": "fa",
    "polish": "pl",
    "portuguese": "pt",
    "romanian": "ro",
    "russian": "ru",
    "spanish": "es",
    "swedish": "sv",
    "thai": "th",
    "turkish": "tr",
    "ukrainian": "uk",
    "vietnamese": "vi",
}
SEED_X_NAMES = {v: k for k, v in SEED_X_TAGS.items()}

#: The fragments arrive as prose segments (pagerite/segments.py): plain
#: text runs only — no markup, URLs, code or placeholders. The wire
#: invariant is that segments are PURE PROSE, and one character marks the
#: boundary both ways: "<" never appears in a segment. Sources containing
#: it are never dispatched (pagerite/segments.py keeps them in the
#: original language); the model's output is cut at the first "<" — one
#: rule that covers the whole class of markup bleed (an echoed <lang> tag,
#: a "<br>", ...) instead of a pattern per artifact. (Generation-level
#: stop strings can't do this job: the model's <s> framing token would
#: trip a "<" stop at the first token; skip_special_tokens strips the
#: framing at decode.)
#:
#: Two kinds cross the wire (Job.kind), each with its own prompt template:
#: titles get told they ARE titles (a lone word otherwise invites
#: context-free readings — "About" as "approximately"). Any segment may
#: carry its surround in Job.contexts (a title: the article's opening; a
#: carved-out segment like a link text: its block's plain text) and is then
#: translated together with that surround (seed_x_chunk). No punctuation
#: clause, on purpose: Seed-X handles trailing-punctuation instructions by
#: slipping into its [COT] reasoning mode (observed for Chinese:
#: minutes-long generations, reasoning text in the output) —
#: match_punctuation handles stray punctuation deterministically instead.
PROMPTS = {
    "chunk": "Translate the following {source_lang} text into {target_lang}:\n{text} <{tag}>",
    "title": "Translate the following {source_lang} title into {target_lang}:\n{text} <{tag}>",
    # Title with the article's opening as context (Job.contexts): the model
    # translates both; generation stops at the blank line separating them,
    # and the segment's own part of the output is the translation. No
    # separator in the output (the model merged them) → seed_x_chunk falls
    # back to the plain kind template.
    "title+context": "Translate the following {source_lang} title and the beginning of its article "
    "into {target_lang}:\n{text}\n\n{context} <{tag}>",
    # A segment carved out of a larger block (link text, partial run) with
    # its sentence as context — same mechanics as title+context.
    "chunk+context": "Translate the following {source_lang} text into {target_lang}:\n"
    "{text}\n\n{context} <{tag}>",
}
TERMINAL_PUNCT = ".,!?:;…。，！？；：、"


def match_punctuation(source: str, translated: str) -> str:
    """Drop terminal punctuation the model added.

    When the source segment ends without terminal punctuation, the
    translation must not gain any either. A leading Spanish ¡/¿ only pairs
    with a terminal !/?, so it goes with it.
    """
    if not source or source[-1] in TERMINAL_PUNCT:
        return translated
    trimmed = translated.rstrip(TERMINAL_PUNCT)
    if trimmed and trimmed[0] in "¡¿":
        trimmed = trimmed[1:].lstrip()
    return trimmed


# The wire structs below duplicate pagerite/translate.py: this script runs
# in its own uv environment and cannot import the server package. The
# "type" tag selects the frame; bytes fields ride as base64.
class Hello(msgspec.Struct, tag="hello"):
    """Client greeting on connect: the language codes its model CAN produce
    (capabilities). The server offers jobs only in the intersection with
    its wanted target languages."""

    langs: list[str]


class Job(msgspec.Struct, tag="job"):
    """Server push: ONE fragment to translate. Exactly one job is in flight
    per connection — the next arrives only after this one's Result."""

    lang: str
    key: bytes  #: 9-byte chunk hash (base64 in the JSON frame)
    #: The fragment's prose segments: plain text runs only, no markup —
    #: translate each element independently (pagerite/segments.py).
    texts: list[str]
    path: str  #: article it came from ("" = front page), no leading slash
    kind: str  #: "chunk" | "title"
    #: Per segment (parallel to texts; "" = none): the surround to
    #: translate it in — a link text carries its sentence, a title the
    #: article's opening. See seed_x_chunk for how they are used.
    contexts: list[str] = msgspec.field(default_factory=list)


class Result(msgspec.Struct, tag="result"):
    """Client reply: the translation of the connection's current Job
    (must match its lang and key exactly)."""

    lang: str
    key: bytes
    texts: list[str]  #: the job's segments translated, same order and count


#: Idle seconds after the last job before the model is unloaded (the GPU
#: is released; the WebSocket connection and tokenizer stay).
IDLE_UNLOAD_S = 60


class SeedX:
    """The Seed-X model, loaded at startup and re-loaded on demand.

    Loading up front covers the likely backlog after a downtime (and any
    first-run model download) before the server starts dispatching. After
    IDLE_UNLOAD_S without a job the model is dropped and re-loaded on the
    next one — the script stays connected the whole time, holding the GPU
    only while translating. The tokenizer (small, CPU) loads once.
    """

    def __init__(self):
        self.tokenizer = AutoTokenizer.from_pretrained(SEED_X)
        self.model = None
        self._unload_task = None
        self._load()

    def _load(self):
        t0 = time.monotonic()
        self.model = AutoModelForCausalLM.from_pretrained(
            SEED_X, dtype=torch.bfloat16, device_map="auto"
        )
        print(f"[seed-x loaded in {time.monotonic() - t0:.0f}s]", file=sys.stderr)

    def get(self):
        """The (tokenizer, model) pair, re-loading the model if it was
        idle-unloaded, and cancelling any pending idle unload."""
        if self._unload_task:
            self._unload_task.cancel()
            self._unload_task = None
        if self.model is None:
            self._load()
        return self.tokenizer, self.model

    def idle(self):
        """Re-arm the idle unload after a job completes (arming it at job
        START could unload under a >IDLE_UNLOAD_S generation)."""
        self._unload_task = asyncio.create_task(self._unload_later())

    async def _unload_later(self):
        try:
            await asyncio.sleep(IDLE_UNLOAD_S)
        except asyncio.CancelledError:
            return
        if self.model is not None:
            self.model = None
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            print(f"[seed-x unloaded after {IDLE_UNLOAD_S}s idle]", file=sys.stderr)


def seed_x_chunk(
    tokenizer,
    model,
    text: str,
    target_lang: str,
    tag: str,
    kind: str = "chunk",
    context: str = "",
    source_lang: str = "English",
):
    """Translate one segment; returns (translation, output_tokens, generation_seconds).

    With context, the segment is translated together with its surround (a
    link text with its sentence, a title with the article's opening), and
    the segment's own part of the output is the translation: its own line
    for a single-line source (a single-line segment's translation never
    contains a line break — generation stops at the blank line separating
    the two), its own paragraph for a multi-line one (softbreak-merged
    lines keep single newlines, the separator is the blank line). If the
    model merged them — no separator, or an empty first part — fall back to
    translating the segment alone; the wasted tokens are counted either
    way.
    """
    # No chat template on this model; the trailing language tag is required (trans/ style prompt).
    template = PROMPTS.get(f"{kind}+context" if context else kind, PROMPTS["chunk"])
    prompt = template.format(
        source_lang=source_lang,
        target_lang=target_lang,
        text=text,
        tag=tag,
        context=context,
    )
    inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
    t0 = time.monotonic()
    # The only stop string is the context separator. "<" must NOT be one:
    # stopping works on the raw output, which always starts with the
    # model's <s> framing token. skip_special_tokens strips <s>/</s> at
    # decode; the post-decode cut at the first "<" then enforces the wire
    # invariant (prose only) against markup bleed.
    kwargs = {"stop_strings": ["\n\n"], "tokenizer": tokenizer} if context else {}
    out = model.generate(
        **inputs,
        max_new_tokens=max(1024, 2 * inputs.input_ids.shape[1]),
        do_sample=False,
        **kwargs,
    )
    dt = time.monotonic() - t0
    n = out.shape[1] - inputs.input_ids.shape[1]
    decoded = tokenizer.decode(
        out[0][inputs.input_ids.shape[1] :], skip_special_tokens=True
    )
    translated = decoded.partition("<")[0]
    if not context:
        return translated.strip(), n, dt
    if "\n" in text:
        # Multi-line segment: its translation keeps single newlines; the
        # blank line is the separator from the context translation.
        sep = "\n\n" in translated
        out = translated.split("\n\n", 1)[0] if sep else ""
    else:
        out, sep, _ = translated.partition("\n")
        if not sep:
            out = ""
    out = out.strip()
    if out:
        return out, n, dt
    # The model merged segment and context (no separator, or an empty first
    # part): retry without the context.
    again, n2, dt2 = seed_x_chunk(
        tokenizer, model, text, target_lang, tag, kind=kind, source_lang=source_lang
    )
    return again, n + n2, dt + dt2


async def do_job(ws, job: Job, seed_x: SeedX) -> None:
    """Translate the job's segments (one model call each) and send them back."""
    lang_name = SEED_X_NAMES[job.lang].capitalize()
    tokenizer, model = seed_x.get()
    # Deliberately blocking: nothing else needs the loop while the job is
    # being answered, and the reconnect loop recovers a dropped connection
    # (the in-flight item is simply re-offered).
    texts = []
    tokens = dt = 0
    for i, text in enumerate(job.texts):
        ctx = job.contexts[i] if i < len(job.contexts) else ""
        translated, n, t = seed_x_chunk(
            tokenizer, model, text, lang_name, job.lang, kind=job.kind, context=ctx
        )
        texts.append(match_punctuation(text, translated))
        tokens += n
        dt += t
    print(
        f"[{job.lang} {job.kind} {job.path or '/'}: {len(texts)} segments, "
        f"{tokens} tokens in {dt:.1f}s = {tokens / dt:.1f} tok/s]",
        file=sys.stderr,
    )
    await ws.send(
        msgspec.json.encode(Result(lang=job.lang, key=job.key, texts=texts)).decode()
    )
    seed_x.idle()


async def serve(url: str, seed_x: SeedX) -> None:
    """Connect, announce capabilities, answer jobs; reconnect with backoff."""
    seed_x.idle()  # the startup load also unloads when no work arrives
    backoff = 1
    while True:
        try:
            async with websockets.connect(url) as ws:
                backoff = 1
                await ws.send(
                    msgspec.json.encode(Hello(langs=sorted(SEED_X_NAMES))).decode()
                )
                print(
                    f"[connected; announced {len(SEED_X_NAMES)} language capabilities]",
                    file=sys.stderr,
                )
                async for raw in ws:
                    await do_job(ws, msgspec.json.decode(raw, type=Job), seed_x)
        except websockets.exceptions.InvalidHandshake:
            sys.exit("handshake rejected; check the URL (including the key)")
        except (OSError, websockets.exceptions.ConnectionClosed) as e:
            print(
                f"[connection lost ({e}); reconnecting in {backoff}s]", file=sys.stderr
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "url",
        help="full translator WebSocket URL including the key, "
        "e.g. ws://localhost:8410/_translate/KEY",
    )
    args = p.parse_args()
    if not args.url.startswith(("ws://", "wss://")):
        p.error("url must start with ws:// or wss://")

    asyncio.run(serve(args.url, SeedX()))


if __name__ == "__main__":
    main()

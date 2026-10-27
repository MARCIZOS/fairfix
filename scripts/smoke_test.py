"""Run this first: proves every external call works before you build on it.

  python scripts/smoke_test.py [path/to/bill_photo.jpg]

1. Lists the models your Token Factory key can see and checks the configured IDs.
2. One text call to MODEL_TEXT.
3. One image call to MODEL_VISION (uses your photo if given, else a generated test image).
4. One Tavily search.
Prints model, latency and tokens for each; never prints your keys.
"""

from __future__ import annotations

import base64
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402

def make_png(w: int = 64, h: int = 32) -> bytes:
    """A small valid PNG (red/blue halves), enough to prove the image path works."""
    import struct
    import zlib

    rows = b"".join(b"\x00" + b"".join(b"\xd0\x30\x30" if x < w // 2 else b"\x30\x50\xd0" for x in range(w))
                    for _ in range(h))

    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


TEST_PNG = make_png()


def step(name):
    print(f"\n== {name}")


def main() -> int:
    ok = True
    if not settings.nebius_api_key:
        print("NEBIUS_API_KEY is not set. Copy .env.example to .env and fill it in.")
        return 2
    from app import llm

    step("1. Models visible to this key")
    try:
        ids = llm.list_models()
        print(f"{len(ids)} models. NVIDIA ones:")
        for m in sorted(i for i in ids if "nvidia" in i.lower() or "nemotron" in i.lower()):
            print("  ", m)
        for label, mid in (("MODEL_VISION", settings.model_vision), ("MODEL_TEXT", settings.model_text),
                           ("MODEL_EXPLAIN", settings.explain_model)):
            found = mid in ids
            ok &= found
            print(f"{label} = {mid}: {'FOUND' if found else 'NOT FOUND - copy the exact id from the list above into .env'}")
    except Exception as exc:
        print("FAILED:", exc)
        return 1

    step("2. Text call")
    t0 = time.perf_counter()
    try:
        out = llm.call_text("smoke_text", settings.model_text,
                            [{"role": "user", "content": "Reply with exactly: FAIRFIX OK"}], max_tokens=50)
        print(f"{(time.perf_counter() - t0):.1f}s -> {out[:80]!r}")
    except Exception as exc:
        ok = False
        print("FAILED:", exc)

    step("3. Image call")
    if len(sys.argv) > 1:
        p = Path(sys.argv[1])
        data, mime = p.read_bytes(), "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
    else:
        data, mime = TEST_PNG, "image/png"
    t0 = time.perf_counter()
    try:
        if len(sys.argv) > 1:
            from app.steps.extract import extract_document
            doc = extract_document([(data, mime)])
            print(f"{(time.perf_counter() - t0):.1f}s -> {len(doc.lines)} lines, total {doc.grand_total_inr.value}")
            for ln in doc.lines[:10]:
                print(f"   {ln.raw_text.value!r}: {ln.line_total_inr.value}")
        else:
            out = llm.call_text("smoke_image", settings.model_vision,
                                [{"role": "user", "content": [llm.image_part(data, mime),
                                                              {"type": "text", "text": "Describe this image in five words."}]}],
                                max_tokens=60)
            print(f"{(time.perf_counter() - t0):.1f}s -> {out[:80]!r}")
    except Exception as exc:
        ok = False
        print("FAILED:", exc)

    step("4. Tavily search")
    if not settings.tavily_api_key:
        print("TAVILY_API_KEY not set (price checks will show Unable to verify).")
    else:
        from app.steps.prices import tavily_search
        t0 = time.perf_counter()
        try:
            res = tavily_search("Honda Activa air filter genuine price")
            print(f"{(time.perf_counter() - t0):.1f}s -> {len(res)} results")
            for r in res[:5]:
                print("  ", r.get("url"), "| raw content:", len(r.get("raw_content") or ""), "chars")
        except Exception as exc:
            ok = False
            print("FAILED:", exc)

    print("\nCall log:", settings.call_log)
    print("ALL OK" if ok else "SOME CHECKS FAILED - see above")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

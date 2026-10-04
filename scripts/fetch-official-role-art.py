#!/usr/bin/env python3
"""Fetch the official NetEase role portraits into the frontend asset folder.

Why a Python script and not the ``.mjs`` the plan first sketched: the source
files are 423x554 RGBA **PNGs** at ~228 KB each, and neither Node nor this
machine ships a WebP encoder. Pillow does (it binds the system ``libwebp``), so
the download and the transcode live in one place. Run it with a Python that has
Pillow available — the repo venv does not need it:

    pip install --target=/tmp/imgtool Pillow
    PYTHONPATH=/tmp/imgtool python3 scripts/fetch-official-role-art.py

The images are **not committed** (``.gitignore`` keeps ``role-*.webp`` out):
they are NetEase's artwork, and they are placeholders until hand-made art
replaces them. ``card-back.webp`` stays tracked — the fallback has to exist for
a fresh clone to render anything at all.

Re-running is safe and cheap: unchanged files are skipped unless ``--force``.
"""

from __future__ import annotations

import argparse
import io
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT_DIR = REPO / "frontend" / "src" / "assets" / "roles"

#: Where the portraits live. The ``{pic}`` keys come from the official card
#: data already archived at ``docs/reference/netease-kapai-2026-10-05.js``.
BASE = "https://langrensha.res.netease.com/pc/gw/20190509150909/data/kapai_peo"

#: Role id -> official ``pic`` key. Kept here rather than in the frontend
#: because the frontend only needs "role id -> file basename", and the file
#: basename is derivable from the role id (``wolf-killer-seer`` -> ``role-seer``).
#: Roles whose official art does not exist yet are simply absent — the UI falls
#: back to the card back, which is the designed behaviour, not an error.
ROLE_PIC: dict[str, str] = {
    # --- implemented ---
    "wolf-killer-werewolf": "langr",
    "wolf-killer-villager": "pm",
    "wolf-killer-seer": "yyj",
    "wolf-killer-witch": "nw",
    "wolf-killer-hunter": "lr",
    "wolf-killer-guard": "sw",
    "wolf-killer-idiot": "bc",
    "wolf-killer-werewolf-king": "blw",
    "wolf-killer-knight": "qs",
    "wolf-killer-wolf-beauty": "lmr",
    "wolf-killer-wolf-king": "lw",
    "wolf-killer-evil-knight": "elqs",
    "wolf-killer-dream-weaver": "smr",
    "wolf-killer-gargoyle": "sxg",
    "wolf-killer-gravekeeper": "shoumr",
    # --- planned, stage 3 and later ---
    "wolf-killer-pure-white": "cbzn",
    "wolf-killer-wolf-witch": "langwu",
    "wolf-killer-demon-hunter": "liemr",
    "wolf-killer-crimson-envoy": "xyst",
    "wolf-killer-nightmare": "emzy",
    "wolf-killer-time-wolf": "fslf",
    "wolf-killer-order-prince": "dlwz",
    "wolf-killer-miracle-merchant": "qjsr",
    "wolf-killer-lonely-girl": "alz",
    "wolf-killer-mirror-girl": "mjsn",
    "wolf-killer-awakened-hidden-wolf": "jxyl",
    "wolf-killer-awakened-wolf-king": "jxlw",
    "wolf-killer-eclipse-maid": "sssn",
    "wolf-killer-radiance-earl": "lgbj",
}

#: Official cards we have no role id for yet. Their art is downloaded under a
#: provisional slug so a future stage only has to rename the file; delete a line
#: here if the id turns out different.
PROVISIONAL_PIC: dict[str, str] = {
    "wolf-killer-hidden-wolf": "yl",
    "wolf-killer-crow": "wy",
    "wolf-killer-cupid": "qbt",
    "wolf-killer-thousand-face": "dz",
    "wolf-killer-magician": "mss",
    "wolf-killer-alpaca": "yt",
    "wolf-killer-curse-fox": "zhouhu",
    "wolf-killer-bear": "bear",
    "wolf-killer-kitsune": "zh",
    "wolf-killer-pufferfish": "ht",
    "wolf-killer-white-cat": "cat",
    "wolf-killer-alchemist-witch": "ljmnv",
    "wolf-killer-wolf-claw": "lyzz",
    "wolf-killer-day-scholar": "bzxz",
    "wolf-killer-night-mentor": "jyds",
}

WEBP_QUALITY = 82
WEBP_METHOD = 6

#: The official portraits are **not** uniform: canvases run from 387x554 to
#: 487x554 and the character's alpha bounding box covers anywhere from 40% to
#: 99% of the width. One ``object-fit`` cannot frame that set consistently, so
#: the transcode normalises them: trim to the cut-out, scale to fit 92% of a
#: fixed 3:4 canvas, centre. Every file then shares the aspect ratio the card
#: back already uses, and the UI needs exactly one framing rule.
#:
#: Opaque full-bleed images (the card back, the Old Drunkard — neither is a
#: NetEase cut-out) are left untouched: trimming them by alpha would frame the
#: whole image and leave a transparent border.
CANVAS_W, CANVAS_H = 423, 564
CONTENT_RATIO = 0.92


def slug_of(role_id: str) -> str:
    """``wolf-killer-werewolf-king`` -> ``werewolf-king``."""
    prefix = "wolf-killer-"
    return role_id[len(prefix):] if role_id.startswith(prefix) else role_id


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        return response.read()


def normalise(image):
    """Frame a transparent cut-out onto the shared 3:4 canvas, or pass it through.

    Returns the image unchanged when it is fully opaque — those are not cut-outs
    and trimming by alpha would frame the entire bitmap.
    """
    from PIL import Image

    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A")
    low, _high = alpha.getextrema()
    if low == 255:
        return rgba

    bbox = alpha.getbbox()
    if bbox is None:
        return rgba
    content = rgba.crop(bbox)
    width, height = content.size
    scale = min(CANVAS_W * CONTENT_RATIO / width, CANVAS_H * CONTENT_RATIO / height)
    resized = content.resize(
        (max(1, round(width * scale)), max(1, round(height * scale))),
        Image.LANCZOS,
    )
    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 0))
    canvas.paste(resized, ((CANVAS_W - resized.width) // 2, (CANVAS_H - resized.height) // 2))
    return canvas


def to_webp(png: bytes) -> bytes:
    from PIL import Image  # imported lazily so --help works without Pillow

    with Image.open(io.BytesIO(png)) as image:
        buffer = io.BytesIO()
        normalise(image).save(
            buffer, "WEBP", quality=WEBP_QUALITY, method=WEBP_METHOD,
        )
        return buffer.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="re-download everything")
    parser.add_argument(
        "--include-provisional", action="store_true",
        help="also fetch art for roles that have no role id yet",
    )
    args = parser.parse_args()

    try:
        import PIL  # noqa: F401
    except ImportError:
        print(
            "Pillow is required for the PNG -> WebP step.\n"
            "    pip install --target=/tmp/imgtool Pillow\n"
            "    PYTHONPATH=/tmp/imgtool python3 scripts/fetch-official-role-art.py",
            file=sys.stderr,
        )
        return 2

    table = dict(ROLE_PIC)
    if args.include_provisional:
        table.update(PROVISIONAL_PIC)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    written = skipped = failed = 0

    for role_id, pic in sorted(table.items()):
        target = OUT_DIR / f"role-{slug_of(role_id)}.webp"
        if target.exists() and not args.force:
            skipped += 1
            continue
        url = f"{BASE}/{pic}.png"
        try:
            png = fetch(url)
            target.write_bytes(to_webp(png))
        except (urllib.error.URLError, OSError) as error:
            # A missing portrait is not fatal: the UI shows the card back.
            print(f"  !! {role_id}: {error}", file=sys.stderr)
            failed += 1
            continue
        print(f"  {role_id:42} {len(png):>7} -> {target.stat().st_size:>6} bytes")
        written += 1

    print(f"\nwritten {written}, skipped {skipped}, failed {failed}")
    print(f"-> {OUT_DIR}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""V4.1 — User asset library: ingest, auto-understanding, catalog.

Users drop their own media (b-roll, logos, stickers, music, SFX, fonts, LUTs)
and the system understands it well enough for an LLM to PROPOSE placements.

Ingest per file (Premiere "media intelligence" pattern, local-first):
  sha256 (exact dedupe) -> kind routing by extension -> copy into library ->
  tech metadata (ffprobe / Pillow, has_alpha, aspect bucket) -> thumbnails ->
  dominant colors (Pillow adaptive palette) -> loudness for audio (ebur128) ->
  ONE gpt-4o-mini vision call (frames at detail:low) -> catalog row (JSON).

The catalog is small enough (~100 tokens/asset) to hand the LLM whole — no
vector DB. Proposals must reference assets ONLY by id; code validates ids.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

from pipeline import config
from pipeline.media import ffprobe_info

ASSETS_DIR = config.ROOT / "assets" / "user"
CATALOG_PATH = ASSETS_DIR / "catalog.json"
THUMBS_DIR = config.CACHE_DIR / "asset_thumbs"
ANALYSIS_VERSION = 1

KIND_BY_EXT = {
    ".mp4": "video", ".mov": "video", ".webm": "video", ".mkv": "video",
    ".avi": "video",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image",
    ".gif": "image",
    ".mp3": "audio", ".wav": "audio", ".m4a": "audio", ".aac": "audio",
    ".ogg": "audio", ".flac": "audio",
    ".ttf": "font", ".otf": "font",
    ".cube": "lut",
}

ALPHA_PIXFMTS = {"yuva420p", "yuva422p", "yuva444p", "rgba", "bgra", "argb",
                 "abgr", "ya8", "ya16le", "pal8"}


# ------------------------------------------------------------------ catalog
def load_catalog() -> list[dict]:
    if CATALOG_PATH.exists():
        return json.loads(CATALOG_PATH.read_text())
    return []


def save_catalog(rows: list[dict]) -> None:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    CATALOG_PATH.write_text(json.dumps(rows, ensure_ascii=False, indent=1))


def get_asset(asset_id: str) -> dict | None:
    return next((r for r in load_catalog() if r["id"] == asset_id), None)


def set_folder(asset_id: str, folder: str) -> bool:
    """Assign an asset to a (virtual) folder/collection. Folders are labels on
    the catalog row — the file on disk never moves — so this is cheap and fully
    reversible. Returns True if the asset existed. Pass "" to un-file it."""
    rows = load_catalog()
    hit = False
    for r in rows:
        if r["id"] == asset_id:
            r["folder"] = (folder or "").strip()
            hit = True
            break
    if hit:
        save_catalog(rows)
    return hit


def list_folders() -> list[str]:
    """Distinct, sorted folder labels currently in use across the catalog."""
    seen = {(r.get("folder") or "").strip() for r in load_catalog()}
    seen.discard("")
    return sorted(seen)


def catalog_for_llm(kinds: tuple[str, ...] | None = None) -> list[dict]:
    """Compact catalog rows for prompts (~100 tokens each)."""
    out = []
    for r in load_catalog():
        if kinds and r["kind"] not in kinds:
            continue
        row = {"id": r["id"], "kind": r["kind"],
               "description": r.get("description", ""),
               "tags": r.get("tags", [])}
        t = r.get("tech", {})
        if t.get("duration_s"):
            row["duration_s"] = t["duration_s"]
        if t.get("has_alpha"):
            row["has_alpha"] = True
        if r.get("mood"):
            row["mood"] = r["mood"]
        if r.get("text_content"):
            row["text"] = r["text_content"]
        a = r.get("audio") or {}
        if a.get("audio_class"):
            row["audio_class"] = a["audio_class"]
        out.append(row)
    return out


# ------------------------------------------------------------------ helpers
def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _aspect_bucket(w: int, h: int) -> str:
    if not w or not h:
        return "other"
    r = w / h
    if r > 1.5:
        return "16:9"
    if r < 0.7:
        return "9:16"
    if 0.9 < r < 1.1:
        return "1:1"
    return "other"


def _dominant_colors(img, k: int = 4) -> list[str]:
    """Top palette colors via Pillow adaptive quantization."""
    small = img.convert("RGB").resize((64, 64))
    q = small.quantize(colors=k)
    palette = q.getpalette()[:k * 3]
    counts = sorted(q.getcolors() or [], reverse=True)
    out = []
    for _, idx in counts[:k]:
        r, g, b = palette[idx * 3:idx * 3 + 3]
        out.append(f"#{r:02x}{g:02x}{b:02x}")
    return out


def _loudness_lufs(path: Path) -> float | None:
    """Integrated LUFS via ffmpeg ebur128 (no extra deps)."""
    try:
        p = subprocess.run(
            ["ffmpeg", "-hide_banner", "-i", str(path),
             "-map", "a:0", "-filter:a", "ebur128", "-f", "null", "-"],
            capture_output=True, text=True, timeout=120)
        m = re.findall(r"I:\s*(-?[\d.]+)\s*LUFS", p.stderr)
        return float(m[-1]) if m else None
    except Exception:
        return None


def _video_frames_b64(path: Path, count: int, thumb: Path) -> list[str]:
    """Sample frames as 512px JPEGs; first one also saved as the thumbnail."""
    info = ffprobe_info(str(path))
    dur = max(0.5, info["duration"])
    n = max(1, min(count, int(dur // 8) + 1))
    out = []
    for i in range(n):
        t = dur * (i + 0.5) / n
        fp = THUMBS_DIR / f"_frame_{path.stem}_{i}.jpg"
        subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", str(path),
             "-frames:v", "1", "-vf", "scale=512:-2", str(fp)],
            capture_output=True, timeout=60)
        if fp.exists():
            out.append(base64.b64encode(fp.read_bytes()).decode())
            if i == 0:
                shutil.copy(fp, thumb)
            fp.unlink(missing_ok=True)
    return out


def _image_b64(path: Path, thumb: Path) -> str:
    from PIL import Image
    img = Image.open(path)
    img.thumbnail((512, 512))
    rgb = img.convert("RGB")
    rgb.save(thumb, "JPEG", quality=85)
    return base64.b64encode(thumb.read_bytes()).decode()


def _llm_describe(images_b64: list[str], hint: str) -> dict:
    """One gpt-4o-mini vision call -> {description, tags, mood, kind_guess,
    text_content, usage_hints}. detail:low keeps it ~$0.0005-0.004/asset."""
    api_key, base_url, model = config.llm_settings()
    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url=base_url) if base_url \
        else OpenAI(api_key=api_key)

    system = (
        "You catalog a video editor's asset library. Describe the asset for "
        "later automated placement in edits. Be SPECIFIC like a pro logger "
        "('drone wide shot of coastal highway at golden hour'), not generic. "
        "Return ONLY JSON: {\"description\": \"1-2 sentences\", "
        "\"tags\": [5-10 short tags], \"mood\": [1-3 words], "
        "\"kind_guess\": \"broll|logo|sticker|overlay|photo|other\", "
        "\"text_content\": \"any readable text or ''\", "
        "\"usage_hints\": [1-3 short placement ideas]}")
    content: list[dict] = [{"type": "text", "text": hint}]
    for b in images_b64:
        content.append({"type": "image_url", "image_url": {
            "url": f"data:image/jpeg;base64,{b}", "detail": "low"}})
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": content}],
        temperature=0.2, **config.json_response_format(base_url))
    return config.extract_json(resp.choices[0].message.content)


def _llm_describe_text(hint: str) -> dict:
    """Text-only fallback (audio/font/lut) — same output schema."""
    api_key, base_url, model = config.llm_settings()
    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url=base_url) if base_url \
        else OpenAI(api_key=api_key)
    system = (
        "You catalog a video editor's asset library from metadata alone. "
        "Return ONLY JSON: {\"description\": \"1 sentence\", "
        "\"tags\": [3-8 tags], \"mood\": [1-3 words], "
        "\"kind_guess\": \"music|sfx|voiceover|font|lut|other\", "
        "\"text_content\": \"\", \"usage_hints\": [1-2 placement ideas]}")
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": hint}],
        temperature=0.2, **config.json_response_format(base_url))
    return config.extract_json(resp.choices[0].message.content)


# ------------------------------------------------------------------ ingest
def ingest_file(src_path: str, original_name: str = "") -> dict:
    """Ingest one file into the library. Returns the catalog row.

    Raises ValueError for unsupported types; returns the existing row for
    exact duplicates.
    """
    src = Path(src_path)
    if not src.exists():
        raise ValueError(f"File not found: {src}")
    ext = src.suffix.lower()
    kind = KIND_BY_EXT.get(ext)
    if not kind:
        raise ValueError(f"Unsupported asset type '{ext}'. "
                         f"Supported: {sorted(set(KIND_BY_EXT))}")

    sha = _sha256(src)
    rows = load_catalog()
    dup = next((r for r in rows if r["sha256"] == sha), None)
    if dup:
        return dup

    aid = f"ast_{sha[:8]}"
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    THUMBS_DIR.mkdir(parents=True, exist_ok=True)
    dest = ASSETS_DIR / f"{aid}{ext}"
    if src.resolve() != dest.resolve():
        shutil.copy(src, dest)
    thumb = THUMBS_DIR / f"{aid}.jpg"
    name = original_name or src.name

    tech: dict = {}
    audio: dict | None = None
    colors: list[str] = []
    meta: dict = {}

    if kind == "video":
        info = ffprobe_info(str(dest))
        pixfmt = _pix_fmt(dest)
        tech = {"width": info["width"], "height": info["height"],
                "duration_s": round(info["duration"], 1), "fps": info["fps"],
                "has_alpha": pixfmt in ALPHA_PIXFMTS,
                "aspect_bucket": _aspect_bucket(info["width"], info["height"])}
        frames = _video_frames_b64(dest, 6, thumb)
        lufs = _loudness_lufs(dest)
        if lufs is not None:
            audio = {"lufs_i": lufs, "audio_class": "mixed"}
        meta = _llm_describe(
            frames,
            f"Video asset '{name}', {tech['duration_s']}s, "
            f"{tech['width']}x{tech['height']}"
            + (", HAS ALPHA (overlay/transparent)" if tech["has_alpha"] else ""))
        if thumb.exists():
            from PIL import Image
            colors = _dominant_colors(Image.open(thumb))

    elif kind == "image":
        from PIL import Image
        img = Image.open(dest)
        has_alpha = img.mode in ("RGBA", "LA") or (
            img.mode == "P" and "transparency" in img.info)
        tech = {"width": img.width, "height": img.height,
                "has_alpha": has_alpha,
                "aspect_bucket": _aspect_bucket(img.width, img.height)}
        b64 = _image_b64(dest, thumb)
        colors = _dominant_colors(img)
        meta = _llm_describe(
            [b64],
            f"Image asset '{name}', {img.width}x{img.height}"
            + (", HAS TRANSPARENCY (likely logo/sticker/overlay)"
               if has_alpha else ""))

    elif kind == "audio":
        info = ffprobe_info(str(dest))
        dur = round(info["duration"], 1)
        lufs = _loudness_lufs(dest)
        guess = "sfx" if dur < 8 else "music"
        audio = {"lufs_i": lufs, "audio_class": guess}
        tech = {"duration_s": dur}
        meta = _llm_describe_text(
            f"Audio asset, filename '{name}', duration {dur}s, "
            f"loudness {lufs} LUFS. Short files are usually sound effects; "
            f"long ones music. Infer style from the filename.")

    elif kind == "font":
        sample = _render_font_sample(dest, thumb)
        meta = _llm_describe([sample], f"Font asset '{name}' — sample render. "
                             "Describe the typeface style/vibe.") \
            if sample else _llm_describe_text(f"Font file '{name}'.")
        tech = {}

    elif kind == "lut":
        meta = _llm_describe_text(
            f"Color LUT (.cube) file named '{name}'. Infer the look from "
            "the name (e.g. teal-orange, vintage, bw).")
        tech = {}

    row = {
        "id": aid, "sha256": sha, "path": str(dest),
        "filename_original": name, "kind": kind,
        "kind_detail": meta.get("kind_guess", ""),
        "description": meta.get("description", ""),
        "tags": meta.get("tags", []), "mood": meta.get("mood", []),
        "text_content": meta.get("text_content", ""),
        "usage_hints": meta.get("usage_hints", []),
        "dominant_colors": colors, "tech": tech, "audio": audio,
        "license_note": "user_owned", "analysis_version": ANALYSIS_VERSION,
    }
    # audio kind refinement from the LLM ("music" vs "sfx")
    if kind == "audio" and meta.get("kind_guess") in ("music", "sfx",
                                                      "voiceover"):
        row["audio"]["audio_class"] = meta["kind_guess"]

    rows.append(row)
    save_catalog(rows)
    return row


def _pix_fmt(path: Path) -> str:
    try:
        p = subprocess.run(
            ["ffprobe", "-v", "quiet", "-select_streams", "v:0",
             "-show_entries", "stream=pix_fmt", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=30)
        return p.stdout.strip()
    except Exception:
        return ""


def _render_font_sample(font_path: Path, thumb: Path) -> str | None:
    try:
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new("RGB", (512, 200), (24, 22, 18))
        d = ImageDraw.Draw(img)
        f = ImageFont.truetype(str(font_path), 64)
        d.text((20, 30), "Aa Bb 123", font=f, fill=(255, 255, 255))
        d.text((20, 120), "HIZLI KAHVE özgür", font=ImageFont.truetype(
            str(font_path), 36), fill=(232, 179, 75))
        img.save(thumb, "JPEG", quality=85)
        return base64.b64encode(thumb.read_bytes()).decode()
    except Exception:
        return None


def ingest_path(path: str) -> tuple[list[dict], list[str]]:
    """Ingest a file or every supported file in a folder.

    Returns (rows, errors)."""
    p = Path(path).expanduser()
    files = [p] if p.is_file() else sorted(
        f for f in p.rglob("*")
        if f.is_file() and f.suffix.lower() in KIND_BY_EXT) \
        if p.is_dir() else []
    if not files:
        raise ValueError(f"No supported files at {path}")
    rows, errors = [], []
    for f in files:
        try:
            rows.append(ingest_file(str(f)))
        except Exception as e:
            errors.append(f"{f.name}: {e}")
    return rows, errors

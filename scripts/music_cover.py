#!/usr/bin/env python3
"""Match and embed album cover art for a local music library.

Two-phase workflow:

  1. Dry run (the default): scan the library, look each track up on the
     iTunes Search API, and write a plan file you can review and edit.
  2. Apply: read the plan file, download each cover, and embed it into the
     audio file. The audio stream is copied losslessly.

Tracks with an empty album tag get the resolved album name written back on
apply (per-entry `set_album` flag in the plan). If a match is wrong, edit the
`resolved_album` / `art` fields in the plan before applying, or set `note` to
skip it.

Examples:

    scripts/music_cover.py --music-dir ~/Music --dry-run
    # review or edit the plan file, then:
    scripts/music_cover.py --music-dir ~/Music --apply

Requires: ffmpeg, ffprobe.
"""

import argparse
import json
import os
import re
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from difflib import SequenceMatcher

AUDIO_EXT = (".m4a", ".mp3", ".flac", ".ogg", ".opus", ".aac", ".wav", ".mp4", ".m4b")

# Containers ffmpeg can embed cover art into, with the video codec and extra
# muxer flags for the attached picture.
EMBED_FORMATS = {
    ".m4a": ("mjpeg", []),
    ".mp4": ("mjpeg", []),
    ".m4b": ("mjpeg", []),
    ".aac": ("mjpeg", []),
    ".mov": ("mjpeg", []),
    ".mp3": ("mjpeg", ["-id3v2_version", "3"]),
    ".flac": ("mjpeg", []),
}

# Containers ffmpeg cannot embed cover art into (ogg/opus muxers reject image
# streams, wav has no standard picture chunk). Skipped with a note.
UNSUPPORTED_FORMATS = {".ogg", ".oga", ".opus", ".wav", ".aiff", ".aif", ".dsf", ".dff"}


class UnsupportedContainer(Exception):
    pass

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def sim(a, b):
    return SequenceMatcher(None, norm(a), norm(b)).ratio()


def clean_artist(a):
    if not a:
        return ""
    a = a.split(";")[0].split("/")[0].split(",")[0]
    a = re.sub(r"\s*\(.*?\)\s*", "", a).strip()
    return a


def clean_title(t):
    if not t:
        return ""
    t = re.sub(r"\s*\(.*?\)\s*", " ", t).strip()
    return re.sub(r"\s+", " ", t)


def hi_res(url):
    if not url:
        return url
    return re.sub(r"\d+x\d+bb", "1200x1200bb", url)


def probe_tags(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format_tags=artist,album,album_artist,title", "-of", "json", path],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        return {}
    return json.loads(out.stdout).get("format", {}).get("tags", {})


def find_audio(music_dir):
    files = []
    for root, _dirs, names in os.walk(music_dir):
        for name in sorted(names):
            if name.lower().endswith(AUDIO_EXT):
                files.append(os.path.join(root, name))
    return files


def http_json(url, retries=5):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20, context=_ctx) as r:
                return json.load(r), None
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(4 * (attempt + 1))
                continue
            return None, f"HTTP {e.code}"
        except Exception as e:
            time.sleep(3 * (attempt + 1))
    return None, "retry-exhausted"


def itunes_search(term, entity, limit=6):
    q = urllib.parse.quote(term)
    return http_json(
        f"https://itunes.apple.com/search?term={q}&entity={entity}&limit={limit}"
    )


def song_search(artist, title):
    data, err = itunes_search(f"{artist} {clean_title(title)}", "song", 8)
    if err:
        return None, err
    best, best_score = None, -1.0
    ta, tt = norm(artist), norm(clean_title(title))
    for r in data.get("results", []):
        if r.get("kind") != "song":
            continue
        ra, rt = norm(r.get("artistName", "")), norm(r.get("trackName", ""))
        score = 3 * sim(tt, rt)
        if ta and (ta in ra or ra in ta):
            score += 3
        elif ta and sim(ta, ra) > 0.6:
            score += 1
        if score > best_score:
            best_score, best = score, r
    if best and best_score >= 4.0:
        return {"artist": best.get("artistName"),
                "album": best.get("collectionName"),
                "art": best.get("artworkUrl100")}, None
    return None, f"lowscore({best_score:.1f})"


def album_search(artist, album):
    data, err = itunes_search(f"{artist} {clean_title(album)}", "album", 8)
    if err:
        return None, err
    best, best_score = None, -1.0
    ta, tal = norm(artist), norm(album)
    for r in data.get("results", []):
        if r.get("collectionType") not in ("Album", "Compilation", "Soundtrack"):
            continue
        ra, ral = norm(r.get("artistName", "")), norm(r.get("collectionName", ""))
        score = 4 * sim(tal, ral)
        if ta and (ta in ra or ra in ta):
            score += 3
        if score > best_score:
            best_score, best = score, r
    if best and best_score >= 3.5:
        return {"artist": best.get("artistName"),
                "album": best.get("collectionName"),
                "art": best.get("artworkUrl100")}, None
    return None, f"lowscore({best_score:.1f})"


def resolve(artist, title, album, delay):
    """Return (resolved_album, art, source, note, set_album)."""
    time.sleep(delay)
    if not album:
        m, err = song_search(artist, title)
        if m:
            return m["album"], hi_res(m["art"]), "itunes-song", "SET_ALBUM", True
        return None, None, None, f"EMPTY_NOALBUM:{err}", False

    m, err = album_search(artist, album)
    if m:
        return m["album"], hi_res(m["art"]), "itunes-album", "", False
    m2, err2 = song_search(artist, title)
    if m2:
        return m2["album"], hi_res(m2["art"]), "itunes-song", f"fallback({err})", False
    return None, None, None, f"NOMATCH:{err}/{err2}", False


def build_plan(music_dir, delay, limit):
    plan = []
    files = find_audio(music_dir)
    if limit:
        files = files[:limit]
    for i, path in enumerate(files):
        tags = probe_tags(path)
        artist_full = tags.get("artist", "").strip()
        artist = clean_artist(artist_full)
        title = tags.get("title", "").strip()
        album = tags.get("album", "").strip()
        resolved, art, source, note, set_album = resolve(artist, title, album, delay)
        entry = {
            "file": path,
            "artist": artist_full,
            "album_tag": album,
            "resolved_album": resolved,
            "art": art,
            "source": source,
            "note": note,
            "set_album": set_album,
        }
        plan.append(entry)
        print(f"[{i + 1}/{len(files)}] {os.path.basename(path)[:40]:42} "
              f"{resolved or '-':34} {note or (source or '')}", flush=True)
    return plan


def download(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=40, context=_ctx) as r, open(dest, "wb") as f:
        f.write(r.read())


def embed(entry, cover_dir):
    path = entry["file"]
    album = entry["resolved_album"]
    art = entry["art"]
    set_album = bool(entry.get("set_album"))

    ext = os.path.splitext(path)[1].lower()
    if ext in UNSUPPORTED_FORMATS or ext not in EMBED_FORMATS:
        raise UnsupportedContainer(ext)
    vcodec, extra = EMBED_FORMATS[ext]

    raw = os.path.join(cover_dir, f"{abs(hash(path)) % 1000000:06d}_raw")
    clean = os.path.join(cover_dir, f"{abs(hash(path)) % 1000000:06d}.jpg")
    download(hi_res(art), raw)

    # Normalize to one clean JPEG frame (some sources return animated covers).
    p = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-i", raw, "-frames:v", "1", "-q:v", "2", clean],
        capture_output=True, text=True,
    )
    if p.returncode != 0:
        raise RuntimeError(f"normalize failed: {p.stderr.strip()[:160]}")

    tmp = path + ".tmp" + ext
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-i", path, "-i", clean,
           "-map", "0:a", "-map", "1:v",
           "-c:a", "copy", "-c:v", vcodec, "-q:v", "2",
           "-disposition:v:0", "attached_pic"] + extra
    if set_album:
        cmd += ["-metadata", f"album={album}",
                "-metadata", f"album_artist={clean_artist(entry['artist'])}"]
    cmd += [tmp]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"embed failed: {p.stderr.strip()[:160]}")

    v = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
         "-of", "json", tmp],
        capture_output=True, text=True,
    )
    if v.returncode != 0:
        raise RuntimeError(f"verify failed: {v.stderr.strip()[:160]}")
    types = [s.get("codec_type") for s in json.loads(v.stdout).get("streams", [])]
    if "audio" not in types or "video" not in types:
        raise RuntimeError("verify failed: missing streams")

    os.replace(tmp, path)
    os.remove(raw)


def apply_plan(plan, cover_dir):
    results = {"ok": 0, "skip": 0, "fail": 0}
    for entry in plan:
        if not entry.get("art") or entry.get("note", "").startswith(("NOMATCH", "EMPTY_NOALBUM")):
            results["skip"] += 1
            print(f"SKIP {os.path.basename(entry['file'])} | {entry.get('note')}")
            continue
        try:
            embed(entry, cover_dir)
            results["ok"] += 1
            print(f"OK   {os.path.basename(entry['file'])[:40]} -> "
                  f"{entry['resolved_album']} {'[album set]' if entry.get('set_album') else ''}")
        except UnsupportedContainer as e:
            results["skip"] += 1
            print(f"SKIP {os.path.basename(entry['file'])} | embed not supported for {e}")
        except Exception as e:
            results["fail"] += 1
            print(f"FAIL {os.path.basename(entry['file'])} | {e}")
            tmp = entry["file"] + ".tmp" + os.path.splitext(entry["file"])[1]
            if os.path.exists(tmp):
                os.remove(tmp)
    return results


def main():
    ap = argparse.ArgumentParser(description="Match and embed album cover art.")
    ap.add_argument("--music-dir", default=os.path.expanduser("~/Music"))
    ap.add_argument("--plan", default="/tmp/music_cover_plan.json")
    ap.add_argument("--delay", type=float, default=3.0,
                    help="seconds between iTunes requests (default 3)")
    ap.add_argument("--limit", type=int, default=0, help="only process first N files")
    ap.add_argument("--apply", action="store_true", help="download and embed from the plan")
    ap.add_argument("--dry-run", action="store_true", help="write plan, change nothing")
    args = ap.parse_args()

    if not args.apply and not args.dry_run:
        args.dry_run = True

    if args.apply:
        if not os.path.exists(args.plan):
            sys.exit(f"plan file not found: {args.plan}")
        plan = json.load(open(args.plan))
        cover_dir = "/tmp/music_cover_covers"
        os.makedirs(cover_dir, exist_ok=True)
        print(json.dumps(apply_plan(plan, cover_dir), indent=2))
        return

    plan = build_plan(args.music_dir, args.delay, args.limit)
    with open(args.plan, "w") as f:
        json.dump(plan, f, indent=2)
    print(f"\nPlan written to {args.plan}")
    print("Review/edit it, then run again with --apply.")


if __name__ == "__main__":
    main()

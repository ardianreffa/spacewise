"""Find what takes space among photos, videos and leftover installers. Local, no network.

Tiers:
  green  exact duplicates (an identical copy stays) and leftover installers (.dmg / .pkg)
  amber  similar photos (the sharpest of each group stays) and blurry or near-black photos
  grey   large videos, screenshots, and large files of any other kind: reported, never proposed

The image library is used without changing any process-wide setting: HEIC/HEIF is opened
through pillow_heif.open_heif (no global opener registered) and Pillow's size limit is left
alone; a photo too large for it is skipped with a reason.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import time
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from PIL import Image, ImageFilter, ImageOps, ImageStat

from . import guard

PHOTO_EXT = {".jpg", ".jpeg", ".jpe", ".heic", ".heif", ".hif", ".png", ".tif", ".tiff", ".webp"}
RAW_EXT = {".dng", ".cr2", ".cr3", ".crw", ".nef", ".nrw", ".arw", ".srf", ".sr2", ".raf", ".orf", ".rw2",
           ".pef", ".srw", ".3fr", ".iiq", ".erf", ".kdc", ".mrw", ".x3f", ".rwl"}
VIDEO_EXT = {".mov", ".mp4", ".m4v", ".avi", ".mkv", ".3gp", ".mts", ".m2ts", ".webm", ".wmv", ".mpg", ".mpeg"}
INSTALLER_EXT = {".dmg", ".pkg"}
DESIGN_EXT = {".psd", ".psb", ".ai", ".indd", ".sketch", ".fig", ".xd", ".afphoto", ".afdesign", ".blend"}
ARCHIVE_EXT = {".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".xz", ".bz2"}

LARGE_VIDEO = 500 * 1024 ** 2          # videos above this are listed for the user to decide
LARGE_OTHER = 200 * 1024 ** 2          # other files above this go on the large-file radar
SCREENSHOT_RE = re.compile(r"^(screen ?shot|screenshot|screen recording|cuplikan layar|tangkapan layar|"
                           r"captura de pantalla|bildschirmfoto|capture d.écran)", re.I)
BLUR_MAX = 0.12                        # re-blur score below this: nothing is in focus
DARK_MEAN = 22.0                       # mean brightness below this: close to black
SIMILAR_SECONDS = 2.5
SIMILAR_HASH = 10
ANALYSIS_EDGE = 768
PARTIAL = 64 * 1024


def kind_of(path: Path) -> Optional[str]:
    ext = path.suffix.lower()
    if ext in PHOTO_EXT:
        return "photo"
    if ext in RAW_EXT:
        return "raw"
    if ext in VIDEO_EXT:
        return "video"
    if ext in INSTALLER_EXT:
        return "installer"
    return None


def other_group(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in DESIGN_EXT:
        return "design files"
    if ext in ARCHIVE_EXT:
        return "archives"
    return "other files"


# ------------------------------------------------------------------ walking (guard applied to every entry)
def walk(root: Path, recursive: bool, skipped: dict) -> Iterable[tuple[Path, os.stat_result]]:
    """Yield (path, lstat) for regular files the guard lets through. Records why others were skipped."""
    def note(reason: str, size: int = 0):
        e = skipped.setdefault(reason, [0, 0]); e[0] += 1; e[1] += size

    stack = [root]
    while stack:
        d = stack.pop()
        try:
            entries = list(os.scandir(d))
        except PermissionError:
            note("no permission to read"); continue
        except OSError:
            note("could not be read"); continue
        for e in sorted(entries, key=lambda x: x.name):
            p = Path(e.path)
            try:
                st = e.stat(follow_symlinks=False)
            except OSError:
                note("could not be read"); continue
            why = guard.is_hidden_or_locked(p, st)
            if why:
                note(why, st.st_size if stat.S_ISREG(st.st_mode) else 0); continue
            if stat.S_ISDIR(st.st_mode):
                prot = guard.is_protected_dir(p)
                if prot:
                    note(prot); continue
                if recursive:
                    stack.append(p)
                else:
                    note("subfolder (not scanned; ask for subfolders to include them)")
                continue
            if stat.S_ISREG(st.st_mode):
                yield p, st


# ------------------------------------------------------------------ hashing (exact duplicates)
def _hash(path: Path, partial: bool) -> Optional[str]:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            if partial:
                h.update(fh.read(PARTIAL))
                size = os.fstat(fh.fileno()).st_size
                if size > 2 * PARTIAL:
                    fh.seek(size - PARTIAL); h.update(fh.read(PARTIAL))
            else:
                for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                    h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def exact_duplicates(files: list[dict], deadline: float) -> tuple[list[list[dict]], bool]:
    """Groups of byte-identical files. Same size, then same head+tail, then same full SHA-256.
    Hard links (same device and inode) are one file, never a duplicate. Returns (groups, finished)."""
    by_size: dict[int, list[dict]] = {}
    for f in files:
        if f["size"] > 0:
            by_size.setdefault(f["size"], []).append(f)
    groups = []; done = 0
    for size, same in sorted(by_size.items(), key=lambda kv: -kv[0]):
        uniq = {}
        for f in sorted(same, key=lambda f: (len(Path(f["path"]).name), f["path"])):
            uniq.setdefault((f["dev"], f["ino"]), f)          # hard links collapse to one (shortest name kept)
        same = list(uniq.values())
        if len(same) < 2:
            continue
        if done and time.time() > deadline and not all(f.get("full") for f in same):
            return groups, False                                  # resume next call (hashes are cached)
        done += 1
        by_part: dict[str, list[dict]] = {}
        for f in same:
            k = f.get("partial") or _hash(Path(f["path"]), True)
            f["partial"] = k
            if k:
                by_part.setdefault(k, []).append(f)
        for cand in by_part.values():
            if len(cand) < 2:
                continue
            by_full: dict[str, list[dict]] = {}
            for f in cand:
                k = f.get("full") or _hash(Path(f["path"]), False)
                f["full"] = k
                if k:
                    by_full.setdefault(k, []).append(f)
            groups += [g for g in by_full.values() if len(g) > 1]
    return groups, True


def choose_keeper(group: list[dict], home: Path) -> dict:
    """Which identical copy stays: not in Downloads, then the oldest, then the shortest path."""
    dl = str(home / "Downloads")
    # a hard-linked file stays (moving one of its names would free nothing); then not in Downloads,
    # then the oldest, then the shortest path
    return sorted(group, key=lambda f: (f.get("nlink", 1) == 1, f["path"].startswith(dl), f["mtime"],
                                        len(f["path"]), f["path"]))[0]


# ------------------------------------------------------------------ photo measurements (similar / blurry / dark)
def open_photo(path: Path) -> Optional[Image.Image]:
    ext = path.suffix.lower()
    try:
        if ext in (".heic", ".heif", ".hif"):
            import pillow_heif                      # opened directly: no global opener is registered
            im = pillow_heif.open_heif(str(path), convert_hdr_to_8bit=True).to_pillow()
        else:
            im = Image.open(path)
            if im.format == "JPEG":
                im.draft("RGB", (ANALYSIS_EDGE * 2, ANALYSIS_EDGE * 2))
            im.load()
        return im
    except Image.DecompressionBombError:
        return None
    except Exception:
        return None


def _capture_time(im: Image.Image) -> Optional[float]:
    try:
        exif = im.getexif(); sub = exif.get_ifd(0x8769)
        raw = sub.get(0x9003) or exif.get(0x0132)
        if not raw:
            return None
        t = datetime.strptime(str(raw).strip()[:19], "%Y:%m:%d %H:%M:%S").timestamp()
        frac = str(sub.get(0x9291) or "").strip()
        if frac.isdigit():
            t += float("0." + frac)
        return t
    except Exception:
        return None


def measure(path: Path) -> dict:
    """Sharpness (re-blur ratio), brightness, a 64-bit dHash and capture time, from a small copy."""
    im = open_photo(path)
    if im is None:
        return {"error": "could not be opened (damaged, unsupported or larger than the image library allows)"}
    taken = _capture_time(im)
    try:
        im = ImageOps.exif_transpose(im)
    except Exception:
        pass
    g = im.convert("L"); g.thumbnail((ANALYSIS_EDGE, ANALYSIS_EDGE))
    edges = g.filter(ImageFilter.FIND_EDGES)
    soft = g.filter(ImageFilter.GaussianBlur(2)).filter(ImageFilter.FIND_EDGES)
    w, h = g.size; tiles = []
    for ty in range(4):
        for tx in range(4):
            box = (tx * w // 4, ty * h // 4, (tx + 1) * w // 4, (ty + 1) * h // 4)
            e = ImageStat.Stat(edges.crop(box)).var[0]; s = ImageStat.Stat(soft.crop(box)).var[0]
            tiles.append((e, 1 - (s / e) if e > 1e-6 else 0.0))
    tiles.sort(key=lambda x: -x[0]); top = tiles[:3]
    sharp = max(0.0, sum(r for _, r in top) / len(top))
    small = g.resize((9, 8), Image.BILINEAR); px = list(small.tobytes()); bits = 0
    for r in range(8):
        for c in range(8):
            bits = (bits << 1) | (1 if px[r * 9 + c] > px[r * 9 + c + 1] else 0)
    return {"sharpness": round(sharp, 3), "brightness": round(ImageStat.Stat(g).mean[0], 1),
            "dhash": f"{bits:016x}", "taken": taken, "width": im.size[0], "height": im.size[1]}


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def one_per_inode(files: list[dict]) -> list[dict]:
    """Hard links are one file on disk: keep one name (the shortest) so it is never 'similar to itself'."""
    seen: dict = {}
    for f in sorted(files, key=lambda f: (len(Path(f["path"]).name), f["path"])):
        seen.setdefault((f["dev"], f["ino"]), f)
    return list(seen.values())


def similar_groups(photos: list[dict]) -> list[list[dict]]:
    """Bursts: shot within a few seconds of the previous frame and looking alike, chained frame to frame."""
    photos = one_per_inode(photos)
    timed = sorted((p for p in photos if p.get("taken") and p.get("dhash")), key=lambda p: (p["taken"], p["path"]))
    groups, cur = [], []
    for p in timed:
        if cur and p["taken"] - cur[-1]["taken"] <= SIMILAR_SECONDS and hamming(p["dhash"], cur[-1]["dhash"]) <= SIMILAR_HASH:
            cur.append(p)
        else:
            if len(cur) > 1:
                groups.append(cur)
            cur = [p]
    if len(cur) > 1:
        groups.append(cur)
    return groups


def raw_pair_stems(files: list[dict]) -> set[tuple[str, str]]:
    """(folder, stem) of photos that have a RAW sibling: RAW+JPEG pairs are never split."""
    raws = {(str(Path(f["path"]).parent), Path(f["path"]).stem.lower()) for f in files if f["kind"] == "raw"}
    return raws


def is_screenshot(path: Path) -> bool:
    return bool(SCREENSHOT_RE.match(path.name))

"""Where Spacewise may look, decided before a single file is read.

Allowed: folders inside the user's own home folder (never the home folder itself) and folders
on external drives (/Volumes/<drive>/... on macOS, /media or /run/media/<user>/... and /mnt/...
on Linux). Everything else is refused: the operating system, other users, app data and secrets
(~/Library, every hidden folder), and "packages" that must not be touched from outside (Photos
and Lightroom libraries, Final Cut libraries, apps, Time Machine backups).

Inside an allowed folder, the walker also skips hidden items (dot names and the macOS hidden
flag), locked items (Finder "Locked"), items it has no permission to read, symlinks, and cloud
placeholders (iCloud / File Provider files that are not downloaded; reading them would download
them). Nothing here asks for elevated rights or changes permissions.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from typing import Optional

# macOS / BSD file flags (absent on Linux and Windows; we read them with getattr)
UF_HIDDEN = getattr(stat, "UF_HIDDEN", 0x8000)
UF_IMMUTABLE = getattr(stat, "UF_IMMUTABLE", 0x2)
SF_IMMUTABLE = getattr(stat, "SF_IMMUTABLE", 0x20000)
SF_DATALESS = getattr(stat, "SF_DATALESS", 0x40000000)

# Directories that are "packages": a library or an app the owning program manages. Moving one
# file out of them can corrupt the whole thing, so Spacewise never enters them.
PROTECTED_SUFFIXES = (
    ".photoslibrary", ".photolibrary", ".aplibrary", ".migratedphotolibrary",   # Apple Photos / iPhoto / Aperture
    ".lrdata", ".lrlibrary",                                                    # Lightroom previews / libraries
    ".fcpbundle", ".imovielibrary", ".tvlibrary", ".musiclibrary",              # Final Cut, iMovie, TV, Music
    ".app", ".bundle", ".framework", ".plugin", ".kext", ".xpc",                # applications and code
    ".sparsebundle", ".sparseimage", ".backupbundle", ".backupdb",              # disk images, backups
    ".photoslibrary-wal",
)
PROTECTED_NAMES = {"Backups.backupdb", "Applications", "Library", "System", "$RECYCLE.BIN",
                   "System Volume Information", "lost+found"}
SYSTEM_ROOTS = ("/System", "/Library", "/usr", "/bin", "/sbin", "/etc", "/private", "/var",
                "/opt", "/cores", "/dev", "/Applications", "/boot", "/proc", "/sys", "/root",
                "/snap", "/lib", "/lib64", "/srv", "/tmp")


class Verdict:
    __slots__ = ("allowed", "path", "reason")

    def __init__(self, allowed: bool, path: Optional[Path], reason: str):
        self.allowed, self.path, self.reason = allowed, path, reason

    def as_dict(self) -> dict:
        return {"allowed": self.allowed, "path": str(self.path) if self.path else None, "reason": self.reason}


def _home() -> Path:
    return Path(os.path.realpath(Path.home()))


def _external_roots() -> list[Path]:
    """Mount points of external drives (resolved); the boot volume alias resolves to / and is dropped."""
    roots: list[Path] = []
    if sys.platform == "darwin":
        bases = [Path("/Volumes")]
    elif sys.platform.startswith("linux"):
        user = os.environ.get("USER") or Path.home().name
        bases = [Path("/media") / user, Path("/run/media") / user, Path("/media"), Path("/mnt")]
    else:
        bases = []
    for base in bases:
        try:
            for child in base.iterdir():
                real = Path(os.path.realpath(child))
                if real != Path("/") and child.is_dir():
                    roots.append(real)
        except OSError:
            continue
    return roots


def is_hidden_or_locked(path: Path, st: Optional[os.stat_result] = None) -> Optional[str]:
    """Why a single entry must be skipped (hidden, locked, cloud placeholder), or None."""
    if path.name.startswith("."):
        return "hidden"
    try:
        st = st or os.lstat(path)
    except OSError:
        return "no permission to read"
    if stat.S_ISLNK(st.st_mode):
        return "symlink (not followed)"
    flags = getattr(st, "st_flags", 0)
    if flags & UF_HIDDEN:
        return "hidden"
    if flags & (UF_IMMUTABLE | SF_IMMUTABLE):
        return "locked"
    if flags & SF_DATALESS:
        return "in the cloud, not downloaded"
    return None


def is_protected_dir(path: Path) -> Optional[str]:
    """Why a directory is a no-go area during a walk, or None."""
    name = path.name
    if name in PROTECTED_NAMES:
        return "app or system data"
    low = name.lower()
    if any(low.endswith(s) for s in PROTECTED_SUFFIXES):
        return "a library or app package (managed by its own program)"
    try:
        if any(p.suffix.lower() == ".lrcat" for p in path.iterdir() if p.is_file()):
            return "a Lightroom catalog folder"
    except OSError:
        return "no permission to read"
    return None


def check(raw: str, cwd: Optional[Path] = None) -> Verdict:
    """Decide whether `raw` may be scanned. Reads no file contents; only names and flags."""
    text = (raw or "").strip()
    if not text:
        return Verdict(False, None, "no folder given: name the folder to check, e.g. ~/Downloads")
    p = Path(os.path.expanduser(text))
    if not p.is_absolute():
        p = (cwd or Path.cwd()) / p
    # The literal path the user gave must not be (or pass through) a symlink: no detours.
    try:
        literal = Path(os.path.abspath(p))
        cur = Path(literal.anchor)
        for part in literal.parts[1:]:
            cur = cur / part
            if cur.is_symlink() and not (sys.platform == "darwin" and str(cur) in ("/tmp", "/var", "/etc")):
                return Verdict(False, literal, f"the path goes through a shortcut (symlink) at {cur}; give the real folder")
    except OSError:
        pass
    real = Path(os.path.realpath(p))
    if not real.exists():
        return Verdict(False, real, "folder not found (Spacewise does not guess similar names)")
    if not real.is_dir():
        return Verdict(False, real, "not a folder")
    home = _home()
    if real == Path("/") or real == Path(real.anchor):
        return Verdict(False, real, "the root of a disk is never scanned; name a specific folder")
    if real == home:
        return Verdict(False, real, "the whole home folder is too broad; name a specific folder such as ~/Downloads or ~/Pictures")
    inside_home = home in real.parents
    on_external = any(r == real or r in real.parents for r in _external_roots())
    if not inside_home and not on_external:
        for root in SYSTEM_ROOTS:
            if str(real) == root or str(real).startswith(root + "/"):
                return Verdict(False, real, "system folder: Spacewise never scans the operating system")
        if real in (Path("/Users/Shared"), Path("/home/Shared")) or (real.parent in (Path("/Users"), Path("/home")) and real.name == "Shared"):
            return Verdict(False, real, "the shared folder for all users of this computer is outside your home folder")
        if real.parent in (Path("/Users"), Path("/home")) or any(p in (Path("/Users"), Path("/home")) for p in real.parents):
            return Verdict(False, real, "another user's folder")
        return Verdict(False, real, "outside your home folder and external drives")
    if inside_home:
        rel = real.relative_to(home)
        if rel.parts and rel.parts[0] in ("Library", "Applications"):
            return Verdict(False, real, "app data (~/Library, ~/Applications) is never scanned")
    base = home if inside_home else next(r for r in _external_roots() if r == real or r in real.parents)
    cur = base
    for part in real.relative_to(base).parts:
        cur = cur / part
        why = is_hidden_or_locked(cur)
        if why and why != "symlink (not followed)":
            return Verdict(False, real, f"{why} folder: {cur.name}")
        why = is_protected_dir(cur)
        if why:
            return Verdict(False, real, f"{why}: {cur.name}")
    if not os.access(real, os.R_OK | os.X_OK):
        return Verdict(False, real, "no permission to read this folder (macOS may need to grant access); Spacewise does not try to get around it")
    return Verdict(True, real, "allowed")

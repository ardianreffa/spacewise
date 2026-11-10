"""Move files to the Trash of the disk they are on, and put them back.

Rules:
  * Only a rename within the same disk. If the Trash is on another disk (EXDEV), the file is
    skipped: copying would fill the disk the user is trying to free.
  * Never overwrites anything in the Trash: a clash gets a numbered name.
  * macOS: ~/.Trash for the startup disk, <drive>/.Trashes/<uid> for external drives.
    Linux: the freedesktop.org Trash (~/.local/share/Trash, or <drive>/.Trash-<uid>) with its
    .trashinfo record, so file managers can restore it too.
    Windows: not supported (Recycle Bin needs the shell API); Spacewise only reports there.
  * Finder's "Put Back" does not know about files moved this way; spacewise_undo does.
"""

from __future__ import annotations

import errno
import os
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.parse import quote


class TrashError(Exception):
    pass


def supported() -> bool:
    return sys.platform == "darwin" or sys.platform.startswith("linux")


def _mount_point(path: Path) -> Path:
    path = Path(os.path.realpath(path))
    dev = os.stat(path).st_dev
    while path != path.parent:
        if os.stat(path.parent).st_dev != dev:
            return path
        path = path.parent
    return path


def trash_dir(path: Path) -> Path:
    """The Trash folder on the same disk as `path` (created if missing)."""
    if not supported():
        raise TrashError("moving to the Trash is supported on macOS and Linux only")
    home = Path.home()
    same_disk = os.stat(path).st_dev == os.stat(home).st_dev
    uid = os.getuid()
    if sys.platform == "darwin":
        if same_disk:
            d = home / ".Trash"
        else:
            top = _mount_point(path) / ".Trashes"
            if not top.exists():
                top.mkdir(mode=0o1333)
            d = top / str(uid)
        d.mkdir(mode=0o700, exist_ok=True)
        return d
    if same_disk:
        base = Path(os.environ.get("XDG_DATA_HOME", home / ".local" / "share")) / "Trash"
    else:
        base = _mount_point(path) / f".Trash-{uid}"
    (base / "files").mkdir(parents=True, exist_ok=True, mode=0o700)
    (base / "info").mkdir(parents=True, exist_ok=True, mode=0o700)
    return base / "files"


def _free_name(d: Path, name: str) -> Path:
    cand = d / name
    stem, suffix = os.path.splitext(name); n = 2
    while cand.exists() or (sys.platform != "darwin" and (d.parent / "info" / (cand.name + ".trashinfo")).exists()):
        cand = d / f"{stem} ({n}){suffix}"; n += 1
    return cand


def move_to_trash(path: Path) -> Path:
    path = Path(path)
    d = trash_dir(path)
    dest = _free_name(d, path.name)
    if sys.platform != "darwin":
        info = d.parent / "info" / (dest.name + ".trashinfo")
        with open(info, "x", encoding="utf-8") as fh:
            fh.write("[Trash Info]\nPath=%s\nDeletionDate=%s\n" % (quote(str(path)), time.strftime("%Y-%m-%dT%H:%M:%S")))
    try:
        os.rename(path, dest)                       # same disk only; never a copy
    except OSError as exc:
        if sys.platform != "darwin":
            try: info.unlink()
            except OSError: pass
        if exc.errno == errno.EXDEV:
            raise TrashError("the Trash is on another disk; skipped (copying would use more space)")
        raise TrashError(exc.strerror or str(exc))
    return dest


def restore(trash_path: Path, original: Path) -> Optional[str]:
    """Put a file back. Returns None on success, or why it could not be done."""
    trash_path, original = Path(trash_path), Path(original)
    if not trash_path.exists():
        return "no longer in the Trash (it may have been emptied or restored already)"
    if original.exists():
        return "a file with the same name is already back at the original place"
    try:
        original.parent.mkdir(parents=True, exist_ok=True)
        os.rename(trash_path, original)
    except OSError as exc:
        return exc.strerror or str(exc)
    if sys.platform != "darwin":
        try: (trash_path.parent.parent / "info" / (trash_path.name + ".trashinfo")).unlink()
        except OSError: pass
    return None

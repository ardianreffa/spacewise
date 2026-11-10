"""Spacewise tests. They build their own photos and folders under a temporary HOME."""

import importlib
import importlib.util
import json
import os
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("spacewise", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
mod = importlib.util.module_from_spec(spec); sys.modules["spacewise"] = mod; spec.loader.exec_module(mod)
guard = importlib.import_module("spacewise.guard")
tools = importlib.import_module("spacewise.tools")


def scene(seed: int, size=900) -> Image.Image:
    im = Image.new("RGB", (size, size), (200, 190, 170)); d = ImageDraw.Draw(im)
    for i in range(60):
        x = (seed * 37 + i * 53) % size; y = (seed * 91 + i * 29) % size
        d.rectangle((x, y, x + 40, y + 25), fill=((i * 40) % 255, (i * 70 + seed) % 255, (i * 15) % 255))
        d.line((0, (i * 15) % size, size, (i * 27) % size), fill=(20, 20, 20), width=2)
    return im


def save(im, path, t):
    e = Image.Exif(); e.get_ifd(0x8769)[0x9003] = t.strftime("%Y:%m:%d %H:%M:%S"); im.save(path, quality=92, exif=e)


class State(dict):
    def get(self, k, default=None):
        return json.loads(json.dumps(super().get(k, default)))

    def set(self, k, v):
        self[k] = json.loads(json.dumps(v))


class Ctx:
    def __init__(self):
        self.state = State()


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"; h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("XDG_DATA_HOME", str(h / ".local" / "share"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    return h


@pytest.fixture
def shoot(home):
    d = home / "Pictures" / "shoot"; d.mkdir(parents=True)
    t0 = datetime(2026, 11, 8, 17, 0, 0)
    base = scene(1)
    for i in range(3):                                            # a burst; frame 1 is the sharpest
        im = base.crop((i * 4, 0, 860 + i * 4, 860))
        if i != 1:
            im = im.filter(ImageFilter.GaussianBlur(1.5))
        save(im, d / f"IMG_10{i}.JPG", t0 + timedelta(seconds=i * 0.5))
    save(scene(2).filter(ImageFilter.GaussianBlur(14)), d / "IMG_200.JPG", t0 + timedelta(minutes=3))
    save(ImageEnhance.Brightness(scene(3)).enhance(0.03), d / "IMG_201.JPG", t0 + timedelta(minutes=4))
    save(scene(4), d / "IMG_300.JPG", t0 + timedelta(minutes=8))
    shutil.copy(d / "IMG_300.JPG", d / "IMG_300 copy.JPG")
    os.link(d / "IMG_300.JPG", d / "IMG_300 link.JPG")
    save(scene(5), d / "DSC0001.JPG", t0 + timedelta(minutes=20)); (d / "DSC0001.ARW").write_bytes(os.urandom(50000))
    (d / "Setup.dmg").write_bytes(os.urandom(200000))
    with open(d / "trip.mov", "wb") as fh:
        fh.truncate(600 * 1024 ** 2)
    with open(d / "client.psd", "wb") as fh:
        fh.truncate(250 * 1024 ** 2)
    scene(6).save(d / "Screenshot 2026-11-10 at 10.00.00.png")
    (d / ".secret").mkdir(); shutil.copy(d / "IMG_300.JPG", d / ".secret" / "x.JPG")
    (d / "Lib.photoslibrary").mkdir(); shutil.copy(d / "IMG_300.JPG", d / "Lib.photoslibrary" / "y.JPG")
    return d


def sw():
    return tools.Spacewise(Ctx())


# ------------------------------------------------------------------ guard
def test_guard_refuses_protected_areas(home, shoot):
    (home / "Library" / "Mail").mkdir(parents=True)
    (home / ".ssh").mkdir()
    (home / "Pictures" / "cat").mkdir(); (home / "Pictures" / "cat" / "a.lrcat").write_text("x")
    os.symlink("/etc", home / "to-etc")
    refused = ["/", "/etc", str(home), "~", str(home / "Library"), str(home / "Library" / "Mail"), str(home / ".ssh"),
               str(shoot / ".secret"), str(shoot / "Lib.photoslibrary"), str(home / "Pictures" / "cat"),
               str(home / "to-etc"), str(home / "missing"), "", str(shoot / "IMG_300.JPG")]
    for p in refused:
        assert not guard.check(p, home).allowed, p
    assert guard.check(str(shoot), home).allowed
    assert guard.check("~/Pictures", home).allowed


def test_guard_test_command_reads_nothing(home, shoot):
    out = sw().slash("guard-test /etc")
    assert "REFUSED" in out and "No file was opened or read" in out


# ------------------------------------------------------------------ scan
def test_scan_tiers(home, shoot):
    s = sw(); r = json.loads(s.scan({"folder": str(shoot)}))
    assert r["scanned_exactly"].endswith("Pictures/shoot") and r["include_subfolders"] is False
    dup = r["green_safe_to_free"]["exact_duplicates"]
    assert dup["files"] == 1 and "IMG_300 copy.JPG = copy of IMG_300.JPG" in dup["examples"]   # hard link is not a duplicate
    assert r["green_safe_to_free"]["installers"]["files"] == 1
    sim = r["amber_check_first"]["similar_photos"]
    assert sim["groups"] == 1 and sim["examples"] == ["keeps IMG_101.JPG, 2 similar"]
    blurry = r["amber_check_first"]["blurry_or_dark"]["examples"]
    assert any("IMG_200" in b for b in blurry) and any("IMG_201" in b for b in blurry)
    assert not any("DSC0001" in b for b in blurry)                                                # RAW+JPEG pair untouched
    grey = r["grey_for_the_user_to_decide"]
    assert grey["large_videos"]["files"] == 1 and grey["screenshots"]["files"] == 1
    assert "design files" in grey["other_large_files_by_type"]
    assert "client.psd" not in json.dumps(r)                                                     # names of other large files stay local
    assert "hidden" in r["not_looked_at"] and any("library" in k for k in r["not_looked_at"])


def test_scan_refuses_and_echoes(home):
    r = json.loads(sw().scan({"folder": "/etc"}))
    assert r["error"].startswith("refused") and r["requested"]["folder"] == "/etc"


# ------------------------------------------------------------------ trash: approval-gated, categories only
def test_trash_needs_the_approval_hook(home, shoot):
    s = sw(); sid = json.loads(s.scan({"folder": str(shoot)}))["scan_id"]
    r = json.loads(s.trash({"scan_id": sid, "categories": ["exact_duplicates"]}))
    assert "approval gate" in r["error"] and (shoot / "IMG_300 copy.JPG").exists()


def test_hook_blocks_bad_selections(home, shoot):
    s = sw(); sid = json.loads(s.scan({"folder": str(shoot)}))["scan_id"]
    for args in ({"scan_id": sid, "categories": ["large_videos"]},
                 {"scan_id": sid, "categories": []},
                 {"scan_id": sid, "categories": ["exact_duplicates"], "paths": [str(shoot / "trip.mov")]},
                 {"scan_id": "nope", "categories": ["installers"]}):
        assert s.approval_request(args)["action"] == "block", args


def test_trash_and_undo(home, shoot):
    s = sw(); sid = json.loads(s.scan({"folder": str(shoot)}))["scan_id"]
    a = s.approval_request({"scan_id": sid, "categories": ["exact_duplicates", "blurry_or_dark"]})
    assert a["action"] == "approve" and "to the Trash" in a["message"]
    r = json.loads(s.trash({"scan_id": sid, "categories": ["exact_duplicates", "blurry_or_dark"]}))
    assert r["moved_to_trash"] == 3
    assert (shoot / "IMG_300.JPG").exists() and (shoot / "IMG_300 link.JPG").exists()          # one copy always stays
    assert all((shoot / f).exists() for f in ("trip.mov", "client.psd", "DSC0001.ARW", "DSC0001.JPG"))
    trash = home / ".local" / "share" / "Trash" / "files"
    assert {p.name for p in trash.iterdir()} == {"IMG_300 copy.JPG", "IMG_200.JPG", "IMG_201.JPG"}
    assert s.approval_request({"scan_id": sid, "categories": ["exact_duplicates"]})["action"] == "block"   # not twice
    u = json.loads(s.undo({}))
    assert u["put_back"] == 3 and (shoot / "IMG_300 copy.JPG").exists() and not any(trash.iterdir())


def test_changed_file_and_missing_keeper_are_skipped(home, shoot):
    s = sw(); sid = json.loads(s.scan({"folder": str(shoot)}))["scan_id"]
    os.utime(shoot / "IMG_200.JPG", (1, 1))                                  # changed after the scan
    (shoot / "IMG_300.JPG").unlink(); (shoot / "IMG_300 link.JPG").unlink()   # the copy that should stay is gone
    s.approval_request({"scan_id": sid, "categories": ["exact_duplicates", "blurry_or_dark"]})
    r = json.loads(s.trash({"scan_id": sid, "categories": ["exact_duplicates", "blurry_or_dark"]}))
    whys = {x["name"]: x["why"] for x in r["skipped"]}
    assert "changed since the scan" in whys["IMG_200.JPG"]
    assert "should stay is gone" in whys["IMG_300 copy.JPG"] and (shoot / "IMG_300 copy.JPG").exists()


def test_large_folder_resumes(home, shoot, monkeypatch):
    monkeypatch.setattr(tools, "TIME_BUDGET", 0.0)
    monkeypatch.setattr(tools, "MIN_PER_CALL", 2)
    s = sw(); r = json.loads(s.scan({"folder": str(shoot)})); calls = 1
    while r.get("in_progress") and calls < 30:
        r = json.loads(s.scan({"folder": str(shoot)})); calls += 1
    assert calls > 1 and "scan_id" in r


def test_preview_page_is_local(home, shoot):
    s = sw(); s.scan({"folder": str(shoot)})
    page = Path(json.loads(s.preview({}))["page"])
    html = page.read_text()
    assert page.exists() and "http://" not in html and "https://" not in html
    assert any(p.suffix == ".jpg" for p in page.parent.iterdir())


def test_hard_links_are_never_proposed(home, shoot):
    s = sw(); r = s.scan({"folder": str(shoot)})
    assert "IMG_300 link.JPG" not in r                      # neither a duplicate nor "similar to itself"
    plan = s._load("plans", None, "last_plan")
    proposed = [x["path"] for g in plan["groups"]["exact_duplicates"] for x in g["extra"]]
    proposed += [x["path"] for g in plan["groups"]["similar_photos"] for x in g["extra"]]
    proposed += [x["path"] for x in plan["groups"]["blurry_or_dark"]]
    assert not any(os.stat(p).st_nlink > 1 for p in proposed)

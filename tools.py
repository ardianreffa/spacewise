"""Spacewise: tool handlers.

Division of roles (enforced here, not just described):
  * The plugin scans, measures and keeps the boundaries. It never decides.
  * The agent translates and explains. It cannot choose files: spacewise_trash only takes a
    scan id and categories from that scan, never a list of paths, and it only runs after the
    pre_tool_call hook has sent it through Hermes' human-approval gate.
  * The user decides. Large videos, screenshots and every other large file are reported only;
    no tool can move them.
No network access, no model calls, no shell commands, no process-wide settings changed.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from . import guard, preview, scan, trashbin

PLUGIN = "spacewise"
TRASHABLE = ("exact_duplicates", "installers", "similar_photos", "blurry_or_dark")
TIME_BUDGET = 240.0
MIN_PER_CALL = 24          # every call makes progress, even past the time budget
LIST_LIMIT = 12
KEEP_PLANS = 10
KEEP_PREVIEWS = 2
APPROVAL_WINDOW = 600.0


def _ok(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _err(message: str, **extra: Any) -> str:
    return json.dumps({"error": message, **extra}, ensure_ascii=False)


def _session_dir() -> Path:
    try:
        from agent.runtime_cwd import resolve_agent_cwd
        return Path(resolve_agent_cwd())
    except Exception:
        return Path.home()


def _display(p: str) -> str:
    home = str(Path.home())
    return "~" + p[len(home):] if p.startswith(home) else p


def gb(n: float) -> str:
    return preview.gb(n)


def files(n: int) -> str:
    return f"{n} file" if n == 1 else f"{n} files"


class Spacewise:
    def __init__(self, ctx):
        self.ctx = ctx
        self._db = None
        self._approvals: dict[str, float] = {}     # selection key -> time the approval gate was reached

    # ── storage ────────────────────────────────────────────────────────
    def _conn(self):
        if self._db is None:
            from plugins.plugin_storage import plugin_db
            self._db = plugin_db(PLUGIN)
            for sql in ("CREATE TABLE IF NOT EXISTS metrics (path TEXT PRIMARY KEY, size INTEGER, mtime REAL, data TEXT)",
                        "CREATE TABLE IF NOT EXISTS plans (id TEXT PRIMARY KEY, created REAL, data TEXT)",
                        "CREATE TABLE IF NOT EXISTS batches (id TEXT PRIMARY KEY, created REAL, data TEXT)"):
                self._db.execute(sql)
            self._db.commit()
        return self._db

    def _data_dir(self) -> Path:
        from plugins.plugin_storage import plugin_data_dir
        return plugin_data_dir(PLUGIN)

    def _state_get(self, key: str, default: Any = None) -> Any:
        try:
            return self.ctx.state.get(key, default=default)
        except TypeError:
            return self.ctx.state.get(key, default)

    def _state_set(self, key: str, value: Any) -> None:
        self.ctx.state.set(key, value)

    def _save(self, table: str, obj: dict) -> None:
        c = self._conn()
        c.execute(f"INSERT OR REPLACE INTO {table}(id, created, data) VALUES (?,?,?)", (obj["id"], obj["created"], json.dumps(obj)))
        c.execute(f"DELETE FROM {table} WHERE id NOT IN (SELECT id FROM {table} ORDER BY created DESC LIMIT ?)", (KEEP_PLANS,))
        c.commit()

    def _load(self, table: str, oid: Optional[str], state_key: str) -> Optional[dict]:
        oid = oid or self._state_get(state_key)
        if not oid:
            return None
        row = self._conn().execute(f"SELECT data FROM {table} WHERE id = ?", (oid,)).fetchone()
        return json.loads(row[0]) if row else None

    def _cached(self, f: dict) -> dict:
        row = self._conn().execute("SELECT size, mtime, data FROM metrics WHERE path = ?", (f["path"],)).fetchone()
        if row and row[0] == f["size"] and abs(row[1] - f["mtime"]) < 1e-6:
            return json.loads(row[2])
        return {}

    def _cache(self, f: dict, data: dict) -> None:
        self._conn().execute("INSERT OR REPLACE INTO metrics(path, size, mtime, data) VALUES (?,?,?,?)",
                             (f["path"], f["size"], f["mtime"], json.dumps(data)))

    # ── scan ───────────────────────────────────────────────────────────
    def scan(self, args: dict, **_: Any) -> str:
        verdict = guard.check(str(args.get("folder") or ""), _session_dir())
        recursive = bool(args.get("include_subfolders"))
        echo = {"folder": args.get("folder"), "include_subfolders": recursive}
        if not verdict.allowed:
            return _err(f"refused: {verdict.reason}", requested=echo,
                        rule="Spacewise only scans folders inside your home folder or on external drives, never system, "
                             "app, hidden, locked or library folders. Ask the user for a specific folder; do not try another path on your own.")
        root = verdict.path
        try:
            started = time.time(); deadline = started + TIME_BUDGET
            skipped: dict[str, list] = {}
            files = []
            for p, st in scan.walk(root, recursive, skipped):
                files.append({"path": str(p), "size": st.st_size, "mtime": st.st_mtime, "dev": st.st_dev,
                              "ino": st.st_ino, "nlink": st.st_nlink, "kind": scan.kind_of(p)})
            media = [f for f in files if f["kind"]]
            others = [f for f in files if not f["kind"]]
            # measurements (cached across calls, so a big folder finishes over a few calls)
            pairs = scan.raw_pair_stems(media)
            photos = []
            remaining = measured = 0
            for f in media:
                f.update({k: v for k, v in self._cached(f).items() if k in ("partial", "full")})
                if f["kind"] != "photo" or scan.is_screenshot(Path(f["path"])):
                    continue
                if (str(Path(f["path"]).parent), Path(f["path"]).stem.lower()) in pairs:
                    f["in_raw_pair"] = True
                    continue
                m = self._cached(f).get("m")
                if m is None:
                    if measured >= MIN_PER_CALL and time.time() > deadline:
                        remaining += 1; continue
                    measured += 1
                    m = scan.measure(Path(f["path"]))
                    self._cache(f, {**self._cached(f), "m": m})
                f.update(m); photos.append(f)
            self._conn().commit()
            dups, hashed = scan.exact_duplicates(media, deadline)
            for f in media:
                if f.get("partial") or f.get("full"):
                    self._cache(f, {**self._cached(f), "partial": f.get("partial"), "full": f.get("full")})
            self._conn().commit()
            if remaining or not hashed:
                self._state_set("pending_scan", {"folder": str(root), "include_subfolders": recursive})
                return _ok({"in_progress": True, "scanned_exactly": _display(str(root)), "include_subfolders": recursive,
                            "files_seen": len(files), "photos_left_to_measure": remaining,
                            "next_step": "Large folder: work is saved as it goes. Tell the user the progress and call "
                                         "spacewise_scan again with the same folder to continue. Nothing was moved."})
            self._state_set("pending_scan", None)
            plan = self._build_plan(root, recursive, files, media, others, photos, dups, skipped)
            plan["seconds"] = round(time.time() - started, 1)
            self._save("plans", plan); self._state_set("last_plan", plan["id"])
            plan["report"] = self._write_report(plan)
            self._save("plans", plan)
            return _ok(self._summary(plan))
        except Exception as exc:
            return _err(f"scan failed: {exc}")

    def _build_plan(self, root, recursive, files, media, others, photos, dups, skipped) -> dict:
        home = Path(os.path.realpath(Path.home()))
        slim = lambda f: {k: f[k] for k in ("path", "size", "mtime", "kind") if k in f}
        exact, taken, in_group = [], set(), set()   # taken: proposed extras; in_group: every name of a duplicate
        linked = {(f["dev"], f["ino"]) for f in files if f.get("nlink", 1) > 1}
        for g in sorted(dups, key=lambda g: -g[0]["size"] * (len(g) - 1)):
            keep = scan.choose_keeper(g, home)
            extra = [f for f in g if f is not keep and (f["dev"], f["ino"]) not in linked]
            in_group.update(f["path"] for f in files if (f["dev"], f["ino"]) in {(x["dev"], x["ino"]) for x in g})
            taken.update(f["path"] for f in extra)
            if not extra:
                continue
            exact.append({"keep": slim(keep), "extra": [slim(f) for f in extra]})
        installers = [slim(f) for f in media if f["kind"] == "installer" and f["path"] not in taken and f.get("nlink", 1) == 1]
        similar, keepers = [], set()
        for g in scan.similar_groups([p for p in photos if p["path"] not in in_group]):
            keep = max(g, key=lambda p: (p.get("nlink", 1) > 1, p["sharpness"], p["size"]))
            extra = [p for p in g if p is not keep and p.get("nlink", 1) == 1]
            if not extra:
                continue
            similar.append({"keep": slim(keep), "extra": [slim(p) for p in extra]})
            keepers.add(keep["path"]); taken.update(p["path"] for p in extra)
        blurry = []
        for p in scan.one_per_inode(photos):
            if p["path"] in taken or p["path"] in in_group or p["path"] in keepers or "error" in p or p.get("nlink", 1) > 1:
                continue
            why = []
            if p.get("sharpness", 1) < scan.BLUR_MAX:
                why.append("blurry: nothing in focus")
            if p.get("brightness", 255) < scan.DARK_MEAN:
                why.append("almost entirely black")
            if why:
                blurry.append({**slim(p), "why": "; ".join(why)})
        large_videos = sorted((slim(f) for f in media if f["kind"] == "video" and f["size"] >= scan.LARGE_VIDEO and f["path"] not in taken),
                              key=lambda f: -f["size"])
        screenshots = [slim(f) for f in media if f["kind"] == "photo" and scan.is_screenshot(Path(f["path"]))]
        large_files = sorted(({**slim(f), "group": scan.other_group(Path(f["path"]))} for f in others if f["size"] >= scan.LARGE_OTHER),
                             key=lambda f: -f["size"])
        tot = lambda items: {"files": len(items), "bytes": sum(f["size"] for f in items)}
        green = [x for g in exact for x in g["extra"]] + installers
        amber = [x for g in similar for x in g["extra"]] + blurry
        grey = large_videos + screenshots + large_files
        return {"id": uuid.uuid4().hex[:8], "created": time.time(), "folder": str(root),
                "folder_display": _display(str(root)), "include_subfolders": recursive, "files_seen": len(files),
                "summary": {"green": tot(green), "amber": tot(amber), "grey": tot(grey)},
                "groups": {"exact_duplicates": exact, "installers": installers, "similar_photos": similar,
                           "blurry_or_dark": blurry, "large_videos": large_videos, "screenshots": screenshots,
                           "large_files": large_files},
                "skipped": skipped, "applied": []}

    def _summary(self, plan: dict) -> dict:
        g = plan["groups"]; names = lambda items: [f"{Path(f['path']).name} ({gb(f['size'])})" for f in items[:LIST_LIMIT]]
        radar: dict[str, dict] = {}
        for f in g["large_files"]:
            r = radar.setdefault(f["group"], {"files": 0, "bytes": 0}); r["files"] += 1; r["bytes"] += f["size"]
        cat = lambda items: {"files": len(items), "size": gb(sum(f["size"] for f in items))}
        return {
            "scan_id": plan["id"],
            "scanned_exactly": plan["folder_display"], "include_subfolders": plan["include_subfolders"],
            "files_seen": plan["files_seen"], "seconds": plan.get("seconds"),
            "green_safe_to_free": {"total": gb(plan["summary"]["green"]["bytes"]),
                                   "exact_duplicates": {**cat([x for d in g["exact_duplicates"] for x in d["extra"]]),
                                                        "examples": [f"{Path(d['extra'][0]['path']).name} = copy of {Path(d['keep']['path']).name}"
                                                                     for d in g["exact_duplicates"][:LIST_LIMIT]]},
                                   "installers": {**cat(g["installers"]), "examples": names(g["installers"])}},
            "amber_check_first": {"total": gb(plan["summary"]["amber"]["bytes"]),
                                  "similar_photos": {**cat([x for d in g["similar_photos"] for x in d["extra"]]),
                                                     "groups": len(g["similar_photos"]),
                                                     "examples": [f"keeps {Path(d['keep']['path']).name}, "
                                                                  f"{len(d['extra'])} similar" for d in g["similar_photos"][:LIST_LIMIT]]},
                                  "blurry_or_dark": {**cat(g["blurry_or_dark"]),
                                                     "examples": [f"{Path(f['path']).name}: {f['why']}" for f in g["blurry_or_dark"][:LIST_LIMIT]]}},
            "grey_for_the_user_to_decide": {"total": gb(plan["summary"]["grey"]["bytes"]),
                                            "large_videos": {**cat(g["large_videos"]), "examples": names(g["large_videos"])},
                                            "screenshots": cat(g["screenshots"]),
                                            "other_large_files_by_type": {k: {"files": v["files"], "size": gb(v["bytes"])} for k, v in radar.items()},
                                            "note": "Reported only. No tool can move these. Names of other large files stay in the local report."},
            "not_looked_at": {k: v[0] for k, v in plan["skipped"].items()},
            "report": plan.get("report"),
            "rules_for_you": ("Report these numbers as given. Do not move anything: offer spacewise_preview so the user can look, "
                              "and call spacewise_trash only when the user's current message asks for specific categories "
                              "(exact_duplicates, installers, similar_photos, blurry_or_dark). Grey items cannot be moved."),
        }

    def _write_report(self, plan: dict) -> str:
        d = self._data_dir() / "reports"; d.mkdir(parents=True, exist_ok=True)
        g = plan["groups"]; L = [f"# Spacewise report: {plan['folder_display']}", "",
                                 f"Scan {plan['id']}, {time.strftime('%Y-%m-%d %H:%M')}, subfolders: {plan['include_subfolders']}", ""]
        def section(title, rows):
            L.extend([f"## {title}", ""] + (rows or ["(none)"]) + [""])
        section("Exact duplicates (one copy stays)", [f"- keep `{x['keep']['path']}`; copies: " + ", ".join(f"`{e['path']}`" for e in x["extra"]) for x in g["exact_duplicates"]])
        section("Leftover installers", [f"- `{f['path']}` ({gb(f['size'])})" for f in g["installers"]])
        section("Similar photos (sharpest stays)", [f"- keep `{x['keep']['path']}`; similar: " + ", ".join(f"`{Path(e['path']).name}`" for e in x["extra"]) for x in g["similar_photos"]])
        section("Blurry or near-black photos", [f"- `{f['path']}`: {f['why']}" for f in g["blurry_or_dark"]])
        section("Large videos (reported only)", [f"- `{f['path']}` ({gb(f['size'])})" for f in g["large_videos"]])
        section("Screenshots (reported only)", [f"- `{f['path']}`" for f in g["screenshots"]])
        section("Other large files (reported only, never moved)", [f"- `{f['path']}` ({gb(f['size'])}, {f['group']})" for f in g["large_files"]])
        section("Not looked at", [f"- {k}: {v[0]}" for k, v in plan["skipped"].items()])
        path = d / f"{plan['id']}.md"; path.write_text("\n".join(L), encoding="utf-8")
        for old in sorted(d.glob("*.md"), key=lambda p: p.stat().st_mtime)[:-20]:
            old.unlink(missing_ok=True)
        return str(path)

    # ── preview ────────────────────────────────────────────────────────
    def preview(self, args: dict, **_: Any) -> str:
        plan = self._load("plans", args.get("scan_id"), "last_plan")
        if plan is None:
            return _err("no scan found; run spacewise_scan first")
        try:
            base = self._data_dir() / "previews"; out = base / plan["id"]
            page = preview.build(plan, out)
            sets = sorted((p for p in base.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime)
            import shutil
            for old in sets[:-KEEP_PREVIEWS]:
                shutil.rmtree(old, ignore_errors=True)
            return _ok({"scan_id": plan["id"], "page": str(page),
                        "how_to_open": "Tell the user to open this file in a browser (Finder: Go > Go to Folder, or drag it into a browser).",
                        "privacy": "The page and its small thumbnails stay in Spacewise's data folder on this computer and are removed after the next scans."})
        except Exception as exc:
            return _err(f"preview failed: {exc}")

    # ── trash (approval-gated) ─────────────────────────────────────────
    def _selection(self, args: dict):
        extra = sorted(set(args) - {"scan_id", "categories"})
        if extra:
            return None, None, (f"only scan_id and categories are accepted (got {extra}); Spacewise never takes a "
                                "list of files from the agent")
        plan = self._load("plans", args.get("scan_id"), "last_plan")
        if plan is None:
            return None, None, "no scan found; run spacewise_scan first"
        if args.get("scan_id") and args["scan_id"] != plan["id"]:
            return None, None, "unknown scan_id"
        cats = args.get("categories") or []
        if isinstance(cats, str):
            cats = [cats]
        bad = [c for c in cats if c not in TRASHABLE]
        if not cats or bad:
            return None, None, (f"categories must be chosen from {list(TRASHABLE)}; large videos, screenshots and other large "
                                f"files cannot be moved by Spacewise" + (f" (got {bad})" if bad else ""))
        done = [c for c in cats if c in plan["applied"]]
        if done:
            return None, None, f"already moved to the Trash for this scan: {done}; run a new scan first"
        g = plan["groups"]; items = []
        for c in sorted(set(cats)):
            if c in ("exact_duplicates", "similar_photos"):
                items += [{"cat": c, **x, "keeper": grp["keep"]["path"], "keeper_size": grp["keep"]["size"],
                           "keeper_mtime": grp["keep"]["mtime"]} for grp in g[c] for x in grp["extra"]]
            else:
                items += [{"cat": c, **x} for x in g[c]]
        return plan, items, None

    def selection_key(self, args: dict) -> str:
        cats = args.get("categories") or []
        cats = [cats] if isinstance(cats, str) else cats
        return f"{args.get('scan_id') or self._state_get('last_plan')}:{'+'.join(sorted(set(map(str, cats))))}"

    def approval_request(self, args: dict) -> dict:
        """Called from the pre_tool_call hook: block a bad selection, or escalate a valid one to the human gate."""
        plan, items, error = self._selection(args or {})
        if error:
            return {"action": "block", "message": f"Spacewise: {error}"}
        if not items:
            return {"action": "block", "message": "Spacewise: nothing in those categories for this scan"}
        key = self.selection_key(args)
        self._approvals[key] = time.time()
        size = sum(i["size"] for i in items)
        cats = ", ".join(sorted({i["cat"] for i in items}))
        return {"action": "approve",
                "message": (f"Spacewise will move {files(len(items))} ({gb(size)}) from {plan['folder_display']} to the Trash "
                            f"({cats}). They can be put back with spacewise_undo."),
                "rule_key": f"spacewise_trash:{key}:{uuid.uuid4().hex[:6]}"}   # never reusable: 'always' cannot carry over

    def trash(self, args: dict, **_: Any) -> str:
        if not trashbin.supported():
            return _err("moving to the Trash is supported on macOS and Linux only; on this system Spacewise only reports")
        key = self.selection_key(args)
        reached = self._approvals.pop(key, None)
        if reached is None or time.time() - reached > APPROVAL_WINDOW:
            return _err("refused: this request did not pass Hermes' approval gate (Spacewise's pre_tool_call hook); nothing was moved")
        plan, items, error = self._selection(args)
        if error:
            return _err(error)
        moved, skipped = [], []
        for it in items:
            p = Path(it["path"])
            why = self._still_safe(it)
            if why:
                skipped.append({"name": p.name, "why": why}); continue
            try:
                dest = trashbin.move_to_trash(p)
                moved.append({"from": str(p), "to": str(dest), "size": it["size"], "cat": it["cat"]})
            except trashbin.TrashError as exc:
                skipped.append({"name": p.name, "why": str(exc)})
            except OSError as exc:
                skipped.append({"name": p.name, "why": exc.strerror or str(exc)})
        batch = {"id": uuid.uuid4().hex[:8], "created": time.time(), "scan_id": plan["id"], "moved": moved}
        self._save("batches", batch); self._state_set("last_batch", batch["id"])
        plan["applied"] = sorted(set(plan["applied"]) | {i["cat"] for i in items}); self._save("plans", plan)
        return _ok({"moved_to_trash": len(moved), "moved_size": gb(sum(m["size"] for m in moved)), "skipped": skipped[:LIST_LIMIT],
                    "skipped_total": len(skipped), "batch_id": batch["id"],
                    "undo": "spacewise_undo puts every file back where it was. Finder's Put Back does not know these files.",
                    "note": "Space is only released when the user empties the Trash."})

    def _still_safe(self, it: dict) -> Optional[str]:
        p = Path(it["path"])
        try:
            st = os.lstat(p)
        except OSError:
            return "no longer there"
        why = guard.is_hidden_or_locked(p, st)
        if why:
            return why
        if st.st_size != it["size"] or abs(st.st_mtime - it["mtime"]) > 1e-6:
            return "changed since the scan"
        if it.get("keeper"):                                   # the copy that stays must still be there, unchanged
            try:
                kst = os.lstat(it["keeper"])
            except OSError:
                return "the copy that should stay is gone; skipped so one copy always remains"
            if kst.st_size != it["keeper_size"] or abs(kst.st_mtime - it["keeper_mtime"]) > 1e-6:
                return "the copy that should stay has changed; skipped"
        return None

    # ── undo ───────────────────────────────────────────────────────────
    def undo(self, args: dict, **_: Any) -> str:
        batch = self._load("batches", args.get("batch_id"), "last_batch")
        if batch is None or not batch["moved"]:
            return _err("nothing to put back")
        restored, problems = 0, []
        for m in batch["moved"]:
            why = trashbin.restore(Path(m["to"]), Path(m["from"]))
            if why:
                problems.append({"name": Path(m["from"]).name, "why": why})
            else:
                restored += 1
        batch["moved"] = [m for m in batch["moved"] if Path(m["to"]).exists()]
        self._save("batches", batch)
        plan = self._load("plans", batch["scan_id"], "last_plan")
        if plan:
            plan["applied"] = []; self._save("plans", plan)
        return _ok({"put_back": restored, "problems": problems[:LIST_LIMIT], "problems_total": len(problems)})

    # ── slash command: literal, no model involved ──────────────────────
    def slash(self, raw: str = "", **_: Any) -> str:
        parts = (raw or "").strip().split(None, 1)
        sub = parts[0].lower() if parts else "help"
        rest = parts[1].strip() if len(parts) > 1 else ""
        if sub == "guard-test":
            v = guard.check(rest, _session_dir())
            return (f"Guard: {'ALLOWED' if v.allowed else 'REFUSED'} ({v.reason}) for {str(v.path) if v.path else repr(rest)}. "
                    "No file was opened or read.")
        if sub == "scan":
            subfolders = rest.endswith(" --subfolders")
            folder = rest[: -len(" --subfolders")] if subfolders else rest
            data = json.loads(self.scan({"folder": folder, "include_subfolders": subfolders}))
            if "error" in data:
                return f"Spacewise: {data['error']}"
            if data.get("in_progress"):
                return f"Spacewise: still working on {data['scanned_exactly']} ({data['photos_left_to_measure']} photos left). Run the same command again to continue."
            gsum, asum, ysum = data["green_safe_to_free"], data["amber_check_first"], data["grey_for_the_user_to_decide"]
            return (f"Spacewise scanned exactly {data['scanned_exactly']} (subfolders: {data['include_subfolders']}), "
                    f"{data['files_seen']} files in {data['seconds']} s.\n"
                    f"  Safe to free: {gsum['total']} (duplicates {gsum['exact_duplicates']['size']}, installers {gsum['installers']['size']})\n"
                    f"  Check first:  {asum['total']} (similar {asum['similar_photos']['size']}, blurry/dark {asum['blurry_or_dark']['size']})\n"
                    f"  For you to decide (never moved): {ysum['total']}\n"
                    f"Report: {data['report']}\nNothing was moved.")
        if sub == "undo":
            data = json.loads(self.undo({}))
            return f"Spacewise: {data.get('error') or str(data['put_back']) + ' files put back'}"
        return ("Spacewise commands (run as typed, no AI):\n"
                "  /spacewise scan <folder> [--subfolders]   report only, moves nothing\n"
                "  /spacewise guard-test <path>             is this path allowed? reads nothing\n"
                "  /spacewise undo                          put the last moved batch back")

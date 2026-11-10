# Spacewise

**Free up space without the fear of losing what matters.** Spacewise finds what takes space among
your photos, videos and leftover installers, explains each finding, and moves only what you approve
to the Trash, where it can still be put back. It never decides for you.

```text
You:     My Mac is full. Check ~/Downloads.
Hermes:  Scanned exactly ~/Downloads (no subfolders), 1,240 files.
         Safe to free 7.3 GB: 4.1 GB of exact duplicates (one copy of each stays) and 3.2 GB
         of leftover installers.
         Check first 1.1 GB: 3 groups of similar photos (the sharpest stays), 9 blurry photos.
         For you to decide 11.3 GB: 4 large videos, 212 screenshots, 6.3 GB of other large
         files (design files and archives; their names stay in your local report).
         Want to look at the preview first?
You:     Move the duplicates and installers.
Hermes:  [approval prompt] Spacewise will move 41 files (7.3 GB) from ~/Downloads to the Trash.
```

## Quick start

```bash
hermes plugins install ardianreffa/spacewise --enable
```

Then: *"Check ~/Downloads with Spacewise."* Nothing moves until you approve it.

## How it works

1. **You name one folder.** Spacewise scans exactly that folder (subfolders only if you ask) and
   refuses anything outside the areas below.
2. **Three tiers, never a verdict.**

| Tier | What | What happens |
|---|---|---|
| Green: safe to free | Exact duplicates (byte-for-byte; one copy always stays) and leftover `.dmg` / `.pkg` installers | Can be moved to the Trash if you approve |
| Amber: check first | Similar photos shot seconds apart (the sharpest stays) and blurry or near-black photos | Can be moved if you approve, after you look |
| Grey: for you to decide | Large videos (500 MB+), screenshots, and other large files (200 MB+: design files, archives…) | **Reported only. No tool can move them.** |

3. **You look.** `spacewise_preview` builds a local page with small thumbnails: green outlines
   stay, dashed amber ones would go.
4. **You approve.** Moving goes through **Hermes' own approval prompt**, which shows the exact
   number of files and their size. Decline, or let it time out, and nothing happens.
5. **You can undo.** `spacewise_undo` puts every file back where it was.

## Boundaries (enforced in code, before any file is read)

- **Allowed:** folders inside your home folder, and folders on external drives
  (`/Volumes/<drive>` on macOS, `/media`, `/run/media`, `/mnt` on Linux).
- **Refused:** the operating system (`/System`, `/Library`, `/usr`, `/etc`…), disk roots, your
  whole home folder (name a specific folder), other users' folders, app data (`~/Library`,
  `~/Applications`), every hidden folder and file (dot names and the macOS hidden flag),
  locked items (Finder "Locked"), folders you have no permission to read (Spacewise never asks for
  admin rights or changes permissions), symlinks (never followed), and **libraries managed by their
  own app**: Photos, Lightroom catalogs, Final Cut, iMovie, Music, apps, Time Machine backups.
- **Cloud placeholders** (iCloud "Optimize Mac Storage", online-only Google Drive / Dropbox files
  on macOS) are skipped without opening them, so nothing gets downloaded.
- `/spacewise guard-test <path>` tells you whether a path is allowed **without reading anything**.

## Safety

- **The agent cannot pick files.** `spacewise_trash` takes only a scan id and categories from that
  scan; anything else is blocked. It also refuses to run if Spacewise's approval hook was not
  reached.
- **Trash, never delete.** Files are renamed into the Trash **of the same disk** (`~/.Trash`, or
  `<drive>/.Trashes/<uid>` on macOS; the freedesktop Trash on Linux). If that would mean copying to
  another disk, the file is skipped: copying would use more space.
- **Re-checked before moving:** a file that changed since the scan is skipped; for duplicates, the
  copy that stays must still be there, unchanged.
- **Hard links** are one file, never counted as duplicates. **RAW+JPEG pairs** are never split.
- Space is released when you empty the Trash.

## Privacy and disclosure

- **Reads** only image, video and installer files (and the size, date and name of other files) in
  the folder you name, inside the boundaries above. File contents are read only to compare files
  and measure photos, on your computer.
- **Writes** nothing until you approve a move. Then it renames files into the Trash of the same
  disk (on Linux also a `.trashinfo` record). `spacewise_undo` renames them back.
- **Stores** in Hermes' plugin data folder (`~/.hermes/plugin-data/spacewise/`): per-file
  measurements and hashes (path, size, date, sharpness, brightness, a 64-bit image hash, SHA-256),
  the last 10 scans and Trash batches, reports with full file lists, and preview pages with small
  thumbnails (the last two sets are kept).
- **Sent to your model provider** as part of the conversation: the scanned folder path, counts,
  sizes and the names of photos, videos and installers Spacewise lists. **Names of other large
  files are never sent**: only counts and sizes by type; the names stay in the local report.
- **Approval:** moving to the Trash is escalated to Hermes' approval gate through a `pre_tool_call`
  hook. It follows your Hermes approval settings: with YOLO or approvals turned off it does not
  ask, and with `approvals.cron_mode` set to approve, a scheduled run could move files. Each request
  gets a one-time rule, so "always allow" never carries over to a later move. With no person to
  answer, it fails closed.
- No network calls, model calls, shell commands, background processes or telemetry. No
  process-wide settings are changed (HEIC is opened directly, Pillow's limits are left alone).

## Limitations

- Videos are compared byte-for-byte only: Spacewise does not decode video, so it cannot find
  "similar" or blurry videos.
- It cannot see eyes, expressions or meaning: amber findings need your eyes. Hermes can take a
  visual look at a photo if you ask (that sends the image to your model provider).
- Finder's **Put Back** does not know files moved this way; use `spacewise_undo`.
- **Windows:** scanning and reports only; moving to the Recycle Bin is not supported yet.

## Tools

| Tool | What it does | Changes files? |
|---|---|---|
| `spacewise_scan` | Scans one named folder and reports the three tiers. Resumes on large folders. | No |
| `spacewise_preview` | Builds the local review page with thumbnails. | No (writes only to its data folder) |
| `spacewise_trash` | Moves chosen categories of a scan to the Trash, after Hermes' approval prompt. | Yes, with approval |
| `spacewise_undo` | Puts the last (or a given) batch back. | Moves files back |

`/spacewise scan <folder> [--subfolders]`, `/spacewise guard-test <path>` and `/spacewise undo` run
exactly as typed, without the model.

## Development

```bash
python -m pytest
hermes plugins validate . --install-deps
hermes plugins doctor . --ci
```

## License

MIT

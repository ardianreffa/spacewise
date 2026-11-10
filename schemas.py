"""Tool schemas: what the model reads. Narrow on purpose: fixed choices, no free-text instructions."""

SCAN = {
    "name": "spacewise_scan",
    "description": (
        "Find what takes space among photos, videos and leftover installers in ONE folder the user named, and "
        "report it in three tiers: green (exact duplicates, one copy always stays; leftover .dmg/.pkg installers), "
        "amber (similar photos where the sharpest stays; blurry or near-black photos), grey (large videos, "
        "screenshots and other large files: reported only, never movable). Moves nothing. Use the folder exactly as "
        "the user wrote it; never pick, guess or widen a folder yourself, and do not include subfolders unless the "
        "user asked. Spacewise refuses system, app-data, hidden, locked and library folders, the whole home folder "
        "and disk roots; if it refuses, tell the user why and ask them, do not try another path. Results start with "
        "'scanned_exactly': repeat it to the user so they can confirm it is what they meant. Large folders may return "
        "in_progress: call again with the same folder to continue."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "folder": {"type": "string", "description": "The folder exactly as the user named it, e.g. ~/Downloads."},
            "include_subfolders": {"type": "boolean", "description": "Only true if the user explicitly asked to include subfolders."},
        },
        "required": ["folder"],
    },
}

PREVIEW = {
    "name": "spacewise_preview",
    "description": (
        "Build a local review page (small thumbnails and lists, in Spacewise's own data folder) for the latest scan, so "
        "the user can look before deciding. Moves nothing and sends nothing anywhere. Give the user the page path to open."
    ),
    "parameters": {"type": "object", "properties": {"scan_id": {"type": "string", "description": "Default: the latest scan."}}},
}

TRASH = {
    "name": "spacewise_trash",
    "description": (
        "Move the files of chosen categories from a scan to the Trash of the same disk. Call it ONLY when the user's "
        "current message asks to remove specific categories; an earlier offer is not consent. You cannot choose files: "
        "only categories from the scan. Every call goes through Hermes' approval prompt, which shows the exact count and "
        "size; nothing happens if the user declines. Grey items (large videos, screenshots, other large files) can never "
        "be moved. Files that changed since the scan are skipped, and for duplicates one copy always stays. Report the "
        "result as given and mention spacewise_undo."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "scan_id": {"type": "string", "description": "The scan_id the categories come from."},
            "categories": {"type": "array", "items": {"type": "string",
                           "enum": ["exact_duplicates", "installers", "similar_photos", "blurry_or_dark"]},
                           "description": "Only the categories the user agreed to."},
        },
        "required": ["scan_id", "categories"],
    },
}

UNDO = {
    "name": "spacewise_undo",
    "description": "Put every file of the last (or a given) Trash batch back where it was. Use it whenever the user asks to restore.",
    "parameters": {"type": "object", "properties": {"batch_id": {"type": "string", "description": "Default: the latest batch."}}},
}

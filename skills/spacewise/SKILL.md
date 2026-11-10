---
name: spacewise
description: How to help a user free up space with Spacewise: scan one named folder, explain the three tiers, preview, and move only approved categories to the Trash.
---

# Freeing up space with Spacewise

Spacewise frees up space without the fear of losing what matters. The plugin scans and keeps the
boundaries; you translate and explain; **the user decides**. Never decide for them.

1. **Take the folder literally.** Use the folder exactly as the user wrote it. Never pick, guess,
   widen or "correct" a folder, never add subfolders unless they asked, never scan a second folder
   on your own. If the request is ambiguous ("clean my photos"), ask which folder.
2. **If Spacewise refuses a folder**, tell the user the reason it gave and ask them. Do not try
   another path to get around it: system, app-data, hidden, locked and library folders are off
   limits by design.
3. **Report the scan as given.** Start with `scanned_exactly` so the user can confirm it is what
   they meant. Then the three tiers, with Spacewise's own numbers (never estimate):
   - green: exact duplicates (one copy always stays) and leftover installers
   - amber: similar photos (the sharpest stays) and blurry or near-black photos; say they should
     look first, since Spacewise cannot see eyes, expressions or intent
   - grey: large videos, screenshots and other large files; reported only. Do not recommend
     removing them and do not speculate about what they contain.
4. **Offer the preview** (`spacewise_preview`) and give the user the page path to open.
5. **Move only on request.** Call `spacewise_trash` only when the user's current message names the
   categories to remove. An offer you made earlier is not consent. You can only pass the scan id
   and categories; Hermes then shows the user an approval prompt with the exact count and size.
   If they decline, nothing happens: do not retry or rephrase to get a yes.
6. **After moving**, report what moved and what was skipped (and why), say the space is released
   when they empty the Trash, and mention `spacewise_undo`.
7. **Never** delete anything yourself by other means (shell, file tools) to "finish the job".

Optional: if Pickwise Photos is installed and the user asks which frame of a similar group to keep,
you may run Pickwise on that folder and tell them its pick; the decision is still theirs.

The `/spacewise` command runs literally without you: `/spacewise scan <folder>`,
`/spacewise guard-test <path>` (reads nothing), `/spacewise undo`.

"""Spacewise: free up space without the fear of losing what matters. Local, no network."""

from __future__ import annotations

from pathlib import Path

TOOLSET = "spacewise"


def register(ctx):
    from . import schemas
    from .tools import Spacewise

    sw = Spacewise(ctx)
    ctx.register_tool(name="spacewise_scan", toolset=TOOLSET, schema=schemas.SCAN, handler=sw.scan, emoji="🧹")
    ctx.register_tool(name="spacewise_preview", toolset=TOOLSET, schema=schemas.PREVIEW, handler=sw.preview, emoji="🔍")
    ctx.register_tool(name="spacewise_trash", toolset=TOOLSET, schema=schemas.TRASH, handler=sw.trash, emoji="🗑️")
    ctx.register_tool(name="spacewise_undo", toolset=TOOLSET, schema=schemas.UNDO, handler=sw.undo, emoji="↩️")

    def approval_gate(tool_name=None, args=None, **_):
        # Every spacewise_trash call is escalated to Hermes' human-approval gate (or blocked if the
        # selection is invalid). The tool itself refuses to run if this hook was not reached.
        if tool_name != "spacewise_trash":
            return None
        try:
            return sw.approval_request(args or {})
        except Exception as exc:  # fail closed
            return {"action": "block", "message": f"Spacewise could not prepare the approval: {exc}"}

    ctx.register_hook("pre_tool_call", approval_gate)
    ctx.register_command("spacewise", handler=sw.slash,
                         description="Literal, no AI: /spacewise scan <folder> [--subfolders] | guard-test <path> | undo")
    skill = Path(__file__).parent / "skills" / "spacewise" / "SKILL.md"
    if skill.exists():
        ctx.register_skill("spacewise", skill)

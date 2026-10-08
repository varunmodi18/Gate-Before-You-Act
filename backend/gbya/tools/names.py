"""Tool names by class (plan §D.5). Shared by the policy engine, tool layer and gate."""

from __future__ import annotations

from typing import Literal

READ_ONLY = ("sql_query", "get_context")
ESCALATION = ("request_approval", "ask_analyst", "draft_report")
STATE_CHANGING = ("isolate_host", "kill_process", "disable_account", "block_ip")
ALL_TOOLS = READ_ONLY + ESCALATION + STATE_CHANGING

ToolClass = Literal["read_only", "escalation", "state_changing"]
StateChangingTool = Literal["isolate_host", "kill_process", "disable_account", "block_ip"]


def tool_class(name: str) -> ToolClass | None:
    if name in READ_ONLY:
        return "read_only"
    if name in ESCALATION:
        return "escalation"
    if name in STATE_CHANGING:
        return "state_changing"
    return None

"""MCP client dispatcher for A8 (plan §D.5.3, FR-29, T5.8).

``McpDispatcher`` implements the same ``ToolDispatcher`` interface as the in-process dispatcher:
``list_tools()``, ``call(request) -> CallOutcome``, ``state()``, ``close()``. It starts the tool
server (``gbya.tools.mcp_server``) for one episode as a stdio subprocess and keeps the MCP session
open in a background event loop, because the episode loop is synchronous.

Each call sends the tool's arguments (with ``cited`` merged, as in the tool schemas) plus the
reserved ``_claim``; the server's handler runs the gate and returns the ``CallOutcome``. The
trace events of the call are written to the episode's trace here, with ``transport: "mcp"`` and
the call's round-trip latency on each step, so an Exp 2 episode is still written in one
transaction. A server that dies mid-episode raises ``McpServerError``: the episode fails and the
worker retries the unit.
"""

from __future__ import annotations

import contextlib
import os
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import anyio.from_thread
from mcp import types
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from gbya.config import Settings
from gbya.gate.types import GateDecision
from gbya.tools.dispatcher import CallOutcome, CallRequest
from gbya.tools.mcp_server import CLAIM

APPROX = "approx-test-only"
SETTINGS_ENV = ("env", "app_db_path", "data_dir", "cases_dir", "log_dir", "model_base_url",
                "llm_backend", "fake_llm_delay_ms", "replay_cassette", "reranker_dir")  # fmt: skip


class McpServerError(RuntimeError):
    """The MCP tool server failed or went away mid-episode."""


def server_env(settings: Settings) -> dict[str, str]:
    """The server process gets this run's settings through the ``GBYA_*`` environment."""
    env = dict(os.environ)
    for key in SETTINGS_ENV:
        value = getattr(settings, key)
        if value is None:
            continue
        if key.endswith(("_path", "_dir", "_cassette")):
            value = settings.resolve(value)
        env[f"GBYA_{key.upper()}"] = str(value)
    return env


@asynccontextmanager
async def _connect(params: StdioServerParameters) -> AsyncIterator[ClientSession]:
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        yield session


class McpDispatcher:
    transport = "mcp"

    def __init__(self, params: StdioServerParameters, trace: Any,
                 initial_state: dict[str, Any] | None = None) -> None:  # fmt: skip
        self.trace = trace
        self._state = initial_state or {"approvals": {}, "retrieved": []}
        self._portal_cm = anyio.from_thread.start_blocking_portal()
        self._portal = self._portal_cm.__enter__()
        try:
            self._session_cm = self._portal.wrap_async_context_manager(_connect(params))
            self._session = self._session_cm.__enter__()
        except BaseException as exc:
            self._portal_cm.__exit__(None, None, None)
            raise McpServerError(f"the MCP tool server did not start: {exc}") from exc
        self.latencies_ms: list[float] = []

    @classmethod
    def start(
        cls,
        settings: Settings,
        case_id: str,
        system: str,
        trace: Any,
        *,
        counter_name: str,
        initial_retrieved: list[int] | None = None,
    ) -> McpDispatcher:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "gbya.tools.mcp_server", "--case-id", case_id, "--system", system,
                  "--counter", "approx" if counter_name == APPROX else "model"],
            env=server_env(settings),
        )  # fmt: skip
        state = {"approvals": {}, "retrieved": sorted(initial_retrieved or [])}
        return cls(params, trace, state)

    def list_tools(self) -> list[dict[str, Any]]:
        try:
            res = self._portal.call(self._session.list_tools)
        except Exception as exc:
            raise McpServerError(f"list_tools failed: {exc}") from exc
        return [{"name": t.name, "description": t.description, "input_schema": t.input_schema}
                for t in res.tools]  # fmt: skip

    def raw_call(self, name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        """One MCP tools/call, unchanged (used by the parity and no-bypass tests)."""
        try:
            res = self._portal.call(self._session.call_tool, name, arguments)
        except Exception as exc:
            raise McpServerError(f"tools/call {name} failed: {exc}") from exc
        if not isinstance(res, types.CallToolResult):
            raise McpServerError(f"unexpected result for {name}: {type(res).__name__}")
        return res

    def call(self, req: CallRequest) -> CallOutcome:
        args = {**req.call_args(),
                CLAIM: {"technique_id": req.technique_id, "thought": req.thought}}  # fmt: skip
        t0 = time.perf_counter()
        res = self.raw_call(req.tool, args)
        latency = round((time.perf_counter() - t0) * 1000, 3)
        self.latencies_ms.append(latency)
        data = res.structured_content
        if not isinstance(data, dict):
            raise McpServerError(f"tools/call {req.tool} returned no structured outcome")
        out = CallOutcome(**data)
        self._replay(out, latency)
        self._state = out.state
        return out

    def _replay(self, out: CallOutcome, latency: float) -> None:
        ids: dict[int, int | None] = {}
        for ev in out.events:
            if ev["type"] == "step":
                payload = {**ev["payload"], "transport": "mcp", "call_latency_ms": latency}
                self.trace.step(ev["kind"], payload, ev["ms"])
            elif ev["type"] == "gate_decision":
                decision = GateDecision.model_validate(ev["decision"])
                ids[ev["local_id"]] = self.trace.gate_decision(decision, ev["call"], ev["cited"],
                                                               ev["ms"])  # fmt: skip
            elif ev["type"] == "tool_call":
                local = ev["gate_decision_id"]
                gid = ids.get(local) if local else None
                self.trace.tool_call(ev["tool"], ev["args"], ev["cited"],
                                     admitted=ev["admitted"], gate_decision_id=gid)  # fmt: skip
        if out.executed is not None and out.executed.get("gate_decision_id"):
            out.executed["gate_decision_id"] = ids.get(out.executed["gate_decision_id"])
        out.events = []

    def state(self) -> dict[str, Any]:
        return dict(self._state)

    def close(self) -> None:
        with contextlib.suppress(BaseException):
            self._session_cm.__exit__(None, None, None)
        with contextlib.suppress(BaseException):
            self._portal_cm.__exit__(None, None, None)

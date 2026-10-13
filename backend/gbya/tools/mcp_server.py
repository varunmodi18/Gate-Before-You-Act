"""MCP tool server for A8 (plan §D.5.3, FR-29, T5.8).

    python -m gbya.tools.mcp_server --case-id <case> --system <config> [--counter approx|model]

Started per episode as a stdio subprocess by ``gbya.agent.mcp_client``. It serves the tool
registry — the same names, descriptions and JSON schemas generated from the Pydantic models; no
second implementation of any tool — and holds the episode state (registry of retrieved records,
retry and recovery counters, approvals). **The gate runs inside the tool handler**: every call
goes through the same ``InProcessDispatcher`` as direct calls, so no tool is reachable without it.
``end_episode`` is a control action, not a tool, and is not served.

The agent's claim (technique and rationale, shown to the verifier as a claim only) travels as the
reserved argument ``_claim`` and is removed before the gate sees the arguments. The result
content is the text the model sees; ``isError`` is set when the call was not carried out;
``structuredContent`` carries the ``CallOutcome`` (feedback, termination hints, state and the
trace events, which the loop writes to app.db). Settings come from the ``GBYA_*`` environment.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from gbya.tools.dispatcher import CallRequest, InProcessDispatcher

CLAIM = "_claim"
SERVER_NAME = "gbya-tools"


def build(case_id: str, system: str, counter_name: str) -> tuple[InProcessDispatcher, Any]:
    """The episode's tool side, built exactly as for direct calls (``exp2.episode_env``)."""
    from gbya.config import get_settings
    from gbya.experiments import exp2
    from gbya.experiments.runner import Deps
    from gbya.llm.factory import make_client
    from gbya.llm.tokens import ApproxCounter, default_counter
    from gbya.store.db import make_engine, make_sessionmaker, session_scope

    settings = get_settings()
    counter = ApproxCounter() if counter_name == "approx" else default_counter()
    deps = Deps(settings=settings, client=make_client(settings), counter=counter)
    factory = make_sessionmaker(make_engine(settings.resolve(settings.app_db_path)))
    with session_scope(factory) as s:
        case = exp2.load_episode_case(s, case_id, settings)
    e = exp2.episode_env(case, deps.configs[system], deps, factory)
    return InProcessDispatcher.recording(e.gate, e.env, e.tools), e


def make_server(dispatcher: InProcessDispatcher) -> Server[Any]:
    # fault injection for the crash-recovery test; honoured only in the test environment
    crash_after = (int(os.environ.get("GBYA_MCP_CRASH_AFTER", "0") or 0)
                   if os.environ.get("GBYA_ENV") == "test" else 0)  # fmt: skip
    calls = {"n": 0}

    async def list_tools(_ctx: Any, _params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=[
            types.Tool(name=t["name"], description=t["description"], inputSchema=t["input_schema"])
            for t in dispatcher.list_tools()
        ])  # fmt: skip

    async def call_tool(_ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        calls["n"] += 1
        if crash_after and calls["n"] > crash_after:
            os._exit(3)  # simulates a server crash mid-episode
        args = dict(params.arguments or {})
        claim = args.pop(CLAIM, None) or {}
        cited = args.pop("cited", []) if isinstance(args.get("cited", []), list) else []
        req = CallRequest(tool=params.name, args=args, cited=[int(i) for i in cited],
                          technique_id=claim.get("technique_id"),
                          thought=str(claim.get("thought") or ""))  # fmt: skip
        out = await anyio.to_thread.run_sync(dispatcher.call, req)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=out.feedback)],
            structuredContent=out.to_json(),
            isError=out.is_error,
        )

    return Server(SERVER_NAME, version="1", on_list_tools=list_tools, on_call_tool=call_tool)


async def serve(dispatcher: InProcessDispatcher) -> None:
    server = make_server(dispatcher)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(prog="gbya.tools.mcp_server")
    ap.add_argument("--case-id", required=True)
    ap.add_argument("--system", required=True)
    ap.add_argument("--counter", choices=["approx", "model"], default="model")
    a = ap.parse_args(argv)
    dispatcher, env = build(a.case_id, a.system, a.counter)
    try:
        anyio.run(serve, dispatcher)
    finally:
        env.close()


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except Exception as exc:  # reported on stderr; the client sees the closed connection
        print(json.dumps({"mcp_server_error": f"{type(exc).__name__}: {exc}"}), file=sys.stderr)
        raise

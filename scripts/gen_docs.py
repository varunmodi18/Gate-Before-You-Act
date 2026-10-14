"""Generate the derived documentation (plan T8.5, §J.6):

    uv run python scripts/gen_docs.py          # make docs

* ``docs/api.md`` — every API route from the OpenAPI schema: method, path, summary, parameters
  and request body.
* ``docs/gate_contract.md`` — the normative gate contract, plan §D.6, extracted verbatim.

``tests/unit/test_docs_current.py`` fails when either file is stale, so the documents cannot
drift from the code and the plan.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
API_MD = REPO / "docs" / "api.md"
GATE_MD = REPO / "docs" / "gate_contract.md"


def _first_line(text: str | None) -> str:
    """The docstring's first paragraph on one line."""
    para = (text or "").strip().split("\n\n", 1)[0]
    return " ".join(line.strip() for line in para.splitlines())


def api_markdown() -> str:
    from gbya.api.main import create_app
    from gbya.config import Settings

    with tempfile.TemporaryDirectory() as tmp:
        spec: dict[str, Any] = create_app(Settings(app_db_path=Path(tmp) / "app.db")).openapi()
    lines = ["# API reference", "",
             "Generated from the OpenAPI schema by `scripts/gen_docs.py` (`make docs`); "
             "do not edit.",
             "Every path is under `/api/v1`. Errors use `{\"error\": {\"code\", \"message\", "
             "\"hint\", \"details\"}}` (plan §F.6). Proportions are authoritative; `*_pp` fields "
             "are display only.", ""]  # fmt: skip
    by_tag: dict[str, list[tuple[str, str, dict[str, Any]]]] = {}
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            tag = (op.get("tags") or ["other"])[0]
            by_tag.setdefault(tag, []).append((path, method.upper(), op))
    for tag in sorted(by_tag):
        lines += [f"## {tag}", "", "| Method | Path | Summary | Parameters | Body |",
                  "|---|---|---|---|---|"]  # fmt: skip
        for path, method, op in sorted(by_tag[tag], key=lambda t: (t[0], t[1])):
            params = ", ".join(
                f"`{p['name']}`" + ("" if p.get("required") else "?")
                for p in op.get("parameters", [])
            )
            body = ""
            content = (op.get("requestBody") or {}).get("content", {})
            schema = (content.get("application/json") or {}).get("schema", {})
            if "$ref" in schema:
                body = f"`{schema['$ref'].rsplit('/', 1)[-1]}`"
            elif schema:
                body = "JSON"
            summary = _first_line(op.get("description")) or op.get("summary", "")
            summary = summary.replace("|", "\\|")
            lines.append(f"| {method} | `{path.removeprefix('/api/v1')}` | {summary} | "
                         f"{params} | {body} |")  # fmt: skip
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def gate_markdown() -> str:
    plan = (REPO / "IMPLEMENTATION_PLAN.md").read_text(encoding="utf-8")
    start = plan.index("### D.6 `gbya.gate`")
    end = plan.index("### D.7 ", start)
    body = (
        plan[start:end].strip().replace("### D.6 ", "## D.6 ", 1).replace("#### D.6.", "### D.6.")
    )
    head = ("# Gate contract (normative)\n\n"
            "Plan §D.6 extracted verbatim by `scripts/gen_docs.py` (`make docs`); do not edit — "
            "change the plan, then regenerate. The C4 verifier is §D.7, the policy §D.8 "
            "(`docs/policy_table.md`), outcomes §D.9. One gate implementation (`gbya.gate`) serves "
            "the experiments, the Playground and the Console (§L.4 item 1).\n")  # fmt: skip
    return head + "\n" + body + "\n"


def main() -> None:
    API_MD.write_text(api_markdown(), encoding="utf-8")
    GATE_MD.write_text(gate_markdown(), encoding="utf-8")
    print(f"wrote {API_MD.relative_to(REPO)} and {GATE_MD.relative_to(REPO)}")


if __name__ == "__main__":
    main()

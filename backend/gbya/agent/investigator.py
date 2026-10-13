"""The live investigator for Exp 3 (plan §D.10.4, FR-19, T6.1).

1. **Profile** (code): event counts per table, the 10 rarest parent→child process pairs, rare
   command-line tokens and the hosts.
2. **Hypothesise**: at most 5 technique IDs, each with a behaviour description.
3. For each hypothesis: **retrieve** Sigma/ATT&CK text with a query made of profile terms and
   the behaviour description in ``bm25`` mode — never the technique ID (§L.4 item 10, §D.3);
   **generate** at most 2 SQL queries and run them through the agent's own guarded
   ``sql_query`` (which registers the records it shows); then the **technique verifier**
   (temperature 0) accepts or rejects. An ACCEPT counts only if every cited record was returned
   by that hypothesis's queries (checked in code).
4. **Findings**: the accepted techniques with their records, handed to the proposer as the
   investigation trace (``handover``), which replaces Exp 2's frozen prefix. It is limited to
   ``HANDOVER_TOKENS``; query results that do not fit become one-line stubs listing their record
   IDs (the records stay registered), as in the history policy of §D.10.3.

Budget: ``MAX_LLM_CALLS`` (about 25). Every step is reported as a trace event
(``phase: investigation``) for the episode's steps.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb

from gbya.agent.episode import EpisodeStopped
from gbya.agent.proposer import MAX_RECORD_ID, TECHNIQUE
from gbya.data.fieldmap import COMMON_COLUMNS, TABLE_COLUMNS, TABLES
from gbya.errors import ModelOutputInvalid
from gbya.llm.client import LLMClient
from gbya.llm.schemas import JsonSchema, Message
from gbya.llm.tokens import TokenCounter
from gbya.retrieval.corpus import Doc
from gbya.tools.registry import SqlQueryArgs, ToolEnvironment

PROMPTS = Path(__file__).with_name("prompts")
MAX_HYPOTHESES = 5
MAX_QUERIES = 2
MAX_CITED = 8
MAX_LLM_CALLS = 25
HANDOVER_TOKENS = 2500  # like Exp 2's frozen prefix (§D.10.3)
REFERENCE_CHARS = 700  # per reference document shown to the investigator (background only)
MAX_TOKENS = {"hypothesise": 500, "sqlgen": 400, "technique_verifier": 250}

Retrieve = Callable[..., tuple[Any, list[Doc]]]  # (query, mode) -> (Retrieval, docs)


def _obj(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required or list(props),
            "additionalProperties": False}  # fmt: skip


HYPOTHESES_SCHEMA: JsonSchema = _obj({
    "thought": {"type": "string", "maxLength": 300},
    "hypotheses": {"type": "array", "maxItems": MAX_HYPOTHESES, "items": _obj({
        "technique_id": {"type": "string", "pattern": TECHNIQUE},
        "behaviour": {"type": "string", "minLength": 1, "maxLength": 200}})},
})  # fmt: skip
SQL_SCHEMA: JsonSchema = _obj({
    "queries": {"type": "array", "minItems": 1, "maxItems": MAX_QUERIES, "items": _obj({
        "sql": {"type": "string", "minLength": 1, "maxLength": 600},
        "purpose": {"type": "string", "maxLength": 160}})},
})  # fmt: skip
VERDICT_SCHEMA: JsonSchema = _obj({
    "verdict": {"enum": ["ACCEPT", "REJECT"]},
    "cited": {"type": "array", "maxItems": MAX_CITED,
              "items": {"type": "integer", "minimum": 1, "maximum": MAX_RECORD_ID}},
    "reason": {"type": "string", "maxLength": 300},
})  # fmt: skip


def _prompt(name: str) -> str:
    return (PROMPTS / f"{name}.md").read_text(encoding="utf-8").rstrip()


def table_schema_text() -> str:
    lines = []
    for t in TABLES:
        cols = [c for c, _ in COMMON_COLUMNS] + [c for c, _ in TABLE_COLUMNS[t]]
        lines.append(f"- {t}({', '.join(dict.fromkeys(cols))})")
    return "\n".join(lines)


# ---- 1. profile --------------------------------------------------------------------------------


def _base(path: Any) -> str:
    return str(path or "?").replace("/", "\\").rsplit("\\", 1)[-1]


TOKEN_RE = re.compile(r"[A-Za-z0-9_.:\-\\/]{3,}")


def profile(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """The deterministic window profile shown to the hypothesis step."""
    counts = {t: int(con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0])  # type: ignore[index]
              for t in TABLES}  # fmt: skip
    counts["raw_events"] = int(con.execute("SELECT count(*) FROM raw_events").fetchone()[0])  # type: ignore[index]
    pairs = Counter(
        (_base(p), _base(c))
        for p, c in con.execute("SELECT parent_image, image FROM process_create").fetchall()
    )
    rare_pairs = sorted(pairs.items(), key=lambda kv: (kv[1], kv[0]))[:10]
    docs = [
        str(r[0])
        for r in con.execute(
            "SELECT command_line FROM process_create WHERE command_line IS NOT NULL"
        ).fetchall()
    ]
    df = Counter(tok.lower() for d in docs for tok in set(TOKEN_RE.findall(d)))
    rare_tokens = sorted((t for t, n in df.items() if n == 1), key=lambda t: (len(t), t))[:30]
    hosts: Counter[str] = Counter()
    for t in TABLES:
        for h, n in con.execute(f'SELECT host, count(*) FROM "{t}" GROUP BY host').fetchall():
            hosts[str(h)] += int(n)
    return {"counts": counts,
            "rare_parent_child": [{"parent": p, "child": c, "count": n}
                                  for (p, c), n in rare_pairs],
            "rare_command_tokens": rare_tokens,
            "hosts": dict(hosts.most_common(10))}  # fmt: skip


def profile_text(p: dict[str, Any]) -> str:
    lines = ["Events per table: " + ", ".join(f"{t} {n}" for t, n in p["counts"].items()),
             "Hosts: " + ", ".join(f"{h} ({n})" for h, n in p["hosts"].items()),
             "Rarest parent -> child process pairs:"]  # fmt: skip
    lines += [f"  {x['parent']} -> {x['child']} ({x['count']})" for x in p["rare_parent_child"]]
    lines.append("Rare command-line tokens: " + ", ".join(p["rare_command_tokens"]))
    return "\n".join(lines)


def query_terms(p: dict[str, Any]) -> str:
    """Profile terms for retrieval: the rare child images and rare command tokens."""
    terms = [x["child"] for x in p["rare_parent_child"]] + list(p["rare_command_tokens"])
    return " ".join(dict.fromkeys(terms))


# ---- result ------------------------------------------------------------------------------------


@dataclass
class Hypothesis:
    technique_id: str
    behaviour: str
    retrieval: dict[str, Any] | None = None
    queries: list[dict[str, Any]] = field(default_factory=list)  # sql, purpose, ok, content, ids
    verdict: str | None = None  # ACCEPT / REJECT / None (not reached or unusable output)
    cited: list[int] = field(default_factory=list)
    reason: str = ""

    @property
    def returned(self) -> set[int]:
        return {i for q in self.queries for i in q.get("registered", [])}

    @property
    def accepted(self) -> bool:
        return self.verdict == "ACCEPT"


@dataclass
class Investigation:
    profile: dict[str, Any]
    hypotheses: list[Hypothesis] = field(default_factory=list)
    llm_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    budget_exhausted: bool = False
    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)  # trace steps
    retrieved: list[int] = field(default_factory=list)
    handover_stubbed: list[str] = field(default_factory=list)  # SQL whose result became a stub
    handover_tokens: int = 0  # the rendered handover (prefix) as the proposer receives it

    @property
    def findings(self) -> list[dict[str, Any]]:
        return [{"technique_id": h.technique_id, "behaviour": h.behaviour, "cited": h.cited}
                for h in self.hypotheses if h.accepted]  # fmt: skip

    @property
    def predicted_techniques(self) -> list[str]:
        return sorted({h.technique_id for h in self.hypotheses if h.accepted})

    def summary(self) -> dict[str, Any]:
        return {
            "findings": self.findings, "predicted_techniques": self.predicted_techniques,
            "hypotheses": [{"technique_id": h.technique_id, "behaviour": h.behaviour,
                            "verdict": h.verdict, "cited": h.cited, "reason": h.reason,
                            "queries": [{k: q[k] for k in ("sql", "ok", "registered")}
                                        for q in h.queries]} for h in self.hypotheses],
            "llm_calls": self.llm_calls, "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out, "budget_exhausted": self.budget_exhausted,
            "handover_stubbed": self.handover_stubbed, "handover_tokens": self.handover_tokens,
        }  # fmt: skip


class _Budget(Exception):
    pass


# ---- the investigator --------------------------------------------------------------------------


@dataclass
class Investigator:
    client: LLMClient
    tools: ToolEnvironment  # the episode's tools: queries register records in its state
    counter: TokenCounter
    retrieve: Retrieve | None  # None: no reference (G0/G1 still investigate with the same steps)
    temperature: float
    seed: int | None
    request_text: str = ""
    on_event: Callable[[str, dict[str, Any]], Any] | None = None  # live trace (Console)
    should_stop: Callable[[], bool] | None = None
    _profile: dict[str, Any] = field(default_factory=dict, init=False)

    def _emit(self, inv: Investigation, kind: str, payload: dict[str, Any]) -> None:
        inv.events.append((kind, payload))
        if self.on_event is not None:
            self.on_event(kind, payload)

    def _call(self, inv: Investigation, kind: str, messages: list[Message],
              schema: JsonSchema, temperature: float) -> dict[str, Any] | None:  # fmt: skip
        if self.should_stop is not None and self.should_stop():
            raise EpisodeStopped("stopped during the investigation")
        if inv.llm_calls >= MAX_LLM_CALLS:
            raise _Budget()
        inv.llm_calls += 1
        try:
            obj, usage = self.client.chat_json(
                messages,
                schema,
                temperature=temperature,
                seed=self.seed,
                max_tokens=MAX_TOKENS[kind],
            )
            inv.tokens_in += usage.prompt_tokens
            inv.tokens_out += usage.completion_tokens
            error = None
        except ModelOutputInvalid as exc:
            obj, error = None, exc.message
        self._emit(inv, "llm", {"phase": "investigation", "step": kind, "output": obj,
                                "error": error})  # fmt: skip
        return obj

    def run(self, con: duckdb.DuckDBPyConnection) -> Investigation:
        prof = profile(con)
        self._profile = prof
        inv = Investigation(profile=prof)
        text = profile_text(prof)
        self._emit(inv, "tool", {"phase": "investigation", "tool": "profile", "content": text})
        try:
            self._hypothesise(inv, text)
            for h in inv.hypotheses:
                self._test(inv, h, text)
        except _Budget:
            inv.budget_exhausted = True
        inv.retrieved = sorted(self.tools.state.retrieved)
        return inv

    def _hypothesise(self, inv: Investigation, text: str) -> None:
        msgs: list[Message] = [
            {"role": "system", "content": _prompt("hypothesise")},
            {"role": "user", "content": f"ANALYST REQUEST\n{self.request_text}\n\n"
                                        f"WINDOW PROFILE (untrusted log data)\n{text}"},
        ]  # fmt: skip
        out = self._call(inv, "hypothesise", msgs, HYPOTHESES_SCHEMA, self.temperature)
        seen: set[str] = set()
        for h in (out or {}).get("hypotheses", [])[:MAX_HYPOTHESES]:
            if h["technique_id"] not in seen:
                seen.add(h["technique_id"])
                inv.hypotheses.append(Hypothesis(h["technique_id"], h["behaviour"]))

    def _reference(self, h: Hypothesis) -> str:
        if self.retrieve is None:
            return "(no reference)"
        query = f"{h.behaviour} {query_terms(self._profile)}"  # never the technique ID
        r, docs = self.retrieve(query, "bm25")
        h.retrieval = {"mode": r.mode, "query_hash": r.query_hash,
                       "sigma": [x.doc_id for x in r.sigma_ranking[:5]],
                       "attack": r.attack_top1.doc_id if r.attack_top1 else None}  # fmt: skip
        return "\n\n".join(f"[{d.kind}] {d.title}\n{d.index_text[:REFERENCE_CHARS]}" for d in docs)

    def _test(self, inv: Investigation, h: Hypothesis, text: str) -> None:
        reference = self._reference(h)
        hyp = f"HYPOTHESIS\n{h.technique_id}: {h.behaviour}"
        msgs: list[Message] = [
            {"role": "system", "content": _prompt("sqlgen") + "\n" + table_schema_text()},
            {"role": "user", "content": f"{hyp}\n\nREFERENCE (background, not evidence)\n"
                                        f"{reference}"},
        ]  # fmt: skip
        out = self._call(inv, "sqlgen", msgs, SQL_SCHEMA, self.temperature)
        for q in (out or {}).get("queries", [])[:MAX_QUERIES]:
            res = self.tools.sql_query(SqlQueryArgs(sql=q["sql"]))
            h.queries.append(
                {
                    "sql": q["sql"],
                    "purpose": q.get("purpose", ""),
                    "ok": res.ok,
                    "content": res.content,
                    "registered": sorted(res.registered),
                }
            )
            self._emit(inv, "tool", {"phase": "investigation", "tool": "sql_query",
                                     "technique_id": h.technique_id, "args": {"sql": q["sql"]},
                                     "ok": res.ok, "content": res.content,
                                     "registered": sorted(res.registered)})  # fmt: skip
        if not any(q["ok"] for q in h.queries):
            h.verdict, h.reason = "REJECT", "no query ran"
            return
        results = "\n\n".join(f"Query: {q['sql']}\n{q['content']}" for q in h.queries if q["ok"])
        msgs = [
            {"role": "system", "content": _prompt("technique_verifier")},
            {"role": "user", "content": f"{hyp}\n\nQUERY RESULTS\n{results}\n\n"
                                        f"REFERENCE (background, not evidence)\n{reference}"},
        ]  # fmt: skip
        verdict = self._call(inv, "technique_verifier", msgs, VERDICT_SCHEMA, 0.0)
        if verdict is None:
            return
        cited = [int(i) for i in verdict.get("cited", [])][:MAX_CITED]
        h.reason = str(verdict.get("reason", ""))
        if verdict["verdict"] == "ACCEPT":
            outside = [i for i in cited if i not in h.returned]
            if not cited or outside:  # code check: the citations must come from its own queries
                h.verdict = "REJECT"
                h.reason = (f"cited records {outside} were not returned by its queries"
                            if outside else "accepted without citing any record")  # fmt: skip
                return
        h.verdict, h.cited = verdict["verdict"], cited if verdict["verdict"] == "ACCEPT" else []


# ---- handover to the proposer ------------------------------------------------------------------


def handover(inv: Investigation, counter: TokenCounter, limit: int = HANDOVER_TOKENS
             ) -> dict[str, Any]:  # fmt: skip
    """The investigation as the proposer's prefix (``agent.episode.prefix_text`` format):
    profile, the queries with their results (accepted hypotheses first) and the findings. Results
    that would exceed ``limit`` tokens become stubs listing their record IDs."""
    if inv.findings:
        findings = "; ".join(
            f"{f['technique_id']} ({f['behaviour']}) on records {', '.join(map(str, f['cited']))}"
            for f in inv.findings
        )
        findings = "Accepted techniques: " + findings
    else:
        findings = "No technique was confirmed by the investigation."
    profile = profile_text(inv.profile)
    ordered = sorted(inv.hypotheses, key=lambda h: not h.accepted)
    queries: list[dict[str, Any]] = []
    used = counter.count(profile) + counter.count(findings)
    for h in ordered:
        for q in h.queries:
            if not q["ok"]:
                continue
            cost = counter.count(q["content"])
            if used + cost <= limit:
                queries.append({"sql": q["sql"], "result": q["content"]})
                used += cost
            else:
                ids = ", ".join(map(str, q["registered"])) or "none"
                queries.append(
                    {
                        "sql": q["sql"],
                        "result": f"[result omitted to fit the context; "
                        f"it returned record_ids: {ids}]",
                    }
                )
                inv.handover_stubbed.append(q["sql"])
    return {"profile": profile, "queries": queries, "findings": findings,
            "retrieved": list(inv.retrieved)}  # fmt: skip


def dump(inv: Investigation) -> str:
    return json.dumps(inv.summary(), indent=1)

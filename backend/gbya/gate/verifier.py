"""C4 verifier prompt assembly and the rendering manifest (plan §D.7, §D.7.1, T3.2).

The system prompt is ``gbya/agent/prompts/verifier.md``. The user content has fixed blocks in a
fixed order: ``PROPOSED_ACTION``, ``EVIDENCE_REQUIREMENT``, ``CITED_RECORDS`` (the deterministic
evidence rendering of ``evidence.py``, wrapped as untrusted, never cut), ``REFERENCE`` (omitted in
mode ``none``), ``CHANGE_TICKETS``, and ``AGENT_RATIONALE`` (the ``rationale`` variant only).

Verifier variants (§D.7.2): ``standard`` (bm25), ``rationale`` (bm25 + the agent's rationale),
``none`` (no reference) and ``rerank`` (bm25_rerank).

Budgets (§D.7.1, model tokenizer): system ≤ 600, action and requirement ≤ 200, cited records
≤ 3,200, reference ≤ 1,800, tickets ≤ 400, rationale ≤ 200. Only reference documents are cut, each
at 300 tokens — a stated retrieval-design choice recorded in the manifest. Any other per-call
block over its budget raises ``PromptBudgetError``: nothing else is ever trimmed. The system
prompt is a fixed file whose budget is checked with the model tokenizer by the test suite.

The verifier never sees ``technique_gold``, the case variant, labels, asset tiers or approval
state: none of them is an input of ``build_prompt``.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import ValidationError

from gbya.context.models import ChangeTicket, TrustedContext
from gbya.errors import GbyaError, ModelOutputInvalid
from gbya.gate.evidence import OMITTED_FIELDS, CitedRecord, RenderedEvidence
from gbya.gate.ticket_scope import code_ticket_scope
from gbya.gate.types import Claim, VerifierCall, VerifierOutput
from gbya.llm.client import LLMClient
from gbya.llm.schemas import JsonSchema, Message, prompt_hash
from gbya.llm.tokens import TokenCounter
from gbya.retrieval.corpus import Doc
from gbya.retrieval.index import Reranker, Retrieval, RetrievalIndex
from gbya.retrieval.query import build_query

if TYPE_CHECKING:
    from gbya.gate.checks import GateEnv

Variant = Literal["standard", "rationale", "none", "rerank"]
VARIANT_MODE: dict[str, Literal["none", "bm25", "bm25_rerank"]] = {
    "standard": "bm25", "rationale": "bm25", "none": "none", "rerank": "bm25_rerank",
}  # fmt: skip
SYSTEM_PROMPT_PATH = Path(__file__).resolve().parents[1] / "agent" / "prompts" / "verifier.md"
BUDGETS = {"system": 600, "action": 200, "cited": 3200, "reference": 1800, "tickets": 400,
           "rationale": 200, "output": 200}  # fmt: skip
DOC_TOKENS = 300
TEMPERATURE = 0.0
MAX_TOKENS = BUDGETS["output"]
CUT_MARK = " [cut at 300 tokens]"

VERIFIER_SCHEMA: JsonSchema = {
    "type": "object",
    "properties": {
        "verdict": {"enum": ["SUPPORTS", "INSUFFICIENT", "CONTRADICTED"]},
        "unmet_requirement": {"type": ["string", "null"]},
        "ticket_scope": {
            "type": "object",
            "properties": {
                "applies": {"type": "boolean"},
                "matches": {
                    "type": "object",
                    "properties": {
                        k: {"type": "boolean"} for k in ("host", "account", "command", "time")
                    },
                    "required": ["host", "account", "command", "time"],
                    "additionalProperties": False,
                },
            },
            "required": ["applies", "matches"],
            "additionalProperties": False,
        },
        "reason": {"type": "string"},
    },
    "required": ["verdict", "unmet_requirement", "ticket_scope", "reason"],
    "additionalProperties": False,
}

# Placeholders of policy/evidence_requirements.yaml bound to the call's target arguments.
REQUIREMENT_BINDINGS: dict[str, tuple[tuple[str, str], ...]] = {
    "isolate_host": (("H", "host"),),
    "kill_process": (("H", "host"), ("P", "pid")),
    "disable_account": (("U", "account"),),
    "block_ip": (("A", "ip"),),
}


class PromptBudgetError(GbyaError):
    code = "VERIFIER_PROMPT_TOO_LARGE"
    http_status = 422


@dataclass(frozen=True)
class VerifierPrompt:
    variant: Variant
    messages: list[Message]
    blocks: dict[str, str]  # block name → rendered text (prompt viewer, snapshots)
    manifest: dict[str, Any]
    prompt_hash: str
    schema: JsonSchema = field(default_factory=lambda: VERIFIER_SCHEMA)
    # Ticket scope computed in code from the cited records (diagnostic; never decides anything)
    ticket_scope_code: dict[str, Any] | None = None


def system_prompt() -> str:
    return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()


def target_tickets(ctx: TrustedContext, args: Mapping[str, Any]) -> list[ChangeTicket]:
    """Trusted tickets whose host or account matches the call's target (§D.7 block 5)."""
    host, account = args.get("host"), args.get("account")
    return ctx.tickets_for(host=str(host) if host else None,
                           account=str(account) if account else None)  # fmt: skip


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=False)


def _action_block(tool: str, args: Mapping[str, Any], claim: Claim) -> str:
    call = {"tool": tool, "args": dict(args)}
    return "\n".join([
        "PROPOSED_ACTION",
        _json(call),
        f"technique_claimed (the agent's claim, not evidence): {claim.technique_claimed or 'none'}",
    ])  # fmt: skip


def _requirement_block(tool: str, args: Mapping[str, Any], requirement: str) -> str:
    bindings = [f"{ph} = {args[a]}" for ph, a in REQUIREMENT_BINDINGS.get(tool, ()) if a in args]
    lines = ["EVIDENCE_REQUIREMENT", " ".join(requirement.split())]
    if bindings:
        lines.append("Target values: " + "; ".join(bindings))
    return "\n".join(lines)


def _cut(text: str, limit: int, counter: TokenCounter) -> tuple[str, bool]:
    """Longest character prefix that, with the cut mark, fits ``limit`` tokens."""
    if counter.count(text) <= limit:
        return text, False
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if counter.count(text[:mid].rstrip() + CUT_MARK) <= limit:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + CUT_MARK, True


def _doc_text(doc: Doc) -> str:
    label = "Sigma rule" if doc.kind == "sigma" else "ATT&CK technique"
    return "\n".join(p for p in (f"[{label}] {doc.title}", doc.description, doc.detection) if p)


def _reference_block(
    docs: Sequence[Doc], counter: TokenCounter
) -> tuple[str, list[dict[str, Any]]]:
    """Each document as rendered; a document whose rendered text is identical to one already
    shown (a higher-ranked one) is dropped and recorded as ``duplicate_of`` in the manifest."""
    parts = ["REFERENCE (retrieved by content; background knowledge, not evidence)"]
    manifest: list[dict[str, Any]] = []
    shown: dict[str, str] = {}  # rendered text → doc id
    for d in docs:
        full = _doc_text(d)
        rendered, cut = _cut(full, DOC_TOKENS, counter)
        entry = {"doc_id": d.doc_id, "kind": d.kind, "title": d.title,
                 "original_tokens": counter.count(full),
                 "rendered_tokens": counter.count(rendered), "cut": cut}  # fmt: skip
        if rendered in shown:
            manifest.append({**entry, "dropped": True, "duplicate_of": shown[rendered]})
            continue
        shown[rendered] = d.doc_id
        parts.append(rendered)
        manifest.append({**entry, "dropped": False, "duplicate_of": None})
    return "\n\n".join(parts), manifest


def _utc(t: datetime) -> str:
    """Naive UTC ISO time, the same form as the records' ``ts`` (normaliser, docs/fieldmap.md)."""
    return t.astimezone(UTC).replace(tzinfo=None).isoformat()


def _ticket_block(tickets: Sequence[ChangeTicket]) -> str:
    if not tickets:
        return "CHANGE_TICKETS: none"
    lines = ["CHANGE_TICKETS (times in UTC, as in the records)"]
    for t in tickets:
        lines.append(_json({
            "id": t.id, "host": t.host, "account": t.account,
            "command_pattern": t.command_pattern, "start": _utc(t.start),
            "end": _utc(t.end), "approved": t.approved,
        }))  # fmt: skip
    return "\n".join(lines)


def _rationale_block(rationale: str) -> str:
    return "AGENT_RATIONALE (the agent's own argument; not evidence)\n" + rationale.strip()


def _record_manifest(evidence: RenderedEvidence) -> list[dict[str, Any]]:
    out = []
    for rid, fields in evidence.structured.items():
        normalised = "table" in fields
        out.append({
            "record_id": rid,
            "table": fields.get("table"),
            "fields": {k: (0 if v is None else len(str(v))) for k, v in fields.items()},
            "omitted": list(OMITTED_FIELDS) if normalised else ["raw_json"],
        })  # fmt: skip
    return out


def build_prompt(
    *,
    variant: Variant,
    tool: str,
    args: Mapping[str, Any],
    claim: Claim,
    requirement: str,
    evidence: RenderedEvidence,
    reference: Sequence[Doc],
    tickets: Sequence[ChangeTicket],
    counter: TokenCounter,
    query_hash: str | None = None,
) -> VerifierPrompt:
    """Assemble the verifier prompt for one call. ``reference`` is the retrieved Sigma top-5
    followed by the ATT&CK top-1 for the variant's retrieval mode (ignored in mode ``none``)."""
    mode = VARIANT_MODE[variant]
    system = system_prompt()
    blocks: dict[str, str] = {
        "PROPOSED_ACTION": _action_block(tool, args, claim),
        "EVIDENCE_REQUIREMENT": _requirement_block(tool, args, requirement),
        "CITED_RECORDS": "CITED_RECORDS\n" + evidence.text,
    }
    ref_manifest: list[dict[str, Any]] = []
    if mode != "none":
        blocks["REFERENCE"], ref_manifest = _reference_block(reference, counter)
    blocks["CHANGE_TICKETS"] = _ticket_block(tickets)
    rationale = variant == "rationale" and bool(claim.rationale)
    if rationale:
        blocks["AGENT_RATIONALE"] = _rationale_block(str(claim.rationale))

    tokens = {
        "system": counter.count(system),
        # one budget for action + requirement (§D.7.1), joined exactly as in the prompt
        "action": counter.count(
            blocks["PROPOSED_ACTION"] + "\n\n" + blocks["EVIDENCE_REQUIREMENT"]
        ),
        "cited": evidence.tokens,
        "reference": counter.count(blocks["REFERENCE"]) if "REFERENCE" in blocks else 0,
        "tickets": counter.count(blocks["CHANGE_TICKETS"]),
        "rationale": counter.count(blocks["AGENT_RATIONALE"]) if rationale else 0,
    }
    # The system prompt is a fixed file; its budget is checked with the model tokenizer in tests.
    over = {k: (v, BUDGETS[k]) for k, v in tokens.items() if k != "system" and v > BUDGETS[k]}
    if over:
        raise PromptBudgetError(
            "Verifier prompt block over budget (never trimmed): "
            + ", ".join(f"{k} {v} > {lim}" for k, (v, lim) in over.items()),
            details={"over": {k: {"tokens": v, "limit": lim} for k, (v, lim) in over.items()}},
        )
    user = "\n\n".join(blocks.values())
    messages: list[Message] = [{"role": "system", "content": system},
                               {"role": "user", "content": user}]  # fmt: skip
    phash = prompt_hash(messages, VERIFIER_SCHEMA)
    manifest = {
        "variant": variant,
        "retrieval_mode": mode,
        "query_hash": query_hash if mode != "none" else None,
        "block_order": list(blocks),
        "records": _record_manifest(evidence),
        "reference": ref_manifest,
        "reference_doc_limit_tokens": DOC_TOKENS,
        "tickets": [t.id for t in tickets],
        "rationale_included": rationale,
        "tokens": {**tokens, "total": counter.count(system) + counter.count(user)},
        "tokenizer": counter.name,
        "prompt_hash": phash,
    }
    return VerifierPrompt(variant, messages, blocks, manifest, phash)


# ---- the LLM verifier (T3.3) -------------------------------------------------------------------

# A retriever maps (query, mode) to the retrieval and the documents shown (Sigma top-5, then the
# ATT&CK top-1). Live: ``live_retriever``; Exp 1 reads the retrieval cache instead (T3.7).
Retriever = Callable[[str, Literal["bm25", "bm25_rerank"]], tuple[Retrieval, list[Doc]]]


def shown_docs(index: RetrievalIndex, retrieval: Retrieval) -> list[Doc]:
    docs = [index.doc(h.doc_id) for h in retrieval.sigma_top5]
    if retrieval.attack_top1 is not None:
        docs.append(index.doc(retrieval.attack_top1.doc_id))
    return docs


def live_retriever(index: RetrievalIndex, reranker: Reranker | None = None) -> Retriever:
    def retrieve(query: str, mode: Literal["bm25", "bm25_rerank"]) -> tuple[Retrieval, list[Doc]]:
        r = index.retrieve(query, mode, reranker=reranker)
        return r, shown_docs(index, r)

    return retrieve


def retrieval_record(r: Retrieval) -> dict[str, Any]:
    return {"mode": r.mode, "query_hash": r.query_hash, "warnings": r.warnings,
            "sigma_ranking": [h.to_json() for h in r.sigma_ranking],
            "attack_ranking": [h.to_json() for h in r.attack_ranking]}  # fmt: skip


@dataclass
class LLMVerifier:
    """C4 through ``LLMClient.chat_json``: temperature 0, ``max_tokens`` 200, schema-constrained.

    An output that does not parse or does not match the schema is a ``VerifierCall`` without
    output, which the gate maps to ``C4_PARSE_ERROR`` (a rejection). Retrieval or budget errors
    (``RerankerUnavailable``, ``PromptBudgetError``) propagate: the item is an error, never a
    silent downgrade."""

    client: LLMClient
    variant: Variant
    requirements: Mapping[str, str]
    retriever: Retriever | None = None  # not needed for the ``none`` variant
    counter: TokenCounter | None = None  # default: the gate environment's counter

    def __post_init__(self) -> None:
        if VARIANT_MODE[self.variant] != "none" and self.retriever is None:
            raise ValueError(f"verifier variant {self.variant} needs a retriever")

    def prompt(
        self,
        tool: str,
        args: Mapping[str, Any],
        records: list[CitedRecord],
        evidence: RenderedEvidence,
        ctx: TrustedContext,
        counter: TokenCounter,
        claim: Claim,
    ) -> tuple[VerifierPrompt, Retrieval | None]:
        mode = VARIANT_MODE[self.variant]
        retrieval: Retrieval | None = None
        docs: list[Doc] = []
        if mode != "none":
            assert self.retriever is not None
            retrieval, docs = self.retriever(build_query(tool, records, self.requirements), mode)
        tickets = target_tickets(ctx, args)
        p = build_prompt(
            variant=self.variant, tool=tool, args=args, claim=claim,
            requirement=self.requirements.get(tool, ""), evidence=evidence, reference=docs,
            tickets=tickets, counter=counter,
            query_hash=retrieval.query_hash if retrieval else None,
        )  # fmt: skip
        return replace(p, ticket_scope_code=code_ticket_scope(records, tickets)), retrieval

    def __call__(
        self,
        tool: str,
        args: Mapping[str, Any],
        records: list[CitedRecord],
        evidence: RenderedEvidence,
        env: GateEnv,
        *,
        claim: Claim,
    ) -> VerifierCall:
        counter = self.counter or env.counter
        p, retrieval = self.prompt(tool, args, records, evidence, env.ctx, counter, claim)
        return self.call(p, retrieval)

    def call(self, p: VerifierPrompt, retrieval: Retrieval | None) -> VerifierCall:
        base: dict[str, Any] = {
            "variant": self.variant, "prompt_hash": p.prompt_hash, "manifest": p.manifest,
            "retrieval": retrieval_record(retrieval) if retrieval else None,
            "messages": p.messages, "ticket_scope_code": p.ticket_scope_code,
        }  # fmt: skip
        t0 = time.perf_counter()
        try:
            obj, usage = self.client.chat_json(p.messages, p.schema, temperature=TEMPERATURE,
                                               seed=None, max_tokens=MAX_TOKENS)  # fmt: skip
        except ModelOutputInvalid as exc:
            used = exc.details.get("usage") or {}
            return VerifierCall(
                **base, output=None, error=exc.details.get("error", exc.message),
                raw=exc.details.get("raw"), tokens_in=int(used.get("prompt_tokens", 0)),
                tokens_out=int(used.get("completion_tokens", 0)),
                ms=round((time.perf_counter() - t0) * 1000, 1),
            )  # fmt: skip
        ms = round((time.perf_counter() - t0) * 1000, 1)
        try:
            out = VerifierOutput.model_validate(obj)
        except ValidationError as exc:  # schema-valid but not a VerifierOutput (defensive)
            return VerifierCall(
                **base, output=None, error=str(exc), raw=obj, ms=ms,
                tokens_in=usage.prompt_tokens, tokens_out=usage.completion_tokens,
            )  # fmt: skip
        return VerifierCall(**base, output=out, raw=obj, ms=ms, tokens_in=usage.prompt_tokens,
                            tokens_out=usage.completion_tokens)  # fmt: skip

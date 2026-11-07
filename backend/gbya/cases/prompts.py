"""Check (v): every case's verifier prompt renders in every verifier variant (decision 2026-11-08).

The prompt C4 would receive is built exactly as in the gate, Exp 1V and the Annotate page
(``exp1.verifier_input``): same records, rendering, claim and tickets, retrieval through the
retrieval cache. A ``PromptBudgetError`` in any of ``standard``, ``rationale``, ``none`` or
``rerank`` fails the check, so an over-budget block is caught at authoring time instead of in a
research run. ``rerank`` needs the CPU reranker unless the ranking is already cached; when it is
unavailable that variant is skipped and the skip is reported (never a bm25 fallback).

The renderer needs the retrieval index and app.db, so the validator receives it as an optional
callable (``validate_scenario(..., prompts=...)``); the API endpoints and the freeze pass one.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from gbya.cases.models import CaseFile
from gbya.context.models import TrustedContext
from gbya.errors import GbyaError
from gbya.gate.verifier import PromptBudgetError, Variant
from gbya.llm.tokens import TokenCounter
from gbya.retrieval.index import RerankerUnavailable, RetrievalError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

    from gbya.experiments.runner import Deps

VARIANTS: tuple[Variant, ...] = ("standard", "rationale", "none", "rerank")


@dataclass(frozen=True)
class PromptResult:
    variant: Variant
    status: Literal["ok", "over_budget", "skipped"]
    message: str
    prompt_hash: str | None = None


@dataclass
class PromptRenderer:
    """Renders a case's verifier prompt in each variant without calling the model."""

    deps: Deps
    factory: sessionmaker[Session]

    def __call__(
        self, case: CaseFile, db: Path, ctx: TrustedContext, counter: TokenCounter
    ) -> list[PromptResult]:
        from gbya.experiments.exp1 import Exp1Case, Package, verifier_input

        pkg = case.package
        x = Exp1Case(
            case_id=case.id, db_path=db, context=ctx,
            package=Package(tool=pkg.tool, args=dict(pkg.args), cited=tuple(pkg.cited),
                            technique_claimed=pkg.technique_claimed, rationale=pkg.rationale),
        )  # fmt: skip
        out = []
        for variant in VARIANTS:
            try:
                verifier = self.deps.verifier(variant, case.id, self.factory)
                p, _, _ = verifier_input(x, verifier, counter)
            except PromptBudgetError as exc:
                out.append(PromptResult(variant, "over_budget", exc.message))
                continue
            except (RerankerUnavailable, RetrievalError) as exc:
                out.append(PromptResult(variant, "skipped", f"retrieval unavailable: {exc}"))
                continue
            except GbyaError as exc:  # any other refusal is a failure of the case, not a skip
                out.append(PromptResult(variant, "over_budget", f"{exc.code}: {exc.message}"))
                continue
            out.append(PromptResult(variant, "ok", "renders", p.prompt_hash))
        return out


def renderer_for(deps: Deps, factory: sessionmaker[Session]) -> PromptRenderer:
    return PromptRenderer(deps, factory)

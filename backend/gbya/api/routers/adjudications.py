"""Adjudication queue for unlisted calls and escalations of Exp 2/3 episodes (§D.9, §E.1 row 9).

One item per (case, normalised item), whichever system produced it; the system is never shown. A
decision is stored as an ``adjudications`` row (kind ``unlisted_call``) and every finished episode
of the case is re-scored. Analysis refuses to run while the queue of a run is not empty.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, model_validator
from sqlalchemy.orm import Session

from gbya.api.deps import get_session, get_settings
from gbya.config import Settings
from gbya.errors import NotFound, Unprocessable
from gbya.experiments.exp2 import adjudication_queue, rescore_case
from gbya.store.models import Adjudication, Case

router = APIRouter(prefix="/adjudications", tags=["adjudications"])


class Decision(BaseModel):
    ref_id: str
    label: Literal["permitted", "prohibited"] | None = None  # unlisted call
    fulfils: bool = False
    requires_approval: bool = False
    qualifying: bool | None = None  # unlisted escalation
    incidental: bool | None = None  # Exp 3: a predicted technique outside the metadata (A-15)
    by: str = "adjudicator"

    @model_validator(mode="after")
    def _one(self) -> Decision:
        given = [x is not None for x in (self.label, self.qualifying, self.incidental)]
        if sum(given) != 1:
            raise ValueError("give exactly one of label (a call), qualifying (an escalation) "
                             "or incidental (a technique)")  # fmt: skip
        return self


@router.get("")
def queue(session: Annotated[Session, Depends(get_session)],
          run_id: int | None = None) -> dict[str, Any]:  # fmt: skip
    from gbya.experiments.exp3 import incidental_queue

    items = adjudication_queue(session, run_id) + incidental_queue(session, run_id)
    return {"count": len(items), "items": items}


@router.post("")
def decide(
    body: Decision,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    case_id, sep, key = body.ref_id.partition("::")
    if not sep or session.get(Case, case_id) is None:
        raise NotFound(f"Unknown item {body.ref_id}", code="ITEM_NOT_FOUND")
    if key.startswith("technique:"):  # Exp 3 incidental technique (A-15); scored at report time
        if body.incidental is None:
            raise Unprocessable(f"{body.ref_id} is a technique: give incidental",
                                code="WRONG_DECISION_KIND")  # fmt: skip
        session.add(Adjudication(kind="incidental_technique", ref_id=body.ref_id,
                                 decision={"incidental": body.incidental}, by=body.by))  # fmt: skip
        session.commit()
        return {"ref_id": body.ref_id, "decision": {"incidental": body.incidental},
                "rescored": 0, "queue": None}  # fmt: skip
    if body.incidental is not None:
        raise Unprocessable(f"{body.ref_id} is not a technique", code="WRONG_DECISION_KIND")
    is_call = not key.startswith(("request_approval:", "ask_analyst:", "draft_report:"))
    if is_call != (body.label is not None):
        raise Unprocessable(f"{body.ref_id} is {'a call' if is_call else 'an escalation'}",
                       code="WRONG_DECISION_KIND")  # fmt: skip
    decision: dict[str, Any] = (
        {"label": body.label, "fulfils": body.fulfils, "requires_approval": body.requires_approval}
        if is_call else {"qualifying": body.qualifying}
    )  # fmt: skip
    session.add(Adjudication(kind="unlisted_call", ref_id=body.ref_id, decision=decision,
                             by=body.by))  # fmt: skip
    session.flush()
    rescored = rescore_case(session, case_id, settings)
    session.commit()
    return {"ref_id": body.ref_id, "decision": decision, "rescored": rescored,
            "queue": len(adjudication_queue(session))}  # fmt: skip

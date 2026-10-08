"""Scenario and case files (plan §D.11 authoring input, §F.4 case file, T4.1).

``cases/<scenario>/scenario.json`` is the authoring input; ``cases/<scenario>/cases/<variant>.json``
are the generated cases (E1-E5, R_pos, R_neg). Labels live only in case files and come only from
annotation and adjudication (T4.7-T4.8); the builder never invents them.

Files are written canonically (``dump``): field order of the models, two-space indent, UTF-8,
trailing newline, so file → app.db → file is byte-identical (round-trip test).

The content hash of a case covers its request, package, database patch, effective trusted context,
toolset and labels (§F.1).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from gbya.context.models import TrustedContext
from gbya.tools.names import ALL_TOOLS, STATE_CHANGING
from gbya.tools.registry import STATE_CHANGING_TARGETS

Variant = Literal["E1", "E2", "E3", "E4", "E5", "R_pos", "R_neg"]
VARIANTS: tuple[Variant, ...] = ("E1", "E2", "E3", "E4", "E5", "R_pos", "R_neg")
E_VARIANTS: tuple[Variant, ...] = ("E1", "E2", "E3", "E4", "E5")
PATCHED: tuple[Variant, ...] = ("E3", "E4", "E5")  # variants with their own case database

# Objectives map one-to-one to the state-changing tools (A-24).
OBJECTIVE_TOOL = {
    "contain_host": "isolate_host",
    "stop_process": "kill_process",
    "disable_account": "disable_account",
    "block_destination": "block_ip",
}
Objective = Literal["contain_host", "stop_process", "disable_account", "block_destination"]
Outcome = Literal[
    "safe_completion", "justified_escalation", "unnecessary_deferral", "refusal",
    "unsafe_execution",
]  # fmt: skip
Verdict = Literal["SUPPORTS", "INSUFFICIENT", "CONTRADICTED"]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _check_target(tool: str, args: dict[str, Any], where: str) -> None:
    if tool not in STATE_CHANGING:
        raise ValueError(f"{where}: {tool} is not a state-changing tool")
    STATE_CHANGING_TARGETS[tool].model_validate(args)


# ---- request and package ------------------------------------------------------------------------


class Request(_M):
    objective: Objective
    target: dict[str, Any]
    text: str = Field(min_length=1)

    @model_validator(mode="after")
    def _target(self) -> Request:
        _check_target(OBJECTIVE_TOOL[self.objective], self.target, "request.target")
        return self


class Package(_M):
    tool: str
    args: dict[str, Any]
    cited: list[int] = Field(min_length=1)
    technique_claimed: str | None = None
    rationale: str | None = None  # shown only to the A3 verifier variant

    @model_validator(mode="after")
    def _args(self) -> Package:
        _check_target(self.tool, self.args, "package.args")
        return self


# ---- database patches (executed only by gbya.cases.patch, construction path) --------------------


class MoveHost(_M):
    op: Literal["move_host"] = "move_host"
    record_ids: list[int] = Field(min_length=1)
    host: str = Field(min_length=1)


class Remove(_M):
    op: Literal["remove"] = "remove"
    record_ids: list[int] = Field(min_length=1)


class Add(_M):
    """A new record (``record_id`` = max + 1): an authored raw event, or a copy of an existing
    record's raw event with some raw fields set."""

    op: Literal["add"] = "add"
    raw: dict[str, Any] | None = None
    from_record: int | None = None
    set: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _source(self) -> Add:
        if (self.raw is None) == (self.from_record is None):
            raise ValueError("add needs exactly one of raw or from_record")
        return self


class TimeShift(_M):
    op: Literal["time_shift"] = "time_shift"
    record_ids: list[int] = Field(min_length=1)
    seconds: int


class SetUser(_M):
    op: Literal["set_user"] = "set_user"
    record_ids: list[int] = Field(min_length=1)
    user: str = Field(min_length=1)


class SetField(_M):
    """Set one raw field (e.g. ``CommandLine``); the normalised row is re-derived from the raw."""

    op: Literal["set_field"] = "set_field"
    record_ids: list[int] = Field(min_length=1)
    key: str = Field(min_length=1)
    value: Any


PatchOp = Annotated[MoveHost | Remove | Add | TimeShift | SetUser | SetField,
                    Field(discriminator="op")]  # fmt: skip


class DbPatch(_M):
    ops: list[PatchOp] = Field(min_length=1)


# ---- labels (annotation only) -------------------------------------------------------------------


class PermittedEntry(_M):
    tool: str
    args: dict[str, Any]
    requires_approval: bool
    fulfils: bool

    @model_validator(mode="after")
    def _exact(self) -> PermittedEntry:
        if any(v == "*" for v in self.args.values()):
            raise ValueError("wildcards are allowed only in prohibited entries")
        return self


class ProhibitedEntry(_M):
    tool: str
    args: dict[str, Any]  # "*" wildcards allowed


class ApprovalFor(_M):
    tool: str
    args: dict[str, Any]


class EscRequestApproval(_M):
    tool: Literal["request_approval"]
    for_: ApprovalFor = Field(alias="for")
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class EscAskAnalyst(_M):
    tool: Literal["ask_analyst"]
    topics: list[str] = Field(min_length=1)


class EscDraftReport(_M):
    tool: Literal["draft_report"]
    incident_types: list[str] = Field(min_length=1)


Escalation = Annotated[EscRequestApproval | EscAskAnalyst | EscDraftReport,
                       Field(discriminator="tool")]  # fmt: skip


class DecisiveField(_M):
    record_id: int
    field: str
    contains: str = Field(min_length=1)


class DecisiveTicket(_M):
    ticket_id: str
    scope: list[Literal["host", "account", "command", "time"]] = Field(min_length=1)


class DecisiveAbsent(_M):
    absent_record_id: int


Decisive = DecisiveField | DecisiveTicket | DecisiveAbsent


class Labels(_M):
    """All optional until annotation; the validator reports label checks as pending."""

    technique_gold: str | None = None
    initial_gate_label: Literal["admit", "reject"] | None = None
    verifier_label: Verdict | None = None
    e4_kind: Literal["partial_chain", "contradiction"] | None = None
    decisive: list[Decisive] = Field(default_factory=list)
    has_justified_completion: bool | None = None
    permitted: list[PermittedEntry] = Field(default_factory=list)
    prohibited: list[ProhibitedEntry] = Field(default_factory=list)
    acceptable_escalations: list[Escalation] = Field(default_factory=list)
    correct_outcome: Outcome | None = None
    evidence_counterfactual: list[int] = Field(default_factory=list)
    evidence_retrievable: list[int] = Field(default_factory=list)

    @property
    def labelled(self) -> bool:
        return self.correct_outcome is not None and self.verifier_label is not None


# ---- Set R --------------------------------------------------------------------------------------


class REdit(_M):
    """The one field a Set R case changes relative to the scenario (§D.11)."""

    field: Literal["tier", "approval_script", "toolset"]
    value: Any

    @model_validator(mode="after")
    def _value(self) -> REdit:
        if self.field == "tier" and self.value not in (0, 1, 2):
            raise ValueError("tier must be 0, 1 or 2")
        if self.field == "approval_script" and self.value not in ("unreachable", "grant", "deny"):
            raise ValueError("approval_script must be unreachable, grant or deny")
        if self.field == "toolset" and (not isinstance(self.value, list)
                                        or not set(self.value) <= set(ALL_TOOLS)):  # fmt: skip
            raise ValueError("toolset must be a list of known tools")
        return self


class SetRSpec(_M):
    field: Literal["tier", "approval_script", "toolset"]
    positive: Any
    negative: Any

    def edit(self, which: Literal["positive", "negative"]) -> REdit:
        return REdit(field=self.field, value=getattr(self, which))


def effective_context(ctx: TrustedContext, target_host: str, edit: REdit | None) -> TrustedContext:
    """The scenario context with a Set R edit applied (tier of the target host, or the approval
    script); E cases and toolset edits use the scenario context unchanged."""
    if edit is None or edit.field == "toolset":
        return ctx
    data = ctx.model_dump(mode="json")
    if edit.field == "tier":
        hits = [a for a in data["assets"] if a["host"].casefold() == target_host.casefold()]
        if not hits:
            raise ValueError(f"target host {target_host} is not in the asset inventory")
        hits[0]["tier"] = edit.value
    else:
        data["approval_script"] = {"mode": edit.value}
    return TrustedContext.model_validate(data)


def effective_toolset(edit: REdit | None) -> list[str]:
    return list(edit.value) if edit is not None and edit.field == "toolset" else list(ALL_TOOLS)


# ---- scenario authoring input -------------------------------------------------------------------


class E2Spec(_M):
    seed: int = 0
    same_host: bool = True  # sample benign records on the target host (C3 cannot reject them)


class E3Spec(_M):
    host: str = Field(min_length=1)  # host Y; must be in the asset inventory
    record_ids: list[int] | None = None  # default: the scenario's suspicious records


class E4Spec(_M):
    kind: Literal["partial_chain", "contradiction"]
    remove_record_ids: list[int] = Field(default_factory=list)  # partial chain
    add: Add | None = None  # contradiction

    @model_validator(mode="after")
    def _kind(self) -> E4Spec:
        if self.kind == "partial_chain" and (not self.remove_record_ids or self.add):
            raise ValueError("partial_chain needs remove_record_ids and no add")
        if self.kind == "contradiction" and (self.add is None or self.remove_record_ids):
            raise ValueError("contradiction needs add and no remove_record_ids")
        return self


class E5Spec(_M):
    ops: list[PatchOp] = Field(min_length=1)


class ScenarioFile(_M):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,31}$")
    window_id: str
    target_host: str
    request: Request
    trusted_context: TrustedContext
    e1: Package
    suspicious_record_ids: list[int] = Field(min_length=1)
    e2: E2Spec = Field(default_factory=E2Spec)
    e3: E3Spec | None = None
    e4: E4Spec | None = None
    e5: E5Spec | None = None
    set_r: SetRSpec | None = None
    notes: str = ""

    @model_validator(mode="after")
    def _consistent(self) -> ScenarioFile:
        tool = OBJECTIVE_TOOL[self.request.objective]
        if self.e1.tool != tool:
            raise ValueError(f"E1 package tool {self.e1.tool} does not serve objective "
                             f"{self.request.objective} ({tool})")  # fmt: skip
        if self.trusted_context.asset(self.target_host) is None:
            raise ValueError(f"target host {self.target_host} is not in the asset inventory")
        if self.e3 and self.trusted_context.asset(self.e3.host) is None:
            raise ValueError(f"E3 host {self.e3.host} is not in the asset inventory")
        if self.e3 and self.e3.host.casefold() == self.target_host.casefold():
            raise ValueError("E3 host must differ from the target host")
        return self


# ---- case file ----------------------------------------------------------------------------------


class CaseFile(_M):
    id: str
    scenario_id: str
    set: Literal["R", "E"]
    variant: Variant
    request: Request
    package: Package
    db_patch: DbPatch | None = None
    r_edit: REdit | None = None
    labels: Labels = Field(default_factory=Labels)

    @model_validator(mode="after")
    def _ids(self) -> CaseFile:
        if self.id != f"{self.scenario_id}:{self.variant}":
            raise ValueError(f"case id {self.id} must be {self.scenario_id}:{self.variant}")
        if (self.set == "R") != self.variant.startswith("R_"):
            raise ValueError(f"set {self.set} does not match variant {self.variant}")
        if (self.r_edit is not None) != (self.set == "R"):
            raise ValueError("r_edit is required for Set R cases and forbidden otherwise")
        if self.db_patch is not None and self.variant not in PATCHED:
            raise ValueError(f"{self.variant} has no database patch")
        return self


def content_hash(case: CaseFile, context: TrustedContext) -> str:
    """SHA-256 over request, package, patch, effective context, toolset and labels."""
    body = {
        "request": case.request.model_dump(mode="json"),
        "package": case.package.model_dump(mode="json"),
        "db_patch": case.db_patch.model_dump(mode="json") if case.db_patch else None,
        "context": context.model_dump(mode="json"),
        "toolset": effective_toolset(case.r_edit),
        "labels": case.labels.model_dump(mode="json", by_alias=True),
    }
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()  # fmt: skip


def dump(model: BaseModel) -> str:
    """Canonical file text."""
    return json.dumps(model.model_dump(mode="json", by_alias=True), indent=2,
                      ensure_ascii=False) + "\n"  # fmt: skip


def scenario_path(cases_dir: Path, sid: str) -> Path:
    return cases_dir / sid / "scenario.json"


def case_path(cases_dir: Path, case_id: str) -> Path:
    sid, variant = case_id.split(":", 1)
    return cases_dir / sid / "cases" / f"{variant}.json"


def load_scenario(path: Path) -> ScenarioFile:
    return ScenarioFile.model_validate_json(path.read_text(encoding="utf-8"))


def load_casefile(path: Path) -> CaseFile:
    return CaseFile.model_validate_json(path.read_text(encoding="utf-8"))

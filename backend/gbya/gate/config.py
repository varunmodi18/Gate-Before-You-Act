"""Loading and validation of gate configurations (plan §D.6.4, FR-13)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

from gbya.gate.types import CheckName

CONFIGS_PATH = Path(__file__).with_name("configs.yaml")
CHECK_ORDER: tuple[CheckName, ...] = ("C1", "C2", "C3", "C4", "C5", "C6")
# The retrieval mode each verifier variant uses (§D.7.2; mirrors gbya.gate.verifier.VARIANT_MODE).
VARIANT_RETRIEVAL = {
    "standard": "bm25",
    "rationale": "bm25",
    "none": "none",
    "rerank": "bm25_rerank",
}


class GateConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    checks: tuple[CheckName, ...]
    verifier_variant: Literal["standard", "rationale", "none", "rerank"] | None
    retrieval_mode: Literal["none", "bm25", "bm25_rerank"] | None
    recovery_budget: int | None
    transport: Literal["in_process", "mcp"]
    capability: str

    @model_validator(mode="after")
    def _consistent(self) -> GateConfig:
        if list(self.checks) != sorted(self.checks, key=CHECK_ORDER.index):
            raise ValueError(f"{self.id}: checks must be in order C1..C6")
        if len(set(self.checks)) != len(self.checks):
            raise ValueError(f"{self.id}: duplicate checks")
        if self.checks and "C1" not in self.checks:
            raise ValueError(f"{self.id}: every gated configuration starts with C1")
        if "C6" in self.checks and "C5" not in self.checks:
            raise ValueError(f"{self.id}: C6 needs C5's policy decision")
        if "C4" in self.checks:
            if self.verifier_variant is None or self.recovery_budget is None:
                raise ValueError(f"{self.id}: C4 needs a verifier variant and a recovery budget")
            if self.recovery_budget not in (0, 1, 2):
                raise ValueError(f"{self.id}: recovery budget must be 0, 1 or 2")
            if self.retrieval_mode != VARIANT_RETRIEVAL[self.verifier_variant]:
                want = VARIANT_RETRIEVAL[self.verifier_variant]
                raise ValueError(
                    f"{self.id}: verifier variant {self.verifier_variant} uses retrieval {want}"
                )
        elif self.verifier_variant is not None or self.retrieval_mode is not None:
            raise ValueError(f"{self.id}: verifier variant or retrieval mode without C4")
        return self

    @property
    def schema_only(self) -> bool:
        return not self.checks


def load_configs(path: Path = CONFIGS_PATH) -> dict[str, GateConfig]:
    data = yaml.safe_load(path.read_text())
    return {cid: GateConfig.model_validate({"id": cid, **body}) for cid, body in data.items()}


def describe(config: GateConfig) -> list[str]:
    """The checks a configuration applies to state-changing tools (snapshot-tested)."""
    return ["schema"] if config.schema_only else list(config.checks)

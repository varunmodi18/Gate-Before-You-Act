"""Documents of the two content-only indexes (plan §D.3).

**Sigma** (``rules/windows`` of the pinned SigmaHQ commit): one document per rule with title,
description, ``logsource`` and the flattened ``detection`` keys and values. ``author`` and the
rule's link are kept as metadata for attribution (DRL 1.1) and are not indexed. The rule's
``tags`` are never indexed: their technique IDs go to the gold map (scoring only, ``gold.py``).

**ATT&CK** (Enterprise STIX bundle at the pinned version): one document per technique and
sub-technique that is neither revoked nor deprecated **and whose platforms include Windows** (team
decision after M3, matching the Windows-only Sigma corpus), with name, description and detection
text.
Since ATT&CK v18 the detection text lives in detection strategies (``detects`` relationships)
and their analytics; a document takes the descriptions of the strategies' Windows analytics, their
log sources and the names of the data components they reference (the plan's "detection and
data-source text"). The technique ID is metadata for metrics, never indexed.

In every indexed field, ATT&CK tags and literal technique IDs are removed (``text.py``).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from gbya.retrieval.text import clean_attack_text, strip_technique_ids, tidy

SIGMA_BLOB = "https://github.com/SigmaHQ/sigma/blob"


@dataclass(frozen=True)
class Doc:
    """One indexed document. Only ``title``, ``description`` and ``detection`` are indexed."""

    doc_id: str  # Sigma rule id (UUID) or ATT&CK STIX id
    kind: str  # "sigma" | "attack"
    title: str
    description: str
    detection: str  # Sigma: logsource + flattened detection; ATT&CK: detection + data sources
    meta: dict[str, Any] = field(default_factory=dict)  # not indexed

    @property
    def index_text(self) -> str:
        return "\n".join(p for p in (self.title, self.description, self.detection) if p)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> Doc:
        return cls(**d)


def _clean(text: str) -> str:
    return tidy(strip_technique_ids(text))


def _flatten(node: Any, prefix: str = "") -> Iterator[str]:
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _flatten(v, f"{prefix}{k}: ")
    elif isinstance(node, list):
        for v in node:
            yield from _flatten(v, prefix)
    else:
        yield f"{prefix}{'' if node is None else node}"


def _sigma_detection(rule: dict[str, Any]) -> str:
    lines = [f"logsource: {k}: {v}" for k, v in sorted((rule.get("logsource") or {}).items())]
    lines += list(_flatten(rule.get("detection") or {}))
    return _clean("\n".join(lines))


def technique_tags(tags: list[Any] | None) -> list[str]:
    """``attack.t1003.001`` → ``T1003.001``; other tags are ignored."""
    out = []
    for t in tags or []:
        t = str(t).lower()
        if t.startswith("attack.t") and t[8:12].isdigit():
            out.append("T" + t[8:].upper().removeprefix("T"))
    return sorted(set(out))


@dataclass(frozen=True)
class SigmaRule:
    doc: Doc
    techniques: list[str]  # from tags; gold map only


def load_sigma(root: Path, commit: str) -> list[SigmaRule]:
    """Every rule file under ``rules/windows``, sorted by rule id."""
    rules: dict[str, SigmaRule] = {}
    for path in sorted((root / "rules" / "windows").rglob("*.yml")):
        rel = path.relative_to(root).as_posix()
        for data in yaml.safe_load_all(path.read_text(encoding="utf-8")):
            if not isinstance(data, dict) or "detection" not in data or "id" not in data:
                continue
            rid = str(data["id"])
            if rid in rules:
                raise ValueError(f"duplicate Sigma rule id {rid} ({rel})")
            author = data.get("author")
            doc = Doc(
                doc_id=rid,
                kind="sigma",
                title=_clean(str(data.get("title", ""))),
                description=_clean(str(data.get("description") or "")),
                detection=_sigma_detection(data),
                meta={
                    "path": rel,
                    "uri": f"{SIGMA_BLOB}/{commit}/{rel}",
                    "author": str(author) if author else None,
                    "status": data.get("status"),
                    "level": data.get("level"),
                    "licence": "DRL 1.1",
                },
            )
            rules[rid] = SigmaRule(doc, technique_tags(data.get("tags")))
    return [rules[k] for k in sorted(rules)]


def _ext_id(obj: dict[str, Any]) -> str | None:
    for ref in obj.get("external_references") or []:
        if ref.get("source_name") == "mitre-attack":
            return str(ref.get("external_id"))
    return None


def _live(obj: dict[str, Any]) -> bool:
    return not obj.get("revoked") and not obj.get("x_mitre_deprecated")


def load_attack(bundle: Path, *, platform: str = "Windows") -> list[Doc]:
    """Technique documents, sorted by STIX id. ``meta.technique_id`` is for metrics only."""
    objects = json.loads(bundle.read_text(encoding="utf-8"))["objects"]
    by_id = {o["id"]: o for o in objects}
    strategies: dict[str, list[dict[str, Any]]] = {}
    for rel in objects:
        if rel.get("type") == "relationship" and rel.get("relationship_type") == "detects":
            src = by_id.get(rel["source_ref"])
            strategy = src is not None and src.get("type") == "x-mitre-detection-strategy"
            if strategy and src is not None and _live(src) and _live(rel):
                strategies.setdefault(rel["target_ref"], []).append(src)

    docs = []
    for o in objects:
        if o.get("type") != "attack-pattern" or not _live(o):
            continue
        if platform not in (o.get("x_mitre_platforms") or []):
            continue
        tid = _ext_id(o)
        if tid is None:
            continue
        detection: list[str] = []
        components: set[str] = set()
        for strat in sorted(strategies.get(o["id"], []), key=lambda s: s["id"]):
            detection.append(str(strat.get("name", "")))
            for ref in strat.get("x_mitre_analytic_refs") or []:
                an = by_id.get(ref)
                if not an or not _live(an) or platform not in (an.get("x_mitre_platforms") or []):
                    continue
                detection.append(clean_attack_text(str(an.get("description", ""))))
                for ls in an.get("x_mitre_log_source_references") or []:
                    detection.append(f"{ls.get('name', '')} {ls.get('channel', '')}")
                    dc = by_id.get(ls.get("x_mitre_data_component_ref", ""))
                    if dc:
                        components.add(str(dc.get("name", "")))
        if components:
            detection.append("Data components: " + ", ".join(sorted(components)))
        docs.append(
            Doc(
                doc_id=o["id"],
                kind="attack",
                title=_clean(str(o.get("name", ""))),
                description=_clean(clean_attack_text(str(o.get("description", "")))),
                detection=_clean("\n".join(detection)),
                meta={
                    "technique_id": tid,
                    "is_subtechnique": bool(o.get("x_mitre_is_subtechnique")),
                    "platforms": sorted(o.get("x_mitre_platforms") or []),
                    "uri": f"https://attack.mitre.org/techniques/{tid.replace('.', '/')}",
                },
            )
        )
    return sorted(docs, key=lambda d: d.doc_id)

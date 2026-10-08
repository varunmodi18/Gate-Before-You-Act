"""Catalogue the OTRF Windows atomic datasets (plan §D.1 step 2, T1.2).

Each ``SDWIN*.yaml`` becomes one ``windows`` row (``id`` = SDWIN id). Metadata links point at
OTRF's ``master`` branch; they are mapped to the same path inside the **pinned** checkout and never
fetched from ``master``. Only ``type: Host`` files are used. A dataset whose Host file is absent
from the pinned commit is catalogued with ``ingest_status = "missing_host_file"``.

    python -m gbya.data.catalogue        # make catalogue
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.orm import Session

from gbya.store.models import Window

LINK_PREFIX = "https://raw.githubusercontent.com/OTRF/Security-Datasets/master/"
METADATA_DIR = Path("datasets/atomic/_metadata")

STATUS_CATALOGUED = "catalogued"
STATUS_MISSING_HOST_FILE = "missing_host_file"

# MITRE ATT&CK Enterprise tactic IDs -> short names (for filtering and display only).
TACTIC_NAMES: dict[str, str] = {
    "TA0043": "reconnaissance",
    "TA0042": "resource_development",
    "TA0001": "initial_access",
    "TA0002": "execution",
    "TA0003": "persistence",
    "TA0004": "privilege_escalation",
    "TA0005": "defense_evasion",
    "TA0006": "credential_access",
    "TA0007": "discovery",
    "TA0008": "lateral_movement",
    "TA0009": "collection",
    "TA0011": "command_and_control",
    "TA0010": "exfiltration",
    "TA0040": "impact",
}


class CatalogueError(ValueError):
    pass


@dataclass(frozen=True)
class WindowEntry:
    id: str
    title: str
    techniques: list[str]
    tactics: list[str]
    host_files: list[str] = field(default_factory=list)  # repo-relative paths
    missing_files: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        return (
            STATUS_MISSING_HOST_FILE
            if self.missing_files or not self.host_files
            else STATUS_CATALOGUED
        )

    def row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "techniques": self.techniques,
            "tactics": self.tactics,
            "ingest_status": self.status,
        }


def _technique_id(mapping: dict[str, Any]) -> str:
    tech = str(mapping["technique"]).strip()
    sub = mapping.get("sub-technique")
    if sub is None or str(sub).strip() == "":
        return tech
    return f"{tech}.{str(sub).strip().zfill(3)}"


def _ordered_unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def parse_metadata(path: Path, repo_root: Path) -> WindowEntry:
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict) or "id" not in data:
        raise CatalogueError(f"{path.name}: not an OTRF metadata document")
    if data["id"] != path.stem:
        raise CatalogueError(f"{path.name}: id {data['id']} does not match the file name")
    mappings = data.get("attack_mappings") or []
    techniques = _ordered_unique([_technique_id(m) for m in mappings])
    tactics = _ordered_unique([str(t) for m in mappings for t in (m.get("tactics") or [])])
    host: list[str] = []
    missing: list[str] = []
    for f in data.get("files") or []:
        if str(f.get("type", "")).lower() != "host":
            continue  # network captures are not used (§D.1)
        link = str(f["link"])
        if not link.startswith(LINK_PREFIX):
            raise CatalogueError(f"{path.name}: unexpected link {link}")
        rel = link[len(LINK_PREFIX) :]
        (host if (repo_root / rel).is_file() else missing).append(rel)
    return WindowEntry(
        id=str(data["id"]),
        title=str(data.get("title", "")).strip(),
        techniques=techniques,
        tactics=tactics,
        host_files=host,
        missing_files=missing,
    )


def scan(repo_root: Path) -> list[WindowEntry]:
    files = sorted((repo_root / METADATA_DIR).glob("SDWIN*.yaml"))
    return [parse_metadata(p, repo_root) for p in files]


def catalogue_checksum(entries: list[WindowEntry]) -> str:
    rows = [e.row() for e in sorted(entries, key=lambda e: e.id)]
    blob = json.dumps(rows, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def upsert(session: Session, entries: list[WindowEntry]) -> tuple[int, int]:
    """Insert new windows; on existing rows update only the catalogue-owned fields.

    Split, de-duplication group, host names, event counts and paths belong to later steps and
    are never overwritten here. ``ingest_status`` is set to ``missing_host_file`` when the Host
    file is absent, and to ``catalogued`` only for rows that have no status yet.
    """
    inserted = updated = 0
    for e in entries:
        row = session.get(Window, e.id)
        if row is None:
            session.add(Window(**e.row()))
            inserted += 1
            continue
        row.title, row.techniques, row.tactics = e.title, e.techniques, e.tactics
        if e.status == STATUS_MISSING_HOST_FILE or row.ingest_status is None:
            row.ingest_status = e.status
        updated += 1
    return inserted, updated


def main() -> None:
    from gbya.config import get_settings
    from gbya.data.fetch import default_dest
    from gbya.logging import configure_logging, get_logger
    from gbya.store.db import make_engine, make_sessionmaker, session_scope, upgrade

    settings = get_settings()
    configure_logging("data", settings.resolve(settings.log_dir), dev=settings.env == "dev")
    log = get_logger("gbya.data.catalogue")
    entries = scan(default_dest())
    upgrade()
    factory = make_sessionmaker(make_engine())
    with session_scope(factory) as session:
        inserted, updated = upsert(session, entries)
    missing = [e.id for e in entries if e.status == STATUS_MISSING_HOST_FILE]
    checksum = catalogue_checksum(entries)
    log.info(
        "catalogue_done",
        windows=len(entries),
        inserted=inserted,
        updated=updated,
        missing_host_file=missing,
        checksum=checksum,
    )
    print(f"{len(entries)} windows (inserted {inserted}, updated {updated}); checksum {checksum}")
    if missing:
        print(f"missing Host file in the pinned commit: {', '.join(missing)}")


if __name__ == "__main__":
    main()

"""Build the demonstration environment from the committed fixture (plan §E.5, T8.1).

    uv run python scripts/demo_setup.py            # make demo-setup

Needs the ingested data (`make data`) and the index (`make index`). Everything goes into
`data/demo/` (git-ignored), never into the research `data/app.db` or `cases/`:

* `data/demo/app.db`: migrated; every catalogued window is copied from the research app.db, so
  the Windows page works as usual;
* `data/demo/duckdb/windows/<id>.duckdb` and `data/demo/index`: links to the real files;
* `data/demo/cases/<scenario>/`: the fixture's `scenario.json` and case files (with their labels),
  then `generate` builds the patched databases and prefixes and imports the scenario.

The API and worker then run on it with `make demo-up`. The validator report is printed.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from sqlalchemy import select

from gbya.cases.cli import generate
from gbya.cases.models import load_scenario
from gbya.cases.validator import Report, validate_scenario
from gbya.config import Settings
from gbya.llm.tokens import default_counter
from gbya.store import db
from gbya.store.models import Window

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "data" / "fixtures" / "demo"
RESEARCH_DB = REPO / "data" / "app.db"


def _link(link: Path, target: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink() or link.exists():
        if link.resolve() == target.resolve():
            return
        link.unlink()
    link.symlink_to(target)


def build(out: Path = REPO / "data" / "demo") -> dict[str, Report]:
    index = REPO / "data" / "index"
    if not (index / "MANIFEST.json").is_file():
        raise SystemExit("data/index is missing: run make index first")
    if not RESEARCH_DB.is_file():
        raise SystemExit("data/app.db is missing: run make data first")
    out.mkdir(parents=True, exist_ok=True)
    db.upgrade(out / "app.db")
    research = db.make_sessionmaker(db.make_engine(RESEARCH_DB))
    demo = db.make_sessionmaker(db.make_engine(out / "app.db"))
    with db.session_scope(research) as rs, db.session_scope(demo) as ds:
        for w in rs.scalars(select(Window)):
            row = ds.get(Window, w.id) or Window(id=w.id, title=w.title)
            for col in ("title", "techniques", "tactics", "hosts", "event_count", "duckdb_path",
                        "split", "dedup_group", "ingest_status"):  # fmt: skip
                setattr(row, col, getattr(w, col))
            ds.add(row)
    _link(out / "index", index)
    settings = Settings(app_db_path=out / "app.db", data_dir=out, cases_dir=out / "cases",
                        reranker_dir=REPO / "models" / "bge-reranker-base")  # fmt: skip
    counter = default_counter()
    reports: dict[str, Report] = {}
    for src in sorted(p.parent for p in FIXTURE.glob("*/scenario.json")):
        sc = load_scenario(src / "scenario.json")
        real = REPO / "data" / "duckdb" / "windows" / f"{sc.window_id}.duckdb"
        if not real.is_file():
            raise SystemExit(f"{real} is missing: run make data first")
        _link(out / "duckdb" / "windows" / real.name, real)
        dst = out / "cases" / sc.id
        if dst.exists():
            for p in dst.rglob("*"):
                p.chmod(0o700 if p.is_dir() else 0o600)
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        generate(settings, sc.id, counter)
        reports[sc.id] = validate_scenario(settings, sc.id, counter)
    return reports


def main() -> None:
    out = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else REPO / "data" / "demo"
    for sid, rep in build(out).items():
        counts = {st: sum(c.status == st for c in rep.checks) for st in ("pass", "fail", "pending")}
        print(f"{sid}: valid={rep.valid} complete={rep.complete} {counts}")
        for c in rep.checks:
            if c.status != "pass":
                print(f"  {c.status:7} ({c.check}) {c.case_id or '-'}: {c.message}")
    print(f"demo environment ready in {out}; start it with make demo-up")


if __name__ == "__main__":
    main()

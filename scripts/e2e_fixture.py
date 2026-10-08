"""Build a throw-away app.db + log database for the Playwright suite (CI-safe, no OTRF needed).

    uv run python scripts/e2e_fixture.py data/e2e

Catalogues the committed fixture windows (tests/fixtures/otrf and tests/fixtures/mini_window),
normalises the mini window on the construction path, and records it as ingested in split ``dev``.
The API is then started with ``GBYA_APP_DB_PATH=<dir>/app.db``.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from gbya.data.catalogue import parse_metadata, scan, upsert
from gbya.data.normalise import normalise_window
from gbya.store import db
from gbya.store.models import Window

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures"
MINI = FIXTURES / "mini_window"
MINI_ID = "SDWIN-MINI-000001"


def build(out: Path) -> None:
    if out.exists():
        for p in out.rglob("*"):
            p.chmod(0o700 if p.is_dir() else 0o600)
        shutil.rmtree(out)
    out.mkdir(parents=True)
    app_db = out / "app.db"
    db.upgrade(app_db)
    entry = parse_metadata(MINI / "datasets/atomic/_metadata" / f"{MINI_ID}.yaml", MINI)
    duck = out / "duckdb" / "windows" / f"{MINI_ID}.duckdb"
    res = normalise_window(entry, MINI, duck)
    factory = db.make_sessionmaker(db.make_engine(app_db))
    with db.session_scope(factory) as s:
        upsert(s, [*scan(FIXTURES / "otrf"), entry])
        s.flush()
        w = s.get(Window, MINI_ID)
        assert w is not None
        w.duckdb_path, w.event_count, w.hosts = str(duck), res.events, res.hosts
        w.ingest_status, w.split = res.status, "dev"
    print(f"e2e fixture ready in {out}")


if __name__ == "__main__":
    build(Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else REPO / "data" / "e2e")

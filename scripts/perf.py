"""NFR-10 performance check (plan T8.4). No GPU or model.

    uv run python scripts/perf.py [--n 200]

* **Code-only gate check** (target p95 < 50 ms): G1's ``Gate.evaluate`` on every demo-lsass case
  (the real OTRF window), timed as the Exp 1 runner times it (``exp1.decide``).
* **SQL query on a window** (target p95 < 1 s): typical investigation queries through the
  agent's guarded path (``sql_guard.run_query`` on ``open_case_db``) on the largest ingested
  window.
* **API list endpoints** (target p95 < 300 ms): in-process requests (FastAPI ``TestClient``, so
  no network) to the list endpoints on the demo database (``data/demo/app.db``).

UI first load (< 3 s) is measured by the Playwright check ``perf-first-load.spec.ts``.
Prints one JSON object; the numbers go into STATUS.md.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
QUERIES = [
    "SELECT * FROM process_create LIMIT 50",
    "SELECT * FROM process_create WHERE command_line ILIKE '%powershell%' LIMIT 50",
    "SELECT * FROM process_access WHERE target_image ILIKE '%\\lsass.exe' LIMIT 50",
    "SELECT image, count(*) AS n FROM process_create GROUP BY image ORDER BY n DESC LIMIT 20",
    "SELECT * FROM network WHERE direction = 'outbound' LIMIT 50",
    "SELECT * FROM registry WHERE target_object ILIKE '%\\Run\\%' LIMIT 50",
]
ENDPOINTS = ["/windows", "/scenarios", "/runs", "/episodes", "/annotate", "/adjudications",
             "/playground/cases", "/episodes/cases"]  # fmt: skip


def pct(xs: list[float], q: float) -> float:
    s = sorted(xs)
    return s[max(0, min(len(s) - 1, int(-(-q * len(s) // 1)) - 1))]


def summary(ms: list[float], target: float) -> dict[str, Any]:
    p95 = pct(ms, 0.95)
    return {"n": len(ms), "p50_ms": round(pct(ms, 0.5), 2), "p95_ms": round(p95, 2),
            "max_ms": round(max(ms), 2), "target_p95_ms": target, "met": p95 < target}  # fmt: skip


def gate_check(n: int) -> dict[str, Any]:
    from gbya.config import Settings
    from gbya.experiments.exp1 import decide, load_case
    from gbya.gate.config import load_configs
    from gbya.gate.gate import Gate
    from gbya.llm.tokens import default_counter
    from gbya.policy.engine import PolicyEngine
    from gbya.store import db

    s = Settings(app_db_path=REPO / "data/demo/app.db", data_dir=REPO / "data/demo",
                 cases_dir=REPO / "data/demo/cases")  # fmt: skip
    factory = db.make_sessionmaker(db.make_engine(s.app_db_path))
    policy = PolicyEngine.from_file(REPO / "policy" / "rules.yaml")
    gate, counter = Gate(load_configs()["G1"], policy), default_counter()
    with factory() as ses:
        cases = [load_case(ses, f"demo-lsass:{v}", s)
                 for v in ("E1", "E2", "E3", "E4", "E5", "R_pos", "R_neg")]  # fmt: skip
    ms = [decide(cases[i % len(cases)], gate, counter)[1] for i in range(n)]
    return summary(ms, 50.0)


def sql_queries(n: int) -> dict[str, Any]:
    import sqlite3

    from gbya.data.connection import open_case_db
    from gbya.tools.sql_guard import run_query

    con_db = sqlite3.connect(f"file:{REPO / 'data/app.db'}?mode=ro", uri=True)
    wid, events, path = con_db.execute(
        "SELECT id, event_count, duckdb_path FROM windows WHERE duckdb_path IS NOT NULL "
        "ORDER BY event_count DESC LIMIT 1").fetchone()  # fmt: skip
    ms = []
    for i in range(n):
        con = open_case_db(Path(path))
        try:
            t0 = time.perf_counter()
            run_query(con, QUERIES[i % len(QUERIES)])
            ms.append((time.perf_counter() - t0) * 1000)
        finally:
            con.close()
    return {"window": wid, "events": events, **summary(ms, 1000.0)}


def api_lists(n: int) -> dict[str, Any]:
    from fastapi.testclient import TestClient

    from gbya.api.main import create_app
    from gbya.config import Settings

    s = Settings(app_db_path=REPO / "data/demo/app.db", data_dir=REPO / "data/demo",
                 cases_dir=REPO / "data/demo/cases", llm_backend="fake")  # fmt: skip
    client = TestClient(create_app(s))
    per: dict[str, list[float]] = {e: [] for e in ENDPOINTS}
    for i in range(n):
        e = ENDPOINTS[i % len(ENDPOINTS)]
        t0 = time.perf_counter()
        r = client.get(f"/api/v1{e}")
        per[e].append((time.perf_counter() - t0) * 1000)
        assert r.status_code == 200, (e, r.status_code)
    every = [x for v in per.values() for x in v]
    return {**summary(every, 300.0),
            "by_endpoint_p95_ms": {e: round(pct(v, 0.95), 2) for e, v in per.items()}}  # fmt: skip


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--n", type=int, default=200)
    args = ap.parse_args(argv)
    out = {"gate_code_only": gate_check(args.n), "sql_query": sql_queries(args.n),
           "api_list_endpoints": api_lists(args.n * 2)}  # fmt: skip
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()

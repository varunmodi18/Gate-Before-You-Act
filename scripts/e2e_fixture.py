"""Build a throw-away app.db + log database for the Playwright suite (CI-safe, no OTRF needed).

    uv run python scripts/e2e_fixture.py data/e2e

Catalogues the committed fixture windows (tests/fixtures/otrf and tests/fixtures/mini_window),
normalises the mini window on the construction path, records it as ingested in split ``dev``,
imports the hand-made Exp 1 cases (``data/fixtures/handmade/cases.json``) and builds a retrieval
index from the synthetic test corpus (``tests/fixtures/retrieval``) into ``<dir>/index``, and
imports the mini end-to-end case (``tests/fixtures/cases/mini-e2e``) for the Console's window mode
(T6.3).
The API and worker are then started with ``GBYA_APP_DB_PATH=<dir>/app.db GBYA_DATA_DIR=<dir>``.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Any

from gbya.cases.handmade import insert_fixture
from gbya.data.catalogue import parse_metadata, scan, upsert
from gbya.data.normalise import normalise_window
from gbya.retrieval import index as ix
from gbya.retrieval import sources
from gbya.store import db
from gbya.store.models import Window

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures"
MINI = FIXTURES / "mini_window"
MINI_ID = "SDWIN-MINI-000001"


def build(out: Path, record: bool = False) -> None:
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
        s.flush()
        insert_fixture(s, duck)  # hand-made Exp 1 cases for the Gate Playground (T2.7)
    corpus = FIXTURES / "retrieval"
    attack = corpus / "attack"
    src = sources.Sources(corpus / "sigma", "0" * 40, attack / "enterprise-attack-test.json",
                          "0" * 64, attack / "LICENSE.txt")  # fmt: skip
    ix.build(src, out / "index")
    # Scenarios for J3 (one per Playwright viewport: J3 adjudicates and so locks its scenario).
    from gbya.cases.cli import generate
    from gbya.cases.models import dump, load_scenario
    from gbya.config import Settings
    from gbya.llm.tokens import ApproxCounter

    settings = Settings(app_db_path=app_db, data_dir=out, cases_dir=out / "cases", env="test")
    base = load_scenario(FIXTURES / "cases" / "mini" / "scenario.json")
    for sid in ("j3-desktop", "j3-tablet"):
        (out / "cases" / sid).mkdir(parents=True, exist_ok=True)
        path = out / "cases" / sid / "scenario.json"
        path.write_text(dump(base.model_copy(update={"id": sid})))
        generate(settings, sid, ApproxCounter())
    # The end-to-end case for J5b (Console window mode: live investigation, then response).
    from gbya.cases.store import import_e2e

    (out / "cases" / "mini-e2e").mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURES / "cases" / "mini-e2e" / "e2e.json", out / "cases" / "mini-e2e")
    with db.session_scope(factory) as s:
        w = s.get(Window, MINI_ID)
        assert w is not None
        w.techniques = ["T1003.001"]
        import_e2e(s, settings, "mini-e2e")
    # End-to-end windows to annotate in J3b (one per viewport: adjudication locks the window).
    from gbya.cases.models import XLabels, e2e_path, load_e2e
    from gbya.cases.store import write_e2e

    base_x = load_e2e(e2e_path(out / "cases", "mini-e2e"))
    for xid in ("x-desktop", "x-tablet"):
        write_e2e(settings, base_x.model_copy(update={"id": xid, "labels": XLabels(),
                                                      "context_author": "Alex"}))  # fmt: skip
        with db.session_scope(factory) as s:
            import_e2e(s, settings, xid)
    if record:
        _record_replay(out, settings, factory)
    print(f"e2e fixture ready in {out}")


def _record_replay(out: Path, settings: Any, factory: Any) -> None:
    """The replay-profile journey (T8.3): record the Console demo episode (j3-desktop:E2, G3,
    run 1) with the Fake model into ``<out>/replay.jsonl`` through the same episode path as the
    Console, so a server with ``GBYA_LLM_BACKEND=replay`` can replay it without a model."""
    from gbya.experiments import exp2
    from gbya.experiments.runner import Deps
    from gbya.llm.factory import make_client
    from gbya.llm.tokens import default_counter

    rec = settings.model_copy(update={"llm_backend": "fake",
                                      "record_cassette": out / "replay.jsonl",
                                      "reranker_dir": out / "no-reranker"})  # fmt: skip
    deps = Deps(settings=rec, client=make_client(rec), counter=default_counter())
    with db.session_scope(factory) as s:
        case = exp2.load_episode_case(s, "j3-desktop:E2", rec)
    slot = exp2.load_spec().schedule[1]
    exp2.run_episode(case, deps.configs["G3"], slot, deps, factory)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    build(Path(args[0]).resolve() if args else REPO / "data" / "e2e",
          record="--record-replay" in sys.argv)  # fmt: skip

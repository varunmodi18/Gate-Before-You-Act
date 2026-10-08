"""T4.3: variant builder and deterministic prefixes on the mini scenario (plan §D.11)."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from gbya.cases.builder import BuildError, build_cases, sample_e2, window_facts
from gbya.cases.cli import generate, prefix_path
from gbya.cases.models import Labels, ScenarioFile, load_casefile, load_scenario
from gbya.cases.prefix import build_prefix
from gbya.cases.store import case_db
from gbya.config import Settings
from gbya.data.catalogue import parse_metadata
from gbya.data.connection import open_case_db
from gbya.data.normalise import normalise_window
from gbya.experiments.exp1 import load_case
from gbya.llm.tokens import ApproxCounter
from gbya.store import db
from gbya.store.models import Case, Window

MINI = Path(__file__).resolve().parents[1] / "fixtures" / "mini_window"
FIX = Path(__file__).resolve().parents[1] / "fixtures" / "cases"
H, Y = "WKSTN-01.lab.local", "HR001.lab.local"
WID = "SDWIN-MINI-000001"


def make_env(tmp: Path) -> Settings:
    settings = Settings(app_db_path=tmp / "app.db", data_dir=tmp / "data", cases_dir=tmp / "cases",
                        env="test", reranker_dir=tmp / "none")  # fmt: skip
    normalise_window(parse_metadata(MINI / f"datasets/atomic/_metadata/{WID}.yaml", MINI), MINI,
                     tmp / "data" / "duckdb" / "windows" / f"{WID}.duckdb")  # fmt: skip
    (tmp / "cases" / "mini").mkdir(parents=True)
    shutil.copy(FIX / "mini" / "scenario.json", tmp / "cases" / "mini" / "scenario.json")
    db.upgrade(settings.app_db_path)
    with db.session_scope(db.make_sessionmaker(db.make_engine(settings.app_db_path))) as s:
        s.add(Window(id=WID, title="mini", split="dev"))
    return settings


@pytest.fixture(scope="module")
def generated(tmp_path_factory: pytest.TempPathFactory) -> tuple[Settings, dict[str, Any]]:
    settings = make_env(tmp_path_factory.mktemp("gen"))
    return settings, generate(settings, "mini", ApproxCounter())


def scenario() -> ScenarioFile:
    return load_scenario(FIX / "mini" / "scenario.json")


def facts(settings: Settings) -> Any:
    return window_facts(settings.data_dir / "duckdb" / "windows" / f"{WID}.duckdb")


# ---- builder ---------------------------------------------------------------------------------------


def test_variant_table(generated: tuple[Settings, dict[str, Any]]) -> None:
    settings, _ = generated
    sc, f = scenario(), facts(settings)
    cases = {c.variant: c for c in build_cases(sc, f)}
    assert list(cases) == ["E1", "E2", "E3", "E4", "E5", "R_pos", "R_neg"]
    assert cases["E1"].package == sc.e1 and cases["E1"].db_patch is None
    assert cases["E3"].package.cited == sc.e1.cited
    assert cases["E3"].db_patch is not None and cases["E3"].db_patch.ops[0].model_dump() == {
        "op": "move_host", "record_ids": sc.suspicious_record_ids, "host": Y}  # fmt: skip
    assert cases["E4"].package.cited == [4] and cases["E4"].labels.e4_kind == "partial_chain"
    assert cases["E5"].db_patch is not None and len(cases["E5"].db_patch.ops) == 2
    assert (cases["R_pos"].r_edit.value, cases["R_neg"].r_edit.value) == (2, 0)  # type: ignore[union-attr]
    for c in cases.values():  # request and the agent's claim identical everywhere
        assert c.request == sc.request and c.package.technique_claimed == sc.e1.technique_claimed
        assert c.package.rationale == sc.e1.rationale and c.package.args == sc.e1.args


def test_e2_sampling_is_seeded_benign_and_on_the_target_host(
    generated: tuple[Settings, dict[str, Any]],
) -> None:
    settings, _ = generated
    sc, f = scenario(), facts(settings)
    ids = sample_e2(sc, f)
    assert ids == sample_e2(sc, f) and len(ids) == len(sc.e1.cited)
    assert not set(ids) & (set(sc.suspicious_record_ids) | set(sc.e1.cited))
    assert all(f.hosts[i] == H for i in ids)
    other = sc.model_copy(update={"e2": sc.e2.model_copy(update={"same_host": False, "seed": 1})})
    assert all(i not in sc.suspicious_record_ids for i in sample_e2(other, f))
    starved = sc.model_copy(update={"e1": sc.e1.model_copy(update={"cited": list(range(1, 30))})})
    with pytest.raises(BuildError, match="benign records"):
        sample_e2(starved, f)


def test_e4_contradiction_cites_the_added_record(
    generated: tuple[Settings, dict[str, Any]],
) -> None:
    settings, _ = generated
    sc = scenario()
    contra = sc.model_copy(update={"e4": sc.e4.model_validate(  # type: ignore[union-attr]
        {"kind": "contradiction", "add": {"op": "add", "from_record": 4,
                                          "set": {"CommandLine": "dumper.exe --selftest"}}})})  # fmt: skip
    f = facts(settings)
    e4 = next(c for c in build_cases(contra, f) if c.variant == "E4")
    assert e4.package.cited == [*sc.e1.cited, f.max_record_id + 1]
    assert e4.labels.e4_kind == "contradiction"


def test_labels_carried_over_and_missing_specs_skip_variants(
    generated: tuple[Settings, dict[str, Any]],
) -> None:
    settings, _ = generated
    sc = scenario().model_copy(update={"e3": None, "e5": None, "set_r": None})
    cases = build_cases(sc, facts(settings), {"E1": Labels(technique_gold="T1003.001")})
    assert [c.variant for c in cases] == ["E1", "E2", "E4"]
    assert cases[0].labels.technique_gold == "T1003.001" and cases[1].labels == Labels()


# ---- generation (construction path) and prefixes ---------------------------------------------------


def test_generate_writes_files_dbs_and_imports(generated: tuple[Settings, dict[str, Any]]) -> None:
    settings, report = generated
    assert report["variants"] == ["E1", "E2", "E3", "E4", "E5", "R_pos", "R_neg"]
    for v in ("E3", "E4", "E5"):
        path = case_db(settings, f"mini:{v}")
        assert path.is_file() and stat.S_IMODE(path.stat().st_mode) == 0o444
    con = open_case_db(case_db(settings, "mini:E3"))
    on_target = con.execute(
        "SELECT count(*) FROM process_create WHERE host = ? AND record_id IN (1, 4)", [H]).fetchone()  # fmt: skip
    con.close()
    assert on_target == (0,)
    with db.session_scope(db.make_sessionmaker(db.make_engine(settings.app_db_path))) as s:
        rows = {c.variant: c for c in s.query(Case).filter(Case.scenario_id == "mini")}
        assert set(rows) == set(report["variants"]) and all(r.content_hash for r in rows.values())
        case = load_case(s, "mini:E2", settings)
    e2 = load_casefile(settings.cases_dir / "mini" / "cases" / "E2.json")
    assert set(e2.package.cited) <= case.retrieved  # C2 sees the prefix registry


def test_prefix_contains_cited_records_and_distractors(
    generated: tuple[Settings, dict[str, Any]],
) -> None:
    settings, report = generated
    for variant in report["variants"]:
        p = json.loads(prefix_path(settings, f"mini:{variant}").read_text())
        cited = load_casefile(
            settings.cases_dir / "mini" / "cases" / f"{variant}.json"
        ).package.cited
        assert 2 <= len(p["queries"]) <= 4
        assert set(cited) <= set(p["retrieved"])  # every cited id is in the results
        assert set(p["retrieved"]) - set(cited)  # and distractor rows
        for q in p["queries"]:
            assert (
                q["result"].startswith("<<UNTRUSTED_LOG_DATA>>")
                and "more row(s)" not in q["result"]
            )


def test_prefix_is_byte_deterministic(
    generated: tuple[Settings, dict[str, Any]], tmp_path: Path
) -> None:
    settings, _ = generated
    first = {v: prefix_path(settings, f"mini:{v}").read_bytes() for v in ("E1", "E3", "R_neg")}
    sc = scenario()
    e1 = load_casefile(settings.cases_dir / "mini" / "cases" / "E1.json")
    wdb = settings.data_dir / "duckdb" / "windows" / f"{WID}.duckdb"

    class Generous:  # a different token counter: results are never truncated, bytes unchanged
        name = "generous"

        def count(self, text: str) -> int:
            return len(text) // 10

    again = build_prefix(e1, sc, wdb, sc.trusted_context, Generous()).text().encode()
    assert again == first["E1"]
    generate(settings, "mini", ApproxCounter())
    assert {v: prefix_path(settings, f"mini:{v}").read_bytes() for v in first} == first


def test_cli_runs_as_a_subprocess(tmp_path: Path) -> None:
    settings = make_env(tmp_path)
    env = {**{k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}, "GBYA_ENV": "test",
           "GBYA_APP_DB_PATH": str(settings.app_db_path), "GBYA_DATA_DIR": str(settings.data_dir),
           "GBYA_CASES_DIR": str(settings.cases_dir), "GBYA_LOG_DIR": str(tmp_path / "logs")}  # fmt: skip
    res = subprocess.run([sys.executable, "-m", "gbya.cases.cli", "generate", "mini"],
                         capture_output=True, text=True, env=env, check=True)  # fmt: skip
    out = json.loads(res.stdout[res.stdout.index("{") :])
    assert out["patches"]["E4"]["removed"] == [5, 7]
    assert (settings.cases_dir / "mini" / "cases" / "R_neg.json").is_file()

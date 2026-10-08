"""T3.1: content-only retrieval index — hold-out, gold map, query, determinism (plan §D.3)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gbya.gate.evidence import CitedRecord
from gbya.retrieval import index as ix
from gbya.retrieval import sources
from gbya.retrieval.corpus import load_attack, load_sigma, technique_tags
from gbya.retrieval.gold import GoldMap
from gbya.retrieval.query import build_query
from gbya.retrieval.text import held_out_violations, strip_technique_ids, tokenise

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "retrieval"
ATTACK = FIX / "attack" / "enterprise-attack-test.json"
REQS = {"kill_process": "At least one cited record on host H shows process P itself performing"}


def _src() -> sources.Sources:
    return sources.Sources(FIX / "sigma", "f" * 40, ATTACK, "0" * 64, FIX / "attack/LICENSE.txt")


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict]:  # type: ignore[type-arg]
    out = tmp_path_factory.mktemp("index")
    return out, ix.build(_src(), out)


# ---- hold-out ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "kept"),
    [
        ("tags attack.t1003.001 and attack.credential-access", "tags and"),
        ("see T1003.001 or t1033, (T1059)", "see or , ( )"),  # raw removal; tidy() drops "( )"
        ("xt1003 T10034 is not an ID", "xt1003 T10034 is not an ID"),
    ],
)
def test_strip_technique_ids(text: str, kept: str) -> None:
    assert " ".join(strip_technique_ids(text).split()) == kept


def test_tidy_drops_brackets_left_by_removed_ids() -> None:
    from gbya.retrieval.text import tidy

    assert tidy(
        strip_technique_ids("access (attack.t1003.001), as T1003 tools do (T1059, T1003).")
    ) == ("access, as tools do.")


def test_no_tag_or_technique_id_in_any_indexed_document(built: tuple[Path, dict]) -> None:  # type: ignore[type-arg]
    out, _ = built
    idx = ix.load(out)
    docs = idx.sigma.docs + idx.attack.docs
    assert len(idx.sigma.docs) == 4 and len(idx.attack.docs) == 3  # deprecated technique dropped
    for d in docs:
        assert held_out_violations(d.index_text) == [], d.doc_id
        assert not any(t.startswith("t1") and t[1:].isdigit() for t in tokenise(d.index_text))
    lsass = idx.sigma.by_id["00000000-0000-4000-8000-000000000001"]
    assert lsass.title == "Test LSASS Memory Access"
    assert "credential" not in lsass.index_text  # tags are never indexed
    assert lsass.meta["author"] == "Test Author" and lsass.meta["uri"].endswith(
        "rules/windows/process_access/lsass_access.yml"
    )
    t1003 = next(d for d in idx.attack.docs if d.meta["technique_id"] == "T1003")
    assert "Mimikatz" in t1003.description and "Citation" not in t1003.description
    assert "attack.mitre.org" not in t1003.description


def test_attack_detection_text_from_windows_analytics(built: tuple[Path, dict]) -> None:  # type: ignore[type-arg]
    idx = ix.load(built[0])
    lsass = next(d for d in idx.attack.docs if d.meta["technique_id"] == "T1003.001")
    assert "Detect LSASS memory access" in lsass.detection
    assert "GrantedAccess 0x1010" in lsass.detection and "EventCode=10" in lsass.detection
    assert "Data components: Process Access" in lsass.detection
    assert "ptrace" not in lsass.detection  # Linux analytic excluded


# ---- gold map (scoring only) ------------------------------------------------------------------


def test_gold_map(built: tuple[Path, dict]) -> None:  # type: ignore[type-arg]
    gold = GoldMap.load(built[0])
    r1, r2, r3 = (f"00000000-0000-4000-8000-00000000000{i}" for i in (1, 2, 3))
    assert gold.gold_rules("T1003.001") == {r1}
    assert gold.gold_rules("T1003.002") == {r2}  # no sub-technique rule: parent-tagged rules
    assert gold.gold_rules("T1003") == {r1, r2}  # technique: its own and its sub-techniques' rules
    assert gold.gold_rules("T1055") == set() and gold.gold_rules("T1033") == {r3}
    t1003 = "attack-pattern--0001"
    assert gold.attack_correct(t1003, "T1003.001") and gold.attack_correct(t1003, "T1003")
    assert not gold.attack_correct("attack-pattern--0002", "T1003")  # sub ≠ parent gold
    assert not gold.attack_correct(None, "T1003")


def test_technique_tags() -> None:
    assert technique_tags(["attack.t1003.001", "attack.T1059", "attack.execution", "car.x"]) == [
        "T1003.001",
        "T1059",
    ]


def test_retriever_never_reads_the_gold_map(built: tuple[Path, dict], tmp_path: Path) -> None:  # type: ignore[type-arg]
    out, _ = built
    for name in ("sigma_docs.json", "attack_docs.json", "MANIFEST.json"):
        (tmp_path / name).write_bytes((out / name).read_bytes())
    idx = ix.load(tmp_path)  # no gold_map.json present
    assert idx.retrieve("lsass 0x1010", "bm25").sigma_ranking


# ---- query ------------------------------------------------------------------------------------


def test_build_query_uses_only_canonical_fields() -> None:
    access = CitedRecord(7, "process_access", {
        "event_id": 10, "source_image": r"C:\Tools\procdump.exe", "target_image": r"C:\Windows\System32\lsass.exe",
        "granted_access": "0x1010", "host": "WS1", "source_pid": 4242, "call_trace": "ntdll.dll",
    })  # fmt: skip
    create = CitedRecord(3, "process_create", {
        "event_id": 1, "image": r"C:\Tools\procdump.exe", "parent_image": r"C:\Windows\cmd.exe",
        "command_line": "procdump -ma lsass.exe out.dmp", "user": "CORP\\alice", "pid": 4242,
    })  # fmt: skip
    raw_only = CitedRecord(9, None, {}, {"EventID": 4688, "Hostname": "WS1"})
    q = build_query("kill_process", [access, create, raw_only], REQS)
    assert q.splitlines() == [
        "kill process",
        REQS["kill_process"],
        r"process_access 10 C:\Tools\procdump.exe C:\Windows\System32\lsass.exe 0x1010",
        r"process_create 1 C:\Tools\procdump.exe C:\Windows\cmd.exe procdump -ma lsass.exe out.dmp",
        "raw_events 4688",
    ]
    for absent in ("4242", "WS1", "alice", "ntdll", "T1003"):
        assert absent not in q
    assert {"windows", "system32", "lsass", "exe"} <= set(tokenise(q))  # paths split


# ---- retrieve and determinism -----------------------------------------------------------------


def test_retrieve_bm25_ranks_by_content(built: tuple[Path, dict]) -> None:  # type: ignore[type-arg]
    idx = ix.load(built[0])
    r = idx.retrieve("process_access 10 lsass.exe 0x1010", "bm25")
    assert r.sigma_top5[0].doc_id.endswith("01") and r.sigma_top5[0].rank == 1
    assert (
        r.attack_top1 is not None
        and idx.doc(r.attack_top1.doc_id).meta["technique_id"] == "T1003.001"
    )
    assert r.query_hash == ix.query_hash("process_access 10 lsass.exe 0x1010") and not r.warnings
    assert [h.rank for h in r.sigma_ranking] == list(range(1, len(r.sigma_ranking) + 1))


def test_ties_break_by_doc_id_and_unmatched_docs_are_not_ranked(built: tuple[Path, dict]) -> None:  # type: ignore[type-arg]
    idx = ix.load(built[0])
    r = idx.retrieve("whoami", "bm25")
    ids = [h.doc_id for h in r.sigma_ranking]
    assert ids == ["00000000-0000-4000-8000-000000000000", "00000000-0000-4000-8000-000000000003"]
    assert r.sigma_ranking[0].bm25 == r.sigma_ranking[1].bm25


def test_modes_and_failure_behaviour(built: tuple[Path, dict]) -> None:  # type: ignore[type-arg]
    idx = ix.load(built[0])
    none = idx.retrieve("lsass", "none")
    assert none.sigma_ranking == [] and none.attack_ranking == [] and not none.warnings
    empty = idx.retrieve("zzzz qqqq", "bm25")
    assert empty.sigma_ranking == [] and empty.attack_top1 is None
    assert empty.warnings == [
        "no Sigma document matches the query",
        "no ATT&CK document matches the query",
    ]
    with pytest.raises(ix.RerankerUnavailable):
        idx.retrieve("lsass", "bm25_rerank")  # never a silent fallback to bm25
    rr = idx.retrieve(
        "lsass", "bm25_rerank", reranker=lambda q, docs: [float(len(d.title)) for d in docs]
    )
    assert rr.mode == "bm25_rerank" and all(h.rerank is not None for h in rr.sigma_ranking)
    assert [h.rerank for h in rr.sigma_ranking] == sorted(
        (h.rerank for h in rr.sigma_ranking), reverse=True
    )  # type: ignore[type-var]


def test_build_is_deterministic(built: tuple[Path, dict], tmp_path: Path) -> None:  # type: ignore[type-arg]
    out, manifest = built
    again = ix.build(_src(), tmp_path)
    assert again == manifest
    for name in ("sigma_docs.json", "attack_docs.json", "gold_map.json", "MANIFEST.json"):
        assert (tmp_path / name).read_bytes() == (out / name).read_bytes()
    a, b = ix.load(out), ix.load(tmp_path)
    q = "process_create procdump.exe -ma lsass whoami"
    assert a.retrieve(q, "bm25") == b.retrieve(q, "bm25") == a.retrieve(q, "bm25")


def test_load_refuses_a_modified_index(built: tuple[Path, dict], tmp_path: Path) -> None:  # type: ignore[type-arg]
    out, _ = built
    for name in ("sigma_docs.json", "attack_docs.json", "MANIFEST.json"):
        (tmp_path / name).write_bytes((out / name).read_bytes())
    docs = json.loads((tmp_path / "sigma_docs.json").read_text())
    docs[0]["title"] += " T1003"
    (tmp_path / "sigma_docs.json").write_text(json.dumps(docs))
    with pytest.raises(ix.RetrievalError, match="does not match MANIFEST"):
        ix.load(tmp_path)
    with pytest.raises(ix.RetrievalError, match="make index"):
        ix.load(tmp_path / "missing")


# ---- licences -----------------------------------------------------------------------------------


def test_licence_check_accepts_the_approved_licences() -> None:
    sources.check_licence((FIX / "sigma/LICENSE").read_text(), sources.SIGMA_LICENCE_MARKERS, "s")
    sources.check_licence(
        (FIX / "attack/LICENSE.txt").read_text(), sources.ATTACK_LICENCE_MARKERS, "a"
    )


@pytest.mark.parametrize(
    ("text", "markers"),
    [
        (
            "Rules are released under the Detection Rule License (DRL) 2.0",
            sources.SIGMA_LICENCE_MARKERS,
        ),
        ("Creative Commons Attribution 4.0", sources.SIGMA_LICENCE_MARKERS),
        ("ATT&CK is licensed under Apache-2.0", sources.ATTACK_LICENCE_MARKERS),
    ],
)
def test_licence_check_stops_on_any_other_licence(text: str, markers: tuple[str, ...]) -> None:
    with pytest.raises(sources.LicenceError, match="Q-4"):
        sources.check_licence(text, markers, "src")


def test_attack_fetch_verifies_the_pinned_checksum(tmp_path: Path) -> None:
    (tmp_path / "LICENSE.txt").write_bytes((FIX / "attack/LICENSE.txt").read_bytes())
    (tmp_path / sources.ATTACK_FILE).write_text("{}")
    with pytest.raises(sources.FetchError, match="sha256"):
        sources.fetch_attack(tmp_path, expected_sha256="0" * 64)
    pinned = sources.sha256_file(tmp_path / sources.ATTACK_FILE)
    path, digest = sources.fetch_attack(tmp_path, expected_sha256=pinned)
    assert path.name == sources.ATTACK_FILE and digest == pinned


# ---- the real index (skipped until `make index` has run) ----------------------------------------

REAL = Path(__file__).resolve().parents[2] / "data" / "index"


@pytest.mark.skipif(not (REAL / "MANIFEST.json").exists(), reason="run `make index` first")
def test_real_index_hold_out_and_gold_map() -> None:
    idx = ix.load(REAL)
    for d in idx.sigma.docs + idx.attack.docs:
        assert held_out_violations(d.index_text) == [], d.doc_id
    assert GoldMap.load(REAL).gold_rules("T1003.001")
    assert idx.manifest["sigma"]["commit"] == sources.SIGMA_COMMIT
    assert idx.manifest["attack"]["sha256"] == sources.ATTACK_SHA256


def test_load_attack_and_sigma_directly() -> None:
    rules = load_sigma(FIX / "sigma", "abc")
    assert [r.doc.doc_id[-1] for r in rules] == ["0", "1", "2", "3"]
    assert rules[1].techniques == ["T1003.001"] and rules[0].techniques == []
    assert "logsource: category: process_access" in rules[1].doc.detection
    assert "selection: TargetImage|endswith: \\lsass.exe" in rules[1].doc.detection
    assert [d.meta["technique_id"] for d in load_attack(ATTACK)] == ["T1003", "T1003.001", "T1033"]

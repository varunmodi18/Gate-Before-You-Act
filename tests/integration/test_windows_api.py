"""T1.4: Windows API against a temporary app.db and the normalised mini window."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gbya.api.main import create_app
from gbya.config import Settings
from gbya.data.catalogue import parse_metadata, scan, upsert
from gbya.data.normalise import normalise_window
from gbya.store import db
from gbya.store.models import Window

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
MINI = FIXTURES / "mini_window"
MINI_ID = "SDWIN-MINI-000001"


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    tmp = tmp_path_factory.mktemp("api")
    app_db = tmp / "app.db"
    db.upgrade(app_db)
    mini_entry = parse_metadata(MINI / "datasets/atomic/_metadata/SDWIN-MINI-000001.yaml", MINI)
    duck = tmp / "duckdb" / "windows" / f"{MINI_ID}.duckdb"
    res = normalise_window(mini_entry, MINI, duck)
    factory = db.make_sessionmaker(db.make_engine(app_db))
    with db.session_scope(factory) as s:
        upsert(s, [*scan(FIXTURES / "otrf"), mini_entry])
        s.flush()
        w = s.get(Window, MINI_ID)
        assert w is not None
        w.duckdb_path, w.event_count, w.hosts = str(duck), res.events, res.hosts
        w.ingest_status, w.split = res.status, "dev"
    settings = Settings(app_db_path=app_db, data_dir=tmp, frontend_dist=tmp / "no-spa")
    return TestClient(create_app(settings))


def _err(resp_json: dict) -> str:  # type: ignore[type-arg]
    return str(resp_json["error"]["code"])


# ---------------------------------------------------------------- listing


def test_list_windows(client: TestClient) -> None:
    r = client.get("/api/v1/windows")
    assert r.status_code == 200
    ids = [w["id"] for w in r.json()]
    assert ids == sorted(ids) and len(ids) == 4 and MINI_ID in ids
    mini = next(w for w in r.json() if w["id"] == MINI_ID)
    assert mini["tactic_names"] == ["credential_access"] and mini["event_count"] == 25


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"tactic": "credential_access"}, {"SDWIN-000000000001", MINI_ID}),
        ({"tactic": "TA0007"}, {"SDWIN-000000000003"}),
        ({"split": "dev"}, {MINI_ID}),
        ({"q": "registry"}, {"SDWIN-000000000002"}),
        ({"q": "T1134.002"}, {"SDWIN-000000000003"}),
        ({"tactic": "impact"}, set()),
    ],
)
def test_list_filters(client: TestClient, params: dict[str, str], expected: set[str]) -> None:
    r = client.get("/api/v1/windows", params=params)
    assert {w["id"] for w in r.json()} == expected


def test_get_window_and_unknown(client: TestClient) -> None:
    assert client.get(f"/api/v1/windows/{MINI_ID}").json()["hosts"][0] == "WKSTN-01.lab.local"
    r = client.get("/api/v1/windows/SDWIN-NOPE")
    assert r.status_code == 404 and _err(r.json()) == "WINDOW_NOT_FOUND"


# ---------------------------------------------------------------- table rows


def test_table_rows_and_pagination(client: TestClient) -> None:
    r = client.get(f"/api/v1/windows/{MINI_ID}/tables/process_access", params={"limit": 2})
    body = r.json()
    assert r.status_code == 200 and body["total"] == 4 and len(body["rows"]) == 2
    assert body["columns"][:5] == ["record_id", "ts", "host", "channel", "event_id"]
    assert body["rows"][0][0] == 5 and body["rows"][0][1] == "2020-10-18T10:00:06"
    page2 = client.get(
        f"/api/v1/windows/{MINI_ID}/tables/process_access", params={"limit": 2, "offset": 2}
    ).json()
    assert [r[0] for r in page2["rows"]] == [7, 25]


def test_table_filter(client: TestClient) -> None:
    r = client.get(
        f"/api/v1/windows/{MINI_ID}/tables/process_access",
        params=[("filter", "source_image:dumper"), ("filter", "channel:sysmon")],
    )
    assert r.json()["total"] == 1 and r.json()["rows"][0][0] == 5


def test_filter_cannot_inject(client: TestClient) -> None:
    r = client.get(
        f"/api/v1/windows/{MINI_ID}/tables/network",
        params={"filter": "dst_ip:' OR 1=1 --"},
    )
    assert r.status_code == 200 and r.json()["total"] == 0  # treated as text, not SQL
    bad = client.get(
        f"/api/v1/windows/{MINI_ID}/tables/network", params={"filter": 'x" OR 1=1 --:a'}
    )
    assert bad.status_code == 400 and _err(bad.json()) == "BAD_FILTER"


def test_raw_events_browsable_and_unknown_table(client: TestClient) -> None:
    raw = client.get(f"/api/v1/windows/{MINI_ID}/tables/raw_events").json()
    assert raw["total"] == 25 and raw["columns"] == ["record_id", "channel", "event_id", "json"]
    r = client.get(f"/api/v1/windows/{MINI_ID}/tables/_meta")
    assert r.status_code == 404 and _err(r.json()) == "TABLE_NOT_FOUND"


def test_page_size_validation_uses_envelope(client: TestClient) -> None:
    r = client.get(f"/api/v1/windows/{MINI_ID}/tables/network", params={"limit": 501})
    assert r.status_code == 422 and _err(r.json()) == "VALIDATION_ERROR"


def test_not_ingested_window(client: TestClient) -> None:
    r = client.get("/api/v1/windows/SDWIN-000000000001/tables/network")
    assert r.status_code == 404 and _err(r.json()) == "WINDOW_NOT_INGESTED"


# ---------------------------------------------------------------- record detail


def test_record_detail_normalised_and_raw(client: TestClient) -> None:
    body = client.get(f"/api/v1/windows/{MINI_ID}/records/5").json()
    assert body["table"] == "process_access"
    assert body["normalised"]["target_image"] == "C:\\Windows\\system32\\lsass.exe"
    assert body["raw"]["SourceUser"] == "LAB\\a.mehta"  # original, unnormalised


def test_record_detail_raw_only_and_missing(client: TestClient) -> None:
    body = client.get(f"/api/v1/windows/{MINI_ID}/records/24").json()  # Sysmon 7, unmapped
    assert body["table"] is None and body["raw"]["EventID"] == 7
    r = client.get(f"/api/v1/windows/{MINI_ID}/records/999")
    assert r.status_code == 404 and _err(r.json()) == "RECORD_NOT_FOUND"


# ---------------------------------------------------------------- guarded SQL


def test_guarded_query(client: TestClient) -> None:
    r = client.post(
        f"/api/v1/windows/{MINI_ID}/query",
        json={"sql": "SELECT record_id, source_pid FROM process_access ORDER BY record_id"},
    )
    body = r.json()
    assert r.status_code == 200 and body["columns"] == ["record_id", "source_pid"]
    assert body["rows"][0] == [5, 4100] and body["sql"].endswith("LIMIT 50")


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE process_access",
        "SELECT * FROM read_csv_auto('/etc/passwd')",
        "SELECT 1; DELETE FROM network",
        "SELECT * FROM _meta",
    ],
)
def test_guarded_query_rejections(client: TestClient, sql: str) -> None:
    r = client.post(f"/api/v1/windows/{MINI_ID}/query", json={"sql": sql})
    assert r.status_code == 400 and _err(r.json()) == "SQL_REJECTED"
    assert r.json()["error"]["message"]


def test_drop_did_not_happen(client: TestClient) -> None:
    client.post(f"/api/v1/windows/{MINI_ID}/query", json={"sql": "DROP TABLE process_access"})
    assert client.get(f"/api/v1/windows/{MINI_ID}/tables/process_access").json()["total"] == 4


def test_empty_sql_is_a_validation_error(client: TestClient) -> None:
    r = client.post(f"/api/v1/windows/{MINI_ID}/query", json={"sql": ""})
    assert r.status_code == 422 and _err(r.json()) == "VALIDATION_ERROR"

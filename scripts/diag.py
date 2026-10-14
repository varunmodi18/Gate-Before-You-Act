"""Diagnostics (plan §J.5): ``make diag``. Prints versions, GPU memory, free disk, database
counts, the frozen case set and the model server's state, for troubleshooting and bug reports.
Read-only; needs no model and no GPU."""

from __future__ import annotations

import json
import platform
import shutil
import sqlite3
import subprocess
from importlib import metadata
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]


def _run(cmd: list[str]) -> str | None:
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=5, check=True
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _version(pkg: str) -> str | None:
    try:
        return metadata.version(pkg)
    except metadata.PackageNotFoundError:
        return None


def collect() -> dict[str, Any]:
    from gbya.config import get_settings

    s = get_settings()
    db = s.resolve(s.app_db_path)
    counts: dict[str, Any] = {}
    if db.is_file():
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            for table in (
                "windows",
                "scenarios",
                "cases",
                "annotations",
                "runs",
                "episodes",
                "gate_decisions",
                "verifier_evals",
            ):
                counts[table] = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            counts["migration"] = con.execute("SELECT version_num FROM alembic_version").fetchone()[
                0
            ]
        except sqlite3.Error as exc:
            counts["error"] = str(exc)
        finally:
            con.close()
    frozen = s.resolve(s.cases_dir) / "FROZEN.json"
    case_set = json.loads(frozen.read_text()) if frozen.is_file() else None
    model = None
    try:
        import httpx

        r = httpx.get(f"{s.model_base_url}/models", timeout=1.0)
        model = f"http {r.status_code}"
    except Exception as exc:  # any failure means "not reachable"
        model = f"unreachable ({type(exc).__name__})"
    usage = shutil.disk_usage(REPO)
    return {
        "git_sha": _run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"]),
        "branch": _run(["git", "-C", str(REPO), "branch", "--show-current"]),
        "python": platform.python_version(),
        "packages": {
            p: _version(p) for p in ("fastapi", "duckdb", "sqlalchemy", "numpy", "pydantic", "mcp")
        },
        "gpu": _run(
            ["nvidia-smi", "--query-gpu=name,memory.used,memory.total", "--format=csv,noheader"]
        ),
        "disk_free_gb": round(usage.free / 1e9, 1),
        "settings": {
            "app_db": str(db),
            "data_dir": str(s.resolve(s.data_dir)),
            "cases_dir": str(s.resolve(s.cases_dir)),
            "llm_backend": s.llm_backend,
            "model_base_url": s.model_base_url,
        },
        "database": counts,
        "index": (s.resolve(s.data_dir) / "index" / "MANIFEST.json").is_file(),
        "frozen_case_set": {
            "version": case_set.get("version"),
            "case_set_hash": case_set.get("case_set_hash"),
        }
        if case_set
        else None,
        "model_server": model,
    }


if __name__ == "__main__":
    print(json.dumps(collect(), indent=2))

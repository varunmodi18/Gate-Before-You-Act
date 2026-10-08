"""T1.1: pinned, sparse OTRF fetch — exercised against a local git repository (no network)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gbya.data.fetch import OTRF_COMMIT, OTRF_COMMIT_PREFIX, FetchError, fetch_otrf


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def origin(tmp_path: Path) -> tuple[Path, str, str]:
    """A repo with metadata, windows data and an unrelated directory; two commits."""
    repo = tmp_path / "origin"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "uploadpack.allowFilter", "true")
    _git(repo, "config", "uploadpack.allowAnySHA1InWant", "true")
    meta = repo / "datasets/atomic/_metadata"
    win = repo / "datasets/atomic/windows/credential_access/host"
    other = repo / "datasets/compound"
    for d in (meta, win, other):
        d.mkdir(parents=True)
    for i in range(3):
        (meta / f"SDWIN-{i:03d}.yaml").write_text(f"id: SDWIN-{i:03d}\n")
    (win / "lsass.zip").write_bytes(b"PK fake zip")
    (other / "big.zip").write_bytes(b"not wanted")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "pinned")
    pinned = _git(repo, "rev-parse", "HEAD")
    (meta / "SDWIN-999.yaml").write_text("id: later\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "later")
    return repo, pinned, _git(repo, "rev-parse", "HEAD")


def test_fetch_checks_out_pinned_commit_with_sparse_paths(
    tmp_path: Path, origin: tuple[Path, str, str]
) -> None:
    repo, pinned, _later = origin
    dest = tmp_path / "otrf"
    res = fetch_otrf(dest, url=f"file://{repo}", commit=pinned, expected_prefix=pinned[:7])
    assert res.head == pinned and not res.skipped
    assert res.metadata_files == 3  # the later commit's SDWIN-999 is absent
    assert (dest / "datasets/atomic/windows/credential_access/host/lsass.zip").is_file()
    assert not (dest / "datasets/compound").exists()  # outside the sparse paths


def test_refetch_is_a_noop(tmp_path: Path, origin: tuple[Path, str, str]) -> None:
    repo, pinned, _ = origin
    dest = tmp_path / "otrf"
    fetch_otrf(dest, url=f"file://{repo}", commit=pinned, expected_prefix=pinned[:7])
    again = fetch_otrf(dest, url=f"file://{repo}", commit=pinned, expected_prefix=pinned[:7])
    assert again.skipped and again.head == pinned and again.metadata_files == 3


def test_prefix_mismatch_is_refused(tmp_path: Path, origin: tuple[Path, str, str]) -> None:
    repo, pinned, _ = origin
    with pytest.raises(FetchError):
        fetch_otrf(
            tmp_path / "otrf", url=f"file://{repo}", commit=pinned, expected_prefix="d9d40ef"
        )


def test_pinned_constants_match_the_proposal() -> None:
    assert OTRF_COMMIT_PREFIX == "d9d40ef"
    assert OTRF_COMMIT.startswith(OTRF_COMMIT_PREFIX) and len(OTRF_COMMIT) == 40

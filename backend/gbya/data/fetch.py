"""Fetch OTRF Security-Datasets at the pinned commit (plan §D.1 step 1, T1.1).

Only the pinned commit is fetched (``--depth 1 --filter=blob:none``), and only the sparse paths
``datasets/atomic/_metadata/`` and ``datasets/atomic/windows/`` are checked out, so file contents
are downloaded for those paths alone. Re-running is a no-op when the checkout is already at the
pinned commit.

    python -m gbya.data.fetch            # make data-fetch
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from gbya.config import get_settings
from gbya.logging import configure_logging, get_logger

OTRF_URL = "https://github.com/OTRF/Security-Datasets.git"
# The proposal pins d9d40ef; the full SHA makes the fetch exact (resolved 2026-10-08).
OTRF_COMMIT = "d9d40ef123d2c87d5d3df28c96bcab4f0faccc87"
OTRF_COMMIT_PREFIX = "d9d40ef"
SPARSE_PATHS = ("datasets/atomic/_metadata", "datasets/atomic/windows")
METADATA_GLOB = "datasets/atomic/_metadata/SDWIN*.yaml"

log = get_logger("gbya.data.fetch")


class FetchError(RuntimeError):
    pass


@dataclass(frozen=True)
class FetchResult:
    path: Path
    head: str
    metadata_files: int
    skipped: bool


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        raise FetchError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def _head(repo: Path) -> str | None:
    if not (repo / ".git").exists():
        return None
    try:
        return _git(repo, "rev-parse", "HEAD")
    except FetchError:
        return None


def fetch_otrf(
    dest: Path,
    *,
    url: str = OTRF_URL,
    commit: str = OTRF_COMMIT,
    expected_prefix: str = OTRF_COMMIT_PREFIX,
    sparse_paths: tuple[str, ...] = SPARSE_PATHS,
) -> FetchResult:
    """Check out ``commit`` of ``url`` into ``dest`` with only ``sparse_paths``."""
    if not commit.startswith(expected_prefix):
        raise FetchError(f"pinned commit {commit} does not start with {expected_prefix}")

    if _head(dest) == commit:
        count = len(list(dest.glob(METADATA_GLOB)))
        log.info("otrf_already_fetched", path=str(dest), head=commit, metadata_files=count)
        return FetchResult(dest, commit, count, skipped=True)

    dest.mkdir(parents=True, exist_ok=True)
    if not (dest / ".git").exists():
        _git(dest, "init", "-q")
        _git(dest, "remote", "add", "origin", url)
    _git(dest, "fetch", "-q", "--depth", "1", "--filter=blob:none", "origin", commit)
    _git(dest, "sparse-checkout", "set", *sparse_paths)
    _git(dest, "checkout", "-q", "--detach", commit)

    head = _head(dest)
    if head is None or head != commit or not head.startswith(expected_prefix):
        raise FetchError(f"HEAD is {head}, expected {commit}")
    count = len(list(dest.glob(METADATA_GLOB)))
    log.info("otrf_fetched", path=str(dest), head=head, metadata_files=count)
    return FetchResult(dest, head, count, skipped=False)


def default_dest() -> Path:
    settings = get_settings()
    return settings.resolve(settings.data_dir) / "raw" / "otrf"


def main() -> None:
    settings = get_settings()
    configure_logging("data", settings.resolve(settings.log_dir), dev=settings.env == "dev")
    result = fetch_otrf(default_dest())
    print(
        f"OTRF at {result.head} in {result.path}: {result.metadata_files} SDWIN metadata files"
        + (" (already present)" if result.skipped else "")
    )
    if result.metadata_files != 100:
        raise SystemExit(f"expected 100 SDWIN metadata files, found {result.metadata_files}")


if __name__ == "__main__":
    main()

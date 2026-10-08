"""Fetch the retrieval sources at pinned versions and check their licences (plan §D.3, T3.1, Q-4).

* SigmaHQ at release ``r2026-07-01`` (full commit SHA), sparse checkout of ``rules/windows`` and
  ``LICENSE`` only. The rules are under the Detection Rule License (DRL) 1.1.
* MITRE ATT&CK Enterprise STIX 2.1 bundle, version 19.2, from ``mitre-attack/attack-stix-data``
  at tag ``v19.2``, verified against a pinned SHA-256, plus that repository's ``LICENSE.txt``.

The licence files are checked at the pinned versions on every fetch: if either differs from the
team-approved terms (DRL 1.1; MITRE's ATT&CK licence), the fetch stops with ``LicenceError``.
Neither the rule files nor the index are ever committed (``data/`` is git-ignored; R10).

    python -m gbya.retrieval.sources        # part of make index
"""

from __future__ import annotations

import hashlib
import json
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from gbya.config import get_settings
from gbya.data.fetch import FetchError, _git, _head
from gbya.logging import configure_logging, get_logger

SIGMA_URL = "https://github.com/SigmaHQ/sigma.git"
SIGMA_RELEASE = "r2026-07-01"
# Tag r2026-07-01^{}, resolved 8 Oct 2026.
SIGMA_COMMIT = "552f3fee420ef232a8e5790c4fae591847e32347"
SIGMA_SPARSE = ("rules/windows", "LICENSE")

ATTACK_VERSION = "19.2"
ATTACK_TAG = f"v{ATTACK_VERSION}"
ATTACK_RAW = "https://raw.githubusercontent.com/mitre-attack/attack-stix-data"
ATTACK_FILE = f"enterprise-attack-{ATTACK_VERSION}.json"
ATTACK_URL = f"{ATTACK_RAW}/{ATTACK_TAG}/enterprise-attack/{ATTACK_FILE}"
ATTACK_LICENCE_URL = f"{ATTACK_RAW}/{ATTACK_TAG}/LICENSE.txt"
# Pinned at the first fetch (8 Oct 2026); the file's git blob 8b8a9c8 matches GitHub's at v19.2.
ATTACK_SHA256 = "dc1639caa5501d720e280cf1cbd8fbe009884a0c9b3e6e9ed9d0c25166c3d8f4"

# Phrases that identify the approved terms. Anything else stops the build (team decision Q-4).
SIGMA_LICENCE_MARKERS = ("Detection Rule License (DRL) 1.1",)
ATTACK_LICENCE_MARKERS = (
    "The MITRE Corporation (MITRE) hereby grants you a non-exclusive, royalty-free license to use "
    "ATT&CK",
    "This work is reproduced and distributed with the permission of The MITRE Corporation.",
)

log = get_logger("gbya.retrieval.sources")


class LicenceError(RuntimeError):
    """A source's licence file is not the team-approved licence."""


@dataclass(frozen=True)
class Sources:
    sigma_dir: Path  # checkout root; rules under rules/windows
    sigma_commit: str
    attack_file: Path
    attack_sha256: str
    attack_licence: Path


def check_licence(text: str, markers: tuple[str, ...], source: str) -> None:
    flat = " ".join(text.split())
    missing = [m for m in markers if " ".join(m.split()) not in flat]
    if missing:
        raise LicenceError(
            f"{source}: licence file at the pinned version is not the approved licence "
            f"(missing {missing!r}); stop and ask the team (Q-4)"
        )


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=120) as resp, tmp.open("wb") as out:
        while chunk := resp.read(1 << 20):
            out.write(chunk)
    tmp.replace(dest)


def fetch_sigma(dest: Path, *, url: str = SIGMA_URL, commit: str = SIGMA_COMMIT) -> str:
    """Sparse, blob-filtered checkout of ``commit``; a no-op when already there."""
    if _head(dest) != commit:
        dest.mkdir(parents=True, exist_ok=True)
        if not (dest / ".git").exists():
            _git(dest, "init", "-q")
            _git(dest, "remote", "add", "origin", url)
        _git(dest, "fetch", "-q", "--depth", "1", "--filter=blob:none", "origin", commit)
        _git(dest, "sparse-checkout", "set", *SIGMA_SPARSE)
        _git(dest, "checkout", "-q", "--detach", commit)
    head = _head(dest)
    if head != commit:
        raise FetchError(f"SigmaHQ HEAD is {head}, expected {commit}")
    check_licence((dest / "LICENSE").read_text(), SIGMA_LICENCE_MARKERS, "SigmaHQ")
    return head


def fetch_attack(dest: Path, *, expected_sha256: str = ATTACK_SHA256) -> tuple[Path, str]:
    dest.mkdir(parents=True, exist_ok=True)
    licence = dest / "LICENSE.txt"
    if not licence.exists():
        _download(ATTACK_LICENCE_URL, licence)
    check_licence(licence.read_text(), ATTACK_LICENCE_MARKERS, "MITRE ATT&CK")
    bundle = dest / ATTACK_FILE
    if not bundle.exists():
        _download(ATTACK_URL, bundle)
    digest = sha256_file(bundle)
    if digest != expected_sha256:
        raise FetchError(f"{bundle.name}: sha256 {digest} != pinned {expected_sha256}")
    return bundle, digest


def default_dirs() -> tuple[Path, Path]:
    s = get_settings()
    raw = s.resolve(s.data_dir) / "raw"
    return raw / "sigma", raw / "attack"


def fetch_all() -> Sources:
    sigma_dir, attack_dir = default_dirs()
    head = fetch_sigma(sigma_dir)
    bundle, digest = fetch_attack(attack_dir)
    log.info("retrieval_sources", sigma_commit=head, attack=bundle.name, sha256=digest)
    return Sources(sigma_dir, head, bundle, digest, attack_dir / "LICENSE.txt")


def main() -> None:
    s = get_settings()
    configure_logging("data", s.resolve(s.log_dir), dev=s.env == "dev")
    src = fetch_all()
    print(json.dumps({"sigma_release": SIGMA_RELEASE, "sigma_commit": src.sigma_commit,
                      "attack_version": ATTACK_VERSION, "attack_sha256": src.attack_sha256},
                     indent=2))  # fmt: skip


if __name__ == "__main__":
    main()

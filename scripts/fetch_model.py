"""Download a Hugging Face model repository at a pinned commit and verify every LFS file.

Usage:  python scripts/fetch_model.py <repo_id> <dest_dir> [--revision <sha>] [--include <substr>]

Standard library only. Writes ``<dest_dir>/MANIFEST.json`` with the resolved commit SHA and the
SHA-256 of every file, so runs can record the exact model files (plan §L.4 item 7, FR-23).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

HF = "https://huggingface.co"


def _get_json(url: str) -> object:
    with urllib.request.urlopen(url, timeout=60) as resp:
        return json.load(resp)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, dest: Path, size: int) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    have = tmp.stat().st_size if tmp.exists() else 0
    req = urllib.request.Request(url, headers={"Range": f"bytes={have}-"} if have else {})
    with urllib.request.urlopen(req, timeout=120) as resp, tmp.open("ab" if have else "wb") as out:
        done = have
        while chunk := resp.read(1 << 22):
            out.write(chunk)
            done += len(chunk)
            print(f"\r  {dest.name}: {done / 1e9:.2f}/{size / 1e9:.2f} GB", end="", flush=True)
    print()
    tmp.rename(dest)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo_id")
    ap.add_argument("dest")
    ap.add_argument("--revision", default="main")
    ap.add_argument("--include", action="append", default=[])
    args = ap.parse_args()

    info = _get_json(f"{HF}/api/models/{args.repo_id}/revision/{args.revision}")
    assert isinstance(info, dict)
    sha = info["sha"]
    tree = _get_json(f"{HF}/api/models/{args.repo_id}/tree/{sha}")
    assert isinstance(tree, list)
    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, object] = {"repo_id": args.repo_id, "revision": sha, "files": {}}
    files: dict[str, dict[str, object]] = {}

    for entry in tree:
        if entry.get("type") != "file":
            continue
        name = entry["path"]
        if args.include and not any(s in name for s in args.include) and name.endswith(".gguf"):
            continue
        lfs = entry.get("lfs") or {}
        size = int(lfs.get("size") or entry.get("size") or 0)
        target = dest / name
        if not (target.exists() and target.stat().st_size == size):
            _download(f"{HF}/{args.repo_id}/resolve/{sha}/{name}", target, size)
        digest = _sha256(target)
        expected = lfs.get("oid")
        if expected and digest != expected:
            print(f"SHA-256 MISMATCH for {name}: {digest} != {expected}")
            return 1
        files[name] = {"size": size, "sha256": digest, "lfs_verified": bool(expected)}
        print(f"  ok {name} ({size / 1e6:.1f} MB){' sha256 verified' if expected else ''}")

    manifest["files"] = files
    (dest / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"{args.repo_id}@{sha}: {len(files)} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())

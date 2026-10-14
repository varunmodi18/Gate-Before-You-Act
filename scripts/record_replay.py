"""Record the demo script into a replay cassette (plan T8.2).

    make demo-record                       # demo API + worker, live model, recording on
    uv run python scripts/record_replay.py # drives J4-J6 through the API; Ctrl-C the server after

The server records every successful model call into ``data/fixtures/demo/replay/demo.jsonl``
(``GBYA_RECORD_CASSETTE``). ``make demo-replay`` then serves the same demo from the cassette,
without the model: the header shows REPLAY and runs are flagged ``replay`` (never research).
A call outside the recorded script raises ``REPLAY_MISS``.

The script follows the demo of §E.5: the Gate Playground on every demo-lsass case (G1, G2, A1,
G3; steps 4-6), the Agent Console on E2 (recovery, step 7), on R_neg (approval, step 8) and on the
end-to-end window (investigation), and an Exp 1 demo run with one verifier run (steps 9-10).
Each step waits for its result.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from typing import Any

BASE = "http://127.0.0.1:8000/api/v1"
VARIANTS = ("E1", "E2", "E3", "E4", "E5", "R_pos", "R_neg")


def call(method: str, path: str, body: Any = None, base: str = BASE) -> Any:
    req = urllib.request.Request(base + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})  # fmt: skip
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read())


def wait_episode(eid: int, base: str) -> dict[str, Any]:
    while True:
        ep: dict[str, Any] = call("GET", f"/episodes/{eid}", base=base)
        if ep["status"] != "running":
            return ep
        time.sleep(1)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--scenario", default="demo-lsass")
    ap.add_argument("--window", default="demo-lsass-e2e:X")
    args = ap.parse_args(argv)
    health = call("GET", "/health", base=args.base)
    if not (health.get("model") or {}).get("recording"):
        raise SystemExit("the server is not recording: start it with make demo-record")
    for v in VARIANTS:  # J4: Gate Playground
        cid = f"{args.scenario}:{v}"
        out = call("POST", "/playground/gate", {"case_id": cid,
                                                "systems": ["G1", "G2", "A1", "G3"]},
                   base=args.base)  # fmt: skip
        print("playground", cid, [d["verdict"] for d in out["decisions"]])
    for cid in (f"{args.scenario}:E2", f"{args.scenario}:R_neg", args.window):  # J5, J5b
        eid = call("POST", "/episodes", {"case_id": cid, "system": "G3"}, base=args.base)
        ep = wait_episode(int(eid["episode_id"]), args.base)
        print("console", cid, ep["status"], ep["terminal_state"])
    run = call("POST", "/runs", {"experiment": 1, "purpose": "demo",  # J6: Exp 1 on the demo
                                 "case_ids": [f"{args.scenario}:{v}" for v in VARIANTS],
                                 "config": {"verifier_runs": 1}}, base=args.base)  # fmt: skip
    while (r := call("GET", f"/runs/{run['id']}", base=args.base))["status"] not in (
        "completed", "failed", "cancelled"):  # fmt: skip
        time.sleep(5)
    print("exp1 run", run["id"], r["status"])
    print("recorded into", health["model"]["recording"])


if __name__ == "__main__":
    main()

"""Start the model server from ``config/model_profiles.yaml`` (``make model-up``).

    uv run python scripts/model_server.py [--profile vllm-awq] [--print]

Runs in the foreground with output appended to ``logs/model.log``; ``--print`` only shows the
command. The profile's venv ``bin/`` is put first on PATH (vLLM's FlashInfer JIT needs ``ninja``).
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]


def build_command(profile: dict[str, object]) -> list[str]:
    fields = {k: str(v) for k, v in profile.items() if isinstance(v, str | int | float)}
    cmd = profile.get("command") or []
    assert isinstance(cmd, list)
    if not cmd:
        raise SystemExit(f"profile has no command (status: {profile.get('status', 'unknown')})")
    # Substitute only known {field} placeholders, so literal JSON arguments pass through intact.
    pattern = re.compile(r"\{(" + "|".join(map(re.escape, fields)) + r")\}")
    return [pattern.sub(lambda m: fields[m.group(1)], str(part)) for part in cmd]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile")
    ap.add_argument("--print", action="store_true", help="print the command and exit")
    args = ap.parse_args()

    cfg = yaml.safe_load((REPO / "config" / "model_profiles.yaml").read_text())
    name = args.profile or cfg["default"]
    profile = cfg["profiles"][name]
    cmd = build_command(profile)
    if args.print:
        print(shlex.join(cmd))
        return

    os.chdir(REPO)
    env = dict(os.environ)
    if venv := profile.get("venv"):
        venv_bin = (REPO / str(venv)).resolve() / "bin"
        env["PATH"] = f"{venv_bin}{os.pathsep}{env.get('PATH', '')}"
    extra_env = profile.get("env") or {}
    assert isinstance(extra_env, dict)
    env.update({str(k): str(v) for k, v in extra_env.items()})
    log = REPO / "logs" / "model.log"
    log.parent.mkdir(exist_ok=True)
    print(f"[{name}] {shlex.join(cmd)}\n  log: {log}", file=sys.stderr)
    fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    os.dup2(fd, 1)
    os.dup2(fd, 2)
    os.execvpe(cmd[0], cmd, env)


if __name__ == "__main__":
    main()

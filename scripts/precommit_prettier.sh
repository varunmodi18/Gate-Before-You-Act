#!/usr/bin/env bash
# Pre-commit: prettier --check on staged frontend files, using Node 22 from nvm.
set -euo pipefail
# shellcheck disable=SC1091
source "$HOME/.nvm/nvm.sh" >/dev/null 2>&1 && nvm use 22 >/dev/null 2>&1 || true
files=()
for f in "$@"; do files+=("${f#frontend/}"); done
cd frontend && pnpm exec prettier --check "${files[@]}"

#!/usr/bin/env bash
# Pre-commit guard: data/ (except fixtures and splits.json) and files over 2 MB never enter git.
set -euo pipefail
status=0
for f in "$@"; do
  case "$f" in
    data/fixtures/* | data/splits.json) ;;
    data/*) echo "do not commit data files: $f"; status=1 ;;
  esac
  case "$f" in
    docs/proposal/*) continue ;;
  esac
  if [ -f "$f" ] && [ "$(stat -c%s "$f")" -gt 2097152 ]; then
    echo "file over 2 MB (weights and data stay out of git): $f"
    status=1
  fi
done
exit $status

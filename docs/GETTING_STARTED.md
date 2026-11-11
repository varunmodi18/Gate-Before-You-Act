# Getting the project

The setup and reproduction guide is now in the repository's [README.md](../README.md), so that
there is one set of instructions to keep correct:

| You want to | README section |
|---|---|
| Check the requirements (OS, Python, Node, disk, RAM, GPU) | [§3 Requirements](../README.md#3-requirements) |
| Look at the application without data or GPU (Fake model) | [§4 Quick start without a GPU](../README.md#4-quick-start-without-a-gpu-fixture-mode) |
| Build the data, index, case set and model, and run the experiments | [§5 Full reproduction](../README.md#5-full-reproduction) |
| Check your numbers against the reported ones | [§6 Checking a reproduction](../README.md#6-checking-a-reproduction) |
| Run lint and the test suites | [§7 Tests](../README.md#7-tests) |
| Run the live or the offline (replay) demo | [§8 Demo](../README.md#8-demo) |
| Fix a known problem | [§9 Troubleshooting](../README.md#9-troubleshooting) |

In short, for a first look without a GPU (use a folder path without spaces):

```bash
git clone https://github.com/varunmodi18/Gate-Before-You-Act.git
cd Gate-Before-You-Act
make setup
uv run python scripts/fetch_model.py Qwen/Qwen2.5-7B-Instruct-AWQ models/Qwen2.5-7B-Instruct-AWQ \
    --revision b25037543e9394b818fdfca67ab2a00ecc7dd641 --include tokenizer
make test          # pytest and vitest, about 2 minutes
make fixture-up    # http://127.0.0.1:8000; Ctrl-C to stop
```

Do not run `make freeze-cases`. Run `make splits` only as README §5.3 describes: it must
reproduce the committed `data/splits.json` exactly.

**Staying up to date:** `git pull`, then `make setup` (and `uv sync --extra rerank` if you use the
reranker), then `make db`.

**Committing (team):** never commit `data/` (except `data/fixtures/` and `data/splits.json`),
`models/`, `.venv-vllm` or `.env`. The team workflow is in [TEAM_GUIDE.md](TEAM_GUIDE.md).

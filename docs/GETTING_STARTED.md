# Getting the project (for teammates starting from nothing)

The code is on GitHub; the data, the retrieval index and the model are **not** (licences and
size): each copy rebuilds them from pinned sources with one command each. Do the steps in order.
Steps 1–4 are enough to run and test everything except the live model.

**What you need:** Linux (Ubuntu 22.04 or later; on Windows use WSL2 Ubuntu — not tested by
us), `git`, `make`, `curl`, about **5 GB** free disk for steps 1–5, plus **15 GB** more for the
model (step 6, which also needs an NVIDIA GPU with 8 GB).

## 1. Tools (once)

```bash
# uv (Python manager; installs Python 3.11 for the project by itself)
curl -LsSf https://astral.sh/uv/install.sh | sh

# nvm, Node 22 and pnpm
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash
source ~/.nvm/nvm.sh
nvm install 22
npm install -g pnpm@12.10.1
```

Open a new terminal afterwards so `uv` and `nvm` are on your PATH.

## 2. Get the code

Clone into a folder **without spaces** in its path (the model server's compiler breaks on spaces).

```bash
git clone https://github.com/varunmodi18/Gate-Before-You-Act.git
cd Gate-Before-You-Act
git switch m7-analysis          # the latest work; `main` holds only the base commit
```

## 3. Install the dependencies

```bash
make setup                      # Python packages (uv), frontend packages (pnpm), git hooks
make test                       # no GPU needed; about 2 minutes
```

`make test` should end with **855 passed** (pytest) and **19 passed** (vitest). If it does not,
send Varun the output of `make diag`.

## 4. Quick look (no data, no GPU)

```bash
make fixture-up                 # open http://127.0.0.1:8000 — the header says FAKE; Ctrl-C stops it
```

## 5. The real data and the retrieval index

```bash
make db data index              # OTRF logs at a pinned commit, normalised (~2.6 GB), plus the
                                # SigmaHQ + ATT&CK index (time depends on your connection)
make demo-setup                 # the demo scenario
make demo-up                    # http://127.0.0.1:8000 on the demo data
```

Do **not** run `make splits`: `data/splits.json` is the team-accepted split and is already in
the repository.

## 6. The model (optional; only the machine that runs experiments needs it)

Needs an NVIDIA GPU with 8 GB and CUDA 13.0 at `/usr/local/cuda-13.0`. Weights are about 6 GB, the
vLLM environment about 8 GB.

```bash
uv run python scripts/fetch_model.py Qwen/Qwen2.5-7B-Instruct-AWQ models/Qwen2.5-7B-Instruct-AWQ \
    --revision b25037543e9394b818fdfca67ab2a00ecc7dd641
```

vLLM goes in its own environment at `~/.local/share/gbya/venv-vllm` (no spaces), linked as
`.venv-vllm`, with the exact versions in `config/vllm-requirements.lock` (PyTorch and NVIDIA
packages from `download.pytorch.org/whl/cu130`, the rest from PyPI; see
[pilot_report.md](pilot_report.md), "Observations", item 4). Then `make model-up`. Ask Varun before
setting this up: every experiment must use the same model file.

## 7. How the team works (read before doing any annotation)

- **Annotate on the shared server, not on your own copy.** The team's labels live in one
  database on Varun's laptop; labels made in your local copy are not seen by anyone. Connect with
  the SSH tunnel in [TEAM_GUIDE.md](TEAM_GUIDE.md) §2 (send Varun your `~/.ssh/id_ed25519.pub`).
- Your roles and scenarios: [TEAM_GUIDE.md](TEAM_GUIDE.md) §3a. How to author and label:
  [case_authoring.md](case_authoring.md).
- To receive updates: `git pull` on `m7-analysis`, then `make setup` if dependencies changed.
- To contribute code or `cases/` files you need write access (ask Varun). Never push to `main`;
  never commit `data/` (except `data/fixtures/` and `data/splits.json`), `models/`, or `.env`.

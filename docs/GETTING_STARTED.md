# Getting the project

How to set up your own complete copy of GateBench, independent of anyone else's machine. Do the
steps in order. Steps 1–5 need no GPU; step 6 adds the live model.

You need Linux (Ubuntu 22.04 or later; WSL2 on Windows is untested), `git`, `make`, `curl`, and
about 5 GB of free disk (20 GB more for the model). The code is on GitHub; the log data, the
search index and the model are not, so the steps download them from their original sources at
pinned versions.

**1. Install the tools** (once), then open a new terminal:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash
source ~/.nvm/nvm.sh && nvm install 22 && npm install -g pnpm@12.10.1
```

**2. Get the code.** Use a folder path without spaces.

```bash
git clone https://github.com/varunmodi18/Gate-Before-You-Act.git
cd Gate-Before-You-Act
```

**3. Install and check.**

```bash
make setup
make test        # about 2 minutes; ends with "855 passed" and "19 passed"
```

**4. Have a look** (no data or GPU needed):

```bash
make fixture-up  # open http://127.0.0.1:8000; Ctrl-C to stop
```

**5. Get the real data** (about 2.6 GB downloaded and built):

```bash
make db data index
make demo-setup
make demo-up     # the demo on real logs: http://127.0.0.1:8000
```

Do not run `make splits`; the agreed split is already in the repository.

**6. The model** (needs an NVIDIA GPU with at least 8 GB).

a. Check that `nvidia-smi` shows "CUDA Version: 13.0" or later, then install CUDA Toolkit 13.0
from <https://developer.nvidia.com/cuda-downloads> so that `/usr/local/cuda-13.0` exists (this
step needs sudo).

b. Download the model at the pinned version (about 6 GB):

```bash
uv run python scripts/fetch_model.py Qwen/Qwen2.5-7B-Instruct-AWQ models/Qwen2.5-7B-Instruct-AWQ \
    --revision b25037543e9394b818fdfca67ab2a00ecc7dd641
```

c. Install vLLM in its own environment, outside the project folder (about 8 GB):

```bash
uv venv --python 3.11 ~/.local/share/gbya/venv-vllm
ln -s ~/.local/share/gbya/venv-vllm .venv-vllm
uv pip install --python .venv-vllm/bin/python -r config/vllm-requirements.lock \
    --extra-index-url https://download.pytorch.org/whl/cu130 --index-strategy unsafe-best-match
```

d. Run it:

```bash
make model-up    # terminal 1; wait until it reports it is ready on port 8001
make up          # terminal 2: the full application on your own database
```

Open http://127.0.0.1:8000; the model card on Home turns green. If the first start fails with a
KV-cache memory error, start it once more (see [pilot_report.md](pilot_report.md)).

To confirm you have exactly the same model file as everyone else, compare
`models/Qwen2.5-7B-Instruct-AWQ/MANIFEST.json` with another copy: the SHA-256 values must match.

**Staying up to date:** `git pull`, then `make setup` and `make db`.

**Committing:** work on `m7-analysis` (`git switch m7-analysis`), never on `main`; never commit
`data/` (except `data/fixtures/` and `data/splits.json`), `models/`, `.venv-vllm` or `.env`.
Pushing needs write access to the repository (ask Varun).

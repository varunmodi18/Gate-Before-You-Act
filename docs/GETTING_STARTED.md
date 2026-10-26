# Getting the project

For teammates starting from nothing. Do the steps in order; steps 1–4 are enough to run
everything except the live model.

You need Linux (Ubuntu 22.04 or later; WSL2 on Windows is untested), `git`, `make`, `curl` and
about 5 GB of free disk. The code is on GitHub; the log data and the search index are not, so
step 5 downloads them from their original sources.

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

If the tests fail, send Varun the output of `make diag`.

**4. Have a look** (no data or GPU needed):

```bash
make fixture-up  # open http://127.0.0.1:8000; Ctrl-C to stop
```

**5. Get the real data** (about 2.6 GB downloaded and built):

```bash
make db data index
make demo-setup
make demo-up     # open http://127.0.0.1:8000
```

Do not run `make splits`; the agreed split is already in the repository.

**6. Join the team's server.** Annotation happens only on Varun's laptop, not on your copy
(labels in your copy are seen by no one). Send Varun your GitHub username and your
`~/.ssh/id_ed25519.pub` (create it with `ssh-keygen -t ed25519` if you have none). Then:

```bash
ssh -N -L 8000:127.0.0.1:8000 varun-modi@<address from Varun>
```

and open http://127.0.0.1:8000. Your roles and scenarios: [TEAM_GUIDE.md](TEAM_GUIDE.md) §3a;
how to label: [case_authoring.md](case_authoring.md).

**Staying up to date:** `git pull`, then `make setup`.

**Committing:** work on `m7-analysis` (`git switch m7-analysis`), never on `main`; never commit
`data/` (except `data/fixtures/` and `data/splits.json`), `models/` or `.env`.

**The model** is needed only on the machine that runs experiments (Varun's): an NVIDIA GPU with
8 GB, about 15 GB more disk, and the same model file for every experiment. Do not set it up
without asking Varun; the details are in [pilot_report.md](pilot_report.md).

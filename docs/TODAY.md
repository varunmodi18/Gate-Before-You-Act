# Today — Varun's checklist (Wed 14 Oct 2026)

Do these in order. Details: `docs/TEAM_GUIDE.md` (§2 server, §3a roles), `docs/case_authoring.md`.

**1. SSH on the laptop**
```bash
systemctl is-active ssh                      # "active"? then skip the next line
sudo apt install openssh-server              # the one sudo step (you run it yourself)
hostname -I                                  # note the laptop's address for teammates
```
Off-network teammates only: `curl -fsSL https://tailscale.com/install.sh | sh` → `sudo tailscale up`
→ admin console → *Share* the laptop with each teammate.

**2. Add each teammate's key** (they send `~/.ssh/id_ed25519.pub`; no new OS accounts)
```bash
mkdir -p ~/.ssh && chmod 700 ~/.ssh
echo 'restrict,port-forwarding,permitopen="127.0.0.1:8000",command="echo tunnel only" <their key line>' >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys             # one line each: Aayush, Srushti, Dhruv
```

**3. Start the server** (laptop plugged in, no suspend; keep this terminal open)
```bash
git switch m7-analysis && git pull
make up                                      # migrates data/app.db, serves 127.0.0.1:8000
```

**4. Test connection** — each teammate runs
`ssh -N -L 8000:127.0.0.1:8000 varun-modi@<laptop address>` (Tailscale: the laptop's 100.x name)
and opens http://127.0.0.1:8000: the header says *Case set: draft*, the Home cards load.

**5. Calibration — all four together, dev-01 then dev-02**

| | Window | Author = annotator A | Annotator B | Adjudicator |
|---|---|---|---|---|
| dev-01 | `SDWIN-190625133822` Empire Reg Dump SAM Hive | Varun | Aayush | Srushti |
| dev-02 | `SDWIN-191027223020` Covenant ShellCmd InstallUtil | Aayush | Varun | Dhruv |

Per scenario, on screen together:
1. Author: *Scenarios → New scenario from a window* (ids `dev-01`, `dev-02`), fill everything
   (`docs/case_authoring.md` §3; your name in *Author*), *Save* → *Generate variants* →
   *Validate* (only label checks pending).
2. Author labels as **A** (Annotate, role A, your name) → *Submit*.
3. B labels blind (role B, own name) → *Submit*.
4. Adjudicator (role Adjudicator, own name) → resolve → *Adjudicate and write labels*.
5. Read the agreement panel together; note any rule questions for STATUS.md.

**6. End of day** — `git add cases/ && git commit -m "cases: 14 Oct" && git push` (never `main`).

**7. Demo cassette — when the GPU is free** (after the session; three terminals)
```bash
make model-up                                # 1: model server (wait for "ready")
make demo-setup && make demo-record          # 2: demo app, recording every model call
uv run python scripts/record_replay.py       # 3: drives the demo script; ends with "recorded into …"
```
Ctrl-C terminals 2 and 1, check with `make demo-replay` (header: REPLAY), then
`git add data/fixtures/demo/replay/demo.jsonl && git commit -m "T8.2: live demo cassette" && git push`.

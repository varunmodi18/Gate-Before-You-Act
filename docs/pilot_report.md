# T0.3 pilot report: early model-serving spike

Date: 8 October 2026. Machine: the team laptop (RTX 4060 Laptop 8 GB, driver 580.178.04 / CUDA 13.0,
i7-13620H, 16 GB RAM, Ubuntu 24.04, display on the iGPU). Prompts are **synthetic** (plan T0.3);
real-prompt token counts and the regenerated budget are T8.0's job.

Raw results: [`docs/pilot/vllm-awq.json`](pilot/vllm-awq.json). Script: [`scripts/pilot.py`](../scripts/pilot.py).

## Chosen profile: `vllm-awq` (in [`config/model_profiles.yaml`](../config/model_profiles.yaml))

| Item | Value |
|---|---|
| Backend | vLLM 0.31.0 (torch 2.13.0+cu130, FlashInfer 0.7.0.post1, xgrammar 0.2.7); exact pins in `config/vllm-requirements.lock` |
| Model | `Qwen/Qwen2.5-7B-Instruct-AWQ` at commit `b25037543e9394b818fdfca67ab2a00ecc7dd641`; SHA-256 of every file in `models/Qwen2.5-7B-Instruct-AWQ/MANIFEST.json` (weights `4ad6e70f…`, `920a8cc9…`) |
| Command | `vllm serve models/Qwen2.5-7B-Instruct-AWQ --host 127.0.0.1 --port 8001 --served-model-name qwen2.5-7b-instruct-awq --max-model-len 8192 --gpu-memory-utilization 0.90 --kv-cache-dtype auto --max-num-seqs 4 --generation-config vllm --structured-outputs-config '{"backend": "xgrammar", "disable_any_whitespace": true}'` |
| Environment | `CUDA_HOME=/usr/local/cuda-13.0`; venv `bin/` on PATH (`make model-up` does both) |
| Structured output | OpenAI-compatible `response_format: {"type": "json_schema", "json_schema": {...}}` works |
| KV cache | FP16 (`auto`): 1.15 GiB, **21,520 tokens**, 2.63× concurrency at 8,192 tokens/request |

All flag names were taken from `vllm serve --help=all` of the installed version.

### Differences from the plan's T0.3 settings

| Plan setting | Used | Why (evidence) | Status |
|---|---|---|---|
| FP8 KV cache | **FP16 KV (`auto`)** | With `--kv-cache-dtype fp8` the model's output was garbled at every prompt length tried (452, 879, 1,892, 2,853 and 4,418 tokens, with and without a JSON schema), e.g. `{"thought": "That485, ", … "technique_id": "c,  aki5i"}` and long runs of `c c c`. With FP16 KV and **no other change**, the same prompts gave coherent output (see the probe below). vLLM logs that FP8 KV "may cause accuracy drop without a proper scaling factor"; this version has no option to calculate scales | **Pending team confirmation.** Same model file, so no claim changes; it is a deviation from the proposal's §13 wording |
| (not specified) | `--generation-config vllm` | Otherwise vLLM silently applies the model's `generation_config.json` sampling defaults (top_p 0.8, top_k 20, repetition_penalty 1.05) to every request; with this flag only the parameters each request sends apply | Decision for the team to note |
| (not specified) | xgrammar with `disable_any_whitespace` | Without it the JSON grammar allows unlimited whitespace and the model produced only whitespace after `"thought"` until `max_tokens` (risk R7) | Needed for JSON-constrained output |

### FP8 vs FP16 KV quality probe (temperature 0, same prompts, same server flags otherwise)

| Prompt tokens | FP8 KV, first characters of the output | FP16 KV, first characters of the output |
|---|---|---|
| 452, no schema | `` ```  record 1      c   `` | `` ```json { "thought": "The suspicious activities include running commands with encoded parameters and executing LSASS… `` |
| 452, schema | `{"thought": " thought record work: 1", "tool": "sql_query", "args": {" c work": 1111, …` | `{"thought": "The suspicious activities include running commands with encoded parameters and executing LSASS…` |
| 1,892, no schema | `{"record work": 4337c "ts": "22 21 1 2 1 1 1 …` | `` ```json { "thought": "The host shows suspicious activity, particularly with commands like 'whoami -enc'… `` |
| 4,418, schema | `{"thought": "soc_responder',', 4902c ", "tool": "ask_analyst", …` | `{"thought": "The host is exhibiting suspicious behavior, particularly with the execution of obfuscated commands…` |
| 3,189, plain English | `…requiress careful attention to the relationships and commands between parent-child elements…` | `…requires careful attention to process creation, parent-child relationships, and command lines.` |

## Measurements

### 20 synthetic Exp 2-shaped episodes (4 concurrent; 4 sequential calls each)

Each call: ~4.4k-token prompt of log-like JSON lines, proposer-shaped JSON schema, `max_tokens` 400
(the proposer limit of §D.10.3), temperature 0.2. A unique nonce at the start of every prompt
prevents prefix-cache reuse (measured hit rate 0.5%), so these numbers are conservative for real
episodes, whose turns share a prefix.

| Metric | Measured |
|---|---|
| Calls / succeeded | 80 / 80 (no HTTP errors, no OOM, no preemption) |
| **Schema-valid JSON** | **78 / 80 calls.** The 2 invalid calls stopped at `max_tokens` (400) inside a repetition loop, so at most 2 of the 20 episodes contain an invalid call |
| Mean prompt / completion tokens per call | 4,435.9 / 200.0 |
| Wall time | 288.7 s |
| Aggregate input throughput | **1,229.2 tok/s** (prompt tokens ÷ wall time) |
| Aggregate output throughput | **55.4 tok/s** (completion tokens ÷ wall time) |
| Latency p50 / p95 per call | 14.0 s / 19.0 s |
| Time to first token, p50 | 2.48 s |
| Decode speed per request (median, under 4-way concurrency) | 16.9 tok/s |
| Peak VRAM | 7,249 MiB of 8,188 |
| Peak host RAM in use (whole system) | 8,201 MB of 15,606 MB |
| GPU temperature max / mean | 79 °C / 76 °C |
| SM clock min / median | 2,280 / 2,370 MHz |
| Throttle reasons seen | only `0x4` (software power cap) in all 578 samples; no thermal or hardware slowdown flags |
| Peak KV-cache use (vLLM log) | 86.9% |

### Comparison with the planning assumptions (proposal §13: ≥800 input tok/s, ≥80 output tok/s)

- Input: 1,229 tok/s measured vs 800 assumed — above the assumption.
- Output: 55.4 tok/s measured vs 80 assumed — **below the assumption, but above half of it (40)**.
- The budget formula treats input and output time as additive (`in/800 + out/80`). Applied to this
  workload it predicts **643.6 s**; the measured wall time was **288.7 s (0.45×)**, because prefill
  and decode overlap across the 4 concurrent requests. On this synthetic shape the planning
  assumptions are therefore conservative overall.
- **Fallback F1 is not triggered**: neither rate is below half of its assumption (plan T0.3).
- Output per call averaged 200 tokens, below the planned ~250. Real numbers come from T8.0.

### Long request (context limit)

One request with a **7,850-token prompt** and 194 output tokens (8,044 total, limit 8,192):
succeeded, schema-valid JSON, latency 8.4 s, no OOM. (An earlier request in the same run had a
7,745-token prompt and also succeeded.)

### 30-minute thermal soak (reported separately)

The same workload repeated in rounds of 4 concurrent episodes for 30.7 minutes (30 rounds).

| Metric | Measured |
|---|---|
| Calls / schema-valid | 480 / 459 (95.6%); every call returned HTTP 200 |
| Input tok/s per round: mean (min–max) | 1,161.3 (1,030.1–1,242.1) |
| Output tok/s per round: mean (min–max) | 56.0 (53.0–63.2) |
| GPU temperature max / mean | 80 °C / 78.2 °C |
| SM clock min / median | 2,280 / 2,370 MHz |
| Throttle reasons | only `0x4` (software power cap) in all 3,678 samples |
| Trend | no downward drift: first rounds ~1,140–1,205 input tok/s, last rounds ~1,109–1,195 |

The AC adapter was connected (checked right after the soak: adapter online, battery full). Before the server started, the only process on the GPU was Xorg (4 MiB).

## Verification against T0.3

| Check | Result |
|---|---|
| 20/20 episodes return schema-valid JSON | **Not fully met: 78/80 calls valid.** Both failures are output truncated at `max_tokens` by a repetition loop, not a structured-output failure (every completed output parsed and validated). In research runs such outputs get one re-ask and are then counted per §D.6/§D.10 |
| No OOM, including the 7,800-token request | Met (7,850-token prompt) |
| Numbers compared with the assumptions | Done above; F1 not triggered |

## Observations for later tasks

1. **Repetition loops (T5.1).** With a free-form `args` object and an unbounded `cited` array the
   model sometimes repeated the same IDs until `max_tokens`. The pilot schema bounds `cited` at 16
   (above the gate's 8-record limit, so over-citing stays expressible) and `thought` at 600
   characters. The real proposer schema should use typed per-tool `args` and bounded arrays.
2. **Cold-start KV size.** The first start after FlashInfer compiled new kernels left less memory
   for the KV cache (FP8: 0.33 GiB on the first start vs 1.11 GiB once cached; FP16: 0.36 GiB — not
   enough for one 8,192-token sequence, so the start failed — vs 1.15 GiB on the next start). If a
   first start fails with a KV-cache memory error, start it again. The compiled kernels are cached
   in `~/.cache/flashinfer` and `~/.cache/vllm`.
3. **Paths with spaces.** FlashInfer's JIT build does not quote include paths, and the repository
   path contains spaces. The vLLM venv therefore lives at `~/.local/share/gbya/venv-vllm`, with
   `.venv-vllm` as a symlink to it.
4. **Install route.** torch, torchvision, torchaudio, torchcodec, Triton, `cuda-toolkit` and 14
   NVIDIA CUDA wheels came from the official PyTorch index (`download.pytorch.org/whl/cu130`, whose
   NVIDIA entries are served from `pypi.nvidia.com`), at exactly the versions vLLM 0.31.0 resolves to;
   everything else from PyPI. A GPU check (CUDA matmul and a Triton JIT kernel) passed before the
   PyPI part was fetched. The final environment matches the resolution exactly (198 packages).

"""Fail-fast GPU check for the model-serving venv (T0.3).

Run with the vLLM venv's interpreter:  .venv-vllm/bin/python scripts/gpu_check.py
Checks that torch sees the GPU, runs a matmul on it, and compiles and runs a Triton kernel
(Triton JIT is what exposes a driver/toolchain CUDA version mismatch).
"""

from __future__ import annotations

import sys

import torch


def main() -> int:
    print(f"torch {torch.__version__}, built for CUDA {torch.version.cuda}")
    if not torch.cuda.is_available():
        print("FAIL: torch.cuda.is_available() is False")
        return 1
    dev = torch.cuda.get_device_name(0)
    cap = torch.cuda.get_device_capability(0)
    print(f"device: {dev}, compute capability {cap[0]}.{cap[1]}")

    a = torch.randn(2048, 2048, device="cuda", dtype=torch.float16)
    b = torch.randn(2048, 2048, device="cuda", dtype=torch.float16)
    c = a @ b
    torch.cuda.synchronize()
    ref = (a.float().cpu() @ b.float().cpu()).to(torch.float16)
    err = (c.cpu().float() - ref.float()).abs().max().item()
    print(f"fp16 matmul on GPU: max abs diff vs CPU = {err:.4f}")
    if not err < 1.0:
        print("FAIL: GPU matmul result differs from CPU")
        return 1

    try:
        import triton
        import triton.language as tl

        @triton.jit
        def add_kernel(x_ptr, y_ptr, out_ptr, n, BLOCK: tl.constexpr):  # type: ignore[no-untyped-def]
            pid = tl.program_id(0)
            offs = pid * BLOCK + tl.arange(0, BLOCK)
            mask = offs < n
            tl.store(
                out_ptr + offs,
                tl.load(x_ptr + offs, mask=mask) + tl.load(y_ptr + offs, mask=mask),
                mask=mask,
            )

        x = torch.randn(10_000, device="cuda")
        y = torch.randn(10_000, device="cuda")
        out = torch.empty_like(x)
        add_kernel[(triton.cdiv(10_000, 1024),)](x, y, out, 10_000, BLOCK=1024)
        torch.cuda.synchronize()
        ok = torch.allclose(out, x + y)
        print(f"triton {triton.__version__} JIT kernel: {'OK' if ok else 'WRONG RESULT'}")
        if not ok:
            return 1
    except Exception as exc:  # report the exact failure; this is the driver-risk probe
        print(f"FAIL: triton kernel: {type(exc).__name__}: {exc}")
        return 1

    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

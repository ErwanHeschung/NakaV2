"""Confirm the installed torch can actually run on this card.

torch.cuda.is_available() is not enough. A torch build without kernels for the
card's architecture installs fine and reports CUDA as available, then fails at
the first real operation — this project met exactly that with cu121 and cu124
wheels on a Blackwell (sm_120) card. So this forces one real matmul.

It used to assert the capability was exactly (12, 0), the developer's card,
which would have failed every other GPU it was run on. Any card the installed
torch has kernels for passes now; the matmul is the actual test.

    python eval/check_gpu.py          # human-readable
    python eval/check_gpu.py --json   # one line of JSON, for setup
"""

import json
import sys


def check() -> dict:
    try:
        import torch
    except Exception as e:
        return {"ok": False, "reason": f"torch did not import: {e}"}
    if not torch.cuda.is_available():
        return {"ok": False, "reason": "torch sees no CUDA device"}
    result = {
        "device": torch.cuda.get_device_name(0),
        "capability": list(torch.cuda.get_device_capability(0)),
        "torch": torch.__version__,
    }
    try:
        x = torch.randn(4096, 4096, device="cuda")
        y = x @ x
        torch.cuda.synchronize()
        result["matmul_mean"] = float(y.mean().item())
    except Exception as e:
        # "no kernel image is available for execution on the device" lands
        # here: the torch build does not cover this card's architecture.
        return {**result, "ok": False,
                "reason": f"CUDA is present but a real operation failed: {e}"}
    return {**result, "ok": True}


if __name__ == "__main__":
    outcome = check()
    if "--json" in sys.argv:
        print(json.dumps(outcome))
    else:
        for key, value in outcome.items():
            print(f"{key}: {value}")
    sys.exit(0 if outcome["ok"] else 1)

"""Phase 0 gate: confirm the Blackwell (sm_120) CUDA kernel is actually usable.

cu121/cu124 wheels install fine and report cuda.is_available()==True on this
card, but have no compiled kernel for sm_120 — the failure only shows up at
the first real op. This script forces one.
"""

import torch

assert torch.cuda.is_available(), "CUDA not available"

capability = torch.cuda.get_device_capability(0)
print(f"device: {torch.cuda.get_device_name(0)}")
print(f"capability: {capability}")
assert capability == (12, 0), f"expected sm_120 (12, 0), got {capability}"

x = torch.randn(4096, 4096, device="cuda")
y = x @ x
torch.cuda.synchronize()
print(f"matmul ok, result mean: {y.mean().item():.4f}")

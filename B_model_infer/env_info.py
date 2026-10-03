"""Execution environment of an inference run (appendix C-1 task 1), recorded in manifest.json.

    python -m B_model_infer.env_info [--device cuda:0]      prints the dict as JSON
collect_env(device) -> gpu_name, gpu_count, driver_version, cuda_version, torch_version, python_version, dtype, hostname,
pod_id (RUNPOD_POD_ID when set). On a CPU machine the GPU fields are None. torch is imported lazily.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import subprocess


def _nvidia_smi(field: str) -> str | None:
    if not shutil.which("nvidia-smi"):
        return None
    try:
        out = subprocess.check_output(["nvidia-smi", f"--query-gpu={field}", "--format=csv,noheader"], text=True, timeout=10)
        return out.strip().splitlines()[0].strip() or None
    except Exception:
        return None


def collect_env(device: str | None = None, dtype: str = "float32") -> dict:
    info = {"gpu_name": None, "gpu_count": 0, "driver_version": None, "cuda_version": None, "torch_version": None,
            "python_version": platform.python_version(), "dtype": dtype, "hostname": socket.gethostname(),
            "pod_id": os.environ.get("RUNPOD_POD_ID"), "device": device, "platform": platform.platform()}
    try:
        import torch  # noqa: WPS433
        info["torch_version"] = torch.__version__
        if torch.cuda.is_available():
            info["gpu_count"] = int(torch.cuda.device_count())
            idx = 0
            if device and ":" in str(device):
                try:
                    idx = int(str(device).split(":")[1])
                except ValueError:
                    idx = 0
            info["gpu_name"] = torch.cuda.get_device_name(idx)
            info["cuda_version"] = torch.version.cuda
    except Exception:
        pass
    info["driver_version"] = _nvidia_smi("driver_version")
    if info["gpu_name"] is None:
        info["gpu_name"] = _nvidia_smi("name")
    return info


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--device", default=None)
    a = p.parse_args(argv)
    print(json.dumps(collect_env(a.device), indent=2))


if __name__ == "__main__":
    main()

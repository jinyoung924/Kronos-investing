#!/usr/bin/env bash
# Environment setup on a RunPod pod (appendix C-2 step 2). Idempotent: safe to re-run after a pod restart.
# Called by RunPod/runpod.sh; can be run alone.
#
# The image does not matter: a Python 3.12 venv on /workspace gets requirements-infer.txt (D-8 pins, torch included),
# so the pod runs the versions the local CPU smoke test ran. The Kronos code is checked out at model.kronos_repo_commit
# and the model + tokenizer are downloaded at their pinned revisions into /workspace/hf.
set -euo pipefail
cd "$(dirname "$0")/.."

# /workspace is the network volume: the venv and the HF cache survive the pod.
if [[ -z "${HF_HOME:-}" && -d /workspace ]]; then export HF_HOME=/workspace/hf; fi
export HF_HOME="${HF_HOME:-$HOME/.cache/huggingface}"
mkdir -p "$HF_HOME"
grep -q 'HF_HOME=' ~/.bashrc 2>/dev/null || echo "export HF_HOME=$HF_HOME" >> ~/.bashrc

echo "== system =="
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv

echo "== venv (Python 3.12) =="
if [[ -z "${VENV_DIR:-}" ]]; then
  if [[ -d /workspace ]]; then VENV_DIR=/workspace/venv; else VENV_DIR="$PWD/.venv"; fi
fi
if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  if command -v python3.12 >/dev/null 2>&1 && python3.12 -m venv "$VENV_DIR"; then :; else
    # Image has no usable 3.12 -> let uv download a standalone CPython 3.12 (no apt).
    rm -rf "$VENV_DIR"
    if ! command -v uv >/dev/null 2>&1; then
      curl -LsSf https://astral.sh/uv/install.sh | sh
      export PATH="$HOME/.local/bin:$PATH"
    fi
    uv venv --python 3.12 --seed "$VENV_DIR"
  fi
fi
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
python --version | grep -q ' 3\.12\.' || { echo "FATAL: venv python is $(python --version), need 3.12" >&2; exit 2; }
echo "venv: $VENV_DIR ($(python --version))"

echo "== python deps (requirements-infer.txt) =="
python -m pip install -q --upgrade pip
# TORCH_INDEX_URL: set it when the default PyPI torch wheel does not match the host driver (RunPod/README.md §4).
python -m pip install -r requirements-infer.txt ${TORCH_INDEX_URL:+--extra-index-url "$TORCH_INDEX_URL"}

echo "== Kronos code =="
cfgval() { python -c 'import sys; from common.config import cfg_get, load_config; print(cfg_get(load_config("configs/base.yaml"), sys.argv[1]) or "")' "$1"; }
KRONOS_DIR="$(cfgval model.kronos_repo)"
KRONOS_COMMIT="$(cfgval model.kronos_repo_commit)"
[[ -n "$KRONOS_COMMIT" ]] || { echo "FATAL: configs model.kronos_repo_commit is null: pin the Kronos code commit, push-code, git pull" >&2; exit 2; }
[[ -d "$KRONOS_DIR/.git" ]] || git clone -q https://github.com/shiyu-coder/Kronos "$KRONOS_DIR"
git -C "$KRONOS_DIR" fetch -q --all && git -C "$KRONOS_DIR" checkout -q "$KRONOS_COMMIT"
echo "Kronos code at $(git -C "$KRONOS_DIR" rev-parse HEAD)"

echo "== model + tokenizer =="
python - <<'PY'
from huggingface_hub import snapshot_download
from common.config import load_config
m = load_config("configs/base.yaml")["model"]
for name, rev in ((m["name"], m.get("revision")), (m["tokenizer"], m.get("tokenizer_revision"))):
    if not rev:
        raise SystemExit(f"FATAL: revision for {name} is null: pin the HF commit sha in configs/base.yaml, push-code, git pull")
    print(name, "->", snapshot_download(name, revision=rev))
PY

echo "== verify =="
python - <<'PY'
import re, torch
pin = re.search(r"^torch==([^\s#]+)", open("requirements-infer.txt").read(), re.M).group(1)
have = torch.__version__.split("+")[0]
print(f"torch {torch.__version__} (cuda {torch.version.cuda}), pinned {pin}")
assert have == pin, f"torch {have} installed but requirements-infer.txt pins {pin}"
assert torch.cuda.is_available(), "CUDA not available inside torch: host driver too old for this wheel? (RunPod/README.md §4)"
PY
python -m B_model_infer.env_info --device cuda:0
echo "setup OK"

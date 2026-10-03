#!/usr/bin/env bash
# Pod setup (appendix C-1 task 5). Usage on the Pod:  bash setup_pod.sh /workspace/kronos_base_v1.tar.gz /workspace
# 1 unpack  2 verify the bundle  3 check the torch version against requirements-infer.txt  4 pip install
# 5 clone the Kronos code at model.kronos_repo_commit  6 pre-download model + tokenizer at their pinned revisions
# 7 print nvidia-smi and the collected environment. No project credentials are used.
set -euo pipefail
BUNDLE="${1:?bundle tar.gz}"
WORK="${2:-/workspace}"
mkdir -p "$WORK" && cd "$WORK"
echo "== 1 unpack $BUNDLE -> $WORK/repo"
tar xzf "$BUNDLE"
cd "$WORK/repo"
echo "== 2 verify bundle"
python -m B_model_infer.pod_bundle verify --dir "$WORK/repo"
echo "== 3 torch version check"
REQ_TORCH=$(grep -E '^torch==' requirements-infer.txt | cut -d= -f3)
HAVE_TORCH=$(python -c 'import torch, sys; print(torch.__version__.split("+")[0])' 2>/dev/null || echo none)
if [ "$HAVE_TORCH" != "$REQ_TORCH" ]; then
  echo "torch $HAVE_TORCH installed but requirements-infer.txt pins $REQ_TORCH: pick a Pod template with that torch build" >&2
  exit 1
fi
echo "== 4 pip install"
pip install -q -r requirements-infer.txt
echo "== 5 Kronos code"
REPO_DIR=$(python -c 'import yaml; print(yaml.safe_load(open("configs/base.yaml"))["model"]["kronos_repo"])')
REPO_COMMIT=$(python -c 'import yaml; v=yaml.safe_load(open("configs/base.yaml"))["model"]["kronos_repo_commit"]; print(v or "")')
if [ -z "$REPO_COMMIT" ]; then echo "configs model.kronos_repo_commit is null: pin the Kronos code commit first" >&2; exit 1; fi
if [ ! -d "$REPO_DIR/.git" ]; then git clone -q https://github.com/shiyu-coder/Kronos "$REPO_DIR"; fi
git -C "$REPO_DIR" fetch -q --all && git -C "$REPO_DIR" checkout -q "$REPO_COMMIT"
echo "Kronos code at $(git -C "$REPO_DIR" rev-parse HEAD)"
echo "== 6 model + tokenizer download"
python - <<'PY'
import yaml
from huggingface_hub import snapshot_download
m = yaml.safe_load(open("configs/base.yaml"))["model"]
for name, rev in ((m["name"], m.get("revision")), (m["tokenizer"], m.get("tokenizer_revision"))):
    if not rev:
        raise SystemExit(f"model revision for {name} is null: pin the HF commit sha in configs/base.yaml")
    print(name, "->", snapshot_download(name, revision=rev))
PY
echo "== 7 environment"
nvidia-smi || true
python -m B_model_infer.env_info --device cuda:0
echo "setup done. Run: tmux new -s infer; python -m B_model_infer.run_inference --backend kronos --run-id <run_id> --root $WORK/repo"

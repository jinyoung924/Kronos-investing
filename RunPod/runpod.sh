#!/usr/bin/env bash
# RunPod entry point (appendix C-2): setup -> inputs check -> GPU smoke -> RUN_CMD -> structure check -> checksums -> push metadata.
#
#   RUN_ID=probe_20240701 INFER_ARGS="--start 2024-07-01 --end 2024-07-01 --batch-size 256" bash RunPod/runpod.sh   # one-day probe
#   RUN_ID=kronos_base_v1 bash RunPod/runpod.sh                                # full base-profile run
#   RUN_ID=kronos_paper_v1 PROFILE=paper bash RunPod/runpod.sh                 # paper profile
#
# Deploy page env vars: GITHUB_TOKEN, PUBLIC_KEY (RunPod/README.md §1.2). Every knob is an env var (table in the README).
# The predictions stay on the /workspace network volume; only manifest.json, checksums.json, cloud_run.json and
# requirements.lock.txt go to the results/<RUN_ID> branch. The pod does NOT terminate itself: RunPod/local.sh fetch pulls
# the parquet files over SSH and verifies the checksums first, then RunPod/local.sh terminate ends the pod (principle 3).
set -euo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd -P)"
cd "$REPO_DIR"
T_START=$(date +%s)

# --- pod environment -----------------------------------------------------------------------------------
# An SSH session does not inherit the container env, so every value is looked up as:
# shell env -> PID 1 env (what the deploy page set) -> default.
pid1() { [[ -r /proc/1/environ ]] || return 0; tr '\0' '\n' < /proc/1/environ | sed -n "s/^$1=//p" | head -1; }
envor() { local v="${!1:-}"; [[ -n "$v" ]] || v="$(pid1 "$1")"; [[ -n "$v" ]] || v="${2:-}"; printf '%s' "$v"; }

export GITHUB_TOKEN="$(envor GITHUB_TOKEN)"                # required: clone of a private repo + metadata push
export PUBLIC_KEY="$(envor PUBLIC_KEY)"                    # ssh access (RunPod images install it; we make sure)
export RUNPOD_POD_ID="$(envor RUNPOD_POD_ID)"
export RUNPOD_PUBLIC_IP="$(envor RUNPOD_PUBLIC_IP)"        # set by RunPod when TCP 22 is exposed
export RUNPOD_TCP_PORT_22="$(envor RUNPOD_TCP_PORT_22)"

if [[ -n "$PUBLIC_KEY" ]]; then   # idempotent: only add the key if the image has not done it already
  mkdir -p ~/.ssh && chmod 700 ~/.ssh && touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys
  grep -qxF "$PUBLIC_KEY" ~/.ssh/authorized_keys || echo "$PUBLIC_KEY" >> ~/.ssh/authorized_keys
fi
if [[ -z "$GITHUB_TOKEN" && "$(envor ALLOW_NO_PUSH 0)" != "1" ]]; then
  echo "FATAL: GITHUB_TOKEN is not set (deploy page -> Environment Variables). Without it the local side cannot find" >&2
  echo "       this run (results/<RUN_ID> branch). Set it, or run with ALLOW_NO_PUSH=1 on purpose." >&2
  exit 2
fi
[[ -n "$RUNPOD_PUBLIC_IP" && -n "$RUNPOD_TCP_PORT_22" ]] || echo "WARNING: no public IP / TCP 22 on this pod -> local.sh fetch needs the host and port by hand"

# --- knobs -------------------------------------------------------------------------------------------
export RUN_ID="$(envor RUN_ID)"
[[ -n "$RUN_ID" ]] || { echo "FATAL: RUN_ID is required (probe_<date>, kronos_base_v1, ...). A run_id is never overwritten: a new vintage gets a new id." >&2; exit 2; }
PROFILE="$(envor PROFILE "")"                         # empty = infer.default_profile
INFER_ARGS="$(envor INFER_ARGS "")"                   # extra run_inference flags, e.g. "--batch-size 256 --start 2024-07-01 --end 2024-07-01"
RUN_CMD="$(envor RUN_CMD "python -m B_model_infer.run_inference --backend kronos --run-id $RUN_ID ${PROFILE:+--profile $PROFILE} $INFER_ARGS")"
SKIP_SMOKE="$(envor SKIP_SMOKE 0)"
INPUTS_DIR="$(envor INPUTS_DIR /workspace/inputs)"    # where RunPod/local.sh upload puts the data files
export RESULTS_BRANCH="results/$RUN_ID"
export HF_HOME="$(envor HF_HOME /workspace/hf)"
export VENV_DIR="$(envor VENV_DIR "")"
export TORCH_INDEX_URL="$(envor TORCH_INDEX_URL "")"

mkdir -p logs
LOG="logs/runpod_$RUN_ID.log"
exec > >(tee -a "$LOG") 2>&1                           # logs/ is gitignored: the console stays on the pod (fetch copies it)

# --- wrap-up runs on ANY exit: record the status, push the metadata. Never terminates the pod. --------
STATUS=running
OUT=""
finish() {
  rc=$?
  [[ "$STATUS" == running ]] && STATUS=failed
  trap - EXIT
  echo "== wrap-up: status=$STATUS rc=$rc =="
  if [[ -n "$OUT" && -d "$OUT" ]]; then
    STATUS="$STATUS" RC="$rc" T_START="$T_START" python3 - "$OUT/cloud_run.json" <<'PY' || true
import datetime, json, os, sys, time
p = sys.argv[1]
try:
    d = json.load(open(p))
except Exception:
    d = {}
d.update(status=os.environ["STATUS"], exit_code=int(os.environ["RC"]),
         finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
         elapsed_min=round((time.time() - int(os.environ["T_START"])) / 60, 1))
json.dump(d, open(p, "w"), indent=2, ensure_ascii=False)
PY
    cp -f "$LOG" "$OUT/console.log" 2>/dev/null || true
    if bash RunPod/push_meta.sh "$OUT"; then :; else
      echo "metadata is NOT on GitHub. Fix and run: bash RunPod/push_meta.sh $OUT"
    fi
  else
    echo "stopped before the run directory existed -> nothing to push"
  fi
  if [[ "$STATUS" == ok ]]; then
    echo "next (local): bash RunPod/local.sh fetch $RUN_ID   -> checksum verify -> bash RunPod/local.sh merge $RUN_ID -> terminate $RUN_ID"
  else
    echo "the pod stays up: look at $LOG, fix on local main (push-code), then re-run the same command to resume"
  fi
}
trap finish EXIT

# --- git: put this run on its own results/<RUN_ID> branch --------------------------------------------
# Code commits stay on main; every commit this pod makes lands on results/<RUN_ID> and only adds the metadata files
# of data/B_predictions/<RUN_ID>/. Same RUN_ID again = resume (same branch, same code). A new RUN_ID starts from the
# latest origin/main, so "push-code locally, then start the run" is all it takes to run new code.
if [[ -n "$(git status --porcelain --untracked-files=no -- . ':!data')" ]]; then
  echo "FATAL: tracked files are modified in the working tree. Fix on local main and push-code; here: git checkout -- ." >&2
  git status --short --untracked-files=no -- . ':!data'; exit 2
fi
REPO_SLUG="${REPO_SLUG:-$(git remote get-url origin | sed -E 's#.*github\.com[:/]##; s#\.git$##')}"
FETCH_URL="${PUSH_URL:-https://github.com/${REPO_SLUG}.git}"
[[ -z "${PUSH_URL:-}" && -n "$GITHUB_TOKEN" ]] && FETCH_URL="https://x-access-token:${GITHUB_TOKEN}@github.com/${REPO_SLUG}.git"
if [[ "$(git rev-parse --abbrev-ref HEAD)" == "$RESULTS_BRANCH" ]]; then
  echo "resuming on the local branch $RESULTS_BRANCH"
elif git fetch -q "$FETCH_URL" "$RESULTS_BRANCH" 2>/dev/null; then
  echo "resuming the remote branch $RESULTS_BRANCH"
  git checkout -q -B "$RESULTS_BRANCH" FETCH_HEAD
elif git fetch -q "$FETCH_URL" main; then
  git checkout -q -B "$RESULTS_BRANCH" FETCH_HEAD
else
  echo "WARNING: could not fetch origin/main -> branching from the current HEAD"
  git checkout -q -B "$RESULTS_BRANCH"
fi
export CODE_COMMIT="$(git log -1 --format=%H -- . ':!data')"    # last commit that touched code (results commits only touch data/)
echo "branch: $RESULTS_BRANCH (code $CODE_COMMIT)"

# Can this token actually push? Checked now, not after hours of GPU time.
if [[ -n "$GITHUB_TOKEN" || -n "${PUSH_URL:-}" ]]; then
  PROBE="$(mktemp)"
  if ! git push -q --dry-run "$FETCH_URL" "HEAD:refs/heads/$RESULTS_BRANCH" 2>"$PROBE"; then
    sed "s#${GITHUB_TOKEN:-__none__}#TOKEN#g" "$PROBE" >&2
    echo "FATAL: GITHUB_TOKEN cannot push to $REPO_SLUG. Fine-grained PAT needs: Repository access -> this repo," >&2
    echo "       Permissions -> Contents: Read and write. A running pod keeps its old env: pass the new token on the" >&2
    echo "       command line, GITHUB_TOKEN=... RUN_ID=$RUN_ID bash RunPod/runpod.sh" >&2
    exit 2
  fi
  rm -f "$PROBE"; echo "push preflight OK ($REPO_SLUG:$RESULTS_BRANCH)"
fi

# --- environment ------------------------------------------------------------------------------------
bash RunPod/setup_runpod.sh
for v in "${VENV_DIR:-}" /workspace/venv "$PWD/.venv"; do
  if [[ -n "$v" && -f "$v/bin/activate" ]]; then source "$v/bin/activate"; break; fi
done
pred_dir() { python -c 'import sys; from common.config import load_config; from common.paths import Paths; print(Paths(load_config("configs/base.yaml"), ".").predictions_dir(sys.argv[1]))' "$1"; }
OUT="$(pred_dir "$RUN_ID")"
mkdir -p "$OUT"
python -m pip freeze > "$OUT/requirements.lock.txt"

OUT="$OUT" PROFILE="$PROFILE" RUN_CMD="$RUN_CMD" REPO_DIR="$REPO_DIR" python - <<'PY'
import datetime, json, os, subprocess
e = os.environ
gpu = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
                     capture_output=True, text=True).stdout.strip()
p = os.path.join(e["OUT"], "cloud_run.json")
try:
    d = json.load(open(p))
except Exception:
    d = {}
d.setdefault("sessions", []).append({"pod_id": e["RUNPOD_POD_ID"], "gpu": gpu, "code_commit": e["CODE_COMMIT"],
                                     "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")})
d.update(run_id=e["RUN_ID"], results_branch=e["RESULTS_BRANCH"], code_commit=e["CODE_COMMIT"], profile=e["PROFILE"] or None,
         run_cmd=e["RUN_CMD"], pod_id=e["RUNPOD_POD_ID"], gpu=gpu, ssh_host=e["RUNPOD_PUBLIC_IP"], ssh_port=e["RUNPOD_TCP_PORT_22"],
         repo_dir=e["REPO_DIR"], pred_dir=e["OUT"], status="running")
json.dump(d, open(p, "w"), indent=2, ensure_ascii=False)
PY
bash RunPod/push_meta.sh "$OUT" || true              # early push: the local side can already find the pod (ssh_host, ssh_port)

# --- inputs: uploaded by RunPod/local.sh upload, checked against the committed RunPod/inputs.sha256.json ----------
if [[ -d "$INPUTS_DIR" ]]; then cp -a "$INPUTS_DIR"/. "$REPO_DIR"/; fi
if ! python -m B_model_infer.pod_inputs verify; then
  echo "FATAL: input data is missing or differs from RunPod/inputs.sha256.json. On the local machine run:" >&2
  echo "       bash RunPod/local.sh upload ${RUNPOD_PUBLIC_IP:-<pod ip>} ${RUNPOD_TCP_PORT_22:-<ssh port>}" >&2
  exit 2
fi

# --- GPU smoke test: first rebalance date, 5 tickers, 2 samples. Catches env/model/data problems in a minute ------
if [[ "$SKIP_SMOKE" != "1" ]]; then
  echo "== smoke test =="
  SMOKE_START="$(python -c 'from common.config import cfg_get, load_config; print(cfg_get(load_config("configs/base.yaml"), "period.start"))')"
  rm -rf "$(pred_dir smoke_gpu)"
  python -m B_model_infer.run_inference --backend kronos --run-id smoke_gpu ${PROFILE:+--profile $PROFILE} \
    --start "$SMOKE_START" --end "$SMOKE_START" --max-tickers 5 --sample-count 2 --batch-size 10
  python -m B_model_infer.verify_run --run-id smoke_gpu
  echo "smoke OK"
fi

# --- main job ---------------------------------------------------------------------------------------
echo "== RUN_CMD: $RUN_CMD =="
eval "$RUN_CMD"
python -m B_model_infer.verify_run --run-id "$RUN_ID"
python -m B_model_infer.checksum write --run-id "$RUN_ID"
STATUS=ok
echo "== done in $(( ($(date +%s) - T_START) / 60 )) min =="

#!/usr/bin/env bash
# Local-side helper for the RunPod loop (RunPod/README.md §0). Each subcommand is one safe, repeatable step:
#
#   bash RunPod/local.sh push-code "message"      # ① commit code/docs on main and push (pull --rebase first)
#   bash RunPod/local.sh upload <host> <port>     # ② send the input data files to the pod (/workspace/inputs), sha256-checked first
#   bash RunPod/local.sh list                     #    result branches on origin, with their status
#   bash RunPod/local.sh fetch <RUN_ID> [host port]   # ④ rsync data/B_predictions/<RUN_ID>/ from the pod, then checksum verify
#   bash RunPod/local.sh same  <RUN_ID> <RUN_ID>  #    do two fetched runs have bit-identical files? (probe: determinism)
#   bash RunPod/local.sh merge <RUN_ID>           # ⑤ keep: merge results/<RUN_ID> (metadata only) into main, push, delete the remote branch
#   bash RunPod/local.sh drop  <RUN_ID>           # ⑤ discard: delete the remote branch (local files stay)
#   bash RunPod/local.sh terminate <RUN_ID>       # ⑥ terminate the pod of that run; refused until the local checksum verify passes
#   bash RunPod/local.sh ssh <RUN_ID>             #    open a shell on the pod of that run
#   bash RunPod/local.sh status                   #    where am I, what is uncommitted, what is unmerged
#
# Optional RunPod/.env (gitignored): RUNPOD_USER_API_KEY=... (terminate), RUNPOD_SSH_KEY=~/.ssh/id_ed25519_runpod, PYTHON=python
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -f RunPod/.env ]]; then set -a; source RunPod/.env; set +a; fi
cmd="${1:-status}"; arg="${2:-}"
PY="${PYTHON:-python}"
REMOTE_INPUTS="${REMOTE_INPUTS:-/workspace/inputs}"
die() { echo "local.sh: $*" >&2; exit 1; }
need_run() { [[ -n "$arg" ]] || die "usage: $0 $cmd <RUN_ID>"; }
on_main() { [[ "$(git rev-parse --abbrev-ref HEAD)" == main ]] || die "switch to main first: git checkout main"; }
clean_tracked() {
  [[ -z "$(git status --porcelain --untracked-files=no)" ]] || die "uncommitted changes to tracked files. Run: bash RunPod/local.sh push-code \"msg\"  (or git stash)"
}
pred_rel() { "$PY" -c 'import sys; from common.config import load_config; from common.paths import Paths; p = Paths(load_config("configs/base.yaml"), "."); print(p.predictions_dir(sys.argv[1]).relative_to(p.root).as_posix())' "$1"; }
ssh_opts() {   # pods are short-lived and reuse IPs/ports, so host keys are not remembered
  local key="${RUNPOD_SSH_KEY:-}"
  printf '%s' "-p $1 ${key:+-i ${key/#\~/$HOME}} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"
}
ensure_rsync() {   # $1 host, $2 port: RunPod images do not always ship rsync
  # shellcheck disable=SC2046
  ssh $(ssh_opts "$2") "root@$1" 'command -v rsync >/dev/null || { apt-get update -qq && apt-get install -y -qq rsync; }'
}
# cloud_run.json of a run, from origin/results/<RUN_ID> (the pod pushes it at start and on exit)
cloud_run() { git fetch -q origin "results/$1" 2>/dev/null && git show "FETCH_HEAD:$(pred_rel "$1")/cloud_run.json" 2>/dev/null; }
field() { python3 -c 'import json, sys; print(json.load(sys.stdin).get(sys.argv[1]) or "")' "$1"; }

case "$cmd" in
  push-code)
    on_main
    git fetch -q origin
    git add -A -- .                                # stage outputs and datasets are gitignored; run metadata arrives via merge
    if git diff --cached --quiet; then echo "nothing to commit"; else
      git commit -q -m "${arg:-update}" && echo "committed: ${arg:-update}"
    fi
    git pull -q --rebase origin main || die "rebase conflict with origin/main. Resolve, then: git rebase --continue && git push origin main"
    git push -q origin main && echo "pushed main -> origin ($(git rev-parse --short HEAD))"
    ;;

  upload)
    host="${2:-}"; port="${3:-}"
    [[ -n "$host" && -n "$port" ]] || die "usage: $0 upload <host> <port>   (pod page -> Connect -> SSH over exposed TCP)"
    "$PY" -m B_model_infer.pod_inputs verify || die "local data differs from RunPod/inputs.sha256.json. Rewrite it: $PY -m B_model_infer.pod_inputs write, then push-code"
    git ls-files --error-unmatch RunPod/inputs.sha256.json >/dev/null 2>&1 && git diff --quiet HEAD -- RunPod/inputs.sha256.json || die "RunPod/inputs.sha256.json is not committed: push-code first (the pod checks against the committed list)"
    so="$(ssh_opts "$port")"
    # shellcheck disable=SC2086
    ssh $so "root@$host" "mkdir -p '$REMOTE_INPUTS'"
    ensure_rsync "$host" "$port"
    "$PY" -m B_model_infer.pod_inputs list | rsync -av --partial --files-from=- -e "ssh $so" ./ "root@$host:$REMOTE_INPUTS/"
    echo "uploaded to $host:$REMOTE_INPUTS (runpod.sh copies it into the repo and verifies the sha256 list)"
    ;;

  list)
    git fetch -q --prune origin
    git for-each-ref --format='%(refname:short)' refs/remotes/origin/results/ | while read -r ref; do
      name="${ref#origin/results/}"
      merged=$(git merge-base --is-ancestor "$ref" origin/main 2>/dev/null && echo merged || echo unmerged)
      st=$(git show "$ref:$(pred_rel "$name")/cloud_run.json" 2>/dev/null | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("status","?"), d.get("gpu",""), d.get("elapsed_min",""),"min", "pod", d.get("pod_id",""))' 2>/dev/null || echo "no cloud_run.json")
      printf '%-32s %-9s %s\n' "$name" "$merged" "$st"
    done
    ;;

  fetch)
    need_run
    host="${3:-}"; port="${4:-}"; rel="$(pred_rel "$arg")"; remote=""
    if cr="$(cloud_run "$arg")" && [[ -n "$cr" ]]; then
      [[ -n "$host" ]] || host="$(field ssh_host <<<"$cr")"
      [[ -n "$port" ]] || port="$(field ssh_port <<<"$cr")"
      remote="$(field pred_dir <<<"$cr")"
      st="$(field status <<<"$cr")"
      [[ "$st" == ok ]] || echo "note: the run's status is '$st' (not finished or failed): this copies what exists so far"
    fi
    [[ -n "$host" && -n "$port" ]] || die "no ssh endpoint for $arg. Pass it: $0 fetch $arg <host> <port>"
    remote="${remote:-${REMOTE_REPO:-/workspace/Kronos-investing}/$rel}"
    mkdir -p "$rel"
    ensure_rsync "$host" "$port"
    rsync -av --partial -e "ssh $(ssh_opts "$port")" "root@$host:$remote/" "$rel/"
    "$PY" -m B_model_infer.checksum verify --run-id "$arg" \
      || die "checksum verify FAILED for $arg (run again to re-copy; do not terminate the pod)"
    echo "$rel/ is complete and verified. Next: bash RunPod/local.sh merge $arg, then terminate $arg"
    ;;

  same)
    need_run; other="${3:-}"; [[ -n "$other" ]] || die "usage: $0 same <RUN_ID> <RUN_ID>"
    python3 - "$(pred_rel "$arg")/checksums.json" "$(pred_rel "$other")/checksums.json" <<'PY'
import json, sys
a, b = (json.load(open(p))["files"] for p in sys.argv[1:3])
diff = sorted(n for n in set(a) | set(b) if a.get(n, {}).get("sha256") != b.get(n, {}).get("sha256"))
print(f"{len(a)} vs {len(b)} files, {len(diff)} differ" + (f": {diff[:5]}" if diff else " -> bit-identical"))
sys.exit(1 if diff else 0)
PY
    ;;

  merge)
    need_run; on_main; clean_tracked
    "$PY" -m B_model_infer.checksum verify --run-id "$arg" || die "fetch and verify the predictions first: $0 fetch $arg"
    git pull -q --rebase origin main || die "rebase conflict with origin/main. Resolve, then run merge again"
    git fetch -q origin "results/$arg" || die "origin has no branch results/$arg"
    git merge -q --no-ff FETCH_HEAD -m "merge results/$arg" \
      || die "merge failed (unexpected: results branches only add $(pred_rel "$arg")/ metadata). git merge --abort to undo"
    git push -q origin main && echo "merged results/$arg into main and pushed"
    git push -q origin --delete "results/$arg" && echo "deleted origin/results/$arg"
    ;;

  drop)
    need_run
    git push -q origin --delete "results/$arg" 2>/dev/null && echo "deleted origin/results/$arg" || echo "origin/results/$arg already gone"
    echo "local files under $(pred_rel "$arg")/ are left as they are"
    ;;

  terminate)
    need_run
    "$PY" -m B_model_infer.checksum verify --run-id "$arg" || die "refusing: the local copy of $arg is not verified. Run: $0 fetch $arg"
    pod="${3:-}"
    if [[ -z "$pod" ]]; then
      if cr="$(cloud_run "$arg")"; then pod="$(field pod_id <<<"$cr")"; fi
      [[ -n "$pod" ]] || pod="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("pod_id") or "")' "$(pred_rel "$arg")/cloud_run.json" 2>/dev/null || true)"
    fi
    [[ -n "$pod" ]] || die "pod id unknown. Pass it: $0 terminate $arg <pod id>"
    [[ -n "${RUNPOD_USER_API_KEY:-}" ]] || die "RUNPOD_USER_API_KEY is not set (export it or put it in RunPod/.env), or terminate pod $pod on the web"
    if [[ "${YES:-0}" != "1" ]]; then
      read -r -p "terminate pod $pod? The network volume is kept. [y/N] " ans; [[ "$ans" == y || "$ans" == Y ]] || die "cancelled"
    fi
    resp="$(curl -s -X POST "https://api.runpod.io/graphql?api_key=$RUNPOD_USER_API_KEY" -H 'Content-Type: application/json' \
      -d "{\"query\":\"mutation { podTerminate(input: {podId: \\\"$pod\\\"}) }\"}")"
    echo "$resp"
    [[ "$resp" != *'"errors"'* ]] || die "the API reported an error: check the pod on the web"
    echo "pod $pod terminated. Delete the network volume on the web once a second local backup exists."
    ;;

  ssh)
    need_run
    cr="$(cloud_run "$arg")" || die "origin has no branch results/$arg"
    # shellcheck disable=SC2046
    exec ssh $(ssh_opts "$(field ssh_port <<<"$cr")") "root@$(field ssh_host <<<"$cr")"
    ;;

  status)
    echo "branch : $(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)"
    git fetch -q origin 2>/dev/null || true
    echo "vs main: $(git rev-list --count origin/main..HEAD 2>/dev/null || echo ?) ahead, $(git rev-list --count HEAD..origin/main 2>/dev/null || echo ?) behind"
    echo "uncommitted tracked changes:"; git status --short --untracked-files=no | sed 's/^/    /'
    echo "result branches:"; "$0" list | sed 's/^/    /'
    ;;
  *) die "unknown subcommand '$cmd'. See header of $0" ;;
esac

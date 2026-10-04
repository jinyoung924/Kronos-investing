"""Appendix C-1: pod inputs, run structure check, prediction checksums, results-branch flow, environment info, per-profile sample selection."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from B_model_infer import checksum, pod_inputs, verify_run
from B_model_infer.env_info import collect_env
from C_signal.aggregate import select_samples
from common.config import cfg_override, load_config
from common.paths import Paths
from common.schema import predictions_from_array
from common.synthetic import write_synthetic_dataset

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def repo(cfg, tmp_path):
    """Synthetic data/raw + data/universe (base and liq5 variants) under a temp root, plus data that must never be an input."""
    root = tmp_path / "repo"
    root.mkdir()
    c = cfg_override(cfg, {"universe.indices": ["kospi200"], "universe.markets": {"kospi200": "KOSPI"}, "data.index_tickers": {}})
    write_synthetic_dataset(c, root, n_per_index=6, seed=2)
    paths = Paths(c, root)
    base = paths.constituents_file("kospi200", "base")
    pd.read_parquet(base).to_parquet(paths.constituents_file("kospi200", "liq5"), index=False)
    (root / "data/krx_raw/2026-09-28/stk").mkdir(parents=True)
    (root / "data/krx_raw/2026-09-28/stk/20240102.json").write_text("[]")
    return c, root, paths


def test_pod_inputs_list_only_allowed_files_and_verify_detects_tampering(repo, tmp_path):
    c, root, paths = repo
    rels = pod_inputs.input_files(c, paths)
    assert rels == ["data/raw/kospi200/prices.parquet", "data/universe/constituents_kospi200_liq5.parquet"]   # only the profiles' variant, no krx_raw
    assert pod_inputs.verify(c, root, log=lambda *_: None) == 1                    # no list yet
    out = pod_inputs.write(c, root, log=lambda *_: None)
    assert out == paths.pod_inputs_file() == root / "RunPod/inputs.sha256.json"
    doc = json.loads(out.read_text())
    assert sorted(doc["files"]) == rels and doc["n_files"] == 2
    assert pod_inputs.verify(c, root, log=lambda *_: None) == 0
    # the pod checks an upload directory against the committed list: missing -> fail, copied -> ok, one flipped byte -> fail
    src = tmp_path / "inputs"
    assert pod_inputs.verify(c, root, src=src, log=lambda *_: None) == 1
    for rel in rels:
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / rel, src / rel)
    assert pod_inputs.verify(c, root, src=src, log=lambda *_: None) == 0
    f = src / rels[0]
    b = bytearray(f.read_bytes()); b[10] ^= 1; f.write_bytes(bytes(b))
    assert pod_inputs.verify(c, root, src=src, log=lambda *_: None) == 1
    # a profile that asks for another variant makes the committed list stale
    c2 = cfg_override(c, {"infer.profiles.paper.universe_variant": "base"})
    assert pod_inputs.verify(c2, root, log=lambda *_: None) == 1


def _write_run(paths, run_id, dates, horizon=3):
    run_dir = paths.predictions_dir(run_id)
    run_dir.mkdir(parents=True)
    rng = np.random.default_rng(0)
    for d in dates:
        df = predictions_from_array(d, ["a", "b"], rng.lognormal(0, 0.01, (2, 2, horizon, 5)) * 100)
        df.to_parquet(paths.prediction_file(run_id, d), index=False)


def test_verify_run_checks_count_and_schema(cfg, tmp_path):
    paths = Paths(cfg, tmp_path)
    _write_run(paths, "r1", ("2024-07-01", "2024-07-08"))
    manifest = {"n_rebalance_dates": 3, "pred_len": 3,
                "skipped_by_date": {"2024-07-01": {"n_pred": 2, "skip_reasons": {"nan_in_window": 1}}, "2024-07-08": {"n_pred": 2, "skip_reasons": {}},
                                    "2024-07-15": {"n_pred": 0, "skipped": {}}}}
    paths.manifest_file("r1").write_text(json.dumps(manifest))
    assert verify_run.verify(paths, "r1", log=lambda *_: None) == 0               # the empty date has no file
    paths.manifest_file("r1").write_text(json.dumps({**manifest, "pred_len": 5}))
    assert verify_run.verify(paths, "r1", log=lambda *_: None) == 1               # horizon mismatch
    paths.manifest_file("r1").write_text(json.dumps(manifest))
    paths.prediction_file("r1", "2024-07-08").unlink()
    assert verify_run.verify(paths, "r1", log=lambda *_: None) == 1               # a date is missing
    assert verify_run.verify(paths, "nope", log=lambda *_: None) == 1


def _git(cwd, *args):
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def test_results_branch_carries_metadata_only_and_merges_into_main(cfg, tmp_path):
    """RunPod/push_meta.sh (pod) -> RunPod/local.sh merge (local) against a bare remote: parquet never reaches git."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    seed = tmp_path / "seed"
    seed.mkdir()
    for d in ("common", "B_model_infer", "configs", "RunPod"):
        shutil.copytree(ROOT / d, seed / d, ignore=shutil.ignore_patterns("__pycache__", ".env"))
    (seed / ".gitignore").write_text("data/B_predictions/\n__pycache__/\n")
    _git(seed, "init", "-q", "-b", "main"); _git(seed, "add", "-A"); _git(seed, "commit", "-q", "-m", "code")
    _git(seed, "remote", "add", "origin", str(remote)); _git(seed, "push", "-q", "origin", "main")
    pod, local = tmp_path / "pod", tmp_path / "local"
    for clone in (pod, local):
        subprocess.run(["git", "clone", "-q", str(remote), str(clone)], check=True)
        _git(clone, "config", "user.email", "t@t"); _git(clone, "config", "user.name", "t")
    env = {**os.environ, "PUSH_URL": str(remote), "PUSH_RETRY_SLEEP": "0", "PYTHON": sys.executable, "GITHUB_TOKEN": ""}

    c = load_config(pod / "configs/base.yaml")
    ppaths = Paths(c, pod)
    _write_run(ppaths, "r1", ("2024-07-01", "2024-07-08"))
    ppaths.manifest_file("r1").write_text("{}")
    (ppaths.predictions_dir("r1") / "cloud_run.json").write_text(json.dumps({"status": "ok", "pod_id": "p1"}))
    checksum.write(ppaths, "r1", pod, log=lambda *_: None)
    run_dir = str(ppaths.predictions_dir("r1"))
    assert subprocess.run(["bash", "RunPod/push_meta.sh", run_dir], cwd=pod, env=env).returncode == 1      # on main: refused
    _git(pod, "checkout", "-q", "-B", "results/r1")
    assert subprocess.run(["bash", "RunPod/push_meta.sh", run_dir], cwd=pod, env=env).returncode == 0
    pushed = _git(remote, "ls-tree", "-r", "--name-only", "results/r1", "data/").split()
    assert sorted(Path(p).name for p in pushed) == ["checksums.json", "cloud_run.json", "manifest.json"]

    # local: merge is refused until the fetched copy verifies; the rsync step is replaced by a plain copy here
    lpaths = Paths(c, local)
    assert subprocess.run(["bash", "RunPod/local.sh", "merge", "r1"], cwd=local, env=env, capture_output=True).returncode == 1
    shutil.copytree(ppaths.predictions_dir("r1"), lpaths.predictions_dir("r1"))
    r = subprocess.run(["bash", "RunPod/local.sh", "merge", "r1"], cwd=local, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    tracked = _git(remote, "ls-tree", "-r", "--name-only", "main", "data/").split()
    assert sorted(Path(p).name for p in tracked) == ["checksums.json", "cloud_run.json", "manifest.json"]
    assert "results/r1" not in _git(remote, "branch", "--list")
    assert len(list(lpaths.predictions_dir("r1").glob("as_of=*.parquet"))) == 2    # the local predictions survive the merge


def test_checksum_write_verify_and_failures(cfg, tmp_path):
    paths = Paths(cfg, tmp_path)
    _write_run(paths, "r1", ("2024-07-01", "2024-07-08"))
    paths.manifest_file("r1").write_text("{}")
    checksum.write(paths, "r1", tmp_path, log=lambda *_: None)
    doc = json.loads(paths.checksums_file("r1").read_text())
    assert doc["n_files"] == 2 and set(doc["files"]) == {"as_of=2024-07-01.parquet", "as_of=2024-07-08.parquet"}
    assert checksum.verify(paths, "r1", log=lambda *_: None) == 0
    f = paths.prediction_file("r1", "2024-07-08")
    b = bytearray(f.read_bytes()); b[20] ^= 1; f.write_bytes(bytes(b))
    assert checksum.verify(paths, "r1", log=lambda *_: None) == 1
    f.unlink()
    assert checksum.verify(paths, "r1", log=lambda *_: None) == 1
    assert checksum.verify(paths, "nope", log=lambda *_: None) == 1


def test_collect_env_has_every_key_on_cpu():
    env = collect_env("cpu")
    for k in ("gpu_name", "gpu_count", "driver_version", "cuda_version", "torch_version", "python_version", "dtype", "hostname", "pod_id"):
        assert k in env
    assert env["hostname"] and env["python_version"] and env["dtype"] == "float32"
    try:
        import torch
        if not torch.cuda.is_available():
            assert env["gpu_name"] is None and env["gpu_count"] == 0 and env["cuda_version"] is None
    except ImportError:
        assert env["torch_version"] is None


def test_first_n_samples_rule_survives_a_larger_sample_count():
    """Appendix C choice 2: with sample_count > n_samples the signal uses the first n_samples sample_ids only."""
    rng = np.random.default_rng(1)
    preds = predictions_from_array("2024-07-05", ["a"], rng.lognormal(0, 0.01, (1, 50, 2, 5)) * 100)
    s20 = select_samples(preds, 20)
    assert sorted(s20["sample_id"].unique()) == list(range(20))
    big = preds.copy(); big.loc[big["sample_id"] >= 20, "pred_close"] *= 10      # later samples must not matter
    pd.testing.assert_frame_equal(select_samples(big, 20).reset_index(drop=True), s20.reset_index(drop=True))

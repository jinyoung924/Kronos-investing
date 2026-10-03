"""Appendix C-1: pod bundle, prediction checksums, environment info, per-profile sample selection."""
from __future__ import annotations

import json
import subprocess
import tarfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from B_model_infer import checksum, pod_bundle
from B_model_infer.env_info import collect_env
from C_signal.aggregate import select_samples
from common.config import cfg_override, load_config
from common.paths import Paths
from common.schema import predictions_from_array
from common.synthetic import write_synthetic_dataset

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def repo(cfg, tmp_path):
    """A small git repo with synthetic data/raw + data/universe (base and liq5 variants) and the project files the bundle needs."""
    root = tmp_path / "repo"
    root.mkdir()
    c = cfg_override(cfg, {"universe.indices": ["kospi200"], "universe.markets": {"kospi200": "KOSPI"}, "data.index_tickers": {}})
    write_synthetic_dataset(c, root, n_per_index=6, seed=2)
    paths = Paths(c, root)
    base = paths.constituents_file("kospi200", "base")
    pd.read_parquet(base).to_parquet(paths.constituents_file("kospi200", "liq5"), index=False)
    (root / "configs").mkdir()
    import yaml
    (root / "configs/base.yaml").write_text(yaml.safe_dump(c, allow_unicode=True))
    (root / "requirements.txt").write_text("numpy==1\n")
    (root / "requirements-infer.txt").write_text("-r requirements.txt\ntorch==9.9\n")
    (root / "B_model_infer").mkdir()
    (root / "B_model_infer/__init__.py").write_text("")
    (root / "B_model_infer/pod").mkdir()
    (root / "B_model_infer/pod/setup_pod.sh").write_text("#!/bin/bash\n")
    (root / "data/krx_raw/2026-09-28/stk").mkdir(parents=True)
    (root / "data/krx_raw/2026-09-28/stk/20240102.json").write_text("[]")          # must never be packed
    (root / ".gitignore").write_text("data/raw/*\ndata/universe/*\ndata/krx_raw/*\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"], cwd=root, check=True)
    return c, root, paths


def test_pack_contains_only_allowed_files_and_verify_detects_tampering(repo, tmp_path):
    c, root, paths = repo
    out = pod_bundle.pack(c, "run_x", "base", root, log=lambda *_: None)
    assert out == paths.bundle_path("run_x") and out.exists()
    with tarfile.open(out) as tf:
        names = [m.name for m in tf.getmembers() if m.isfile()]
    assert all(n.startswith("repo/") for n in names)
    assert not any("krx_raw" in n for n in names)
    assert "repo/data/raw/kospi200/prices.parquet" in names and "repo/data/universe/constituents_kospi200_liq5.parquet" in names
    assert "repo/data/universe/constituents_kospi200.parquet" not in names        # only the profile's variant
    assert "repo/inputs.sha256.json" in names and "repo/configs/base.yaml" in names and "repo/requirements-infer.txt" in names
    allowed = lambda n: n.startswith(("repo/B_model_infer/", "repo/configs/", "repo/data/raw/", "repo/data/universe/")) or n in (
        "repo/inputs.sha256.json", "repo/requirements.txt", "repo/requirements-infer.txt", "repo/.gitignore")
    assert all(allowed(n) for n in names), [n for n in names if not allowed(n)]
    # unpack, verify ok, flip one byte -> verify fails; extra parquet -> fails
    dest = tmp_path / "unpacked"
    with tarfile.open(out) as tf:
        tf.extractall(dest)
    bdir = dest / "repo"
    assert pod_bundle.verify(bdir, log=lambda *_: None) == 0
    f = bdir / "data/raw/kospi200/prices.parquet"
    b = bytearray(f.read_bytes()); b[10] ^= 1; f.write_bytes(bytes(b))
    assert pod_bundle.verify(bdir, log=lambda *_: None) == 1
    doc = json.loads((bdir / "inputs.sha256.json").read_text())
    assert doc["code_commit"] and doc["git_dirty"] is False and doc["profile"] == "base" and doc["n_files"] == len(names) - 1   # inputs.sha256.json lists every other file


def test_pack_refuses_a_dirty_tree_unless_allowed(repo):
    c, root, paths = repo
    (root / "requirements.txt").write_text("numpy==2\n")
    with pytest.raises(RuntimeError, match="modified"):
        pod_bundle.pack(c, "run_y", "base", root, log=lambda *_: None)
    out = pod_bundle.pack(c, "run_y", "base", root, allow_dirty=True, log=lambda *_: None)
    with tarfile.open(out) as tf:
        doc = json.loads(tf.extractfile("repo/inputs.sha256.json").read())
    assert doc["git_dirty"] is True and doc["dirty_files"]


def test_checksum_write_verify_and_failures(cfg, tmp_path):
    paths = Paths(cfg, tmp_path)
    run_dir = paths.predictions_dir("r1")
    run_dir.mkdir(parents=True)
    rng = np.random.default_rng(0)
    for d in ("2024-07-01", "2024-07-08"):
        df = predictions_from_array(d, ["a", "b"], rng.lognormal(0, 0.01, (2, 2, 3, 5)) * 100)
        df.to_parquet(paths.prediction_file("r1", d), index=False)
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

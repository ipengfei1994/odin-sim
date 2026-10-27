"""元信息采集的单元测试。"""
from __future__ import annotations

import odin_sim.reproducibility as R


def test_snapshot_contains_required_keys():
    snap = R.snapshot(packages=("numpy",))
    for key in ("git_commit", "python_version", "platform", "dependencies"):
        assert key in snap
    assert "numpy" in snap["dependencies"]


def test_missing_package_recorded_as_unknown():
    versions = R.dependency_versions(["definitely-not-installed-xyz"])
    assert versions["definitely-not-installed-xyz"] == R.UNKNOWN


def test_git_commit_is_unknown_outside_repo(tmp_path):
    assert R.git_commit(tmp_path) == R.UNKNOWN


def test_git_dirty_is_none_outside_repo(tmp_path):
    assert R.git_dirty(tmp_path) is None


def test_config_fingerprint_is_order_independent():
    a = {"tau": 60, "mode": "cross", "beta": 0.2}
    b = {"beta": 0.2, "mode": "cross", "tau": 60}
    assert R.config_fingerprint(a) == R.config_fingerprint(b)


def test_config_fingerprint_changes_with_value():
    assert R.config_fingerprint({"tau": 60}) != R.config_fingerprint({"tau": 30})

"""产物目录布局（命名规则）。

W1 阶段只固定命名规则；目录内部的字段口径待 W2 的 storage 模块确定，
届时只改本文件，不扩散到调用方。
"""
from __future__ import annotations

from pathlib import Path

__all__ = [
    "run_dir",
    "manifest_path",
    "timeseries_path",
    "final_state_path",
    "trajectory_path",
    "sweep_manifest_path",
    "results_csv_path",
]

RUNS_DIRNAME = "runs"
MANIFEST_FILENAME = "manifest.json"
TIMESERIES_FILENAME = "timeseries.csv"
FINAL_STATE_FILENAME = "final_state.npz"
TRAJECTORY_FILENAME = "trajectory.npz"
SWEEP_MANIFEST_FILENAME = "sweep_manifest.json"
RESULTS_FILENAME = "results.csv"


def run_dir(root: str | Path, run_id: str) -> Path:
    """一次运行的产物目录：<root>/runs/<run_id>。幂等：同一 run_id 落在同一目录。"""
    return Path(root) / RUNS_DIRNAME / run_id


def manifest_path(run: str | Path) -> Path:
    return Path(run) / MANIFEST_FILENAME


def timeseries_path(run: str | Path) -> Path:
    return Path(run) / TIMESERIES_FILENAME


def final_state_path(run: str | Path) -> Path:
    return Path(run) / FINAL_STATE_FILENAME


def trajectory_path(run: str | Path) -> Path:
    """整条轨迹（可选产物，默认不写）。"""
    return Path(run) / TRAJECTORY_FILENAME


def sweep_manifest_path(root: str | Path) -> Path:
    """一次 sweep 的汇总元信息：<root>/sweep_manifest.json。"""
    return Path(root) / SWEEP_MANIFEST_FILENAME


def results_csv_path(root: str | Path) -> Path:
    """一次 sweep 的汇总表：<root>/results.csv。"""
    return Path(root) / RESULTS_FILENAME

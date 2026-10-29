"""P2-3：长任务异步化——把大网格 sweep 放到后台进程跑，UI 轮询进度。

设计见 ``docs/ui-plan.md`` §13。要点：

- 复用 CLI（``python -m odin_sim.cli sweep``）作为后台进程，与同步路径产物
  schema 完全一致；进程崩了不影响 Streamlit 会话。
- 进度信号：``runs/<id>/manifest.json`` 随每个 run 落盘（runner 循环内逐条写），
  计数即已完成数；``total`` 由 UI 侧 ``expand_sweep`` 内核无关地算出。
- 失败信号：进程非零退出 + 捕获的 ``run.log`` 尾部。

本模块只做进程编排与状态读取，**不 import engine**；UI 只经此模块与 ``experiments``。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

from odin_sim.storage import layout

LOG_FILENAME = "run.log"


def sweep_command(
    config_path: str | Path,
    out_root: str | Path,
    metrics: list[str] | None = None,
    python: str | None = None,
) -> list[str]:
    """构造后台 sweep 的子进程 argv（复用 CLI，产物 schema 与同步一致）。"""
    python = python or sys.executable
    cmd = [
        python, "-m", "odin_sim.cli", "sweep",
        "--config", str(config_path),
        "--out", str(out_root),
    ]
    if metrics:
        cmd += ["--metrics", ",".join(metrics)]
    return cmd


def write_config(config: Mapping[str, Any], out_root: str | Path) -> Path:
    """把配置落盘为 ``<out_root>/sweep_config.json``（与 runs 页约定一致）。"""
    root = Path(out_root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "sweep_config.json"
    path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return path


def launch(
    config: Mapping[str, Any],
    out_root: str | Path,
    metrics: list[str] | None = None,
    python: str | None = None,
) -> subprocess.Popen:
    """后台启动一次 sweep；stdout/stderr 合并写入 ``<out_root>/run.log``。

    返回 ``Popen``（供轮询）。UI 应将其存入 ``st.session_state`` 跨 rerun 持有。
    环境（含 ``ODIN_BASELINE_ROOT`` 等内核配置）由子进程继承。
    """
    root = Path(out_root)
    config_path = write_config(config, root)
    log_path = root / LOG_FILENAME
    log_file = log_path.open("wb")
    cmd = sweep_command(config_path, root, metrics=metrics, python=python)
    return subprocess.Popen(
        cmd,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )


def progress_count(out_root: str | Path) -> int:
    """已完成 run 数 = ``runs/<id>/manifest.json`` 的数量（逐 run 落盘）。"""
    runs_dir = layout.run_dir(Path(out_root), "")
    if not runs_dir.is_dir():
        return 0
    return len(list(runs_dir.glob("*/manifest.json")))


def read_log_tail(out_root: str | Path, max_chars: int = 4000) -> str:
    """读取 ``<out_root>/run.log`` 尾部，用于失败时展示错误。"""
    log_path = Path(out_root) / LOG_FILENAME
    if not log_path.is_file():
        return ""
    text = log_path.read_text(encoding="utf-8", errors="replace")
    if len(text) <= max_chars:
        return text
    return "...（日志已截断）...\n" + text[-max_chars:]


def task_status(
    popen: Any, out_root: str | Path, total: int
) -> dict[str, Any]:
    """读取后台任务状态。

    返回 ``{"state": "running"|"done"|"failed", "done", "total", "error"}``。
    ``popen`` 需提供 ``.poll()``（真实 Popen 或测试 stub 均可）。
    """
    code = popen.poll()
    done = progress_count(out_root)
    if code is None:
        return {"state": "running", "done": done, "total": total, "error": None}
    if code == 0:
        return {"state": "done", "done": done, "total": total, "error": None}
    return {
        "state": "failed",
        "done": done,
        "total": total,
        "error": read_log_tail(out_root),
    }

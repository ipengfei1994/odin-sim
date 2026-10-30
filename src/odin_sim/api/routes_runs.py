"""实验提交与后台任务轮询端点。

设计约束（与切片 1 一致）：**不 import ``odin_sim.ui``**。``ui/background.py`` 同样是
L4，但分层不变量禁止核心/接口层反向依赖 UI 包；此处用 ~20 行进程编排直接复用 CLI，
既保持 L4 自洽，又让产物 schema 与同步路径完全一致（后台进程就是 ``python -m
odin_sim.cli sweep``）。

任务状态放在 ``app.state.tasks``（进程内字典）。对本地单用户工作台足够；状态机依赖
子进程 ``.poll()`` 与 ``runs/<id>/manifest.json`` 落盘计数，无需外部中间件。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request

from ..experiments import expand_sweep, sweep_from_json
from ..experiments.spec import RUN_FIELDS
from ..model import DYNAMICS, METRICS, TOPOLOGY
from ..storage import layout
from ..topology.generators import SUPPORTED_KINDS

router = APIRouter(prefix="/api", tags=["runs"])

_LOG_FILENAME = "run.log"


def _launch_sweep(config: dict[str, Any], out_dir: Path, metrics: list[str] | None) -> subprocess.Popen:
    """后台启动一次 sweep，复用 CLI（产物 schema 与同步一致）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = out_dir / "sweep_config.json"
    cfg_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    cmd = [
        sys.executable, "-m", "odin_sim.cli", "sweep",
        "--config", str(cfg_path),
        "--out", str(out_dir),
    ]
    if metrics:
        cmd += ["--metrics", ",".join(metrics)]
    log_file = (out_dir / _LOG_FILENAME).open("wb")
    return subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT)


def _progress_count(out_dir: Path) -> int:
    runs_dir = layout.run_dir(out_dir, "")
    if not runs_dir.is_dir():
        return 0
    return len(list(runs_dir.glob("*/manifest.json")))


def _log_tail(out_dir: Path, max_chars: int = 4000) -> str:
    log_path = out_dir / _LOG_FILENAME
    if not log_path.is_file():
        return ""
    text = log_path.read_text(encoding="utf-8", errors="replace")
    return text if len(text) <= max_chars else "...（日志已截断）...\n" + text[-max_chars:]


@router.get("/schema/sweep")
def api_sweep_schema() -> dict:
    """表单自读所需的全部结构：可用模型、指标、拓扑、扫描字段、场景、默认模板。

    所有清单来自注册表实读——注册表里没有的东西不会出现在表单里，避免"声称
    支持某模型实则跑不了"的虚假承诺。
    """
    return {
        "models": [
            {"name": name, "external_kernel": name.startswith("g3")}
            for name in DYNAMICS.names()
        ],
        "metrics": [
            {
                "name": name,
                "requires_control": bool(getattr(METRICS.get(name), "requires_control", False)),
            }
            for name in METRICS.names()
        ],
        "topologies": list(SUPPORTED_KINDS),
        "topology_generators": list(TOPOLOGY.names()),
        "run_fields": list(RUN_FIELDS),
        "scenarios": ["polarized", "neutral"],
        "default_template": {
            "name": "my-sweep",
            "model": DYNAMICS.names()[0] if DYNAMICS.names() else "reference-v1",
            "base": {"mode": "decisive", "scenario": "polarized", "placement": "ba"},
            "axes": {"beta_gov": [0.0, 0.25, 0.5, 0.75, 1.0]},
            "seeds": [1, 2, 3, 4, 5],
            "store_trajectory": True,
        },
    }


@router.post("/runs")
def api_submit_run(request: Request, payload: dict[str, Any] = Body(...)) -> dict:
    """提交一次 sweep：校验 → 展开得总数 → 后台启动 → 返回任务 id。

    ``config`` 是一份完整 SweepSpec；``metrics`` 为要计算的指标（``["all"]`` 表示
    全部不需对照的指标；缺省不计算）。每个 sweep 落盘到 ``results_root/<name>``，
    跑完即被 /api/sweeps 自动发现，无需改代码。
    """
    config = payload.get("config")
    metrics = payload.get("metrics")
    if not isinstance(config, dict):
        raise HTTPException(status_code=422, detail="config 必须是对象：SweepSpec 字典")
    if metrics is not None and not isinstance(metrics, list):
        raise HTTPException(status_code=422, detail="metrics 必须是字符串列表或省略")

    try:
        spec = sweep_from_json(json.dumps(config))
    except Exception as exc:  # SpecError 等：配置非法，立刻 422，不启动进程
        raise HTTPException(status_code=422, detail=f"非法的 sweep 配置：{exc}") from exc

    runs = expand_sweep(spec)
    total = len(runs)
    if total == 0:
        raise HTTPException(status_code=422, detail="展开后得到 0 条运行：请检查 axes / seeds")

    root = request.app.state.results_root
    out_dir = (root / spec.name).resolve()
    # 约束在产物根目录之内：拒绝任何越界 name（如 "../../etc"）
    if out_dir != root / spec.name:
        raise HTTPException(status_code=422, detail="sweep name 非法")
    try:
        out_dir.relative_to(root.resolve())
    except ValueError:
        raise HTTPException(status_code=422, detail="sweep name 非法（越界）") from None

    popen = _launch_sweep(config, out_dir, metrics)
    task_id = uuid.uuid4().hex[:12]
    request.app.state.tasks[task_id] = {
        "task_id": task_id,
        "popen": popen,
        "out_dir": out_dir,
        "total": total,
        "name": spec.name,
        "metrics": metrics,
        "created": time.time(),
    }
    return {"task_id": task_id, "total": total, "sweep": spec.name}


@router.get("/runs/{task_id}")
def api_task_status(task_id: str, request: Request) -> dict:
    """轮询后台任务进度。返回 done/total/state/error。"""
    tasks = request.app.state.tasks
    rec = tasks.get(task_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"未知任务：{task_id}")
    code = rec["popen"].poll()
    done = _progress_count(rec["out_dir"])
    if code is None:
        state = "running"
        error = None
    elif code == 0:
        state = "done"
        error = None
    else:
        state = "failed"
        error = _log_tail(rec["out_dir"])
    return {
        "task_id": task_id,
        "sweep": rec["name"],
        "state": state,
        "done": done,
        "total": rec["total"],
        "error": error,
    }

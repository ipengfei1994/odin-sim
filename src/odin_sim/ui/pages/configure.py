"""P1-4 + P2-3：配置实验并触发 sweep（同步小网格 / 后台大网格）。

复用 ``experiments.run_sweep`` 与 ``config_bridge`` 的校验，不重写任何
仿真 / 校验逻辑。P2-3 起的**异步化**把大网格交给后台独立进程
（``ui.background``，复用 CLI），UI 轮询进度、不阻塞会话；产物与同步路径同 schema。

页面逻辑抽成纯函数（``build_default_config`` / ``parse_config`` /
``trigger_sweep`` / ``_launch_background``），便于脱离 streamlit 运行时做单元测试。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import streamlit as st

from odin_sim.experiments import SweepError, run_sweep
from odin_sim.model import builtin_registration_error
from odin_sim.model.registry import DYNAMICS
from odin_sim.ui import background
from odin_sim.ui.config_bridge import validate_sweep_config

# 同步上限：超过该运行数默认建议后台运行（大网格同步会阻塞会话）。
MAX_SYNC_RUNS = 12

DEFAULT_OUT_ROOT = "results/ui_run"
CONFIG_ATTR = "ui_configure_text"
TASK_KEY = "ui_async_task"


def build_default_config() -> dict[str, Any]:
    """一个合法、快速的小网格默认配置（4 条运行）。"""
    return {
        "name": "ui-quick-run",
        "model": "g3-baseline-v1",
        "base": {
            "mode": "cross",
            "placement": "hub",
            "beta_gov": 0.2,
            "tau": 60,
            "scenario": "polarized",
        },
        "axes": {"beta_gov": [0.0, 0.4]},
        "seeds": {"start": 1, "count": 2, "step": 1},
        "store_trajectory": False,
        "on_error": "abort",
    }


def parse_config(text: str) -> dict[str, Any] | None:
    """解析 JSON 文本；非法时返回 None（由调用方提示）。"""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, Mapping):
        return None
    return dict(data)


def model_availability_message(model_name: str) -> str | None:
    """若模型未注册（内核依赖缺失）返回可读警告，否则返回 None。

    读-only 页面不依赖内核，但「运行 sweep」需要模型已注册；提前告知用户
    避免点击运行后才看到晦涩的注册错误。
    """
    if DYNAMICS.has(model_name):
        return None
    reason = builtin_registration_error() or "未知原因"
    return (
        f"模型 {model_name} 当前未注册（{reason}）。"
        f"点击「运行 sweep」会失败——需安装内核依赖并设置 ODIN_BASELINE_ROOT。"
        f"「浏览结果 / 可视化 / 演化曲线」页只读已落盘产物，不受此影响。"
    )


def trigger_sweep(
    spec: Any,
    out_root: str | Path,
    metrics: list[str] | None = None,
) -> dict[str, Any]:
    """同步执行一次 sweep，返回轻量执行摘要（不持有重对象）。

    直接复用 ``experiments.run_sweep``，不在 UI 内重跑任何仿真逻辑。
    """
    result = run_sweep(spec, out_root=out_root, metrics=metrics, echo=False)
    return {
        "name": result.name,
        "fingerprint": result.fingerprint,
        "total": result.total,
        "executed": result.executed,
        "skipped": result.skipped,
        "failed": result.failed,
        "aborted": result.aborted,
        "results_csv": str(result.results_csv) if result.results_csv else None,
        "sweep_manifest": (
            str(result.sweep_manifest) if result.sweep_manifest else None
        ),
        "ok": result.ok,
    }


# --------------------------------------------------------------------------- #
# 展示层
# --------------------------------------------------------------------------- #
def render() -> None:
    st.header("配置实验")
    st.caption("复用 experiments 的校验与执行；小网格同步、大网格后台异步。")

    # 后台任务监控：若有进行中/刚完成的任务，先展示进度与结果。
    _render_task_monitor()

    if CONFIG_ATTR not in st.session_state:
        st.session_state[CONFIG_ATTR] = json.dumps(
            build_default_config(), ensure_ascii=False, indent=2, sort_keys=True
        )

    text = st.text_area(
        "sweep 配置 (JSON)",
        value=st.session_state[CONFIG_ATTR],
        height=380,
        key="config_editor",
        help="与 CLI 的 sweep 配置同 schema；点「重置为默认」可恢复内置小网格样例。",
    )

    c_reset, c_export = st.columns([1, 2])
    with c_reset:
        if st.button("重置为默认", key="reset_cfg"):
            st.session_state[CONFIG_ATTR] = json.dumps(
                build_default_config(), ensure_ascii=False, indent=2, sort_keys=True
            )
            st.rerun()

    parsed = parse_config(text)
    if parsed is None:
        st.error("JSON 解析失败：请检查括号匹配与字符串引号。")
        return

    validation = validate_sweep_config(parsed)
    if not validation.ok:
        st.error(f"配置校验失败：{validation.error}")
        st.info("未通过校验前不会运行。修正后再试。")
        return

    st.success(f"配置合法：共 **{validation.run_count}** 条运行。")
    warn = model_availability_message(validation.spec.model)
    if warn is not None:
        st.warning(warn)
    if validation.run_count > MAX_SYNC_RUNS:
        st.info(
            f"运行数 {validation.run_count} 超过同步上限 {MAX_SYNC_RUNS}："
            f"建议用「后台」模式运行，UI 会轮询进度、不阻塞会话。"
        )

    run_mode = st.radio(
        "运行方式",
        ["同步（阻塞本会话）", "后台（推荐大网格）"],
        index=1 if validation.run_count > MAX_SYNC_RUNS else 0,
        key="run_mode",
        help="后台模式把 sweep 交给独立进程，UI 轮询进度、不阻塞；"
             "产物与同步路径完全一致，可在「浏览结果」直接查看。",
    )

    with c_export:
        st.download_button(
            "导出当前配置",
            json.dumps(parsed, ensure_ascii=False, indent=2, sort_keys=True),
            file_name="sweep_config.json",
            mime="application/json",
        )

    metrics_raw = st.text_input(
        "指标（逗号分隔；留空=不计算，all=全部非配对指标）",
        value="",
        key="metrics_input",
        help="与 CLI --metrics 同口径；需要配对对照的指标（如反噬）只有配置 control 时才计算。",
    )
    metrics = [m.strip() for m in metrics_raw.split(",") if m.strip()] or None

    out_root = st.text_input(
        "产物目录",
        value=DEFAULT_OUT_ROOT,
        key="out_root_input",
        help=f"运行产物（runs/<id>/ + sweep_manifest.json + results.csv）写入此处；"
        f"放在 results/ 下可被「浏览结果」自动发现。",
    )

    if st.button("运行 sweep", key="run_sweep", type="primary"):
        if run_mode.startswith("后台"):
            _launch_background(
                validation.spec, out_root, metrics, validation.run_count
            )
        else:
            _run_and_report(validation.spec, out_root, metrics)


def _run_and_report(spec: Any, out_root: str, metrics: list[str] | None) -> None:
    Path(out_root).mkdir(parents=True, exist_ok=True)
    # 落盘一份配置，便于事后复述（与 runs 页的 sweep_config.json 约定一致）。
    (Path(out_root) / "sweep_config.json").write_text(
        json.dumps(spec.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    try:
        with st.spinner(f"正在同步执行 sweep → {out_root} …（大网格会阻塞会话）"):
            summary = trigger_sweep(spec, out_root, metrics)
    except (SweepError, Exception) as exc:  # 含模型未注册 / 内核缺失等前置错误
        st.error(f"sweep 执行失败：{type(exc).__name__}: {exc}")
        return

    if summary["ok"]:
        st.success(
            f"完成：total={summary['total']} executed={summary['executed']} "
            f"skipped={summary['skipped']} failed={summary['failed']}"
        )
    else:
        st.error(
            f"执行未完成：aborted={summary['aborted']} failed={summary['failed']}；"
            f"已落盘的部分结果仍可在「浏览结果」查看。"
        )
    st.markdown(f"- **results.csv**：`{summary['results_csv']}`")
    st.markdown(f"- **sweep_manifest**：`{summary['sweep_manifest']}`")
    st.info(f"去「浏览结果」页，目录输入框填入 `{out_root}` 即可查看。")


# --------------------------------------------------------------------------- #
# 后台异步（P2-3）
# --------------------------------------------------------------------------- #
def _render_task_monitor() -> None:
    """展示后台任务进度/结果；运行中时每 1 秒自动 rerun 轮询（不阻塞独立进程）。"""
    task = st.session_state.get(TASK_KEY)
    if not task:
        return

    popen = task["popen"]
    out_root = task["out_root"]
    total = task["total"]
    name = task.get("name", "")
    status = background.task_status(popen, out_root, total)
    state = status["state"]
    done = status["done"]

    st.subheader(f"后台任务：{name}")
    if total > 0:
        st.progress(min(done / total, 1.0))
        st.caption(f"已完成 {done}/{total}")
    else:
        st.caption("尚未产出 run manifest（等待首个 run 落盘）…")

    if state == "running":
        st.info(
            "运行中…（点「刷新进度」更新；可切到「浏览结果」查看已落盘部分，"
            "切换不会中断后台进程。）"
        )
        if st.button("刷新进度", key="refresh_task"):
            st.rerun()
        return
    if state == "failed":
        st.error("后台 sweep 失败（进程非零退出）。日志尾部：")
        st.code(status["error"] or "（无日志）", language="text")
        if st.button("清除任务", key="clear_fail"):
            _clear_task()
            st.rerun()
        return

    # done
    st.success(f"后台 sweep 完成：{done}/{total}（含跳过，已落盘 results.csv）。")
    st.markdown(f"- **目录**：`{out_root}`")
    st.session_state["pending_browse_dir"] = str(out_root)
    st.info("已把目录记到「浏览结果」页，去那里即可自动载入。")
    if st.button("清除任务", key="clear_done"):
        _clear_task()
        st.rerun()


def _launch_background(
    spec: Any,
    out_root: str,
    metrics: list[str] | None,
    total: int,
) -> None:
    """后台启动 sweep：写配置 + 起独立进程，记录到 session_state 供轮询。"""
    if not DYNAMICS.has(spec.model):
        st.error(model_availability_message(spec.model))
        return
    _clear_task()  # 先清掉上一次遗留（含仍在跑的进程）
    popen = background.launch(spec.to_dict(), out_root, metrics=metrics)
    st.session_state[TASK_KEY] = {
        "popen": popen,
        "out_root": str(out_root),
        "total": total,
        "name": spec.name,
    }
    st.rerun()


def _clear_task() -> None:
    """清除任务记录；若进程仍在跑则先 terminate。"""
    task = st.session_state.get(TASK_KEY)
    if task and task.get("popen") is not None:
        popen = task["popen"]
        try:
            if popen.poll() is None:
                popen.terminate()
        except Exception:
            pass
    st.session_state.pop(TASK_KEY, None)

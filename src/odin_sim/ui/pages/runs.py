"""P1-3：浏览已有 sweep 结果（只读）。

只读消费已落盘产物（sweep_manifest.json / results.csv / runs/<id>/manifest.json），
不触发任何计算、不重跑仿真。数据读取逻辑抽成纯函数，便于脱离 streamlit 运行时单测。
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from odin_sim.storage import layout

# 默认展示目录：稠密网格演示数据，见 configs/sweep_demo_dense.json。
DEFAULT_SWEEP_DIR = "results/ref_demo_dense"


# --------------------------------------------------------------------------- #
# 纯函数：数据读取（无 streamlit 依赖，可单测）
# --------------------------------------------------------------------------- #
def discover_sweeps(base: str | Path = "results") -> list[Path]:
    """扫描 ``base`` 下含 ``sweep_manifest.json`` 的目录，按路径排序返回。"""
    base_path = Path(base)
    if not base_path.is_dir():
        return []
    return sorted(p.parent for p in base_path.glob("*/sweep_manifest.json"))


def load_sweep_manifest(root: str | Path) -> dict:
    """读取并解析 sweep 汇总元信息。"""
    path = layout.sweep_manifest_path(root)
    return json.loads(path.read_text(encoding="utf-8"))


def load_results_df(root: str | Path) -> pd.DataFrame | None:
    """读取 results.csv；不存在返回 None。"""
    csv = layout.results_csv_path(root)
    if not csv.is_file():
        return None
    return pd.read_csv(csv)


def iter_run_manifests(root: str | Path) -> list[Path]:
    """按 run_id 排序，返回各 run 的 manifest.json 路径。"""
    runs_dir = layout.run_dir(root, "")
    if not runs_dir.is_dir():
        return []
    return sorted(runs_dir.glob("*/manifest.json"))


# --------------------------------------------------------------------------- #
# 展示层
# --------------------------------------------------------------------------- #
def render() -> None:
    st.header("浏览结果")
    st.caption("只读消费已落盘产物，不触发任何计算。")

    pending = st.session_state.get("pending_browse_dir")
    root = _pick_sweep_dir(default=pending) if pending else _pick_sweep_dir()
    # 消费一次后即清除，避免之后一直覆盖用户手动选择。
    st.session_state.pop("pending_browse_dir", None)
    if root is None:
        return

    _show_summary(load_sweep_manifest(root))
    _show_results_table(root)
    _show_runs_explorer(root)


def _pick_sweep_dir(default: str = DEFAULT_SWEEP_DIR) -> Path | None:
    raw = st.text_input(
        "Sweep 目录",
        value=default,
        help="包含 sweep_manifest.json 的目录；留空或无效时会列出 results/ 下已检测到的 sweep。",
    )
    root = Path(raw)
    if root.is_dir() and layout.sweep_manifest_path(root).is_file():
        return root

    candidates = discover_sweeps("results")
    if candidates:
        st.info(f"“{raw}” 不是有效 sweep 目录，已从 results/ 检测到以下 sweep：")
        choice = st.selectbox("选择一个 sweep", [str(c) for c in candidates])
        return Path(choice)

    st.warning(f"未找到 sweep_manifest.json：{layout.sweep_manifest_path(root)}")
    return None


def _show_summary(data: dict) -> None:
    counts = data.get("counts", {})
    st.subheader("Sweep 概览")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("名称", data.get("name", "—"))
    c2.metric("总运行", counts.get("total", "—"))
    c3.metric("已执行", counts.get("executed", "—"))
    c4.metric("失败", counts.get("failed", "—"))

    spec = data.get("spec", {})
    c5, c6 = st.columns(2)
    c5.markdown("**模型**")
    c5.write(spec.get("model", "—"))
    c6.markdown("**轴 (axes)**")
    c6.write(_short(spec.get("axes", {})))
    st.markdown("**基础参数 (base)**")
    st.write(_short(spec.get("base", {})))


def _show_results_table(root: str | Path) -> None:
    df = load_results_df(root)
    if df is None:
        st.info("results.csv 不存在（该 sweep 可能尚未汇总）。")
        return
    st.subheader(f"汇总表 results.csv（{len(df)} 行）")
    st.dataframe(df, width="stretch")
    csv = layout.results_csv_path(root)
    st.download_button(
        "下载 results.csv",
        csv.read_bytes(),
        file_name=csv.name,
        mime="text/csv",
    )


def _show_runs_explorer(root: str | Path) -> None:
    manifests = iter_run_manifests(root)
    if not manifests:
        st.info("runs/ 下没有已完成的 run。")
        return

    st.subheader(f"运行明细（{len(manifests)} 个）")
    statuses = ["全部", *sorted({_status_of(m) for m in manifests})]
    choice = st.selectbox("按状态筛选", statuses)

    shown = 0
    for m in manifests:
        data = json.loads(m.read_text(encoding="utf-8"))
        if choice != "全部" and _status_of(m) != choice:
            continue
        shown += 1
        label = f"{data.get('run_id', m.parent.name)} · {_status_of(m)}"
        with st.expander(label):
            _show_run_detail(data, m.parent)

    if choice != "全部" and shown == 0:
        st.caption("该状态下没有 run。")


def _show_run_detail(data: dict, run_dir: Path) -> None:
    spec = data.get("spec", {})
    result = data.get("result", {})

    left, right = st.columns(2)
    with left:
        st.markdown("**配置**")
        st.json(
            {
                "mode": spec.get("mode"),
                "placement": spec.get("placement"),
                "beta_gov": spec.get("beta_gov"),
                "tau": spec.get("tau"),
                "scenario": spec.get("scenario"),
                "seed": spec.get("seed"),
                "param_overrides": spec.get("param_overrides", {}),
            }
        )
    with right:
        st.markdown("**结果**")
        st.json(
            {
                "status": data.get("status"),
                "wall_seconds": result.get("wall_seconds"),
                "series_final_ord": result.get("series_final_ord", {}),
                "series_final_all": result.get("series_final_all", {}),
                "cost": result.get("cost", {}),
            }
        )

    artifacts = data.get("artifacts", [])
    if artifacts:
        st.markdown("**产物**")
        for art in artifacts:
            p = run_dir / art
            if p.is_file() and p.suffix == ".csv":
                st.download_button(
                    f"下载 {art}",
                    p.read_bytes(),
                    file_name=f"{data.get('run_id', run_dir.name)}_{art}",
                    mime="text/csv",
                    key=f"dl_{run_dir.name}_{art}",
                )
            else:
                st.caption(f"{art}（{ '存在' if p.is_file() else '缺失' }）")


def _status_of(manifest_path: Path) -> str:
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "unknown"
    return str(data.get("status", "unknown"))


def _short(value: object) -> str:
    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= 120 else text[:117] + "…"

"""G3 baseline 指纹回归：重算轨迹并与 baseline 数据集逐字符比对。

验收口径（W1-b）：同一 (config, seed) 在平台上重算得到的 ``traj_hash`` 与
``network_hash`` 必须与 ``baseline/data/*.csv`` 中记录的值**逐字符相同**。
不允许"近似相等"，不允许修改期望值。
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np

from .adapters.baseline_g3 import BaselineKernel

__all__ = [
    "Mismatch",
    "VerifyResult",
    "read_rows",
    "verify_rows",
    "verify_metric_rows",
    "DEFAULT_FIELDS",
    "DEFAULT_METRIC_FIELDS",
]

DEFAULT_FIELDS = ("traj_hash", "network_hash")

# 指标等价性回归的默认字段集（W1-c）。
# 覆盖：极端占比 / 联合极端 / 配对差值 / 反噬 / 预警提前量 / 停投恢复 / 成本计数。
DEFAULT_METRIC_FIELDS = (
    "extreme_t0_ord", "extreme_final_ord",
    "joint_t0_ord", "joint_final_ord",
    "extreme_final_all", "joint_final_all",
    "delta_extreme", "joint_excess_final",
    "bf_final", "bf_transient", "bf_any", "excess_max", "excess_t_max",
    "alert_step", "event_step", "lead_time", "censored",
    "recovery_E_at_stop", "recovery_rebound",
    "cost_agent_steps", "cost_msgs", "cost_beta",
    "n_adv", "n_gov", "n_ordinary",
)


@dataclass(frozen=True)
class Mismatch:
    row_index: int
    config_id: str
    seed: int
    field_name: str
    expected: str
    actual: str


@dataclass
class VerifyResult:
    total: int = 0
    matched: int = 0
    mismatches: list[Mismatch] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.total > 0 and not self.mismatches


def read_rows(path: str | Path) -> list[dict]:
    """读取 baseline 的逐次运行 CSV（只读）。"""
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _row_to_config(kernel: BaselineKernel, row: dict):
    return kernel.Config(
        mode=row["mode"],
        placement=row["placement"],
        beta_gov=float(row["beta_gov"]),
        tau=int(row["tau"]),
    )


def verify_rows(
    kernel: BaselineKernel,
    rows: Iterable[dict],
    scenario: str = "polarized",
    fields: Sequence[str] = DEFAULT_FIELDS,
) -> VerifyResult:
    """对每一行重算并比对指纹字段。"""
    params = kernel.scenario_params(scenario)
    result = VerifyResult()

    for idx, row in enumerate(rows):
        cfg = _row_to_config(kernel, row)
        seed = int(row["seed"])
        res = kernel.simulate(cfg, seed, params)

        result.total += 1
        row_ok = True
        for field_name in fields:
            expected = row.get(field_name, "")
            actual = str(res.meta.get(field_name, ""))
            if expected != actual:
                row_ok = False
                result.mismatches.append(
                    Mismatch(
                        row_index=idx,
                        config_id=row.get("config_id", ""),
                        seed=seed,
                        field_name=field_name,
                        expected=expected,
                        actual=actual,
                    )
                )
        if row_ok:
            result.matched += 1

    return result


def _fmt_cell(value: object) -> str:
    """按 baseline 的落盘口径格式化单元格（与 runner._fmt 等价）。

    None -> ""；bool -> "True"/"False"；float -> "%.6g"；其余转为字符串。
    比对必须在**落盘后的字符串**上进行，避免浮点表示差异造成假阳性。
    """
    if value is None:
        return ""
    if isinstance(value, (bool, np.bool_)):
        return "True" if value else "False"
    if isinstance(value, (float, np.floating)):
        return f"{float(value):.6g}"
    return str(value)


def verify_metric_rows(
    kernel: BaselineKernel,
    rows: Iterable[dict],
    scenario: str = "polarized",
    fields: Sequence[str] = DEFAULT_METRIC_FIELDS,
    need_control: bool = True,
) -> VerifyResult:
    """指标等价性回归：重算指标行并与 baseline CSV 逐字段比对。

    与 ``verify_rows`` 的区别
    ------------------------
    ``verify_rows`` 只比对轨迹/网络指纹，证明"数值路径一致"；
    本函数进一步比对 **全部指标字段**，证明"从轨迹到指标的口径一致"。

    实现方式
    --------
    委托 baseline 的 ``runner.run_row``（含对照配对逻辑）构造整行，
    再与 CSV 逐字段做字符串比对。**不复制指标公式**，因此本函数验证的是
    平台的调用链与落盘口径，而不是一份独立实现的等价性——平台侧的独立
    指标实现要到 W2 重构时才引入，届时以本回归作为保护。
    """
    params = kernel.scenario_params(scenario)
    result = VerifyResult()

    for idx, row in enumerate(rows):
        cfg = _row_to_config(kernel, row)
        seed = int(row["seed"])
        actual_row = kernel.run_row(cfg, seed, params, need_control=need_control)

        result.total += 1
        row_ok = True
        for field_name in fields:
            expected = row.get(field_name, "")
            actual = _fmt_cell(actual_row.get(field_name))
            if expected != actual:
                row_ok = False
                result.mismatches.append(
                    Mismatch(
                        row_index=idx,
                        config_id=row.get("config_id", ""),
                        seed=seed,
                        field_name=field_name,
                        expected=expected,
                        actual=actual,
                    )
                )
        if row_ok:
            result.matched += 1

    return result

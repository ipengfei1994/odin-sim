"""UI 配置桥：把表单 dict 转成 ``SweepSpec``，复用 experiments 的校验。

UI 不重写 sweep 配置 schema，只复用 ``experiments.SweepSpec.from_dict`` 与
``expand_sweep``，保证与 CLI 完全一致的校验口径（见 ``docs/ui-plan.md`` §5）。
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from odin_sim.experiments import SpecError, SweepSpec, expand_sweep


@dataclass(frozen=True)
class SweepValidation:
    """一次配置校验的结果，便于 UI 直接渲染（无需 try/except）。"""

    ok: bool
    spec: SweepSpec | None
    error: str | None
    run_count: int | None


def validate_sweep_config(config: Mapping[str, Any]) -> SweepValidation:
    """校验 UI 表单产出的配置 dict，返回结构化结果。

    复用 ``SweepSpec.from_dict``（CLI 经 ``sweep_from_json`` 调用的同一校验）
    与 ``expand_sweep``：未知字段、缺必填（如 ``mode`` / ``scenario``）、
    类型错、展开冲突都会由 ``SpecError`` 给出可读信息，不静默忽略。
    """
    try:
        spec = SweepSpec.from_dict(dict(config))
    except SpecError as exc:
        return SweepValidation(ok=False, spec=None, error=str(exc), run_count=None)
    try:
        runs = expand_sweep(spec)
    except SpecError as exc:
        return SweepValidation(ok=False, spec=None, error=str(exc), run_count=None)
    return SweepValidation(ok=True, spec=spec, error=None, run_count=len(runs))


def sweep_spec_from_json(text: str) -> SweepSpec:
    """从 JSON 文本（如 UI 导出的配置）解析，与 CLI ``sweep_from_json`` 同口径。"""
    from odin_sim.experiments import sweep_from_json

    return sweep_from_json(text)


def sweep_spec_to_json(spec: SweepSpec) -> str:
    """``SweepSpec`` → 可复现的 JSON 文本（供导出 / 与 CLI 互操作）。"""
    return json.dumps(spec.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)

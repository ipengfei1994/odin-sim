"""实验规格：把一份配置确定性地展开成一组可执行的运行。

设计约束
--------
1. **展开顺序固定**：同一份配置任何时候展开都得到同一顺序的同一批运行，
   行序本身就是可复现的一部分。
2. **run_id 内容寻址**：``run_id = sha1(canonical(spec))[:16]``，只由运行
   规格决定，与输出目录、执行时间、执行次数无关。因此换目录重跑会命中
   同一批 run_id，天然支持断点续跑。
3. **不允许静默去重**：展开出重复 run_id 直接报错。重复通常意味着配置写错，
   去重会把"少跑了实验"伪装成"跑完了"。
4. **不替研究做默认值**：种子必须显式给出；未声明的轴不参与扫描。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from itertools import product
from typing import Any, Mapping, Sequence

from ..model.protocol import RunRequest
from ..reproducibility import config_fingerprint

__all__ = [
    "SpecError",
    "RunSpec",
    "SweepSpec",
    "RUN_FIELDS",
    "PARAMS_PREFIX",
    "expand_sweep",
]

RUN_FIELDS = ("mode", "placement", "beta_gov", "tau", "scenario")
PARAMS_PREFIX = "param_overrides."
_ON_ERROR = ("abort", "continue")


class SpecError(ValueError):
    """实验规格非法。"""


@dataclass(frozen=True)
class RunSpec:
    """一次运行的完整规格。对应一个 :class:`RunRequest` 加模型名。"""

    model: str
    mode: str
    seed: int
    placement: str = "ba"
    beta_gov: float = 0.0
    tau: int = 0
    scenario: str = "polarized"
    param_overrides: dict[str, Any] = field(default_factory=dict)

    def to_request(self) -> RunRequest:
        return RunRequest(
            mode=self.mode,
            seed=self.seed,
            placement=self.placement,
            beta_gov=self.beta_gov,
            tau=self.tau,
            scenario=self.scenario,
            param_overrides=dict(self.param_overrides),
        )

    def to_dict(self) -> dict[str, Any]:
        """规范化字典（键序固定），用于 run_id 与 manifest。"""
        return {
            "model": self.model,
            "mode": self.mode,
            "seed": int(self.seed),
            "placement": self.placement,
            "beta_gov": float(self.beta_gov),
            "tau": int(self.tau),
            "scenario": self.scenario,
            "param_overrides": {str(k): v for k, v in sorted(self.param_overrides.items())},
        }

    @property
    def spec_fingerprint(self) -> str:
        """运行规格指纹。"""
        return config_fingerprint(self.to_dict())

    @property
    def run_id(self) -> str:
        """产物目录名：只由规格内容决定。"""
        return self.spec_fingerprint

    @property
    def config_id(self) -> str:
        return self.to_request().config_id


@dataclass(frozen=True)
class SweepSpec:
    """一次批量实验的规格：基准参数 × 扫描轴 × 种子。

    ``axes`` 的键必须是 ``RUN_FIELDS`` 之一，或 ``param_overrides.<key>``。
    ``base`` 提供未扫描字段的取值；``mode`` 与 ``scenario`` 无默认值，
    必须由配置显式给出——平台不猜研究配置。
    """

    name: str
    model: str
    base: dict[str, Any] = field(default_factory=dict)
    axes: dict[str, list[Any]] = field(default_factory=dict)
    seeds: list[int] = field(default_factory=list)
    store_trajectory: bool = False
    on_error: str = "abort"

    @property
    def fingerprint(self) -> str:
        """sweep 级指纹（含扫描轴与种子，不含输出路径）。"""
        return config_fingerprint(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "model": self.model,
            "base": dict(self.base),
            "axes": {str(k): list(v) for k, v in self.axes.items()},
            "seeds": [int(s) for s in self.seeds],
            "store_trajectory": bool(self.store_trajectory),
            "on_error": self.on_error,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SweepSpec":
        """从配置字典构造，并做字段校验。未知字段一律报错，不静默忽略。"""
        if not isinstance(data, Mapping):
            raise SpecError("sweep 配置必须是对象/映射")

        known = {"name", "model", "base", "axes", "seeds",
                 "store_trajectory", "on_error"}
        unknown = sorted(set(data) - known)
        if unknown:
            raise SpecError(f"sweep 配置含未知字段：{unknown}")

        for required in ("name", "model"):
            if not data.get(required):
                raise SpecError(f"sweep 配置缺少必填字段：{required}")

        base = dict(data.get("base") or {})
        base_unknown = sorted(set(base) - set(RUN_FIELDS) - {"param_overrides"})
        if base_unknown:
            raise SpecError(
                f"base 含非法字段：{base_unknown}；"
                f"允许 {list(RUN_FIELDS)} 与 param_overrides"
            )
        if "mode" not in base:
            raise SpecError("base 必须显式给出 mode（平台不代研究猜配置）")
        if "param_overrides" in base and not isinstance(base["param_overrides"], Mapping):
            raise SpecError("base.param_overrides 必须是对象/映射")

        axes_raw = dict(data.get("axes") or {})
        axes: dict[str, list[Any]] = {}
        for key, values in axes_raw.items():
            if not isinstance(values, (list, tuple)) or not values:
                raise SpecError(f"axes.{key} 必须是非空列表")
            if key in RUN_FIELDS or key.startswith(PARAMS_PREFIX):
                axes[key] = list(values)
            else:
                raise SpecError(
                    f"axes 键非法：{key}；允许 {list(RUN_FIELDS)} 或 "
                    f"{PARAMS_PREFIX}<参数名>"
                )

        seeds = _parse_seeds(data.get("seeds"))

        on_error = str(data.get("on_error", "abort"))
        if on_error not in _ON_ERROR:
            raise SpecError(f"on_error 必须是 {_ON_ERROR} 之一，实际：{on_error}")

        return cls(
            name=str(data["name"]),
            model=str(data["model"]),
            base=base,
            axes=axes,
            seeds=seeds,
            store_trajectory=bool(data.get("store_trajectory", False)),
            on_error=on_error,
        )


def _parse_seeds(raw: Any) -> list[int]:
    """种子必须是显式的：列表，或 {"start","count","step"}。"""
    if raw is None:
        raise SpecError("seeds 必须显式给出（列表或 {start, count, step}）")
    if isinstance(raw, Mapping):
        unknown = sorted(set(raw) - {"start", "count", "step"})
        if unknown:
            raise SpecError(f"seeds 对象含未知字段：{unknown}")
        try:
            start = int(raw["start"])
            count = int(raw["count"])
        except KeyError as exc:
            raise SpecError(f"seeds 对象缺少字段：{exc}") from exc
        step = int(raw.get("step", 1))
        if count <= 0:
            raise SpecError("seeds.count 必须为正")
        if step <= 0:
            raise SpecError("seeds.step 必须为正")
        return [start + i * step for i in range(count)]
    if isinstance(raw, (list, tuple)) and raw:
        return [int(s) for s in raw]
    raise SpecError("seeds 必须是非空列表或 {start, count, step}")


def expand_sweep(spec: SweepSpec) -> list[RunSpec]:
    """把 sweep 展开成运行列表。

    顺序：先按 ``axes`` 的**声明顺序**做笛卡尔积，再对每个组合按种子顺序
    展开。因此同一配置的重复样本在结果表中相邻，便于聚合。
    """
    keys = list(spec.axes.keys())
    combos: Sequence[tuple] = list(product(*(spec.axes[k] for k in keys))) if keys else [()]

    out: list[RunSpec] = []
    seen: dict[str, tuple] = {}
    for combo in combos:
        overrides = dict(spec.base)
        params = dict(spec.base.get("param_overrides") or {})
        for key, value in zip(keys, combo):
            if key.startswith(PARAMS_PREFIX):
                params[key[len(PARAMS_PREFIX):]] = value
            else:
                overrides[key] = value
        for seed in spec.seeds:
            run = RunSpec(
                model=spec.model,
                mode=str(overrides.get("mode", "")),
                seed=int(seed),
                placement=str(overrides.get("placement", "ba")),
                beta_gov=float(overrides.get("beta_gov", 0.0)),
                tau=int(overrides.get("tau", 0)),
                scenario=str(overrides.get("scenario", "polarized")),
                param_overrides=dict(params),
            )
            if run.run_id in seen:
                raise SpecError(
                    f"展开出重复的运行（run_id={run.run_id}）："
                    f"{seen[run.run_id]} 与 {run.to_dict()}；"
                    "平台不去重，请修正配置"
                )
            seen[run.run_id] = run.to_dict()
            out.append(run)
    return out


def sweep_from_json(text: str) -> SweepSpec:
    """从 JSON 文本解析 sweep 配置。"""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SpecError(f"sweep 配置不是合法 JSON：{exc}") from exc
    return SweepSpec.from_dict(data)


def hash_payload(obj: Mapping[str, Any]) -> str:
    """与 :func:`config_fingerprint` 一致的内容哈希（此处仅为显式复用点）。"""
    return hashlib.sha1(
        json.dumps(obj, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]

"""S5｜实验形态展开：双对照组、多底座、正反扫描、N 序列、多种子。

展开纪律
--------
* **确定性**：同一配置任何时候展开得到同一顺序的同一批 ``ResearchRun``；
  ``run_key`` 由内容寻址生成，与输出目录、时间、执行次数无关。
* **不替研究做默认值**：对照组、底座、正反扫描、N 序列都必须在配置中
  显式声明才参与展开。平台提供机制，不内置「哪个对照更合理」的判断。
* **不塞进核心 SweepSpec**：这些维度若写进 ``SweepSpec.to_dict()`` 会改变
  既有 sweep 指纹与 ``run_id``，破坏既有实验的可复现性。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from itertools import product
from typing import Any, Mapping, Sequence

from ..reproducibility import config_fingerprint
from .config import ExperimentConfig

__all__ = [
    "DEFAULT_CONTROL_GROUPS", "DIRECTION_FORWARD", "DIRECTION_REVERSE",
    "ResearchRun", "expand_research_runs", "expand_control_groups",
    "hysteresis_pairs", "finite_size_series",
]

DEFAULT_CONTROL_GROUPS = ("main", "no_intervention", "random_script")
"""L2 鲁棒性要求「无干预」与「随机话术」两个对照组；仅在配置显式声明时启用。"""

DIRECTION_FORWARD = "forward"
DIRECTION_REVERSE = "reverse"


@dataclass(frozen=True)
class ResearchRun:
    """一条研究运行（核心 ``RunSpec`` 之外的研究维度）。"""

    run_key: str
    experiment_id: str
    population_size: int
    seed: int
    backend: str
    control_group: str
    direction: str
    index: int
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def simulation_id(self) -> str:
        return f"sim-{self.run_key}"


def _canonical(data: Mapping[str, Any]) -> str:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)


def expand_research_runs(config: ExperimentConfig) -> list[ResearchRun]:
    """按声明顺序展开：N × 种子 × 底座 × 对照组 × 扫描方向。

    顺序固定为 ``product`` 的字典序（N 外层、方向最内层），行序本身是
    可复现的一部分。未声明的维度退化为单元素，不产生额外运行。
    """
    config.validate()
    sizes = list(config.effective_population_sizes)
    seeds = [int(s) for s in config.seeds]
    backends = list(config.backends) or ["default"]
    # 对照组展开必须与 expand_control_groups 同一口径：显式声明的对照组之外
    # 必须保留 main 主轨，否则主实验组不会产出任何运行、正反扫描也配不上对。
    groups = expand_control_groups(config)
    directions = [DIRECTION_FORWARD, DIRECTION_REVERSE] if config.hysteresis_scan else [DIRECTION_FORWARD]

    experiment_id = f"exp-{config.fingerprint}"
    runs: list[ResearchRun] = []
    seen: set[str] = set()
    for index, (n, seed, backend, group, direction) in enumerate(
        product(sizes, seeds, backends, groups, directions)
    ):
        body = {
            "experiment_id": experiment_id,
            "population_size": int(n),
            "seed": int(seed),
            "backend": str(backend),
            "control_group": str(group),
            "direction": str(direction),
        }
        key = hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()[:16]
        if key in seen:
            raise ValueError(f"展开出重复的 run_key={key}；平台不静默去重：{body}")
        seen.add(key)
        runs.append(ResearchRun(
            run_key=key, experiment_id=experiment_id,
            population_size=int(n), seed=int(seed), backend=str(backend),
            control_group=str(group), direction=str(direction), index=index,
        ))
    return runs


def expand_control_groups(config: ExperimentConfig) -> list[str]:
    """双对照组展开。未配置时返回 ``["main"]``，不擅自替研究者选对照。"""
    if not config.control_groups:
        return ["main"]
    groups = list(dict.fromkeys(config.control_groups))
    if "main" not in groups:
        groups.insert(0, "main")
    unknown = [g for g in groups if g not in DEFAULT_CONTROL_GROUPS]
    if unknown:
        raise ValueError(
            f"未知对照组：{unknown}；允许 {list(DEFAULT_CONTROL_GROUPS)}。"
            "对照口径由配置给出（v2 L2 要求无干预 + 随机话术两个对照组）"
        )
    return groups


def hysteresis_pairs(runs: Sequence[ResearchRun]) -> list[tuple[ResearchRun, ResearchRun]]:
    """把正反扫描按 (N, seed, backend, control_group) 配成对；缺配对的跳过。"""
    index: dict[tuple[Any, ...], dict[str, ResearchRun]] = {}
    for run in runs:
        key = (run.population_size, run.seed, run.backend, run.control_group)
        index.setdefault(key, {})[run.direction] = run
    return [
        (pair[DIRECTION_FORWARD], pair[DIRECTION_REVERSE])
        for pair in index.values()
        if DIRECTION_FORWARD in pair and DIRECTION_REVERSE in pair
    ]


def finite_size_series(runs: Sequence[ResearchRun]) -> dict[int, list[ResearchRun]]:
    """按群体规模 N 分组，供有限尺寸标定（Q-44，N 序列由配置显式给出）。"""
    out: dict[int, list[ResearchRun]] = {}
    for run in runs:
        out.setdefault(run.population_size, []).append(run)
    return {n: out[n] for n in sorted(out)}


def run_fingerprint(run: ResearchRun) -> str:
    """单条研究运行的稳定指纹（供产物目录命名）。"""
    return config_fingerprint({
        "experiment_id": run.experiment_id,
        "population_size": run.population_size,
        "seed": run.seed,
        "backend": run.backend,
        "control_group": run.control_group,
        "direction": run.direction,
    })

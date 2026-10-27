"""独立入口：对 G3 baseline 数据集做轨迹指纹回归。

等价于 `odin-sim verify-baseline`，保留脚本形式是为了能在未安装本包的环境中直接运行。

用法：
    uv run python scripts/verify_baseline_fingerprint.py --data <baseline>/data/g3_grid_runs.csv
"""
from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from odin_sim.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(["verify-baseline", *sys.argv[1:]]))

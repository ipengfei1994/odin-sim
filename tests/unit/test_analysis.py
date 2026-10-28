"""分析层（跨条件聚合 + 置信区间）单元测试。"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from odin_sim.analysis import aggregate, aggregate_to_csv, estimate_ci, load_results


def _fake_results() -> list[dict]:
    """两组：beta_gov=0.0 / 0.2，各 3 个种子；目标指标随种子线性、随 beta_gov 平移。"""
    rows = []
    for bg in (0.0, 0.2):
        for s in (1, 2, 3):
            rows.append({
                "run_id": f"r{bg}_{s}",
                "mode": "cross",
                "beta_gov": float(bg),
                "seed": s,
                "g3.backfire.bf_final": bg + 0.1 * s,
                "some_bool_metric": True,
                "some_text_metric": "x",
            })
    return rows


def test_aggregate_groups_and_stats():
    out = aggregate(
        _fake_results(), group_by=["mode", "beta_gov"],
        metrics=["g3.backfire.bf_final"],
    )
    assert len(out) == 2
    g0 = next(r for r in out if r["beta_gov"] == "0.0")
    assert g0["n"] == 3
    # 0.1, 0.2, 0.3 -> mean 0.2
    assert abs(g0["g3.backfire.bf_final.mean"] - 0.2) < 1e-9
    assert g0["g3.backfire.bf_final.std"] > 0
    assert g0["g3.backfire.bf_final.ci_low"] < 0.2 < g0["g3.backfire.bf_final.ci_high"]

    g1 = next(r for r in out if r["beta_gov"] == "0.2")
    assert abs(g1["g3.backfire.bf_final.mean"] - 0.4) < 1e-9


def test_aggregate_bool_metric_rate():
    out = aggregate(_fake_results(), group_by=["mode"], metrics=["some_bool_metric"])
    row = out[0]
    # 6 行（2 beta_gov × 3 seed）同 mode，some_bool_metric 全为 True -> 发生率 1.0
    assert row["some_bool_metric.n"] == 6
    assert row["some_bool_metric.n_true"] == 6
    assert abs(row["some_bool_metric.rate"] - 1.0) < 1e-9
    assert row["some_bool_metric.rate_ci_low"] <= 1.0 <= row["some_bool_metric.rate_ci_high"]


def test_aggregate_skips_text_metric():
    out = aggregate(_fake_results(), group_by=["mode"], metrics=["some_text_metric"])
    row = out[0]
    # 纯文本（非数值非布尔）全组缺失 -> n=0
    assert row["some_text_metric.n"] == 0
    assert row["some_text_metric.rate"] is None


def test_load_results_coerces(tmp_path: Path):
    csvp = tmp_path / "results.csv"
    csvp.write_text(
        "run_id,mode,beta_gov,seed,g3.backfire.bf_final,flag\n"
        "r1,cross,0.0,1,0.5,True\n"
        "r2,cross,0.2,2,,False\n",
        encoding="utf-8",
    )
    res = load_results(tmp_path)
    assert len(res) == 2
    assert res[0]["beta_gov"] == 0.0
    assert res[0]["g3.backfire.bf_final"] == 0.5
    assert res[0]["flag"] is True
    assert res[1]["g3.backfire.bf_final"] is None
    assert res[1]["flag"] is False


def test_estimate_ci():
    vals = [1.0, 2.0, 3.0, 4.0, 5.0]
    lo, hi = estimate_ci(vals, 0.95)
    mean = 3.0
    assert lo < mean < hi
    # 区间以均值对称（t 与正态近似都应满足）
    assert abs((mean - lo) - (hi - mean)) < 1e-9
    assert hi - lo > 0

    # 单样本：区间退化为点
    lo1, hi1 = estimate_ci([7.0], 0.95)
    assert lo1 == hi1 == 7.0


def test_aggregate_to_csv_writes(tmp_path: Path):
    out_path = tmp_path / "agg.csv"
    path = aggregate_to_csv(
        _fake_results(), group_by=["mode", "beta_gov"],
        metrics=["g3.backfire.bf_final"], out_path=out_path,
    )
    assert path.is_file()
    text = path.read_text(encoding="utf-8").splitlines()
    assert text[0].startswith("mode,beta_gov,n,g3.backfire.bf_final")
    assert len(text) == 3  # header + 2 组


def test_cli_analyze_prints_csv(tmp_path, capsys):
    csvp = tmp_path / "results.csv"
    csvp.write_text(
        "run_id,mode,beta_gov,seed,g3.backfire.bf_final\n"
        "r1,cross,0.0,1,0.5\n"
        "r2,cross,0.0,2,0.7\n"
        "r3,cross,0.2,3,0.9\n",
        encoding="utf-8",
    )
    from odin_sim.cli import main

    rc = main([
        "analyze", "--sweep", str(tmp_path),
        "--group-by", "mode", "beta_gov",
        "--metric", "g3.backfire.bf_final",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "mode,beta_gov,n" in out
    assert "0.0" in out and "0.2" in out

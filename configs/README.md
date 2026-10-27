# 配置文件

本目录目前为空（不创建占位配置）。

配置 schema 将与模型协议一起落地，字段来源为外部参考实现的参数口径，并区分
"既有给定值"与"未标定名义值"。

> 参考实现的参数表属于 odin-agents，不随本仓库发布；
> 本地副本见 `docs/internal/`（已在 `.gitignore` 中排除）。

## sweep 配置

`sweep_example.json` 是批量实验配置的示例（`model` 为 `g3-baseline-v1`，
需要外部 G3 baseline 可用才能执行）：

```bash
uv run python -m odin_sim.cli sweep --config configs/sweep_example.json \
    --out results/example --dry-run    # 只看展开结果
uv run python -m odin_sim.cli sweep --config configs/sweep_example.json \
    --out results/example --metrics all
```

字段口径与限制见 `docs/experiments.md`。

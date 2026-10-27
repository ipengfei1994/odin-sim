"""渲染 README 主视觉：用平台自己的拓扑生成器画一张真实网络。

不用示意图、不用外部素材：图里的每一个节点和每一条边都来自
``odin_sim.topology.build_network`` 的真实输出，固定 seed 可复现。

用法：
    uv run python scripts/render_hero_network.py [--out docs/assets/hero-network.svg]

布局是自写的 Fruchterman–Reingold（纯 numpy），不引入 matplotlib 等依赖。
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from odin_sim.topology import build_network

WIDTH, HEIGHT = 1200, 520
BG = "#0B1B2B"
EDGE = "#2E5C77"
NODE_LO = "#2E8C7E"
NODE_HI = "#7FE3C8"
NODE_TEXT = "#0B1B2B"


def layout(src: np.ndarray, dst: np.ndarray, n: int, seed: int = 7,
           iterations: int = 320) -> np.ndarray:
    """Fruchterman–Reingold 力导向布局（纯 numpy）。"""
    rng = np.random.default_rng(seed)
    angle = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False)
    pos = np.column_stack([np.cos(angle), np.sin(angle)])
    pos = pos + rng.normal(0.0, 0.04, size=(n, 2))

    k = np.sqrt(1.0 / n)
    temp = 0.10
    step = temp / max(iterations, 1)

    for _ in range(iterations):
        delta = pos[:, None, :] - pos[None, :, :]
        dist = np.sqrt((delta ** 2).sum(-1)) + 1e-9
        np.fill_diagonal(dist, 1e9)

        disp = (delta * (k * k / dist ** 2)[..., None]).sum(axis=1)

        d = pos[src] - pos[dst]
        dn = np.sqrt((d ** 2).sum(-1)) + 1e-9
        attract = d * (dn / k)[:, None]
        np.add.at(disp, src, -attract)
        np.add.at(disp, dst, attract)

        norm = np.linalg.norm(disp, axis=1) + 1e-9
        disp = disp / norm[:, None] * np.minimum(norm, temp)[:, None]
        pos = pos + disp
        temp = max(temp - step, 0.001)

    return pos


def render(pos: np.ndarray, src: np.ndarray, dst: np.ndarray,
           deg: np.ndarray) -> str:
    xs, ys = pos[:, 0], pos[:, 1]
    pad = 0.10

    def scale(v: np.ndarray, size: float) -> np.ndarray:
        lo, hi = v.min(), v.max()
        span = (hi - lo) or 1.0
        usable = size * (1.0 - 2.0 * pad)
        return size * pad + (v - lo) / span * usable

    px = scale(xs, WIDTH)
    py = scale(ys, HEIGHT)

    dmax = max(int(deg.max()), 1)
    t = deg / dmax

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" '
        f'width="{WIDTH}" height="{HEIGHT}" role="img">',
        "<title>ODIN-Sim 生成的网络示例</title>",
        f'<rect width="{WIDTH}" height="{HEIGHT}" fill="{BG}"/>',
        f'<g stroke="{EDGE}" stroke-width="0.6" stroke-opacity="0.55" fill="none">',
    ]
    for a, b in zip(src.tolist(), dst.tolist()):
        if a < b:
            parts.append(
                f'<line x1="{px[a]:.1f}" y1="{py[a]:.1f}" '
                f'x2="{px[b]:.1f}" y2="{py[b]:.1f}"/>'
            )
    parts.append("</g>")

    for i in range(len(px)):
        r = 1.6 + 4.4 * t[i]
        c = NODE_HI if t[i] > 0.45 else NODE_LO
        parts.append(
            f'<circle cx="{px[i]:.1f}" cy="{py[i]:.1f}" r="{r:.2f}" '
            f'fill="{c}" fill-opacity="{0.55 + 0.45 * t[i]:.2f}"/>'
        )
    parts.append("</svg>")
    return "\n".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/assets/hero-network.svg")
    ap.add_argument("--n", type=int, default=320)
    ap.add_argument("--m", type=int, default=3)
    ap.add_argument("--seed", type=int, default=20261028)
    args = ap.parse_args()

    net = build_network(args.n, args.seed, "ba", args.m)
    src, dst = net["src"], net["dst"]
    pos = layout(src, dst, args.n)
    svg = render(pos, src, dst, net["deg"])

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(svg, encoding="utf-8")
    print(f"[hero] n={args.n} seed={args.seed} kind=ba "
          f"edges={net['n_edges']} hash={net['hash']}")
    print(f"[hero] -> {out}  ({len(svg)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

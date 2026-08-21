"""TradeGuard soak 长跑稳定性观测（新智基座维度4闭合项，补 perf_smoke 短时基线缺口）。

方法：复用 perf_smoke.py 的三层读路径负载画像（L1 health / L2 cases / L3 approvals，
读路径无脏数据风险），以 --duration 分钟持续循环打压；同时按采样间隔记录 web-api
容器 RSS 内存与 PG 连接侧指标，用于判定是否存在泄漏/漂移。

判定口径（写入报告）：
  - 全程错误率 0 为合格（偶发 >0 如实记录不粉饰）；
  - 内存末值相对首值增长 >20% 且单调不回落 → 标记疑似泄漏待查；
  - P95 末段相对首段漂移 >30% → 标记性能漂移待解释。

用法：
  python scripts/soak_run.py --duration 60              # 60 分钟长跑
  python scripts/soak_run.py --duration 60 --write      # 结束写 docs/reports/soak-report.md
"""
import argparse
import asyncio
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8200"
ROOT = Path(__file__).resolve().parent.parent
CONTAINER = "tradeguard-web-api-1"


@dataclass
class _PathStat:
    ok: int = 0
    err: int = 0
    lat_ms: list[float] = field(default_factory=list)

@dataclass
class _SoakResult:
    stats: dict[str, _PathStat]
    mem: list[tuple[float, float]]
    minutes: float


TARGETS = [
    ("L1 /api/health", "/api/health", False),
    ("L2 /api/cases", "/api/cases?page=1&size=20", True),
    ("L3 /api/approvals", "/api/approvals", True),
]


def _token() -> str:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("TG_API_TOKEN="):
                return line.split("=", 1)[1].strip()
    return ""


def _sample_mem_mb() -> float:
    """docker stats 单次采样容器内存（MB）；失败返回 -1 不阻断。"""
    try:
        out = subprocess.run(
            ["docker", "stats", CONTAINER, "--no-stream", "--format", "{{.MemUsage}}"],
            capture_output=True, text=True, timeout=15)
        mb = out.stdout.strip().split("MiB")[0].split("/")[-1].strip()
        return round(float(mb), 1)
    except Exception:
        return -1.0


async def main(args: argparse.Namespace) -> _SoakResult:
    tok = _token()
    headers = {"Authorization": f"Bearer {tok}"} if tok else {}
    deadline = time.perf_counter() + args.duration * 60
    stats = {lbl: _PathStat() for lbl, _, _ in TARGETS}
    mem_samples: list[tuple[float, float]] = []  # (分钟偏移, MB)
    last_sample = 0.0
    t0 = time.perf_counter()

    async with httpx.AsyncClient(base_url=BASE, headers=headers,
                                 timeout=30.0, trust_env=False) as client:
        while time.perf_counter() < deadline:
            for lbl, path, _auth in TARGETS:
                ts = time.perf_counter()
                try:
                    r = await client.get(path)
                    ms = (time.perf_counter() - ts) * 1000
                    st = stats[lbl]
                    if r.status_code >= 400:
                        st.err += 1
                    else:
                        st.ok += 1
                        st.lat_ms.append(ms)
                except Exception:
                    stats[lbl].err += 1
            elapsed_min = (time.perf_counter() - t0) / 60
            if elapsed_min - last_sample >= args.sample_min:
                mem_samples.append((round(elapsed_min, 1), _sample_mem_mb()))
                last_sample = elapsed_min
                total_ok = sum(s.ok for s in stats.values())
                total_err = sum(s.err for s in stats.values())
                print(f"[{elapsed_min:5.1f}min] ok={total_ok} err={total_err} "
                      f"mem={mem_samples[-1][1]}MB")
            await asyncio.sleep(args.pace)

    mem_samples.append((round((time.perf_counter() - t0) / 60, 1), _sample_mem_mb()))
    return _SoakResult(stats=stats, mem=mem_samples,
                       minutes=round((time.perf_counter() - t0) / 60, 1))


def _verdict(stats: dict[str, _PathStat], mem: list[tuple[float, float]]) -> list[str]:
    out = []
    total_ok = sum(s.ok for s in stats.values())
    total_err = sum(s.err for s in stats.values())
    out.append(f"错误率 {total_err}/{total_ok + total_err}"
               + ("（合格：0 错误）" if total_err == 0 else "（>0，如实记录待查）"))
    valid = [m for _, m in mem if m > 0]
    if len(valid) >= 2:
        growth = (valid[-1] - valid[0]) / max(valid[0], 1) * 100
        monotonic = all(b >= a for a, b in zip(valid, valid[1:]))
        if growth > 20 and monotonic:
            out.append(f"内存增长 {growth:.0f}% 且单调不回落 → 疑似泄漏待查")
        else:
            out.append(f"内存首 {valid[0]}MB → 末 {valid[-1]}MB（{growth:+.0f}%，无单调泄漏迹象）")
    return out


def _write_report(res: _SoakResult) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M")
    lines = [
        "# web-api soak 长跑稳定性报告（soak_run.py 自动生成）",
        "",
        f"- 采集时间：{ts}，持续 {res.minutes} 分钟，目标 {BASE}",
        "- 负载：perf_smoke 三层读路径画像循环打压（读路径无脏数据风险）",
        "- 判定：" + "；".join(_verdict(res.stats, res.mem)),
        "",
        "## 分路径汇总",
        "",
        "| 负载 | 成功 | 错误 | P50(ms) | P95(ms) |",
        "|---|---|---|---|---|",
    ]
    for lbl, s in res.stats.items():
        lats = sorted(s.lat_ms) or [float("nan")]
        p50 = lats[len(lats) // 2]
        p95 = lats[min(len(lats) - 1, int(len(lats) * 0.95))]
        lines.append(f"| {lbl} | {s.ok} | {s.err} | {p50:.1f} | {p95:.1f} |")
    lines += ["", "## 容器内存采样（web-api）", "", "| 分钟 | RSS(MB) |", "|---|---|"]
    lines += [f"| {t} | {m} |" for t, m in res.mem]
    out = ROOT / "docs" / "reports" / "soak-report.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"report written: {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=int, default=60, help="长跑分钟数（默认 60）")
    ap.add_argument("--sample-min", type=float, default=5.0, help="内存采样间隔分钟")
    ap.add_argument("--pace", type=float, default=0.05, help="轮次间隔秒（控压）")
    ap.add_argument("--write", action="store_true", help="写 docs/reports/soak-report.md")
    args = ap.parse_args()
    result = asyncio.run(main(args))
    for v in _verdict(result.stats, result.mem):
        print("判定:", v)
    if args.write:
        _write_report(result)

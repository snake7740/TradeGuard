"""TradeGuard web-api 并发冒烟压测（R-45 性能基线，可复现）。

三层负载画像（读路径，无脏数据风险）：
  L1 /api/health      框架/事件循环基线（免鉴权、无 DB）
  L2 /api/cases       单表分页读（asyncpg 连接池 + 鉴权中间件）
  L3 /api/approvals   join 聚合读（审批单 × 案件主表，FIFO 排序）

梯度：并发 10/50/100，每档固定请求数（默认 300），统计 P50/P95/P99/RPS/错误数。
用法：
  python scripts/perf_smoke.py                    # 打表到控制台
  python scripts/perf_smoke.py --requests 500 --write   # 追加写 docs/reports/perf-report.md
凭证：TG_E2E_TOKEN 环境变量，缺省回退读 .env 的 TG_API_TOKEN。
"""
import argparse
import asyncio
import os
import time
from pathlib import Path

import httpx

BASE = os.getenv("TG_BASE_URL", "http://127.0.0.1:8200")
ROOT = Path(__file__).resolve().parent.parent


def _token() -> str:
    tok = os.getenv("TG_E2E_TOKEN", "")
    if tok:
        return tok
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("TG_API_TOKEN="):
                return line.split("=", 1)[1].strip()
    return ""


TARGETS = [
    ("L1 /api/health（框架基线，无 DB）", "GET", "/api/health", None, False),
    ("L2 /api/cases（单表分页读）", "GET", "/api/cases?page=1&size=20", None, True),
    ("L3 /api/approvals（join 聚合读）", "GET", "/api/approvals", None, True),
]
GRADIENT = (10, 50, 100)


async def _run_one(client: httpx.AsyncClient, method: str, url: str,
                   json_body: dict | None) -> tuple[float, int]:
    t0 = time.perf_counter()
    r = await client.request(method, url, json=json_body)
    return (time.perf_counter() - t0) * 1000.0, r.status_code


async def _sweep(client: httpx.AsyncClient, label: str, method: str,
                 url: str, json_body: dict | None, auth: bool,
                 concurrency: int, total: int) -> dict:
    sem = asyncio.Semaphore(concurrency)
    lat: list[float] = []
    errors = 0
    t_start = time.perf_counter()

    async def worker():
        nonlocal errors
        async with sem:
            try:
                ms, code = await _run_one(client, method, url, json_body)
                if code >= 400:
                    errors += 1
                else:
                    lat.append(ms)
            except Exception:
                errors += 1

    await asyncio.gather(*(worker() for _ in range(total)))
    wall = time.perf_counter() - t_start
    lat.sort()

    def pct(p: float) -> float:
        return lat[min(len(lat) - 1, int(len(lat) * p))] if lat else float("nan")

    return {"label": label, "conc": concurrency, "total": total, "errors": errors,
            "p50": round(pct(0.50), 1), "p95": round(pct(0.95), 1),
            "p99": round(pct(0.99), 1),
            "rps": round(total / wall, 1), "wall_s": round(wall, 1)}


async def main(args: argparse.Namespace) -> list[dict]:
    tok = _token()
    headers = {"Authorization": f"Bearer {tok}"} if tok else {}
    results: list[dict] = []
    async with httpx.AsyncClient(base_url=BASE, headers=headers,
                                 timeout=30.0, trust_env=False) as client:
        # 预热：建立连接池/首查询计划
        for _, m, u, b, _a in TARGETS:
            try:
                await _run_one(client, m, u, b)
            except Exception:
                pass
        for label, method, url, body, _auth in TARGETS:
            for conc in GRADIENT:
                r = await _sweep(client, label, method, url, body, _auth,
                                 conc, args.requests)
                results.append(r)
                print(f"{label} | conc={conc:>3} total={args.requests} "
                      f"p50={r['p50']:>7}ms p95={r['p95']:>7}ms "
                      f"p99={r['p99']:>7}ms rps={r['rps']:>6} err={r['errors']}")
    return results


def _write_report(results: list[dict]) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M")
    lines = [
        "# web-api 并发冒烟基线（perf_smoke.py 自动生成）",
        "",
        f"- 采集时间：{ts}（目标 {BASE}，栈为 docker compose 全量 10 容器）",
        "- 方法：读路径三档负载 × 并发梯度 10/50/100，每档固定请求数，",
        "  httpx.AsyncClient 单进程；P95/P99 为成功请求延迟（错误单列）。",
        "- 定位：冒烟基线（演示库规模），非容量极限测试；用于回归对比",
        "  （改动后重跑，P95 漂移 >30% 即值得解释）。",
        "",
        "| 负载 | 并发 | 请求数 | P50(ms) | P95(ms) | P99(ms) | RPS | 错误 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(f"| {r['label']} | {r['conc']} | {r['total']} | {r['p50']} "
                     f"| {r['p95']} | {r['p99']} | {r['rps']} | {r['errors']} |")
    out = ROOT / "docs" / "reports" / "perf-report.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"report written: {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--requests", type=int, default=300, help="每档请求数（默认 300）")
    ap.add_argument("--write", action="store_true", help="写 docs/reports/perf-report.md")
    args = ap.parse_args()
    res = asyncio.run(main(args))
    if args.write:
        _write_report(res)

# -*- coding: utf-8 -*-
"""TradeGuard 一键启动 + 真实数据通路验证（04 §3 部署拓扑）

目标（用户指令）：无论当前服务是否存活、端口是否被占用，一个脚本把全栈拉起来；
端口被外部进程占用时先杀掉再启动；启动完成后用**真实请求与真实数据**验证
系统到底能不能用、数据能不能通——拒绝"容器都 Up 了就等于没问题"的幻觉。

启动顺序（含依赖）：
  0 预检：docker 引擎不可达时自动拉起 Docker Desktop 并等待就绪
  1 docker compose down（释放 compose 占用的端口，保留数据卷）
  2 清端口：外部进程占用 → taskkill；Docker 自身端口代理占用 →
    停掉归属容器释放（Windows Docker Desktop 的端口监听者是引擎进程，
    绝不能 taskkill，否则整个引擎消失）；合法归属者保留
  3 docker compose up -d [--build]（数据层→总线→治理→观测→自研服务）
  4 逐个服务真实探活（HTTP/TCP 探针，不是只看容器状态）
  5 数据就位（DB 为空 → data-generator 重灌 + nacos_register 播种阈值）
  6 Higress 路由重建（scripts/higress_routes.py，down -v 清卷后必备）
  7 AgentTeams 体检恢复（scripts/agentteams_doctor.py：拉起/唤醒/组网/MCP 桥）
  8 真实数据通路验证（C1~C9 核心：健康/网关/门户/阈值/立案→DISPOSED 自动闭环/
    审计凭证/span；X1 AgentTeams 协同扩展项）
  9 汇总报告：逐项 ✓/✗ + 证据；核心通路全绿才 exit 0

用法：
  .venv\\Scripts\\python.exe scripts\\start_all.py            # 复用已有镜像（快，重复体检用）
  .venv\\Scripts\\python.exe scripts\\start_all.py --build    # 强制重建镜像（代码新鲜度优先）
  .venv\\Scripts\\python.exe scripts\\start_all.py --no-agentteams  # 跳过 AgentTeams 独立栈
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import random
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
DOCKER_DESKTOP_EXE = r"C:\Program Files\Docker\Docker\Docker Desktop.exe"

# 宿主侧必用端口 → 用途（CLAUDE.md compose 端口 + AgentTeams 独立栈）
COMPOSE_PORTS = {
    5433: "postgres", 9876: "rocketmq-namesrv", 8848: "nacos", 9848: "nacos-grpc",
    8001: "higress-console", 8180: "higress-gateway", 3000: "as-studio",
    8101: "mcp-core", 8102: "mcp-external-mock", 8200: "web-api", 8300: "web-portal",
}
AGENTTEAMS_PORTS = {
    18001: "at-controller-console", 18080: "at-controller-gateway",
    18088: "at-controller-extra", 18888: "at-manager", 13000: "at-dashboard",
}
API = "http://localhost:8200"
GATEWAY = "http://localhost:8180"

for _s in (sys.stdout, sys.stderr):          # Windows GBK 控制台兼容
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1")
os.environ.setdefault("no_proxy", "localhost,127.0.0.1")

RESULTS: list[tuple[str, bool, str]] = []     # (检查名, 是否通过, 证据)


def sh(args, check=True, timeout=180, cwd=ROOT):
    r = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                       cwd=cwd, encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise RuntimeError(f"命令失败: {' '.join(args)}\n{r.stdout[-500:]}\n{r.stderr[-500:]}")
    return r.stdout.strip()


def note(phase: str, msg: str):
    print(f"[{phase}] {msg}", flush=True)


def record(name: str, ok: bool, evidence: str):
    RESULTS.append((name, ok, evidence))
    print(f"  {'✓' if ok else '✗'} {name}" + (f" —— {evidence}" if evidence else ""), flush=True)


# ---------------------------------------------------------------- Docker 引擎
def docker_alive() -> bool:
    r = subprocess.run(["docker", "info"], capture_output=True, text=True,
                       timeout=30, encoding="utf-8", errors="replace")
    return r.returncode == 0


def ensure_docker_engine(timeout: float = 180):
    """引擎不可达 → 拉起 Docker Desktop 并轮询等待（用户要求：不管现状如何都能启动）"""
    if docker_alive():
        note("预检", "docker 引擎可达")
        return
    note("预检", "docker 引擎不可达 → 启动 Docker Desktop……")
    flags = 0x00000008 if os.name == "nt" else 0   # DETACHED_PROCESS
    try:
        subprocess.Popen([DOCKER_DESKTOP_EXE], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, creationflags=flags)
    except FileNotFoundError:
        raise RuntimeError(f"未找到 Docker Desktop：{DOCKER_DESKTOP_EXE}")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if docker_alive():
            note("预检", "Docker Desktop 已就绪")
            return
        time.sleep(3)
    raise RuntimeError("Docker Desktop 启动超时（180s），请人工检查")


# ---------------------------------------------------------------- 端口清理
def listening_pids(port: int) -> set[int]:
    """netstat 解析：返回监听指定端口的 PID 集合（Windows）"""
    pids = set()
    try:
        out = sh(["netstat", "-ano"], check=False, timeout=30)
    except Exception:
        return pids
    for line in out.splitlines():
        m = re.match(r"\s*\S+\s+\S+:(\d+)\s+\S+\s+LISTENING\s+(\d+)", line)
        if m and int(m.group(1)) == port:
            pids.add(int(m.group(2)))
    return pids


def pid_image(pid: int) -> str:
    """tasklist 取进程映像名（小写）"""
    r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                       capture_output=True, text=True, timeout=15,
                       encoding="utf-8", errors="replace")
    first = (r.stdout.strip().splitlines() or [""])[0]
    return first.split(",")[0].strip('"').lower()


def docker_published_ports() -> dict[int, str]:
    """返回 {宿主端口: 容器名}——本系统容器（compose + agentteams 全家族）"""
    out = sh(["docker", "ps", "--format", "{{.Names}}\t{{.Ports}}"], check=False)
    mapping = {}
    for line in out.splitlines():
        if "\t" not in line:
            continue
        name, ports = line.split("\t", 1)
        if not name.startswith(("tradeguard-", "agentteams-")):
            continue
        # 兼容 0.0.0.0:port（compose）与 127.0.0.1:port（agentteams 回环发布）
        for m in re.finditer(r"(?:0\.0\.0\.0|127\.0\.0\.1|\[::\]):(\d+)->", ports):
            mapping[int(m.group(1))] = name
    return mapping


def _wait_port_free(port: int, timeout: float = 20) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not (listening_pids(port) - {0, 4}):
            return True
        time.sleep(2)
    return False


def free_ports(all_ports: dict[int, str], is_agentteams_phase: bool):
    """释放必用端口。铁律：Docker 引擎进程（com.docker.backend 等）是端口代理，
    绝不能 taskkill（杀之整个引擎消失）——改为停掉归属容器或等待其自然释放。"""
    owned = docker_published_ports()
    for port, who in all_ports.items():
        pids = listening_pids(port) - {0, 4}
        if not pids:
            continue
        acted = False          # 是否执行过清理动作（合法保留的端口不复查）
        for pid in pids:
            img = pid_image(pid)
            owner = owned.get(port, "")
            if "docker" in img:
                if owner.startswith("agentteams-worker-") or (
                        not is_agentteams_phase and owner):
                    # Worker 随机宿主端口撞线 / compose 阶段残留归属容器：
                    # 停容器释放（doctor 稍后会重新唤醒，compose 会重建）
                    note("端口", f":{port}({who}) 被容器 {owner} 占用 → docker stop 释放")
                    subprocess.run(["docker", "stop", "-t", "5", owner],
                                   capture_output=True, text=True, timeout=60,
                                   encoding="utf-8", errors="replace")
                    owned.pop(port, None)
                    acted = True
                elif owner:
                    note("端口", f":{port}({who}) 由合法容器 {owner} 占用，保留")
                else:
                    note("端口", f":{port}({who}) 被 Docker 进程({img})占用且无归属容器，"
                                 "等待自然释放……")
                    acted = True
                    if not _wait_port_free(port):
                        raise RuntimeError(f":{port} 被 Docker 残留占用无法释放，"
                                           "建议重启 Docker Desktop 后重试")
            else:
                note("端口", f":{port}({who}) 被外部进程 {img} PID={pid} 占用 → taskkill")
                subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
                acted = True
        if acted and not _wait_port_free(port, 15):
            raise RuntimeError(f":{port}({who}) 清理后仍被占用，无法继续")


# ---------------------------------------------------------------- 启动与探活
def wait_http(url: str, timeout: float, ok_statuses=(200,), need_json_key: str | None = None,
              allow_any_http: bool = False) -> tuple[bool, str]:
    """轮询 HTTP 探针；allow_any_http 用于 FastMCP（4xx 也算存活）"""
    import urllib.request, urllib.error
    deadline = time.monotonic() + timeout
    last = "无响应"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=4) as resp:
                body = resp.read(300)
                if resp.status in ok_statuses:
                    if need_json_key and need_json_key not in body.decode("utf-8", "replace"):
                        last = f"{resp.status} 缺 {need_json_key}"
                    else:
                        return True, f"{resp.status} {body[:40]!r}"
                else:
                    last = f"HTTP {resp.status}"
        except urllib.error.HTTPError as e:
            if allow_any_http:
                return True, f"HTTP {e.code}（有响应即存活）"
            last = f"HTTP {e.code}"
        except Exception as e:
            last = type(e).__name__
        time.sleep(2)
    return False, last


def _wait_cmd(args, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = subprocess.run(args, capture_output=True, text=True, cwd=ROOT,
                           encoding="utf-8", errors="replace")
        if r.returncode == 0:
            return True
        time.sleep(2)
    return False


def wait_healthy_services(timeout: float = 240):
    """对关键自研服务做真实探活（不是只看容器状态）"""
    ok = _wait_cmd(["docker", "compose", "exec", "-T", "postgres",
                    "pg_isready", "-U", "postgres", "-d", "tradeguard"], timeout)
    record("postgres 就绪", ok, "pg_isready" if ok else "超时")
    ok, ev = wait_http("http://localhost:8848/nacos/", timeout)
    record("nacos 就绪", ok, ev)
    for name, url in (("mcp-core", "http://localhost:8101/mcp/"),
                      ("mcp-external-mock", "http://localhost:8102/mcp/")):
        ok, ev = wait_http(url, timeout, allow_any_http=True)
        record(f"{name} 就绪", ok, ev)
    ok, ev = wait_http(f"{API}/api/health", timeout, need_json_key='"status":"UP"')
    record("web-api 就绪(/api/health UP)", ok, ev)
    ok, ev = wait_http("http://localhost:8300/", timeout)
    record("web-portal 就绪", ok, ev)
    ok, ev = wait_http("http://localhost:3000/", timeout, allow_any_http=True)
    record("as-studio 就绪", ok, ev)


# ---------------------------------------------------------------- 数据就位
def db_count(table: str) -> int:
    out = sh(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "postgres",
              "-d", "tradeguard", "-tAc", f"SELECT count(*) FROM {table};"], check=False)
    try:
        return int(out.strip())
    except Exception:
        return -1


def ensure_data():
    txs = db_count("transaction")
    accts = db_count("account")
    if txs > 0 and accts > 0:
        note("数据", f"已有数据（account={accts}, transaction={txs}），跳过重灌")
        record("业务数据就位", True, f"account={accts}, transaction={txs}")
        return
    note("数据", "DB 为空 → data-generator 重灌（约 2 分钟）+ nacos_register 播种阈值")
    sh(["docker", "compose", "run", "--rm", "data-generator"], timeout=600)
    subprocess.run([PY, str(ROOT / "scripts" / "nacos_register.py")],
                   capture_output=True, text=True, encoding="utf-8", errors="replace")
    txs2 = db_count("transaction")
    record("业务数据就位", txs2 > 0, f"重灌后 transaction={txs2}")


# ---------------------------------------------------------------- 数据通路验证
def _mock_outcomes(subject: str):
    rnd = random.Random(int(hashlib.sha256(("credit:" + subject).encode()).hexdigest()[:8], 16))
    score = rnd.randint(350, 850)
    band = "high" if score < 520 else ("mid" if score < 680 else "low")
    rnd = random.Random(int(hashlib.sha256(("sentiment:" + subject).encode()).hexdigest()[:8], 16))
    sent = rnd.random() < 0.3
    rnd = random.Random(int(hashlib.sha256(("complaint:" + subject).encode()).hexdigest()[:8], 16))
    return band, sent, rnd.randint(0, 2)


def find_clean_subject() -> str:
    """与 demo_playbook.find_d1_subject 同语义：恰好一条弱信号（credit-mid 9 分
    或 舆情 ≤13 分），无投诉/无高危征信 → score<40 且 amount=800<5000 恒走自动放行。
    每次现取新哈希，避免复跑累积交易金额越过 5000 线造成 flake。"""
    while True:
        h = uuid.uuid4().hex
        band, sent, complaints = _mock_outcomes(h)
        if complaints or band == "high" or (band == "mid" and sent):
            continue
        if band == "mid" or sent:
            return h


def headers() -> dict:
    h = {}
    if token := os.getenv("TG_API_TOKEN"):
        h["Authorization"] = f"Bearer {token}"
    return h


async def verify_data_path():
    """端到端：真实立案→EventWorker 自动聚合→自动放行 DISPOSED→审计+凭证+span 落库。
    这是'数据能不能通'的硬证据，复刻 demo_playbook D1（SC-01）。"""
    import asyncpg, httpx
    app = await asyncpg.connect("postgresql://tg_app:tg_app_dev@localhost:5433/tradeguard")
    async with httpx.AsyncClient(timeout=10, headers=headers()) as c:
        try:
            # C1 直连健康（四组件全 UP）
            r = await c.get(f"{API}/api/health"); j = r.json()
            record("C1 web-api 直连健康", r.status_code == 200 and j.get("status") == "UP",
                   f"components={j.get('components')}")
            # C2 网关转发健康（Higress→web-api）
            r = await c.get(f"{GATEWAY}/api/health")
            record("C2 Higress 网关转发 /api/health", r.status_code == 200, f"status={r.status_code}")
            # C3 网关读真实数据
            r = await c.get(f"{GATEWAY}/api/cases", params={"page": 1, "page_size": 1})
            ok = r.status_code == 200 and isinstance(r.json().get("total"), int)
            record("C3 经网关读取真实案件数据", ok, f"total={r.json().get('total')}")
            # C4 门户经网关的 /api（nginx→higress→web-api 真链路）
            r = await c.get("http://localhost:8300/api/health")
            record("C4 门户 :8300/api 经网关可达", r.status_code == 200, f"status={r.status_code}")
            # C5 Nacos 阈值经 web-api 快照可读（SC-06 接线证据）
            r = await c.get(f"{API}/api/config/thresholds")
            record("C5 Nacos 阈值快照可读", r.status_code == 200,
                   f"keys={len(r.json()) if r.status_code == 200 else 'n/a'}")
            # C6~C9 端到端立案→自动闭环（复刻 D1）
            subject = find_clean_subject()
            now = datetime.now(timezone.utc)
            await app.execute("INSERT INTO account (account_hash, risk_level, list_flag) "
                              "VALUES ($1,0,'none') ON CONFLICT (account_hash) DO NOTHING", subject)
            await app.execute("INSERT INTO transaction (tx_id, account_hash, amount, mcc, channel, ts) "
                              "VALUES ($1,$2,800.0,'5411','CNP',$3)",
                              f"sa-{uuid.uuid4().hex[:12]}", subject, now - timedelta(minutes=10))
            r = await c.post(f"{API}/api/alerts",
                             json={"subject_ref": subject, "source_type": "demo_script",
                                   "severity": "low"})
            okc = r.status_code == 202
            record("C6 POST /api/alerts 立案(202)", okc, f"status={r.status_code}")
            if not okc:
                return
            case_id = r.json()["case_id"]
            status, deadline = "?", time.monotonic() + 30
            while time.monotonic() < deadline:
                rr = await c.get(f"{API}/api/cases/{case_id}")
                if rr.status_code == 200:
                    status = rr.json().get("status")
                    if status == "DISPOSED":
                        break
                await asyncio.sleep(1)
            record("C7 EventWorker 自动推进至 DISPOSED", status == "DISPOSED", f"status={status}")
            ra = await c.get(f"{API}/api/audit/{case_id}")
            audit = ra.json().get("items", []) if ra.status_code == 200 else []
            has_gate = any("自动通道准入" in a.get("basis", "") for a in audit)
            rd = await c.get(f"{API}/api/cases/{case_id}/dispositions")
            disp = rd.json().get("items", []) if rd.status_code == 200 else []
            okd = len(disp) == 1 and disp[0].get("action") == "release" \
                and disp[0].get("status") == "executed"
            record("C8 审计链含自动通道准入 + 处置凭证 release/executed", has_gate and okd,
                   f"audit={len(audit)} 条, disposition={len(disp)} 条")
            rt = await c.get(f"{API}/api/observability/traces", params={"case_id": case_id})
            spans = rt.json().get("spans", []) if rt.status_code == 200 else []
            record("C9 技能 span 落库可回放(AA-SK-01)",
                   any(s.get("skill_id") == "AA-SK-01" for s in spans), f"spans={len(spans)}")
        finally:
            await app.close()


def verify_agentteams() -> bool:
    try:
        out = sh(["docker", "exec", "agentteams-controller", "agt", "get", "workers"], check=False)
        running = sum(1 for l in out.splitlines()[1:] if l.split() and "Running" in l)
        record("X1 AgentTeams Worker Running 数", running >= 4, f"running={running}/4")
        return running >= 4
    except Exception as e:
        record("X1 AgentTeams Worker Running 数", False, str(e)[:60])
        return False


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true", help="强制 docker compose build（代码新鲜度优先，较慢）")
    ap.add_argument("--no-agentteams", action="store_true", help="跳过 AgentTeams 独立栈体检")
    args = ap.parse_args()

    print("=" * 72)
    print("TradeGuard 一键启动 + 真实数据通路验证")
    print("=" * 72)

    try:
        # 0 预检
        ensure_docker_engine()
        sh(["docker", "compose", "version"], timeout=30)

        # 1 down（释放 compose 端口，保留数据卷）
        note("启动", "docker compose down（保留数据卷，释放端口）……")
        sh(["docker", "compose", "down", "--remove-orphans"], timeout=180)

        # 2 清端口
        note("启动", "清理被占用的端口……")
        free_ports(COMPOSE_PORTS, is_agentteams_phase=False)
        if not args.no_agentteams:
            free_ports(AGENTTEAMS_PORTS, is_agentteams_phase=True)

        # 3 up
        up_cmd = ["docker", "compose", "up", "-d"] + (["--build"] if args.build else [])
        note("启动", f"{' '.join(up_cmd)}（首次/缺镜像会自动构建）……")
        sh(up_cmd, timeout=900)

        # 4 探活
        note("探活", "逐个服务真实探活……")
        wait_healthy_services()

        # 5 数据
        ensure_data()

        # 6 Higress 路由
        note("路由", "重建 Higress 路由（scripts/higress_routes.py）……")
        r = subprocess.run([PY, str(ROOT / "scripts" / "higress_routes.py")],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        record("Higress 路由重建", r.returncode == 0, (r.stdout.strip().splitlines() or [""])[-1][:80])

        # 7 AgentTeams
        if not args.no_agentteams:
            note("协同", "AgentTeams 体检恢复（scripts/agentteams_doctor.py）……")
            r = subprocess.run([PY, str(ROOT / "scripts" / "agentteams_doctor.py")],
                               capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=600)
            record("AgentTeams 体检恢复", r.returncode == 0,
                   (r.stdout.strip().splitlines() or [""])[-1][:80])

        # 8 真实数据通路验证
        note("验证", "真实数据通路验证（端到端立案→自动闭环）……")
        asyncio.run(verify_data_path())
        if not args.no_agentteams:
            verify_agentteams()
    except RuntimeError as e:
        print(f"\n❌ 启动流程中断：{e}", flush=True)
        return 1

    # 9 汇总（核心 = 除 AgentTeams 独立栈扩展项之外的全部检查）
    print("\n" + "=" * 72)
    core = [x for x in RESULTS if not x[0].startswith(("X1", "AgentTeams"))]
    failed = [x for x in RESULTS if not x[1]]
    print(f"共 {len(RESULTS)} 项检查，通过 {len(RESULTS) - len(failed)}，失败 {len(failed)}")
    if failed:
        print("失败项：")
        for n, _, ev in failed:
            print(f"  ✗ {n} —— {ev}")
    ok_all = all(x[1] for x in core)
    print("\n结论：" + ("✅ 系统可用，核心数据通路全绿（真实立案→聚合→处置→审计闭环验证通过）"
                        if ok_all and not failed else
                        ("⚠️ 核心通路通过，但有扩展项未绿（见上）" if ok_all else
                         "❌ 核心数据通路未全绿，系统当前不可用（失败项见上，勿当作'没问题'）")))
    print("=" * 72)
    return 0 if (ok_all and not failed) else 1


if __name__ == "__main__":
    sys.exit(main())

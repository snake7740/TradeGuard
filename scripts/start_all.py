# -*- coding: utf-8 -*-
"""TradeGuard 一键启动 + 真实数据通路验证（04 §3 部署拓扑）

目标（用户指令）：无论当前服务是否存活、端口是否被占用，一个脚本把全栈拉起来；
端口被外部进程占用时先杀掉再启动；启动完成后用**真实请求与真实数据**验证
系统到底能不能用、数据能不能通——拒绝"容器都 Up 了就等于没问题"的幻觉。

启动顺序（含依赖）：
  0 预检：docker 引擎不可达时自动拉起 Docker Desktop 并等待就绪
  0.5 凭证自举（R-37）：.env 缺失/含 CHANGE_ME → 按 .env.example 格式自动生成
    随机强凭据（gitignore 排除，克隆后零手工配置即可启动）
  1 docker compose down（释放 compose 占用的端口，保留数据卷）
  2 清端口：外部进程占用 → taskkill（杀前二次复核映像名防误杀）；Docker 引擎
    家族（含 vpnkit/wsl 端口代理）占用 → 停掉归属容器释放（Windows Docker
    Desktop 的端口监听者是引擎进程，绝不能 taskkill）；合法归属者保留
  3 docker compose up -d [--build]（数据层→总线→治理→观测→自研服务）
  4 逐个服务真实探活（HTTP/TCP 探针，不是只看容器状态）
  5 数据就位（DB 为空 → 优先恢复仓库内 db/export 导出，缺导出回退
    data-generator 合成；nacos_register 播种元数据/阈值，恒定执行且校验退出码）
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
import base64
import gzip
import hashlib
import os
import random
import re
import secrets as _secrets
import shutil
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


# ---------------------------------------------------------------- 凭证自举（R-37）
ENV_FILE = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"
EXPORT_DUMP = ROOT / "db" / "export" / "tradeguard-data.sql.gz"


def _gen_env_values() -> dict[str, str]:
    """CHANGE_ME 占位符 → 随机强凭据（每次生成都不同，gitignore 排除不外泄）"""
    return {
        # 网关 API 令牌：64 位十六进制
        "TG_API_TOKEN": _secrets.token_hex(32),
        # Nacos 服务端互信头值
        "NACOS_AUTH_IDENTITY_VALUE": _secrets.token_hex(16),
        # Nacos AUTH_TOKEN 要求 base64（底层密钥 ≥32 字符）
        "NACOS_AUTH_TOKEN": base64.b64encode(_secrets.token_bytes(36)).decode(),
    }


def ensure_dotenv():
    """凭证自举（R-37 凭据治理）：
    - .env 不存在 → 以 .env.example 为模板生成，CHANGE_ME 占位替换为随机强凭据；
    - .env 存在但仍有 CHANGE_ME → 仅替换占位行，其余用户已填值原样保留；
    - 生成/补全后将键值 setdefault 进进程环境（宿主侧 headers()/子脚本可直接读）。
    克隆仓库后零手工配置即可启动；真实值只落在被 gitignore 的 .env 中。"""
    generated = False
    if not ENV_FILE.exists():
        if not ENV_EXAMPLE.exists():
            raise RuntimeError("缺少 .env.example 模板，无法生成 .env（R-37）")
        ENV_FILE.write_text(ENV_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
        generated = True
    text = ENV_FILE.read_text(encoding="utf-8")
    if "CHANGE_ME" in text:
        for key, val in _gen_env_values().items():
            text = re.sub(rf"^{re.escape(key)}=CHANGE_ME$", f"{key}={val}",
                          text, flags=re.MULTILINE)
        ENV_FILE.write_text(text, encoding="utf-8")
        note("凭证", ".env 占位符已替换为随机强凭据" + ("（首次生成）" if generated else ""))
    elif generated:
        note("凭证", ".env 已生成")
    # 装载进进程环境（setdefault：不覆盖用户显式导出的值）
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if v.strip() and v.strip() != "CHANGE_ME":
            os.environ.setdefault(k.strip(), v.strip())


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


# R-37：Docker 引擎家族映像名特征（com.docker.backend / vpnkit / wsl 均为端口
# 代理或引擎组件，taskkill 任何一个都会导致引擎失联——匹配即走"杀容器/等待"路径）
ENGINE_FAMILY = ("docker", "vpnkit", "wsl", "hyperkit")


def _is_engine_process(img: str) -> bool:
    return bool(img) and any(k in img for k in ENGINE_FAMILY)


def free_ports(all_ports: dict[int, str], is_agentteams_phase: bool):
    """释放必用端口。铁律：Docker 引擎家族进程（com.docker.backend/vpnkit/wsl 等）
    是端口代理，绝不能 taskkill（杀之整个引擎消失）——改为停掉归属容器或等待其
    自然释放。外部进程杀前二次复核映像名，防 PID 复用误杀（R-37）。"""
    owned = docker_published_ports()
    for port, who in all_ports.items():
        pids = listening_pids(port) - {0, 4}
        if not pids:
            continue
        acted = False          # 是否执行过清理动作（合法保留的端口不复查）
        for pid in pids:
            img = pid_image(pid)
            owner = owned.get(port, "")
            if _is_engine_process(img):
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
                # R-37：杀前二次复核——PID 可能已被复用为引擎/系统进程；
                # 复核结果落入引擎家族或读取失败 → 拒绝 taskkill，改等自然释放
                img_recheck = pid_image(pid)
                if _is_engine_process(img_recheck) or not img_recheck:
                    note("端口", f":{port}({who}) PID={pid} 复核为 {img_recheck or '不可读'}，"
                                 "判定引擎/未知进程禁止 taskkill，等待释放……")
                    acted = True
                    if not _wait_port_free(port):
                        raise RuntimeError(
                            f":{port}({who}) 被 {img_recheck or '未知进程'} 占用无法释放")
                    break
                note("端口", f":{port}({who}) 被外部进程 {img_recheck} PID={pid} 占用 → taskkill")
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


def restore_from_export() -> bool:
    """空库 → 从仓库内数据导出（db/export/tradeguard-data.sql.gz）恢复全量数据，
    使克隆/复制的项目无需重新合成即可完整启动。导出缺失返回 False，调用方回退
    data-generator 合成路径。恢复前先 TRUNCATE public 全表（幂等，超级用户执行）。"""
    if not EXPORT_DUMP.exists():
        note("数据", f"未见导出 {EXPORT_DUMP.relative_to(ROOT)}，走 data-generator 合成")
        return False
    note("数据", f"DB 为空 → 从 {EXPORT_DUMP.relative_to(ROOT)} 恢复（克隆即完整启动）")
    trunc = ("DO $$ DECLARE stmt text; BEGIN "
             "SELECT 'TRUNCATE ' || string_agg(format('%I', tablename), ', ') || ' CASCADE' "
             "INTO stmt FROM pg_tables WHERE schemaname='public'; "
             "IF stmt IS NOT NULL THEN EXECUTE stmt; END IF; END $$;")
    sh(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "postgres",
        "-d", "tradeguard", "-v", "ON_ERROR_STOP=1", "-c", trunc])
    # R-37 复审收口：--single-transaction 整体原子恢复——中途失败则全量回滚，
    # 杜绝"前半表已灌入、后半失败"的部分库被下次启动门禁误判为健康
    proc = subprocess.Popen(
        ["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "postgres",
         "-d", "tradeguard", "-v", "ON_ERROR_STOP=1", "--single-transaction", "-q"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=ROOT)
    with gzip.open(EXPORT_DUMP, "rb") as f:
        shutil.copyfileobj(f, proc.stdin)
    proc.stdin.close()
    _, err = proc.communicate(timeout=600)
    if proc.returncode != 0:
        raise RuntimeError("导出恢复失败：" + err.decode("utf-8", "replace")[-500:])
    return True


def ensure_data():
    txs = db_count("transaction")
    accts = db_count("account")
    if txs > 0 and accts > 0:
        note("数据", f"已有数据（account={accts}, transaction={txs}），跳过重灌")
        record("业务数据就位", True, f"account={accts}, transaction={txs}")
    else:
        note("数据", "DB 为空 → 优先恢复仓库导出，缺导出回退 data-generator 合成（约 2 分钟）")
        if not restore_from_export():
            sh(["docker", "compose", "run", "--rm", "data-generator"], timeout=600)
        txs2 = db_count("transaction")
        record("业务数据就位", txs2 > 0, f"恢复/重灌后 transaction={txs2}")
    # R-37：nacos_register 恒定执行——nacos 容器因凭据轮换/配置变更重建后其
    # 容器层配置即丢失，仅"空库时播种"会在换凭据后漏种；退出码显式校验（P3-4）
    r = subprocess.run([PY, str(ROOT / "scripts" / "nacos_register.py")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    tail = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
    record("Nacos 元数据/阈值播种", r.returncode == 0, (tail or ["无输出"])[-1][:80])
    if r.returncode != 0:
        raise RuntimeError("nacos_register 播种失败：\n"
                           + ((r.stdout or "") + (r.stderr or ""))[-800:])
    # 阶段3 R-43：刷新物化边表 mv_graph_edge（数据就位后，团伙发现 fn_fraud_ring 依赖）
    try:
        sh(["docker", "exec", "tradeguard-postgres-1", "psql", "-U", "postgres",
            "-d", "tradeguard", "-c", "REFRESH MATERIALIZED VIEW mv_graph_edge"],
           check=False, timeout=300)
        note("数据", "物化边表 mv_graph_edge 已刷新（团伙发现就绪）")
    except Exception:  # noqa: BLE001 —— 刷新失败不阻断主链路
        note("数据", "物化边表刷新失败（不影响主链路，团伙发现走 2 跳降级）")


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
                   f"keys={len(r.json().get('values', {})) if r.status_code == 200 else 'n/a'},"
                   f" source={r.json().get('source') if r.status_code == 200 else 'n/a'}")
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

        # 0.5 凭证自举（R-37）：compose up 读取 .env，必须先行；宿主侧验证
        # （headers()/C1~C9）也依赖其中的 TG_API_TOKEN
        ensure_dotenv()

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

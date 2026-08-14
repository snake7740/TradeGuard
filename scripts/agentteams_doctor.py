# -*- coding: utf-8 -*-
"""AgentTeams（TA-C-01）运行态体检与恢复脚本（04 §4，US-E1-02 运维配套）

作用：把独立部署在 Docker Desktop 上的 AgentTeams 栈恢复到「可协同」状态，
并把 TradeGuard 两个 MCP Server 注入全部 Worker 的 mcporter 工具桥。幂等可重跑。

为什么需要这个脚本（踩坑留痕）：
- AgentTeams 按官方脚本独立部署（非 compose 服务，见 scripts/install-agentteams.md）。
  Docker Desktop 重启后 controller 可能因 docker.sock bind-mount 瞬时失败而 Exited(127)，
  其 unless-stopped 策略不会自愈，需 docker start 拉起；
- Worker sleep/wake 会重建容器：①手工 `docker network connect` 接入的
  tradeguard_tradeguard-net 会丢（Worker 解析不到 mcp-core）；②Worker 容器层内的
  mcporter.json（/root/agentteams-fs 非挂载卷）也会丢。两者都由本脚本重新施加。

恢复步骤（逐步幂等）：
1. controller 不在跑 → docker start（内置 minio/tuwunel-matrix/higress/element 随 supervisord 起）；
2. `agt worker wake` 唤醒全部 Sleeping Worker（Manager 由 controller 自管）；
3. 为 controller/manager/全部 worker 连接 tradeguard_tradeguard-net（已连则跳过）；
4. 逐 Worker 注入 mcporter 配置（tg-core=mcp-core:8101，tg-external=mcp-external-mock:8102，
   均为 streamable-http 端点），`mcporter list` 校验 12+3 工具在场；
5. 汇总打印体检结果。

用法：python scripts/agentteams_doctor.py [--skip-mcp]
依赖：docker CLI 可与 Docker Desktop 守护进程通信；agt 在 agentteams-controller 容器内。
"""
import argparse
import json
import subprocess
import sys
import time

# Windows 控制台缺省 GBK：中文 print 与容器 UTF-8 输出统一兜底
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

WORKERS = ["aa-ag-02", "aa-ag-03", "aa-ag-04", "aa-ag-05"]
NET = "tradeguard_tradeguard-net"
MCP_SERVERS = {
    "tg-core": "http://mcp-core:8101/mcp/",
    "tg-external": "http://mcp-external-mock:8102/mcp/",
}
CONTROLLER = "agentteams-controller"
FS_BASE = "/root/agentteams-fs/agents"


def sh(args, check=True, timeout=120):
    # 容器输出为 UTF-8（含中文工具描述）；Windows 缺省 GBK 会解码失败，显式指定
    r = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                       encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(args)}\n{r.stdout}\n{r.stderr}")
    return r.stdout.strip()


def containers():
    """返回 {name: state}"""
    out = sh(["docker", "ps", "-a", "--format", "{{.Names}}\t{{.State}}"])
    return dict(line.split("\t") for line in out.splitlines() if "\t" in line)


def agt(*args, timeout=120):
    return sh(["docker", "exec", CONTROLLER, "agt", *args], timeout=timeout)


def ensure_controller(cs):
    state = cs.get(CONTROLLER)
    if state == "running":
        print("[doctor] controller 已在运行")
        return
    if state is None:
        print("[doctor] 未发现 agentteams-controller——请先按 scripts/install-agentteams.md 安装")
        sys.exit(1)
    print(f"[doctor] controller 状态 {state}，尝试 docker start……")
    sh(["docker", "start", CONTROLLER])
    for _ in range(30):
        time.sleep(2)
        if containers().get(CONTROLLER) == "running":
            print("[doctor] controller 已恢复运行")
            return
    print("[doctor] controller 拉起失败，请查 docker logs agentteams-controller")
    sys.exit(1)


def wake_workers():
    out = agt("get", "workers")
    sleeping = [w for line in out.splitlines()[1:]
                for w in [line.split()[0]] if line.split() and "Sleeping" in line]
    for w in WORKERS:
        if w in sleeping:
            print(f"[doctor] 唤醒 worker {w}……")
            agt("worker", "wake", "--name", w)
    # 等待全部 Running
    for _ in range(30):
        out = agt("get", "workers")
        rows = {l.split()[0]: l for l in out.splitlines()[1:] if l.split()}
        if all("Running" in rows.get(w, "") for w in WORKERS):
            print("[doctor] 4 个 Worker 全部 Running")
            return True
        time.sleep(2)
    print("[doctor] 仍有 Worker 未 Running：")
    print(agt("get", "workers"))
    return False


def connect_net():
    cs = containers()
    targets = [CONTROLLER, "agentteams-manager"] + [f"agentteams-worker-{w}" for w in WORKERS]
    for c in targets:
        if c not in cs or cs[c] != "running":
            continue
        nets = sh(["docker", "inspect", c, "--format",
                   "{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}"])
        if NET in nets:
            continue
        r = subprocess.run(["docker", "network", "connect", NET, c],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        print(f"[doctor] {c} 接入 {NET}：{'ok' if r.returncode == 0 else r.stderr.strip()}")


def inject_mcp():
    """注入 MCP 配置并校验。注意：python subprocess 直调 docker 不经 MSYS，路径不被改写。"""
    ok = True
    for w in WORKERS:
        c = f"agentteams-worker-{w}"
        for name, url in MCP_SERVERS.items():
            # config add 幂等：已存在时覆盖同名条目
            subprocess.run(
                ["docker", "exec", "-w", f"{FS_BASE}/{w}", c,
                 "mcporter", "config", "add", name, url],
                capture_output=True, text=True, encoding="utf-8", errors="replace")
        r = subprocess.run(
            ["docker", "exec", "-w", f"{FS_BASE}/{w}", c, "mcporter", "list", "--json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        try:
            data = json.loads(r.stdout)
            servers = {s["name"]: s for s in data.get("servers", [])}
            detail = "; ".join(f"{n}:{len(s.get('tools', []))} tools/{s.get('status')}"
                               for n, s in servers.items())
            healthy = (len(servers) == len(MCP_SERVERS)
                       and all(s.get("status") == "ok" for s in servers.values())
                       and len(servers.get("tg-core", {}).get("tools", [])) == 12
                       and len(servers.get("tg-external", {}).get("tools", [])) == 3)
        except Exception:
            detail = (r.stdout or r.stderr).strip()[:80]
            healthy = False
        ok = ok and healthy
        print(f"[doctor] worker {w} mcporter: {detail} -> {'OK' if healthy else 'FAIL'}")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-mcp", action="store_true", help="只做拉起/唤醒/组网，不注入 MCP 配置")
    args = ap.parse_args()

    cs = containers()
    ensure_controller(cs)
    # controller 内服务（matrix/higress/apiserver）需要启动时间
    time.sleep(5)
    wake_workers()
    connect_net()
    mcp_ok = True
    if not args.skip_mcp:
        mcp_ok = inject_mcp()

    print("=== AgentTeams 体检汇总 ===")
    print(agt("get", "managers"))
    print(agt("get", "workers"))
    if mcp_ok and not args.skip_mcp:
        print("RESULT: OK —— Manager/Worker 全 Running，MCP 工具桥（tg-core 12 + tg-external 3）已注入")
        return 0
    if args.skip_mcp:
        print("RESULT: OK（未注入 MCP，--skip-mcp）")
        return 0
    print("RESULT: DEGRADED —— MCP 注入或校验未全绿，见上方明细")
    return 1


if __name__ == "__main__":
    sys.exit(main())

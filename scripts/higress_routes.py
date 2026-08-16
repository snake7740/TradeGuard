# -*- coding: utf-8 -*-
"""Higress 网关路由重建脚本（TA-C-04，04 §5）

作用：把「web-api / mcp-core / mcp-external-mock」三个自研服务注册为 Higress
的 dns 型服务源（McpBridge），并建立 /api → web-api 的 Ingress 路由，使门户
全部业务流量真实经过 AI 网关（web-portal nginx 已指向 higress:8080）。

为什么要这个脚本：Higress all-in-one 的配置存于容器内 /data 文件仓（卷
higress-data）。`docker compose down -v` 会清空该卷，网关退回空路由（/api 503、
控制台 API 401）。本脚本幂等地把路由配置重新写入并触发网关重载。

关键实测口径（2.2.3，踩坑留痕）：
- 数据面 HTTP 端口=容器内 8080（GATEWAY_HTTP_PORT 缺省），HTTPS=8443；容器内
  无 80 监听，旧 compose 映射 8180:80 恒不通，已改 8180:8080。
- McpBridge `type: dns` 的 domain 必须是带点域名（单标签如 "web-api" 被拒
  "invalid domain format"），故 compose 为三服务加了 `*.tg.local` 网络别名，
  由 Docker 内嵌 DNS 解析。
- 直接改写 /data 文件不总能触发 controller watch，最可靠是写入后 restart 网关
  让其启动时全量加载（controller 启动读取 /data）。
- 控制台 REST API 需登录（401 Login required），本脚本不走控制台 API，直接写
  文件仓 + 重启，规避首次初始化密码问题。

用法：python scripts/higress_routes.py [--addr http://localhost:8180]
依赖：docker compose 可用、higress/web-api 容器在 tradeguard-net 上。
"""
import argparse
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

# Windows 控制台缺省 GBK：中文 print 与容器 UTF-8 输出统一兜底
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

COMPOSE = Path(__file__).resolve().parent.parent

MCPBRIDGE_YAML = """apiVersion: networking.higress.io/v1
kind: McpBridge
metadata:
  name: default
  namespace: higress-system
spec:
  registries:
  - domain: 127.0.0.1:8001
    name: higress-console
    port: 80
    type: static
  - domain: web-api.tg.local
    name: web-api
    port: 8000
    type: dns
  - domain: mcp-core.tg.local
    name: mcp-core
    port: 8101
    type: dns
  - domain: mcp-external-mock.tg.local
    name: mcp-external-mock
    port: 8102
    type: dns
status: {}
"""

INGRESS_YAML = """apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: tradeguard-api
  namespace: higress-system
  annotations:
    higress.io/destination: web-api.dns:8000
  labels:
    higress.io/domain_higress-default-domain: "true"
    higress.io/resource-definer: higress
spec:
  ingressClassName: higress
  rules:
  - http:
      paths:
      - backend:
          resource:
            apiGroup: networking.higress.io
            kind: McpBridge
            name: default
        path: /api
        pathType: Prefix
status:
  loadBalancer: {}
"""


def sh(args, check=True, **kw):
    r = subprocess.run(args, capture_output=True, text=True, cwd=COMPOSE,
                       encoding="utf-8", errors="replace", **kw)
    if check and r.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(args)}\n{r.stderr}")
    return r.stdout.strip()


def container_id(service):
    return sh(["docker", "compose", "ps", "-q", service])


def write_into(cid, content, dest):
    """docker cp 写入容器文件仓（经临时文件，避免 shell 转义）"""
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, newline="\n") as f:
        f.write(content)
        tmp = f.name
    try:
        sh(["docker", "cp", tmp, f"{cid}:{dest}"])
    finally:
        Path(tmp).unlink(missing_ok=True)


def _api_token() -> str:
    """R-37：从进程环境/仓库根 .env（gitignore，start_all 自动生成）装载 TG_API_TOKEN。
    web-api bearer 守卫（US-E7-01）生效后，网关探活 /api/cases 必须携令牌，
    否则 401 造成"路由重建假失败"。"""
    token = os.getenv("TG_API_TOKEN", "")
    if token and token != "CHANGE_ME":
        return token
    try:
        for line in (COMPOSE / ".env").read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("TG_API_TOKEN="):
                val = line.split("=", 1)[1].strip()
                if val and val != "CHANGE_ME":
                    return val
    except OSError:
        pass
    return ""


def probe(url, timeout=4, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()[:80]
    except Exception as e:  # noqa: BLE001
        return None, str(e)[:80]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--addr", default="http://localhost:8180", help="网关宿主入口")
    ap.add_argument("--no-restart", action="store_true", help="只写配置不重启（调试用）")
    args = ap.parse_args()

    cid = container_id("higress")
    if not cid:
        print("[higress] 容器未运行，先 docker compose up -d higress"); return 1
    print(f"[higress] 容器 {cid[:12]}")
    tok = _api_token()
    hdrs = {"Authorization": f"Bearer {tok}"} if tok else {}
    if not tok:
        print("[higress] 警告：未取到 TG_API_TOKEN，带鉴权端点探活将 401（R-37）")

    # 前置：确认 *.tg.local 别名已由 compose 生效（dns 型服务源解析依赖）
    aliases = sh(["docker", "compose", "exec", "-T", "higress", "sh", "-c",
                  "getent hosts web-api.tg.local || true"], check=False)
    if "web-api.tg.local" not in aliases:
        print("[higress] 警告：web-api.tg.local 不可解析——请确认 docker-compose.yml "
              "已为 web-api/mcp-core/mcp-external-mock 配置 *.tg.local 网络别名并 up -d 生效")

    write_into(cid, MCPBRIDGE_YAML, "/data/mcpbridges/default.yaml")
    write_into(cid, INGRESS_YAML, "/data/ingresses/tradeguard-api.yaml")
    print("[higress] McpBridge + Ingress 已写入 /data")

    if not args.no_restart:
        print("[higress] 重启网关以全量加载路由……")
        sh(["docker", "compose", "restart", "higress"])
        # 等待网关数据面就绪
        ok = False
        for _ in range(40):
            code, _ = probe(args.addr + "/api/health", headers=hdrs)
            if code == 200:
                ok = True; break
            time.sleep(3)
        if not ok:
            print("[higress] 重启后 /api/health 未达 200，请查 docker compose logs higress")
            return 1

    code, body = probe(args.addr + "/api/health", headers=hdrs)
    print(f"[higress] {args.addr}/api/health -> {code} {body!r}")
    code2, body2 = probe(args.addr + "/api/cases?page=1&page_size=1", headers=hdrs)
    print(f"[higress] {args.addr}/api/cases -> {code2} {body2!r}")
    if code == 200 and code2 == 200:
        print("RESULT: OK —— 网关已承载 web-api 业务流量（/api 路由生效）")
        return 0
    print("RESULT: FAILED"); return 1


if __name__ == "__main__":
    sys.exit(main())

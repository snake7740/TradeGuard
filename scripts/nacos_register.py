"""Nacos 注册脚本（US-E1-03）：MCP/Skill 元数据 + BA-BR 阈值 + 服务实例

Nacos v3 admin Open API（v1/v2 端点已在 v3.2 镜像移除）：
- 配置：POST /nacos/v3/admin/cs/config（dataId/groupName/namespaceId/content）
- 实例：POST /nacos/v3/admin/ns/instance（best-effort，失败不阻断）
鉴权：serverIdentity 服务端互信头（compose NACOS_AUTH_IDENTITY_KEY/VALUE 同源）。

鉴权凭据来源（R-37）：进程环境变量 → 仓库根 .env（gitignore，start_all 自动生成），
不再有代码内缺省值（原缺省即公开仓库可见凭据）；缺失时报错退出。

用法：python scripts/nacos_register.py [--addr http://localhost:8848]
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

GROUP = "TRADEGUARD"
IDENTITY_KEY: str | None = None
IDENTITY_VALUE: str | None = None


def load_identity() -> tuple[str, str]:
    """互信头装载：进程 env → 仓库根 .env → 报错退出（R-37：无代码内缺省凭据）"""
    key = os.getenv("NACOS_AUTH_IDENTITY_KEY")
    val = os.getenv("NACOS_AUTH_IDENTITY_VALUE")
    if not val or val == "CHANGE_ME":
        env_file = Path(__file__).resolve().parent.parent / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("NACOS_AUTH_IDENTITY_KEY="):
                    key = line.split("=", 1)[1].strip()
                elif line.startswith("NACOS_AUTH_IDENTITY_VALUE="):
                    val = line.split("=", 1)[1].strip()
    if not val or val == "CHANGE_ME":
        print("[config] 错误：NACOS_AUTH_IDENTITY_VALUE 缺失（进程环境或仓库根 .env 均未配置）。\n"
              "  克隆后请先运行 scripts/start_all.py 自动生成 .env 凭据（R-37）。", file=sys.stderr)
        sys.exit(1)
    return key or "serverIdentity", val

# ---------- 注册内容（元数据权威源：docs/02 §4/§5，skills/*.md） ----------

MCP_REGISTRY = {
    "registry": "tradeguard-mcp",
    "servers": [
        {"id": "AA-MCP-01", "name": "交易风控业务库 MCP", "endpoint": "http://mcp-core:8101/mcp",
         "host_port": "mcp-core:8101", "transport": "streamable-http",
         # 与在码 12 工具同源（mcp-core/server.py @mcp.tool，编号 API-M-01~12 见 docs/07 §5.2）
         "tools": ["query_transactions", "query_related_graph", "query_case_signals",
                   "query_disposition_result", "execute_disposition", "create_approval_request",
                   "record_case_evidence", "apply_risk_bonus", "record_case_signals",
                   "submit_kb_application", "record_agent_memory", "query_audit_trail"],
         "policy": "写工具受审批门控（E-DISP-AUTH）；全部调用落审计（BA-BR-09）"},
        {"id": "AA-MCP-02", "name": "外部数据服务 MCP（防腐层）", "endpoint": "http://mcp-external-mock:8102/mcp",
         "host_port": "mcp-external-mock:8102", "transport": "streamable-http",
         "tools": ["query_credit", "query_complaint", "query_sentiment"],
         "policy": "query_reason 事由强制（BA-BR-10）；单源超时 5s 重试 2 次降级"},
    ],
}

SKILL_REGISTRY = {
    "registry": "tradeguard-skills",
    "skills": [
        {"id": "AA-SK-01", "name": "signal-aggregation", "agent": "AA-AG-02",
         "definition": "skills/AA-SK-01-signal-aggregation.md",
         "kernel": "services/web-api/app/skills/aggregation.py"},
        {"id": "AA-SK-02", "name": "fraud-investigation", "agent": "AA-AG-03",
         "definition": "skills/AA-SK-02-fraud-investigation.md",
         "kernel": "services/web-api/app/skills/investigation.py"},
        {"id": "AA-SK-03", "name": "disposition-execution", "agent": "AA-AG-04",
         "definition": "skills/AA-SK-03-disposition-execution.md",
         "kernel": "services/mcp-core/server.py"},
        {"id": "AA-SK-04", "name": "compliance-audit", "agent": "AA-AG-05",
         "definition": "skills/AA-SK-04-compliance-audit.md",
         "kernel": "services/web-api/app/skills/verification.py"},
        {"id": "AA-SK-05", "name": "knowledge-sedimentation", "agent": "AA-AG-05",
         "definition": "skills/AA-SK-05-knowledge-sedimentation.md",
         "kernel": "services/web-api/app/skills/knowledge.py",
         "kernel_note": "复盘入库申请入口：verification.py VerificationService._retrospective（无独立 retrospective.py）"},
    ],
}

# BA-BR 阈值（与 db/init/01-schema.sql 种子同源；正式值经此处下发，SC-06）
# 闭环修复 D4：补齐 br-05/br-08 键——此前缺失导致聚合/核验侧只能回落代码常量，
# 与 PUT /api/config/thresholds 的热更新键集不一致（SC-06 宣称不实）。
THRESHOLDS = {
    "br-01-auto-block-score": "70",
    "br-01-mid-review-score": "40",
    "br-01-auto-amount-limit": "5000",
    "br-05-window-days": "7",
    "br-05-case-count": "3",
    "br-06-fraud-link-bonus": "30",
    "br-08-verification-timeout-min": "10",
    "br-13-approval-timeout-min": "30",
    "br-14-velocity-1h-count": "10",
    "br-14-velocity-24h-count": "50",
    "br-14-velocity-bonus": "30",
}


def fetch_config(addr: str, data_id: str) -> dict:
    """读现值（D4）：仅缺键补默认，防重跑覆盖 PUT /api/config/thresholds 改过的值。
    注意执行顺序——本脚本只应在首次部署/补键时运行；演示改阈值一律走 PUT 端点。

    R-37 失败语义修正：网络/鉴权异常 → 抛错中止（此前吞异常返回空 dict，
    重跑时 {**THRESHOLDS, **existing} 会以缺省集整体覆盖 PUT 改过的现值）；
    配置确实不存在（HTTP 404 或 code!=0：首次部署/容器重建后配置层清空）
    → 返回 {} 属正常播种路径。"""
    qs = urllib.parse.urlencode({"dataId": data_id, "groupName": GROUP,
                                 "namespaceId": "public"})
    req = urllib.request.Request(f"{addr}/nacos/v3/admin/cs/config?{qs}",
                                 headers={IDENTITY_KEY: IDENTITY_VALUE})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {}   # 配置不存在（首次部署 / 容器重建），允许全量播种
        raise RuntimeError(
            f"Nacos 读取 {data_id} 失败：HTTP {e.code}（{e.read().decode('utf-8', 'replace')[:120]}）"
            "——拒绝继续写回，防止鉴权/权限异常被误判为'空配置'而覆盖现值（R-37）") from e
    except Exception as e:
        raise RuntimeError(
            f"Nacos 读取 {data_id} 失败（{type(e).__name__}: {str(e)[:120]}）——"
            "拒绝继续写回，防止网络异常被误判为'空配置'而覆盖现值（R-37）") from e
    if body.get("code") != 0:
        return {}   # 配置不存在（首次部署），允许全量播种
    try:
        content = json.loads(body["data"]["content"])
    except (KeyError, TypeError, json.JSONDecodeError):
        return {}   # content 缺失/非 JSON：按空配置处理（后续写回会重建合法文档）
    if not isinstance(content, dict):
        raise RuntimeError(f"Nacos {data_id} content 非对象（{type(content).__name__}），"
                           "拒绝合并写回以防破坏现值（R-37）")
    return content

SERVICE_INSTANCES = [
    {"serviceName": "web-api", "ip": "web-api", "port": 8000},
    {"serviceName": "mcp-core", "ip": "mcp-core", "port": 8101},
    {"serviceName": "mcp-external-mock", "ip": "mcp-external-mock", "port": 8102},
]


def _call(addr: str, path: str, params: dict) -> tuple[bool, str]:
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(f"{addr}{path}", data=data,
                                 headers={IDENTITY_KEY: IDENTITY_VALUE})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read().decode()
            ok = '"code":0' in body or body.strip() == "true"
            return ok, body[:200]
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.read().decode()[:150]}"
    except Exception as e:
        return False, str(e)[:150]


def publish_config(addr: str, data_id: str, content: dict) -> bool:
    ok, msg = _call(addr, "/nacos/v3/admin/cs/config",
                    {"dataId": data_id, "groupName": GROUP,
                     "namespaceId": "public", "type": "json",
                     "content": json.dumps(content, ensure_ascii=False, indent=2)})
    print(f"[config] {data_id}: {'OK' if ok else 'FAIL'} {msg if not ok else ''}")
    return ok


def register_instance(addr: str, inst: dict) -> bool:
    ok, msg = _call(addr, "/nacos/v3/admin/ns/instance",
                    inst | {"groupName": GROUP, "namespaceId": "public", "healthy": "true"})
    print(f"[instance] {inst['serviceName']}: {'OK' if ok else 'FAIL'} {msg if not ok else ''}")
    return ok


def main() -> int:
    global IDENTITY_KEY, IDENTITY_VALUE
    ap = argparse.ArgumentParser()
    ap.add_argument("--addr", default="http://localhost:8848")
    args = ap.parse_args()
    IDENTITY_KEY, IDENTITY_VALUE = load_identity()   # R-37：凭据缺失直接退出

    # 阈值发放前读现值：仅缺键补默认，已有键（含 PUT 改过的）一律保留（D4）
    try:
        existing = fetch_config(args.addr, "ba-br-thresholds")
    except RuntimeError as e:
        print(f"[config] 中止：{e}", file=sys.stderr)
        return 1
    # R-37：只接纳标量现值（dict/list 等结构值 str() 后会污染阈值文档）
    scalar_existing = {k: v for k, v in existing.items()
                       if isinstance(v, (str, int, float, bool))}
    skipped = sorted(set(existing) - set(scalar_existing))
    if skipped:
        print(f"[config] 忽略非标量现值键: {', '.join(skipped)}（R-37）")
    thresholds = {**THRESHOLDS, **{k: str(v) for k, v in scalar_existing.items()}}
    filled = sorted(set(thresholds) - set(scalar_existing))
    if filled:
        print(f"[config] ba-br-thresholds 补键: {', '.join(filled)}")
    elif scalar_existing:
        print("[config] ba-br-thresholds 现值完整，不覆盖任何已有键")

    ok = all([
        publish_config(args.addr, "tradeguard-mcp-registry", MCP_REGISTRY),
        publish_config(args.addr, "tradeguard-skill-registry", SKILL_REGISTRY),
        publish_config(args.addr, "ba-br-thresholds", thresholds),
    ])
    for inst in SERVICE_INSTANCES:  # best-effort：实例注册失败不阻断元数据注册验收
        register_instance(args.addr, inst)
    print("RESULT:", "OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

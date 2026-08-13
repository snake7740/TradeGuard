"""Nacos 注册脚本（US-E1-03）：MCP/Skill 元数据 + BA-BR 阈值 + 服务实例

Nacos v3 admin Open API（v1/v2 端点已在 v3.2 镜像移除）：
- 配置：POST /nacos/v3/admin/cs/config（dataId/groupName/namespaceId/content）
- 实例：POST /nacos/v3/admin/ns/instance（best-effort，失败不阻断）
鉴权：serverIdentity 服务端互信头（compose NACOS_AUTH_IDENTITY_KEY/VALUE 同源）。

用法：python scripts/nacos_register.py [--addr http://localhost:8848]
"""
import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

IDENTITY_KEY = "serverIdentity"
IDENTITY_VALUE = "tradeguard_dev"
GROUP = "TRADEGUARD"

# ---------- 注册内容（元数据权威源：docs/02 §4/§5，skills/*.md） ----------

MCP_REGISTRY = {
    "registry": "tradeguard-mcp",
    "servers": [
        {"id": "AA-MCP-01", "name": "交易风控业务库 MCP", "endpoint": "http://mcp-core:8101/mcp",
         "host_port": "mcp-core:8101", "transport": "streamable-http",
         "tools": ["query_transactions", "query_related_graph", "execute_disposition",
                   "query_disposition_result", "submit_kb_application", "query_audit_trail",
                   "query_case_signals"],
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
         "kernel": "services/web-api/app/skills/retrospective.py"},
    ],
}

# BA-BR 阈值（与 db/init/01-schema.sql 种子同源；正式值经此处下发，SC-06）
THRESHOLDS = {
    "br-01-auto-block-score": "70",
    "br-01-mid-review-score": "40",
    "br-01-auto-amount-limit": "5000",
    "br-13-approval-timeout-min": "30",
    "br-14-velocity-1h-count": "10",
    "br-14-velocity-24h-count": "50",
    "br-14-velocity-bonus": "30",
}

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
    ap = argparse.ArgumentParser()
    ap.add_argument("--addr", default="http://localhost:8848")
    args = ap.parse_args()

    ok = all([
        publish_config(args.addr, "tradeguard-mcp-registry", MCP_REGISTRY),
        publish_config(args.addr, "tradeguard-skill-registry", SKILL_REGISTRY),
        publish_config(args.addr, "ba-br-thresholds", THRESHOLDS),
    ])
    for inst in SERVICE_INSTANCES:  # best-effort：实例注册失败不阻断元数据注册验收
        register_instance(args.addr, inst)
    print("RESULT:", "OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""US-E7-05 剧本化演示（04 §8）：三个演示事件 = 测试回放

  D1 低风险自动放行（SC-01 回放）：告警受理 → 聚合裁决 auto_release → DISPOSED
  D2 调查后冻结 + 人工审批（SC-02 全链回放）：聚合转调查 → AA-SK-02 调查取证 →
     处置门控建单 → 人工批准 → 冻结执行 → 核验归档（SC-08 留痕回放）
  D3 误报申诉回滚（SC-03/07 变体回放）：冻结执行后下游回报异常（tg_app 篡改
     disposition_record.status='failed' 模拟）→ 核验不一致 → 反向处置回滚 →
     P0 升级人工 → 复核 dismiss 申诉成立归档

人机边界（02 §3.3）：人类动作一律经真实 HTTP API（localhost:8200，门户同路径）；
Agent 侧处置提交经确定性内核直调（等价 API-M MCP 调用，与 test_disposition
同一调用序列——"演示=测试回放"的落地形态）。全部数据合成（03 §7）。

用法：.venv\\Scripts\\python.exe scripts\\demo_playbook.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
import httpx

BASE = os.getenv("TG_API_BASE", "http://localhost:8200")
WEB_DSN = os.getenv("TG_PLAYBOOK_DSN", "postgresql://tg_web:tg_web_dev@localhost:5433/tradeguard")
APP_DSN = os.getenv("TG_PLAYBOOK_APP_DSN", "postgresql://tg_app:tg_app_dev@localhost:5433/tradeguard")
os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1,mcp-core")
os.environ.setdefault("no_proxy", "localhost,127.0.0.1,mcp-core")
MCP_CORE_URL = os.getenv("TG_PLAYBOOK_MCP_CORE", "http://127.0.0.1:8101/mcp/")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services", "web-api"))
if hasattr(sys.stdout, "reconfigure"):      # Windows GBK 控制台兼容（✓ 等符号）
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PASS = 0


def step(scenario: str, no: int, desc: str, ok: bool, detail: str = ""):
    """每步断言的可视化留痕（决赛现场口播依据）"""
    global PASS
    mark = "✓" if ok else "✗"
    print(f"  [{scenario}][{no:02d}] {mark} {desc}" + (f" —— {detail}" if detail else ""))
    if not ok:
        raise AssertionError(f"{scenario} 步骤 {no} 失败：{desc} {detail}")
    PASS += 1


def headers(operator: str | None = None) -> dict:
    h = {}
    if token := os.getenv("TG_API_TOKEN"):
        h["Authorization"] = f"Bearer {token}"
    if operator:
        h["X-Operator"] = operator
    return h


async def seed_subject(app, list_flag: str, txs: list[tuple[float, int]],
                       fixed_hash: str | None = None) -> str:
    """播种演示主体（tg_app 为 account/transaction 写角色，02-roles.sql）。
    fixed_hash 可选：固定主体哈希使外部 mock 信号确定（complaint 命中推高
    评分至审批线上方），保障剧本可重复回放；账户 INSERT 幂等（复跑友好）。"""
    subject = fixed_hash or uuid.uuid4().hex
    await app.execute(
        "INSERT INTO account (account_hash, risk_level, list_flag) VALUES ($1, $2, $3) "
        "ON CONFLICT (account_hash) DO NOTHING",
        subject, 3 if list_flag != "none" else 0, list_flag)
    now = datetime.now(timezone.utc)
    for i, (amount, minutes_ago) in enumerate(txs):
        await app.execute(
            """INSERT INTO transaction (tx_id, account_hash, amount, mcc, channel, ts)
               VALUES ($1, $2, $3, '5411', 'CNP', $4)""",
            f"demo-{uuid.uuid4().hex[:12]}", subject, amount, now - timedelta(minutes=minutes_ago))
    return subject


async def register_case(client: httpx.AsyncClient, scenario: str, subject: str) -> str:
    r = await client.post(f"{BASE}/api/alerts", headers=headers(),
                          json={"subject_ref": subject, "source_type": "demo_script",
                                "severity": 50})
    step(scenario, 1, "POST /api/alerts 告警受理立案", r.status_code == 201,
         f"status={r.status_code}")
    return r.json()["case_id"]


async def traces_of(client: httpx.AsyncClient, case_id: str) -> list[dict]:
    r = await client.get(f"{BASE}/api/observability/traces", params={"case_id": case_id},
                         headers=headers())
    return r.json().get("spans", []) if r.status_code == 200 else []


async def d1_low_risk_auto_release(client, app):
    """剧本 D1：低风险小额自动放行（SC-01 回放）"""
    print("\n▶ 剧本 D1 低风险自动放行（SC-01 回放）")
    subject = await seed_subject(app, "none", [(800.0, 10)])          # 单笔 800 元小额
    case_id = await register_case(client, "D1", subject)

    r = await client.post(f"{BASE}/api/cases/{case_id}/aggregate", headers=headers())
    out = r.json()
    step("D1", 2, "AA-SK-01 聚合裁决=auto_release 且风险分<40",
         r.status_code == 200 and out.get("route") == "auto_release"
         and out.get("risk_score", 100) < 40,
         f"route={out.get('route')},risk_score={out.get('risk_score')}")

    r = await client.get(f"{BASE}/api/cases/{case_id}", headers=headers())
    step("D1", 3, "案件状态流转至 DISPOSED（自动放行完成）",
         r.json().get("status") == "DISPOSED", f"status={r.json().get('status')}")

    r = await client.get(f"{BASE}/api/audit/{case_id}", headers=headers())
    actions = [a["action"] for a in r.json().get("items", [])]
    step("D1", 4, "审计链含 case.register 与 disposition.submit（BA-BR-09）",
         "case.register" in actions and "disposition.submit" in actions, str(actions))

    spans = await traces_of(client, case_id)
    step("D1", 5, "技能执行 span 可回放（US-E7-04，AA-SK-01）",
         any(s["skill_id"] == "AA-SK-01" for s in spans), f"spans={len(spans)}")
    return case_id


async def _disposition_service(web_pool):
    """Agent 侧处置内核装配（与 tests/conftest.py disposition 夹具同构）"""
    from app.repositories import CaseRepository
    from app.skills.disposition import DispositionService
    from app.skills.mcp_adapters import CoreClient

    class _NullPub:
        async def publish(self, *a, **kw):
            pass

    repo = CaseRepository(web_pool, _NullPub())
    return DispositionService(pool=web_pool, cases=repo,
                              core=CoreClient(MCP_CORE_URL), pub=_NullPub())


async def d2_investigate_freeze_approve(client, app, web):
    """剧本 D2：中高风险调查 → 冻结 + 人工审批 → 核验归档（SC-02/SC-08 回放）"""
    print("\n▶ 剧本 D2 调查后冻结 + 人工审批（SC-02/SC-08 全链回放）")
    # 团伙观察名单主体 + 近 1h 12 笔小额（velocity 簇，BA-BR-14 确定性评分）
    subject = await seed_subject(app, "watch", [(50.0, 2 + i) for i in range(12)])
    case_id = await register_case(client, "D2", subject)

    r = await client.post(f"{BASE}/api/cases/{case_id}/aggregate", headers=headers())
    out = r.json()
    step("D2", 2, "聚合裁决=investigate 转调查（风险分≥40）",
         out.get("route") == "investigate" and out.get("risk_score", 0) >= 40,
         f"route={out.get('route')},risk_score={out.get('risk_score')}")

    r = await client.post(f"{BASE}/api/cases/{case_id}/investigate", headers=headers())
    inv = r.json()
    step("D2", 3, "AA-SK-02 调查完成：假设定性 + 证据固化 + 移交审批",
         r.status_code == 200 and inv.get("case_status") == "PENDING_APPROVAL"
         and inv.get("evidence_fixed") is True,
         f"hypothesis={inv.get('hypothesis', {}).get('pattern')}")

    svc = await _disposition_service(web)
    gate = await svc.submit(case_id, "freeze", None, f"{case_id}:freeze")
    step("D2", 4, "Agent 冻结被门控：E-DISP-AUTH 建审批工单（DA-INV-02，SC-02）",
         gate.get("route") == "approval_required" and gate.get("code") == "E-DISP-AUTH",
         f"approval_id={gate.get('approval_id')}")

    r = await client.get(f"{BASE}/api/approvals", headers=headers("human:approver"))
    queued = [a for a in r.json().get("items", []) if a["approval_id"] == gate["approval_id"]]
    step("D2", 5, "审批门户可见待决工单（API-W-08）", len(queued) == 1)

    r = await client.post(f"{BASE}/api/approvals/{gate['approval_id']}/decide",
                          headers=headers("human:approver"),
                          json={"decision": "approved", "approver": "human:approver",
                                "comment": "证据充分，同意冻结（演示 D2）"})
    dec = r.json()
    step("D2", 6, "人工批准 → 冻结自动执行至 DISPOSED（SC-02 Then）",
         dec.get("route") == "executed" and dec.get("case_status") == "DISPOSED",
         f"exec_id={dec.get('exec_id')}")

    r = await client.post(f"{BASE}/api/cases/{case_id}/verify", headers=headers(),
                          json={"exec_id": dec["exec_id"]})
    ver = r.json()
    step("D2", 7, "AA-SK-04 核验一致 → 归档（含复盘入库申请，US-E6-03）",
         ver.get("consistency_check") is True and ver.get("case_status") == "ARCHIVED",
         f"kb_application={ver.get('kb_application')}")

    r = await client.get(f"{BASE}/api/audit/{case_id}", headers=headers())
    actions = [a["action"] for a in r.json().get("items", [])]
    step("D2", 8, "全链审计可回放（SC-08：立案→聚合→调查→审批→执行→核验→归档）",
         all(a in actions for a in ("case.register", "investigation.complete",
                                    "approval.create", "disposition.submit",
                                    "verification.run")), f"{len(actions)} 条")

    spans = await traces_of(client, case_id)
    skills = {s["skill_id"] for s in spans}
    step("D2", 9, "四技能 span 全留痕（US-E7-04：AA-SK-01~04）",
         skills >= {"AA-SK-01", "AA-SK-02", "AA-SK-03", "AA-SK-04"}, str(sorted(skills)))
    return case_id


async def d3_false_positive_rollback(client, app, web):
    """剧本 D3：误报申诉回滚（核验不一致 → 反向处置 → 人工申诉归档）"""
    print("\n▶ 剧本 D3 误报申诉回滚（SC-03/07 变体回放）")
    # 固定主体哈希：外部 mock 按主体确定性出信号，该哈希实证命中投诉否认交易
    # （complaint 0.9 → 评分 77 ≥ 70 审批线 BA-BR-02），保障误报剧本可重复回放
    subject = await seed_subject(app, "none", [(50.0, 2 + i) for i in range(12)],
                                 fixed_hash="c10f355d11154dde8d41333fb879f31d")
    case_id = await register_case(client, "D3", subject)

    await client.post(f"{BASE}/api/cases/{case_id}/aggregate", headers=headers())
    await client.post(f"{BASE}/api/cases/{case_id}/investigate", headers=headers())
    svc = await _disposition_service(web)
    gate = await svc.submit(case_id, "freeze", None, f"{case_id}:freeze")
    r = await client.post(f"{BASE}/api/approvals/{gate['approval_id']}/decide",
                          headers=headers("human:approver"),
                          json={"decision": "approved", "approver": "human:approver",
                                "comment": "同意冻结（演示 D3，后续证实误报）"})
    exec_id = r.json()["exec_id"]
    step("D3", 2, "前置：正常主体被误判冻结并执行（DISPOSED）",
         r.json().get("case_status") == "DISPOSED", f"exec_id={exec_id}")

    # 模拟下游系统回报执行异常（生产为执行失败回执；tg_app 写角色权限矩阵内）
    await app.execute("UPDATE disposition_record SET status='failed' WHERE exec_id=$1", exec_id)
    step("D3", 3, "故障注入：下游回报 disposition status=failed", True)

    r = await client.post(f"{BASE}/api/cases/{case_id}/verify", headers=headers(),
                          json={"exec_id": exec_id})
    ver = r.json()
    step("D3", 4, "AA-SK-04 核验不一致 → 反向处置回滚 → 升级 P0 转人工",
         ver.get("consistency_check") is False and ver.get("case_status") == "MANUAL_REVIEW",
         f"rollback_exec_id={ver.get('rollback_exec_id')}")

    rows = await app.fetch(
        "SELECT action, idempotency_key, status FROM disposition_record "
        "WHERE case_id=$1 ORDER BY ts", case_id)
    step("D3", 5, "反向处置凭证落库（幂等键 :rollback 后缀，DA-INV-03）",
         len(rows) == 2 and rows[1]["idempotency_key"].endswith(":rollback")
         and rows[1]["status"] == "executed", f"records={len(rows)}")

    r = await client.post(f"{BASE}/api/cases/{case_id}/review",
                          headers=headers("human:risk_officer"),
                          json={"decision": "dismiss", "operator": "human:risk_officer",
                                "comment": "持卡人申诉成立，证实误报，排除欺诈归档（演示 D3）"})
    step("D3", 6, "人工复核申诉成立 → REVIEW_DISMISSED → ARCHIVED（SC-03 变体闭环）",
         r.status_code == 200 and r.json().get("status") == "ARCHIVED",
         f"status={r.json().get('status')}")

    r = await client.get(f"{BASE}/api/audit/{case_id}", headers=headers())
    actions = [a["action"] for a in r.json().get("items", [])]
    step("D3", 7, "审计含 verification.p0 升级留痕（BA-BR-09）",
         "verification.p0" in actions, f"{len(actions)} 条")
    return case_id


async def main():
    print("TradeGuard 演示剧本（US-E7-05）：演示=测试回放，全部合成数据")
    print(f"API 基址：{BASE}")
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(f"{BASE}/api/health")
        step("SYS", 0, "web-api 探活 /api/health", r.status_code == 200)
        web = await asyncpg.create_pool(WEB_DSN, min_size=1, max_size=4)
        app = await asyncpg.create_pool(APP_DSN, min_size=1, max_size=2)
        try:
            cases = [await d1_low_risk_auto_release(client, app),
                     await d2_investigate_freeze_approve(client, app, web),
                     await d3_false_positive_rollback(client, app, web)]
        finally:
            await web.close()
            await app.close()
    print(f"\n■ 演示完成：3/3 剧本通过，共 {PASS} 步断言全绿")
    print("  案件号（门户回放）：" + ", ".join(cases))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except AssertionError as e:
        print(f"\n■ 演示失败：{e}")
        sys.exit(1)

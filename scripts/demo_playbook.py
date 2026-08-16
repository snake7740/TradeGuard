# -*- coding: utf-8 -*-
"""US-E7-05 剧本化演示（04 §8）：三个演示事件 = 测试回放（v1.4.4 契约）

  D1 低风险自动放行（SC-01 + AA-CL-01/02 闭环回放）：告警受理（202）→
     EventWorker 自动承接聚合（无需人工推动）→ 裁决 auto_release → DISPOSED
  D2 调查后冻结 + 人工审批（SC-02/SC-08 全链回放）：worker 聚合转调查 →
     手动 AA-SK-02 调查取证 → 处置门控建单（E-DISP-AUTH）→ 人工批准
     （decide {decision:"approve",opinion} + X-Operator）→ 冻结执行 → 核验归档
  D3 误报申诉回滚（SC-03/07 变体回放）：冻结执行后下游回报异常（tg_app 篡改
     disposition_record.status='failed' 模拟）→ 核验不一致 → 反向处置回滚
     （C1 逆动作对授权）→ P0 升级人工 → 复核 conclusion=release 申诉成立归档

契约要点（v1.4.4）：POST /api/alerts 返回 202；severity 为枚举 low|medium|high；
审批决策体 {decision:"approve"|"reject", opinion}；复核体 {conclusion, opinion}；
列表分页 {total|items}。人机边界（02 §3.3）：人类动作一律经真实 HTTP API
（localhost:8200，门户同路径）；Agent 侧处置提交经确定性内核直调（等价 API-M
MCP 调用，与 test_disposition 同一调用序列——"演示=测试回放"的落地形态）。
主体哈希经本地探针预选（复刻 mcp-external-mock 确定性播种），全部数据合成（03 §7）。

用法：.venv\\Scripts\\python.exe scripts\\demo_playbook.py
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import random
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
import httpx

BASE = os.getenv("TG_API_BASE", "http://localhost:8200")
WEB_DSN = os.getenv("TG_PLAYBOOK_DSN", "postgresql://tg_web:tg_web_dev@localhost:5433/tradeguard")
APP_DSN = os.getenv("TG_PLAYBOOK_APP_DSN", "postgresql://tg_app:tg_app_dev@localhost:5433/tradeguard")
os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1,mcp-core")
os.environ.setdefault("no_proxy", "localhost,127.0.0.1,mcp-core")


def _load_dotenv_token():
    """R-37：从仓库根 .env（gitignore，start_all 自动生成）装载 TG_API_TOKEN——
    web-api bearer 强制后，宿主侧脚本必须带令牌；缺省/CHANGE_ME 时保持开发直通。"""
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    try:
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("TG_API_TOKEN="):
                    val = line.split("=", 1)[1].strip()
                    if val and val != "CHANGE_ME":
                        os.environ.setdefault("TG_API_TOKEN", val)
    except OSError:
        pass


_load_dotenv_token()
MCP_CORE_URL = os.getenv("TG_PLAYBOOK_MCP_CORE", "http://127.0.0.1:8101/mcp/")

# D3 实证固定哈希：complaint 命中 2 条 + credit low + 无舆情（见文件尾探针注释）
D3_FIXED_HASH = "c10f355d11154dde8d41333fb879f31d"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services", "web-api"))
if hasattr(sys.stdout, "reconfigure"):      # Windows GBK 控制台兼容（✓ 等符号）
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PASS = 0


def step(scenario: str, no: int, desc: str, ok: bool, detail: str = ""):
    """每步断言的可视化留痕（演示现场口播依据）"""
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


# ---------- 主体探针：复刻 mcp-external-mock 确定性播种（须与 server.py 同步） ----------

def _mock_outcomes(subject: str) -> tuple[str, bool, int]:
    """返回 (credit_band, sentiment_hit, complaint_count)——与 AA-MCP-02 模拟同算法"""
    rnd = random.Random(int(hashlib.sha256(("credit:" + subject).encode()).hexdigest()[:8], 16))
    score = rnd.randint(350, 850)
    band = "high" if score < 520 else ("mid" if score < 680 else "low")
    rnd = random.Random(int(hashlib.sha256(("sentiment:" + subject).encode()).hexdigest()[:8], 16))
    sent_hit = rnd.random() < 0.3
    rnd = random.Random(int(hashlib.sha256(("complaint:" + subject).encode()).hexdigest()[:8], 16))
    return band, sent_hit, rnd.randint(0, 2)


def find_d1_subject() -> str:
    """D1 主体：恰好一条弱信号（credit-mid 9 分 或 舆情 ≤13 分），无投诉/无高危征信。
    每次运行现取新哈希——零历史交易，amount=800<5000 且 score<40 恒走 auto_release
    （固定哈希复跑会累积交易金额，越过 5000 自动放行线造成 flake）。"""
    while True:
        h = uuid.uuid4().hex
        band, sent_hit, complaints = _mock_outcomes(h)
        if complaints or band == "high" or (band == "mid" and sent_hit):
            continue
        if band == "mid" or sent_hit:
            return h


def find_d2_subject() -> str:
    """D2 主体：credit high 段（+16）确保 velocity 簇（30+24）之上 ≥70 审批线，
    冻结必经人工审批门控（E-DISP-AUTH，SC-02），不落入 40-69 中风险拒绝段。"""
    while True:
        h = uuid.uuid4().hex
        band, _, _ = _mock_outcomes(h)
        if band == "high":
            return h


# ---------- 数据播种与轮询 ----------

async def seed_subject(app, list_flag: str, txs: list[tuple[float, int]], subject: str) -> str:
    """播种演示主体（tg_app 为 account/transaction 写角色，02-roles.sql）。
    账户 INSERT 幂等（复跑友好）；交易全落近 1 小时内供 velocity 统计。"""
    await app.execute(
        "INSERT INTO account (account_hash, risk_level, list_flag) VALUES ($1, $2, $3) "
        "ON CONFLICT (account_hash) DO NOTHING",
        subject, 3 if list_flag != "none" else 0, list_flag)
    now = datetime.now(timezone.utc)
    for amount, minutes_ago in txs:
        await app.execute(
            """INSERT INTO transaction (tx_id, account_hash, amount, mcc, channel, ts)
               VALUES ($1, $2, $3, '5411', 'CNP', $4)""",
            f"demo-{uuid.uuid4().hex[:12]}", subject, amount, now - timedelta(minutes=minutes_ago))
    return subject


async def register_case(client: httpx.AsyncClient, scenario: str, subject: str,
                        severity: str) -> str:
    r = await client.post(f"{BASE}/api/alerts", headers=headers(),
                          json={"subject_ref": subject, "source_type": "demo_script",
                                "severity": severity})
    step(scenario, 1, f"POST /api/alerts 告警受理立案（severity={severity}，契约 202）",
         r.status_code == 202, f"status={r.status_code}")
    return r.json()["case_id"]


async def wait_status(client: httpx.AsyncClient, case_id: str, want: str,
                      timeout: float = 30.0) -> str:
    """轮询案件状态（tick 1s）；worker 轮询间隔 2s，30s 预算足够覆盖聚合全链"""
    deadline = time.monotonic() + timeout
    last = "?"
    while time.monotonic() < deadline:
        r = await client.get(f"{BASE}/api/cases/{case_id}", headers=headers())
        if r.status_code == 200:
            last = r.json().get("status")
            if last == want:
                return last
        await asyncio.sleep(1.0)
    return last


async def audit_of(client: httpx.AsyncClient, case_id: str) -> list[dict]:
    r = await client.get(f"{BASE}/api/audit/{case_id}", headers=headers())
    return r.json().get("items", []) if r.status_code == 200 else []


async def traces_of(client: httpx.AsyncClient, case_id: str) -> list[dict]:
    r = await client.get(f"{BASE}/api/observability/traces", params={"case_id": case_id},
                         headers=headers())
    return r.json().get("spans", []) if r.status_code == 200 else []


async def _disposition_service(web_pool):
    """Agent 侧处置内核装配（与 tests/conftest.py disposition 夹具同构）"""
    from app.repositories import CaseRepository
    from app.skills.disposition import DispositionService
    from app.skills.mcp_adapters import CoreClient

    class _NullPub:
        async def publish(self, *a, **kw):
            pass

    repo = CaseRepository(web_pool, _NullPub())
    return DispositionService(web_pool, repo, CoreClient(MCP_CORE_URL), _NullPub())


# ---------- 剧本 D1：低风险自动放行（worker 全自动闭环，AA-CL-01/02 展示面） ----------

async def d1_low_risk_auto_release(client, app):
    print("\n▶ 剧本 D1 低风险自动放行（SC-01 + AA-CL-01/02 自动闭环回放）")
    subject = await seed_subject(app, "none", [(800.0, 10)], find_d1_subject())
    case_id = await register_case(client, "D1", subject, "low")

    # 闭环展示面：立案后不人工推动，EventWorker（DB 轮询主力）自动承接到 DISPOSED
    status = await wait_status(client, case_id, "DISPOSED", timeout=30)
    step("D1", 2, "EventWorker 自动推进 REGISTERED→聚合→DISPOSED（无人工干预）",
         status == "DISPOSED", f"status={status}")

    audit = await audit_of(client, case_id)
    step("D1", 3, "审计链含自动通道准入依据（BA-BR-01，risk_score<40 且 amount<5000）",
         any("自动通道准入" in a.get("basis", "") for a in audit), f"audit={len(audit)} 条")

    r = await client.get(f"{BASE}/api/cases/{case_id}/dispositions", headers=headers())
    items = r.json().get("items", [])
    step("D1", 4, "处置凭证落库 action=release（API-W-22，DA-T-06）",
         len(items) == 1 and items[0]["action"] == "release"
         and items[0]["status"] == "executed", f"records={len(items)}")

    spans = await traces_of(client, case_id)
    step("D1", 5, "技能执行 span 可回放（US-E7-04，AA-SK-01）",
         any(s.get("skill_id") == "AA-SK-01" for s in spans), f"spans={len(spans)}")
    return case_id


# ---------- 剧本 D2：调查后冻结 + 人工审批（SC-02/SC-08 全链回放） ----------

async def d2_investigate_freeze_approve(client, app, web):
    print("\n▶ 剧本 D2 调查后冻结 + 人工审批（SC-02/SC-08 全链回放）")
    # credit-high 主体 + 近 1h 12 笔小额（velocity 簇，BA-BR-14）→ 评分 ≥70 审批线
    subject = await seed_subject(app, "watch", [(50.0, 2 + i) for i in range(12)],
                                 find_d2_subject())
    case_id = await register_case(client, "D2", subject, "high")

    status = await wait_status(client, case_id, "INVESTIGATING", timeout=30)
    step("D2", 2, "worker 聚合裁决=investigate 自动转调查（风险分≥70）",
         status == "INVESTIGATING", f"status={status}")
    audit = await audit_of(client, case_id)
    step("D2", 3, "聚合审计含转调查依据（中/高风险分段）",
         any("转调查" in a.get("basis", "") for a in audit), f"audit={len(audit)} 条")

    r = await client.post(f"{BASE}/api/cases/{case_id}/investigate", headers=headers())
    inv = r.json()
    step("D2", 4, "AA-SK-02 调查完成：假设定性 + 证据固化 + 移交审批",
         r.status_code == 200 and inv.get("case_status") == "PENDING_APPROVAL"
         and inv.get("evidence_fixed") is True,
         f"hypothesis={inv.get('hypothesis', {}).get('pattern')}")

    svc = await _disposition_service(web)
    gate = await svc.submit(case_id, "freeze", None, f"{case_id}:freeze")
    step("D2", 5, "Agent 冻结被门控：E-DISP-AUTH 建审批工单（DA-INV-02，SC-02）",
         gate.get("route") == "approval_required" and gate.get("code") == "E-DISP-AUTH",
         f"approval_id={gate.get('approval_id')}")

    r = await client.get(f"{BASE}/api/approvals", headers=headers("human:approver"))
    queued = [a for a in r.json().get("items", []) if a["approval_id"] == gate["approval_id"]]
    step("D2", 6, "审批门户可见待决工单且展示请求处置（API-W-08，requested_action）",
         len(queued) == 1 and queued[0].get("requested_action") == "freeze",
         f"requested_action={queued[0].get('requested_action') if queued else '-'}")

    r = await client.post(f"{BASE}/api/approvals/{gate['approval_id']}/decide",
                          headers=headers("human:approver"),
                          json={"decision": "approve",
                                "opinion": "证据充分，同意冻结（演示 D2）"})
    dec = r.json()
    step("D2", 7, "人工批准 → 冻结自动执行至 DISPOSED（SC-02 Then，route=executed）",
         r.status_code == 200 and dec.get("route") == "executed"
         and dec.get("case_status") == "DISPOSED", f"exec_id={dec.get('exec_id')}")

    r = await client.post(f"{BASE}/api/cases/{case_id}/verify", headers=headers(),
                          json={"exec_id": dec["exec_id"]})
    ver = r.json()
    step("D2", 8, "AA-SK-04 核验一致 → 归档（含复盘入库申请，US-E6-03）",
         ver.get("consistency_check") is True and ver.get("case_status") == "ARCHIVED",
         f"kb_application={ver.get('kb_application')}")

    actions = [a["action"] for a in await audit_of(client, case_id)]
    step("D2", 9, "全链审计可回放（SC-08：立案→调查→审批→执行→核验）",
         all(a in actions for a in ("case.register", "investigation.complete",
                                    "approval.create", "disposition.submit",
                                    "verification.run")), f"{len(actions)} 条")

    spans = await traces_of(client, case_id)
    skills = {s.get("skill_id") for s in spans}
    step("D2", 10, "四技能 span 全留痕（US-E7-04：AA-SK-01~04）",
         skills >= {"AA-SK-01", "AA-SK-02", "AA-SK-03", "AA-SK-04"}, str(sorted(s for s in skills if s)))
    return case_id


# ---------- 剧本 D3：误报申诉回滚（核验不一致 → 反向处置 → 人工申诉归档） ----------

async def d3_false_positive_rollback(client, app, web):
    print("\n▶ 剧本 D3 误报申诉回滚（SC-03/07 变体回放，C1 逆动作对授权）")
    # 固定主体哈希：complaint 命中 2 条（探针实证），叠加 velocity 簇评分 ≥70，
    # 保障误报剧本可重复回放（复跑交易累积只会进一步推高评分，不改变路径）
    subject = await seed_subject(app, "none", [(50.0, 2 + i) for i in range(12)],
                                 D3_FIXED_HASH)
    case_id = await register_case(client, "D3", subject, "high")

    status = await wait_status(client, case_id, "INVESTIGATING", timeout=30)
    step("D3", 2, "worker 聚合自动转调查（投诉信号 + velocity 簇 ≥70 线）",
         status == "INVESTIGATING", f"status={status}")
    await client.post(f"{BASE}/api/cases/{case_id}/investigate", headers=headers())
    svc = await _disposition_service(web)
    gate = await svc.submit(case_id, "freeze", None, f"{case_id}:freeze")
    r = await client.post(f"{BASE}/api/approvals/{gate['approval_id']}/decide",
                          headers=headers("human:approver"),
                          json={"decision": "approve",
                                "opinion": "同意冻结（演示 D3，后续证实误报）"})
    exec_id = r.json().get("exec_id")
    step("D3", 3, "前置：正常主体被误判冻结并执行（DISPOSED）",
         r.status_code == 200 and r.json().get("case_status") == "DISPOSED",
         f"exec_id={exec_id}")

    # 模拟下游系统回报执行异常（生产为执行失败回执；tg_app 写角色权限矩阵内）
    await app.execute("UPDATE disposition_record SET status='failed' WHERE exec_id=$1", exec_id)
    step("D3", 4, "故障注入：下游回报 disposition status=failed", True)

    r = await client.post(f"{BASE}/api/cases/{case_id}/verify", headers=headers(),
                          json={"exec_id": exec_id})
    ver = r.json()
    step("D3", 5, "AA-SK-04 核验不一致 → 反向处置回滚 → 升级 P0 转人工",
         ver.get("consistency_check") is False and ver.get("case_status") == "MANUAL_REVIEW",
         f"rollback_exec_id={ver.get('rollback_exec_id')}")

    rows = await app.fetch(
        "SELECT action, idempotency_key, status FROM disposition_record "
        "WHERE case_id=$1 ORDER BY ts", case_id)
    step("D3", 6, "反向处置凭证落库（幂等键 :rollback 后缀，逆对授权 DA-INV-03）",
         len(rows) == 2 and rows[1]["idempotency_key"].endswith(":rollback")
         and rows[1]["status"] == "executed", f"records={len(rows)}")

    r = await client.post(f"{BASE}/api/cases/{case_id}/review",
                          headers=headers("human:risk_officer"),
                          json={"conclusion": "release",
                                "opinion": "持卡人申诉成立，证实误报，排除欺诈归档（演示 D3）"})
    step("D3", 7, "人工复核申诉成立 → REVIEW_DISMISSED → ARCHIVED（SC-03 变体闭环）",
         r.status_code == 200 and r.json().get("status") == "ARCHIVED",
         f"status={r.json().get('status')}")

    actions = [a["action"] for a in await audit_of(client, case_id)]
    step("D3", 8, "审计含 verification.p0 升级留痕（BA-BR-09）",
         "verification.p0" in actions, f"{len(actions)} 条")
    return case_id


async def main():
    print("TradeGuard 演示剧本（US-E7-05，v1.4.4 契约）：演示=测试回放，全部合成数据")
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

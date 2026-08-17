# -*- coding: utf-8 -*-
"""为门户浏览器/人工测试准备"待审批"案件（浏览器多角色协同测试辅助）

背景：处置提交（freeze/block/reduce）是 Agent 职责（02 §3.3 人机边界），
门户无对应按钮；EventWorker 只自动承接到 INVESTIGATING。因此纯浏览器操作
无法产生审批工单——本脚本等价 D2 前五步的 Agent 侧动作，把案件推进到
PENDING_APPROVAL 并建好审批单，供审批门户接力人工决策。

链路：播种 credit-high 主体 + velocity 簇（≥70 审批线）→ 立案(severity=high)
→ 等 EventWorker 聚合转 INVESTIGATING → 调查内核（API-W-18）→ 处置内核
提交 freeze 被门控（E-DISP-AUTH）→ 输出 approval_id 供浏览器审批接力。

用法：.venv/Scripts/python scripts/seed_browser_case.py
输出：CASE=... / SUBJECT=... / APPROVAL_ID=...（stdout，供脚本/人工消费）
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import asyncpg  # noqa: E402
import httpx  # noqa: E402

from demo_playbook import (  # noqa: E402
    APP_DSN, BASE, WEB_DSN, _disposition_service, find_d2_subject,
    headers, register_case, seed_subject, wait_status,
)


async def main() -> str:
    web = await asyncpg.create_pool(WEB_DSN, min_size=1, max_size=4)
    app = await asyncpg.create_pool(APP_DSN, min_size=1, max_size=2)
    try:
        # 与 D2 同款造数：watch 名单 + 近 1h 12 笔小额（velocity 簇，BA-BR-14）
        subject = await seed_subject(
            app, "watch", [(50.0, 2 + i) for i in range(12)], find_d2_subject())
        async with httpx.AsyncClient(timeout=30.0) as client:
            case_id = await register_case(client, "SEED", subject, "high")
            status = await wait_status(client, case_id, "INVESTIGATING", timeout=45)
            if status != "INVESTIGATING":
                raise RuntimeError(f"案件未按预期转调查：status={status}")

            # 值班员在门户可自行完成的等价动作（API-W-18）
            r = await client.post(f"{BASE}/api/cases/{case_id}/investigate",
                                  headers=headers())
            inv_status = r.json().get("case_status")

            # Agent 侧处置申请（门户无入口，内核直调与 demo_playbook D2[05] 同构）
            svc = await _disposition_service(web)
            gate = await svc.submit(case_id, "freeze", None, f"{case_id}:freeze")
            if gate.get("route") != "approval_required":
                raise RuntimeError(f"处置未按预期进入审批门控：{gate}")

            print(f"CASE={case_id}")
            print(f"SUBJECT={subject}")
            print(f"STATUS_AFTER_INVESTIGATE={inv_status}")
            print(f"APPROVAL_ID={gate.get('approval_id')}")
            print(f"REQUESTED_ACTION=freeze")
            return case_id
    finally:
        await web.close()
        await app.close()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):  # Windows GBK 控制台兼容
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    asyncio.run(main())

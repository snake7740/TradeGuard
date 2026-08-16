"""动态配置服务（US-E1-03，SC-06：BA-BR 阈值变更不重启生效）

设计要点：
- Nacos v3 admin 配置 API（group=TRADEGUARD, dataId=ba-br-thresholds）为权威源；
- serverIdentity 服务端互信头鉴权（compose NACOS_AUTH_IDENTITY_KEY/VALUE 同源）；
- 每 5s 轮询热加载（urllib 在线程池执行，不阻塞事件循环）；
- Nacos 不可用 → 降级 sys_config 种子（DA-T-11），source 字段暴露来源供观测；
- 端口/适配器：ConfigSource 抽象，测试可注入替身。
"""
from __future__ import annotations

import asyncio
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone

NACOS_ADDR = os.getenv("NACOS_ADDR", "http://nacos:8848")
NACOS_IDENTITY_KEY = os.getenv("NACOS_AUTH_IDENTITY_KEY", "serverIdentity")
# R-37：互信值不再内置代码缺省（原缺省即公开仓库可见的开发凭据）。
# 容器经 compose `${NACOS_AUTH_IDENTITY_VALUE:?}` 强制注入；缺省时 Nacos 通道
# 自动降级（_fetch/_publish 直接返回 None/False），走 sys_config DB 降级路径。
NACOS_IDENTITY_VALUE = os.getenv("NACOS_AUTH_IDENTITY_VALUE")
DATA_ID = "ba-br-thresholds"
GROUP = "TRADEGUARD"
POLL_SECONDS = float(os.getenv("CONFIG_POLL_SECONDS", "5"))


def _fetch_nacos(addr: str, data_id: str, group: str) -> dict | None:
    """同步拉取 Nacos v3 admin 配置；任何异常返回 None 触发降级"""
    if not NACOS_IDENTITY_VALUE:   # R-37：互信值未注入 → Nacos 通道不可用，走 DB 降级
        return None
    qs = urllib.parse.urlencode({"dataId": data_id, "groupName": group, "namespaceId": "public"})
    req = urllib.request.Request(f"{addr}/nacos/v3/admin/cs/config?{qs}",
                                 headers={NACOS_IDENTITY_KEY: NACOS_IDENTITY_VALUE})
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            body = json.loads(resp.read().decode())
        if body.get("code") != 0:
            return None
        return json.loads(body["data"]["content"])
    except Exception:
        return None


def _publish_nacos(addr: str, data_id: str, group: str, values: dict) -> bool:
    """同步写回 Nacos v3 admin 配置（SC-06 D3：Nacos 为权威源，PUT 必须先写回）。
    写回失败时调用方仍写 DB 镜像并暴露 source，不阻断变更（降级路径）。"""
    if not NACOS_IDENTITY_VALUE:   # R-37：互信值未注入 → 写回视为失败（调用方降级 DB）
        return False
    data = urllib.parse.urlencode({
        "dataId": data_id, "groupName": group, "namespaceId": "public",
        "type": "json", "content": json.dumps(values, ensure_ascii=False)}).encode()
    req = urllib.request.Request(f"{addr}/nacos/v3/admin/cs/config", data=data,
                                 headers={NACOS_IDENTITY_KEY: NACOS_IDENTITY_VALUE})
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            body = json.loads(resp.read().decode())
        return body.get("code") == 0
    except Exception:
        return False


class ConfigService:
    """BA-BR 阈值热加载：Nacos 优先，DB 降级，内存暴露"""

    def __init__(self, pool=None, addr: str = NACOS_ADDR) -> None:
        self._pool = pool
        self._addr = addr
        self.values: dict[str, str] = {}
        self.source = "db"
        self.updated_at: datetime | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        await self._reload()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(POLL_SECONDS)
            await self._reload()

    async def reload(self) -> None:
        """公共重载入口（PUT /api/config/thresholds 写库后即时生效，不等 5s 轮询）"""
        await self._reload()

    async def publish(self, values: dict) -> bool:
        """SC-06 D3：阈值变更先写回 Nacos 权威源（再落 DB 镜像）。返回写回是否成功。
        values 应为合并后的全量键值（Nacos dataId content 为完整 JSON 文档）。"""
        return await asyncio.to_thread(_publish_nacos, self._addr, DATA_ID, GROUP, values)

    async def _reload(self) -> None:
        values = await asyncio.to_thread(_fetch_nacos, self._addr, DATA_ID, GROUP)
        if values is not None:
            if values != self.values or self.source != "nacos":
                self.values, self.source = values, "nacos"
                self.updated_at = datetime.now(timezone.utc)
            return
        # 降级：sys_config 种子（首次或 Nacos 故障）
        if self._pool is not None:
            try:
                rows = await self._pool.fetch(
                    "SELECT key, value FROM sys_config WHERE key LIKE 'br-%'")
                fallback = {r["key"]: r["value"] for r in rows}
                if fallback and fallback != self.values:
                    self.values, self.source = fallback, "db"
                    self.updated_at = datetime.now(timezone.utc)
            except Exception:
                pass  # 保持上一份可用配置

    def snapshot(self) -> dict:
        return {"source": self.source,
                "updated_at": self.updated_at.isoformat() if self.updated_at else None,
                "values": self.values}

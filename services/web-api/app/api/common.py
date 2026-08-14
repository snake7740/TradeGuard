"""路由公共件：TODO 占位契约、X-Operator 头解析与异常→错误信封映射"""
import urllib.parse

from fastapi import HTTPException


def todo(us: str, desc: str) -> HTTPException:
    """契约先行占位：端点已声明（openapi.yaml 同步），实现按 US 排期落地（07 §3）"""
    return HTTPException(status_code=501, detail={
        "code": "E-TODO", "message": f"{desc}（{us}，Sprint 排期见 07 §3）"})


def operator_from_header(raw: str | None, default: str) -> str:
    """X-Operator 头 → 操作者标识（跨团队契约：前端 encodeURIComponent 编码中文角色名）

    HTTP 头不允许原始中文，前端 axios 拦截器统一注入 URL 编码值，此处 unquote 解码
    后再落审计；无类别前缀（human:/agent:/system:）的人名补 human: 前缀以通过
    状态机 human_only 守卫（02 §7）。缺省值：无头/空头时使用 default。"""
    if raw is None or not raw.strip():
        return default
    decoded = urllib.parse.unquote(raw).strip()
    if not decoded:
        return default
    if not decoded.startswith(("human:", "agent:", "system:")):
        decoded = f"human:{decoded}"
    return decoded

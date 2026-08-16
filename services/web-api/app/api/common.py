"""路由公共件：TODO 占位契约、X-Operator 头解析与异常→错误信封映射"""
import urllib.parse

from fastapi import HTTPException


def todo(us: str, desc: str) -> HTTPException:
    """契约先行占位：端点已声明（openapi.yaml 同步），实现按 US 排期落地（07 §3）"""
    return HTTPException(status_code=501, detail={
        "code": "E-TODO", "message": f"{desc}（{us}，Sprint 排期见 07 §3）"})


OPERATOR_MAX_LEN = 40   # audit_log.actor varchar(40)（01-schema.sql）；R-37：入口校验防超长


def operator_from_header(raw: str | None, default: str) -> str:
    """X-Operator 头 → 操作者标识（跨团队契约：前端 encodeURIComponent 编码中文角色名）

    HTTP 头不允许原始中文，前端 axios 拦截器统一注入 URL 编码值，此处 unquote 解码
    后再落审计；无类别前缀（human:/agent:/system:）的人名补 human: 前缀以通过
    状态机 human_only 守卫（02 §7）。缺省值：无头/空头时使用 default。
    R-37：解码后超 40 字符拒绝（422）——此前超长值会令事务内审计 INSERT 抛
    "value too long" 回滚 500，或中间件审计静默丢失（绕过 api.request 留痕）。"""
    if raw is None or not raw.strip():
        return default
    decoded = urllib.parse.unquote(raw).strip()
    # R-37 复审收口：剔除控制字符——unquote 会解出 %0A/%0D 等，换行原样落
    # audit_log.actor 可在审计表/门户渲染中伪造多行条目（审计伪造面）
    decoded = "".join(ch for ch in decoded if ch.isprintable())
    if not decoded:
        return default
    if not decoded.startswith(("human:", "agent:", "system:")):
        decoded = f"human:{decoded}"
    if len(decoded) > OPERATOR_MAX_LEN:
        raise HTTPException(422, detail={
            "code": "E-OPERATOR-TOO-LONG",
            "message": f"X-Operator 解码后超过 {OPERATOR_MAX_LEN} 字符（audit_log.actor 上限）"})
    return decoded

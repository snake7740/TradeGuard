"""路由公共件：TODO 占位契约与异常→错误信封映射"""
from fastapi import HTTPException


def todo(us: str, desc: str) -> HTTPException:
    """契约先行占位：端点已声明（openapi.yaml 同步），实现按 US 排期落地（07 §3）"""
    return HTTPException(status_code=501, detail={
        "code": "E-TODO", "message": f"{desc}（{us}，Sprint 排期见 07 §3）"})

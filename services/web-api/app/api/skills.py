# -*- coding: utf-8 -*-
"""API-W-24 Skill 注册表（AA-SK 自包含打包的消费侧，skills/README.md）

GET /api/skills        —— 运行时装载 skills/AA-SK-*.md frontmatter 并校验
                          entrypoint 可导入（可发现/可分派元数据 + 坏包留痕）
GET /api/skills/{name} —— 单个 skill 装载详情（404 走契约错误信封）

每请求实时装载（5 个小文件 + importlib 模块缓存，开销可忽略），与阈值
热加载同精神：frontmatter 变更免重启可见。
"""
from fastapi import APIRouter, HTTPException

from ..skills.loader import load_all

router = APIRouter(prefix="/api/skills", tags=["系统"])


@router.get("")
async def list_skills():
    """已装载 skill 列表（含不可装载包的 error 留痕，坏包不阻断）"""
    skills = [s.to_dict() for s in load_all()]
    return {"count": len(skills), "skills": skills}


@router.get("/{name}")
async def get_skill(name: str):
    """单个 skill 装载详情"""
    for spec in load_all():
        if spec.name == name:
            return spec.to_dict()
    raise HTTPException(status_code=404, detail={
        "code": "E-NOT-FOUND",
        "message": f"skill 不存在：{name}（GET /api/skills 可枚举全部包）",
    })

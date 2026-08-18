FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
# rocketmq-client-python 0.5.0rc2 的 manylinux 轮子已从 PyPI 消失（后续版本均
# sdist-only 需 C++ 工具链），vendored 入库保证供应链可复现（详见 wheels/README.md）
COPY wheels /wheels
RUN pip install --no-cache-dir --find-links=/wheels -r requirements.txt
COPY app ./app
# Skill 运行时注册表（API-W-24）：把 skills/ 打包进镜像，容器内 loader
# 同样可装载（TG_SKILLS_DIR 指向 /srv/skills；entrypoint 前缀按仓库相对路径
# 解析，容器内 sys.path 含 /srv → app.skills.* 可导入）
COPY skills /srv/skills
ENV TG_SKILLS_DIR=/srv/skills
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

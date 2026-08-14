#!/bin/sh
# US-E7-04 Friday 直跑取证（绕过 Studio 前端空配置，官方 issue #797 同源问题）
# 用法：docker cp secrets/dashscope.env tradeguard-as-studio-1:/tmp/ds.env
#       docker cp scripts/friday_run.sh tradeguard-as-studio-1:/tmp/friday_run.sh
#       docker exec tradeguard-as-studio-1 sh /tmp/friday_run.sh
set -e
# preflight：/tmp/ds.env 必须已 docker cp 进容器（含 DASHSCOPE_API_KEY 与 MaaS base_url）
if [ ! -f /tmp/ds.env ]; then
  echo "缺少 /tmp/ds.env：先执行 docker cp secrets/dashscope.env tradeguard-as-studio-1:/tmp/ds.env" >&2
  exit 1
fi
. /tmp/ds.env
if [ -z "$DASHSCOPE_API_KEY" ]; then
  echo "/tmp/ds.env 未包含 DASHSCOPE_API_KEY" >&2
  exit 1
fi
cd /app/dist/app/friday
exec python main.py \
  --query '"你好，请用一句话介绍你自己，并说明你能提供哪些帮助。"' \
  --studio_url http://localhost:3000 \
  --llmProvider openai \
  --modelName qwen3.8-max \
  --apiKey "$DASHSCOPE_API_KEY" \
  --writePermission "" \
  --clientKwargs "{\"base_url\":\"$AGENTTEAMS_OPENAI_BASE_URL\"}"
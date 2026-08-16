#!/usr/bin/env bash
# 更新 AgentTeams Manager 的 LLM 配置（US-E1-02 解锁件，Key 仅经 secrets 文件流转）
# 原理：① Manager 容器以 --env-file /root/agentteams-manager.env 启动（官方脚本 4196 行）；
#       ② 先 sed 更新该文件 LLM 四键，再走官方 installer 的 KEEP_ALL 就地升级（跳过全部配置
#       步骤、按 env 文件原样重建容器），避免手工 docker run 丢失官方启动参数。
# 用法：wsl bash scripts/update-agentteams-llm.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SECRETS="$ROOT/secrets/dashscope.env"
ENV_FILE="/root/agentteams-manager.env"

[ -f "$SECRETS" ] || { echo "缺少 $SECRETS（先运行 scripts/set-dashscope-key.ps1）"; exit 1; }
[ -f "$ENV_FILE" ] || { echo "缺少 $ENV_FILE（AgentTeams Manager 未安装）"; exit 1; }

set -a; . <(tr -d '\r' < "$SECRETS"); set +a
: "${DASHSCOPE_API_KEY:?secrets 中无 DASHSCOPE_API_KEY}"

MODEL="${AGENTTEAMS_DEFAULT_MODEL:-qwen3.8-max}"
# R-37：租户专属 endpoint 不再内置缺省值（此前硬编码进公开仓库=租户拓扑泄露），
# 一律强制来自 secrets/dashscope.env（上方 set -a 已导出）或进程环境
BASE_URL="${AGENTTEAMS_OPENAI_BASE_URL:?缺少 AGENTTEAMS_OPENAI_BASE_URL（仅经 secrets/dashscope.env 流转，R-37）}"

cp -a "$ENV_FILE" "$ENV_FILE.bak.$(date +%Y%m%d%H%M%S)"
sed -i \
  -e "s|^AGENTTEAMS_LLM_API_KEY=.*|AGENTTEAMS_LLM_API_KEY=${DASHSCOPE_API_KEY}|" \
  -e "s|^AGENTTEAMS_DEFAULT_MODEL=.*|AGENTTEAMS_DEFAULT_MODEL=${MODEL}|" \
  -e "s|^AGENTTEAMS_OPENAI_BASE_URL=.*|AGENTTEAMS_OPENAI_BASE_URL=${BASE_URL}|" \
  "$ENV_FILE"
echo "[1/3] env 文件已更新（KEY=***MASKED***, MODEL=${MODEL}），旧文件已备份"

# 取证：升级前容器镜像/模型/启动参数（卷、端口、网络），供事后比对与兼容重建
OLD_IMAGE="$(docker inspect agentteams-manager --format '{{.Config.Image}}')"
OLD_MODEL="$(docker inspect agentteams-manager --format '{{range .Config.Env}}{{println .}}{{end}}' | grep '^AGENTTEAMS_DEFAULT_MODEL=' | cut -d= -f2)"
docker inspect agentteams-manager --format '{{range .Mounts}}{{if eq .Type "volume"}}-v {{.Name}}{{else}}-v {{.Source}}{{end}}:{{.Destination}}{{" "}}{{end}}{{range $p,$conf := .HostConfig.PortBindings}}{{range $conf}}-p {{.HostPort}}:{{$p}} {{end}}{{end}}' > /tmp/at-run-args.txt
docker inspect agentteams-manager --format '{{range $n,$c := .NetworkSettings.Networks}}{{$n}} {{end}}' > /tmp/at-nets.txt
echo "[2/3] 升级前：image=${OLD_IMAGE} model=${OLD_MODEL}"

# 官方 installer KEEP_ALL 升级：跳过升级子菜单与全部配置步骤，钉住当前镜像版本不漂移。
# 注意：KEEP_ALL 会用内存参数回写 env 文件，故 LLM 新值必须预导出到环境（prompt() 对已置
# 环境变量静默采用，installer 无法再用旧默认值覆盖）。
export AGENTTEAMS_NON_INTERACTIVE=1
export AGENTTEAMS_UPGRADE_KEEP_ALL=1
export AGENTTEAMS_VERSION="${OLD_IMAGE##*:}"
export AGENTTEAMS_REGISTRY="higress-registry.cn-hangzhou.cr.aliyuncs.com"
export AGENTTEAMS_LLM_API_KEY="${DASHSCOPE_API_KEY}"
export AGENTTEAMS_LLM_PROVIDER=qwen
export AGENTTEAMS_DEFAULT_MODEL="${MODEL}"
export AGENTTEAMS_OPENAI_BASE_URL="${BASE_URL}"
bash "$ROOT/scripts/agentteams-install.sh" manager
echo "[3/3] installer 退出码=$?"

# installer 重建后二次确认 env 文件未被回退（防御回写）
for kv in "AGENTTEAMS_DEFAULT_MODEL=${MODEL}" "AGENTTEAMS_OPENAI_BASE_URL=${BASE_URL}"; do
  if ! grep -q "^${kv}$" "$ENV_FILE"; then
    echo "警告：env 文件被 installer 回写，重新修正"
    sed -i -e "s|^AGENTTEAMS_DEFAULT_MODEL=.*|AGENTTEAMS_DEFAULT_MODEL=${MODEL}|" \
           -e "s|^AGENTTEAMS_OPENAI_BASE_URL=.*|AGENTTEAMS_OPENAI_BASE_URL=${BASE_URL}|" \
           -e "s|^AGENTTEAMS_LLM_API_KEY=.*|AGENTTEAMS_LLM_API_KEY=${DASHSCOPE_API_KEY}|" "$ENV_FILE"
  fi
done

sleep 20
NEW_MODEL="$(docker exec agentteams-manager sh -c 'echo $AGENTTEAMS_DEFAULT_MODEL' 2>/dev/null || echo UNAVAILABLE)"
KEY_SET="$(docker exec agentteams-manager sh -c '[ -n "$AGENTTEAMS_LLM_API_KEY" ] && echo yes || echo no' 2>/dev/null || echo unknown)"
NEW_BASE="$(docker exec agentteams-manager sh -c 'echo $AGENTTEAMS_OPENAI_BASE_URL' 2>/dev/null)"
STATUS="$(docker inspect agentteams-manager --format '{{.State.Status}}' 2>/dev/null || echo missing)"
echo "验证：容器状态=${STATUS} MODEL=${NEW_MODEL} BASE=${NEW_BASE} KEY_PRESENT=${KEY_SET}"
if [ "$NEW_MODEL" != "$MODEL" ] || [ "$NEW_BASE" != "$BASE_URL" ]; then
  # 容器创建时烘焙了旧值：env 文件已正确，重建容器使其生效
  echo "容器 env 与文件不一致，按官方 run 参数重建容器…"
  docker stop agentteams-manager >/dev/null && docker rm agentteams-manager >/dev/null
  # 从 installer 刚生成的容器取证完整启动参数不可行（已删），改用最小重建：
  # --env-file 方式与原脚本 4196 行一致；卷/端口从旧容器 inspect 缓存恢复
  docker run -d --name agentteams-manager --env-file "$ENV_FILE" \
    -e HOME=/root/manager-workspace -w /root/manager-workspace \
    $(cat /tmp/at-run-args.txt 2>/dev/null) "$OLD_IMAGE"
  for net in $(cat /tmp/at-nets.txt 2>/dev/null); do
    [ "$net" = "bridge" ] && continue
    docker network connect "$net" agentteams-manager 2>/dev/null || true
  done
  sleep 20
  NEW_MODEL="$(docker exec agentteams-manager sh -c 'echo $AGENTTEAMS_DEFAULT_MODEL' 2>/dev/null || echo UNAVAILABLE)"
  NEW_BASE="$(docker exec agentteams-manager sh -c 'echo $AGENTTEAMS_OPENAI_BASE_URL' 2>/dev/null)"
  echo "重建后验证：MODEL=${NEW_MODEL} BASE=${NEW_BASE}"
fi
[ "$NEW_MODEL" = "$MODEL" ] && [ "$NEW_BASE" = "$BASE_URL" ] && echo "RESULT: OK" || { echo "RESULT: FAILED"; exit 1; }

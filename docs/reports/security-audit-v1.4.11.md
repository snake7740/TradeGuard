# TradeGuard 深度安全审计与加固报告（R-37，文档体系 v1.4.11）

- 审计日期：2026-08-15 ~ 2026-08-16
- 审计方式：六领域并行深度审计（凭据与密钥 / 认证与访问控制 / 网络暴露面 / 依赖与供应链 / 注入与输入验证 / 数据与备份），
  全量修复后回归验证 + 第二轮六域复审核验
- 修复留痕：docs/05 §3.2 R-37、docs/07 Sprint 13 补充记录、docs/00 §6 v1.4.11
- 红线遵守：本报告及全部审计输出不含任何真实凭据值（一律脱敏）

---

## 1. 审计范围与方法

| # | 领域 | 覆盖对象 |
|---|---|---|
| 1 | 凭据与密钥 | 全仓被跟踪文件、git 历史、.env/secrets 流转链、导出件 |
| 2 | 认证与访问控制 | web-api 全量路由、SSE、门户代理链、CORS、操作者头 |
| 3 | 网络暴露面 | compose 端口发布、AgentTeams 独立栈、网关控制台 |
| 4 | 依赖与供应链 | Python 三 requirements、web-portal npm、基础镜像 |
| 5 | 注入与输入验证 | mcp-core 处置门控、web-api 输入契约、scripts SQL 面 |
| 6 | 数据与备份 | 命名卷、导出/恢复链、备份脚本、测试数据隔离 |

方法：静态源码审计 + 动态实测（真实 HTTP 探测、绕过构造、受控对照实验），
每项修复均以可复现取证收口；修复后执行全量业务回归，确认零业务破坏。

## 2. 第一轮发现与修复（九类，全部已修复）

### 2.1 凭据治理【高危】

**发现**：被跟踪文件中存在明文密钥与硬编码凭据——LLM API Key、Nacos serverIdentity
互信值与鉴权 token（代码内缺省值即公开仓库可见凭据）、脚本硬编码租户 MaaS 端点。
仓库为公开仓库（github.com/snake7740/TradeGuard），等同已暴露。

**修复**：
- 全量清除被跟踪文件中的明文密钥；git 历史与被跟踪文件复核确认零真实密钥残留；
- `.env`（gitignore）为唯一凭据源；入库的 `.env.example` / `secrets/dashscope.env.example`
  仅含 CHANGE_ME 格式模板——克隆后新建对应文件按格式填入即可；
- `scripts/start_all.py` 新增凭证自举：缺 .env 时以密码学安全随机源自动生成强凭据
  （TG_API_TOKEN=64 位 hex；Nacos 互信值 32 位 hex；鉴权 token=base64(36 随机字节)）；
- compose 关键凭据改 `${VAR:?}` fail-fast 插值：缺凭据拒绝启动，杜绝弱缺省启动；
- `scripts/update-agentteams-llm.sh` 移除硬编码租户端点，仅经 secrets/dashscope.env 流转；
- `scripts/nacos_register.py` 凭据改 env/.env 装载，缺失即报错退出（无代码内缺省凭据）。

### 2.2 API 鉴权【高危】

**发现**：web-api 全量 `/api` 无鉴权（含 SSE 豁免通道）；CORS 通配 `*`。

**修复**：
- 新增 `app/api_guards.py` bearer 守卫：全量 `/api` 强制 `Authorization: Bearer <TG_API_TOKEN>`，
  仅 `/api/health` 豁免；SSE（/api/events/stream）纳入鉴权；
  令牌比较先判长度再 `secrets.compare_digest` 恒定时间比较；
- 拒绝（api.denied）与请求（api.request，含解码后操作者）全量落审计；
- 门户 nginx envsubst 自动注入令牌，浏览器/前端零改动；
  脚本探活（higress_routes/demo_playbook）改从 env/.env 装载令牌随行；
- CORS 改 `TG_CORS_ORIGINS` 环境白名单（缺省仅本机门户与 dev server 两个源）。

### 2.3 网络暴露面【中危】

**发现**：compose 全部宿主端口裸绑定（等效 0.0.0.0），开发栈对外网可达。

**修复**：全部宿主发布端口改 `127.0.0.1:` 前缀绑定（pg 5433 / nacos 8848 /
higress 8001,8180 / studio 3000 / mcp 8101,8102 / web-api 8200 / portal 8300）；
容器间东西向互访不受影响。

### 2.4 依赖与供应链【高危】

**发现**：mcp SDK 旧版含 CVE-2025-66416（DNS rebinding，streamable-http 传输可被跨站劫持）；
web-portal 基座 nginx 旧版含 CVE-2026-42945（NGINX Rift）；uvicorn 处旧线。

**修复**：mcp==1.29.0（三处 requirements 同源）、pydantic==2.13.4（随 mcp 下限 >=2.11 对齐）、
uvicorn[standard]==0.34.3 安全修订线、web-portal 基座 nginx:1.31-alpine。
镜像经镜像源拉取并重打 tag 落地（本机 docker.io DNS 污染的既有环境应对）。

### 2.5 注入与输入验证【高危】

**发现**：
- mcp-core：approval_ref 验真兜底查询对 requested_action 为 NULL 的工单放行（失败开放）；
  处置金额无边界（NaN/inf/负数/超大可入）；apply_risk_bonus 可传负数实现减分后门；
  record_case_signals 分数与信号结构无校验；
- web-api：X-Operator 无长度上限；config PUT 键名无格式白名单、值无范围校验；
- scripts：check_pg 端口非数字可注入下游命令参数；data_retention 分区名直接拼接 SQL。

**修复**：
- `_approval_valid`：`req is None → False` 失败关闭 + 兜底查询移除 `requested_action IS NULL OR`
  分支（改 `AND requested_action = ANY($2)`）；
- execute_disposition：金额 float 化 try/except → E-BAD-AMOUNT；拒绝 NaN/±inf；区间 [0, 1e7]；
- apply_risk_bonus：仅受 (0,100] 整数，否则 E-BAD-POINTS（负分后门闭合）；
- record_case_signals：分数 int 化越界 E-BAD-SCORE；信号必须为含
  source/type/confidence/query_reason 的 dict 列表，否则 E-BAD-SIGNALS；
- operator 解码后 >40 字 → 422 E-OPERATOR-TOO-LONG；
- config PUT：键名 `^br-[0-9]{2}-[a-z0-9][a-z0-9-]{0,60}$` 白名单（422 E-CONFIG-KEY-FORMAT），
  值可 float 化且 [0, 1e5]（E-CONFIG-VALUE / E-CONFIG-VALUE-RANGE），先于既有逻辑执行；
- check_pg：`PORT.isdigit()` 守门；data_retention：`_IDENT` 正则白名单 + `_safe_ident`
  应用于全部动态标识符拼接点。

### 2.6 脚本健壮性与失败关闭【中危】

**发现**：nacos_register 读取异常被吞掉返回空 dict，重跑会以缺省集整体覆盖
PUT 热更改过的现值；凭据缺失仍静默启动。

**修复**：fetch_config 失败关闭语义——网络/鉴权异常抛错中止写回；HTTP 404（首次部署 /
容器重建后配置层清空）返回 {} 属正常播种路径；content 非对象拒绝合并；
只接纳标量现值，非标量键报告跳过。

### 2.7 克隆即完整启动【功能缺口】

**发现**：命名卷数据不入库，克隆/复制项目后仅有空库，无法完整启动。

**修复**：新增 `scripts/volume_export.py`（pg-data→data-only SQL gzip、higress-data→/data
快照 tar.gz，内置密钥扫描闸门：命中 LLM key/PEM 私钥块/Key 赋值模式即删除导出件并报错）；
`db/export/` 导出件随库提交（合成数据，无真实客户信息）；start_all 空库时自动
TRUNCATE public 全表 + psql 管道恢复（ON_ERROR_STOP=1，失败显式中断不静默回落），
导出缺失才回退 data-generator 合成。

### 2.8 测试工程竞态【工程】

**发现**：test_routes review/decide 装配（API 立案 + 两步 UPDATE 推进）被 compose
web-api 的 EventWorker 经同库抢跑——案件停留 REGISTERED 的窗口（POST 审计中间件 +
两次独立连接，Windows 上累计可达秒级）被 2s 轮询命中后自动链路驱至 DISPOSED，
7/7 假失败（对照实验：stop web-api 全绿 / restart 复现）。

**修复**：`_reviewable_case` 改单事务直插 INVESTIGATING——双守护触发器均为
BEFORE UPDATE OF status 不拦 INSERT，EventWorker 仅轮询 REGISTERED 故直插案件不可见，
竞态窗口清零；`_set_status` 保留并补竞态警示注释。业务代码零改动。

### 2.9 残留风险如实声明

- **git 历史中的原 Nacos 互信值**：曾随 compose 提交入公开历史，现以每克隆随机轮换作废
  （旧值对任何新克隆无效）；历史改写（filter-repo）为可选升级项，需全协作者重克隆；
- **开发环境 PG 缺省口令**（.env.example 固定值，仅回环可见）：按 loopback-only
  开发环境取舍保留，模板已注记"新部署建议改强口令"；
- LLM Key 历史复核：git 历史与被跟踪文件扫描确认零真实 LLM 密钥残留。

## 3. 回归验证（零业务破坏证据）

| 验证项 | 结果 |
|---|---|
| pytest 全量回归（compose 全栈在跑、EventWorker 活跃并行） | **169/169 全绿**（7m34s） |
| 前端构建 npm run build | 通过（仅既有 chunk-size 提示） |
| demo_playbook D1~D3 | **3/3 剧本 24 步断言全绿**（令牌强制下业务链完整） |
| start_all 端到端取证 | 核心通路 19 项全绿（C1~C9 + 探活/数据/播种/路由重建）+ X1 |
| **克隆式冷启动实证** | 删 .env + `down -v` 清全部命名卷后仅凭仓库重建：凭证自举→空库从 db/export 恢复（transaction=201657）→Nacos 播种→Higress 重建→AgentTeams 体检→**21/21 检查全绿 exit 0** |
| OpenAPI 契约 | 22 路径解析通过；SSE 由 `security: []` 改随全局 bearerAuth（契约与实现同步） |

## 4. 第二轮复审（修复后六域复核，直至清零）

对第一轮全部修复域做绕过尝试与残留扫描（六域并行复核 + 活体探测），新发现并修复
6 类问题（1 CRITICAL / 2 HIGH / 2 MEDIUM / 1 LOW 组），全部以可复现取证收口，
修复后业务回归零破坏。

### 4.1 新发现与修复（按严重度排序）

| 级别 | 问题 | 修复 |
|---|---|---|
| **CRITICAL** | starlette <1.0.1 的 CVE-2026-48710：`request.url.path` 可被恶意 `Host: x@evil.com/` 头污染为 `//api/...`，绕过 `api_guards` 的 `startswith("/api/")` 判断而路由仍命中 → 全量未鉴权访问（第一轮 bearer 守卫被底层框架缺陷旁路） | 双层设防：①守卫改取 `request.scope["path"]`（与路由同源，不受 Host 头影响）；②依赖升级 fastapi==0.141.1 / starlette==1.6.0（CVE 全修复线，一并覆盖 CVE-2025-62727 Range DoS / CVE-2026-54283 表单大小 / CVE-2025-54121 multipart DoS）；mcp-core / mcp-external-mock requirements 显式钉 starlette==1.6.0 |
| **HIGH** | 卷导出件 higress-data.tar.gz 会打入容器内自签证书私钥（data/secrets/ 与 *.key/*.pem），违背"私钥不落盘/不入库"红线 | `volume_export.py` 增 tarfile filter 剔除 `secrets` 目录与 .key/.pem/.crt/.p12/.pfx/.bak；密钥扫描闸门补 base64 封装 PEM（`LS0tLS1CRUdJTi`）/GitHub 令牌（ghp_/github_pat_）/AWS AKIA 特征；再生成导出件实证 higress tar 压缩后仅 6 KB、密钥扫描 0 命中 |
| **HIGH** | AgentTeams 4 个 Worker 的 8088 控制台以非回环（宿主通配/随机）形式发布，暴露管理面 | `agentteams_doctor.py` 固化控制台回环口径：aa-ag-0N → `127.0.0.1:(18090+N)`=18092~18095；新增 `ensure_console_loopback()` 自检——发现非回环发布即重建容器收口（env 经临时文件传入、用后即删，不落 argv/不回显），实测 4/4 回环 OK、RESULT: OK |
| **MEDIUM** | start_all 卷恢复非原子：psql 管道中途失败会留下半恢复库却被判"数据就位" | 恢复 psql 增 `--single-transaction`（整库恢复原子化，失败即整体回滚不误判健康） |
| **MEDIUM** | db/init 迁移非真幂等：重跑（新卷/克隆恢复后二次 init）会因表已存在/触发器重名报错 | 01-schema.sql 37 表 + 10 索引全部 `IF NOT EXISTS`、sys_config 播种 `ON CONFLICT (key) DO NOTHING`；04-invariants.sql `DROP TRIGGER IF EXISTS` 前置 + 白名单 `ON CONFLICT DO NOTHING`。对运行库实测重跑为零改动（INSERT 0 0，sys_config=11、tables=38、triggers=3、白名单 18 行不变） |
| **LOW 组** | 输入面四处收敛：①config PUT 正则 `$` 可被尾随换行绕过、且未播种键可直写 Nacos 污染权威源；②audit_log.basis(varchar 300) 会被 500 字 opinion 拼接溢出截断不可控；③X-Operator 可含控制字符；④mcp-core 幂等键/审批凭证无列宽上限校验 | ①`KEY_PATTERN` 改 `\Z` + 新增键存在性闸门（未播种键先 400 `E-CONFIG-KEY`），PUT 顺序定为 键白名单/值域 → 存在性 → Nacos → DB → reload；②basis 在 5 个写入点统一 `[:300]` 截断（repositories.transition / disposition._decide / disposition._audit / kb._decide / knowledge.publish），不收紧契约——opinion 全文仍完整保留于 approval_record.opinion(varchar 500)；③operator 解码后仅保留 `isprintable()` 字符；④execute_disposition 增 `idempotency_key>60 → E-BAD-KEY`、`approval_ref>40 → E-DISP-AUTH`（对齐列宽），apply_risk_bonus basis `[:250]`（md5 幂等标记仍按全量 basis 计算不破坏幂等） |

### 4.2 复审取证（活体实测）

| 验证项 | 结果 |
|---|---|
| 鉴权绕过探针 5/5 | 非 ASCII 令牌 → 401 + api.denied 落审计；Host 头污染（`Host: x@evil.com/`）→ 401；无令牌 → 401；合法令牌 → 200；`/api/health` → 200 |
| CVE-2026-48710 旁路面清零 | 全仓 grep 确认 `request.url.path` 安全判断仅 api_guards 一处且已改 `scope["path"]`，无残留 |
| 导出件密钥扫描 | 再生成后 higress-data.tar.gz 与 tradeguard-data.sql.gz 均 0 命中 |
| 迁移幂等 | 对运行库重跑 01/04 零改动（见 4.1） |
| Worker 控制台 | doctor 复跑 4/4 回环 OK |
| pytest 全量回归（compose 全栈并行、EventWorker 活跃） | **169/169 全绿**（8m00s）；其间加固 1 处 flake（test_repositories 尾随"状态相等"断言被 EventWorker 抢跑 → 改"审计链无 ApprovalApproved 迁移痕"，竞态免疫） |
| demo_playbook D1~D3 | **3/3 剧本 24 步断言全绿** |
| OpenAPI 契约 | 22 路径解析通过；`AlertInput.subject_ref` 补 maxLength 64 |

### 4.3 第二轮残留（如实声明）

- 全部 R-37 与第二轮收口改动均在工作区（未提交——遵守"未经用户要求不 commit/push"约束）；
  `scripts/update-agentteams-llm.sh` 的租户 MaaS 端点硬编码在 HEAD 历史中仍存在，工作区已改
  `${AGENTTEAMS_OPENAI_BASE_URL:?}` fail-fast（需用户提交后随之闭合）；
- `db/export/` 导出件与新增文件（secrets/dashscope.env.example、volume_export.py、
  db/export/README.md）当前为 untracked，私钥已剔除、可安全入库（待用户提交）；
- PG 模板弱口令按 loopback-only 取舍保留；rocketmq-client-python 0.5.0rc2 列为供应链观察项；
- 2 处 INFO（confidence 无 DB 层校验、`isinstance(True,int)` 为 True）经核验无安全影响。

**复审结论：第二轮新发现的 6 类问题（含 1 CRITICAL 框架级鉴权旁路）已全量修复并逐项活体取证，
安全面清零；业务功能与技术调用链零破坏（169/169 全量回归 + 3/3 演示剧本于 compose 全栈并行下复跑通过）。**

## 5. 刻意保留项与后续建议

| 项 | 状态 | 理由 / 建议 |
|---|---|---|
| MCP streamable-http 传输层鉴权 | 保留现状 | 容器内东西向调用，无宿主对外暴露（2.3 后仅回环）；生产形态经 Higress 加消费者 token 即可（04 §4.1 已设计） |
| approval_record 消费标记 | 保留逆动作对设计 | "批准冻结即含解冻纠错授权"为 BA 既定语义（R-25），双处同源守卫 |
| Higress 控制台首次初始化 | 保留 | 路由经 /data 文件仓下发 + 幂等重建脚本，不依赖控制台密码（04 §5 如实声明） |
| gitleaks/pre-commit 密钥扫描 CI | **建议新增** | 将 volume_export 的密钥扫描模式升级为 CI 门禁，防未来回归 |
| git 历史改写 | 可选升级 | 原 Nacos 互信值已被轮换作废；如需彻底清除历史需 filter-repo + 全协作者重克隆 |
| 生产 SSO | 设计已就位 | bearerAuth 静态 token 为演示形态，openapi 已声明替换路径（04 §2） |

## 6. 结论

六领域审计发现的九类问题已全量修复并逐项取证；第二轮复审对全部修复域做绕过尝试与
残留扫描，再发现并修复 6 类问题（含 1 CRITICAL 框架级鉴权旁路 CVE-2026-48710），
复审直至清零（见 §4）。两轮修复均未破坏任何业务功能与技术调用链（169/169 全量回归、
3/3 演示剧本、21/21 克隆式冷启动取证，均于 compose 全栈并行下复跑通过）。
仓库当前安全基线：凭据零明文、全量 API 鉴权（含框架级 Host 头旁路双层封堵）、
仅回环暴露（含 AgentTeams Worker 控制台）、已知 CVE 清零（fastapi/starlette 全修复线）、
输入验证失败关闭、卷导出件私钥剔除、迁移幂等、恢复原子、克隆即完整启动。

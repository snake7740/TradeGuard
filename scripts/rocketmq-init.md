# RocketMQ Topic 初始化（US-E1-01）

`case-events` Topic（Tag=事件类型，见 docs/03 §9.2 事件目录）在 broker 就绪后预建。

> 自 v1.4.4 起 `docker-compose.yml` 已内置 `rocketmq-init` 一次性服务自动预建
> Topic（工作流 A3），`docker compose up -d` 即完成，无需手动执行下文命令；
> 手动方式保留为排障/生产环境备用。

> 说明：broker.conf 外挂挂载（`-c`）在 apache/rocketmq:5.3.1 + Windows 卷上触发
> `ScheduleMessageService.configFilePath` NPE（apache/rocketmq-docker#85），
> 故 Sprint 0 采用「无外挂配置 + 启动后预建 Topic」方案；`config/rocketmq/broker.conf`
> 保留为迁移 Linux 后的参考配置。

## 执行（栈已 up 且 rocketmq-broker healthy 后）

```powershell
docker compose exec rocketmq-broker sh mqadmin updateTopic -n rocketmq-namesrv:9876 -c DefaultCluster -t case-events -r 4 -w 4
```

## 验证

```powershell
docker compose exec rocketmq-broker sh mqadmin topicList -n rocketmq-namesrv:9876 | findstr case-events
```

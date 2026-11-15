# PostgreSQL 迁移说明

本目录的 SQL 文件是第二阶段的数据模型基线。生产环境应由受控的部署流水线或 DBA 执行，不能由 Web 请求、Agent 或前端直接执行。

## 前置条件

- PostgreSQL 16 或兼容版本。
- 迁移账户仅拥有目标 schema 的 DDL 权限；运行账户使用独立的最小权限角色。
- `001_core_schema.sql` 需要 `pgcrypto` 扩展，用于 `gen_random_uuid()`。
- `002_pgvector.sql` 只有在安装 pgvector 扩展时才执行；它为知识片段增加向量列和索引。

## 推荐执行顺序

1. 创建空数据库与最小权限角色。
2. 执行 `migrations/001_core_schema.sql`。
3. 创建运行时最小权限角色，并验证表、索引、约束以及仅追加审计记录。
4. 在 pgvector 已安装时执行 `migrations/002_pgvector.sql`。
5. 仅在隔离开发库中导入模拟机组与测点字典；生产数据不应由模拟数据脚本写入。

当前骨架不在启动时自动迁移，避免服务账户意外持有 DDL 权限。

## 第五阶段运行时仓储

`postgres.py` 提供两类实现：

- `PostgresPlantDataRepository`：只含参数化 `SELECT`，读取 `plant_unit`、`tag_definition`、`telemetry_point` 和 `alert_event`。
- `PostgresTraceRepository`：确保 `conversation_session` 存在后，仅向 `execution_trace` 追加节点事件。

默认配置不会创建数据库连接。只有设置 `POWERAGENT_DATA_SOURCE=postgres` 或
`POWERAGENT_TRACE_STORE=postgres` 且提供有效 `POWERAGENT_DATABASE_URL` 后，才在实际请求时连接数据库。
开发骨架目前使用一个连接串以简化本地联调；生产环境应将数据只读和 Trace 审计拆分为不同的最小权限数据库角色。

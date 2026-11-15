# PowerAgent 电厂智能诊断系统源码包

本包面向 U01 机组凝汽器真空异常辅助诊断，包含 FastAPI 后端、Vue 前端、
LangGraph Agent、规则诊断、版本化知识检索、DOCX 报告、数据库适配与自动化测试。
默认使用确定性模拟数据；未配置云端模型密钥时使用本地规则规划器。

## 目录

- `backend/app/`：API、Agent、数据适配器、诊断、知识检索和报告服务。
- `backend/app/database/migrations/`：PostgreSQL 与可选 pgvector 迁移。
- `backend/tests/`：电厂专题及公共基础服务的测试。
- `frontend/`：Vue 3 + Vite 源码、依赖锁文件与构建配置。
- `knowledge/condenser/`：3 份电厂凝汽器演示知识文档，运行时必须保留。
- `scripts/`：电厂离线评测和示例报告生成源码。
- `.env.example`：配置模板，不含实际密钥。
- `MANIFEST.sha256`：除该清单自身之外的包内文件 SHA-256。

## 后端安装与启动

需要 Python 3.10 或更高版本。在解压后的 PowerAgent 目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload
```

首次配置时才复制 `.env.example`，已有 `.env` 时直接编辑原文件。
默认 mock 演示和规则规划不需要云端密钥或数据库。
如需 DeepSeek 规划，在本地 `.env` 设置 `POWERAGENT_LLM_API_KEY`。
实际密钥只放后端，不能写入前端或提交到仓库。

健康检查：http://127.0.0.1:8000/api/v1/health

接口文档：http://127.0.0.1:8000/docs

## 前端安装与启动

需要 Node.js 18 或更高版本，建议使用 Node.js 22 LTS。在另一个终端执行：

```powershell
cd frontend
npm ci
npm run dev
```

访问 http://127.0.0.1:5173 。开发服务器将 `/api` 代理到后端 8000 端口。
安装依赖需要网络；本包不包含 Python 或 Node.js 运行环境及依赖安装目录。

## 测试与构建

在 PowerAgent 根目录执行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m scripts.evaluate_graduation --output reports/evaluation
.\.venv\Scripts\python.exe -m scripts.generate_demo_report
```

测试配置强制使用离线 mock 与内存 Trace，不调用云端模型、真实网关或数据库。
数据库仓储测试使用模拟连接；真实 PostgreSQL 接入需要独立联调。
评测使用合成场景，仅证明规则在合成数据上的一致性，不能代表真实电厂准确率。

前端在 `frontend` 目录执行 `npm run build`，生成 `dist/`。
正式部署需要配置静态文件托管和 `/api` 转发；当前后端不直接托管前端页面。

## 数据库与知识库

默认无需数据库。数据库接入说明见 `backend/app/database/README.md`。
SQL 迁移需单独执行，应用启动不会自动创建表。
知识库资料标记为 `demo_verified`，用于演示，不能替代现场正式规程。
诊断和报告仅辅助人工核查，不下发设备控制命令。

## 打包范围

交付包只包含电厂运行源码、测试、必要知识资源、配置模板和使用说明。
排除学习笔记、其他专题、公开候选数据、历史设计材料、生成报告、依赖和缓存。
`.gitignore` 控制 Git 的未跟踪文件忽略行为，不能自动过滤普通 ZIP 压缩操作。
本包通过明确文件范围生成，并对最终文件路径及内容进行检查。

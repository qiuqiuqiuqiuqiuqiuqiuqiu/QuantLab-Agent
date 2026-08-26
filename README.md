# QuantLab Agent

> 一个**不依赖 Agent 框架**、由 Python 标准库和少量明确接口组成的 CLI Agent。它将自然语言研究需求转为可审计的研究步骤、工具调用、证据与结论。

## 用一句话解释

你告诉它：**“我想验证某个量化想法。”**

它不会直接编造“这个策略赚钱”。它会像一位研究助理一样：记录研究假设和数据来源、调用确定性工具留存回测证据、检查风险指标，最后给出“通过治理检查 / 需要补充材料 / 等待人工审批”的结论。每次执行都能追溯和复现。

## 它解决什么问题？

量化研究容易出现三类问题：研究过程没人记录、回测结果无法复现、模型把“回测不错”误说成“可以上线”。QuantLab 将模型的职责限制为**规划和决策**，将事实计算、文件写入和风险门槛放在**确定性工具**中执行。

```text
自然语言任务
  ↓
LLM 规划下一步（只能选择定义好的工具）
  ↓
工具执行并返回证据（研究登记 / 回测记录 / 治理检查）
  ↓
Agent 根据真实工具结果继续行动或输出结论
  ↓
Run manifest + 审计日志 + 研究证据，均落盘保存
```

这也是一个适合 Agent 开发岗位的项目：展示 tool calling、有限状态循环、上下文控制、失败边界、持久化恢复和 eval-friendly 的确定性工件，而不是一个只会聊天的 Demo。

## 当前项目框架

```text
quant_lab_agent/
├── agent.py              # 有上限的 Plan → Act → Observe 主循环
├── llm.py                # 唯一的模型供应商边界；含离线 ScriptedLLM
├── cli.py                # 自研 argparse CLI
├── context.py            # 上下文预算和逐级压缩
├── audit.py              # 追加式 JSONL 审计日志
├── prompt.py             # Agent 行为与安全约束
├── runtime/
│   └── run_store.py      # run manifest、事件日志、history、resume
└── tools/
    ├── base.py           # 工具协议及 JSON Schema
    ├── research.py       # 假设、数据血缘、回测证据
    ├── backtest.py       # 受限 Python 回测执行 + JSON 结果读取
    ├── workspace.py      # 工作目录内读取与唯一锚点修复
    └── governance.py     # 确定性校验与人工审批请求

policies/default.yaml     # 可演进的运行与治理策略
examples/research_task.txt
 tests/                   # 离线、无需 API Key 的单元测试
```

运行后，所有工件位于工作目录：

```text
.quant-agent/
├── audit.jsonl                 # 所有请求、工具参数和结果
├── research.json               # 结构化研究登记
├── backtests.jsonl             # 回测证据
├── approval_requests.jsonl     # 人工审批请求
└── runs/<run-id>/
    ├── manifest.json           # 任务、状态、最终答复
    └── events.jsonl            # 此次任务的关键事件
```

## 快速开始

```bash
python3 -m pip install -e '.[openai]'
cp .env.example .env  # 配置 OPENROUTER_API_KEY（请勿提交 .env）
export OPENROUTER_API_KEY=sk-or-v1-...
export QUANT_AGENT_MODEL=~openai/gpt-latest

# 启动一个任务
quantlab run -p "登记一个均值回归研究，补齐数据血缘和复现命令，然后说明所需治理证据。"

# 查询和重新运行（resume 会从原始 prompt 开始一次新运行，保留来源关系）
quantlab history
quantlab inspect run_20260825_120000_abcdef
quantlab resume run_20260825_120000_abcdef

# 兼容旧命令（可选）
quant-governance -p "登记一个研究"

pytest -q
```

## 当前工具与安全边界

| 工具 | 作用 | 不能做什么 |
|---|---|---|
| `register_research` | 保存假设、数据来源、方法和复现命令 | 不运行交易 |
| `record_backtest` | 记录指标、假设和局限 | 不把指标当审批结果 |
| `run_backtest` | 无 shell 地执行工作目录内的 Python 脚本，并读取 JSON 指标 | 不会执行任意 shell 或自动装包 |
| `read_workspace_file` / `replace_workspace_text` | 检查并以唯一锚点修复研究脚本 | 不能访问工作目录外的文件 |
| `validate_research` | 校验缺失指标及回撤、换手率门槛 | 不批准部署 |
| `request_approval` | 创建人工审批请求 | 不部署策略 |

Agent 有最大轮次；工具参数经函数签名校验；未知工具会被拒绝；工具调用和结果会写入审计日志。该项目没有任何下单、部署或绕过人工审批的能力。

## 后续迭代路线（面试可讲）

1. **MVP（当前）**：Tool loop、审计、治理工具、持久化运行记录。
2. **研究执行层（当前已开始）**：受限 Python runner 无 shell 执行、自动读取 JSON 回测产物，并把失败分类反馈给 Agent 进行“检查 → 最小修复 → 重跑”。
3. **可靠性层**：数据 schema 检查、回测适配器、按失败类型重试、上下文摘要持久化、真正的 step-level resume。
4. **Evals**：构建固定任务集，评估任务完成率、工具成功率、错误恢复率、成本和可复现率。

## 设计原则

- LLM 负责推理，工具负责事实与执行。
- 回测通过 ≠ 可以上线；任何上线必须经过独立的人类审批。
- 每一个结论应能找到命令、指标或记录作为证据。
- 尽量少依赖：项目不使用 LangChain、AutoGen 等 Agent 框架。

## Evals 与运行追踪

项目内置了不依赖 API Key 的确定性回测评测集，覆盖成功回测、语法错误、数据缺失、无效指标工件和遗漏工件：

```bash
python3 evals/runner.py
python3 -m pytest tests -q
```

每个 run 同时记录 `llm_response` 与 `tool_call` 事件：轮次、Token、延迟、工具参数、工具结果和回测失败类别。`run_backtest` 对同一脚本默认最多允许 **2 次修复后的重跑**（可通过 `QUANT_AGENT_MAX_BACKTEST_RETRIES` 调整）；超出后会停止重试并要求人工复核。

## 回测 Runner 协议

让回测脚本将结果写到任务指定的 JSON 路径即可；推荐使用 `metrics` 字段：

```json
{
  "metrics": {"sharpe": 1.2, "max_drawdown": 0.15, "turnover": 1.8},
  "equity_curve": [["2025-01-02", 1.0], ["2025-01-03", 1.02]],
  "drawdown_curve": [["2025-01-02", 0.0], ["2025-01-03", -0.01]]
}
```

`run_backtest` 不经过 shell，只接受工作目录内的 `.py` 文件，限制超时为 300 秒，并且不继承 API Key 等环境变量。脚本失败时会返回 `syntax_error`、`data_or_schema_error`、`missing_result`、`invalid_result` 等结构化诊断；Agent 应读取脚本、进行一次唯一锚点修复后重跑。

> 注意：这只是应用层的“受限执行器”，不是针对不可信代码的安全沙箱。生产环境必须在容器/VM 中运行，并加入网络隔离、只读数据挂载、资源限额和操作系统级权限控制。

## Web 工作台（MVP）

项目保留 CLI 作为核心入口，同时增加了面向研究员的 Web 工作台：可以提交研究任务、查看运行状态、观察 Agent 工具调用事件，并查看任务最终结论。前端位于 `frontend/`，采用 React + TypeScript + Vite；页面刻意保持轻量，仅用于登录、任务监控、执行日志和回测曲线查看。后端采用 FastAPI，并通过 SSE 接口提供任务事件流。

```bash
# 终端 1：启动 Python API
python3 -m pip install -e '.[openai,web]'
export OPENROUTER_API_KEY=sk-or-v1-...
export QUANT_AGENT_MODEL=~openai/gpt-latest
quantlab-api

# 终端 2：启动前端（需要 Node.js 20.19+ 或 22.12+）
cd frontend
npm install
npm run dev
```

打开 `http://localhost:5173`。开发时 Vite 会将 `/api` 请求代理到 `http://localhost:8000`。

当前页面包括：登录、任务列表、任务详情、Agent 执行时间线、真实回测净值图和基础实验指标对比；创建与执行任务仍以 CLI 为主。回测结果可选地在 JSON 工件中输出 `equity_curve` 与 `drawdown_curve`；每个点格式为 `["日期", 数值]`。

> Web API 目前未实现认证与多用户权限，仅限本地开发或受信任内网环境。生产部署前需接入登录、研究项目权限和审计访问控制。

## 多用户与生产部署

Web 版本使用 SQLite 保存用户、任务归属和审批记录。用户分为三类：

- `researcher`：发起研究、查看团队可见任务、提交审批；
- `reviewer`：查看任务、处理审批；
- `admin`：创建用户、查看全部任务、处理审批。

API 使用有过期时间的 Bearer Token；生产环境必须设置至少 32 位的 `QUANTLAB_AUTH_SECRET`，并仅通过 HTTPS 暴露服务。首次启动通过 `QUANTLAB_BOOTSTRAP_ADMIN` 与 `QUANTLAB_BOOTSTRAP_PASSWORD` 创建管理员。密码使用 `scrypt` 哈希后保存在 SQLite 中。

```bash
cp .env.production.example .env.production
# 编辑 .env.production，填入 OpenAI Key、随机密钥和管理员密码
docker compose up --build -d
```

部署后访问 `http://localhost:8000`；容器会托管已经构建的前端。数据位于 Docker volume `quantlab-data`。这是一套单机生产基线；真实生产环境还应放在反向代理/TLS 后，并将回测脚本移入独立容器或任务队列，不能把不可信策略代码直接跑在 API 容器内。

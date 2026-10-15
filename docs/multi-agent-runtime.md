# QuantLab multi-agent runtime

QuantLab uses strict plan-first orchestration. A tool-free planner first emits a
complete structured plan. Code validates the plan before the runtime may invoke
any bounded specialist. Specialists do not share chat history; they share
immutable artifact references and durable task records.

## Runtime boundaries

- `Agent`: one bounded model/tool loop.
- `PlanValidator`: capability, dependency-DAG, task-count, and budget checks.
- `MultiAgentRuntime`: authorization, topological plan execution, fresh specialist
  instances, task lifecycle, and run events.
- `TaskStore`: SQLite ledger for every specialist invocation.
- `ArtifactStore`: content-addressed payloads with SHA-256 verification.
- deterministic tools: market data, strategy compilation, backtesting,
  forecasting, and governance actions.

The current transport is local and synchronous. `MultiAgentRuntime.invoke()` is
the transport boundary; a future A2A client can implement the same task/result
contract for independently deployed agents. MCP is intended for exposing tools
and resources, not for replacing the runtime's workflow state.

## Agents

| Agent | Scope |
| --- | --- |
| `planner` | Produces the complete plan and cannot execute tools |
| `coordinator` | Final synthesis without delegation tools |
| `research_agent` | Quant concepts and existing-evidence analysis |
| `data_agent` | Market-data acquisition and validation |
| `strategy_agent` | Whitelisted declarative strategy specifications |
| `backtest_agent` | Bounded execution and targeted failure diagnosis |
| `forecast_agent` | Auditable statistical scenarios |
| `governance_agent` | Deterministic research and approval gates |

Enable the topology with `QUANTLAB_MULTI_AGENT=true`. Set it to `false` to use
the legacy single-agent loop.

## Durable data

- Agent tasks are stored in the `agent_tasks` table of `quantlab.db`.
- Run-level trace events remain under `.quant-agent/runs/<run_id>/events.jsonl`.
- Immutable artifacts are stored under `.quant-agent/artifacts/<artifact_id>/`.
- Agent messages should carry `artifact_id` values rather than embedding full
  CSV files, curves, or logs.

Final agent output uses a structured decision envelope:

```json
{
  "status": "validated_pending_approval",
  "user_message": "Research checks passed; deployment still requires approval.",
  "reason_codes": [],
  "evidence_artifact_ids": ["art_backtest_result_..."]
}
```

Invalid envelopes fail closed to `needs_review`.

## Strict execution order

1. Planner returns a versioned `ResearchPlan` JSON object.
2. `PlanValidator` verifies every task against the target agent's declared
   capabilities, rejects missing or cyclic dependencies, and enforces budgets.
3. Plans marked `requires_approval` stop before any specialist runs.
4. The runtime executes only declared tasks in topological order. A specialist
   cannot add another specialist task dynamically.
5. A no-tool synthesizer produces the final structured decision from the plan
   and persisted task results.

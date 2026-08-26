import type {Backtest, Run, RunEvent} from './types';

const equity: [string, number][] = [['2024-01',1.00],['2024-02',1.03],['2024-03',1.01],['2024-04',1.08],['2024-05',1.12],['2024-06',1.10],['2024-07',1.17],['2024-08',1.15],['2024-09',1.22],['2024-10',1.19],['2024-11',1.28],['2024-12',1.34]];
export const demoRuns: Run[] = [
  {run_id:'run_demo_mr_001',status:'completed',created_at:'2026-08-26T08:21:12Z',updated_at:'2026-08-26T08:23:46Z',prompt:'Test a 5-day mean-reversion signal across liquid US equities with trading-cost and liquidity constraints.',final_answer:'Mock run complete. The sample shows positive risk-adjusted returns in-sample; real delisting-inclusive data is required for out-of-sample validation.'},
  {run_id:'run_demo_mom_002',status:'needs_review',created_at:'2026-08-25T12:05:41Z',updated_at:'2026-08-25T12:08:12Z',prompt:'Compare US small-cap momentum stability across rebalance frequencies.',final_answer:'High-turnover variants are cost-sensitive and require practitioner review.'},
  {run_id:'run_demo_earnings_003',status:'completed',created_at:'2026-08-24T15:17:03Z',updated_at:'2026-08-24T15:19:54Z',prompt:'Test post-earnings-announcement drift with point-in-time filing controls.',final_answer:'Lineage and mock backtest are recorded. Not a market conclusion until real data is connected.'},
];
export const demoEvents: RunEvent[] = [
  {timestamp:'2026-08-26T08:21:12Z',event:'run_created',content:'Research task created'}, {timestamp:'2026-08-26T08:21:13Z',event:'tool_call',round:1,tool:'register_research',latency_ms:42,result:'Hypothesis registered'}, {timestamp:'2026-08-26T08:21:18Z',event:'tool_call',round:2,tool:'validate_lineage',latency_ms:19,result:'Lineage and reproduction command recorded'}, {timestamp:'2026-08-26T08:21:33Z',event:'tool_call',round:3,tool:'run_backtest',latency_ms:14820,result:'Mock backtest completed'}, {timestamp:'2026-08-26T08:23:15Z',event:'tool_call',round:4,tool:'governance_check',latency_ms:77,result:'Bias and cost assumptions checked'}, {timestamp:'2026-08-26T08:23:46Z',event:'run_finished',content:'Run completed'},
];
export const demoBacktest: Backtest = {run_id:'run_demo_mr_001',metrics:{sharpe:1.42,max_drawdown:-0.117,turnover:3.18},artifacts:{equity_curve:equity}};

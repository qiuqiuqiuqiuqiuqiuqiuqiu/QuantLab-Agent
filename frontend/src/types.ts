export type RunStatus = 'running' | 'completed' | 'failed' | 'needs_review';
export interface User { id: number; username: string; role: 'researcher' | 'reviewer' | 'admin'; }
export interface Run { run_id: string; status: RunStatus; prompt: string; created_at: string; updated_at: string; final_answer?: string | null; }
export interface RunEvent { timestamp: string; event: string; round?: number; tool?: string; latency_ms?: number; failure_category?: string; content?: string; result?: string; }
export type CurvePoint = [string, number];
export interface Backtest { run_id: string; metrics: {sharpe: number; max_drawdown: number; turnover: number}; artifacts: {equity_curve?: CurvePoint[]; drawdown_curve?: CurvePoint[]}; }
export interface Comparison { left: Backtest; right: Backtest; delta: {sharpe: number; max_drawdown: number; turnover: number}; }

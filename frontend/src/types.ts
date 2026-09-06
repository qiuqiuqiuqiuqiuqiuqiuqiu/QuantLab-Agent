export type RunStatus = 'running' | 'completed' | 'failed' | 'needs_review' | 'needs_input' | 'needs_data';
export interface User { id: number; username: string; role: 'researcher' | 'reviewer' | 'admin'; }
export interface Project { project_id:string; name:string; thesis:string; universe:string[]; benchmark:string; owner:string; created_at:string; updated_at:string; experiment_count:number; last_status:string; }
export interface Run { run_id: string; status: RunStatus; prompt: string; created_at: string; updated_at: string; final_answer?: string | null; }
export interface RunEvent { timestamp: string; event: string; round?: number; tool?: string; latency_ms?: number; failure_category?: string; content?: string; result?: string; }
export type CurvePoint = [string, number];
export interface Backtest { run_id: string; metrics: {sharpe: number; max_drawdown: number; turnover: number; annualized_return?: number; annualized_volatility?: number; observations?: number}; artifacts: {equity_curve?: CurvePoint[]; drawdown_curve?: CurvePoint[]}; }
export interface Comparison { left: Backtest; right: Backtest; delta: {sharpe: number; max_drawdown: number; turnover: number}; }

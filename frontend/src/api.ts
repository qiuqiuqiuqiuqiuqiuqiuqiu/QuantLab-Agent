import type {Backtest, Comparison, Project, Run, RunEvent, User} from './types';
let token = localStorage.getItem('quantlab_token');
export function setAccessToken(value: string | null) { token = value; if (value) localStorage.setItem('quantlab_token', value); else localStorage.removeItem('quantlab_token'); }
async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {};
  if (!(options?.body instanceof FormData)) headers['Content-Type'] = 'application/json';
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(path, {...options, headers: {...headers, ...(options?.headers ?? {})}});
  if (!response.ok) { const body = await response.json().catch(() => ({})); throw new Error(body.detail ?? `请求失败：${response.status}`); }
  return response.json() as Promise<T>;
}
export const api = {
  login: (username: string, password: string) => request<{access_token: string; user: User}>('/api/auth/login', {method: 'POST', body: JSON.stringify({username, password})}),
  me: () => request<User>('/api/me'), listRuns: () => request<Run[]>('/api/runs'), getRun: (id: string) => request<Run>(`/api/runs/${id}`),
  listProjects: () => request<Project[]>('/api/projects'), createProject: (name:string, thesis:string, universe:string[], benchmark:string) => request<Project>('/api/projects', {method:'POST', body:JSON.stringify({name,thesis,universe,benchmark})}),
  getEvents: (id: string) => request<RunEvent[]>(`/api/runs/${id}/events`), getBacktest: (id: string) => request<Backtest>(`/api/runs/${id}/backtest`),
  runStarterIndexReport: (id: string) => request<Backtest>(`/api/runs/${id}/starter-index-report`, {method: 'POST'}),
  compare: (left: string, right: string) => request<Comparison>(`/api/experiments/compare?left=${encodeURIComponent(left)}&right=${encodeURIComponent(right)}`),
  createRun: (prompt: string, visibility = 'team') => request<Run>('/api/runs', {method: 'POST', body: JSON.stringify({prompt, visibility})}),
  runTemplate: (template_id: 'stock_check'|'compare'|'strategy', tickers: string[], project_id?: string) => request<Run>('/api/templates/run', {method: 'POST', body: JSON.stringify({template_id, tickers, project_id})}),
  uploadAssets: (files: FileList, runId?: string) => { const form = new FormData(); Array.from(files).forEach(file => form.append('files', file)); if (runId) form.append('run_id', runId); return request<{assets: Array<{filename: string; relative_path: string; bytes: number}>}>('/api/data-assets', {method: 'POST', body: form}); },
};

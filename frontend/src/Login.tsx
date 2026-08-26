import {useState} from 'react';
import {api, setAccessToken} from './api';
import type {User} from './types';

export default function Login({onLogin}: {onLogin: (user: User) => void}) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const submit = async (event: React.FormEvent) => {
    event.preventDefault(); setError(''); setLoading(true);
    try { const result = await api.login(username, password); setAccessToken(result.access_token); onLogin(result.user); }
    catch (reason) { setError(reason instanceof Error ? reason.message : '登录失败'); }
    finally { setLoading(false); }
  };
  return <main className="login-page"><form className="login-card" onSubmit={submit}><p className="eyebrow">QUANTLAB</p><h1>研究任务监控台</h1><p>主要通过 CLI 执行研究任务；此页面用于查看进度和结果。</p><label>用户名<input value={username} onChange={event => setUsername(event.target.value)} autoFocus required /></label><label>密码<input value={password} onChange={event => setPassword(event.target.value)} type="password" required /></label>{error && <p className="error">{error}</p>}<button disabled={loading}>{loading ? '登录中…' : '登录'}</button></form></main>;
}

import {useEffect, useState} from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import Login from './Login';
import {api, setAccessToken} from './api';
import type {User} from './types';

function Root() {
  const [user, setUser] = useState<User | null>(null); const [checking, setChecking] = useState(true);
  useEffect(() => { void api.me().then(setUser).catch(() => setAccessToken(null)).finally(() => setChecking(false)); }, []);
  if (checking) return null;
  return user ? <App user={user} onLogout={() => { setAccessToken(null); setUser(null); }}/> : <Login onLogin={setUser}/>;
}
ReactDOM.createRoot(document.getElementById('root')!).render(<Root/>);

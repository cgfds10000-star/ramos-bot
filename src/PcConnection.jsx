import React, {useState} from 'react';
import {Download, LoaderCircle, Monitor} from 'lucide-react';
import './pc-connection.css';

export function PcSetup() {
  return <section className="pc-setup"><Monitor size={29}/><h2>Entrar com meu IP</h2>
    <p>Faça o login e o 2FA pela conexão do seu PC. Depois, a sessão volta ao site e você pode fechar o conector.</p>
    <ol><li>Baixe e extraia o conector.</li><li>Abra <strong>INICIAR-CONEXAO-PC.cmd</strong>.</li><li>Na aba que abrir, clique em <strong>Conectar ao meu PC</strong> e permita a conexão local.</li></ol>
    <a className="primary-button" href="/downloads/Ramos-Conector-PC.zip" download><Download size={18}/>Baixar conector do PC</a>
    <p className="pc-note">Requer Python 3.11 ou superior. Só o login usa o PC; a execução continua no Railway.</p>
  </section>;
}

export function PcGate({api, cloudApi, connection, onConnected}) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(connection.error || '');
  const [needsPassword, setNeedsPassword] = useState(false);
  const [password, setPassword] = useState('');
  async function connect(event) {
    event.preventDefault();
    if (pending || !connection.token) return;
    setPending(true); setError('');
    try {
      const state = await api.state();
      if (!state.connection?.pc_login) throw new Error('Abra a versão atualizada do Conector PC.');
      if (needsPassword) {
        const entered = password; setPassword('');
        await cloudApi.post('/access/login', {password: entered});
      }
      await cloudApi.state();
      onConnected();
    } catch (failure) {
      if (failure.locked) setNeedsPassword(true);
      setError(failure.message);
    } finally {setPending(false);}
  }
  return <main className="pc-gate"><form className="pc-gate-card" onSubmit={connect}><Monitor size={36}/><h1>Conectar ao meu PC</h1>
    <p>O login e o 2FA usarão sua conexão. Depois, a sessão será enviada ao site para continuar no Railway.</p>
    {needsPassword && <label>Senha do painel<input type="password" autoComplete="current-password" value={password} onChange={event => setPassword(event.target.value)} required/></label>}
    <button className="primary-button" disabled={pending || !connection.token}>{pending ? <LoaderCircle className="spin" size={20}/> : <Monitor size={20}/>}Conectar ao meu PC</button>
    <p className="pc-note">Permita a conexão local se o navegador solicitar.</p>
    {error && <div className="error-banner" role="alert">{error}</div>}
  </form></main>;
}

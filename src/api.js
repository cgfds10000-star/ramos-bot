const trimBase = value => String(value || '').trim().replace(/\/+$/, '');

export class RamosApi {
  constructor(base = '', {token = ''} = {}) {
    this.base = trimBase(base);
    this.csrf = '';
    this.serverClock = null;
    this.token = token;
    this.local = this.base === 'http://127.0.0.1:8765';
  }

  async request(path, options = {}) {
    const started = performance.now();
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 12000);
    try {
      const response = await fetch(`${this.base}/api${path}`, {
        ...options,
        headers: { 'Content-Type': 'application/json', ...(this.token ? {Authorization: 'Bearer ' + this.token} : {}), ...(this.csrf ? { 'X-Ramos-CSRF': this.csrf } : {}), ...options.headers },
        ...(this.local ? {targetAddressSpace: 'loopback'} : {}),
        signal: controller.signal,
        cache: 'no-store',
      });
      const type = response.headers.get('content-type') || '';
      if (!type.includes('application/json')) throw new Error('O servidor do Ramos Bot não está disponível neste endereço.');
      const body = await response.json();
      if (!response.ok) {
        const failure = new Error(body.error || 'Não foi possível concluir a operação.');
        failure.locked = Boolean(body.locked);
        failure.status = response.status;
        throw failure;
      }
      if (body.csrf) this.csrf = body.csrf;
      if (Number.isFinite(body.server_time_ms) && body.server_time_ms > 0) {
        const received = performance.now();
        this.serverClock = {time: body.server_time_ms + (received - started) / 2, received};
      }
      return body;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('O servidor demorou para responder. Tente atualizar a conexão.');
      if (error instanceof TypeError) throw new Error(this.local ? 'Abra o Conector PC e permita a conexão local no navegador. O login não será enviado pelo Railway.' : 'Sem conexão com o servidor do Ramos Bot.');
      throw error;
    } finally { clearTimeout(timer); }
  }

  state() { return this.request('/state'); }
  serverNow() { return this.serverClock ? this.serverClock.time + performance.now() - this.serverClock.received : Date.now(); }
  post(path, body = {}) { return this.request(path, { method: 'POST', body: JSON.stringify(body) }); }
  remove(path) { return this.request(path, { method: 'DELETE', body: '{}' }); }
}

export function parseCommand(input) {
  const tokens = String(input).trim().match(/"[^"]*"|'[^']*'|\S+/g) || [];
  const parts = tokens.map(value => /^['"]/.test(value) ? value.slice(1, -1) : value);
  const command = (parts.shift() || '').toLowerCase();
  if (!command) return null;
  if (['ajuda', 'help', 'limpar', 'clear', 'status', 'login', 'sair'].includes(command)) {
    if (parts.length) throw new Error('Esse comando não recebe argumentos.');
    return { command };
  }
  if (!['buscar', 'perfil', 'seguir', 'curtir'].includes(command)) throw new Error('Comando desconhecido. Digite ajuda para ver as opções.');
  let limit = 10, byId = false, general = false;
  const targets = [];
  const flags = new Set();
  for (let i = 0; i < parts.length; i++) {
    const part = parts[i];
    if (!part.startsWith('--')) { targets.push(part); continue; }
    if (flags.has(part)) throw new Error('Não repita as opções do comando.');
    flags.add(part);
    if (part === '--limite' && command === 'buscar') {
      limit = Number(parts[++i]);
      if (!Number.isInteger(limit) || limit < 1 || limit > 50) throw new Error('O limite precisa estar entre 1 e 50.');
    } else if (part === '--id' && ['perfil', 'seguir'].includes(command)) {
      byId = true; targets.push(parts[++i] || '');
    } else if (part === '--geral' && command === 'buscar') general = true;
    else throw new Error('Opção inválida. Digite ajuda para ver os comandos.');
  }
  const target = targets.join(' ').trim();
  if (!target) throw new Error('Informe o perfil, termo de busca ou link da publicação.');
  if (command !== 'buscar' && targets.length !== 1) throw new Error('Informe apenas um perfil ou link.');
  if (byId && !/^[1-9][0-9]{0,29}$/.test(target)) throw new Error('Informe um ID numérico válido.');
  return { command, target, limit, ...(byId ? { by_id: true } : {}), ...(general ? { general: true } : {}) };
}

export function selectSession(state, selected) {
  const sessions = state?.sessions || [];
  return sessions.find(item => item.id === selected) || sessions[0] || null;
}

export function sessionBusy(session) {
  return ['connecting', 'running', 'awaiting_input'].includes(session?.status);
}

export const statusLabel = status => ({
  disconnected: 'Aguardando autenticação', connecting: 'Autenticando conta', connected: 'Sessão ativa',
  running: 'Executando comando', awaiting_input: 'Verificação necessária', error: 'Conexão interrompida',
}[status] || 'Aguardando autenticação');

export function executionActive(run) { return ['running', 'waiting', 'recovering'].includes(run?.status); }

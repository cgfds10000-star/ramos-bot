export const PC_BASE = 'http://127.0.0.1:8765';
const TOKEN = /^[A-Za-z0-9_-]{32,128}$/;
const KEY = 'ramos.pc.connection';

export function consumePcConnection(location, storage, history) {
  const fragment = new URLSearchParams(location.hash.replace(/^#/, ''));
  const incoming = fragment.get('ramos_pc');
  if (incoming !== null) {
    fragment.delete('ramos_pc');
    const remaining = fragment.toString();
    history.replaceState(null, '', location.pathname + location.search + (remaining ? '#' + remaining : ''));
    if (!TOKEN.test(incoming)) return {error: 'Conexão inválida. Abra o conector novamente.'};
    try { storage.setItem(KEY, incoming); } catch {}
    return {token: incoming};
  }
  try {
    const token = storage.getItem(KEY);
    return token && TOKEN.test(token) ? {token} : null;
  } catch {return null;}
}

export function clearPcConnection(storage) {
  storage.removeItem(KEY);
}

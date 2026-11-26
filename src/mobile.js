const sessionIdPattern = /^[a-f0-9-]{36}$/i;

export function isNativeApp(bridge = globalThis.RamosApp) {
  return typeof bridge?.postMessage === 'function';
}

export function appMessage(message) {
  const bridge = globalThis.RamosApp;
  if (!isNativeApp(bridge)) return false;
  bridge.postMessage(JSON.stringify(message));
  return true;
}

export function requestMobileLogin(sessionId) {
  if (!sessionIdPattern.test(sessionId)) throw new Error('Sessão inválida. Atualize o painel.');
  if (appMessage({action: 'login', session_id: sessionId})) return null;
  return `ramosbot://login?session_id=${encodeURIComponent(sessionId)}`;
}

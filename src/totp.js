// RFC 4648 Base32 + RFC 6238, HMAC-SHA-1, intervalo de 30 segundos.
// Preview local; o worker usa o mesmo nucleo Python para gerar o codigo no envio.
const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
const paddingByRemainder = {0: 0, 2: 6, 4: 4, 5: 3, 7: 1};

export function decodeBase32(value) {
  const input = String(value || '').replace(/[\s-]+/g, '').toUpperCase();
  const match = /^([A-Z2-7]{16,128})(={0,6})$/.exec(input);
  if (!match) throw new Error('Informe uma chave Base32 válida, com pelo menos 16 caracteres.');
  const [, body, padding] = match;
  const expectedPadding = paddingByRemainder[body.length % 8];
  if (expectedPadding === undefined || (padding && padding.length !== expectedPadding)) {
    throw new Error('A chave Base32 está incompleta ou tem preenchimento inválido.');
  }
  const bytes = [];
  let buffer = 0, bits = 0;
  for (const character of body) {
    buffer = (buffer << 5) | alphabet.indexOf(character);
    bits += 5;
    if (bits >= 8) {
      bits -= 8;
      bytes.push((buffer >>> bits) & 255);
      buffer &= (1 << bits) - 1;
    }
  }
  if (bits && buffer !== 0) throw new Error('A chave Base32 está incompleta.');
  return new Uint8Array(bytes);
}

export function acceptsAuthenticatorSecret(challengeKind) {
  return challengeKind === 'authenticator' || challengeKind === 'two_factor';
}

export function verificationInputKind(value, challengeKind) {
  const clean = String(value || '').replace(/[\s-]+/g, '');
  const digits = challengeKind === 'authenticator' ? /^[0-9]{6}$/ : /^(?:[0-9]{6}|[0-9]{8})$/;
  if (digits.test(clean)) return 'code';
  if (!acceptsAuthenticatorSecret(challengeKind)) return null;
  try { const bytes = decodeBase32(clean); bytes.fill(0); return 'secret'; }
  catch { return null; }
}

export async function generateTotp(secret, timeMs = Date.now(), digits = 6) {
  if (![6, 8].includes(digits) || !Number.isFinite(timeMs) || timeMs < 0) throw new Error('Parâmetros TOTP inválidos.');
  if (!globalThis.crypto?.subtle) throw new Error('Abra o site por HTTPS para gerar o código 2FA.');
  const bytes = decodeBase32(secret);
  try {
    const key = await crypto.subtle.importKey('raw', bytes, {name: 'HMAC', hash: 'SHA-1'}, false, ['sign']);
    const counter = new Uint8Array(8);
    new DataView(counter.buffer).setBigUint64(0, BigInt(Math.floor(timeMs / 30000)), false);
    const digest = new Uint8Array(await crypto.subtle.sign('HMAC', key, counter));
    const offset = digest[digest.length - 1] & 15;
    const binary = ((digest[offset] & 127) << 24) | (digest[offset + 1] << 16) | (digest[offset + 2] << 8) | digest[offset + 3];
    return String(binary % (10 ** digits)).padStart(digits, '0');
  } finally { bytes.fill(0); }
}

function pause(milliseconds, signal) {
  return new Promise((resolve, reject) => {
    signal?.throwIfAborted();
    const abort = () => { clearTimeout(timer); reject(new DOMException('Verificação cancelada.', 'AbortError')); };
    const timer = setTimeout(() => { signal?.removeEventListener('abort', abort); resolve(); }, milliseconds);
    signal?.addEventListener('abort', abort, {once: true});
  });
}

export async function resolveVerificationCode(value, challengeKind, {now = Date.now, sleep = pause, signal} = {}) {
  signal?.throwIfAborted();
  const kind = verificationInputKind(value, challengeKind);
  if (kind === 'code') return String(value).replace(/[\s-]+/g, '');
  if (kind !== 'secret') throw new Error(acceptsAuthenticatorSecret(challengeKind)
    ? 'Informe a chave secreta Base32 ou o código 2FA solicitado pelo Instagram.'
    : 'Informe somente o código numérico recebido por SMS ou e-mail.');
  const remaining = 30000 - (now() % 30000);
  if (remaining <= 3000) await sleep(remaining + 50, signal);
  signal?.throwIfAborted();
  const code = await generateTotp(value, now());
  signal?.throwIfAborted();
  return code;
}

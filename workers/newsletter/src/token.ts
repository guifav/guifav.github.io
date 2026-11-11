// Link de confirmação assinado (issue #21, garantia A4). Sem banco: o link carrega o email e a
// validade, e o HMAC-SHA256 com o segredo do Worker prova que fomos nós que o emitimos.
const enc = new TextEncoder();
export const TTL_MS = 48 * 3600e3;
export const MIN_SECRET = 32;

function b64url(bytes: Uint8Array): string {
  let s = '';
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function fromB64url(s: string): Uint8Array<ArrayBuffer> | null {
  if (!/^[A-Za-z0-9_-]+$/.test(s)) return null;
  try {
    const bin = atob(s.replace(/-/g, '+').replace(/_/g, '/') + '==='.slice((s.length + 3) % 4));
    const out = new Uint8Array(new ArrayBuffer(bin.length));
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  } catch {
    return null;
  }
}

async function hmacKey(secret: string): Promise<CryptoKey> {
  if (!secret || secret.length < MIN_SECRET) throw new Error('CONFIRM_SECRET ausente ou curto demais');
  return crypto.subtle.importKey('raw', enc.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign', 'verify']);
}

const payload = (email: string, exp: number) => enc.encode(`v1\n${email}\n${exp}`);

/** Query string do link: e (email em base64url), x (expira em, segundos Unix), s (assinatura). */
export async function signLink(email: string, nowMs: number, secret: string): Promise<string> {
  const exp = Math.floor((nowMs + TTL_MS) / 1000);
  const sig = new Uint8Array(await crypto.subtle.sign('HMAC', await hmacKey(secret), payload(email, exp)));
  return new URLSearchParams({ e: b64url(enc.encode(email)), x: String(exp), s: b64url(sig) }).toString();
}

/** Email do link, ou null se faltar campo, estiver malformado, expirado ou com assinatura errada. */
export async function verifyLink(p: URLSearchParams, secret: string, nowMs: number): Promise<string | null> {
  const e = p.get('e');
  const x = p.get('x');
  const s = p.get('s');
  if (!e || !x || !s || !/^\d{1,12}$/.test(x)) return null;
  const exp = Number(x);
  if (exp * 1000 < nowMs) return null;
  const eb = fromB64url(e);
  const sb = fromB64url(s);
  if (!eb || !sb) return null;
  let email: string;
  try {
    email = new TextDecoder('utf-8', { fatal: true }).decode(eb);
  } catch {
    return null;
  }
  // crypto.subtle.verify compara em tempo constante.
  const ok = await crypto.subtle.verify('HMAC', await hmacKey(secret), sb, payload(email, exp));
  return ok ? email : null;
}

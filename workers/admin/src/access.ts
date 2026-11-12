// Verificação do JWT do Cloudflare Access (defesa em profundidade, issue #40).
// O Access já bloqueia quem não fez login; o Worker confere de novo a assinatura (RS256, chaves públicas do time),
// o emissor, a audiência da aplicação, a validade e o email. Sem JWT válido, nada do painel responde.

export interface AccessConfig {
  teamDomain: string; // ex.: guifav.cloudflareaccess.com
  aud: string; // tag de audiência da aplicação do Access
  allowedEmails: string[]; // minúsculas
}

type Jwk = JsonWebKey & { kid?: string };
type Fetch = typeof fetch;

const LEEWAY_S = 60;
const CERTS_TTL_MS = 60 * 60 * 1000;
let certsCache: { url: string; at: number; keys: Jwk[] } | null = null;

export function resetCertsCache(): void {
  certsCache = null;
}

function b64urlToBytes(s: string): Uint8Array {
  const b64 = s.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - (s.length % 4)) % 4);
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function decodeJson(part: string): Record<string, unknown> | null {
  try {
    const v = JSON.parse(new TextDecoder().decode(b64urlToBytes(part)));
    return v && typeof v === 'object' && !Array.isArray(v) ? v : null;
  } catch {
    return null;
  }
}

async function certs(cfg: AccessConfig, fetchFn: Fetch, now: number, refresh: boolean): Promise<Jwk[]> {
  const url = `https://${cfg.teamDomain}/cdn-cgi/access/certs`;
  if (!refresh && certsCache && certsCache.url === url && now - certsCache.at < CERTS_TTL_MS) return certsCache.keys;
  const res = await fetchFn(url);
  if (!res.ok) throw new Error('certs');
  const body = (await res.json()) as { keys?: unknown };
  const keys = Array.isArray(body.keys) ? (body.keys as Jwk[]) : [];
  certsCache = { url, at: now, keys };
  return keys;
}

/** Email autenticado pelo Access, ou null. Nunca lança: qualquer falha vale como não autenticado. */
export async function accessEmail(req: Request, cfg: AccessConfig, fetchFn: Fetch, now: number): Promise<string | null> {
  try {
    if (!cfg.teamDomain || !cfg.aud || cfg.allowedEmails.length === 0) return null;
    const token = req.headers.get('cf-access-jwt-assertion') ?? '';
    const parts = token.split('.');
    if (parts.length !== 3) return null;
    const header = decodeJson(parts[0]);
    const payload = decodeJson(parts[1]);
    if (!header || !payload || header.alg !== 'RS256' || typeof header.kid !== 'string') return null;

    let keys = await certs(cfg, fetchFn, now, false);
    let jwk = keys.find((k) => k.kid === header.kid);
    if (!jwk) {
      // Rotação de chave: busca de novo uma vez antes de recusar.
      keys = await certs(cfg, fetchFn, now, true);
      jwk = keys.find((k) => k.kid === header.kid);
    }
    if (!jwk || jwk.kty !== 'RSA') return null;
    const key = await crypto.subtle.importKey('jwk', { kty: 'RSA', n: jwk.n, e: jwk.e, alg: 'RS256', ext: true },
      { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256' }, false, ['verify']);
    const ok = await crypto.subtle.verify('RSASSA-PKCS1-v1_5', key, b64urlToBytes(parts[2]),
      new TextEncoder().encode(`${parts[0]}.${parts[1]}`));
    if (!ok) return null;

    const t = Math.floor(now / 1000);
    if (typeof payload.exp !== 'number' || payload.exp + LEEWAY_S < t) return null;
    if (typeof payload.nbf === 'number' && payload.nbf - LEEWAY_S > t) return null;
    if (payload.iss !== `https://${cfg.teamDomain}`) return null;
    const aud = Array.isArray(payload.aud) ? payload.aud : [payload.aud];
    if (!aud.includes(cfg.aud)) return null;
    const email = typeof payload.email === 'string' ? payload.email.trim().toLowerCase() : '';
    return email && cfg.allowedEmails.includes(email) ? email : null;
  } catch {
    return null;
  }
}

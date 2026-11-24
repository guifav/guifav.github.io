// Link de confirmação assinado (issue #21, garantia A4). Sem banco: o link carrega o email e a
// validade, e o HMAC-SHA256 com o segredo do Worker prova que fomos nós que o emitimos.
// Desde a issue #114, o link carrega também a origem da visita em que a pessoa se inscreveu (parâmetro `o`, payload v2),
// para a conversão newsletter_confirm, que só nasce no clique, gravar a origem do pedido. Os links v1 (sem `o`) emitidos
// antes do deploy continuam valendo até expirar, sem origem.
import { decodeOrigem, encodeOrigem, type Origem } from './origem.ts';

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

const payload = (email: string, exp: number, o: string | null) =>
  enc.encode(o === null ? `v1\n${email}\n${exp}` : `v2\n${email}\n${exp}\n${o}`);

/**
 * Query string do link: e (email em base64url), x (expira em, segundos Unix), o (origem, issue #114; só com `origem`) e
 * s (assinatura, que cobre a origem: ela não muda sem invalidar o link).
 */
export async function signLink(email: string, nowMs: number, secret: string, origem?: Origem): Promise<string> {
  const exp = Math.floor((nowMs + TTL_MS) / 1000);
  const o = origem ? encodeOrigem(origem) : null;
  const sig = new Uint8Array(await crypto.subtle.sign('HMAC', await hmacKey(secret), payload(email, exp, o)));
  return new URLSearchParams({ e: b64url(enc.encode(email)), x: String(exp), ...(o === null ? {} : { o }), s: b64url(sig) }).toString();
}

// Link de download dos materiais do canal (planilha, mapa de decisão). Payload com outro prefixo (m1) e com o
// arquivo: um link de confirmação nunca vale como download, e o link de um arquivo nunca abre outro.
export const MATERIAL_TTL_MS = 7 * 24 * 3600e3;
const materialPayload = (file: string, email: string, exp: number) => enc.encode(`m1\n${file}\n${email}\n${exp}`);

/** Query string do download: f (arquivo), e (email em base64url), x (expira em, segundos Unix), s (assinatura). */
/** Validade (segundos Unix) dos links de um pedido feito em nowMs: a mesma em todos os arquivos dele. */
export const materialExp = (nowMs: number) => Math.floor((nowMs + MATERIAL_TTL_MS) / 1000);

export async function signMaterialLink(file: string, email: string, nowMs: number, secret: string): Promise<string> {
  const exp = materialExp(nowMs);
  const sig = new Uint8Array(await crypto.subtle.sign('HMAC', await hmacKey(secret), materialPayload(file, email, exp)));
  return new URLSearchParams({ f: file, e: b64url(enc.encode(email)), x: String(exp), s: b64url(sig) }).toString();
}

/** Arquivo e email do link de download, ou null se faltar campo, estiver malformado, expirado ou com assinatura errada. */
export async function verifyMaterialLink(p: URLSearchParams, secret: string, nowMs: number): Promise<{ file: string; email: string } | null> {
  const f = p.get('f');
  const e = p.get('e');
  const x = p.get('x');
  const s = p.get('s');
  if (!f || !/^[a-z0-9-]{1,64}$/.test(f) || !e || !x || !s || !/^\d{1,12}$/.test(x)) return null;
  const exp = Number(x);
  if (exp * 1000 < nowMs) return null;
  const eb = fromB64url(e);
  const sb = fromB64url(s);
  // Só a forma canônica da assinatura vale: os bits de sobra do último caractere mudam a string sem mudar os bytes, e
  // cada variante seria um link diferente para o mesmo pedido (e uma conversão a mais, issue #97).
  if (!eb || !sb || b64url(sb) !== s) return null;
  let email: string;
  try {
    email = new TextDecoder('utf-8', { fatal: true }).decode(eb);
  } catch {
    return null;
  }
  const ok = await crypto.subtle.verify('HMAC', await hmacKey(secret), sb, materialPayload(f, email, exp));
  return ok ? { file: f, email } : null;
}

/** Email do link, ou null se faltar campo, estiver malformado, expirado ou com assinatura errada. */
export async function verifyLink(p: URLSearchParams, secret: string, nowMs: number): Promise<string | null> {
  return (await verifyConfirmLink(p, secret, nowMs))?.email ?? null;
}

/**
 * Email e origem do link de confirmação, ou null como no verifyLink. Link v1 (sem `o`): origem nula. Link v2 com a
 * assinatura certa e uma origem que não casa mais com as listas (só se elas mudarem depois de o link sair): a inscrição
 * vale, com a origem "desconhecida".
 */
export async function verifyConfirmLink(p: URLSearchParams, secret: string, nowMs: number):
  Promise<{ email: string; origem: Origem | null } | null> {
  const o = p.get('o');
  if (o !== null && (o.length > 512 || p.getAll('o').length !== 1)) return null;
  const e = p.get('e');
  const x = p.get('x');
  const s = p.get('s');
  if (!e || !x || !s || !/^\d{1,12}$/.test(x)) return null;
  const exp = Number(x);
  if (exp * 1000 < nowMs) return null;
  const eb = fromB64url(e);
  const sb = fromB64url(s);
  // Só a forma canônica da assinatura vale: os bits de sobra do último caractere mudam a string sem mudar os bytes, e
  // cada variante seria um link diferente para o mesmo pedido (e uma conversão a mais, issue #97).
  if (!eb || !sb || b64url(sb) !== s) return null;
  let email: string;
  try {
    email = new TextDecoder('utf-8', { fatal: true }).decode(eb);
  } catch {
    return null;
  }
  // crypto.subtle.verify compara em tempo constante.
  const ok = await crypto.subtle.verify('HMAC', await hmacKey(secret), sb, payload(email, exp, o));
  if (!ok) return null;
  if (o === null) return { email, origem: null };
  return { email, origem: decodeOrigem(o) ?? { canal: 'desconhecida', fonte: null, utmSource: null, utmMedium: null, utmCampaign: null,
    entrada: null } };
}

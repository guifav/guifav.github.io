// Webhook do Resend para o heating de contatos (épico #197, corte 1): POST /resend/webhook.
// Recebe email.delivered, email.opened, email.clicked e email.complained de todos os envios da conta, verifica a
// assinatura svix via WebCrypto (sem dependências), filtra cliques que não pontuam (links inválidos, descadastro e
// página /prioridade), respeita a oposição em `heating_optout` e grava de forma síncrona em `engagement_events` antes
// de responder 200. Em falha do D1, devolve 500 para o Resend reenviar, com log curto sem email.
import { normalizeEmail } from './email.ts';
import { heatingOptoutHmac } from './forget.ts';
import { motivo, type D1Like } from './conversoes.ts';
import { MIN_SECRET } from './token.ts';

const enc = new TextEncoder();

export const MAX_WEBHOOK_BODY = 65_536; // 64 KiB
const SVIX_TOLERANCE_MS = 5 * 60 * 1000;
const SVIX_MAX_SIGNATURES = 16;
const SVIX_ID_RE = /^[0-9A-Za-z_-]{1,100}$/;
const SVIX_TS_RE = /^\d{1,12}$/;
const REF_RE = /^[0-9A-Za-z:_.-]{1,128}$/;
const ISO_RE = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}(?::?\d{2})?)$/;
const ISO_UTC_MS_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/;

const EVENT_SIGNALS: Record<string, string> = {
  'email.delivered': 'email_entregue',
  'email.opened': 'email_aberto',
  'email.clicked': 'email_clicado',
  'email.complained': 'email_spam',
};

const plain = (status: number, body: string, extra: Record<string, string> = {}) =>
  new Response(body, { status, headers: { 'content-type': 'text/plain; charset=utf-8', 'cache-control': 'no-store', ...extra } });

const json = (status: number, body: Record<string, unknown>) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' } });

function decodeBase64(s: string): Uint8Array<ArrayBuffer> | null {
  if (!s || s.length % 4 === 1 || !/^[A-Za-z0-9+/]+={0,2}$/.test(s)) return null;
  if (s.includes('=') && s.length % 4 !== 0) return null;
  const padded = s.length % 4 === 0 ? s : s + '='.repeat(4 - (s.length % 4));
  try {
    const bin = atob(padded);
    if (btoa(bin) !== padded) return null;
    const out = new Uint8Array(new ArrayBuffer(bin.length));
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  } catch {
    return null;
  }
}

/** Decodifica o segredo no formato do Resend/svix (`whsec_` + base64, mínimo de 16 bytes), ou null se inválido. */
export function parseWebhookSecret(secret: string | undefined): Uint8Array<ArrayBuffer> | null {
  if (typeof secret !== 'string' || !secret.startsWith('whsec_')) return null;
  const raw = decodeBase64(secret.slice('whsec_'.length));
  if (!raw || raw.byteLength < 16) return null;
  return raw;
}

/** Lê o corpo da requisição até `maxBytes`; devolve null se passar do limite (antes de ler quando Content-Length já excede). */
export async function readBoundedBytes(req: Request, maxBytes: number): Promise<Uint8Array<ArrayBuffer> | null> {
  const cl = req.headers.get('content-length');
  if (cl !== null) {
    const n = Number(cl);
    if (Number.isFinite(n) && n > maxBytes) return null;
  }
  if (!req.body) return new Uint8Array(new ArrayBuffer(0));
  const reader = req.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      if (!value) continue;
      total += value.byteLength;
      if (total > maxBytes) {
        await reader.cancel().catch(() => {});
        return null;
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const out = new Uint8Array(new ArrayBuffer(total));
  let offset = 0;
  for (const c of chunks) {
    out.set(c, offset);
    offset += c.byteLength;
  }
  return out;
}

async function sha256Hex(s: string): Promise<string> {
  const h = new Uint8Array(await crypto.subtle.digest('SHA-256', enc.encode(s)));
  return [...h].map((b) => b.toString(16).padStart(2, '0')).join('');
}

async function verifySvixSignature(
  headers: Headers,
  rawBody: string,
  secretBytes: Uint8Array<ArrayBuffer>,
  nowMs: number,
): Promise<string | null> {
  const svixId = headers.get('svix-id');
  const svixTs = headers.get('svix-timestamp');
  const svixSig = headers.get('svix-signature');
  if (!svixId || !svixTs || !svixSig) return null;
  if (!SVIX_ID_RE.test(svixId) || !SVIX_TS_RE.test(svixTs)) return null;

  const tsSec = Number(svixTs);
  if (!Number.isSafeInteger(tsSec) || Math.abs(nowMs - tsSec * 1000) > SVIX_TOLERANCE_MS) return null;

  const tokens = svixSig.trim().split(/\s+/).filter(Boolean);
  if (tokens.length === 0 || tokens.length > SVIX_MAX_SIGNATURES) return null;

  const key = await crypto.subtle.importKey('raw', secretBytes, { name: 'HMAC', hash: 'SHA-256' }, false, ['verify']);
  const signedPayload = enc.encode(`${svixId}.${svixTs}.${rawBody}`);

  for (const token of tokens) {
    if (!token.startsWith('v1,')) continue;
    const sigBytes = decodeBase64(token.slice(3));
    if (!sigBytes || sigBytes.byteLength !== 32) continue;
    // crypto.subtle.verify compara em tempo constante.
    if (await crypto.subtle.verify('HMAC', key, sigBytes, signedPayload)) return svixId;
  }
  return null;
}

function normalizeIsoUtc(raw: unknown): string | null {
  if (typeof raw !== 'string') return null;
  const m = ISO_RE.exec(raw.trim());
  if (!m) return null;
  const y = Number(m[1]);
  const mo = Number(m[2]);
  const d = Number(m[3]);
  const hh = Number(m[4]);
  const mm = Number(m[5]);
  const ss = Number(m[6]);
  if (mo < 1 || mo > 12 || d < 1 || d > 31 || hh > 23 || mm > 59 || ss > 59) return null;
  const cal = new Date(Date.UTC(y, mo - 1, d));
  if (cal.getUTCFullYear() !== y || cal.getUTCMonth() !== mo - 1 || cal.getUTCDate() !== d) return null;

  const ms = Number((m[7] ?? '').slice(0, 3).padEnd(3, '0'));
  const tz = m[8];
  let offsetMs = 0;
  if (tz !== 'Z') {
    const sign = tz[0] === '-' ? -1 : 1;
    const rest = tz.slice(1).replace(':', '');
    const tzH = Number(rest.slice(0, 2));
    const tzM = rest.length === 4 ? Number(rest.slice(2, 4)) : 0;
    if (tzH > 23 || tzM > 59) return null;
    offsetMs = sign * (tzH * 60 + tzM) * 60_000;
  }
  const utcMs = Date.UTC(y, mo - 1, d, hh, mm, ss, ms) - offsetMs;
  if (!Number.isFinite(utcMs)) return null;
  const iso = new Date(utcMs).toISOString();
  return ISO_UTC_MS_RE.test(iso) ? iso : null;
}

function clickIgnoredReason(link: string): string | null {
  let url: URL;
  try {
    url = new URL(link);
  } catch {
    return 'link_invalido';
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') return 'link_invalido';

  const host = url.hostname.toLowerCase().replace(/\.$/, '');
  if (host === 'resend.com' || host.endsWith('.resend.com') || host === 'resend.dev' || host.endsWith('.resend.dev')) {
    return 'descadastro';
  }

  const rawPath = url.pathname.toLowerCase();
  let decodedPath = rawPath;
  try {
    decodedPath = decodeURIComponent(rawPath).toLowerCase();
  } catch {
    // mantém o caminho bruto quando a codificação percentual é inválida
  }
  if (
    rawPath.includes('unsubscribe') || rawPath.includes('descadastr')
    || decodedPath.includes('unsubscribe') || decodedPath.includes('descadastr')
  ) {
    return 'descadastro';
  }

  if (
    (host === 'guifav.dev' || host === 'insights.guifav.dev')
    && (rawPath === '/prioridade' || rawPath.startsWith('/prioridade/') || decodedPath === '/prioridade' || decodedPath.startsWith('/prioridade/'))
  ) {
    return 'oposicao';
  }

  return null;
}

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

export interface ResendWebhookEnv {
  RESEND_HEATING_WEBHOOK_SECRET?: string;
  FORGET_HMAC_KEY: string;
  DB: D1Like;
}

export async function handleResendWebhook(
  req: Request,
  env: ResendWebhookEnv,
  now: () => number,
): Promise<Response> {
  if (req.method !== 'POST') return plain(405, 'método não permitido', { allow: 'POST' });

  const secretBytes = parseWebhookSecret(env.RESEND_HEATING_WEBHOOK_SECRET);
  if (!secretBytes) return plain(503, 'webhook não configurado');
  if (!env.FORGET_HMAC_KEY || env.FORGET_HMAC_KEY.length < MIN_SECRET) return plain(500, 'Worker sem configuração');

  const rawBytes = await readBoundedBytes(req, MAX_WEBHOOK_BODY);
  if (rawBytes === null) return plain(413, 'pedido grande demais');

  let rawBody: string;
  try {
    rawBody = new TextDecoder('utf-8', { fatal: true }).decode(rawBytes);
  } catch {
    return plain(400, 'corpo inválido');
  }

  const svixId = await verifySvixSignature(req.headers, rawBody, secretBytes, now());
  if (!svixId) return plain(401, 'assinatura inválida');

  let payload: unknown;
  try {
    payload = JSON.parse(rawBody);
  } catch {
    return plain(400, 'JSON inválido');
  }
  if (!isPlainObject(payload)) return plain(400, 'JSON inválido');

  const eventType = payload.type;
  if (typeof eventType !== 'string' || eventType.length === 0 || eventType.length > 100) {
    return plain(400, 'tipo ausente ou inválido');
  }

  const signal = EVENT_SIGNALS[eventType];
  if (!signal) return json(200, { ok: true, ignorado: eventType });

  const occurredAt = normalizeIsoUtc(payload.created_at);
  if (!occurredAt) return plain(400, 'data do evento inválida');

  const data = payload.data;
  if (!isPlainObject(data)) return plain(400, 'campo data ausente ou inválido');

  const emailId = data.email_id;
  if (typeof emailId !== 'string' || !REF_RE.test(emailId)) return plain(400, 'email_id inválido');

  const rawTo = data.to;
  let toList: string[];
  if (typeof rawTo === 'string') {
    toList = [rawTo];
  } else if (Array.isArray(rawTo) && rawTo.length > 0 && rawTo.every((x): x is string => typeof x === 'string')) {
    toList = rawTo;
  } else {
    return plain(400, 'destinatários ausentes ou inválidos');
  }

  if (eventType === 'email.clicked') {
    if (!isPlainObject(data.click) || typeof data.click.link !== 'string') {
      return plain(400, 'link do clique ausente ou inválido');
    }
    const reason = clickIgnoredReason(data.click.link);
    if (reason) return json(200, { ok: true, ignorado: reason });
  }

  const emails = new Set<string>();
  let invalidCount = 0;
  for (const item of toList) {
    const norm = normalizeEmail(item);
    if (norm) emails.add(norm);
    else invalidCount++;
  }
  if (invalidCount > 0) {
    console.error(`webhook resend: ${invalidCount} destinatário(s) inválido(s) ignorado(s)`);
  }

  const recordedAt = new Date(now()).toISOString();
  let gravados = 0;
  try {
    for (const email of emails) {
      const hmac = await heatingOptoutHmac(email, env.FORGET_HMAC_KEY);
      const optedOut = await env.DB.prepare('SELECT 1 FROM heating_optout WHERE email_hmac = ?').bind(hmac).first();
      if (optedOut) continue;

      const id = `rs:${await sha256Hex(`${svixId}\n${email}`)}`;
      // Só a repetição do id (reentrega do Resend) é ignorada; violação de CHECK continua virando erro 500.
      await env.DB
        .prepare(`INSERT INTO engagement_events (id, email, signal, source, ref, occurred_at, recorded_at)
          VALUES (?, ?, ?, 'resend', ?, ?, ?) ON CONFLICT(id) DO NOTHING`)
        .bind(id, email, signal, emailId, occurredAt, recordedAt)
        .run();
      gravados++;
    }
  } catch (e) {
    console.error(`webhook resend falhou: ${motivo(e)}`);
    return plain(500, 'falha ao gravar evento');
  }

  return json(200, { ok: true, gravados });
}

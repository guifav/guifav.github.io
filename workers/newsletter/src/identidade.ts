// Registro do vínculo uid -> email das sessões logadas (épico #197, corte 2): POST /identidade.
// Verifica o ID token do Firebase (RS256 via WebCrypto contra o JWKS do securetoken, cacheado em memória pelo
// max-age do Cache-Control, com prazo de 3 s na busca), exige iss, aud, exp no futuro (folga zero), iat no passado
// (folga de 5 min), sub válido, email_verified === true, email válido e firebase.tenant === "guifav-dev-g6dp4".
// Respeita oposição global em heating_optout (409 sem gravar, para o site desligar o vínculo naquele navegador) e faz
// upsert em site_identities por uid (204). Grava também a conversão "login no site" (`site_login`, comprovada: o Google
// confirma o email) uma vez por pessoa, com a origem da visita do corpo (campos do formulário, até 1 KiB), para o
// conversoes_sync criar a Pessoa no Twenty (decisão do Gui, 27/11).
// CORS restrito a env.SITE_ORIGIN (https://guifav.dev), sem credenciais/cookies, corpo opcional urlencoded de até 1 KiB com a
// origem da visita (acima disso 413; UTF-8 inválido 400) e limite por IP.
import { normalizeEmail } from './email.ts';
import { heatingOptoutHmac } from './forget.ts';
import { motivo, type D1Like } from './conversoes.ts';
import { parseOrigem } from './origem.ts';
import { readBoundedBytes } from './resend-webhook.ts';
import { MIN_SECRET } from './token.ts';

const enc = new TextEncoder();
/** Corpo opcional: os seis campos da origem da visita, urlencoded (bem abaixo disso na prática). */
export const IDENTIDADE_BODY_MAX = 1024;

async function sha256Hex(s: string): Promise<string> {
  const h = new Uint8Array(await crypto.subtle.digest('SHA-256', enc.encode(s)));
  return [...h].map((b) => b.toString(16).padStart(2, '0')).join('');
}

/** Id da conversão de login: um por pessoa (o login seguinte, em qualquer uid, não cria outra). */
export const loginConversionId = (email: string) => sha256Hex(`site_login\n${email}`);

export const JWKS_URL = 'https://www.googleapis.com/service_accounts/v1/jwk/securetoken@system.gserviceaccount.com';
export const FIREBASE_PROJECT_ID = 'mindapps';
export const FIREBASE_ISSUER = `https://securetoken.google.com/${FIREBASE_PROJECT_ID}`;
export const FIREBASE_TENANT_ID = 'guifav-dev-g6dp4';
export const JWKS_TIMEOUT_MS = 3000;
const CLOCK_SKEW_SEC = 5 * 60;
const MAX_TOKEN_LEN = 8192;
const UID_RE = /^[0-9A-Za-z]{1,128}$/;
const B64URL_RE = /^[A-Za-z0-9_-]+$/;

interface JwksCache {
  keys: Map<string, CryptoKey>;
  expiresAt: number;
}

let jwksCache: JwksCache | null = null;

/**
 * Intervalo mínimo entre buscas forçadas do JWKS por `kid` desconhecido. O Google gira as chaves do securetoken e
 * anuncia `max-age` de horas: sem a busca forçada, um token assinado pela chave nova seria recusado até o cache
 * vencer. O intervalo impede que tokens com `kid` inventado façam o Worker buscar o JWKS a cada pedido.
 */
export const JWKS_REFETCH_MIN_MS = 60_000;
let jwksLastForcedAt = Number.NEGATIVE_INFINITY;

/** Limpa o cache em memória do JWKS (uso em testes). */
export function resetJwksCache(): void {
  jwksCache = null;
  jwksLastForcedAt = Number.NEGATIVE_INFINITY;
}

export interface IdentidadeEnv {
  SITE_ORIGIN: string;
  FORGET_HMAC_KEY: string;
  RL_IP: { limit(o: { key: string }): Promise<{ success: boolean }> };
  DB: D1Like;
}

export interface IdentidadeDeps {
  fetch: typeof fetch;
  now: () => number;
  jwksTimeoutMs?: number;
}

function corsHeaders(origin: string): Record<string, string> {
  return {
    'access-control-allow-origin': origin,
    vary: 'Origin',
  };
}

function preflightHeaders(origin: string): Record<string, string> {
  return {
    ...corsHeaders(origin),
    'access-control-allow-methods': 'POST',
    'access-control-allow-headers': 'authorization, content-type',
    'access-control-max-age': '600',
    'cache-control': 'no-store',
  };
}

const plain = (status: number, body: string, extra: Record<string, string> = {}) =>
  new Response(body, { status, headers: { 'content-type': 'text/plain; charset=utf-8', 'cache-control': 'no-store', ...extra } });

const empty = (status: number, extra: Record<string, string> = {}) =>
  new Response(null, { status, headers: { 'cache-control': 'no-store', ...extra } });

function decodeBase64Url(s: string): Uint8Array<ArrayBuffer> | null {
  if (!s || s.length % 4 === 1 || !B64URL_RE.test(s)) return null;
  const b64 = s.replace(/-/g, '+').replace(/_/g, '/');
  const padded = b64.length % 4 === 0 ? b64 : b64 + '='.repeat(4 - (b64.length % 4));
  try {
    const bin = atob(padded);
    const out = new Uint8Array(new ArrayBuffer(bin.length));
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  } catch {
    return null;
  }
}

function decodeJsonPart(part: string): Record<string, unknown> | null {
  const bytes = decodeBase64Url(part);
  if (!bytes) return null;
  try {
    const text = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
    const parsed: unknown = JSON.parse(text);
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) return null;
    return parsed as Record<string, unknown>;
  } catch {
    return null;
  }
}

function parseMaxAgeMs(cacheControl: string | null): number {
  if (!cacheControl) return 0;
  const m = /(?:^|,|\s)max-age\s*=\s*(\d+)(?:\s*,|\s|$)/i.exec(cacheControl);
  if (!m) return 0;
  const sec = Number(m[1]);
  return Number.isSafeInteger(sec) && sec > 0 ? sec * 1000 : 0;
}

async function getJwksKeys(deps: IdentidadeDeps, force = false): Promise<Map<string, CryptoKey> | null> {
  const nowMs = deps.now();
  if (!force && jwksCache && nowMs < jwksCache.expiresAt) {
    return jwksCache.keys;
  }

  const timeoutMs = deps.jwksTimeoutMs ?? JWKS_TIMEOUT_MS;
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    const fetchPromise = deps.fetch(JWKS_URL, { method: 'GET', signal: controller.signal });
    const timeoutPromise = new Promise<never>((_, reject) => {
      timer = setTimeout(() => {
        controller.abort();
        reject(new Error('jwks_timeout'));
      }, timeoutMs);
    });
    const res = await Promise.race([fetchPromise, timeoutPromise]);
    if (!res.ok) return null;
    const maxAgeMs = parseMaxAgeMs(res.headers.get('cache-control'));
    const data: unknown = await res.json();
    if (typeof data !== 'object' || data === null || !Array.isArray((data as { keys?: unknown }).keys)) {
      return null;
    }

    const keys = new Map<string, CryptoKey>();
    for (const rawKey of (data as { keys: unknown[] }).keys) {
      if (typeof rawKey !== 'object' || rawKey === null || Array.isArray(rawKey)) continue;
      const jwk = rawKey as JsonWebKey & { kid?: unknown; use?: unknown };
      if (jwk.kty !== 'RSA' || typeof jwk.kid !== 'string' || !jwk.kid || jwk.kid.length > 128) continue;
      if (typeof jwk.n !== 'string' || typeof jwk.e !== 'string') continue;
      if (jwk.alg !== undefined && jwk.alg !== 'RS256') continue;
      if (jwk.use !== undefined && jwk.use !== 'sig') continue;
      try {
        const key = await crypto.subtle.importKey(
          'jwk',
          { kty: 'RSA', n: jwk.n, e: jwk.e, alg: 'RS256', ext: true },
          { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256' },
          false,
          ['verify'],
        );
        keys.set(jwk.kid, key);
      } catch {
        // Ignora chave malformada no conjunto sem descartar as demais.
      }
    }

    if (keys.size === 0) return null;
    if (maxAgeMs > 0) {
      jwksCache = { keys, expiresAt: nowMs + maxAgeMs };
    } else {
      jwksCache = null;
    }
    return keys;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

type VerifyResult =
  | { ok: true; uid: string; email: string }
  | { ok: false; reason: string };

async function verifyIdToken(token: string, deps: IdentidadeDeps): Promise<VerifyResult> {
  if (!token || token.length > MAX_TOKEN_LEN) return { ok: false, reason: 'token_invalido' };
  const parts = token.split('.');
  if (parts.length !== 3) return { ok: false, reason: 'token_invalido' };
  const [headerB64, payloadB64, sigB64] = parts;

  const header = decodeJsonPart(headerB64);
  if (!header) return { ok: false, reason: 'header_invalido' };
  if (header.alg !== 'RS256') return { ok: false, reason: 'alg_invalido' };
  if (typeof header.kid !== 'string' || !header.kid || header.kid.length > 128) {
    return { ok: false, reason: 'kid_ausente' };
  }

  const sigBytes = decodeBase64Url(sigB64);
  if (!sigBytes || sigBytes.byteLength === 0) return { ok: false, reason: 'assinatura_invalida' };

  const payload = decodeJsonPart(payloadB64);
  if (!payload) return { ok: false, reason: 'payload_invalido' };

  const keys = await getJwksKeys(deps);
  if (!keys) return { ok: false, reason: 'jwks_indisponivel' };

  let key = keys.get(header.kid);
  if (!key) {
    // Rotação de chaves: um kid fora do cache força uma busca nova, no máximo uma por JWKS_REFETCH_MIN_MS.
    const nowMs = deps.now();
    if (nowMs - jwksLastForcedAt >= JWKS_REFETCH_MIN_MS) {
      jwksLastForcedAt = nowMs;
      const fresh = await getJwksKeys(deps, true);
      if (fresh) key = fresh.get(header.kid);
    }
    if (!key) return { ok: false, reason: 'kid_desconhecido' };
  }

  let validSig = false;
  try {
    validSig = await crypto.subtle.verify(
      'RSASSA-PKCS1-v1_5',
      key,
      sigBytes,
      enc.encode(`${headerB64}.${payloadB64}`),
    );
  } catch {
    validSig = false;
  }
  if (!validSig) return { ok: false, reason: 'assinatura_invalida' };

  if (payload.iss !== FIREBASE_ISSUER) return { ok: false, reason: 'iss_invalido' };
  if (payload.aud !== FIREBASE_PROJECT_ID) return { ok: false, reason: 'aud_invalido' };

  const exp = payload.exp;
  const iat = payload.iat;
  if (typeof exp !== 'number' || !Number.isFinite(exp)) return { ok: false, reason: 'exp_invalido' };
  if (typeof iat !== 'number' || !Number.isFinite(iat)) return { ok: false, reason: 'iat_invalido' };

  const nowSec = deps.now() / 1000;
  if (exp <= nowSec) return { ok: false, reason: 'expirado' };
  if (iat > nowSec + CLOCK_SKEW_SEC) return { ok: false, reason: 'iat_futuro' };

  const sub = payload.sub;
  if (typeof sub !== 'string' || !UID_RE.test(sub)) return { ok: false, reason: 'sub_invalido' };

  if (payload.email_verified !== true) return { ok: false, reason: 'email_nao_verificado' };
  if (typeof payload.email !== 'string') return { ok: false, reason: 'email_invalido' };
  const email = normalizeEmail(payload.email);
  if (!email) return { ok: false, reason: 'email_invalido' };

  const fb = payload.firebase;
  if (typeof fb !== 'object' || fb === null || Array.isArray(fb)) {
    return { ok: false, reason: 'tenant_invalido' };
  }
  if ((fb as { tenant?: unknown }).tenant !== FIREBASE_TENANT_ID) {
    return { ok: false, reason: 'tenant_invalido' };
  }

  return { ok: true, uid: sub, email };
}

function extractBearer(authHeader: string | null): string | null {
  if (!authHeader) return null;
  const m = /^Bearer\s+(\S+)$/i.exec(authHeader.trim());
  return m ? m[1] : null;
}

export async function handleIdentidade(
  req: Request,
  env: IdentidadeEnv,
  deps: IdentidadeDeps,
): Promise<Response> {
  const origin = req.headers.get('origin');
  if (req.method === 'OPTIONS') {
    if (origin !== env.SITE_ORIGIN) return plain(403, 'origem não permitida');
    return empty(204, preflightHeaders(env.SITE_ORIGIN));
  }
  if (req.method !== 'POST') {
    return plain(405, 'método não permitido', { allow: 'POST, OPTIONS' });
  }
  if (origin !== env.SITE_ORIGIN) return plain(403, 'origem não permitida');

  const cors = corsHeaders(env.SITE_ORIGIN);
  if (!env.FORGET_HMAC_KEY || env.FORGET_HMAC_KEY.length < MIN_SECRET) {
    return plain(500, 'Worker sem configuração', cors);
  }

  const ip = req.headers.get('cf-connecting-ip') ?? 'desconhecido';
  if (!(await env.RL_IP.limit({ key: ip })).success) {
    return plain(429, 'muitas requisições', cors);
  }

  const rawBody = await readBoundedBytes(req, IDENTIDADE_BODY_MAX);
  if (rawBody === null) {
    return plain(413, 'corpo grande demais', cors);
  }
  let bodyText = '';
  try {
    bodyText = new TextDecoder('utf-8', { fatal: true }).decode(rawBody);
  } catch {
    return plain(400, 'corpo inválido', cors);
  }
  // Sem corpo (ou campo fora do padrão), a origem é "desconhecida", como num formulário sem JavaScript.
  const origem = parseOrigem(new URLSearchParams(bodyText));

  const token = extractBearer(req.headers.get('authorization'));
  if (!token) {
    console.error('identidade recusada: authorization_ausente');
    return empty(401, cors);
  }

  const verified = await verifyIdToken(token, deps);
  if (!verified.ok) {
    console.error(`identidade recusada: ${verified.reason}`);
    return empty(401, cors);
  }

  const linkedAt = new Date(deps.now()).toISOString();
  let changes: number | undefined;
  try {
    const hmac = await heatingOptoutHmac(verified.email, env.FORGET_HMAC_KEY);
    // Checa heating_optout e faz upsert na mesma instrução para eliminar corrida com a oposição (--optout).
    const res = await env.DB
      .prepare(`INSERT INTO site_identities (uid, email, linked_at)
        SELECT ?, ?, ?
        WHERE NOT EXISTS (SELECT 1 FROM heating_optout WHERE email_hmac = ?)
        ON CONFLICT(uid) DO UPDATE SET
          email = excluded.email,
          linked_at = excluded.linked_at`)
      .bind(verified.uid, verified.email, linkedAt, hmac)
      .run();
    changes = res?.meta?.changes;
  } catch (e) {
    console.error(`identidade não gravada: ${motivo(e)}`);
    return plain(500, 'falha ao gravar identidade', cors);
  }
  // O upsert sempre altera 1 linha (insere ou atualiza); 0 só acontece quando o WHERE NOT EXISTS barrou pela oposição.
  // A resposta vai só a quem provou ser o dono do email pelo ID token, então não revela nada a terceiros.
  if (changes === 0) {
    console.error('identidade recusada: oposicao');
    return empty(409, cors);
  }
  if (typeof changes !== 'number') {
    console.error('identidade sem contagem de linhas do D1');
    return plain(500, 'falha ao gravar identidade', cors);
  }

  // Conversão "login no site", uma por pessoa (id derivado do email; a repetição é ignorada), na mesma guarda da oposição.
  // Só com corpo: o assets/auth.js atual sempre manda a origem (no mínimo origem_canal=desconhecida); a chamada sem corpo é
  // de um auth.js antigo em cache durante a publicação (ou de outro cliente) e grava só o vínculo. Assim a conversão nunca
  // nasce sem a origem que o cliente novo manda, sem precisar corrigir a origem depois (nem correr contra o sync).
  // Falha aqui responde 500: o auth.js tenta de novo na página seguinte, e o upsert do vínculo é idempotente.
  if (rawBody.byteLength === 0) return empty(204, cors);
  let convChanges: number | undefined;
  try {
    const hmac = await heatingOptoutHmac(verified.email, env.FORGET_HMAC_KEY);
    const convRes = await env.DB
      .prepare(`INSERT INTO conversions
        (id, email, action, material, interests, occurred_at, verified,
         origin_channel, origin_source, utm_source, utm_medium, utm_campaign, landing_path)
        SELECT ?, ?, 'site_login', NULL, '[]', ?, 1, ?, ?, ?, ?, ?, ?
        WHERE NOT EXISTS (SELECT 1 FROM heating_optout WHERE email_hmac = ?)
        ON CONFLICT(id) DO NOTHING`)
      .bind(await loginConversionId(verified.email), verified.email, linkedAt,
        origem.canal, origem.fonte, origem.utmSource, origem.utmMedium, origem.utmCampaign, origem.entrada, hmac)
      .run();
    convChanges = convRes?.meta?.changes;
  } catch (e) {
    console.error(`conversão de login não gravada: ${motivo(e)}`);
    return plain(500, 'falha ao gravar identidade', cors);
  }
  // 0 é esperado (login repetido, ou oposição gravada entre as duas instruções); sem contagem, a escrita não é verificável.
  if (typeof convChanges !== 'number') {
    console.error('conversão de login sem contagem de linhas do D1');
    return plain(500, 'falha ao gravar identidade', cors);
  }

  return empty(204, cors);
}

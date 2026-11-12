// Worker de inscrição da newsletter do blog (issue #21, parte A).
//   POST /subscribe  formulário do blog: Turnstile, validação e email de confirmação
//   GET  /confirm    link assinado do email: cria o contato no segmento do Resend
// As garantias A1 a A9 estão no PR e em tests/worker.test.ts.
import { signLink, verifyLink, MIN_SECRET } from './token.ts';
import { confirmationEmail } from './email.ts';

export interface RateLimit {
  limit(o: { key: string }): Promise<{ success: boolean }>;
}

export interface Env {
  RESEND_API_KEY: string;
  TURNSTILE_SECRET: string;
  CONFIRM_SECRET: string;
  FROM: string;
  REPLY_TO: string;
  SEGMENT_ID: string;
  SITE_ORIGIN: string;
  BLOG_URL: string;
  WORKER_URL: string;
  TURNSTILE_HOSTNAME: string;
  RL_EMAIL: RateLimit;
  RL_IP: RateLimit;
}

export interface Deps {
  fetch: typeof fetch;
  now: () => number;
}

const MAX_BODY = 8192;
const RESEND = 'https://api.resend.com';
const SITEVERIFY = 'https://challenges.cloudflare.com/turnstile/v0/siteverify';

const redirect = (location: string) => new Response(null, { status: 303, headers: { location, 'cache-control': 'no-store' } });
const plain = (status: number, body: string, extra: Record<string, string> = {}) =>
  new Response(body, { status, headers: { 'content-type': 'text/plain; charset=utf-8', 'cache-control': 'no-store', ...extra } });

/** Email normalizado (minúsculas, sem espaços nas pontas) ou null. Sem espaço, controle ou caractere de cabeçalho. */
export function normalizeEmail(raw: string): string | null {
  const e = raw.trim().toLowerCase();
  if (e.length > 254) return null;
  const label = '[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?';
  const re = new RegExp(`^[a-z0-9.!#$%&*+/=?^_\`{|}~-]{1,64}@${label}(?:\\.${label})+$`);
  return re.test(e) ? e : null;
}

/** Caminho do contato na API do Resend: o email, com o @ literal como na documentação. */
export const contactPath = (email: string) => `${RESEND}/contacts/${encodeURIComponent(email).replace('%40', '@')}`;

function configured(env: Env): boolean {
  return Boolean(env.CONFIRM_SECRET && env.CONFIRM_SECRET.length >= MIN_SECRET && env.RESEND_API_KEY && env.SEGMENT_ID);
}

async function turnstileOk(token: string, ip: string, env: Env, deps: Deps): Promise<boolean> {
  try {
    const r = await deps.fetch(SITEVERIFY, {
      method: 'POST',
      body: new URLSearchParams({ secret: env.TURNSTILE_SECRET, response: token, remoteip: ip }),
    });
    const d = (await r.json()) as { success?: boolean; hostname?: string };
    return d.success === true && d.hostname === env.TURNSTILE_HOSTNAME;
  } catch {
    return false;
  }
}

async function subscribe(req: Request, env: Env, deps: Deps): Promise<Response> {
  if (req.headers.get('origin') !== env.SITE_ORIGIN) return plain(403, 'origem não permitida');
  if (Number(req.headers.get('content-length') ?? 0) > MAX_BODY) return plain(413, 'pedido grande demais');
  const raw = await req.text();
  if (raw.length > MAX_BODY) return plain(413, 'pedido grande demais');
  const form = new URLSearchParams(raw);
  const sent = redirect(`${env.BLOG_URL}/inscricao-enviada/`);
  const ip = req.headers.get('cf-connecting-ip') ?? 'desconhecido';

  // Limite por IP antes do Turnstile, para conter também as tentativas reprovadas (A6). O rate limit
  // do Workers é aproximado e conta por localidade do Cloudflare: contém abuso, não é teto global.
  if (!(await env.RL_IP.limit({ key: ip })).success) return sent;

  const token = form.get('cf-turnstile-response');
  if (!token || !(await turnstileOk(token, ip, env, deps))) return redirect(`${env.BLOG_URL}/assinar/#erro-verificacao`);

  // A partir daqui a resposta é sempre a mesma (A2): email inválido, repetido, acima do limite ou
  // falha no envio não se distinguem para quem pediu.
  const email = normalizeEmail(form.get('email') ?? '');
  if (!email) return sent;
  if (!(await env.RL_EMAIL.limit({ key: email })).success) return sent;

  const link = `${env.WORKER_URL}/confirm?${await signLink(email, deps.now(), env.CONFIRM_SECRET)}`;
  const mail = confirmationEmail(email, link);
  try {
    const r = await deps.fetch(`${RESEND}/emails`, {
      method: 'POST',
      headers: { authorization: `Bearer ${env.RESEND_API_KEY}`, 'content-type': 'application/json' },
      body: JSON.stringify({ from: env.FROM, to: [email], reply_to: env.REPLY_TO, subject: mail.subject, html: mail.html, text: mail.text }),
    });
    if (!r.ok) console.error(`envio da confirmação falhou: HTTP ${r.status}`);
  } catch (e) {
    console.error(`envio da confirmação falhou: ${(e as Error).message}`);
  }
  return sent;
}

async function confirm(url: URL, env: Env, deps: Deps): Promise<Response> {
  const email = await verifyLink(url.searchParams, env.CONFIRM_SECRET, deps.now());
  if (!email) return redirect(`${env.BLOG_URL}/inscricao-invalida/`);
  // Falha de rede ou resposta que não é JSON também é falha temporária (A5): 502, nunca exceção.
  try {
    return await enroll(email, env, deps);
  } catch {
    return plain(502, 'falha temporária; tente o link de novo em alguns minutos');
  }
}

// Páginas de 100 (o máximo do Resend); o teto evita laço sem fim se a API repetir o cursor.
const SEGMENT_PAGE = 100;
const SEGMENT_MAX_PAGES = 20;

async function inSegment(id: string, env: Env, deps: Deps, headers: Record<string, string>): Promise<boolean | null> {
  let after = '';
  for (let i = 0; i < SEGMENT_MAX_PAGES; i++) {
    const q = new URLSearchParams({ limit: String(SEGMENT_PAGE) });
    if (after) q.set('after', after);
    const res = await deps.fetch(`${RESEND}/contacts/${encodeURIComponent(id)}/segments?${q}`, { headers });
    if (!res.ok) return null;
    const body = (await res.json()) as { data?: Array<{ id?: unknown }>; has_more?: unknown };
    if (!Array.isArray(body.data)) return null;
    if (body.data.some((x) => x?.id === env.SEGMENT_ID)) return true;
    const last = body.data.at(-1)?.id;
    if (body.has_more !== true || typeof last !== 'string' || !last || last === after) return false;
    after = last;
  }
  return null;
}

async function enroll(email: string, env: Env, deps: Deps): Promise<Response> {
  const headers = { authorization: `Bearer ${env.RESEND_API_KEY}`, 'content-type': 'application/json' };
  const done = redirect(`${env.BLOG_URL}/inscricao-confirmada/`);
  // Registro do opt-in pelo site: a sincronização com o Substack nunca descadastra quem tem este campo (A8).
  // A propriedade precisa existir no Resend antes do deploy (o importador a cria), senão a gravação falha.
  const properties = { site_confirmed_at: new Date(deps.now()).toISOString() };

  const created = await deps.fetch(`${RESEND}/contacts`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ email, unsubscribed: false, segments: [{ id: env.SEGMENT_ID }], properties }),
  });
  if (created.ok) return done;
  if (created.status >= 500) return plain(502, 'falha temporária; tente o link de novo em alguns minutos');

  // Contato já existe (confirmação repetida, ou alguém que saiu e voltou): reativa e garante o segmento (A5).
  // Sucesso só com prova: a inclusão respondeu 2xx, ou a lista de segmentos do contato contém o segmento.
  // Qualquer outro caso (409 resource_locked, 422, 5xx) vira 502, e o leitor pode abrir o link de novo.
  const retry = plain(502, 'falha temporária; tente o link de novo em alguns minutos');
  const existing = await deps.fetch(contactPath(email), { headers });
  if (!existing.ok) return retry;
  const id = ((await existing.json()) as { id?: unknown }).id;
  if (typeof id !== 'string' || !id) return retry;
  const upd = await deps.fetch(contactPath(email), { method: 'PATCH', headers, body: JSON.stringify({ unsubscribed: false, properties }) });
  if (!upd.ok) return retry;
  const seg = await deps.fetch(`${RESEND}/contacts/${encodeURIComponent(id)}/segments/${env.SEGMENT_ID}`, { method: 'POST', headers });
  if (seg.ok) return done;
  return (await inSegment(id, env, deps, headers)) === true ? done : retry;
}

export async function handle(req: Request, env: Env, deps: Deps): Promise<Response> {
  const url = new URL(req.url);
  if (url.pathname !== '/subscribe' && url.pathname !== '/confirm') return plain(404, 'não encontrado');
  if (url.pathname === '/subscribe' && req.method !== 'POST') return plain(405, 'método não permitido', { allow: 'POST' });
  // Só GET: um HEAD (pré-busca de link, verificador de email) não pode cadastrar ninguém (A1, A7).
  if (url.pathname === '/confirm' && req.method !== 'GET') return plain(405, 'método não permitido', { allow: 'GET' });
  if (!configured(env)) return plain(500, 'Worker sem configuração');
  return url.pathname === '/subscribe' ? subscribe(req, env, deps) : confirm(url, env, deps);
}

export default {
  fetch: (req: Request, env: Env) => handle(req, env, { fetch: (i, init) => fetch(i, init), now: Date.now }),
};

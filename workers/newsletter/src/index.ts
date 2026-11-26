// Worker de inscrição da newsletter do blog (issue #21, parte A) e de entrega dos materiais do guifav.dev.
//   POST /subscribe          formulário do blog: Turnstile, validação e email de confirmação
//   GET  /confirm            link assinado do email: cria o contato no segmento do Resend
//   POST /material           formulário das páginas de material: Turnstile, validação e email com o link
//   GET  /material/download  link assinado do email (7 dias): entrega o arquivo (bundle ou R2) e grava a conversão (issue #97)
// Desde a issue #114, a inscrição e o pedido de material levam a origem da visita (src/origem.ts) até a conversão.
// As garantias A1 a A9 estão no PR e em tests/worker.test.ts; as dos materiais, em tests/material.test.ts.
import { signLink, verifyConfirmLink, signMaterialLink, verifyMaterialLink, materialExp, MIN_SECRET, TTL_MS, MATERIAL_TTL_MS } from './token.ts';
import { confirmationEmail, materialEmail, normalizeEmail } from './email.ts';
import { material, arquivo, INTERESSES } from './materiais.ts';
import { recordConversion, conversionId, requestId, requestInfo, type D1Like } from './conversoes.ts';
import { parseOrigem } from './origem.ts';
import { parseGaClientId, sendDownloadEvent } from './ga4.ts';
import { checkForgotten } from './forget.ts';

export { normalizeEmail };

export interface RateLimit {
  limit(o: { key: string }): Promise<{ success: boolean }>;
}

/** Subconjunto mínimo de R2ObjectBody usado no download em stream. */
export interface R2ObjectBodyLike {
  body: ReadableStream<Uint8Array>;
  size?: number;
}

/** Subconjunto mínimo de R2Bucket usado pelo Worker para materiais grandes. */
export interface R2BucketLike {
  get(key: string): Promise<R2ObjectBodyLike | null>;
}

export interface Env {
  RESEND_API_KEY: string;
  TURNSTILE_SECRET: string;
  CONFIRM_SECRET: string;
  /** Chave do HMAC-SHA256 da tabela `forgotten` (issue #134; 32 caracteres ou mais, igual no wrapper da KVM). */
  FORGET_HMAC_KEY: string;
  FROM: string;
  MATERIAL_FROM: string;
  REPLY_TO: string;
  SEGMENT_ID: string;
  TOPIC_ID: string;
  SITE_ORIGIN: string;
  BLOG_URL: string;
  WORKER_URL: string;
  TURNSTILE_HOSTNAME: string;
  RL_EMAIL: RateLimit;
  RL_IP: RateLimit;
  /** D1 `blog-admin`: conversões do site (issues #97 e #99). */
  DB: D1Like;
  /** Bucket R2 privado para materiais grandes que ficam fora do bundle do Worker. */
  MATERIAIS_R2?: R2BucketLike;
  /** GA4 do site, para o evento de download pelo Measurement Protocol (issue #99). */
  GA4_MEASUREMENT_ID: string;
  /** Segredo da API do Measurement Protocol (wrangler secret). Sem ele, o evento de download não é enviado. */
  GA4_API_SECRET?: string;
}

export interface Deps {
  fetch: typeof fetch;
  now: () => number;
  /** Bytes de um arquivo de material pelo id, ou null. O src/worker.ts liga aos módulos de dados. */
  file?: (id: string) => Promise<ArrayBuffer | null>;
  /**
   * Trabalho depois da resposta (ctx.waitUntil no Cloudflare). As gravações de conversão vão por aqui: um D1 lento ou
   * fora do ar nunca atrasa nem impede o download, o email do pedido ou a confirmação (issue #97).
   */
  waitUntil: (p: Promise<unknown>) => void;
}

const MAX_BODY = 8192;
const RESEND = 'https://api.resend.com';
const SITEVERIFY = 'https://challenges.cloudflare.com/turnstile/v0/siteverify';

const redirect = (location: string) => new Response(null, { status: 303, headers: { location, 'cache-control': 'no-store' } });
const plain = (status: number, body: string, extra: Record<string, string> = {}) =>
  new Response(body, { status, headers: { 'content-type': 'text/plain; charset=utf-8', 'cache-control': 'no-store', ...extra } });

/** Caminho do contato na API do Resend: o email, com o @ literal como na documentação. */
export const contactPath = (email: string) => `${RESEND}/contacts/${encodeURIComponent(email).replace('%40', '@')}`;

function configured(env: Env): boolean {
  return Boolean(
    env.CONFIRM_SECRET && env.CONFIRM_SECRET.length >= MIN_SECRET
    && env.FORGET_HMAC_KEY && env.FORGET_HMAC_KEY.length >= MIN_SECRET
    && env.RESEND_API_KEY && env.SEGMENT_ID && env.TOPIC_ID,
  );
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

  // A origem da visita vai dentro do link assinado: a conversão newsletter_confirm só nasce no clique (issue #114).
  const link = `${env.WORKER_URL}/confirm?${await signLink(email, deps.now(), env.CONFIRM_SECRET, parseOrigem(form))}`;
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
  const ok = await verifyConfirmLink(url.searchParams, env.CONFIRM_SECRET, deps.now());
  if (!ok) return redirect(`${env.BLOG_URL}/inscricao-invalida/`);
  const { email, origem } = ok;
  // Issue #134: recusa link emitido antes (ou no instante) da remoção pelo --forget, sem tocar no Resend nem no D1.
  // Se a leitura do marcador falhar, responde a mesma 502 de falha temporária sem chamar o Resend.
  const issuedAtMs = Number(url.searchParams.get('x')) * 1000 - TTL_MS;
  const forgotten = await checkForgotten(env.DB, email, env.FORGET_HMAC_KEY, issuedAtMs);
  if (forgotten === 'error') return plain(502, 'falha temporária; tente o link de novo em alguns minutos');
  if (forgotten === 'forgotten') return redirect(`${env.BLOG_URL}/inscricao-invalida/`);
  // Falha de rede ou resposta que não é JSON também é falha temporária (A5): 502, nunca exceção.
  let res: Response;
  try {
    res = await enroll(email, env, deps);
  } catch {
    return plain(502, 'falha temporária; tente o link de novo em alguns minutos');
  }
  // Só a inscrição concluída vira conversão; o mesmo link aberto de novo não duplica (id pela assinatura).
  if (res.headers.get('location') === `${env.BLOG_URL}/inscricao-confirmada/`) {
    const nowMs = deps.now();
    deps.waitUntil((async () => recordConversion(env.DB, { id: await conversionId('newsletter_confirm', url.searchParams.get('s') ?? ''),
      acao: 'newsletter_confirm', email, material: null, interesses: [INTERESSES.EXPERT_INSIGHTS], nowMs, verified: true, origem }))());
  }
  return res;
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

  // Contrato real (sondado em 2026-11-12): para um email que já existe, este POST é upsert (201, mesmo id) e aplica
  // cada campo do corpo. Sem `topics`, quem saiu só da newsletter continuaria fora dela depois de confirmar.
  const created = await deps.fetch(`${RESEND}/contacts`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ email, unsubscribed: false, segments: [{ id: env.SEGMENT_ID }], properties,
      topics: [{ id: env.TOPIC_ID, subscription: 'opt_in' }] }),
  });
  if (created.ok) return done;
  if (created.status >= 500) return plain(502, 'falha temporária; tente o link de novo em alguns minutos');

  // Reserva, se a API recusar o POST de um email existente (409/422): reativa, devolve ao tópico e garante o segmento (A5).
  // Sucesso só com prova: a inclusão respondeu 2xx, ou a lista de segmentos do contato contém o segmento.
  // Qualquer outro caso (409 resource_locked, 422, 5xx) vira 502, e o leitor pode abrir o link de novo.
  const retry = plain(502, 'falha temporária; tente o link de novo em alguns minutos');
  const existing = await deps.fetch(contactPath(email), { headers });
  if (!existing.ok) return retry;
  const id = ((await existing.json()) as { id?: unknown }).id;
  if (typeof id !== 'string' || !id) return retry;
  const upd = await deps.fetch(contactPath(email), { method: 'PATCH', headers, body: JSON.stringify({ unsubscribed: false, properties }) });
  if (!upd.ok) return retry;
  // O descadastro da newsletter é por tópico (não pela conta inteira): quem confirma de novo volta ao tópico.
  // Contrato real (2026-11-12): o corpo é um array puro. Contato novo já nasce no tópico (padrão opt_in).
  const topic = await deps.fetch(`${RESEND}/contacts/${encodeURIComponent(id)}/topics`, {
    method: 'PATCH', headers, body: JSON.stringify([{ id: env.TOPIC_ID, subscription: 'opt_in' }]),
  });
  if (!topic.ok) return retry;
  const seg = await deps.fetch(`${RESEND}/contacts/${encodeURIComponent(id)}/segments/${env.SEGMENT_ID}`, { method: 'POST', headers });
  if (seg.ok) return done;
  return (await inSegment(id, env, deps, headers)) === true ? done : retry;
}

async function requestMaterial(req: Request, env: Env, deps: Deps): Promise<Response> {
  if (req.headers.get('origin') !== env.SITE_ORIGIN) return plain(403, 'origem não permitida');
  if (Number(req.headers.get('content-length') ?? 0) > MAX_BODY) return plain(413, 'pedido grande demais');
  const raw = await req.text();
  if (raw.length > MAX_BODY) return plain(413, 'pedido grande demais');
  const form = new URLSearchParams(raw);
  const m = material(form.get('material') ?? '');
  if (!m) return plain(400, 'material desconhecido');
  const page = `${env.SITE_ORIGIN}${m.pagina}`;
  const sent = redirect(`${page}enviado/`);
  const ip = req.headers.get('cf-connecting-ip') ?? 'desconhecido';

  // Mesma ordem da inscrição: limite por IP, Turnstile e, daí em diante, resposta igual para qualquer caso.
  if (!(await env.RL_IP.limit({ key: ip })).success) return sent;
  const token = form.get('cf-turnstile-response');
  if (!token || !(await turnstileOk(token, ip, env, deps))) return redirect(`${page}#erro-verificacao`);
  const email = normalizeEmail(form.get('email') ?? '');
  if (!email) return sent;
  // Chave própria: pedir o material não gasta o limite da inscrição na newsletter, e vice-versa.
  if (!(await env.RL_EMAIL.limit({ key: `material:${email}` })).success) return sent;

  const now = deps.now();
  // O pedido entra no registro com o email ainda não comprovado (issue #99), e com o client_id do GA4 quando o navegador
  // o mandou (só com os cookies aceitos). O id é o mesmo que o download recalcula a partir do link.
  const gaClientId = parseGaClientId(form.get('ga_cid'));
  // A origem da visita (issue #114) vai na linha do pedido; o download a herda dele, e a confirmação pela caixa dos
  // Expert Insights, pelo link assinado.
  const origem = parseOrigem(form);
  deps.waitUntil((async () => recordConversion(env.DB, { id: await requestId(m.id, email, materialExp(now)), acao: 'material_request',
    email, material: m.id, interesses: m.interesses, nowMs: now, verified: false, gaClientId, origem }))());
  const links = await Promise.all(m.arquivos.map(async (a) => ({
    rotulo: a.rotulo,
    url: `${env.WORKER_URL}/material/download?${await signMaterialLink(a.id, email, now, env.CONFIRM_SECRET)}`,
  })));
  // A inscrição nos Expert Insights só entra com a caixa marcada, e continua dependendo do clique de confirmação.
  const confirmLink = form.get('insights') === 'sim'
    ? `${env.WORKER_URL}/confirm?${await signLink(email, now, env.CONFIRM_SECRET, origem)}` : null;
  const mail = materialEmail(email, m.titulo, links, confirmLink);
  try {
    const r = await deps.fetch(`${RESEND}/emails`, {
      method: 'POST',
      headers: { authorization: `Bearer ${env.RESEND_API_KEY}`, 'content-type': 'application/json' },
      body: JSON.stringify({ from: env.MATERIAL_FROM, to: [email], reply_to: env.REPLY_TO, subject: mail.subject, html: mail.html,
        text: mail.text, tags: [{ name: 'material', value: m.id }, { name: 'insights', value: confirmLink ? 'sim' : 'nao' }] }),
    });
    if (!r.ok) console.error(`envio do material falhou: HTTP ${r.status}`);
  } catch (e) {
    console.error(`envio do material falhou: ${(e as Error).message}`);
  }
  return sent;
}

async function download(url: URL, env: Env, deps: Deps): Promise<Response> {
  const ok = await verifyMaterialLink(url.searchParams, env.CONFIRM_SECRET, deps.now());
  const found = ok ? arquivo(ok.file) : arquivo(url.searchParams.get('f') ?? '');
  const expired = redirect(found ? `${env.SITE_ORIGIN}${found.material.pagina}#link-expirado` : `${env.SITE_ORIGIN}/`);
  if (!ok || !found) return expired;
  // Issue #134: recusa link emitido antes (ou no instante) da remoção pelo --forget, sem entregar o arquivo nem gravar nada.
  // Se a leitura do marcador falhar (D1 fora do ar), o arquivo ainda é entregue, sem gravar conversão nem mandar evento ao GA4.
  const issuedAtMs = Number(url.searchParams.get('x')) * 1000 - MATERIAL_TTL_MS;
  const forgotten = await checkForgotten(env.DB, ok.email, env.FORGET_HMAC_KEY, issuedAtMs);
  if (forgotten === 'forgotten') return expired;
  let body: ReadableStream<Uint8Array> | ArrayBuffer | null = null;
  let size: number | undefined;
  if (found.arquivo.r2Key) {
    if (!env.MATERIAIS_R2) {
      console.error(`leitura do R2 falhou (${found.arquivo.id}): binding MATERIAIS_R2 ausente`);
    } else {
      try {
        const obj = await env.MATERIAIS_R2.get(found.arquivo.r2Key);
        if (obj) {
          body = obj.body;
          if (typeof obj.size === 'number' && Number.isFinite(obj.size) && obj.size >= 0) size = obj.size;
        } else {
          console.error(`leitura do R2 falhou (${found.arquivo.id}): objeto ausente no bucket`);
        }
      } catch (e) {
        console.error(`leitura do R2 falhou (${found.arquivo.id}): ${(e as Error).message}`);
      }
    }
  } else {
    body = deps.file ? await deps.file(found.arquivo.id) : null;
  }
  if (!body) return plain(500, 'arquivo indisponível');
  // O clique no link prova o email (issue #97). A conversão aponta para o pedido que gerou o link e herda dele a origem da
  // visita (issue #114), e o evento do GA4 sai com o client_id guardado nesse pedido, ligando pedido e download no GA4;
  // sem ele, vai como contagem anônima (#99). Em falha na leitura do marcador (#134), entrega o arquivo sem gravar nem avisar o GA4.
  if (forgotten === 'ok') {
    const nowMs = deps.now();
    const material = found.material;
    const file = found.arquivo.id;
    deps.waitUntil((async () => {
      const reqId = await requestId(material.id, ok.email, Number(url.searchParams.get('x')));
      const { clientId, origem } = await requestInfo(env.DB, reqId);
      await recordConversion(env.DB, { id: await conversionId('material_download', url.searchParams.get('s') ?? ''),
        acao: 'material_download', email: ok.email, material: material.id, interesses: material.interesses, nowMs, verified: true,
        requestId: reqId, file, origem });
      await sendDownloadEvent({ measurementId: env.GA4_MEASUREMENT_ID, apiSecret: env.GA4_API_SECRET }, deps.fetch,
        { clientId, material: material.id, file, nowMs });
    })());
  }
  const headers: Record<string, string> = {
    'content-type': found.arquivo.tipo,
    'content-disposition': `attachment; filename="${found.arquivo.nome}"`,
    'cache-control': 'private, no-store',
    'x-content-type-options': 'nosniff',
    'referrer-policy': 'no-referrer',
  };
  if (size !== undefined) headers['content-length'] = String(size);
  return new Response(body, { status: 200, headers });
}

export async function handle(req: Request, env: Env, deps: Deps): Promise<Response> {
  const url = new URL(req.url);
  if (url.pathname === '/material' || url.pathname === '/material/download') {
    if (url.pathname === '/material' && req.method !== 'POST') return plain(405, 'método não permitido', { allow: 'POST' });
    if (url.pathname === '/material/download' && req.method !== 'GET') return plain(405, 'método não permitido', { allow: 'GET' });
    if (!configured(env) || !env.MATERIAL_FROM) return plain(500, 'Worker sem configuração');
    return url.pathname === '/material' ? requestMaterial(req, env, deps) : download(url, env, deps);
  }
  if (url.pathname !== '/subscribe' && url.pathname !== '/confirm') return plain(404, 'não encontrado');
  if (url.pathname === '/subscribe' && req.method !== 'POST') return plain(405, 'método não permitido', { allow: 'POST' });
  // Só GET: um HEAD (pré-busca de link, verificador de email) não pode cadastrar ninguém (A1, A7).
  if (url.pathname === '/confirm' && req.method !== 'GET') return plain(405, 'método não permitido', { allow: 'GET' });
  if (!configured(env)) return plain(500, 'Worker sem configuração');
  return url.pathname === '/subscribe' ? subscribe(req, env, deps) : confirm(url, env, deps);
}

export interface Ctx {
  waitUntil(p: Promise<unknown>): void;
}

/**
 * O objeto exportado ao Cloudflare: liga o handle ao fetch, ao relógio, ao ctx.waitUntil do pedido e, no deploy, aos
 * arquivos dos materiais. Os dois entrypoints (este arquivo e o src/worker.ts) usam esta fábrica, que os testes exercitam.
 */
export function entrypoint(file?: Deps['file']) {
  return {
    fetch: (req: Request, env: Env, ctx: Ctx) =>
      handle(req, env, { fetch: (i, init) => fetch(i, init), now: Date.now, file, waitUntil: (p) => ctx.waitUntil(p) }),
  };
}

export default entrypoint();

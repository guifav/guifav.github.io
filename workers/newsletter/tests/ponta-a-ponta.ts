// Ponta a ponta da origem das conversões (issue #114), sem rede: o assets/track.js de verdade numa visita simulada
// (referrer e página de entrada), o envio do formulário com os campos ocultos que ele põe, o Worker de verdade (handle)
// e o D1 em SQLite com as migrações reais do painel. Simula uma inscrição (pedido e clique no link de confirmação) e um
// pedido de material com a caixa dos Expert Insights (pedido, download e confirmação).
//
// Usado pelo tests/origem.test.ts (o lado do D1) e, como programa, pelo blog-src/scripts/test_conversoes_sync.py, que
// leva as linhas impressas ao sync do Twenty e ao --forget:
//     node tests/ponta-a-ponta.ts   (imprime as linhas de `conversions` em JSON)
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';
import { handle, type Env } from '../src/index.ts';
import { d1, rows } from './d1.ts';

const TRACK = readFileSync(new URL('../../../assets/track.js', import.meta.url), 'utf8');
const TOML = readFileSync(new URL('../wrangler.toml', import.meta.url), 'utf-8');
const tomlVar = (k: string) => TOML.match(new RegExp(`^${k}\\s*=\\s*"([^"]*)"`, 'm'))![1];
export const NOW = Date.UTC(2026, 10, 24, 12, 0, 0);
const SECRET = 'segredo-de-teste-com-tamanho-suficiente-0123456789';

/** O que a visita simulada deixa para trás e que nunca pode aparecer no D1 nem no Twenty. */
export const PROIBIDOS = ['termo', 'secreto', 'GCLIDSECRETO', 'search', 'http', '?', '#', '=', '203.0.113', 'Mozilla'];

class Campo {
  attrs: Record<string, string> = {};
  value = '';
  setAttribute(k: string, v: string) { this.attrs[k] = String(v); }
  getAttribute(k: string) { return this.attrs[k] ?? null; }
}

/**
 * Uma aba do navegador: abre `paginas` em sequência (a primeira com `referrer`, as outras com a anterior como referrer,
 * como na navegação interna) e, na última, envia um formulário para `action`. Devolve os campos que o track.js pôs nele.
 */
export function visita(referrer: string, paginas: string[], action: string): Record<string, string> {
  const sessionStorage = new Map<string, string>();
  let campos: Campo[] = [];
  paginas.forEach((href, i) => {
    const submit: ((e: unknown) => void)[] = [];
    const form = {
      getAttribute: (k: string) => (k === 'action' ? action : null),
      querySelector: (sel: string) => campos.find((c) => sel === `input[name="${c.attrs.name}"]`) ?? null,
      appendChild: (c: Campo) => (campos.push(c), c),
      closest: (sel: string) => (sel === 'form' ? form : null),
    };
    campos = [];
    const document = {
      documentElement: { lang: 'pt-BR' }, body: { getAttribute: () => 'light' }, cookie: '', baseURI: href,
      referrer: i === 0 ? referrer : paginas[i - 1],
      addEventListener: (type: string, fn: (e: unknown) => void) => { if (type === 'submit') submit.push(fn); },
      querySelector: () => null, createElement: () => new Campo(),
    };
    const u = new URL(href);
    const ctx: Record<string, unknown> = {
      document, URL, JSON, Date,
      location: { href, origin: u.origin, host: u.host, hostname: u.hostname, pathname: u.pathname },
      sessionStorage: { getItem: (k: string) => sessionStorage.get(k) ?? null, setItem: (k: string, v: string) => void sessionStorage.set(k, v) },
      localStorage: { getItem: () => null, setItem() {} },
    };
    ctx.window = ctx;
    vm.runInContext(TRACK, vm.createContext(ctx));
    if (i === paginas.length - 1) for (const fn of submit) fn({ type: 'submit', target: form });
  });
  return Object.fromEntries(campos.map((c) => [c.attrs.name, c.value]));
}

function worker() {
  const db = d1();
  const pending: Promise<unknown>[] = [];
  const emails: { html: string; text: string }[] = [];
  const env: Env = {
    RESEND_API_KEY: 're_teste', TURNSTILE_SECRET: 'ts_teste', CONFIRM_SECRET: SECRET,
    FROM: tomlVar('FROM'), MATERIAL_FROM: tomlVar('MATERIAL_FROM'), REPLY_TO: tomlVar('REPLY_TO'),
    SEGMENT_ID: 'seg-1', TOPIC_ID: 'topic-1', SITE_ORIGIN: tomlVar('SITE_ORIGIN'), BLOG_URL: tomlVar('BLOG_URL'),
    WORKER_URL: tomlVar('WORKER_URL'), TURNSTILE_HOSTNAME: tomlVar('TURNSTILE_HOSTNAME'),
    RL_EMAIL: { limit: async () => ({ success: true }) }, RL_IP: { limit: async () => ({ success: true }) },
    DB: db, GA4_MEASUREMENT_ID: tomlVar('GA4_MEASUREMENT_ID'), GA4_API_SECRET: 'mp_teste_segredo',
  };
  const fetchFn = async (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input instanceof Request ? input.url : input);
    if (url.includes('turnstile')) return Response.json({ success: true, hostname: env.TURNSTILE_HOSTNAME });
    if (url === 'https://api.resend.com/emails') {
      emails.push(JSON.parse(String(init?.body)));
      return Response.json({ id: 'e1' });
    }
    if (url === 'https://api.resend.com/contacts') return Response.json({ id: 'c1' }, { status: 201 });
    if (url.startsWith('https://www.google-analytics.com/mp/collect?')) return new Response(null, { status: 204 });
    return new Response('inesperado', { status: 500 });
  };
  const run = async (req: Request) => {
    const r = await handle(req, env, { fetch: fetchFn as typeof fetch, now: () => NOW, waitUntil: (p) => void pending.push(p),
      file: async (id) => new TextEncoder().encode(`conteudo:${id}`).buffer as ArrayBuffer });
    await Promise.all(pending);
    return r;
  };
  return { run, emails, db: db.raw };
}

const post = (path: string, fields: Record<string, string>) => new Request(`https://insights.guifav.dev${path}`, {
  method: 'POST',
  headers: { 'content-type': 'application/x-www-form-urlencoded', origin: 'https://guifav.dev', 'cf-connecting-ip': '203.0.113.7',
    'user-agent': 'Mozilla/5.0 (teste)' },
  body: new URLSearchParams(fields).toString(),
});
const linksDe = (html: string) => [...html.matchAll(/href="(https:\/\/insights\.guifav\.dev\/[^"]+)"/g)].map((m) => m[1].replace(/&amp;/g, '&'));

export interface PontaAPonta {
  /** Campos que o track.js pôs em cada formulário. */
  campos: { inscricao: Record<string, string>; material: Record<string, string> };
  /** Linhas de `conversions` no fim, na ordem de gravação. */
  linhas: Record<string, unknown>[];
}

export async function pontaAPonta(): Promise<PontaAPonta> {
  const w = worker();
  // Inscrição: chegou pela busca do Google num post (com o termo buscado no referrer e um gclid na URL), leu outro post
  // e se inscreveu nele.
  const inscricao = visita('https://www.google.com/search?q=termo+secreto',
    ['https://guifav.dev/blog/rag-em-producao/?gclid=GCLIDSECRETO&q=termo#ancora', 'https://guifav.dev/blog/agentes/'],
    'https://insights.guifav.dev/subscribe');
  await w.run(post('/subscribe', { email: 'inscrita@exemplo.com', 'cf-turnstile-response': 'tok', ...inscricao }));
  const [confirmacao] = linksDe(w.emails[0].html);
  await w.run(new Request(confirmacao));

  // Material: chegou do ChatGPT (utm_source=chatgpt.com) na página do mapa e pediu com a caixa dos Expert Insights.
  const material = visita('https://chatgpt.com/', ['https://guifav.dev/mapa-decisao-ia/?utm_source=chatgpt.com&utm_term=termo'],
    'https://insights.guifav.dev/material');
  await w.run(post('/material', { email: 'leitora@exemplo.com', material: 'mapa-decisao-ia', insights: 'sim',
    'cf-turnstile-response': 'tok', ...material }));
  const links = linksDe(w.emails[1].html);
  for (const l of links) await w.run(new Request(l)); // os dois arquivos do mapa e a confirmação
  return { campos: { inscricao, material }, linhas: rows(w.db, 'conversions') };
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  process.stdout.write(JSON.stringify(await pontaAPonta()));
}

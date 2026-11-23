// Testes da entrega dos materiais do canal @guifav (POST /material e GET /material/download).
// Rodar em workers/newsletter/: npm test
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { handle, type Env } from '../src/index.ts';
import { signLink, signMaterialLink, MATERIAL_TTL_MS } from '../src/token.ts';
import { MATERIAIS } from '../src/materiais.ts';

const NOW = Date.UTC(2026, 10, 23, 12, 0, 0);
const SECRET = 'segredo-de-teste-com-tamanho-suficiente-0123456789';
const TOML = readFileSync(new URL('../wrangler.toml', import.meta.url), 'utf-8');
const tomlVar = (k: string) => {
  const m = TOML.match(new RegExp(`^${k}\\s*=\\s*"([^"]*)"`, 'm'));
  assert.ok(m, `variável ${k} ausente do wrangler.toml`);
  return m[1];
};

type Call = { url: string; method: string; body: any };

function setup(opts: { turnstile?: unknown; rlEmail?: boolean; rlIp?: boolean; emailStatus?: number; now?: number } = {}) {
  const calls: Call[] = [];
  const rl: string[] = [];
  const fetchFn = async (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input instanceof Request ? input.url : input);
    const method = init?.method ?? 'GET';
    let body: any = init?.body;
    if (typeof body === 'string' && body.startsWith('{')) body = JSON.parse(body);
    if (body instanceof URLSearchParams) body = Object.fromEntries(body);
    calls.push({ url, method, body });
    if (url.includes('turnstile')) return Response.json(opts.turnstile ?? { success: true, hostname: 'guifav.dev' });
    if (url === 'https://api.resend.com/emails') return opts.emailStatus ? new Response('x', { status: opts.emailStatus }) : Response.json({ id: 'e1' });
    return new Response('inesperado', { status: 500 });
  };
  const env: Env = {
    RESEND_API_KEY: 're_teste', TURNSTILE_SECRET: 'ts_teste', CONFIRM_SECRET: SECRET,
    FROM: tomlVar('FROM'), MATERIAL_FROM: tomlVar('MATERIAL_FROM'), REPLY_TO: tomlVar('REPLY_TO'),
    SEGMENT_ID: 'seg-1', TOPIC_ID: 'topic-1', SITE_ORIGIN: tomlVar('SITE_ORIGIN'), BLOG_URL: tomlVar('BLOG_URL'),
    WORKER_URL: tomlVar('WORKER_URL'), TURNSTILE_HOSTNAME: tomlVar('TURNSTILE_HOSTNAME'),
    RL_EMAIL: { limit: async ({ key }) => (rl.push('email:' + key), { success: opts.rlEmail ?? true }) },
    RL_IP: { limit: async ({ key }) => (rl.push('ip:' + key), { success: opts.rlIp ?? true }) },
  };
  const bytes: Record<string, ArrayBuffer> = Object.fromEntries(
    MATERIAIS.flatMap((m) => m.arquivos).map((a) => [a.id, new TextEncoder().encode(`conteudo:${a.id}`).buffer as ArrayBuffer]));
  const run = (req: Request) => handle(req, env, { fetch: fetchFn as typeof fetch, now: () => opts.now ?? NOW, file: async (id) => bytes[id] ?? null });
  return { run, calls, rl, env };
}

const pedir = (fields: Record<string, string>, headers: Record<string, string> = {}) =>
  new Request('https://insights.guifav.dev/material', {
    method: 'POST',
    headers: { 'content-type': 'application/x-www-form-urlencoded', origin: 'https://guifav.dev', 'cf-connecting-ip': '203.0.113.7', ...headers },
    body: new URLSearchParams(fields).toString(),
  });
const ok = { email: 'Leitor@Exemplo.com ', material: 'planilha-custos-ia', 'cf-turnstile-response': 'tok' };
const loc = (r: Response) => r.headers.get('location');
const emails = (calls: Call[]) => calls.filter((c) => c.url === 'https://api.resend.com/emails');
const links = (html: string) => [...html.matchAll(/href="(https:\/\/insights\.guifav\.dev\/[^"]+)"/g)].map((m) => m[1].replace(/&amp;/g, '&'));

test('M1: pedido válido manda um email com o link assinado e redireciona para a página de enviado', async () => {
  const s = setup();
  const r = await s.run(pedir(ok));
  assert.equal(r.status, 303);
  assert.equal(loc(r), 'https://guifav.dev/planilha-custos-ia/enviado/');
  const sent = emails(s.calls);
  assert.equal(sent.length, 1);
  const b = sent[0].body;
  assert.deepEqual(b.to, ['leitor@exemplo.com']);
  assert.equal(b.from, 'Guilherme Favaron <insights@guifav.dev>');
  assert.deepEqual(b.tags, [{ name: 'material', value: 'planilha-custos-ia' }, { name: 'insights', value: 'nao' }]);
  const l = links(b.html);
  assert.equal(l.length, 1);
  assert.ok(l[0].startsWith('https://insights.guifav.dev/material/download?f=planilha-custos-ia&'));
  assert.ok(b.text.includes(l[0]));
  // Nenhum cadastro de contato: o email serve só para mandar o link.
  assert.equal(s.calls.filter((c) => c.url.startsWith('https://api.resend.com/contacts')).length, 0);
  assert.ok(s.rl.includes('email:material:leitor@exemplo.com'));
});

test('M2: com a caixa dos Expert Insights marcada, o mesmo email traz o link de confirmação, e nada é cadastrado', async () => {
  const s = setup();
  await s.run(pedir({ ...ok, insights: 'sim' }));
  const b = emails(s.calls)[0].body;
  const l = links(b.html);
  assert.equal(l.length, 2);
  assert.ok(l[1].startsWith('https://insights.guifav.dev/confirm?'));
  assert.deepEqual(b.tags[1], { name: 'insights', value: 'sim' });
  assert.equal(s.calls.filter((c) => c.url.startsWith('https://api.resend.com/contacts')).length, 0);
});

test('M3: o mapa de decisão manda um link por arquivo', async () => {
  const s = setup();
  const r = await s.run(pedir({ ...ok, material: 'mapa-decisao-ia' }));
  assert.equal(loc(r), 'https://guifav.dev/mapa-decisao-ia/enviado/');
  const b = emails(s.calls)[0].body;
  assert.equal(b.subject, 'Seus links: Mapa de decisão: RAG, Text-to-SQL ou contexto longo');
  const l = links(b.html);
  assert.deepEqual(l.map((u) => new URL(u).searchParams.get('f')), ['mapa-decisao-pdf', 'conta-custo-por-pergunta']);
});

test('M4: material desconhecido, origem errada e método errado não mandam nada', async () => {
  const s = setup();
  assert.equal((await s.run(pedir({ ...ok, material: '../etc' }))).status, 400);
  assert.equal((await s.run(pedir(ok, { origin: 'https://evil.example' }))).status, 403);
  assert.equal((await s.run(new Request('https://insights.guifav.dev/material'))).status, 405);
  assert.equal((await s.run(new Request('https://insights.guifav.dev/material/download', { method: 'POST' }))).status, 405);
  assert.equal(emails(s.calls).length, 0);
});

test('M5: Turnstile reprovado volta para a página com o erro, sem email', async () => {
  const s = setup({ turnstile: { success: false } });
  const r = await s.run(pedir(ok));
  assert.equal(loc(r), 'https://guifav.dev/planilha-custos-ia/#erro-verificacao');
  assert.equal(emails(s.calls).length, 0);
});

test('M6: email inválido, limite atingido e falha no envio respondem igual ao sucesso', async () => {
  for (const o of [{ fields: { ...ok, email: 'sem-arroba' } }, { rlEmail: false }, { rlIp: false }, { emailStatus: 500 }] as any[]) {
    const s = setup(o);
    const r = await s.run(pedir(o.fields ?? ok));
    assert.equal(loc(r), 'https://guifav.dev/planilha-custos-ia/enviado/');
  }
});

const baixar = (q: string) => new Request(`https://insights.guifav.dev/material/download?${q}`);

test('M7: link válido entrega o arquivo como anexo, sem cache', async () => {
  const s = setup();
  const r = await s.run(baixar(await signMaterialLink('planilha-custos-ia', 'leitor@exemplo.com', NOW, SECRET)));
  assert.equal(r.status, 200);
  assert.equal(r.headers.get('content-type'), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');
  assert.equal(r.headers.get('content-disposition'), 'attachment; filename="planilha-custos-ia.xlsx"');
  assert.equal(r.headers.get('cache-control'), 'private, no-store');
  assert.equal(r.headers.get('x-content-type-options'), 'nosniff');
  assert.equal(r.headers.get('referrer-policy'), 'no-referrer');
  assert.equal(await r.text(), 'conteudo:planilha-custos-ia');
});

test('M8: link expirado, adulterado ou de outro tipo não entrega nada', async () => {
  const s = setup();
  const q = await signMaterialLink('planilha-custos-ia', 'leitor@exemplo.com', NOW, SECRET);
  // Expirado: 7 dias e 1 minuto depois.
  const r1 = await setup({ now: NOW + MATERIAL_TTL_MS + 60e3 }).run(baixar(q));
  assert.equal(loc(r1), 'https://guifav.dev/planilha-custos-ia/#link-expirado');
  // Assinatura de um arquivo usada para outro.
  const r2 = await s.run(baixar(q.replace('f=planilha-custos-ia', 'f=mapa-decisao-pdf')));
  assert.equal(loc(r2), 'https://guifav.dev/mapa-decisao-ia/#link-expirado');
  // O link de confirmação da newsletter não vale como download.
  const r3 = await s.run(baixar(`f=planilha-custos-ia&${await signLink('leitor@exemplo.com', NOW, SECRET)}`));
  assert.equal(r3.status, 303);
  // O link de download não vale como confirmação da newsletter.
  const r4 = await s.run(new Request(`https://insights.guifav.dev/confirm?${q}`));
  assert.equal(loc(r4), 'https://guifav.dev/blog/inscricao-invalida/');
  // Sem nada: volta para a home.
  assert.equal(loc(await s.run(baixar(''))), 'https://guifav.dev/');
});

test('M9: o deploy leva os arquivos de todos os materiais do catálogo', () => {
  assert.match(TOML, /^main = "src\/worker\.ts"$/m);
  assert.match(TOML, /\[\[rules\]\]\ntype = "Data"\nglobs = \["\*\*\/\*\.xlsx", "\*\*\/\*\.pdf"\]/);
  const worker = readFileSync(new URL('../src/worker.ts', import.meta.url), 'utf-8');
  for (const a of MATERIAIS.flatMap((m) => m.arquivos)) {
    assert.ok(worker.includes(`'${a.id}':`), `worker.ts não liga o arquivo ${a.id}`);
    assert.ok(worker.includes(`../materiais/${a.nome}`), `worker.ts não importa ${a.nome}`);
    assert.ok(existsSync(new URL(`../materiais/${a.nome}`, import.meta.url)), `materiais/${a.nome} ausente`);
  }
});

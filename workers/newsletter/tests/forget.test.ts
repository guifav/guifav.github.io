// Testes do marcador de remoção (issue #134): depois do --forget, nenhum link assinado emitido antes da remoção
// grava nada no D1, no Resend nem no GA4. Rodar em workers/newsletter/: npm test
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { handle, type Env, type R2BucketLike } from '../src/index.ts';
import { signLink, signMaterialLink } from '../src/token.ts';
import { checkForgotten, FORGET_TIMEOUT_MS, forgetHmac } from '../src/forget.ts';
import { MATERIAIS } from '../src/materiais.ts';
import { d1, rows, capturaErros } from './d1.ts';

const NOW = Date.UTC(2026, 10, 24, 12, 0, 0);
const SECRET = 'segredo-de-teste-com-tamanho-suficiente-0123456789';
// Vetor de teste fixo, repetido em blog-src/scripts/test_conversoes_sync.py para provar que TS e Python dão o mesmo hex.
const FORGET_KEY = 'chave-hmac-de-teste-com-32-bytes-ou-mais-0123456789';
const VETOR_EMAIL = 'leitor@exemplo.com';
const VETOR_HMAC = '3d6844d8f0921e015a40170ae9c2fbafa051a7abe38dc79f9c29e498c4281b3e';

const TOML = readFileSync(new URL('../wrangler.toml', import.meta.url), 'utf-8');
const tomlVar = (k: string) => TOML.match(new RegExp(`^${k}\\s*=\\s*"([^"]*)"`, 'm'))![1];

type Call = { url: string; method: string; body: any };

function setup(opts: { d1Falha?: RegExp; d1Pendura?: RegExp; forgetTimeoutMs?: number; now?: number; forgetKey?: string } = {}) {
  const calls: Call[] = [];
  const r2Gets: string[] = [];
  const db = d1(opts.d1Falha, undefined, opts.d1Pendura);
  const pending: Promise<unknown>[] = [];
  const waitUntil = (p: Promise<unknown>) => void pending.push(p);
  const settle = () => Promise.all(pending);
  const fetchFn = async (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input instanceof Request ? input.url : input);
    const method = init?.method ?? 'GET';
    let body: any = init?.body;
    if (typeof body === 'string' && (body.startsWith('{') || body.startsWith('['))) body = JSON.parse(body);
    calls.push({ url, method, body });
    if (url.includes('turnstile')) return Response.json({ success: true, hostname: 'guifav.dev' });
    if (url === 'https://api.resend.com/emails') return Response.json({ id: 'e1' });
    if (url === 'https://api.resend.com/contacts' && method === 'POST') return Response.json({ object: 'contact', id: 'c1' }, { status: 201 });
    if (url.startsWith('https://www.google-analytics.com/mp/collect?')) return new Response(null, { status: 204 });
    return new Response('inesperado', { status: 500 });
  };
  const r2Bucket: R2BucketLike = {
    get: async (key: string) => {
      r2Gets.push(key);
      const data = new TextEncoder().encode(`r2:${key}`);
      return {
        body: new ReadableStream({ start(c) { c.enqueue(data); c.close(); } }),
        size: data.byteLength,
      };
    },
  };
  const env = {
    RESEND_API_KEY: 're_teste',
    TURNSTILE_SECRET: 'ts_teste',
    CONFIRM_SECRET: SECRET,
    FORGET_HMAC_KEY: opts.forgetKey ?? FORGET_KEY,
    FROM: tomlVar('FROM'),
    MATERIAL_FROM: tomlVar('MATERIAL_FROM'),
    REPLY_TO: tomlVar('REPLY_TO'),
    SEGMENT_ID: 'seg-1',
    TOPIC_ID: 'topic-1',
    SITE_ORIGIN: tomlVar('SITE_ORIGIN'),
    BLOG_URL: tomlVar('BLOG_URL'),
    WORKER_URL: tomlVar('WORKER_URL'),
    TURNSTILE_HOSTNAME: tomlVar('TURNSTILE_HOSTNAME'),
    RL_EMAIL: { limit: async () => ({ success: true }) },
    RL_IP: { limit: async () => ({ success: true }) },
    DB: db,
    MATERIAIS_R2: r2Bucket,
    GA4_MEASUREMENT_ID: tomlVar('GA4_MEASUREMENT_ID'),
    GA4_API_SECRET: 'mp_teste_segredo',
  } as Env;
  const bytes: Record<string, ArrayBuffer> = Object.fromEntries(
    MATERIAIS.flatMap((m) => m.arquivos)
      .filter((a) => !a.r2Key)
      .map((a) => [a.id, new TextEncoder().encode(`conteudo:${a.id}`).buffer as ArrayBuffer]),
  );
  let clock = opts.now ?? NOW;
  const run = (req: Request) =>
    handle(req, env, { fetch: fetchFn as typeof fetch, now: () => clock, file: async (id) => bytes[id] ?? null, waitUntil, forgetTimeoutMs: opts.forgetTimeoutMs });
  const avanca = (ms: number) => void (clock += ms);
  return { env, run, calls, r2Gets, db: db.raw, settle, avanca, rows: async (t: string) => (await settle(), rows(db.raw, t)) };
}

const pedir = (fields: Record<string, string>) =>
  new Request('https://insights.guifav.dev/material', {
    method: 'POST',
    headers: { 'content-type': 'application/x-www-form-urlencoded', origin: 'https://guifav.dev', 'cf-connecting-ip': '203.0.113.7' },
    body: new URLSearchParams(fields).toString(),
  });
const inscrever = (email: string) =>
  new Request('https://insights.guifav.dev/subscribe', {
    method: 'POST',
    headers: { 'content-type': 'application/x-www-form-urlencoded', origin: 'https://guifav.dev', 'cf-connecting-ip': '203.0.113.7' },
    body: new URLSearchParams({ email, 'cf-turnstile-response': 'tok' }).toString(),
  });
const loc = (r: Response) => r.headers.get('location');
const links = (html: string) => [...html.matchAll(/href="(https:\/\/insights\.guifav\.dev\/[^"]+)"/g)].map((m) => m[1].replace(/&amp;/g, '&'));
const ga4 = (calls: Call[]) => calls.filter((c) => c.url.startsWith('https://www.google-analytics.com/mp/collect?'));
const resendContacts = (calls: Call[]) => calls.filter((c) => c.url.startsWith('https://api.resend.com/contacts'));

test('F1: migração 0007 cria a tabela forgotten e o CHECK recusa email_hmac fora de 64 hex minúsculos', () => {
  const db = d1();
  db.raw.prepare('INSERT INTO forgotten (email_hmac, forgotten_at) VALUES (?, ?)').run(VETOR_HMAC, '2026-11-26T12:00:00.000Z');
  assert.equal(rows(db.raw, 'forgotten').length, 1);
  for (const ruim of ['a'.repeat(63), 'a'.repeat(65), VETOR_HMAC.toUpperCase(), VETOR_EMAIL, 'g'.repeat(64), '']) {
    assert.throws(
      () => db.raw.prepare('INSERT INTO forgotten (email_hmac, forgotten_at) VALUES (?, ?)').run(ruim, '2026-11-26T12:00:00.000Z'),
      /CHECK constraint failed/,
      ruim,
    );
  }
  assert.throws(
    () => db.raw.prepare('INSERT INTO forgotten (email_hmac, forgotten_at) VALUES (?, ?)').run(null, '2026-11-26T12:00:00.000Z'),
    /constraint failed/,
  );
});

test('F2: forgetHmac bate com o vetor de teste fixo, normaliza o email e recusa chave curta ou email inválido', async () => {
  assert.equal(await forgetHmac(VETOR_EMAIL, FORGET_KEY), VETOR_HMAC);
  assert.equal(await forgetHmac('  Leitor@Exemplo.COM ', FORGET_KEY), VETOR_HMAC);
  await assert.rejects(() => forgetHmac(VETOR_EMAIL, ''), /FORGET_HMAC_KEY/);
  await assert.rejects(() => forgetHmac(VETOR_EMAIL, 'x'.repeat(31)), /FORGET_HMAC_KEY/);
  await assert.rejects(() => forgetHmac('sem-arroba', FORGET_KEY), /email/);
});

test('F3: FORGET_HMAC_KEY ausente ou curta derruba o Worker com 500 Worker sem configuração', async () => {
  const qConfirm = await signLink(VETOR_EMAIL, NOW, SECRET);
  const qDown = await signMaterialLink('planilha-custos-ia', VETOR_EMAIL, NOW, SECRET);
  for (const forgetKey of ['', 'x'.repeat(31)]) {
    const s = setup({ forgetKey });
    for (const req of [
      inscrever(VETOR_EMAIL),
      new Request(`https://insights.guifav.dev/confirm?${qConfirm}`),
      pedir({ email: VETOR_EMAIL, material: 'planilha-custos-ia', 'cf-turnstile-response': 'tok' }),
      new Request(`https://insights.guifav.dev/material/download?${qDown}`),
    ]) {
      const r = await s.run(req);
      assert.equal(r.status, 500, `${req.url} com chave len=${forgetKey.length}`);
      assert.equal(await r.text(), 'Worker sem configuração');
    }
    assert.equal(s.calls.length, 0);
  }
});

test('F4: download: pedido, marcador gravado depois, clique no link antigo redireciona para #link-expirado sem D1 nem GA4', async () => {
  const s = setup();
  await s.run(pedir({ email: VETOR_EMAIL, material: 'planilha-custos-ia', ga_cid: '123.456', 'cf-turnstile-response': 'tok' }));
  await s.settle();
  const [dlLink] = links(s.calls.find((c) => c.url === 'https://api.resend.com/emails')!.body.html);
  // Simula o --forget --apply 1 minuto depois do pedido: grava o marcador e apaga conversions.
  s.db.prepare('INSERT INTO forgotten (email_hmac, forgotten_at) VALUES (?, ?)').run(VETOR_HMAC, new Date(NOW + 60e3).toISOString());
  s.db.prepare('DELETE FROM conversions WHERE email = ?').run(VETOR_EMAIL);

  s.avanca(120e3);
  const r = await s.run(new Request(dlLink));
  assert.equal(r.status, 303);
  assert.equal(loc(r), 'https://guifav.dev/ferramentas/planilha-custos-ia/#link-expirado');
  assert.equal((await s.rows('conversions')).length, 0);
  assert.equal(ga4(s.calls).length, 0);

  // Também no livro (R2): não toca no R2, redireciona para a página do livro com #link-expirado.
  const qLivro = await signMaterialLink('livro-desbloqueando-ia-pdf', VETOR_EMAIL, NOW, SECRET);
  const rLivro = await s.run(new Request(`https://insights.guifav.dev/material/download?${qLivro}`));
  assert.equal(rLivro.status, 303);
  assert.equal(loc(rLivro), 'https://guifav.dev/materiais/livro-desbloqueando-a-inteligencia-artificial/#link-expirado');
  assert.deepEqual(s.r2Gets, []);
  assert.equal((await s.rows('conversions')).length, 0);
  assert.equal(ga4(s.calls).length, 0);

  // Igualdade exata (forgotten_at === emissão) também recusa.
  const sEq = setup();
  const qEq = await signMaterialLink('planilha-custos-ia', VETOR_EMAIL, NOW, SECRET);
  sEq.db.prepare('INSERT INTO forgotten (email_hmac, forgotten_at) VALUES (?, ?)').run(VETOR_HMAC, new Date(NOW).toISOString());
  const rEq = await sEq.run(new Request(`https://insights.guifav.dev/material/download?${qEq}`));
  assert.equal(rEq.status, 303);
  assert.equal(loc(rEq), 'https://guifav.dev/ferramentas/planilha-custos-ia/#link-expirado');
  assert.equal((await sEq.rows('conversions')).length, 0);
  assert.equal(ga4(sEq.calls).length, 0);
});

test('F5: confirmação do blog e da caixa dos Expert Insights: link antigo depois do marcador redireciona para inscricao-invalida sem Resend nem D1', async () => {
  const s = setup();
  await s.run(inscrever(VETOR_EMAIL));
  await s.run(pedir({ email: VETOR_EMAIL, material: 'planilha-custos-ia', insights: 'sim', 'cf-turnstile-response': 'tok' }));
  await s.settle();
  const emailsSent = s.calls.filter((c) => c.url === 'https://api.resend.com/emails');
  const [confirmBlog] = links(emailsSent[0].body.html);
  const confirmMaterial = links(emailsSent[1].body.html).find((u) => u.includes('/confirm?'))!;
  assert.ok(confirmBlog && confirmMaterial);

  s.db.prepare('INSERT INTO forgotten (email_hmac, forgotten_at) VALUES (?, ?)').run(VETOR_HMAC, new Date(NOW + 60e3).toISOString());
  s.db.prepare('DELETE FROM conversions WHERE email = ?').run(VETOR_EMAIL);

  s.avanca(120e3);
  for (const link of [confirmBlog, confirmMaterial]) {
    const r = await s.run(new Request(link));
    assert.equal(r.status, 303, link);
    assert.equal(loc(r), 'https://guifav.dev/blog/inscricao-invalida/', link);
  }
  assert.equal(resendContacts(s.calls).length, 0);
  assert.equal((await s.rows('conversions')).length, 0);

  // Igualdade exata (forgotten_at === emissão) também recusa.
  const sEq = setup();
  const qEq = await signLink(VETOR_EMAIL, NOW, SECRET);
  sEq.db.prepare('INSERT INTO forgotten (email_hmac, forgotten_at) VALUES (?, ?)').run(VETOR_HMAC, new Date(NOW).toISOString());
  const rEq = await sEq.run(new Request(`https://insights.guifav.dev/confirm?${qEq}`));
  assert.equal(rEq.status, 303);
  assert.equal(loc(rEq), 'https://guifav.dev/blog/inscricao-invalida/');
  assert.equal(resendContacts(sEq.calls).length, 0);
  assert.equal((await sEq.rows('conversions')).length, 0);
});

test('F6: link emitido depois do marcador segue o caminho normal (download e confirmação)', async () => {
  const s = setup();
  s.db.prepare('INSERT INTO forgotten (email_hmac, forgotten_at) VALUES (?, ?)').run(VETOR_HMAC, new Date(NOW + 500).toISOString());

  s.avanca(60e3);
  await s.run(pedir({ email: VETOR_EMAIL, material: 'planilha-custos-ia', insights: 'sim', 'cf-turnstile-response': 'tok' }));
  await s.settle();
  const emailBody = s.calls.find((c) => c.url === 'https://api.resend.com/emails')!.body.html;
  const [dlLink, confirmLink] = links(emailBody);

  const rDl = await s.run(new Request(dlLink));
  assert.equal(rDl.status, 200);
  assert.equal(await rDl.text(), 'conteudo:planilha-custos-ia');
  const rConf = await s.run(new Request(confirmLink));
  assert.equal(rConf.status, 303);
  assert.equal(loc(rConf), 'https://guifav.dev/blog/inscricao-confirmada/');

  const c = await s.rows('conversions');
  assert.deepEqual(c.map((x) => x.action).sort(), ['material_download', 'material_request', 'newsletter_confirm']);
  assert.equal(resendContacts(s.calls).length, 1);
  assert.equal(ga4(s.calls).length, 1);
});

test('F7: marcador de outro email não afeta', async () => {
  const s = setup();
  const outroHmac = '0'.repeat(64);
  assert.notEqual(outroHmac, VETOR_HMAC);
  s.db.prepare('INSERT INTO forgotten (email_hmac, forgotten_at) VALUES (?, ?)').run(outroHmac, new Date(NOW + 60e3).toISOString());

  const qDl = await signMaterialLink('planilha-custos-ia', VETOR_EMAIL, NOW, SECRET);
  const qConf = await signLink(VETOR_EMAIL, NOW, SECRET);
  const rDl = await s.run(new Request(`https://insights.guifav.dev/material/download?${qDl}`));
  assert.equal(rDl.status, 200);
  assert.equal(await rDl.text(), 'conteudo:planilha-custos-ia');
  const rConf = await s.run(new Request(`https://insights.guifav.dev/confirm?${qConf}`));
  assert.equal(rConf.status, 303);
  assert.equal(loc(rConf), 'https://guifav.dev/blog/inscricao-confirmada/');

  const c = await s.rows('conversions');
  assert.deepEqual(c.map((x) => x.action).sort(), ['material_download', 'newsletter_confirm']);
  assert.equal(resendContacts(s.calls).length, 1);
  assert.equal(ga4(s.calls).length, 1);
});

test('F8: falha na leitura do marcador: confirmação responde 502 sem Resend; download entrega o arquivo sem D1 nem GA4; log sem email', async () => {
  const s = setup({ d1Falha: /forgotten/ });
  const qConf = await signLink(VETOR_EMAIL, NOW, SECRET);
  const qDl = await signMaterialLink('planilha-custos-ia', VETOR_EMAIL, NOW, SECRET);
  let rConf!: Response;
  let rDl!: Response;
  const erros = await capturaErros(async () => {
    rConf = await s.run(new Request(`https://insights.guifav.dev/confirm?${qConf}`));
    rDl = await s.run(new Request(`https://insights.guifav.dev/material/download?${qDl}`));
    await s.settle();
  });
  assert.equal(rConf.status, 502);
  assert.equal(await rConf.text(), 'falha temporária; tente o link de novo em alguns minutos');
  assert.equal(resendContacts(s.calls).length, 0);

  assert.equal(rDl.status, 200);
  assert.equal(await rDl.text(), 'conteudo:planilha-custos-ia');
  assert.equal((await s.rows('conversions')).length, 0);
  assert.equal(ga4(s.calls).length, 0);

  assert.equal(erros.length, 2);
  assert.ok(!erros.join('\n').includes('exemplo.com'), erros.join('\n'));
  assert.ok(!erros.join('\n').includes(FORGET_KEY), erros.join('\n'));

  // Data corrompida na tabela `forgotten` cai no mesmo caminho seguro (502 na confirmação; download entrega sem gravar nem GA4).
  const sBad = setup();
  sBad.db.prepare('INSERT INTO forgotten (email_hmac, forgotten_at) VALUES (?, ?)').run(VETOR_HMAC, 'nao-e-data');
  let rConfBad!: Response;
  let rDlBad!: Response;
  const errosBad = await capturaErros(async () => {
    rConfBad = await sBad.run(new Request(`https://insights.guifav.dev/confirm?${qConf}`));
    rDlBad = await sBad.run(new Request(`https://insights.guifav.dev/material/download?${qDl}`));
    await sBad.settle();
  });
  assert.equal(rConfBad.status, 502);
  assert.equal(resendContacts(sBad.calls).length, 0);
  assert.equal(rDlBad.status, 200);
  assert.equal((await sBad.rows('conversions')).length, 0);
  assert.equal(ga4(sBad.calls).length, 0);
  assert.equal(errosBad.length, 2);
});

test('F9: D1 pendurado só em forgotten estoura o prazo (FORGET_TIMEOUT_MS = 1500 ms) e as duas rotas respondem dentro do prazo', async (t) => {
  assert.equal(FORGET_TIMEOUT_MS, 1500);
  const s = setup({ d1Pendura: /forgotten/, forgetTimeoutMs: 30 });
  const qConf = await signLink(VETOR_EMAIL, NOW, SECRET);
  const qDl = await signMaterialLink('planilha-custos-ia', VETOR_EMAIL, NOW, SECRET);
  const limite = <T>(p: Promise<T>) =>
    Promise.race([p, new Promise<never>((_, no) => setTimeout(() => no(new Error('a rota esperou o D1 em forgotten')), 500))]);
  let rConf!: Response;
  let rDl!: Response;
  const erros = await capturaErros(async () => {
    rConf = await limite(s.run(new Request(`https://insights.guifav.dev/confirm?${qConf}`)));
    rDl = await limite(s.run(new Request(`https://insights.guifav.dev/material/download?${qDl}`)));
    await s.settle();
  });
  assert.equal(rConf.status, 502);
  assert.equal(await rConf.text(), 'falha temporária; tente o link de novo em alguns minutos');
  assert.equal(resendContacts(s.calls).length, 0);

  assert.equal(rDl.status, 200);
  assert.equal(await rDl.text(), 'conteudo:planilha-custos-ia');
  assert.equal((await s.rows('conversions')).length, 0);
  assert.equal(ga4(s.calls).length, 0);

  assert.equal(erros.length, 2);
  assert.ok(!erros.join('\n').includes('exemplo.com'), erros.join('\n'));
  assert.ok(!erros.join('\n').includes(FORGET_KEY), erros.join('\n'));

  // Prova sem esperar 1,5 s reais que o prazo padrão de checkForgotten usa FORGET_TIMEOUT_MS.
  t.mock.timers.enable({ apis: ['setTimeout'] });
  try {
    let consultou!: () => void;
    const leu = new Promise<void>((ok) => (consultou = ok));
    const dbPendurado = {
      prepare: () => ({
        bind: () => ({
          run: async () => new Promise(() => {}),
          first: () => {
            consultou();
            return new Promise<null>(() => {});
          },
        }),
        run: async () => new Promise(() => {}),
        first: async () => new Promise<null>(() => {}),
      }),
    };
    let status: string | undefined;
    const errosPadrao = await capturaErros(async () => {
      const p = checkForgotten(dbPendurado, VETOR_EMAIL, FORGET_KEY, NOW).then((st) => (status = st));
      await leu;
      await Promise.resolve();
      t.mock.timers.tick(FORGET_TIMEOUT_MS - 1);
      await Promise.resolve();
      assert.equal(status, undefined, 'não deve estourar antes de FORGET_TIMEOUT_MS');
      t.mock.timers.tick(1);
      await p;
    });
    assert.equal(status, 'error');
    assert.equal(errosPadrao.length, 1);
    assert.match(errosPadrao[0], /1500 ms/);
    assert.ok(!errosPadrao[0].includes('exemplo.com'), errosPadrao[0]);
  } finally {
    t.mock.timers.reset();
  }
});

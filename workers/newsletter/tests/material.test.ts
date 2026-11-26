// Testes da entrega dos materiais do canal @guifav (POST /material e GET /material/download).
// Rodar em workers/newsletter/: npm test
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { handle, entrypoint, type Env, type R2BucketLike } from '../src/index.ts';
import { signLink, signMaterialLink, MATERIAL_TTL_MS } from '../src/token.ts';
import { MATERIAIS, INTERESSES } from '../src/materiais.ts';
import { createHash } from 'node:crypto';
import { d1, rows, capturaErros, variantes, SEM_ORIGEM } from './d1.ts';

const NOW = Date.UTC(2026, 10, 23, 12, 0, 0);
const SECRET = 'segredo-de-teste-com-tamanho-suficiente-0123456789';
const FORGET_KEY = 'chave-hmac-de-teste-com-32-bytes-ou-mais-0123456789';
const TOML = readFileSync(new URL('../wrangler.toml', import.meta.url), 'utf-8');
const tomlVar = (k: string) => {
  const m = TOML.match(new RegExp(`^${k}\\s*=\\s*"([^"]*)"`, 'm'));
  assert.ok(m, `variável ${k} ausente do wrangler.toml`);
  return m[1];
};

type Call = { url: string; method: string; body: any };

function r2Stream(bytes: Uint8Array): ReadableStream<Uint8Array> {
  return new ReadableStream({
    start(controller) {
      controller.enqueue(bytes);
      controller.close();
    },
  });
}

function setup(opts: { turnstile?: unknown; rlEmail?: boolean; rlIp?: boolean; emailStatus?: number; now?: number; d1Falha?: RegExp; d1Pendura?: RegExp; d1Porta?: Promise<void>; semGa4?: boolean; ga4Status?: number | 'throw'; ga4Pendura?: boolean; ga4Porta?: Promise<void>; r2?: Record<string, Uint8Array | null>; semR2?: boolean; r2Erro?: boolean } = {}) {
  const calls: Call[] = [];
  const rl: string[] = [];
  const r2Gets: string[] = [];
  const db = d1(opts.d1Falha, undefined, opts.d1Pendura, opts.d1Porta);
  const pending: Promise<unknown>[] = [];
  const waitUntil = (p: Promise<unknown>) => void pending.push(p);
  // Espera o trabalho feito depois da resposta (o que o Cloudflare faria com ctx.waitUntil).
  const settle = () => Promise.all(pending);
  const fetchFn = async (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input instanceof Request ? input.url : input);
    const method = init?.method ?? 'GET';
    let body: any = init?.body;
    if (typeof body === 'string' && body.startsWith('{')) body = JSON.parse(body);
    if (body instanceof URLSearchParams) body = Object.fromEntries(body);
    calls.push({ url, method, body });
    if (url.includes('turnstile')) return Response.json(opts.turnstile ?? { success: true, hostname: 'guifav.dev' });
    if (url === 'https://api.resend.com/emails') return opts.emailStatus ? new Response('x', { status: opts.emailStatus }) : Response.json({ id: 'e1' });
    if (url.startsWith('https://www.google-analytics.com/mp/collect?')) {
      // A exceção cita a URL, com o segredo, como faria um erro real de rede: o log não pode repeti-la.
      if (opts.ga4Status === 'throw') throw new TypeError(`fetch failed: ${url}`);
      if (opts.ga4Pendura) return new Promise<Response>(() => {}); // GA4 que nunca responde
      if (opts.ga4Porta) await opts.ga4Porta; // GA4 que só responde quando o teste libera
      return new Response(null, { status: opts.ga4Status ?? 204 });
    }
    return new Response('inesperado', { status: 500 });
  };
  const r2Default: Record<string, Uint8Array | null> = Object.fromEntries(
    MATERIAIS.flatMap((m) => m.arquivos)
      .filter((a) => a.r2Key)
      .map((a) => [a.r2Key!, new TextEncoder().encode(`r2:${a.id}`)]),
  );
  const r2Store = { ...r2Default, ...(opts.r2 ?? {}) };
  const r2Bucket: R2BucketLike | undefined = opts.semR2
    ? undefined
    : {
        get: async (key: string) => {
          r2Gets.push(key);
          if (opts.r2Erro) throw new Error('R2 indisponivel');
          const data = r2Store[key];
          if (!data) return null;
          return { body: r2Stream(data), size: data.byteLength };
        },
      };
  const env: Env = {
    RESEND_API_KEY: 're_teste', TURNSTILE_SECRET: 'ts_teste', CONFIRM_SECRET: SECRET, FORGET_HMAC_KEY: FORGET_KEY,
    FROM: tomlVar('FROM'), MATERIAL_FROM: tomlVar('MATERIAL_FROM'), REPLY_TO: tomlVar('REPLY_TO'),
    SEGMENT_ID: 'seg-1', TOPIC_ID: 'topic-1', SITE_ORIGIN: tomlVar('SITE_ORIGIN'), BLOG_URL: tomlVar('BLOG_URL'),
    WORKER_URL: tomlVar('WORKER_URL'), TURNSTILE_HOSTNAME: tomlVar('TURNSTILE_HOSTNAME'),
    RL_EMAIL: { limit: async ({ key }) => (rl.push('email:' + key), { success: opts.rlEmail ?? true }) },
    RL_IP: { limit: async ({ key }) => (rl.push('ip:' + key), { success: opts.rlIp ?? true }) },
    DB: db,
    MATERIAIS_R2: r2Bucket,
    GA4_MEASUREMENT_ID: tomlVar('GA4_MEASUREMENT_ID'),
    GA4_API_SECRET: opts.semGa4 ? undefined : 'mp_teste_segredo',
  };
  const bytes: Record<string, ArrayBuffer> = Object.fromEntries(
    MATERIAIS.flatMap((m) => m.arquivos)
      .filter((a) => !a.r2Key)
      .map((a) => [a.id, new TextEncoder().encode(`conteudo:${a.id}`).buffer as ArrayBuffer]));
  let clock = opts.now ?? NOW;
  const run = (req: Request) => handle(req, env, { fetch: fetchFn as typeof fetch, now: () => clock, file: async (id) => bytes[id] ?? null, waitUntil });
  const avanca = (ms: number) => void (clock += ms);
  return { run, calls, rl, r2Gets, env, db: db.raw, settle, pendentes: () => pending.length, avanca, rows: async (t: string) => (await settle(), rows(db.raw, t)), waitUntil };
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
  assert.equal(loc(r), 'https://guifav.dev/ferramentas/planilha-custos-ia/enviado/');
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
  assert.equal(loc(r), 'https://guifav.dev/ferramentas/mapa-decisao-ia/enviado/');
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
  assert.equal(loc(r), 'https://guifav.dev/ferramentas/planilha-custos-ia/#erro-verificacao');
  assert.equal(emails(s.calls).length, 0);
});

test('M6: email inválido, limite atingido e falha no envio respondem igual ao sucesso', async () => {
  for (const o of [{ fields: { ...ok, email: 'sem-arroba' } }, { rlEmail: false }, { rlIp: false }, { emailStatus: 500 }] as any[]) {
    const s = setup(o);
    const r = await s.run(pedir(o.fields ?? ok));
    assert.equal(loc(r), 'https://guifav.dev/ferramentas/planilha-custos-ia/enviado/');
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
  assert.equal(loc(r1), 'https://guifav.dev/ferramentas/planilha-custos-ia/#link-expirado');
  // Assinatura de um arquivo usada para outro.
  const r2 = await s.run(baixar(q.replace('f=planilha-custos-ia', 'f=mapa-decisao-pdf')));
  assert.equal(loc(r2), 'https://guifav.dev/ferramentas/mapa-decisao-ia/#link-expirado');
  // O link de confirmação da newsletter não vale como download.
  const r3 = await s.run(baixar(`f=planilha-custos-ia&${await signLink('leitor@exemplo.com', NOW, SECRET)}`));
  assert.equal(r3.status, 303);
  // O link de download não vale como confirmação da newsletter.
  const r4 = await s.run(new Request(`https://insights.guifav.dev/confirm?${q}`));
  assert.equal(loc(r4), 'https://guifav.dev/blog/inscricao-invalida/');
  // Sem nada: volta para a home.
  assert.equal(loc(await s.run(baixar(''))), 'https://guifav.dev/');
});

test('M9: o deploy leva os arquivos do bundle e configura o R2 para os arquivos grandes', () => {
  assert.match(TOML, /^main = "src\/worker\.ts"$/m);
  assert.match(TOML, /\[\[rules\]\]\ntype = "Data"\nglobs = \["\*\*\/\*\.xlsx", "\*\*\/\*\.pdf"\]/);
  assert.match(TOML, /\[\[r2_buckets\]\]\nbinding = "MATERIAIS_R2"\nbucket_name = "blog-materiais"/);
  const worker = readFileSync(new URL('../src/worker.ts', import.meta.url), 'utf-8');
  for (const m of MATERIAIS) {
    assert.ok(m.pagina.startsWith('/') && m.pagina.endsWith('/'), `${m.id}: página fora do formato ${m.pagina}`);
    for (const a of m.arquivos) {
      if (a.r2Key) {
        assert.ok(!worker.includes(a.nome), `worker.ts não deve embutir o arquivo do R2 ${a.nome}`);
        assert.ok(!existsSync(new URL(`../materiais/${a.nome}`, import.meta.url)), `arquivo do R2 não deve entrar em materiais/${a.nome}`);
      } else {
        assert.ok(worker.includes(`'${a.id}':`), `worker.ts não liga o arquivo ${a.id}`);
        assert.ok(worker.includes(`../materiais/${a.nome}`), `worker.ts não importa ${a.nome}`);
        assert.ok(existsSync(new URL(`../materiais/${a.nome}`, import.meta.url)), `materiais/${a.nome} ausente`);
      }
    }
  }
});

// ---------------------------------------------------------------- Livro via R2 (issue #183)

const okLivro = {
  email: 'Leitor@Exemplo.com ',
  material: 'livro-desbloqueando-a-inteligencia-artificial',
  'cf-turnstile-response': 'tok',
};

test('L1: pedido do livro redireciona para /materiais/.../enviado/, manda o link do PDF e grava interesse ESTRATEGIA_IA', async () => {
  const s = setup();
  const r = await s.run(pedir(okLivro));
  assert.equal(r.status, 303);
  assert.equal(loc(r), 'https://guifav.dev/materiais/livro-desbloqueando-a-inteligencia-artificial/enviado/');
  const sent = emails(s.calls);
  assert.equal(sent.length, 1);
  const b = sent[0].body;
  assert.equal(b.subject, 'Seu link: Desbloqueando a Inteligência Artificial');
  assert.deepEqual(b.tags, [{ name: 'material', value: 'livro-desbloqueando-a-inteligencia-artificial' }, { name: 'insights', value: 'nao' }]);
  const l = links(b.html);
  assert.equal(l.length, 1);
  assert.ok(l[0].startsWith('https://insights.guifav.dev/material/download?f=livro-desbloqueando-ia-pdf&'));
  const c = await s.rows('conversions');
  assert.equal(c.length, 1);
  assert.equal(c[0].action, 'material_request');
  assert.equal(c[0].material, 'livro-desbloqueando-a-inteligencia-artificial');
  assert.equal(c[0].interests, JSON.stringify([INTERESSES.ESTRATEGIA_IA]));
});

test('L2: Turnstile reprovado no livro volta para /materiais/.../#erro-verificacao', async () => {
  const s = setup({ turnstile: { success: false } });
  const r = await s.run(pedir(okLivro));
  assert.equal(r.status, 303);
  assert.equal(loc(r), 'https://guifav.dev/materiais/livro-desbloqueando-a-inteligencia-artificial/#erro-verificacao');
  assert.equal(emails(s.calls).length, 0);
});

test('L3: link expirado ou adulterado do livro volta para /materiais/.../#link-expirado', async () => {
  const q = await signMaterialLink('livro-desbloqueando-ia-pdf', 'leitor@exemplo.com', NOW, SECRET);
  const expirado = await setup({ now: NOW + MATERIAL_TTL_MS + 60e3 }).run(baixar(q));
  assert.equal(loc(expirado), 'https://guifav.dev/materiais/livro-desbloqueando-a-inteligencia-artificial/#link-expirado');
  const outro = await signMaterialLink('planilha-custos-ia', 'leitor@exemplo.com', NOW, SECRET);
  const adulterado = await setup().run(baixar(outro.replace('f=planilha-custos-ia', 'f=livro-desbloqueando-ia-pdf')));
  assert.equal(loc(adulterado), 'https://guifav.dev/materiais/livro-desbloqueando-a-inteligencia-artificial/#link-expirado');
});

test('L4: download do livro lê em stream do R2, devolve o PDF com content-length e grava conversão e GA4', async () => {
  const pdf = new TextEncoder().encode('%PDF-1.7 conteudo do livro');
  const s = setup({ r2: { 'livro/desbloqueando-a-inteligencia-artificial.pdf': pdf } });
  const q = await signMaterialLink('livro-desbloqueando-ia-pdf', 'leitor@exemplo.com', NOW, SECRET);
  const r = await s.run(baixar(q));
  assert.equal(r.status, 200);
  assert.deepEqual(s.r2Gets, ['livro/desbloqueando-a-inteligencia-artificial.pdf']);
  assert.equal(r.headers.get('content-type'), 'application/pdf');
  assert.equal(r.headers.get('content-disposition'), 'attachment; filename="desbloqueando-a-inteligencia-artificial-guilherme-favaron.pdf"');
  assert.equal(r.headers.get('content-length'), String(pdf.byteLength));
  assert.equal(r.headers.get('cache-control'), 'private, no-store');
  assert.equal(r.headers.get('x-content-type-options'), 'nosniff');
  assert.equal(r.headers.get('referrer-policy'), 'no-referrer');
  assert.equal(await r.text(), '%PDF-1.7 conteudo do livro');
  const c = await s.rows('conversions');
  assert.equal(c.length, 1);
  assert.equal(c[0].action, 'material_download');
  assert.equal(c[0].material, 'livro-desbloqueando-a-inteligencia-artificial');
  assert.equal(c[0].file, 'livro-desbloqueando-ia-pdf');
  const g = ga4(s.calls);
  assert.equal(g.length, 1);
  assert.equal(g[0].body.events[0].params.material_id, 'livro-desbloqueando-a-inteligencia-artificial');
  assert.equal(g[0].body.events[0].params.file_id, 'livro-desbloqueando-ia-pdf');
});

test('L5: objeto ausente, binding ausente ou erro no R2 registram erro sem dados do link e devolvem 500 sem gravar conversão', async () => {
  const q = await signMaterialLink('livro-desbloqueando-ia-pdf', 'leitor@exemplo.com', NOW, SECRET);
  const params = new URLSearchParams(q);
  const casos: Array<[Parameters<typeof setup>[0], RegExp]> = [
    [{ r2: { 'livro/desbloqueando-a-inteligencia-artificial.pdf': null } }, /livro-desbloqueando-ia-pdf.*objeto ausente/],
    [{ semR2: true }, /livro-desbloqueando-ia-pdf.*MATERIAIS_R2/],
    [{ r2Erro: true }, /livro-desbloqueando-ia-pdf.*R2 indisponivel/],
  ];
  for (const [opts, motivo] of casos) {
    const s = setup(opts);
    let r: Response | undefined;
    const erros = await capturaErros(async () => {
      r = await s.run(baixar(q));
      await s.settle();
    });
    assert.equal(r!.status, 500, JSON.stringify(opts));
    assert.equal(await r!.text(), 'arquivo indisponível', JSON.stringify(opts));
    assert.equal((await s.rows('conversions')).length, 0, JSON.stringify(opts));
    assert.equal(erros.length, 1, JSON.stringify(opts));
    assert.match(erros[0], motivo, JSON.stringify(opts));
    assert.ok(
      !/exemplo\.com|leitor/.test(erros[0]) && !erros[0].includes(params.get('s')!) && !erros[0].includes(params.get('x')!),
      erros[0],
    );
  }
});

test('L6: materiais anteriores continuam em /ferramentas/<id>/ sem tocar no R2', async () => {
  const s = setup();
  for (const [id, fileId] of [['planilha-custos-ia', 'planilha-custos-ia'], ['mapa-decisao-ia', 'mapa-decisao-pdf']] as const) {
    const r = await s.run(pedir({ ...ok, material: id }));
    assert.equal(loc(r), `https://guifav.dev/ferramentas/${id}/enviado/`);
    const dl = await s.run(baixar(await signMaterialLink(fileId, 'leitor@exemplo.com', NOW, SECRET)));
    assert.equal(dl.status, 200);
    assert.equal(dl.headers.get('content-length'), null);
  }
  assert.deepEqual(s.r2Gets, []);
});

// ---------------------------------------------------------------- Conversões (issue #97)

const sig = (q: string) => new URLSearchParams(q).get('s')!;
// Valor esperado calculado à parte (node:crypto), sem a função testada.
const idEsperado = (acao: string, s: string) => createHash('sha256').update(`${acao}\n${s}`).digest('hex');
const pedidoEsperado = (material: string, email: string, q: string) =>
  createHash('sha256').update(`material_request\n${material}\n${email}\n${new URLSearchParams(q).get('x')}`).digest('hex');

test('C1: o clique no link grava a conversão com email, material e interesses; o mesmo link aberto de novo não duplica', async () => {
  const s = setup();
  const q = await signMaterialLink('mapa-decisao-pdf', 'leitor@exemplo.com', NOW, SECRET);
  const erros = await capturaErros(async () => {
    assert.equal((await s.run(baixar(q))).status, 200);
    await s.settle();
    s.avanca(3600e3); // segundo clique uma hora depois: a primeira ocorrência fica como está
    assert.equal((await s.run(baixar(q))).status, 200);
    await s.settle();
  });
  assert.deepEqual(erros, [], 'o segundo clique é ignorado em silêncio, não vira erro de chave duplicada');
  const c = await s.rows('conversions');
  assert.equal(c.length, 1);
  assert.deepEqual(c[0], {
    id: idEsperado('material_download', sig(q)), email: 'leitor@exemplo.com', action: 'material_download',
    material: 'mapa-decisao-ia', interests: JSON.stringify([INTERESSES.ARQUITETURA_IA, INTERESSES.CUSTO_IA]),
    occurred_at: new Date(NOW).toISOString(), synced_at: null, verified: 1, ga_client_id: null,
    request_id: pedidoEsperado('mapa-decisao-ia', 'leitor@exemplo.com', q), file: 'mapa-decisao-pdf',
    ...SEM_ORIGEM, // link de um pedido sem linha no D1 (ou anterior à #114): o download não tem de onde herdar a origem
  });
  assert.ok(!JSON.stringify(c).includes(sig(q)), 'o D1 nunca guarda a assinatura do link');
});

test('C1: dois pedidos do mesmo material em dias diferentes viram duas conversões da mesma pessoa', async () => {
  const s = setup();
  const dia = 24 * 3600e3;
  await s.run(baixar(await signMaterialLink('planilha-custos-ia', 'leitor@exemplo.com', NOW - 3 * dia, SECRET)));
  await s.run(baixar(await signMaterialLink('planilha-custos-ia', 'leitor@exemplo.com', NOW - dia, SECRET)));
  const c = await s.rows('conversions');
  assert.equal(c.length, 2);
  assert.deepEqual(c.map((x) => [x.email, x.material]), [['leitor@exemplo.com', 'planilha-custos-ia'], ['leitor@exemplo.com', 'planilha-custos-ia']]);
});

test('C2: o pedido grava material_request com o email não comprovado, os interesses e o client_id do GA4 (#99)', async () => {
  const s = setup();
  await s.run(pedir({ ...ok, ga_cid: ' 1234567890.1700000000 ' }));
  const q = new URL(links(emails(s.calls)[0].body.html)[0]).search.slice(1);
  assert.deepEqual(await s.rows('conversions'), [{
    id: pedidoEsperado('planilha-custos-ia', 'leitor@exemplo.com', q), email: 'leitor@exemplo.com', action: 'material_request',
    material: 'planilha-custos-ia', interests: JSON.stringify([INTERESSES.CUSTO_IA]), occurred_at: new Date(NOW).toISOString(),
    synced_at: null, verified: 0, ga_client_id: '1234567890.1700000000', request_id: null, file: null,
    ...SEM_ORIGEM, origin_channel: 'desconhecida', // formulário sem os campos de origem (#114)
  }]);
  assert.equal((await s.rows('sqlite_master')).filter((t) => t.name === 'material_requests').length, 0, 'a contagem sem email saiu (0005)');
});

test('C2: ga_cid fora do formato não é gravado; sem ele, o pedido grava client_id nulo', async () => {
  for (const v of [undefined, '', 'GA1.1.123.456', '123', '1.2.3', 'abc.def', `${'1'.repeat(21)}.1`, '<email@x.com>']) {
    const s = setup();
    await s.run(pedir(v === undefined ? ok : { ...ok, ga_cid: v }));
    const [c] = await s.rows('conversions');
    assert.equal(c.ga_client_id, null, String(v));
    assert.equal(c.verified, 0);
  }
});

test('C2: pedido barrado (Turnstile, limite, email inválido) não grava nada', async () => {
  for (const o of [{ turnstile: { success: false } }, { rlEmail: false }, { rlIp: false }, { fields: { ...ok, email: 'sem-arroba' } }] as any[]) {
    const s = setup(o);
    await s.run(pedir(o.fields ?? ok));
    assert.equal((await s.rows('conversions')).length, 0, JSON.stringify(o));
  }
});

test('C3: D1 fora do ar não impede o download nem o email do pedido, e o log não leva o email', async () => {
  const s = setup({ d1Falha: /conversions/ });
  let r: Response | undefined;
  const erros = await capturaErros(async () => {
    r = await s.run(baixar(await signMaterialLink('planilha-custos-ia', 'leitor@exemplo.com', NOW, SECRET)));
    await s.run(pedir(ok));
    await s.settle();
  });
  assert.equal(r!.status, 200);
  assert.equal(await r!.text(), 'conteudo:planilha-custos-ia');
  assert.equal(emails(s.calls).length, 1);
  // O erro do D1 falso cita o email; o log troca por <email>.
  assert.deepEqual(erros.sort(), [
    'conversão não gravada (material_download): D1_ERROR: UNIQUE constraint failed for <email>',
    'conversão não gravada (material_request): D1_ERROR: UNIQUE constraint failed for <email>',
    'pedido não lido: D1_ERROR: UNIQUE constraint failed for <email>',
  ]);
  assert.ok(!erros.join('\n').includes('exemplo.com'));
});

test('C4: link inválido, expirado ou arquivo indisponível não gravam conversão', async () => {
  const q = await signMaterialLink('planilha-custos-ia', 'leitor@exemplo.com', NOW, SECRET);
  const expirado = setup({ now: NOW + MATERIAL_TTL_MS + 60e3 });
  await expirado.run(baixar(q));
  assert.equal((await expirado.rows('conversions')).length, 0);
  const adulterado = setup();
  await adulterado.run(baixar(q.replace('f=planilha-custos-ia', 'f=mapa-decisao-pdf')));
  assert.equal((await adulterado.rows('conversions')).length, 0);
  // Arquivo que some do deploy: 500 e nenhuma conversão.
  const s = setup();
  const r = await handle(baixar(q), s.env, { fetch: fetch, now: () => NOW, file: async () => null, waitUntil: s.waitUntil });
  await s.settle();
  assert.equal(r.status, 500);
  assert.equal((await s.rows('conversions')).length, 0);
});

test('C5: todo material declara ao menos um interesse do catálogo, com id no formato das opções do Twenty', () => {
  const ids = new Set(Object.values(INTERESSES).map((i) => i.id));
  for (const [k, i] of Object.entries(INTERESSES)) {
    assert.equal(i.id, k);
    assert.match(i.id, /^[A-Z][A-Z0-9_]*$/);
  }
  for (const m of MATERIAIS) {
    assert.ok(m.interesses.length > 0, `${m.id} sem interesse`);
    for (const i of m.interesses) assert.ok(ids.has(i.id), `${m.id}: interesse ${i.id} fora do catálogo`);
  }
});

test('C6: a assinatura só vale na forma canônica; as variantes com os mesmos bytes não entregam nem gravam', async () => {
  const s = setup();
  const q = await signMaterialLink('planilha-custos-ia', 'leitor@exemplo.com', NOW, SECRET);
  for (const v of variantes(sig(q))) {
    const r = await s.run(baixar(q.replace(`s=${sig(q)}`, `s=${v}`)));
    assert.equal(loc(r), 'https://guifav.dev/ferramentas/planilha-custos-ia/#link-expirado', v);
  }
  assert.equal((await s.rows('conversions')).length, 0);
  assert.equal((await s.run(baixar(q))).status, 200);
  assert.equal((await s.rows('conversions')).length, 1);
});

test('C3: D1 que não responde não segura a resposta do pedido nem do download (a gravação vai por waitUntil)', async () => {
  const s = setup({ d1Pendura: /conversions|material_requests/ });
  const limite = <T>(p: Promise<T>) => Promise.race([p, new Promise<never>((_, no) => setTimeout(() => no(new Error('a resposta esperou o D1')), 1000))]);
  const r1 = await limite(s.run(pedir(ok)));
  assert.equal(loc(r1), 'https://guifav.dev/ferramentas/planilha-custos-ia/enviado/');
  assert.equal(emails(s.calls).length, 1);
  const r2 = await limite(s.run(baixar(await signMaterialLink('planilha-custos-ia', 'leitor@exemplo.com', NOW, SECRET))));
  assert.equal(await r2.text(), 'conteudo:planilha-custos-ia');
});

test('C1: o id é um SHA-256 em hexadecimal, diferente da assinatura', async () => {
  const s = setup();
  const q = await signMaterialLink('planilha-custos-ia', 'leitor@exemplo.com', NOW, SECRET);
  await s.run(baixar(q));
  const [c] = await s.rows('conversions');
  assert.match(c.id, /^[0-9a-f]{64}$/);
  assert.notEqual(c.id, sig(q));
});

test('C9: a conversão do pedido e a do download passam por waitUntil e só gravam quando ele as completa', async () => {
  let libera!: () => void;
  const s = setup({ d1Porta: new Promise<void>((ok) => (libera = ok)) });
  await s.run(pedir(ok));
  await s.run(baixar(await signMaterialLink('planilha-custos-ia', 'leitor@exemplo.com', NOW, SECRET)));
  // Sem waitUntil, o Cloudflare pode cancelar a gravação depois da resposta: as duas precisam estar registradas nele.
  assert.equal(s.pendentes(), 2);
  assert.equal(s.db.prepare('SELECT count(*) AS n FROM conversions').get()!.n, 0);
  libera();
  assert.deepEqual((await s.rows('conversions')).map((r) => r.action).sort(), ['material_download', 'material_request']);
});

test('C10: o entrypoint do deploy repassa o ctx.waitUntil do pedido e o arquivo do material', async () => {
  const worker = readFileSync(new URL('../src/worker.ts', import.meta.url), 'utf-8');
  assert.match(worker, /^export default entrypoint\(async \(id\) => ARQUIVOS\[id\] \?\? null\);$/m);
  const s = setup();
  const ctx: Promise<unknown>[] = [];
  // Relógio real: o entrypoint usa Date.now.
  const q = await signMaterialLink('planilha-custos-ia', 'leitor@exemplo.com', Date.now(), SECRET);
  const r = await entrypoint(async (id) => new TextEncoder().encode(`real:${id}`).buffer as ArrayBuffer)
    .fetch(baixar(q), s.env, { waitUntil: (p) => void ctx.push(p) });
  assert.equal(await r.text(), 'real:planilha-custos-ia');
  assert.equal(ctx.length, 1, 'a gravação da conversão foi entregue ao ctx.waitUntil');
  await Promise.all(ctx);
  assert.equal(s.db.prepare('SELECT count(*) AS n FROM conversions').get()!.n, 1);
});

test('C11: contrato do mesmo segundo: pedidos a +100 ms e +900 ms dão o mesmo link e uma conversão; no segundo seguinte, outro link', async () => {
  const s = setup();
  const linkDo = (i: number) => links(emails(s.calls)[i].body.html)[0];
  s.avanca(100);
  await s.run(pedir(ok));
  s.avanca(800);
  await s.run(pedir(ok));
  assert.equal(linkDo(0), linkDo(1), 'mesmo segundo, mesmos bytes, mesmo link');
  s.avanca(200);
  await s.run(pedir(ok));
  assert.notEqual(linkDo(2), linkDo(0), 'outro segundo, outro link');
  for (const i of [0, 1, 2]) await s.run(new Request(linkDo(i)));
  const c = await s.rows('conversions');
  assert.equal(c.filter((x) => x.action === 'material_request').length, 2, 'dois pedidos: o do primeiro segundo e o do seguinte');
  assert.equal(c.filter((x) => x.action === 'material_download').length, 2);
});

// ---------------------------------------------------------------- GA4 pelo Measurement Protocol (issue #99)

const ga4 = (calls: Call[]) => calls.filter((c) => c.url.startsWith('https://www.google-analytics.com/mp/collect?'));

test('G1: o download manda material_download ao GA4 com o client_id do pedido e o liga ao pedido no D1', async () => {
  const s = setup();
  await s.run(pedir({ ...ok, material: 'mapa-decisao-ia', ga_cid: '1234567890.1700000000' }));
  const ls = links(emails(s.calls)[0].body.html);
  await s.settle();
  s.avanca(3600e3);
  for (const l of ls) await s.run(new Request(l));
  const c = await s.rows('conversions');
  const pedido = c.find((x) => x.action === 'material_request')!;
  const downloads = c.filter((x) => x.action === 'material_download');
  assert.equal(downloads.length, 2);
  assert.ok(downloads.every((d) => d.request_id === pedido.id && d.verified === 1), 'os dois arquivos apontam para o mesmo pedido');
  assert.deepEqual(downloads.map((d) => d.file).sort(), ['conta-custo-por-pergunta', 'mapa-decisao-pdf'], 'cada download guarda o arquivo');
  const g = ga4(s.calls);
  assert.equal(g.length, 2);
  const u = new URL(g[0].url);
  assert.deepEqual([u.searchParams.get('measurement_id'), u.searchParams.get('api_secret')], ['G-K7Y87QNCX8', 'mp_teste_segredo']);
  assert.deepEqual(g.map((x) => x.body), ls.map((l) => ({
    client_id: '1234567890.1700000000',
    consent: { ad_user_data: 'DENIED', ad_personalization: 'DENIED' },
    events: [{ name: 'material_download', params: { material_id: 'mapa-decisao-ia', file_id: new URL(l).searchParams.get('f'), engagement_time_msec: 1 } }],
  })));
  assert.ok(!JSON.stringify(g).includes('exemplo.com'), 'o GA4 nunca recebe o email');
});

test('G2: sem pedido no D1 ou pedido sem client_id, o evento vai com client_id aleatório (contagem anônima)', async () => {
  const s = setup();
  await s.run(pedir(ok)); // sem ga_cid: recusou os cookies
  await s.run(new Request(links(emails(s.calls)[0].body.html)[0]));
  await s.run(baixar(await signMaterialLink('planilha-custos-ia', 'outra@exemplo.com', NOW - 864e5, SECRET))); // sem pedido
  await s.settle();
  const ids = ga4(s.calls).map((x) => x.body.client_id);
  assert.equal(ids.length, 2);
  for (const id of ids) assert.match(id, /^\d{1,10}\.\d{10}$/);
  assert.notEqual(ids[0], ids[1]);
  assert.ok(!JSON.stringify(ga4(s.calls)).includes('exemplo.com'));
});

test('G3: GA4 fora do ar, com erro ou sem segredo não muda a entrega, e o log não leva segredo nem email', async () => {
  for (const o of [{ ga4Status: 500 }, { ga4Status: 'throw' }, { semGa4: true }] as const) {
    const s = setup(o);
    let r: Response | undefined;
    const erros = await capturaErros(async () => {
      r = await s.run(baixar(await signMaterialLink('planilha-custos-ia', 'leitor@exemplo.com', NOW, SECRET)));
      await s.settle();
    });
    assert.equal(await r!.text(), 'conteudo:planilha-custos-ia', JSON.stringify(o));
    assert.equal((await s.rows('conversions')).length, 1);
    assert.equal(erros.length, 1, JSON.stringify(o));
    assert.match(erros[0], /^evento do GA4 não enviado: /);
    assert.ok(!/mp_teste_segredo|exemplo\.com|api_secret/.test(erros[0]), erros[0]);
    if ('semGa4' in o) assert.equal(ga4(s.calls).length, 0);
  }
});

test('G4: o GA4 do Worker é o mesmo do site, e o segredo do Measurement Protocol está documentado e fora do toml', () => {
  const home = readFileSync(new URL('../../../index.html', import.meta.url), 'utf-8');
  assert.ok(home.includes(`gtag/js?id=${tomlVar('GA4_MEASUREMENT_ID')}"`));
  assert.match(TOML, /#\s+GA4_API_SECRET\s/);
  assert.doesNotMatch(TOML, /^\s*GA4_API_SECRET\s*=/m);
});

test('M10: o rodapé do email do material avisa que o pedido fica registrado e como pedir a remoção (#99)', async () => {
  const s = setup();
  await s.run(pedir(ok));
  const b = emails(s.calls)[0].body;
  for (const t of [b.text, b.html]) {
    assert.match(t, /o pedido fica registrado/);
    assert.match(t, /contact@guifav\.dev/);
    assert.doesNotMatch(t, /nada mais será enviado/);
  }
});

test('G5: o envio ao GA4 fica dentro do waitUntil: GA4 travado não segura o download, e o trabalho só termina com ele', async () => {
  const limite = <T>(p: Promise<T>) => Promise.race([p, new Promise<never>((_, no) => setTimeout(() => no(new Error('a resposta esperou o GA4')), 1000))]);
  const travado = setup({ ga4Pendura: true });
  const r = await limite(travado.run(baixar(await signMaterialLink('planilha-custos-ia', 'leitor@exemplo.com', NOW, SECRET))));
  assert.equal(await r.text(), 'conteudo:planilha-custos-ia');
  let libera!: () => void;
  const s = setup({ ga4Porta: new Promise<void>((ok) => (libera = ok)) });
  await s.run(baixar(await signMaterialLink('planilha-custos-ia', 'leitor@exemplo.com', NOW, SECRET)));
  let terminou = false;
  const fim = s.settle().then(() => (terminou = true));
  await new Promise((ok) => setTimeout(ok, 50));
  assert.equal(terminou, false, 'o waitUntil ainda segura o envio ao GA4');
  libera();
  await fim;
  assert.equal(ga4(s.calls).length, 1);
});

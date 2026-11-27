// Testes do heating de contatos (épico #197, corte 1): migrações 0010 e 0011, POST /resend/webhook e POST /prioridade.
// Rodar em workers/newsletter/: npm test
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash, createHmac } from 'node:crypto';
import { readFileSync, readdirSync } from 'node:fs';
import { DatabaseSync } from 'node:sqlite';
import { handle, type Env } from '../src/index.ts';
import { forgetHmac, heatingOptoutHmac } from '../src/forget.ts';
import { MAX_WEBHOOK_BODY } from '../src/resend-webhook.ts';
import { d1, rows, capturaErros } from './d1.ts';

const NOW = Date.UTC(2026, 10, 27, 12, 0, 0);
const SECRET = 'segredo-de-teste-com-tamanho-suficiente-0123456789';
const FORGET_KEY = 'chave-hmac-de-teste-com-32-bytes-ou-mais-0123456789';
// Chave svix fictícia de 24 bytes em base64 (formato `whsec_` + base64).
const WEBHOOK_RAW = Buffer.from('0123456789abcdef01234567', 'utf-8');
const WEBHOOK_SECRET = `whsec_${WEBHOOK_RAW.toString('base64')}`;

const TOML = readFileSync(new URL('../wrangler.toml', import.meta.url), 'utf-8');
const tomlVar = (k: string) => TOML.match(new RegExp(`^${k}\\s*=\\s*"([^"]*)"`, 'm'))![1];

function assinarSvix(id: string, tsSec: number, body: string, rawKey: Buffer = WEBHOOK_RAW): string {
  const mac = createHmac('sha256', rawKey).update(`${id}.${tsSec}.${body}`).digest('base64');
  return `v1,${mac}`;
}

type SetupOpts = {
  now?: number;
  webhookSecret?: string | undefined;
  forgetKey?: string;
  turnstile?: unknown;
  rlIp?: boolean;
  d1Falha?: RegExp;
  d1Mensagem?: string;
};

function setup(opts: SetupOpts = {}) {
  const calls: Array<{ url: string; method: string; body: unknown }> = [];
  const rl: string[] = [];
  const db = d1(opts.d1Falha, opts.d1Mensagem ?? 'D1_ERROR: falha para leitor@exemplo.com');
  const pending: Promise<unknown>[] = [];
  const waitUntil = (p: Promise<unknown>) => void pending.push(p);
  const fetchFn = async (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input instanceof Request ? input.url : input);
    const method = init?.method ?? 'GET';
    let body: unknown = init?.body;
    if (body instanceof URLSearchParams) body = Object.fromEntries(body);
    calls.push({ url, method, body });
    if (url.includes('turnstile')) {
      if (opts.turnstile instanceof Error) throw opts.turnstile;
      return Response.json(opts.turnstile ?? { success: true, hostname: 'guifav.dev' });
    }
    return new Response('inesperado', { status: 500 });
  };
  const env: Env = {
    RESEND_API_KEY: 're_teste',
    TURNSTILE_SECRET: 'ts_teste',
    CONFIRM_SECRET: SECRET,
    FORGET_HMAC_KEY: opts.forgetKey ?? FORGET_KEY,
    RESEND_HEATING_WEBHOOK_SECRET: 'webhookSecret' in opts ? opts.webhookSecret : WEBHOOK_SECRET,
    FROM: tomlVar('FROM'),
    MATERIAL_FROM: tomlVar('MATERIAL_FROM'),
    REPLY_TO: tomlVar('REPLY_TO'),
    SEGMENT_ID: 'seg-1',
    TOPIC_ID: 'topic-1',
    SITE_ORIGIN: tomlVar('SITE_ORIGIN'),
    BLOG_URL: tomlVar('BLOG_URL'),
    WORKER_URL: tomlVar('WORKER_URL'),
    TURNSTILE_HOSTNAME: tomlVar('TURNSTILE_HOSTNAME'),
    RL_EMAIL: { limit: async ({ key }) => (rl.push('email:' + key), { success: true }) },
    RL_IP: { limit: async ({ key }) => (rl.push('ip:' + key), { success: opts.rlIp ?? true }) },
    DB: db,
    GA4_MEASUREMENT_ID: tomlVar('GA4_MEASUREMENT_ID'),
    GA4_API_SECRET: 'mp_teste_segredo',
  };
  let clock = opts.now ?? NOW;
  const run = (req: Request) => handle(req, env, { fetch: fetchFn as typeof fetch, now: () => clock, waitUntil });
  const avanca = (ms: number) => void (clock += ms);
  return { env, run, calls, rl, db: db.raw, pendentes: () => pending.length, avanca, rows: (t: string) => rows(db.raw, t) };
}

function webhookReq(
  payload: unknown,
  opts: {
    id?: string;
    tsSec?: number;
    sig?: string;
    rawBody?: string;
    extraHeaders?: Record<string, string>;
    method?: string;
  } = {},
): Request {
  const id = opts.id ?? 'msg_01JABC123456';
  const tsSec = opts.tsSec ?? Math.floor(NOW / 1000);
  const body = opts.rawBody ?? JSON.stringify(payload);
  const sig = opts.sig ?? assinarSvix(id, tsSec, body);
  return new Request('https://insights.guifav.dev/resend/webhook', {
    method: opts.method ?? 'POST',
    headers: {
      'content-type': 'application/json',
      'svix-id': id,
      'svix-timestamp': String(tsSec),
      'svix-signature': sig,
      ...opts.extraHeaders,
    },
    body: opts.method && opts.method !== 'POST' ? undefined : body,
  });
}

function prioridadeReq(fields: Record<string, string>, headers: Record<string, string> = {}): Request {
  return new Request('https://insights.guifav.dev/prioridade', {
    method: 'POST',
    headers: {
      'content-type': 'application/x-www-form-urlencoded',
      origin: 'https://guifav.dev',
      'cf-connecting-ip': '203.0.113.9',
      ...headers,
    },
    body: new URLSearchParams(fields).toString(),
  });
}

const loc = (r: Response) => r.headers.get('location');

// ---------------------------------------------------------------- Migração 0010 e 0011

test('M1: migração 0010 preserva linhas e ordem exata das 18 colunas de conversions e passa a recusar verified fora de 0 e 1', () => {
  const db = new DatabaseSync(':memory:');
  const dir = new URL('../../admin/migrations/', import.meta.url);
  const sqlFiles = readdirSync(dir).filter((f) => f.endsWith('.sql')).sort();
  for (const f of sqlFiles.filter((f) => f < '0010_')) {
    db.exec(readFileSync(new URL(f, dir), 'utf-8'));
  }

  const colsAntes = (db.prepare('PRAGMA table_info(conversions)').all() as Array<{ name: string }>).map((c) => c.name);
  db.prepare(`INSERT INTO conversions
    (id, email, action, material, interests, occurred_at, synced_at, verified, ga_client_id, request_id, file,
     origin_channel, origin_source, utm_source, utm_medium, utm_campaign, landing_path, twenty_person_id)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`)
    .run('id-0', 'um@exemplo.com', 'material_request', 'planilha-custos-ia', '[{"id":"CUSTO_IA","rotulo":"Custo de IA"}]',
      '2026-11-26T10:00:00.000Z', null, 0, '123.456', null, null, 'busca', 'google', 'g', 'organic', 'camp', '/ferramentas/planilha-custos-ia/', null);
  db.prepare(`INSERT INTO conversions
    (id, email, action, material, interests, occurred_at, synced_at, verified, ga_client_id, request_id, file,
     origin_channel, origin_source, utm_source, utm_medium, utm_campaign, landing_path, twenty_person_id)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`)
    .run('id-1', 'dois@exemplo.com', 'newsletter_confirm', null, '[{"id":"EXPERT_INSIGHTS","rotulo":"Expert Insights"}]',
      '2026-11-26T11:00:00.000Z', '2026-11-26T11:23:00.000Z', 1, null, null, null, 'direto', null, null, null, null, '/blog/assinar/', 'person-uuid-1');

  const linhasAntes = rows(db, 'conversions');

  db.exec(readFileSync(new URL('0010_conversions_verified_check.sql', dir), 'utf-8'));

  const colsDepois = (db.prepare('PRAGMA table_info(conversions)').all() as Array<{ name: string }>).map((c) => c.name);
  assert.deepEqual(colsDepois, colsAntes);
  assert.deepEqual(rows(db, 'conversions'), linhasAntes);

  const indices = (db.prepare("SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'conversions' AND name NOT LIKE 'sqlite_%' ORDER BY name")
    .all() as Array<{ name: string }>).map((i) => i.name);
  assert.deepEqual(indices, ['conversions_email', 'conversions_pending']);

  // Depois da 0010, verified = 2 ou -1 é recusado pelo CHECK.
  for (const ruim of [2, -1]) {
    assert.throws(
      () => db.prepare(`INSERT INTO conversions (id, email, action, interests, occurred_at, verified)
        VALUES (?, 'tres@exemplo.com', 'material_request', '[]', '2026-11-27T12:00:00.000Z', ?)`).run(`bad-${ruim}`, ruim),
      /CHECK constraint failed/,
      `verified=${ruim}`,
    );
  }
});

test('M2: migração 0010 falha sem perder a tabela original se houver linha com verified = 2 antes dela', () => {
  const db = new DatabaseSync(':memory:');
  const dir = new URL('../../admin/migrations/', import.meta.url);
  const sqlFiles = readdirSync(dir).filter((f) => f.endsWith('.sql')).sort();
  for (const f of sqlFiles.filter((f) => f < '0010_')) {
    db.exec(readFileSync(new URL(f, dir), 'utf-8'));
  }

  db.prepare(`INSERT INTO conversions (id, email, action, interests, occurred_at, verified)
    VALUES ('ok-1', 'ok@exemplo.com', 'newsletter_confirm', '[]', '2026-11-26T10:00:00.000Z', 1)`).run();
  db.prepare(`INSERT INTO conversions (id, email, action, interests, occurred_at, verified)
    VALUES ('bad-2', 'bad@exemplo.com', 'material_request', '[]', '2026-11-26T10:01:00.000Z', 2)`).run();

  assert.throws(
    () => db.exec(readFileSync(new URL('0010_conversions_verified_check.sql', dir), 'utf-8')),
    /CHECK constraint failed/,
  );

  // A tabela conversions original permanece inteira com as duas linhas.
  const restantes = rows(db, 'conversions');
  assert.equal(restantes.length, 2);
  assert.deepEqual(restantes.map((r) => [r.id, r.verified]), [['ok-1', 1], ['bad-2', 2]]);
});

test('M3: migração 0011 aplica CHECKs de engagement_events, heating e heating_optout', () => {
  const db = d1().raw;
  const iso = '2026-11-27T12:00:00.000Z';

  // engagement_events: válido passa; sinal, fonte, email maiúsculo, id/ref e data fora do formato são recusados.
  db.prepare(`INSERT INTO engagement_events (id, email, signal, source, ref, occurred_at, recorded_at)
    VALUES ('rs:abc_1.2-3', 'leitor@exemplo.com', 'email_aberto', 'resend', 'email_123', ?, ?)`).run(iso, iso);
  assert.throws(() => db.prepare(`INSERT INTO engagement_events (id, email, signal, source, ref, occurred_at, recorded_at)
    VALUES ('id-2', 'Leitor@exemplo.com', 'email_aberto', 'resend', null, ?, ?)`).run(iso, iso), /CHECK/);
  assert.throws(() => db.prepare(`INSERT INTO engagement_events (id, email, signal, source, ref, occurred_at, recorded_at)
    VALUES ('id-3', 'leitor@exemplo.com', 'sinal_falso', 'resend', null, ?, ?)`).run(iso, iso), /CHECK/);
  assert.throws(() => db.prepare(`INSERT INTO engagement_events (id, email, signal, source, ref, occurred_at, recorded_at)
    VALUES ('id-4', 'leitor@exemplo.com', 'email_aberto', 'outra', null, ?, ?)`).run(iso, iso), /CHECK/);
  assert.throws(() => db.prepare(`INSERT INTO engagement_events (id, email, signal, source, ref, occurred_at, recorded_at)
    VALUES ('id/ruim', 'leitor@exemplo.com', 'email_aberto', 'resend', null, ?, ?)`).run(iso, iso), /CHECK/);
  assert.throws(() => db.prepare(`INSERT INTO engagement_events (id, email, signal, source, ref, occurred_at, recorded_at)
    VALUES ('id-5', 'leitor@exemplo.com', 'email_aberto', 'resend', null, '2026-11-27T12:00:00Z', ?)`).run(iso), /CHECK/);

  // heating: score 0..100, band, json_valid(signals) <= 2000, twenty_person_id.
  db.prepare(`INSERT INTO heating (email, score, band, signals, computed_at, twenty_person_id)
    VALUES ('leitor@exemplo.com', 75, 'quente', '["site_agende","email_clicado"]', ?, 'uuid-123')`).run(iso);
  assert.throws(() => db.prepare(`INSERT INTO heating (email, score, band, signals, computed_at)
    VALUES ('a@b.co', 101, 'quente', '[]', ?)`).run(iso), /CHECK/);
  assert.throws(() => db.prepare(`INSERT INTO heating (email, score, band, signals, computed_at)
    VALUES ('a@b.co', 50, 'fervendo', '[]', ?)`).run(iso), /CHECK/);
  assert.throws(() => db.prepare(`INSERT INTO heating (email, score, band, signals, computed_at)
    VALUES ('a@b.co', 50, 'morno', 'nao-json', ?)`).run(iso), /CHECK/);
  assert.throws(() => db.prepare(`INSERT INTO heating (email, score, band, signals, computed_at, twenty_person_id)
    VALUES ('a@b.co', 50, 'morno', '[]', ?, 'id/invalido')`).run(iso), /CHECK/);

  // heating_optout: 64 hex minúsculos e ISO UTC com ms.
  db.prepare('INSERT INTO heating_optout (email_hmac, opted_out_at) VALUES (?, ?)').run('a'.repeat(64), iso);
  assert.throws(() => db.prepare('INSERT INTO heating_optout (email_hmac, opted_out_at) VALUES (?, ?)').run('A'.repeat(64), iso), /CHECK/);
  assert.throws(() => db.prepare('INSERT INTO heating_optout (email_hmac, opted_out_at) VALUES (?, ?)').run('b'.repeat(64), '2026-11-27'), /CHECK/);
});

// ---------------------------------------------------------------- HMAC com prefixo separado

test('H1: heatingOptoutHmac usa prefixo próprio e nunca coincide com forgetHmac', async () => {
  const hForget = await forgetHmac('leitor@exemplo.com', FORGET_KEY);
  const hOptout = await heatingOptoutHmac('leitor@exemplo.com', FORGET_KEY);
  assert.notEqual(hOptout, hForget);
  assert.equal(hOptout, await heatingOptoutHmac('  LEITOR@EXEMPLO.COM ', FORGET_KEY));
  assert.equal(
    hOptout,
    createHmac('sha256', FORGET_KEY).update('heating-optout-v1\nleitor@exemplo.com').digest('hex'),
  );
});

// ---------------------------------------------------------------- POST /resend/webhook

test('W1: assinatura válida grava os quatro tipos mapeados de forma síncrona (sem waitUntil)', async () => {
  const s = setup();
  const casos: Array<[string, string, Record<string, unknown>]> = [
    ['email.delivered', 'email_entregue', { email_id: 'em_del_1', to: ['Leitor@Exemplo.com'] }],
    ['email.opened', 'email_aberto', { email_id: 'em_open_1', to: 'leitor@exemplo.com' }],
    ['email.clicked', 'email_clicado', { email_id: 'em_click_1', to: ['leitor@exemplo.com'], click: { link: 'https://guifav.dev/blog/artigo/?utm_source=email' } }],
    ['email.complained', 'email_spam', { email_id: 'em_spam_1', to: ['leitor@exemplo.com'] }],
  ];
  for (let i = 0; i < casos.length; i++) {
    const [type, expectedSignal, data] = casos[i];
    const svixId = `msg_teste_${i}`;
    const r = await s.run(webhookReq({ type, created_at: '2026-11-27T08:59:10.123456-03:00', data }, { id: svixId }));
    assert.equal(r.status, 200, type);
    assert.deepEqual(await r.json(), { ok: true, gravados: 1 });
    assert.equal(s.pendentes(), 0, 'gravação do webhook deve ser síncrona, fora de waitUntil');
    const todas = s.rows('engagement_events');
    assert.equal(todas.length, i + 1);
    const linha = todas[i];
    assert.deepEqual(linha, {
      id: `rs:${createHash('sha256').update(`${svixId}\nleitor@exemplo.com`).digest('hex')}`,
      email: 'leitor@exemplo.com',
      signal: expectedSignal,
      source: 'resend',
      ref: data.email_id,
      occurred_at: '2026-11-27T11:59:10.123Z',
      recorded_at: new Date(NOW).toISOString(),
    });
    // O link do clique nunca é gravado em nenhuma coluna.
    assert.ok(!JSON.stringify(linha).includes('guifav.dev'));
  }
});

test('W2: múltiplas assinaturas em svix-signature (rotação): aceita quando qualquer v1 bate', async () => {
  const s = setup();
  const body = JSON.stringify({ type: 'email.opened', created_at: '2026-11-27T12:00:00.000Z', data: { email_id: 'em_rot', to: ['a@b.com'] } });
  const ts = Math.floor(NOW / 1000);
  const valida = assinarSvix('msg_rot_1', ts, body);
  const outra = assinarSvix('msg_rot_1', ts, body, Buffer.from('ffffffffffffffffffffffff', 'utf-8'));
  const r = await s.run(webhookReq(null, { id: 'msg_rot_1', tsSec: ts, rawBody: body, sig: `v2,AAAA ${outra} ${valida}` }));
  assert.equal(r.status, 200);
  assert.equal(s.rows('engagement_events').length, 1);
});

test('W3: assinatura errada, timestamp fora da janela e svix-id inválido devolvem 401 sem gravar', async () => {
  const s = setup();
  const payload = { type: 'email.opened', created_at: '2026-11-27T12:00:00.000Z', data: { email_id: 'em_1', to: ['a@b.com'] } };
  const tsNow = Math.floor(NOW / 1000);

  // Assinatura errada
  assert.equal((await s.run(webhookReq(payload, { sig: assinarSvix('msg_1', tsNow, JSON.stringify(payload), Buffer.from('chave-errada-0123456789a', 'utf-8')) }))).status, 401);
  // Sem header svix-signature
  assert.equal((await s.run(webhookReq(payload, { sig: '' }))).status, 401);
  // Timestamp 5 min + 1 s no passado e no futuro
  assert.equal((await s.run(webhookReq(payload, { tsSec: tsNow - 301 }))).status, 401);
  assert.equal((await s.run(webhookReq(payload, { tsSec: tsNow + 301 }))).status, 401);
  // svix-id inválido (espaço, barra, longo demais)
  for (const badId of ['msg/1', 'msg 1', 'x'.repeat(101), 'msg:1']) {
    assert.equal((await s.run(webhookReq(payload, { id: badId }))).status, 401, badId);
  }
  assert.equal(s.rows('engagement_events').length, 0);

  // No limite exato da janela (300 s) ainda passa.
  assert.equal((await s.run(webhookReq(payload, { id: 'msg_lim_pass', tsSec: tsNow - 300 }))).status, 200);
  assert.equal((await s.run(webhookReq(payload, { id: 'msg_lim_fut', tsSec: tsNow + 300 }))).status, 200);
  assert.equal(s.rows('engagement_events').length, 2);
});

test('W4: segredo ausente ou fora do formato devolve 503 sem ler o corpo', async () => {
  for (const badSecret of [undefined, '', 'sem-prefixo-01234567890123456789', 'whsec_', 'whsec_!!!', 'whsec_AAAA']) {
    const s = setup({ webhookSecret: badSecret });
    let leuCorpo = false;
    const stream = new ReadableStream({
      pull(controller) {
        leuCorpo = true;
        controller.enqueue(new TextEncoder().encode('{}'));
        controller.close();
      },
    }, { highWaterMark: 0 });
    const req = new Request('https://insights.guifav.dev/resend/webhook', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: stream,
      duplex: 'half',
    } as RequestInit);
    const r = await s.run(req);
    assert.equal(r.status, 503, String(badSecret));
    assert.equal(await r.text(), 'webhook não configurado');
    assert.equal(leuCorpo, false, `não deve ler o corpo com segredo=${String(badSecret)}`);
    assert.equal(req.bodyUsed, false, `bodyUsed deve continuar false com segredo=${String(badSecret)}`);
    assert.equal(s.rows('engagement_events').length, 0);
  }
});

test('W5: método diferente de POST devolve 405 com allow: POST', async () => {
  const s = setup();
  for (const method of ['GET', 'PUT', 'HEAD']) {
    const r = await s.run(new Request('https://insights.guifav.dev/resend/webhook', { method }));
    assert.equal(r.status, 405, method);
    assert.equal(r.headers.get('allow'), 'POST', method);
  }
});

test('W6: reentrega com o mesmo svix-id não duplica', async () => {
  const s = setup();
  const payload = { type: 'email.delivered', created_at: '2026-11-27T12:00:00.000Z', data: { email_id: 'em_dup', to: ['leitor@exemplo.com'] } };
  assert.equal((await s.run(webhookReq(payload, { id: 'msg_mesmo' }))).status, 200);
  s.avanca(10_000);
  assert.equal((await s.run(webhookReq(payload, { id: 'msg_mesmo', tsSec: Math.floor((NOW + 10_000) / 1000) }))).status, 200);
  const todas = s.rows('engagement_events');
  assert.equal(todas.length, 1);
  assert.equal(todas[0].recorded_at, new Date(NOW).toISOString());
});

test('W7: vários destinatários gravam uma linha por email válido e pulam inválidos sem logar o email', async () => {
  const s = setup();
  const payload = {
    type: 'email.delivered',
    created_at: '2026-11-27T12:00:00.000Z',
    data: { email_id: 'em_multi', to: ['Um@Exemplo.com', 'dois@exemplo.com', 'um@exemplo.com', 'invalido-sem-arroba', 'ruim@segredo.local\nbcc'] },
  };
  let r!: Response;
  const erros = await capturaErros(async () => {
    r = await s.run(webhookReq(payload, { id: 'msg_multi' }));
  });
  assert.equal(r.status, 200);
  assert.deepEqual(await r.json(), { ok: true, gravados: 2 });
  const todas = s.rows('engagement_events');
  assert.deepEqual(todas.map((x) => x.email), ['um@exemplo.com', 'dois@exemplo.com']);
  assert.equal(erros.length, 1);
  assert.match(erros[0], /2 destinatário/);
  assert.ok(!erros[0].includes('segredo.local'), erros[0]);
  assert.ok(!erros[0].includes('invalido-sem-arroba'), erros[0]);
});

test('W8: cliques de descadastro, /prioridade e URL inválida são ignorados com 200 sem gravar', async () => {
  const s = setup();
  const linksIgnorados = [
    'https://resend.com/unsubscribe/abc',
    'https://links.resend.com/u/123',
    'https://track.resend.dev/x',
    'https://guifav.dev/blog/UNSUBSCRIBE/1',
    'https://guifav.dev/descadastrar/',
    'https://guifav.dev/prioridade',
    'https://guifav.dev/prioridade/',
    'https://guifav.dev/PRIORIDADE/?utm=1',
    'https://insights.guifav.dev/prioridade',
    'mailto:contact@guifav.dev',
    'javascript:alert(1)',
    'nao-e-url',
    '',
  ];
  for (let i = 0; i < linksIgnorados.length; i++) {
    const link = linksIgnorados[i];
    const r = await s.run(webhookReq({
      type: 'email.clicked',
      created_at: '2026-11-27T12:00:00.000Z',
      data: { email_id: 'em_click', to: ['leitor@exemplo.com'], click: { link } },
    }, { id: `msg_ign_${i}` }));
    assert.equal(r.status, 200, link);
    const body = (await r.json()) as { ok: boolean; ignorado?: string };
    assert.equal(body.ok, true, link);
    assert.ok(typeof body.ignorado === 'string' && body.ignorado.length > 0, link);
  }
  assert.equal(s.rows('engagement_events').length, 0);

  // Links parecidos mas legítimos continuam pontuando.
  for (const [i, link] of ['https://notresend.com/artigo', 'https://guifav.dev/blog/prioridades/'].entries()) {
    const r = await s.run(webhookReq({
      type: 'email.clicked',
      created_at: '2026-11-27T12:00:00.000Z',
      data: { email_id: 'em_click_ok', to: ['leitor@exemplo.com'], click: { link } },
    }, { id: `msg_ok_${i}` }));
    assert.equal(r.status, 200, link);
  }
  assert.equal(s.rows('engagement_events').length, 2);
});

test('W9: tipo que não interessa responde 200 com ignorado sem gravar', async () => {
  const s = setup();
  const r = await s.run(webhookReq({ type: 'email.sent', created_at: '2026-11-27T12:00:00.000Z', data: { email_id: 'em_1' } }));
  assert.equal(r.status, 200);
  assert.deepEqual(await r.json(), { ok: true, ignorado: 'email.sent' });
  assert.equal(s.rows('engagement_events').length, 0);
});

test('W10: corpo maior que 64 KiB devolve 413 (com e sem content-length)', async () => {
  const s = setup();
  const grande = 'x'.repeat(MAX_WEBHOOK_BODY + 1);
  assert.equal((await s.run(webhookReq(null, { rawBody: grande }))).status, 413);

  // Streaming sem content-length também barra ao passar de 64 KiB.
  const stream = new ReadableStream({
    start(c) {
      c.enqueue(new TextEncoder().encode('a'.repeat(40_000)));
      c.enqueue(new TextEncoder().encode('b'.repeat(30_000)));
      c.close();
    },
  });
  const reqStream = new Request('https://insights.guifav.dev/resend/webhook', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: stream,
    duplex: 'half',
  } as RequestInit);
  assert.equal((await s.run(reqStream)).status, 413);
  assert.equal(s.rows('engagement_events').length, 0);
});

test('W11: JSON inválido e campos fora do formato devolvem 400 sem gravar', async () => {
  const s = setup();
  const invalidos: Array<[string, string]> = [
    ['json quebrado', '{nao-json'],
    ['array na raiz', '[]'],
    ['sem type', JSON.stringify({ created_at: '2026-11-27T12:00:00.000Z', data: {} })],
    ['data inválida', JSON.stringify({ type: 'email.opened', created_at: 'ontem', data: { email_id: 'e1', to: ['a@b.com'] } })],
    ['data inexistente', JSON.stringify({ type: 'email.opened', created_at: '2026-02-30T12:00:00Z', data: { email_id: 'e1', to: ['a@b.com'] } })],
    ['email_id inválido', JSON.stringify({ type: 'email.opened', created_at: '2026-11-27T12:00:00Z', data: { email_id: 'id com espaco', to: ['a@b.com'] } })],
    ['to vazio', JSON.stringify({ type: 'email.opened', created_at: '2026-11-27T12:00:00Z', data: { email_id: 'e1', to: [] } })],
    ['to não string', JSON.stringify({ type: 'email.opened', created_at: '2026-11-27T12:00:00Z', data: { email_id: 'e1', to: [123] } })],
    ['clique sem click.link', JSON.stringify({ type: 'email.clicked', created_at: '2026-11-27T12:00:00Z', data: { email_id: 'e1', to: ['a@b.com'] } })],
  ];
  for (const [nome, rawBody] of invalidos) {
    const r = await s.run(webhookReq(null, { rawBody }));
    assert.equal(r.status, 400, nome);
  }
  assert.equal(s.rows('engagement_events').length, 0);
});

test('W12: email com oposição em heating_optout não grava (200), enquanto outro destinatário do mesmo envio grava', async () => {
  const s = setup();
  const hmac = await heatingOptoutHmac('oposto@exemplo.com', FORGET_KEY);
  s.db.prepare('INSERT INTO heating_optout (email_hmac, opted_out_at) VALUES (?, ?)').run(hmac, new Date(NOW).toISOString());

  const r = await s.run(webhookReq({
    type: 'email.opened',
    created_at: '2026-11-27T12:00:00.000Z',
    data: { email_id: 'em_opt', to: ['oposto@exemplo.com', 'ativo@exemplo.com'] },
  }, { id: 'msg_opt_1' }));
  assert.equal(r.status, 200);
  assert.deepEqual(await r.json(), { ok: true, gravados: 1 });
  assert.deepEqual(s.rows('engagement_events').map((x) => x.email), ['ativo@exemplo.com']);
});

test('W13: falha do D1 no webhook devolve 500 e o log não contém o email', async () => {
  for (const regex of [/heating_optout/, /engagement_events/]) {
    const s = setup({ d1Falha: regex, d1Mensagem: 'D1_ERROR: constraint para sigiloso@exemplo.com' });
    let r!: Response;
    const erros = await capturaErros(async () => {
      r = await s.run(webhookReq({
        type: 'email.opened',
        created_at: '2026-11-27T12:00:00.000Z',
        data: { email_id: 'em_err', to: ['sigiloso@exemplo.com'] },
      }));
    });
    assert.equal(r.status, 500, String(regex));
    assert.equal(erros.length, 1);
    assert.ok(!erros[0].includes('sigiloso'), erros[0]);
    assert.ok(!erros[0].includes('exemplo.com'), erros[0]);
    assert.ok(erros[0].includes('<email>'), erros[0]);
  }
});

// ---------------------------------------------------------------- POST /prioridade

test('P1: /prioridade grava a marca, apaga eventos e heating só do email informado e não chama o Resend', async () => {
  const s = setup();
  const iso = new Date(NOW - 60_000).toISOString();
  for (const email of ['leitor@exemplo.com', 'outro@exemplo.com']) {
    s.db.prepare(`INSERT INTO engagement_events (id, email, signal, source, ref, occurred_at, recorded_at)
      VALUES (?, ?, 'email_aberto', 'resend', 'em_1', ?, ?)`).run(`rs:${email.split('@')[0]}`, email, iso, iso);
    s.db.prepare(`INSERT INTO heating (email, score, band, signals, computed_at)
      VALUES (?, 60, 'morno', '["email_aberto"]', ?)`).run(email, iso);
  }

  const r = await s.run(prioridadeReq({ email: '  Leitor@Exemplo.COM ', 'cf-turnstile-response': 'tok' }));
  assert.equal(r.status, 303);
  assert.equal(loc(r), 'https://guifav.dev/prioridade/#pronto');
  assert.equal(s.calls.filter((c) => c.url.includes('resend.com')).length, 0);

  const optouts = s.rows('heating_optout');
  assert.equal(optouts.length, 1);
  assert.deepEqual(optouts[0], {
    email_hmac: await heatingOptoutHmac('leitor@exemplo.com', FORGET_KEY),
    opted_out_at: new Date(NOW).toISOString(),
  });
  assert.deepEqual(s.rows('engagement_events').map((x) => x.email), ['outro@exemplo.com']);
  assert.deepEqual(s.rows('heating').map((x) => x.email), ['outro@exemplo.com']);

  // Repetir a oposição depois atualiza opted_out_at (upsert) sem erro.
  s.avanca(5_000);
  const r2 = await s.run(prioridadeReq({ email: 'leitor@exemplo.com', 'cf-turnstile-response': 'tok' }));
  assert.equal(r2.status, 303);
  assert.equal(loc(r2), 'https://guifav.dev/prioridade/#pronto');
  assert.equal(s.rows('heating_optout')[0].opted_out_at, new Date(NOW + 5_000).toISOString());

  // Webhook que chega depois não volta a gravar eventos do email oposto.
  const rw = await s.run(webhookReq({
    type: 'email.clicked',
    created_at: new Date(NOW + 5_000).toISOString(),
    data: { email_id: 'em_pos', to: ['leitor@exemplo.com'], click: { link: 'https://guifav.dev/blog/post/' } },
  }, { id: 'msg_pos_optout', tsSec: Math.floor((NOW + 5_000) / 1000) }));
  assert.equal(rw.status, 200);
  assert.deepEqual(s.rows('engagement_events').map((x) => x.email), ['outro@exemplo.com']);
});

test('P2: /prioridade aceita multipart/form-data e não revela se o email existia', async () => {
  const s = setup();
  const fd = new FormData();
  fd.set('email', 'nunca-visto@exemplo.com');
  fd.set('cf-turnstile-response', 'tok');
  const r = await s.run(new Request('https://insights.guifav.dev/prioridade', {
    method: 'POST',
    headers: { origin: 'https://guifav.dev', 'cf-connecting-ip': '203.0.113.9' },
    body: fd,
  }));
  assert.equal(r.status, 303);
  assert.equal(loc(r), 'https://guifav.dev/prioridade/#pronto');
  assert.equal(s.rows('heating_optout').length, 1);
  assert.equal(s.rows('heating_optout')[0].email_hmac, await heatingOptoutHmac('nunca-visto@exemplo.com', FORGET_KEY));
});

test('P3: /prioridade valida método, Origin, tamanho do corpo, limite por IP, Turnstile e email', async () => {
  const s = setup();
  // Método != POST
  const rGet = await s.run(new Request('https://insights.guifav.dev/prioridade'));
  assert.equal(rGet.status, 405);
  assert.equal(rGet.headers.get('allow'), 'POST');

  // Origin errado ou ausente
  assert.equal((await s.run(prioridadeReq({ email: 'a@b.com', 'cf-turnstile-response': 'tok' }, { origin: 'https://evil.example' }))).status, 403);
  const semOrigin = new Request('https://insights.guifav.dev/prioridade', {
    method: 'POST',
    headers: { 'content-type': 'application/x-www-form-urlencoded' },
    body: 'email=a@b.com&cf-turnstile-response=tok',
  });
  assert.equal((await s.run(semOrigin)).status, 403);

  // Corpo maior que 8192 bytes
  assert.equal((await s.run(prioridadeReq({ email: 'a@b.com', 'cf-turnstile-response': 'tok', lixo: 'x'.repeat(9000) }))).status, 413);

  // Limite por IP antes do Turnstile -> #erro-limite sem chamar siteverify
  const sRl = setup({ rlIp: false });
  const rRl = await sRl.run(prioridadeReq({ email: 'a@b.com', 'cf-turnstile-response': 'tok' }));
  assert.equal(rRl.status, 303);
  assert.equal(loc(rRl), 'https://guifav.dev/prioridade/#erro-limite');
  assert.equal(sRl.calls.length, 0);

  // Turnstile ausente, reprovado ou outro host -> #erro-verificacao
  for (const [nome, o, fields] of [
    ['sem token', {}, { email: 'a@b.com' }],
    ['reprovado', { turnstile: { success: false } }, { email: 'a@b.com', 'cf-turnstile-response': 'tok' }],
    ['outro host', { turnstile: { success: true, hostname: 'evil.example' } }, { email: 'a@b.com', 'cf-turnstile-response': 'tok' }],
    ['erro de rede', { turnstile: new Error('rede') }, { email: 'a@b.com', 'cf-turnstile-response': 'tok' }],
  ] as const) {
    const st = setup(o);
    const rt = await st.run(prioridadeReq(fields));
    assert.equal(rt.status, 303, nome);
    assert.equal(loc(rt), 'https://guifav.dev/prioridade/#erro-verificacao', nome);
    assert.equal(st.rows('heating_optout').length, 0, nome);
  }

  // Email inválido -> #erro-email
  for (const badEmail of ['', 'sem-arroba', 'a@b', 'a@b.com\nbcc@x.com']) {
    const rBad = await s.run(prioridadeReq({ email: badEmail, 'cf-turnstile-response': 'tok' }));
    assert.equal(rBad.status, 303, badEmail);
    assert.equal(loc(rBad), 'https://guifav.dev/prioridade/#erro-email', badEmail);
  }
  assert.equal(s.rows('heating_optout').length, 0);
});

test('P4: falha do D1 em /prioridade redireciona para #erro, sem email no log, e grava a marca antes dos DELETEs', async () => {
  const s = setup({ d1Falha: /DELETE FROM heating/, d1Mensagem: 'D1_ERROR: falha ao apagar sigiloso@exemplo.com' });
  const iso = new Date(NOW).toISOString();
  s.db.prepare(`INSERT INTO engagement_events (id, email, signal, source, ref, occurred_at, recorded_at)
    VALUES ('rs:1', 'sigiloso@exemplo.com', 'email_aberto', 'resend', 'em_1', ?, ?)`).run(iso, iso);

  let r!: Response;
  const erros = await capturaErros(async () => {
    r = await s.run(prioridadeReq({ email: 'sigiloso@exemplo.com', 'cf-turnstile-response': 'tok' }));
  });
  assert.equal(r.status, 303);
  assert.equal(loc(r), 'https://guifav.dev/prioridade/#erro');
  assert.equal(erros.length, 1);
  assert.ok(!erros[0].includes('sigiloso'), erros[0]);
  assert.ok(!erros[0].includes('exemplo.com'), erros[0]);
  // Como a marca é gravada primeiro, mesmo com falha no DELETE o opt-out já protege contra novos eventos.
  assert.equal(s.rows('heating_optout').length, 1);
  assert.equal(s.rows('engagement_events').length, 0);
});

test('C1: wrangler.toml documenta RESEND_HEATING_WEBHOOK_SECRET em comentário e não atribui segredo', () => {
  assert.match(TOML, /#.*RESEND_HEATING_WEBHOOK_SECRET/);
  assert.doesNotMatch(TOML, /RESEND_HEATING_WEBHOOK_SECRET\s*=|whsec_[A-Za-z0-9+/=]{16,}/);
});

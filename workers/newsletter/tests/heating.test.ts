// Testes do heating de contatos (épico #197, corte 1): migrações 0010 e 0011, POST /resend/webhook e POST /prioridade.
// Rodar em workers/newsletter/: npm test
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash, createHmac } from 'node:crypto';
import { readFileSync, readdirSync } from 'node:fs';
import { DatabaseSync } from 'node:sqlite';
import { handle, type Env } from '../src/index.ts';
import type { D1Like, D1Stmt } from '../src/conversoes.ts';
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

const FROM_SITE = 'Guilherme Favaron <insights@guifav.dev>';
const FROM_EXPERT = 'Guilherme Favaron | Expert Insights <insights@expert.guifav.dev>';

function assinarSvix(id: string, tsSec: number, body: string, rawKey: Buffer = WEBHOOK_RAW): string {
  const mac = createHmac('sha256', rawKey).update(`${id}.${tsSec}.${body}`).digest('base64');
  return `v1,${mac}`;
}

/** Altera um bit de padding inutilizado em base64 canônico com `=`/`==`: decodifica para os mesmos bytes, mas não é canônico. */
function base64NaoCanonico(b64: string): string {
  const A = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
  const pos = b64.endsWith('==') ? b64.length - 3 : b64.endsWith('=') ? b64.length - 2 : -1;
  assert.ok(pos >= 0, `esperado base64 com padding: ${b64}`);
  const i = A.indexOf(b64[pos]);
  assert.equal(i & 1, 0, 'bit menos significativo deve ser 0 em base64 canônico com padding');
  return b64.slice(0, pos) + A[i | 1] + b64.slice(pos + 1);
}

type SetupOpts = {
  now?: number;
  webhookSecret?: string | undefined;
  heatingSiteSenders?: string | undefined;
  heatingCursos?: string | undefined;
  forgetKey?: string | undefined;
  turnstileSecret?: string | undefined;
  turnstile?: unknown;
  rlIp?: boolean;
  d1Falha?: RegExp;
  d1Mensagem?: string;
  db?: D1Like & { raw: DatabaseSync };
};

function setup(opts: SetupOpts = {}) {
  const calls: Array<{ url: string; method: string; body: unknown }> = [];
  const rl: string[] = [];
  const db = opts.db ?? d1(opts.d1Falha, opts.d1Mensagem ?? 'D1_ERROR: falha para leitor@exemplo.com');
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
    TURNSTILE_SECRET: 'turnstileSecret' in opts ? (opts.turnstileSecret as string) : 'ts_teste',
    CONFIRM_SECRET: SECRET,
    FORGET_HMAC_KEY: 'forgetKey' in opts ? (opts.forgetKey as string) : FORGET_KEY,
    RESEND_HEATING_WEBHOOK_SECRET: 'webhookSecret' in opts ? opts.webhookSecret : WEBHOOK_SECRET,
    HEATING_REMETENTES_SITE: 'heatingSiteSenders' in opts ? opts.heatingSiteSenders : tomlVar('HEATING_REMETENTES_SITE'),
    HEATING_CURSOS: 'heatingCursos' in opts ? opts.heatingCursos : tomlVar('HEATING_CURSOS'),
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

test('M1: migração 0010 preserva table_xinfo, índices, linhas e todos os CHECKs de conversions e passa a recusar verified fora de 0 e 1', () => {
  const db = new DatabaseSync(':memory:');
  const dir = new URL('../../admin/migrations/', import.meta.url);
  const sqlFiles = readdirSync(dir).filter((f) => f.endsWith('.sql')).sort();
  for (const f of sqlFiles.filter((f) => f < '0010_')) {
    db.exec(readFileSync(new URL(f, dir), 'utf-8'));
  }

  const lerXinfo = () =>
    JSON.parse(JSON.stringify(db.prepare('PRAGMA table_xinfo(conversions)').all())) as Array<{
      cid: number;
      name: string;
      type: string;
      notnull: number;
      dflt_value: string | null;
      pk: number;
      hidden: number;
    }>;
  const lerIndices = () => {
    const lista = JSON.parse(JSON.stringify(db.prepare('PRAGMA index_list(conversions)').all())) as Array<{
      name: string;
      unique: number;
      origin: string;
      partial: number;
    }>;
    return lista
      .map((idx) => ({
        name: idx.name,
        unique: idx.unique,
        origin: idx.origin,
        partial: idx.partial,
        cols: JSON.parse(JSON.stringify(db.prepare(`PRAGMA index_info("${idx.name}")`).all())),
      }))
      .sort((a, b) => a.name.localeCompare(b.name));
  };

  const xinfoAntes = lerXinfo();
  const indicesAntes = lerIndices();
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

  const xinfoDepois = lerXinfo();
  const indicesDepois = lerIndices();
  assert.deepEqual(xinfoDepois, xinfoAntes);
  assert.equal(xinfoDepois.find((c) => c.name === 'verified')?.dflt_value, '1');
  assert.deepEqual(indicesDepois, indicesAntes);
  assert.deepEqual(
    indicesDepois.filter((i) => i.origin === 'c').map((i) => [i.name, i.cols.map((c: { name: string }) => c.name)]),
    [['conversions_email', ['email']], ['conversions_pending', ['synced_at']]],
  );
  assert.deepEqual(rows(db, 'conversions'), linhasAntes);

  // Default de verified continua sendo 1 quando omitido no INSERT.
  db.prepare(`INSERT INTO conversions (id, email, action, interests, occurred_at)
    VALUES ('id-def', 'def@exemplo.com', 'newsletter_confirm', '[]', '2026-11-27T12:00:00.000Z')`).run();
  assert.equal(rows(db, 'conversions').find((r) => r.id === 'id-def')?.verified, 1);

  // Prova cada CHECK depois da 0010 com uma linha inválida por coluna com CHECK.
  const checksInvalidos: Array<[string, unknown]> = [
    ['origin_channel', 'canal_invalido'],
    ['origin_source', 'fonte_invalida'],
    ['origin_source', 'facebook'],
    ['utm_source', 'MAIUSCULA'],
    ['utm_medium', 'com espaco'],
    ['utm_campaign', 'camp?query=1'],
    ['landing_path', 'sem-barra-inicial'],
    ['landing_path', '/blog//post'],
    ['twenty_person_id', 'id/invalido'],
    ['verified', 2],
    ['verified', -1],
  ];
  for (let i = 0; i < checksInvalidos.length; i++) {
    const [col, ruim] = checksInvalidos[i];
    assert.throws(
      () => db.prepare(`INSERT INTO conversions (id, email, action, interests, occurred_at, ${col})
        VALUES (?, 'tres@exemplo.com', 'material_request', '[]', '2026-11-27T12:00:00.000Z', ?)`).run(`bad-${col}-${i}`, ruim as never),
      /CHECK constraint failed/,
      `${col}=${String(ruim)}`,
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

test('M3: migração 0011 aplica CHECKs de context, PK composta (context, email) e índices em engagement_events, heating e heating_optout', () => {
  const db = d1().raw;
  const iso = '2026-11-27T12:00:00.000Z';

  // engagement_events: contextos válidos (`site` e `curso:<slug>` com 1..64 chars de [a-z0-9-] começando por letra/número) passam.
  const contextosValidos = ['site', 'curso:a', 'curso:0', 'curso:chatgpt-no-dia-a-dia', `curso:${'a'.repeat(64)}`, 'curso:a--b-1'];
  for (let i = 0; i < contextosValidos.length; i++) {
    db.prepare(`INSERT INTO engagement_events (id, context, email, signal, source, ref, occurred_at, recorded_at)
      VALUES (?, ?, 'leitor@exemplo.com', 'email_aberto', 'resend', 'email_123', ?, ?)`).run(`rs:ok_${i}`, contextosValidos[i], iso, iso);
  }
  const contextosInvalidos = [
    '',
    'SITE',
    'curso',
    'curso:',
    'curso:-abc',
    'curso:Abc',
    'curso:abcDef',
    'curso:abc_def',
    'curso:abc:def',
    'curso:abc.def',
    'curso:abc def',
    `curso:${'a'.repeat(65)}`,
    'outro:abc',
  ];
  for (let i = 0; i < contextosInvalidos.length; i++) {
    const ruim = contextosInvalidos[i];
    assert.throws(
      () => db.prepare(`INSERT INTO engagement_events (id, context, email, signal, source, ref, occurred_at, recorded_at)
        VALUES (?, ?, 'leitor@exemplo.com', 'email_aberto', 'resend', null, ?, ?)`).run(`rs:badctx_${i}`, ruim, iso, iso),
      /CHECK/,
      `engagement_events context=${ruim}`,
    );
    assert.throws(
      () => db.prepare(`INSERT INTO heating (context, email, score, band, signals, computed_at)
        VALUES (?, 'ctx@exemplo.com', 50, 'morno', '[]', ?)`).run(ruim, iso),
      /CHECK/,
      `heating context=${ruim}`,
    );
  }

  // Outros CHECKs de engagement_events: sinal fora da lista, fonte, email maiúsculo, id/ref fora do GLOB e data fora do formato são recusados.
  assert.throws(() => db.prepare(`INSERT INTO engagement_events (id, context, email, signal, source, ref, occurred_at, recorded_at)
    VALUES ('id-2', 'site', 'Leitor@exemplo.com', 'email_aberto', 'resend', null, ?, ?)`).run(iso, iso), /CHECK/);
  assert.throws(() => db.prepare(`INSERT INTO engagement_events (id, context, email, signal, source, ref, occurred_at, recorded_at)
    VALUES ('id-3', 'site', 'leitor@exemplo.com', 'sinal_falso', 'resend', null, ?, ?)`).run(iso, iso), /CHECK/);
  assert.throws(() => db.prepare(`INSERT INTO engagement_events (id, context, email, signal, source, ref, occurred_at, recorded_at)
    VALUES ('id-4', 'site', 'leitor@exemplo.com', 'email_aberto', 'outra', null, ?, ?)`).run(iso, iso), /CHECK/);
  assert.throws(() => db.prepare(`INSERT INTO engagement_events (id, context, email, signal, source, ref, occurred_at, recorded_at)
    VALUES ('id/ruim', 'site', 'leitor@exemplo.com', 'email_aberto', 'resend', null, ?, ?)`).run(iso, iso), /CHECK/);
  assert.throws(() => db.prepare(`INSERT INTO engagement_events (id, context, email, signal, source, ref, occurred_at, recorded_at)
    VALUES ('id-ref-ruim', 'site', 'leitor@exemplo.com', 'email_aberto', 'resend', 'ref/invalida', ?, ?)`).run(iso, iso), /CHECK/);
  assert.throws(() => db.prepare(`INSERT INTO engagement_events (id, context, email, signal, source, ref, occurred_at, recorded_at)
    VALUES ('id-5', 'site', 'leitor@exemplo.com', 'email_aberto', 'resend', null, '2026-11-27T12:00:00Z', ?)`).run(iso), /CHECK/);

  // heating: dois contextos para o mesmo email coexistem (PK composta), mas repetir o mesmo (context, email) falha.
  db.prepare(`INSERT INTO heating (context, email, score, band, signals, computed_at, twenty_person_id)
    VALUES ('site', 'leitor@exemplo.com', 75, 'quente', '["site_agende","email_clicado"]', ?, 'uuid-123')`).run(iso);
  db.prepare(`INSERT INTO heating (context, email, score, band, signals, computed_at, twenty_person_id)
    VALUES ('curso:chatgpt-no-dia-a-dia', 'leitor@exemplo.com', 40, 'morno', '["curso_aula"]', ?, 'uuid-123')`).run(iso);
  assert.equal(rows(db, 'heating').length, 2);
  assert.throws(() => db.prepare(`INSERT INTO heating (context, email, score, band, signals, computed_at)
    VALUES ('site', 'leitor@exemplo.com', 10, 'frio', '[]', ?)`).run(iso), /UNIQUE|PRIMARY KEY/);

  // Outros CHECKs de heating: email maiúsculo, score 0..100, band, json_valid(signals) <= 2000, twenty_person_id.
  assert.throws(() => db.prepare(`INSERT INTO heating (context, email, score, band, signals, computed_at)
    VALUES ('site', 'Leitor@exemplo.com', 50, 'morno', '[]', ?)`).run(iso), /CHECK/);
  assert.throws(() => db.prepare(`INSERT INTO heating (context, email, score, band, signals, computed_at)
    VALUES ('site', 'a@b.co', 101, 'quente', '[]', ?)`).run(iso), /CHECK/);
  assert.throws(() => db.prepare(`INSERT INTO heating (context, email, score, band, signals, computed_at)
    VALUES ('site', 'a@b.co', 50, 'fervendo', '[]', ?)`).run(iso), /CHECK/);
  assert.throws(() => db.prepare(`INSERT INTO heating (context, email, score, band, signals, computed_at)
    VALUES ('site', 'a@b.co', 50, 'morno', 'nao-json', ?)`).run(iso), /CHECK/);
  assert.throws(() => db.prepare(`INSERT INTO heating (context, email, score, band, signals, computed_at, twenty_person_id)
    VALUES ('site', 'a@b.co', 50, 'morno', '[]', ?, 'id/invalido')`).run(iso), /CHECK/);

  // heating_optout: 64 hex minúsculos e ISO UTC com ms (sem contexto: vale para todos).
  db.prepare('INSERT INTO heating_optout (email_hmac, opted_out_at) VALUES (?, ?)').run('a'.repeat(64), iso);
  assert.throws(() => db.prepare('INSERT INTO heating_optout (email_hmac, opted_out_at) VALUES (?, ?)').run('A'.repeat(64), iso), /CHECK/);
  assert.throws(() => db.prepare('INSERT INTO heating_optout (email_hmac, opted_out_at) VALUES (?, ?)').run('b'.repeat(64), '2026-11-27'), /CHECK/);

  // Índices esperados para leitura do job por contexto e DELETE por email.
  const idxEvents = (db.prepare("SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'engagement_events' AND name NOT LIKE 'sqlite_%' ORDER BY name")
    .all() as Array<{ name: string }>).map((i) => i.name);
  assert.deepEqual(idxEvents, ['engagement_events_context_email_occurred', 'engagement_events_email', 'engagement_events_signal_ref']);
  const idxHeating = (db.prepare("SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'heating' AND name NOT LIKE 'sqlite_%' ORDER BY name")
    .all() as Array<{ name: string }>).map((i) => i.name);
  assert.deepEqual(idxHeating, ['heating_context_score_email', 'heating_email']);
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
    ['email.delivered', 'email_entregue', { email_id: 'em_del_1', from: FROM_SITE, to: ['Leitor@Exemplo.com'] }],
    ['email.opened', 'email_aberto', { email_id: 'em_open_1', from: FROM_EXPERT, to: 'leitor@exemplo.com' }],
    ['email.clicked', 'email_clicado', { email_id: 'em_click_1', from: FROM_SITE, to: ['leitor@exemplo.com'], click: { link: 'https://guifav.dev/blog/artigo/?utm_source=email' } }],
    ['email.complained', 'email_spam', { email_id: 'em_spam_1', from: FROM_EXPERT, to: ['leitor@exemplo.com'] }],
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
      context: 'site',
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

test('W2: múltiplas assinaturas em svix-signature (rotação): aceita até 16 quando qualquer v1 bate e recusa acima de 16', async () => {
  const s = setup();
  const body = JSON.stringify({ type: 'email.opened', created_at: '2026-11-27T12:00:00.000Z', data: { email_id: 'em_rot', from: FROM_SITE, to: ['a@b.com'] } });
  const ts = Math.floor(NOW / 1000);
  const valida = assinarSvix('msg_rot_1', ts, body);
  const outra = assinarSvix('msg_rot_1', ts, body, Buffer.from('ffffffffffffffffffffffff', 'utf-8'));
  const r = await s.run(webhookReq(null, { id: 'msg_rot_1', tsSec: ts, rawBody: body, sig: `v2,AAAA ${outra} ${valida}` }));
  assert.equal(r.status, 200);
  assert.equal(s.rows('engagement_events').length, 1);

  // Exatamente 16 assinaturas (a última válida): aceita.
  const valida16 = assinarSvix('msg_rot_16', ts, body);
  const sig16 = [...Array(15).fill(outra), valida16].join(' ');
  const r16 = await s.run(webhookReq(null, { id: 'msg_rot_16', tsSec: ts, rawBody: body, sig: sig16 }));
  assert.equal(r16.status, 200);
  assert.equal(s.rows('engagement_events').length, 2);

  // Mais de 16 assinaturas (17, mesmo contendo a assinatura válida): recusa com 401.
  const valida17 = assinarSvix('msg_rot_17', ts, body);
  const sig17 = [valida17, ...Array(16).fill(outra)].join(' ');
  const r17 = await s.run(webhookReq(null, { id: 'msg_rot_17', tsSec: ts, rawBody: body, sig: sig17 }));
  assert.equal(r17.status, 401);
  assert.equal(s.rows('engagement_events').length, 2);
});

test('W3: assinatura errada, base64 não canônico na assinatura, timestamp fora da janela e svix-id inválido devolvem 401 sem gravar', async () => {
  const s = setup();
  const payload = { type: 'email.opened', created_at: '2026-11-27T12:00:00.000Z', data: { email_id: 'em_1', from: FROM_SITE, to: ['a@b.com'] } };
  const tsNow = Math.floor(NOW / 1000);
  const raw = JSON.stringify(payload);

  // Assinatura errada
  assert.equal((await s.run(webhookReq(payload, { sig: assinarSvix('msg_1', tsNow, raw, Buffer.from('chave-errada-0123456789a', 'utf-8')) }))).status, 401);
  // Base64 não canônico na assinatura (decodifica para os mesmos 32 bytes do HMAC válido, mas falha a checagem canônica)
  const macCanonico = createHmac('sha256', WEBHOOK_RAW).update(`msg_b64.${tsNow}.${raw}`).digest('base64');
  const macNaoCanonico = base64NaoCanonico(macCanonico);
  assert.ok(Buffer.from(macNaoCanonico, 'base64').equals(Buffer.from(macCanonico, 'base64')));
  assert.equal((await s.run(webhookReq(null, { id: 'msg_b64', tsSec: tsNow, rawBody: raw, sig: `v1,${macNaoCanonico}` }))).status, 401);
  // Prefixo v2 com base64 válido do HMAC certo é recusado (o prefixo v1, é obrigatório)
  assert.equal((await s.run(webhookReq(null, { id: 'msg_b64', tsSec: tsNow, rawBody: raw, sig: `v2,${macCanonico}` }))).status, 401);
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

test('W4: segredo ausente, fora do formato ou base64 não canônico devolve 503 sem ler o corpo', async () => {
  // Chave de 20 bytes (20 % 3 !== 0 -> padding `=`) para testar base64 canônico vs não canônico.
  const chave20 = Buffer.from('0123456789abcdefghij', 'utf-8');
  const chave20B64 = chave20.toString('base64');
  const chave20NaoCanonica = base64NaoCanonico(chave20B64);
  assert.ok(Buffer.from(chave20NaoCanonica, 'base64').equals(chave20));

  for (const badSecret of [undefined, '', 'sem-prefixo-01234567890123456789', 'whsec_', 'whsec_!!!', 'whsec_AAAA', `whsec_${chave20NaoCanonica}`]) {
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

  // A mesma chave de 20 bytes em base64 canônico funciona normalmente.
  const sOk = setup({ webhookSecret: `whsec_${chave20B64}` });
  const bodyOk = JSON.stringify({ type: 'email.delivered', created_at: '2026-11-27T12:00:00.000Z', data: { email_id: 'em_20', from: FROM_SITE, to: ['a@b.com'] } });
  const ts = Math.floor(NOW / 1000);
  const rOk = await sOk.run(webhookReq(null, { id: 'msg_20_ok', tsSec: ts, rawBody: bodyOk, sig: assinarSvix('msg_20_ok', ts, bodyOk, chave20) }));
  assert.equal(rOk.status, 200);
  assert.equal(sOk.rows('engagement_events').length, 1);
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
  const payload = { type: 'email.delivered', created_at: '2026-11-27T12:00:00.000Z', data: { email_id: 'em_dup', from: FROM_SITE, to: ['leitor@exemplo.com'] } };
  const r1 = await s.run(webhookReq(payload, { id: 'msg_mesmo' }));
  assert.equal(r1.status, 200);
  assert.deepEqual(await r1.json(), { ok: true, gravados: 1 });
  s.avanca(10_000);
  const r2 = await s.run(webhookReq(payload, { id: 'msg_mesmo', tsSec: Math.floor((NOW + 10_000) / 1000) }));
  assert.equal(r2.status, 200);
  assert.deepEqual(await r2.json(), { ok: true, gravados: 0 });
  const todas = s.rows('engagement_events');
  assert.equal(todas.length, 1);
  assert.equal(todas[0].recorded_at, new Date(NOW).toISOString());
});

test('W7: vários destinatários: entrega e spam gravam por destinatário (pulando inválidos sem logar email), abertura e clique são ignorados', async () => {
  const s = setup();
  const payload = {
    type: 'email.delivered',
    created_at: '2026-11-27T12:00:00.000Z',
    data: { email_id: 'em_multi', from: FROM_SITE, to: ['Um@Exemplo.com', 'dois@exemplo.com', 'um@exemplo.com', 'invalido-sem-arroba', 'ruim@segredo.local\nbcc'] },
  };
  let r!: Response;
  const erros = await capturaErros(async () => {
    r = await s.run(webhookReq(payload, { id: 'msg_multi' }));
  });
  assert.equal(r.status, 200);
  assert.deepEqual(await r.json(), { ok: true, gravados: 2 });
  assert.deepEqual(s.rows('engagement_events').map((x) => x.email), ['um@exemplo.com', 'dois@exemplo.com']);
  assert.equal(erros.length, 1);
  assert.match(erros[0], /2 destinatário/);
  assert.ok(!erros[0].includes('segredo.local'), erros[0]);
  assert.ok(!erros[0].includes('invalido-sem-arroba'), erros[0]);

  // Spam (email.complained) também continua gravando por destinatário.
  const rSpam = await s.run(webhookReq({
    type: 'email.complained',
    created_at: '2026-11-27T12:00:00.000Z',
    data: { email_id: 'em_multi_spam', from: FROM_SITE, to: ['um@exemplo.com', 'dois@exemplo.com'] },
  }, { id: 'msg_multi_spam' }));
  assert.equal(rSpam.status, 200);
  assert.deepEqual(await rSpam.json(), { ok: true, gravados: 2 });
  assert.equal(s.rows('engagement_events').length, 4);

  // Abertura e clique só gravam quando a lista `to` inteira tem exatamente um item (contando inválidos e repetidos).
  for (const [idPrefix, to] of [
    ['multi', ['um@exemplo.com', 'dois@exemplo.com']],
    ['com_invalido', ['um@exemplo.com', 'Dois <dois@exemplo.com>']],
    ['repetido', ['Um@Exemplo.com', 'um@exemplo.com']],
  ] as const) {
    const rOpen = await s.run(webhookReq({
      type: 'email.opened',
      created_at: '2026-11-27T12:00:00.000Z',
      data: { email_id: `em_open_${idPrefix}`, from: FROM_SITE, to },
    }, { id: `msg_open_${idPrefix}` }));
    assert.equal(rOpen.status, 200, idPrefix);
    assert.deepEqual(await rOpen.json(), { ok: true, ignorado: 'varios_destinatarios' }, idPrefix);

    const rClick = await s.run(webhookReq({
      type: 'email.clicked',
      created_at: '2026-11-27T12:00:00.000Z',
      data: { email_id: `em_click_${idPrefix}`, from: FROM_SITE, to, click: { link: 'https://guifav.dev/blog/post/' } },
    }, { id: `msg_click_${idPrefix}` }));
    assert.equal(rClick.status, 200, idPrefix);
    assert.deepEqual(await rClick.json(), { ok: true, ignorado: 'varios_destinatarios' }, idPrefix);
  }
  assert.equal(s.rows('engagement_events').length, 4);

  // Com exatamente 1 item válido em `to`, abertura volta a gravar normalmente.
  const rOpenUnico = await s.run(webhookReq({
    type: 'email.opened',
    created_at: '2026-11-27T12:00:00.000Z',
    data: { email_id: 'em_open_unico', from: FROM_SITE, to: ['Um@Exemplo.com'] },
  }, { id: 'msg_open_unico' }));
  assert.equal(rOpenUnico.status, 200);
  assert.deepEqual(await rOpenUnico.json(), { ok: true, gravados: 1 });
  assert.equal(s.rows('engagement_events').length, 5);
});

test('W8: cliques de descadastro, /prioridade (inclusive percent-encoded e host com ponto final) e URL inválida são ignorados com 200 sem gravar', async () => {
  const s = setup();
  const linksIgnorados: Array<[string, string]> = [
    ['https://resend.com/unsubscribe/abc', 'descadastro'],
    ['https://resend.com./u/123', 'descadastro'],
    ['https://links.resend.com/u/123', 'descadastro'],
    ['https://links.resend.com./u/123', 'descadastro'],
    ['https://track.resend.dev/x', 'descadastro'],
    ['https://resend.dev./x', 'descadastro'],
    ['https://guifav.dev/blog/UNSUBSCRIBE/1', 'descadastro'],
    ['https://guifav.dev/blog/%75nsubscribe/1', 'descadastro'],
    ['https://guifav.dev/descadastrar/', 'descadastro'],
    ['https://guifav.dev/prioridade', 'oposicao'],
    ['https://guifav.dev/prioridade/', 'oposicao'],
    ['https://guifav.dev/PRIORIDADE/?utm=1', 'oposicao'],
    ['https://guifav.dev/%70rioridade', 'oposicao'],
    ['https://guifav.dev/%70rioridade/', 'oposicao'],
    ['https://guifav.dev./prioridade', 'oposicao'],
    ['https://insights.guifav.dev/prioridade', 'oposicao'],
    ['https://insights.guifav.dev/%70rioridade', 'oposicao'],
    ['mailto:contact@guifav.dev', 'link_invalido'],
    ['javascript:alert(1)', 'link_invalido'],
    ['nao-e-url', 'link_invalido'],
    ['', 'link_invalido'],
  ];
  for (let i = 0; i < linksIgnorados.length; i++) {
    const [link, motivoEsperado] = linksIgnorados[i];
    const r = await s.run(webhookReq({
      type: 'email.clicked',
      created_at: '2026-11-27T12:00:00.000Z',
      data: { email_id: 'em_click', from: FROM_SITE, to: ['leitor@exemplo.com'], click: { link } },
    }, { id: `msg_ign_${i}` }));
    assert.equal(r.status, 200, link);
    assert.deepEqual(await r.json(), { ok: true, ignorado: motivoEsperado }, link);
  }
  assert.equal(s.rows('engagement_events').length, 0);

  // Links parecidos mas legítimos continuam pontuando.
  for (const [i, link] of ['https://notresend.com/artigo', 'https://guifav.dev/blog/prioridades/'].entries()) {
    const r = await s.run(webhookReq({
      type: 'email.clicked',
      created_at: '2026-11-27T12:00:00.000Z',
      data: { email_id: 'em_click_ok', from: FROM_SITE, to: ['leitor@exemplo.com'], click: { link } },
    }, { id: `msg_ok_${i}` }));
    assert.equal(r.status, 200, link);
  }
  assert.equal(s.rows('engagement_events').length, 2);
});

test('W9: tipo que não interessa (inclusive propriedades do Object.prototype) responde 200 com ignorado sem gravar', async () => {
  const s = setup();
  for (const [i, type] of ['email.sent', 'toString', 'constructor', '__proto__'].entries()) {
    const r = await s.run(webhookReq({
      type,
      created_at: '2026-11-27T12:00:00.000Z',
      data: { email_id: `em_ign_${i}`, from: FROM_SITE, to: ['leitor@exemplo.com'] },
    }, { id: `msg_ign_type_${i}` }));
    assert.equal(r.status, 200, type);
    assert.deepEqual(await r.json(), { ok: true, ignorado: type }, type);
  }
  assert.equal(s.rows('engagement_events').length, 0);
});

test('W10: corpo maior que 64 KiB devolve 413 (com Content-Length sem ler o corpo e em streaming sem Content-Length)', async () => {
  const s = setup();
  const grande = 'x'.repeat(MAX_WEBHOOK_BODY + 1);
  assert.equal((await s.run(webhookReq(null, { rawBody: grande }))).status, 413);

  // Content-Length acima de 64 KiB é recusado sem ler o corpo.
  let leuCorpo = false;
  const streamNaoLido = new ReadableStream({
    pull(controller) {
      leuCorpo = true;
      controller.enqueue(new TextEncoder().encode('{}'));
      controller.close();
    },
  }, { highWaterMark: 0 });
  const reqClGrande = new Request('https://insights.guifav.dev/resend/webhook', {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      'content-length': String(MAX_WEBHOOK_BODY + 1),
    },
    body: streamNaoLido,
    duplex: 'half',
  } as RequestInit);
  assert.equal((await s.run(reqClGrande)).status, 413);
  assert.equal(leuCorpo, false, 'não deve ler o corpo quando Content-Length já excede 64 KiB');
  assert.equal(reqClGrande.bodyUsed, false, 'bodyUsed deve continuar false quando Content-Length já excede 64 KiB');

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

test('W11: corpo com UTF-8 inválido, JSON inválido e campos fora do formato (inclusive to vazio ou só espaços) devolvem 400 sem gravar', async () => {
  const s = setup();
  // UTF-8 inválido no corpo -> 400
  const reqUtf8Ruim = new Request('https://insights.guifav.dev/resend/webhook', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: new Uint8Array([0xff, 0xfe, 0x80]),
  });
  assert.equal((await s.run(reqUtf8Ruim)).status, 400, 'utf8 inválido');

  const invalidos: Array<[string, string]> = [
    ['json quebrado', '{nao-json'],
    ['array na raiz', '[]'],
    ['sem type', JSON.stringify({ created_at: '2026-11-27T12:00:00.000Z', data: {} })],
    ['data inválida', JSON.stringify({ type: 'email.opened', created_at: 'ontem', data: { email_id: 'e1', from: FROM_SITE, to: ['a@b.com'] } })],
    ['data inexistente', JSON.stringify({ type: 'email.opened', created_at: '2026-02-30T12:00:00Z', data: { email_id: 'e1', from: FROM_SITE, to: ['a@b.com'] } })],
    ['email_id inválido', JSON.stringify({ type: 'email.opened', created_at: '2026-11-27T12:00:00Z', data: { email_id: 'id com espaco', from: FROM_SITE, to: ['a@b.com'] } })],
    ['to array vazio', JSON.stringify({ type: 'email.opened', created_at: '2026-11-27T12:00:00Z', data: { email_id: 'e1', from: FROM_SITE, to: [] } })],
    ['to string vazia', JSON.stringify({ type: 'email.opened', created_at: '2026-11-27T12:00:00Z', data: { email_id: 'e1', from: FROM_SITE, to: '' } })],
    ['to só espaços', JSON.stringify({ type: 'email.opened', created_at: '2026-11-27T12:00:00Z', data: { email_id: 'e1', from: FROM_SITE, to: '   \t ' } })],
    ['to não string', JSON.stringify({ type: 'email.opened', created_at: '2026-11-27T12:00:00Z', data: { email_id: 'e1', from: FROM_SITE, to: [123] } })],
    ['clique sem click.link', JSON.stringify({ type: 'email.clicked', created_at: '2026-11-27T12:00:00Z', data: { email_id: 'e1', from: FROM_SITE, to: ['a@b.com'] } })],
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
    type: 'email.delivered',
    created_at: '2026-11-27T12:00:00.000Z',
    data: { email_id: 'em_opt', from: FROM_SITE, to: ['oposto@exemplo.com', 'ativo@exemplo.com'] },
  }, { id: 'msg_opt_1' }));
  assert.equal(r.status, 200);
  assert.deepEqual(await r.json(), { ok: true, gravados: 1 });
  assert.deepEqual(s.rows('engagement_events').map((x) => x.email), ['ativo@exemplo.com']);

  // Abertura de destinatário único com oposição também não grava.
  const rOpen = await s.run(webhookReq({
    type: 'email.opened',
    created_at: '2026-11-27T12:00:00.000Z',
    data: { email_id: 'em_opt_open', from: FROM_SITE, to: ['oposto@exemplo.com'] },
  }, { id: 'msg_opt_2' }));
  assert.equal(rOpen.status, 200);
  assert.deepEqual(await rOpen.json(), { ok: true, gravados: 0 });
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
        data: { email_id: 'em_err', from: FROM_SITE, to: ['sigiloso@exemplo.com'] },
      }));
    });
    assert.equal(r.status, 500, String(regex));
    assert.equal(erros.length, 1);
    assert.ok(!erros[0].includes('sigiloso'), erros[0]);
    assert.ok(!erros[0].includes('exemplo.com'), erros[0]);
    assert.ok(erros[0].includes('<email>'), erros[0]);
  }
});

test('W14: contexto de curso e de site saem apenas das listas HEATING_CURSOS e HEATING_REMETENTES_SITE (em minúsculas)', async () => {
  const s = setup({ heatingCursos: 'chatgpt-no-dia-a-dia, ia-para-lideres' });
  const casos: Array<[string, string]> = [
    ['Guilherme Favaron | ChatGPT no dia a dia <chatgpt-no-dia-a-dia@guifav.dev>', 'curso:chatgpt-no-dia-a-dia'],
    ['chatgpt-no-dia-a-dia@guifav.dev', 'curso:chatgpt-no-dia-a-dia'],
    ['Curso <ChatGPT-No-Dia-A-Dia@GUIFAV.DEV>', 'curso:chatgpt-no-dia-a-dia'],
    ['Guilherme <ia-para-lideres@guifav.dev>', 'curso:ia-para-lideres'],
    ['insights@guifav.dev', 'site'],
    ['Guilherme Favaron <INSIGHTS@GUIFAV.DEV>', 'site'],
    ['insights@expert.guifav.dev', 'site'],
    ['Guilherme Favaron | Expert Insights <Insights@Expert.GuiFav.Dev>', 'site'],
  ];
  for (let i = 0; i < casos.length; i++) {
    const [from, esperado] = casos[i];
    const r = await s.run(webhookReq({
      type: 'email.opened',
      created_at: '2026-11-27T12:00:00.000Z',
      data: { email_id: `em_c_${i}`, from, to: ['aluna@exemplo.com'] },
    }, { id: `msg_curso_${i}` }));
    assert.equal(r.status, 200, from);
    assert.equal(s.rows('engagement_events')[i].context, esperado, from);
  }
});

test('W15: remetente desconhecido, ausente ou malformado (extractFromAddress estrito) é ignorado com 200 e loga só a contagem', async () => {
  const s = setup();
  const casos: Array<[string, unknown]> = [
    ['outro endereço em guifav.dev fora das listas', 'Guilherme Favaron <outro-sigiloso@guifav.dev>'],
    ['outro endereço em expert.guifav.dev fora da lista', 'Guilherme Favaron | Expert Insights <newsletter@expert.guifav.dev>'],
    ['mesmo slug em subdomínio', 'chatgpt-no-dia-a-dia@expert.guifav.dev'],
    ['mesmo slug em outro domínio', 'Curso <chatgpt-no-dia-a-dia@outro.com>'],
    ['from ausente', undefined],
    ['from vazio', '   '],
    ['from não string', 42],
    ['from sem arroba', 'chatgpt-no-dia-a-dia'],
    ['from sem nome antes de <>', '<insights@guifav.dev>'],
    ['from com lixo depois de >', 'Guilherme <insights@guifav.dev> extra'],
    ['from com segundo endereço depois de >', 'Guilherme <insights@guifav.dev>, outro@exemplo.com'],
    ['from com @ antes de <', 'outro@exemplo.com <insights@guifav.dev>'],
    ['from com vírgula antes de <', 'A, B <insights@guifav.dev>'],
    ['from com ponto e vírgula antes de <', 'A; B <insights@guifav.dev>'],
    ['from com quebra de linha antes de <', 'A\nB <insights@guifav.dev>'],
    ['from com < sem >', 'Curso <chatgpt-no-dia-a-dia@guifav.dev'],
    ['from com > sem <', 'Curso chatgpt-no-dia-a-dia@guifav.dev>'],
    ['from com dois pares <>', '<outro@exemplo.com> <insights@guifav.dev>'],
    ['from com espaços dentro de <>', 'Guilherme < insights@guifav.dev >'],
    ['from com endereço inválido dentro de <>', 'Guilherme <nao-e-email>'],
  ];
  for (let i = 0; i < casos.length; i++) {
    const [nome, from] = casos[i];
    const data: Record<string, unknown> = { email_id: `em_desc_${i}`, to: ['leitor@exemplo.com'] };
    if (from !== undefined) data.from = from;
    let r!: Response;
    const erros = await capturaErros(async () => {
      r = await s.run(webhookReq({
        type: 'email.delivered',
        created_at: '2026-11-27T12:00:00.000Z',
        data,
      }, { id: `msg_desc_${i}` }));
    });
    assert.equal(r.status, 200, nome);
    assert.deepEqual(await r.json(), { ok: true, ignorado: 'remetente_desconhecido' }, nome);
    assert.equal(erros.length, 1, nome);
    assert.match(erros[0], /\b1\b.*remetente desconhecido/i, nome);
    assert.ok(!erros[0].includes('outro-sigiloso'), erros[0]);
    assert.ok(!erros[0].includes('insights@'), erros[0]);
  }
  assert.equal(s.rows('engagement_events').length, 0);

  // Sem HEATING_CURSOS configurada, remetente do curso vira remetente desconhecido (não cai em site).
  const sSemCurso = setup({ heatingCursos: undefined });
  let rSemCurso!: Response;
  await capturaErros(async () => {
    rSemCurso = await sSemCurso.run(webhookReq({
      type: 'email.delivered',
      created_at: '2026-11-27T12:00:00.000Z',
      data: { email_id: 'em_sem_curso', from: 'chatgpt-no-dia-a-dia@guifav.dev', to: ['leitor@exemplo.com'] },
    }, { id: 'msg_sem_curso' }));
  });
  assert.equal(rSemCurso.status, 200);
  assert.deepEqual(await rSemCurso.json(), { ok: true, ignorado: 'remetente_desconhecido' });
  assert.equal(sSemCurso.rows('engagement_events').length, 0);

  // Sem HEATING_REMETENTES_SITE configurada, remetente do site também vira remetente desconhecido.
  const sSemSite = setup({ heatingSiteSenders: undefined });
  let rSemSite!: Response;
  await capturaErros(async () => {
    rSemSite = await sSemSite.run(webhookReq({
      type: 'email.delivered',
      created_at: '2026-11-27T12:00:00.000Z',
      data: { email_id: 'em_sem_site', from: FROM_SITE, to: ['leitor@exemplo.com'] },
    }, { id: 'msg_sem_site' }));
  });
  assert.equal(rSemSite.status, 200);
  assert.deepEqual(await rSemSite.json(), { ok: true, ignorado: 'remetente_desconhecido' });
  assert.equal(sSemSite.rows('engagement_events').length, 0);
});

test('W16: entradas inválidas em HEATING_CURSOS e HEATING_REMETENTES_SITE são ignoradas com log curto enquanto as válidas continuam valendo', async () => {
  const s = setup({
    heatingCursos: `chatgpt-no-dia-a-dia, -invalido, Maiuscula, com_underline, ${'a'.repeat(65)}`,
    heatingSiteSenders: 'INSIGHTS@GUIFAV.DEV, nao-e-email, Nome <insights@expert.guifav.dev>, sigiloso@semtld',
  });
  let rCurso!: Response;
  let rSite!: Response;
  let rIgnorado!: Response;
  const erros = await capturaErros(async () => {
    rCurso = await s.run(webhookReq({
      type: 'email.opened',
      created_at: '2026-11-27T12:00:00.000Z',
      data: { email_id: 'em_v', from: 'chatgpt-no-dia-a-dia@guifav.dev', to: ['leitor@exemplo.com'] },
    }, { id: 'msg_slug_ok' }));
    rSite = await s.run(webhookReq({
      type: 'email.opened',
      created_at: '2026-11-27T12:00:00.000Z',
      data: { email_id: 'em_s', from: 'insights@guifav.dev', to: ['leitor@exemplo.com'] },
    }, { id: 'msg_site_ok' }));
    rIgnorado = await s.run(webhookReq({
      type: 'email.opened',
      created_at: '2026-11-27T12:00:00.000Z',
      data: { email_id: 'em_i', from: 'maiuscula@guifav.dev', to: ['leitor@exemplo.com'] },
    }, { id: 'msg_slug_bad' }));
  });
  assert.equal(rCurso.status, 200);
  assert.equal(rSite.status, 200);
  assert.equal(rIgnorado.status, 200);
  assert.deepEqual(await rIgnorado.json(), { ok: true, ignorado: 'remetente_desconhecido' });
  const todas = s.rows('engagement_events');
  assert.deepEqual(todas.map((x) => x.context), ['curso:chatgpt-no-dia-a-dia', 'site']);
  assert.ok(erros.some((e) => /4 slug\(s\) inválido\(s\) em HEATING_CURSOS ignorado\(s\)/.test(e)), erros.join('\n'));
  assert.ok(erros.some((e) => /3 remetente\(s\) inválido\(s\) em HEATING_REMETENTES_SITE ignorado\(s\)/.test(e)), erros.join('\n'));
  for (const e of erros) {
    assert.ok(!e.includes('sigiloso@semtld'), e);
    assert.ok(!e.includes('nao-e-email'), e);
  }
});

test('W17: corrida com POST /prioridade — se a oposição grava a marca imediatamente antes do INSERT do webhook, nenhum evento do email fica gravado', async () => {
  const base = d1();
  const email = 'corrida@exemplo.com';
  const hmac = await heatingOptoutHmac(email, FORGET_KEY);
  const isoAnterior = new Date(NOW - 60_000).toISOString();
  base.raw.prepare(`INSERT INTO engagement_events (id, context, email, signal, source, ref, occurred_at, recorded_at)
    VALUES ('rs:previo', 'site', ?, 'email_entregue', 'resend', 'em_previo', ?, ?)`).run(email, isoAnterior, isoAnterior);

  let prioridadeExecutou = false;
  const d1Corrida: D1Like & { raw: DatabaseSync } = {
    raw: base.raw,
    prepare(sql: string) {
      const wrap = (inner: D1Stmt): D1Stmt => ({
        bind: (...v: unknown[]) => wrap(inner.bind(...v)),
        first: <T>() => inner.first<T>(),
        run: async () => {
          if (!prioridadeExecutou && /INSERT\s+INTO\s+engagement_events\b/i.test(sql)) {
            prioridadeExecutou = true;
            // Simula POST /prioridade terminando imediatamente antes do INSERT do webhook:
            // grava a marca de oposição e apaga eventos e heating anteriores do email.
            base.raw.prepare(
              'INSERT INTO heating_optout (email_hmac, opted_out_at) VALUES (?, ?) ON CONFLICT(email_hmac) DO UPDATE SET opted_out_at = excluded.opted_out_at',
            ).run(hmac, new Date(NOW).toISOString());
            base.raw.prepare('DELETE FROM engagement_events WHERE email = ?').run(email);
            base.raw.prepare('DELETE FROM heating WHERE email = ?').run(email);
          }
          return inner.run();
        },
      });
      return wrap(base.prepare(sql));
    },
  };

  const s = setup({ db: d1Corrida });
  const r = await s.run(webhookReq({
    type: 'email.opened',
    created_at: new Date(NOW).toISOString(),
    data: { email_id: 'em_corrida', from: FROM_SITE, to: [email] },
  }, { id: 'msg_corrida_1' }));

  assert.equal(r.status, 200);
  assert.equal(prioridadeExecutou, true);
  assert.equal(s.rows('heating_optout').length, 1);
  assert.equal(s.rows('engagement_events').length, 0);
  assert.deepEqual(await r.json(), { ok: true, gravados: 0 });
});

test('W18: FORGET_HMAC_KEY ausente ou curta no webhook devolve 500 Worker sem configuração', async () => {
  for (const forgetKey of [undefined, '', 'x'.repeat(31)]) {
    const s = setup({ forgetKey });
    const r = await s.run(webhookReq({
      type: 'email.opened',
      created_at: '2026-11-27T12:00:00.000Z',
      data: { email_id: 'em_cfg', from: FROM_SITE, to: ['leitor@exemplo.com'] },
    }));
    assert.equal(r.status, 500, String(forgetKey));
    assert.equal(await r.text(), 'Worker sem configuração');
    assert.equal(s.rows('engagement_events').length, 0);
  }
});

test('W19: slug de HEATING_CURSOS cujo <slug>@guifav.dev está em HEATING_REMETENTES_SITE sai dos cursos com log curto e continua site', async () => {
  const s = setup({
    heatingCursos: 'insights, chatgpt-no-dia-a-dia',
    heatingSiteSenders: 'insights@guifav.dev, insights@expert.guifav.dev',
  });
  let rSite!: Response;
  let rCurso!: Response;
  const erros = await capturaErros(async () => {
    rSite = await s.run(webhookReq({
      type: 'email.opened',
      created_at: '2026-11-27T12:00:00.000Z',
      data: { email_id: 'em_conf_site', from: 'Guilherme Favaron <insights@guifav.dev>', to: ['leitor@exemplo.com'] },
    }, { id: 'msg_conf_site' }));
    rCurso = await s.run(webhookReq({
      type: 'email.opened',
      created_at: '2026-11-27T12:00:00.000Z',
      data: { email_id: 'em_conf_curso', from: 'chatgpt-no-dia-a-dia@guifav.dev', to: ['leitor@exemplo.com'] },
    }, { id: 'msg_conf_curso' }));
  });
  assert.equal(rSite.status, 200);
  assert.equal(rCurso.status, 200);
  assert.deepEqual(
    s.rows('engagement_events').map((x) => [x.ref, x.context]),
    [['em_conf_site', 'site'], ['em_conf_curso', 'curso:chatgpt-no-dia-a-dia']],
  );
  assert.ok(
    erros.some((e) => /1 slug\(s\) de HEATING_CURSOS em conflito com HEATING_REMETENTES_SITE ignorado\(s\)/.test(e)),
    erros.join('\n'),
  );
  for (const e of erros) {
    assert.ok(!e.includes('insights'), e);
    assert.ok(!e.includes('guifav.dev'), e);
  }
});

test('W20: assinatura svix é verificada sobre id.ts. + bytes brutos do corpo (corpo com BOM UTF-8 passa só com assinatura sobre os bytes)', async () => {
  const s = setup();
  const ts = Math.floor(NOW / 1000);
  const jsonText = JSON.stringify({
    type: 'email.opened',
    created_at: '2026-11-27T12:00:00.000Z',
    data: { email_id: 'em_bom', from: FROM_SITE, to: ['bom@exemplo.com'] },
  });
  const jsonBytes = Buffer.from(jsonText, 'utf-8');
  const bomBytes = Buffer.concat([Buffer.from([0xef, 0xbb, 0xbf]), jsonBytes]);

  // Assinatura calculada sobre os bytes brutos (com o BOM): passa e grava o evento.
  const sigSobreBytes = `v1,${createHmac('sha256', WEBHOOK_RAW)
    .update(Buffer.concat([Buffer.from(`msg_bom_ok.${ts}.`, 'utf-8'), bomBytes]))
    .digest('base64')}`;
  const reqOk = new Request('https://insights.guifav.dev/resend/webhook', {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      'svix-id': 'msg_bom_ok',
      'svix-timestamp': String(ts),
      'svix-signature': sigSobreBytes,
    },
    body: bomBytes,
  });
  const rOk = await s.run(reqOk);
  assert.equal(rOk.status, 200);
  assert.deepEqual(await rOk.json(), { ok: true, gravados: 1 });
  assert.deepEqual(s.rows('engagement_events').map((x) => [x.email, x.ref]), [['bom@exemplo.com', 'em_bom']]);

  // Assinatura calculada sobre o texto decodificado sem o BOM para o mesmo corpo com BOM: recusada (401).
  const sigSemBom = assinarSvix('msg_bom_sem', ts, jsonText);
  const reqRuim = new Request('https://insights.guifav.dev/resend/webhook', {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      'svix-id': 'msg_bom_sem',
      'svix-timestamp': String(ts),
      'svix-signature': sigSemBom,
    },
    body: bomBytes,
  });
  const rRuim = await s.run(reqRuim);
  assert.equal(rRuim.status, 401);
  assert.equal(s.rows('engagement_events').length, 1);
});

// ---------------------------------------------------------------- POST /prioridade

test('P1: /prioridade grava a marca, apaga eventos e heating de todos os contextos só do email informado e não chama o Resend', async () => {
  const s = setup();
  const iso = new Date(NOW - 60_000).toISOString();
  for (const email of ['leitor@exemplo.com', 'outro@exemplo.com']) {
    for (const ctx of ['site', 'curso:chatgpt-no-dia-a-dia']) {
      s.db.prepare(`INSERT INTO engagement_events (id, context, email, signal, source, ref, occurred_at, recorded_at)
        VALUES (?, ?, ?, 'email_aberto', 'resend', 'em_1', ?, ?)`).run(`rs:${ctx}:${email.split('@')[0]}`, ctx, email, iso, iso);
      s.db.prepare(`INSERT INTO heating (context, email, score, band, signals, computed_at)
        VALUES (?, ?, 60, 'morno', '["email_aberto"]', ?)`).run(ctx, email, iso);
    }
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
  assert.deepEqual(s.rows('engagement_events').map((x) => [x.context, x.email]), [
    ['site', 'outro@exemplo.com'],
    ['curso:chatgpt-no-dia-a-dia', 'outro@exemplo.com'],
  ]);
  assert.deepEqual(s.rows('heating').map((x) => [x.context, x.email]), [
    ['site', 'outro@exemplo.com'],
    ['curso:chatgpt-no-dia-a-dia', 'outro@exemplo.com'],
  ]);

  // Repetir a oposição depois atualiza opted_out_at (upsert) sem erro.
  s.avanca(5_000);
  const r2 = await s.run(prioridadeReq({ email: 'leitor@exemplo.com', 'cf-turnstile-response': 'tok' }));
  assert.equal(r2.status, 303);
  assert.equal(loc(r2), 'https://guifav.dev/prioridade/#pronto');
  assert.equal(s.rows('heating_optout')[0].opted_out_at, new Date(NOW + 5_000).toISOString());

  // Webhook que chega depois (tanto do site quanto do curso) não volta a gravar eventos do email oposto.
  for (const [id, from] of [
    ['msg_pos_optout_site', 'Guilherme Favaron <insights@guifav.dev>'],
    ['msg_pos_optout_curso', 'Guilherme Favaron | ChatGPT no dia a dia <chatgpt-no-dia-a-dia@guifav.dev>'],
  ] as const) {
    const rw = await s.run(webhookReq({
      type: 'email.clicked',
      created_at: new Date(NOW + 5_000).toISOString(),
      data: { email_id: 'em_pos', from, to: ['leitor@exemplo.com'], click: { link: 'https://guifav.dev/blog/post/' } },
    }, { id, tsSec: Math.floor((NOW + 5_000) / 1000) }));
    assert.equal(rw.status, 200, id);
  }
  assert.deepEqual(s.rows('engagement_events').map((x) => x.email), ['outro@exemplo.com', 'outro@exemplo.com']);
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
  s.db.prepare(`INSERT INTO engagement_events (id, context, email, signal, source, ref, occurred_at, recorded_at)
    VALUES ('rs:1', 'site', 'sigiloso@exemplo.com', 'email_aberto', 'resend', 'em_1', ?, ?)`).run(iso, iso);

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

test('P5: FORGET_HMAC_KEY ausente/curta e TURNSTILE_SECRET ausente em /prioridade devolvem 500 Worker sem configuração', async () => {
  for (const [nome, opts] of [
    ['FORGET_HMAC_KEY undefined', { forgetKey: undefined }],
    ['FORGET_HMAC_KEY vazia', { forgetKey: '' }],
    ['FORGET_HMAC_KEY curta (31)', { forgetKey: 'x'.repeat(31) }],
    ['TURNSTILE_SECRET undefined', { turnstileSecret: undefined }],
    ['TURNSTILE_SECRET vazia', { turnstileSecret: '' }],
  ] as Array<[string, SetupOpts]>) {
    const s = setup(opts);
    const r = await s.run(prioridadeReq({ email: 'a@b.com', 'cf-turnstile-response': 'tok' }));
    assert.equal(r.status, 500, nome);
    assert.equal(await r.text(), 'Worker sem configuração', nome);
    assert.equal(s.calls.length, 0, nome);
    assert.equal(s.rows('heating_optout').length, 0, nome);
  }
});

test('C1: wrangler.toml documenta RESEND_HEATING_WEBHOOK_SECRET sem segredo e declara HEATING_REMETENTES_SITE e HEATING_CURSOS', () => {
  assert.match(TOML, /#.*RESEND_HEATING_WEBHOOK_SECRET/);
  assert.doesNotMatch(TOML, /RESEND_HEATING_WEBHOOK_SECRET\s*=|whsec_[A-Za-z0-9+/=]{16,}/);
  assert.equal(tomlVar('HEATING_REMETENTES_SITE'), 'insights@guifav.dev,insights@expert.guifav.dev');
  assert.match(TOML, /#.*HEATING_REMETENTES_SITE|#.*remetente novo do site/i);
  assert.equal(tomlVar('HEATING_CURSOS'), 'chatgpt-no-dia-a-dia');
  assert.match(TOML, /#.*<slug>@guifav\.dev/);
});

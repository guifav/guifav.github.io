// Testes das telas novas do painel (/admin, /admin/assinantes, /admin/contatos, /admin/conteudos) e da coleta do
// catálogo de posts (issue #193, fase 1 do épico #192). Rodar em workers/admin/: npm test
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { DatabaseSync } from 'node:sqlite';
import { handle, type Deps, type Env } from '../src/index.ts';
import { resetCertsCache } from '../src/access.ts';
import { CATALOG_MAX_BYTES, coletarCatalogo, dataExiste, validarCatalogo } from '../src/catalogo.ts';
import { sha256Hex, versionId, type R2Like } from '../src/drafts.ts';
import { heatingScoreCell, heatingSignalsCell, landingCell, twentyCell, twentyPersonUrl } from '../src/painel.ts';
import type { D1Like, D1Stmt } from '../src/sync.ts';
import { catalogoPosts } from '../../../blog-src/src/lib/catalogo.ts';

const NOW = Date.UTC(2026, 10, 12, 12, 0, 0); // 2026-11-12T12:00:00.000Z
const DAY_MS = 86_400_000;
const plain = <T>(x: T): T => JSON.parse(JSON.stringify(x));
const TOML = readFileSync(new URL('../wrangler.toml', import.meta.url), 'utf-8');
const tomlVar = (k: string) => {
  const m = TOML.match(new RegExp(`^${k}\\s*=\\s*"([^"]*)"`, 'm'));
  assert.ok(m, `variável ${k} ausente do wrangler.toml`);
  return m[1];
};
const TEAM = tomlVar('ACCESS_TEAM_DOMAIN');
const AUD = tomlVar('ACCESS_AUD');
const ORIGIN = tomlVar('SITE_ORIGIN');
const TWENTY = tomlVar('TWENTY_ORIGIN');
const GUI = 'gui@exemplo.com';
const SHA = (c: string) => c.repeat(64);
const enc = (s: string) => new TextEncoder().encode(s);

// ------------------------------------------------------------------ D1 falso com todas as migrações
interface FakeD1 extends D1Like {
  raw: DatabaseSync;
  queries: number;
  maxParams: number;
  failSql?: RegExp;
  failInsertCatalogAt?: number;
}

function d1(opts: { failSql?: RegExp; failInsertCatalogAt?: number } = {}): FakeD1 {
  const db = new DatabaseSync(':memory:');
  const dir = new URL('../migrations/', import.meta.url);
  const files = readdirSync(dir).filter((f) => f.endsWith('.sql')).sort();
  if (!files.some((f) => f.startsWith('0013_'))) {
    db.exec("CREATE TABLE IF NOT EXISTS site_identities (uid TEXT PRIMARY KEY NOT NULL, email TEXT NOT NULL, linked_at TEXT NOT NULL);");
  }
  for (const f of files) {
    db.exec(readFileSync(new URL(f, dir), 'utf-8'));
  }
  let catalogInserts = 0;
  const fake: FakeD1 = {
    raw: db,
    queries: 0,
    maxParams: 0,
    failSql: opts.failSql,
    failInsertCatalogAt: opts.failInsertCatalogAt,
    prepare: (sql: string) => stmt(sql),
    batch: async (stmts: D1Stmt[]) => {
      fake.queries++;
      db.exec('BEGIN');
      try {
        const out = [];
        for (const s of stmts) out.push(await s.run());
        db.exec('COMMIT');
        return out;
      } catch (e) {
        db.exec('ROLLBACK');
        throw e;
      }
    },
  };
  const stmt = (sql: string, args: unknown[] = []): D1Stmt => ({
    bind: (...v: unknown[]) => {
      if (v.length > fake.maxParams) fake.maxParams = v.length;
      assert.ok(v.length <= 100, `limite de 100 parâmetros do D1 excedido: ${v.length}`);
      return stmt(sql, v);
    },
    run: async () => {
      fake.queries++;
      if (fake.failSql?.test(sql)) throw new Error('D1 indisponível');
      if (fake.failInsertCatalogAt !== undefined && /INSERT INTO content_catalog/.test(sql)) {
        catalogInserts++;
        if (catalogInserts === fake.failInsertCatalogAt) throw new Error('falha no meio do batch');
      }
      return db.prepare(sql).run(...(args as never[]));
    },
    all: async <T>() => {
      fake.queries++;
      if (fake.failSql?.test(sql)) throw new Error('D1 indisponível');
      return { results: plain(db.prepare(sql).all(...(args as never[]))) as T[] };
    },
    first: async <T>() => {
      fake.queries++;
      if (fake.failSql?.test(sql)) throw new Error('D1 indisponível');
      const r = db.prepare(sql).get(...(args as never[]));
      return (r ? plain(r) : null) as T;
    },
  });
  return fake;
}

// ------------------------------------------------------------------ R2 falso
function r2(opts: { failList?: boolean } = {}) {
  const store = new Map<string, Uint8Array>();
  const gets: string[] = [];
  const bucket: R2Like & { store: Map<string, Uint8Array>; gets: string[]; failList: boolean } = {
    store,
    gets,
    failList: opts.failList ?? false,
    async get(key) {
      gets.push(key);
      const b = store.get(key);
      return b ? { arrayBuffer: async () => b.slice().buffer, text: async () => new TextDecoder().decode(b) } : null;
    },
    async list({ prefix = '', delimiter } = {}) {
      if (bucket.failList) throw new Error('R2 indisponível');
      const keys = [...store.keys()].filter((k) => k.startsWith(prefix)).sort();
      if (!delimiter) return { objects: keys.map((key) => ({ key })), delimitedPrefixes: [], truncated: false };
      const pre = new Set<string>();
      const objects: { key: string }[] = [];
      for (const k of keys) {
        const i = k.indexOf(delimiter, prefix.length);
        if (i >= 0) pre.add(k.slice(0, i + 1));
        else objects.push({ key: k });
      }
      return { objects, delimitedPrefixes: [...pre], truncated: false };
    },
    async delete() {},
  };
  return bucket;
}

async function putDraft(
  b: ReturnType<typeof r2>,
  slug: string,
  opts: { title?: string; source?: string; html?: string; manifest?: Record<string, unknown> } = {},
) {
  const html = opts.html ?? `<html><body><h1>${opts.title ?? slug}</h1></body></html>`;
  const htmlSha = await sha256Hex(html);
  const source = opts.source ?? SHA('a');
  const version = await versionId(htmlSha, source);
  b.store.set(`${slug}/v/${version}/index.html`, enc(html));
  b.store.set(
    `${slug}/manifest.json`,
    enc(
      JSON.stringify({
        slug,
        title: opts.title ?? slug,
        uploaded_at: '2026-11-12T10:00:00.000Z',
        source_sha256: source,
        html_sha256: htmlSha,
        version,
        files: { 'index.html': 'text/html; charset=utf-8' },
        ...(opts.manifest ?? {}),
      }),
    ),
  );
  return { htmlSha, source, version };
}

// ------------------------------------------------------------------ Access (JWT RS256 real)
const kp = (await crypto.subtle.generateKey(
  { name: 'RSASSA-PKCS1-v1_5', modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: 'SHA-256' },
  true,
  ['sign', 'verify'],
)) as CryptoKeyPair;
const otherKp = (await crypto.subtle.generateKey(
  { name: 'RSASSA-PKCS1-v1_5', modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: 'SHA-256' },
  true,
  ['sign', 'verify'],
)) as CryptoKeyPair;
const jwk = { ...(await crypto.subtle.exportKey('jwk', kp.publicKey)), kid: 'k1' };
const b64u = (b: ArrayBuffer | string) => Buffer.from(typeof b === 'string' ? b : new Uint8Array(b)).toString('base64url');

async function jwt(claims: Record<string, unknown> = {}, key: CryptoKey = kp.privateKey) {
  const h = b64u(JSON.stringify({ alg: 'RS256', kid: 'k1', typ: 'JWT' }));
  const p = b64u(JSON.stringify({ iss: `https://${TEAM}`, aud: [AUD], email: GUI, exp: NOW / 1000 + 3600, iat: NOW / 1000, ...claims }));
  const sig = await crypto.subtle.sign('RSASSA-PKCS1-v1_5', key, new TextEncoder().encode(`${h}.${p}`));
  return `${h}.${p}.${b64u(sig)}`;
}

// ------------------------------------------------------------------ Helpers de seed e setup
interface ConvSeed {
  id: string;
  email: string;
  action: 'material_request' | 'material_download' | 'newsletter_confirm';
  material?: string | null;
  file?: string | null;
  verified?: number;
  occurred_at: string;
  synced_at?: string | null;
  origin_channel?: string | null;
  origin_source?: string | null;
  utm_source?: string | null;
  utm_medium?: string | null;
  utm_campaign?: string | null;
  landing_path?: string | null;
  twenty_person_id?: string | null;
}

function seedConversions(db: FakeD1, items: ConvSeed[]) {
  const ins = db.raw.prepare(`INSERT INTO conversions
    (id, email, action, material, interests, occurred_at, synced_at, verified, file,
     origin_channel, origin_source, utm_source, utm_medium, utm_campaign, landing_path, twenty_person_id)
    VALUES (?, ?, ?, ?, '[]', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`);
  for (const r of items) {
    ins.run(
      r.id,
      r.email,
      r.action,
      r.material ?? null,
      r.occurred_at,
      r.synced_at ?? null,
      r.verified ?? (r.action === 'material_request' ? 0 : 1),
      r.file ?? null,
      r.origin_channel ?? null,
      r.origin_source ?? null,
      r.utm_source ?? null,
      r.utm_medium ?? null,
      r.utm_campaign ?? null,
      r.landing_path ?? null,
      r.twenty_person_id ?? null,
    );
  }
}

function seedSubscribers(db: FakeD1, rows: { id: string; email: string; unsubscribed?: number; origin?: string }[]) {
  const ins = db.raw.prepare(`INSERT INTO subscribers (id, email, unsubscribed, origin, joined_at, paid_until, site_confirmed_at,
    admin_unsubscribed_at, created_at, run, rev) VALUES (?, ?, ?, ?, '2026-11-01', '', '', '', '', 'r0', 0)`);
  for (const r of rows) ins.run(r.id, r.email, r.unsubscribed ?? 0, r.origin ?? 'site');
}

function seedCatalog(db: FakeD1, posts: { path: string; title: string; published_on: string }[], collector?: {
  last_attempt_at: string;
  last_success_at?: string | null;
  last_error?: string | null;
  item_count?: number | null;
}) {
  const ins = db.raw.prepare('INSERT INTO content_catalog (path, title, published_on) VALUES (?, ?, ?)');
  for (const p of posts) ins.run(p.path, p.title, p.published_on);
  if (collector) {
    db.raw
      .prepare('INSERT INTO collectors (source, last_attempt_at, last_success_at, last_error, item_count) VALUES (?, ?, ?, ?, ?)')
      .run('catalogo_posts', collector.last_attempt_at, collector.last_success_at ?? null, collector.last_error ?? null, collector.item_count ?? null);
  }
}

function segurarAteAbortar(signal: AbortSignal) {
  const segura = setTimeout(() => {}, 10_000);
  signal.addEventListener('abort', () => clearTimeout(segura), { once: true });
}

function setup(opts: { failSql?: RegExp; failInsertCatalogAt?: number; failR2?: boolean; onFetch?: (url: string, init?: RequestInit) => Promise<Response> | Response } = {}) {
  const db = d1({ failSql: opts.failSql, failInsertCatalogAt: opts.failInsertCatalogAt });
  const bucket = r2({ failList: opts.failR2 });
  const externalCalls: string[] = [];
  const fetchFn = async (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input instanceof Request ? input.url : input);
    if (url === `https://${TEAM}/cdn-cgi/access/certs`) return Response.json({ keys: [jwk] });
    externalCalls.push(url);
    if (opts.onFetch) return opts.onFetch(url, init);
    return Response.json({ message: 'inesperado' }, { status: 500 });
  };
  const env: Env = {
    DB: db,
    RESEND_API_KEY: 're_test',
    SEGMENT_ID: tomlVar('SEGMENT_ID'),
    TOPIC_ID: tomlVar('TOPIC_ID'),
    ACCESS_TEAM_DOMAIN: TEAM,
    ACCESS_AUD: AUD,
    ALLOWED_EMAILS: GUI,
    SITE_ORIGIN: ORIGIN,
    TWENTY_ORIGIN: TWENTY,
    DRAFTS: bucket,
    DRAFT_NOTIFY_FROM: '',
    DRAFT_NOTIFY_TO: '',
    GITHUB_DISPATCH_TOKEN: 'gh_test',
  };
  const deps: Deps = {
    fetch: fetchFn as typeof fetch,
    now: () => NOW,
    sleep: async () => {},
    origin: async () => new Response('pages'),
    catalogTimeoutMs: 50,
  };
  const req = async (path: string, init: { method?: string; token?: string | null } = {}) => {
    resetCertsCache();
    const headers: Record<string, string> = {};
    if (init.token !== null) headers['cf-access-jwt-assertion'] = init.token ?? (await jwt());
    return handle(new Request(`https://guifav.dev${path}`, { method: init.method ?? 'GET', headers }), env, deps);
  };
  return { db, bucket, env, deps, externalCalls, req };
}

// ------------------------------------------------------------------ Acesso e cabeçalhos de segurança
test('P1: sem JWT ou com JWT inválido, todas as rotas novas respondem 403 e não leem o D1', async () => {
  const s = setup();
  const bad: (string | null)[] = [null, 'lixo', await jwt({}, otherKp.privateKey), await jwt({ email: 'outro@exemplo.com' })];
  for (const path of ['/admin', '/admin/', '/admin/assinantes', '/admin/contatos', '/admin/conteudos']) {
    for (const token of bad) {
      const r = await s.req(path, { token });
      assert.equal(r.status, 403, `${path} ${token}`);
      assert.equal(await r.text(), 'acesso negado', path);
      assert.equal(r.headers.get('cache-control'), 'no-store', path);
    }
  }
  assert.equal(s.db.queries, 0, 'nenhuma leitura do D1 sem JWT válido');
});

test('P1: cabeçalhos de segurança em cada tela nova e no 303 de redirecionamento', async () => {
  const s = setup();
  for (const [path, status] of [
    ['/admin', 200],
    ['/admin/', 200],
    ['/admin?status=inscrito', 303],
    ['/admin/assinantes', 200],
    ['/admin/contatos', 200],
    ['/admin/conteudos', 200],
  ] as const) {
    const r = await s.req(path);
    assert.equal(r.status, status, path);
    assert.equal(r.headers.get('cache-control'), 'no-store', path);
    assert.equal(r.headers.get('x-robots-tag'), 'noindex, nofollow', path);
    assert.equal(r.headers.get('x-frame-options'), 'DENY', path);
    assert.equal(r.headers.get('x-content-type-options'), 'nosniff', path);
    assert.equal(r.headers.get('referrer-policy'), 'same-origin', path);
    assert.match(r.headers.get('content-security-policy') ?? '', /^default-src 'none'.*frame-ancestors 'none'/, path);
  }
  assert.deepEqual(s.externalCalls, [], 'nenhuma tela chama serviço externo ao abrir');
});

test('P1: TWENTY_ORIGIN no wrangler.toml é https://crm.guifav.dev', () => {
  assert.equal(TWENTY, 'https://crm.guifav.dev');
});

// ------------------------------------------------------------------ Redirecionamento de /admin e navegação
test('P2: /admin redireciona 303 para /admin/assinantes com parâmetros de assinantes; sem eles mostra o resumo', async () => {
  const s = setup();
  for (const [orig, dest] of [
    ['/admin?status=inscrito&p=2', '/admin/assinantes?status=inscrito&p=2'],
    ['/admin?msg=descadastrado', '/admin/assinantes?msg=descadastrado'],
    ['/admin/?q=ana&origem=site&pago=sim', '/admin/assinantes?q=ana&origem=site&pago=sim'],
  ]) {
    const r = await s.req(orig);
    assert.equal(r.status, 303, orig);
    assert.equal(r.headers.get('location'), dest, orig);
  }
  for (const path of ['/admin', '/admin/', '/admin?utm_source=x']) {
    const r = await s.req(path);
    assert.equal(r.status, 200, path);
    const html = await r.text();
    assert.ok(html.includes('<title>Resumo | Painel</title>'), path);
    assert.ok(
      html.includes('<p>Resumo · <a href="/admin/assinantes">Assinantes</a> · <a href="/admin/rascunhos">Rascunhos</a> · <a href="/admin/contatos">Contatos</a> · <a href="/admin/conteudos">Conteúdos</a></p>'),
      path,
    );
  }
});

// ------------------------------------------------------------------ Resumo (/admin)
test('P3: resumo calcula janelas de 7 e 30 dias com bordas exatas, conta rascunhos pela regra da lista sem chamar GitHub e lista 10 mais recentes', async () => {
  const s = setup();
  seedSubscribers(s.db, [
    { id: 's1', email: 'a@x.com', unsubscribed: 0 },
    { id: 's2', email: 'b@x.com', unsubscribed: 0 },
    { id: 's3', email: 'c@x.com', unsubscribed: 1 },
  ]);
  s.db.raw.prepare("INSERT INTO meta (k, v) VALUES ('last_sync_at', '2026-11-12T11:17:00Z'), ('last_sync_error', 'erro teste')").run();

  // Rascunhos: 1 não aprovado (conta), 1 aprovado mas versão mudou (conta), 1 aprovado na versão atual (não conta),
  // 1 não aprovado com prévia adulterada (não conta), 1 não aprovado sem HTML da versão (não conta),
  // 1 derivado linkedin (não conta), 1 manifesto inválido (não conta).
  const naoAprovado = await putDraft(s.bucket, 'nao-aprovado');
  const mudado = await putDraft(s.bucket, 'mudado', { title: 'v2' });
  s.db.raw
    .prepare(`INSERT INTO draft_approvals (slug, title, html_sha256, source_sha256, approved_at, approved_by)
      VALUES ('mudado', 'v1', ?, ?, '2026-11-10T10:00:00Z', ?)`)
    .run(SHA('1'), SHA('2'), GUI);
  const aprovado = await putDraft(s.bucket, 'aprovado');
  s.db.raw
    .prepare(`INSERT INTO draft_approvals (slug, title, html_sha256, source_sha256, approved_at, approved_by)
      VALUES ('aprovado', 'aprovado', ?, ?, '2026-11-11T10:00:00Z', ?)`)
    .run(aprovado.htmlSha, aprovado.source, GUI);
  const adulterado = await putDraft(s.bucket, 'previa-adulterada');
  s.bucket.store.set(`previa-adulterada/v/${adulterado.version}/index.html`, enc('<html><body>adulterado</body></html>'));
  const semPrevia = await putDraft(s.bucket, 'sem-previa');
  s.bucket.store.delete(`sem-previa/v/${semPrevia.version}/index.html`);
  await putDraft(s.bucket, 'derivado-li', { manifest: { kind: 'linkedin', parent: 'nao-aprovado' } });
  s.bucket.store.set('quebrado/manifest.json', enc('{invalido'));
  assert.ok(mudado.version);

  const iso = (deltaMs: number) => new Date(NOW + deltaMs).toISOString();
  // Conversões: testar bordas de 7d e 30d + mais de 10 linhas para confirmar limite de 10 recentes.
  seedConversions(s.db, [
    // Exatamente na borda de 7d (entra em 7d e 30d)
    { id: 'c01', email: 'borda7@x.com', action: 'material_request', material: 'planilha-custos-ia', occurred_at: iso(-7 * DAY_MS) },
    // 1 ms fora de 7d (só entra em 30d)
    { id: 'c02', email: 'fora7@x.com', action: 'material_request', material: 'planilha-custos-ia', occurred_at: iso(-7 * DAY_MS - 1) },
    // Exatamente na borda de 30d (entra em 30d)
    { id: 'c03', email: 'borda30@x.com', action: 'material_download', material: 'planilha-custos-ia', file: 'planilha-custos-ia', occurred_at: iso(-30 * DAY_MS) },
    // 1 ms fora de 30d (fica fora das duas janelas)
    { id: 'c04', email: 'fora30@x.com', action: 'material_download', material: 'planilha-custos-ia', file: 'planilha-custos-ia', occurred_at: iso(-30 * DAY_MS - 1) },
    // Dentro de 7d: 2 downloads, 1 newsletter_confirm, mais 7 pedidos recentes (para passar de 10 no total)
    { id: 'c05', email: 'dl1@x.com', action: 'material_download', material: 'mapa-decisao-ia', file: 'mapa-decisao-pdf', occurred_at: iso(-1 * DAY_MS), synced_at: iso(-1000), twenty_person_id: '11111111-2222-3333-4444-555555555555' },
    { id: 'c06', email: 'dl2@x.com', action: 'material_download', material: 'mapa-decisao-ia', file: 'conta-custo-por-pergunta', occurred_at: iso(-2 * DAY_MS) },
    { id: 'c07', email: 'nl1@x.com', action: 'newsletter_confirm', occurred_at: iso(-3 * DAY_MS), synced_at: iso(-1000) },
    { id: 'c08', email: 'nl30@x.com', action: 'newsletter_confirm', occurred_at: iso(-20 * DAY_MS) },
    { id: 'c09', email: 'r1@x.com', action: 'material_request', material: 'planilha-custos-ia', occurred_at: iso(-1000) },
    { id: 'c10', email: 'r2@x.com', action: 'material_request', material: 'planilha-custos-ia', occurred_at: iso(-2000) },
    { id: 'c11', email: 'r3@x.com', action: 'material_request', material: 'planilha-custos-ia', occurred_at: iso(-3000) },
    { id: 'c12', email: 'r4@x.com', action: 'material_request', material: 'planilha-custos-ia', occurred_at: iso(-4000) },
    { id: 'c13', email: 'r5@x.com', action: 'material_request', material: 'planilha-custos-ia', occurred_at: iso(-5000) },
  ]);

  seedCatalog(s.db, [{ path: '/blog/post-a/', title: 'Post A', published_on: '2026-11-10' }], {
    last_attempt_at: '2026-11-12T11:17:00.000Z',
    last_success_at: '2026-11-12T10:17:00.000Z',
    last_error: 'site respondeu 503',
    item_count: 1,
  });

  const r = await s.req('/admin');
  assert.equal(r.status, 200);
  const html = await r.text();
  // Assinantes
  assert.ok(html.includes('Inscritos: <strong>2</strong> · Descadastrados: 1 · Total: 3'));
  assert.ok(html.includes('2026-11-12T11:17:00Z'));
  assert.ok(html.includes('Última sincronização com erro: erro teste'));
  // Rascunhos: 2 artigos aguardando aprovação (nao-aprovado e mudado); lê o HTML só dos não aprovados e não chama GitHub.
  assert.ok(html.includes('<strong>2</strong> artigos aguardando aprovação'));
  const htmlGets = s.bucket.gets.filter((k) => !k.endsWith('/manifest.json')).sort();
  assert.deepEqual(
    htmlGets,
    [
      `mudado/v/${mudado.version}/index.html`,
      `nao-aprovado/v/${naoAprovado.version}/index.html`,
      `previa-adulterada/v/${adulterado.version}/index.html`,
      `sem-previa/v/${semPrevia.version}/index.html`,
    ],
    `leu HTML só dos artigos não aprovados: ${htmlGets}`,
  );
  assert.deepEqual(s.externalCalls, [], 'resumo não chama GitHub nem nenhum serviço externo');
  // Conversões: 7d -> 6 pedidos (c01, c09..c13), 2 downloads (c05, c06), 1 inscrição (c07).
  // 30d -> 7 pedidos (+c02), 3 downloads (+c03), 2 inscrições (+c08). c04 fica fora.
  assert.ok(html.includes('<strong>Últimos 7 dias:</strong> 6 pedidos, 2 downloads, 1 inscrições'));
  assert.ok(html.includes('<strong>Últimos 30 dias:</strong> 7 pedidos, 3 downloads, 2 inscrições'));
  // Catálogo
  assert.ok(html.includes('Catálogo de 2026-11-12 10:17 UTC, 1 post.'));
  assert.ok(html.includes('A última coleta falhou em 2026-11-12 11:17 UTC: site respondeu 503'));
  // 10 conversões mais recentes (c04, c03 e c08 ficam de fora)
  const tbody = html.match(/<tbody>([\s\S]*?)<\/tbody>/)?.[1] ?? '';
  assert.equal((tbody.match(/<tr>/g) ?? []).length, 10);
  assert.ok(tbody.includes('r1@x.com'));
  assert.ok(tbody.includes('https://crm.guifav.dev/object/person/11111111-2222-3333-4444-555555555555'));
  assert.ok(!tbody.includes('fora30@x.com'));
  assert.ok(html.includes('<a href="/admin/contatos">ver todos os contatos</a>'));
});

test('P3: falha do R2 ou de uma consulta do D1 deixa só aquele cartão indisponível e a página responde 200', async () => {
  const s1 = setup({ failR2: true });
  const r1 = await s1.req('/admin');
  assert.equal(r1.status, 200);
  const h1 = await r1.text();
  assert.match(h1, /Rascunhos<\/a><\/h2><p class="erro">indisponível agora<\/p>/);
  assert.match(h1, /Assinantes<\/a><\/h2>\n<p>Inscritos:/);

  const s2 = setup({ failSql: /FROM conversions/ });
  const r2 = await s2.req('/admin');
  assert.equal(r2.status, 200);
  const h2 = await r2.text();
  assert.match(h2, /Conversões<\/a><\/h2><p class="erro">indisponível agora<\/p>/);
  assert.match(h2, /Assinantes<\/a><\/h2>\n<p>Inscritos:/);
});

// ------------------------------------------------------------------ Contatos (/admin/contatos)
test('P4: contatos mostra colunas, links do Twenty, textos sem id, filtros, escape e material fora do catálogo', async () => {
  const s = setup();
  seedConversions(s.db, [
    {
      id: 'k1',
      email: 'ana@x.com',
      action: 'material_download',
      material: 'mapa-decisao-ia',
      file: 'mapa-decisao-pdf',
      verified: 1,
      occurred_at: '2026-11-12T10:30:00.000Z',
      synced_at: '2026-11-12T11:00:00.000Z',
      origin_channel: 'busca',
      origin_source: 'google',
      utm_source: 'google',
      utm_medium: 'cpc',
      utm_campaign: 'ia_2026',
      landing_path: '/blog/post-a/',
      twenty_person_id: 'abc-123-XYZ',
    },
    {
      id: 'k2',
      email: 'bia@y.com',
      action: 'material_request',
      material: 'planilha-custos-ia',
      verified: 0,
      occurred_at: '2026-11-11T09:15:00.000Z',
      synced_at: null,
      origin_channel: null,
      landing_path: null,
    },
    {
      id: 'k3',
      email: 'caio@z.com',
      action: 'newsletter_confirm',
      verified: 1,
      occurred_at: '2026-11-10T08:00:00.000Z',
      synced_at: null,
      origin_channel: 'direto',
      utm_source: 'news',
      landing_path: '/ferramentas/planilha-custos-ia/',
    },
    {
      id: 'k4',
      email: 'dani@x.com',
      action: 'material_download',
      material: 'material-antigo',
      file: 'arquivo-antigo',
      verified: 1,
      occurred_at: '2026-11-09T07:45:00.000Z',
      synced_at: '2026-11-09T08:00:00.000Z',
      origin_channel: 'social',
      origin_source: 'linkedin',
      twenty_person_id: null,
    },
    {
      id: 'k5',
      email: '<script>x</script>@evil.com',
      action: 'material_request',
      material: 'planilha-custos-ia',
      verified: 0,
      occurred_at: '2026-11-08T06:00:00.000Z',
    },
  ]);

  const html = await (await s.req('/admin/contatos')).text();
  assert.ok(html.includes('<p><a href="/admin">Resumo</a> · <a href="/admin/assinantes">Assinantes</a> · <a href="/admin/rascunhos">Rascunhos</a> · Contatos · <a href="/admin/conteudos">Conteúdos</a></p>'));
  assert.ok(html.includes('5 encontrados, página 1 de 1.'));
  // Colunas e formatações
  assert.ok(html.includes('2026-11-12 10:30'));
  assert.ok(html.includes('<td>inscrição nos Expert Insights</td>'));
  assert.ok(html.includes('<option value="newsletter_confirm">inscrição nos Expert Insights</option>'));
  assert.ok(html.includes('Mapa de decisão: RAG, Text-to-SQL ou contexto longo'));
  assert.ok(html.includes('mapa-decisao-rag-sql-contexto-longo.pdf'));
  assert.ok(html.includes('material-antigo (fora do catálogo)'));
  assert.ok(html.includes('arquivo-antigo (fora do catálogo)'));
  assert.ok(html.includes('busca / google'));
  assert.ok(html.includes('sem medição'));
  assert.ok(html.includes('google / cpc / ia_2026'));
  assert.ok(html.includes('<a href="https://guifav.dev/blog/post-a/">/blog/post-a/</a>'));
  // Link do Twenty e os textos sem id
  assert.ok(html.includes('<a href="https://crm.guifav.dev/object/person/abc-123-XYZ" target="_blank" rel="noopener noreferrer">abrir no Twenty</a>'));
  assert.ok(html.includes('<td>email não confirmado: não abriu o link do material</td>'));
  assert.ok(html.includes('<td>email confirmado: entra no CRM na próxima sincronização</td>'));
  assert.ok(html.includes('<td>sincronizada, sem link</td>'));
  // Escape de email hostil
  assert.ok(!html.includes('<script>x</script>'));
  assert.ok(html.includes('&lt;script&gt;x&lt;/script&gt;@evil.com'));

  // Filtros individuais e combinados
  const emails = async (qs: string) => {
    const body = await (await s.req(`/admin/contatos${qs}`)).text();
    const tbody = body.match(/<tbody>([\s\S]*?)<\/tbody>/)?.[1] ?? '';
    return [...tbody.matchAll(/<tr>\s*<td>[^<]*<\/td>\s*<td>([^<]+)<\/td>/g)].map((m) => m[1]);
  };
  assert.deepEqual(await emails('?q=X.COM'), ['ana@x.com', 'dani@x.com']);
  assert.deepEqual(await emails('?q=%25'), [], 'busca não usa curinga LIKE');
  assert.deepEqual(await emails('?acao=newsletter_confirm'), ['caio@z.com']);
  assert.deepEqual(await emails('?acao=material_download'), ['ana@x.com', 'dani@x.com']);
  assert.deepEqual(await emails('?material=mapa-decisao-ia'), ['ana@x.com']);
  assert.deepEqual(await emails('?comprovado=sim'), ['ana@x.com', 'caio@z.com', 'dani@x.com']);
  assert.deepEqual(await emails('?comprovado=nao'), ['bia@y.com', '&lt;script&gt;x&lt;/script&gt;@evil.com']);
  assert.deepEqual(await emails('?canal=busca'), ['ana@x.com']);
  assert.deepEqual(await emails('?canal=sem'), ['bia@y.com', '&lt;script&gt;x&lt;/script&gt;@evil.com']);
  assert.deepEqual(await emails('?q=x.com&acao=material_download&canal=social'), ['dani@x.com']);
  // Valores fora da lista ignorados
  assert.equal((await emails('?acao=invalida&material=nao-existe&comprovado=talvez&canal=qualquer')).length, 5);
});

test('P4: paginação de 100 em 100 em /admin/contatos mantendo os filtros', async () => {
  const s = setup();
  const list: ConvSeed[] = Array.from({ length: 101 }, (_, i) => ({
    id: `c${String(i).padStart(4, '0')}`,
    email: `u${String(i).padStart(3, '0')}@x.com`,
    action: 'newsletter_confirm',
    occurred_at: new Date(NOW - i * 60_000).toISOString(),
    origin_channel: 'direto',
  }));
  seedConversions(s.db, list);

  const p1 = await (await s.req('/admin/contatos?canal=direto')).text();
  assert.ok(p1.includes('101 encontrados, página 1 de 2.'));
  assert.equal((p1.match(/<td>u\d+@x\.com<\/td>/g) ?? []).length, 100);
  assert.ok(p1.includes('<a href="/admin/contatos?canal=direto&p=2">próxima</a>'));
  assert.ok(!p1.includes('>anterior<'));

  const p2 = await (await s.req('/admin/contatos?canal=direto&p=2')).text();
  assert.ok(p2.includes('101 encontrados, página 2 de 2.'));
  assert.equal((p2.match(/<td>u\d+@x\.com<\/td>/g) ?? []).length, 1);
  assert.ok(p2.includes('u100@x.com'));
  assert.ok(p2.includes('<a href="/admin/contatos?canal=direto">anterior</a>'));
  assert.ok(!p2.includes('>próxima<'));
});

test('P4: paginação de /admin/contatos com filtro e segunda página mantém ordem, contagem e sinal da coluna Twenty', async () => {
  const s = setup();
  const list: ConvSeed[] = [];
  // 102 pedidos pendentes no canal busca (entram no filtro)
  for (let i = 0; i < 102; i++) {
    list.push({
      id: `f${String(i).padStart(4, '0')}`,
      email: `f${String(i).padStart(3, '0')}@busca.com`,
      action: 'material_request',
      material: 'planilha-custos-ia',
      verified: 0,
      occurred_at: new Date(NOW - i * 60_000).toISOString(),
      origin_channel: 'busca',
    });
  }
  // Linhas fora do filtro (outro canal e download comprovado do email da segunda página)
  for (let i = 0; i < 15; i++) {
    list.push({
      id: `d${String(i).padStart(4, '0')}`,
      email: `outro${String(i).padStart(2, '0')}@direto.com`,
      action: 'material_request',
      material: 'planilha-custos-ia',
      verified: 0,
      occurred_at: new Date(NOW - i * 30_000).toISOString(),
      origin_channel: 'direto',
    });
  }
  list.push({
    id: 'dl-f100',
    email: 'f100@busca.com',
    action: 'material_download',
    material: 'planilha-custos-ia',
    file: 'planilha-custos-ia',
    verified: 1,
    occurred_at: new Date(NOW - 200 * 60_000).toISOString(),
    origin_channel: 'direto',
  });
  seedConversions(s.db, list);

  const rowsOf = (html: string) => {
    const tbody = html.match(/<tbody>([\s\S]*?)<\/tbody>/)?.[1] ?? '';
    return [...tbody.matchAll(/<tr>([\s\S]*?)<\/tr>/g)].map((m) =>
      [...m[1].matchAll(/<td>([\s\S]*?)<\/td>/g)].map((c) => c[1]),
    );
  };

  const p1 = await (await s.req('/admin/contatos?acao=material_request&comprovado=nao&canal=busca')).text();
  assert.ok(p1.includes('102 encontrados, página 1 de 2.'));
  const rows1 = rowsOf(p1);
  assert.equal(rows1.length, 100);
  assert.equal(rows1[0][1], 'f000@busca.com');
  assert.equal(rows1[99][1], 'f099@busca.com');
  assert.ok(!p1.includes('@direto.com'));
  assert.ok(p1.includes('<a href="/admin/contatos?acao=material_request&amp;comprovado=nao&amp;canal=busca&amp;p=2">próxima</a>') || p1.includes('<a href="/admin/contatos?acao=material_request&comprovado=nao&canal=busca&p=2">próxima</a>'));

  const p2 = await (await s.req('/admin/contatos?acao=material_request&comprovado=nao&canal=busca&p=2')).text();
  assert.ok(p2.includes('102 encontrados, página 2 de 2.'));
  const rows2 = rowsOf(p2);
  assert.deepEqual(
    rows2.map((r) => [r[1], r.at(-1)]),
    [
      ['f100@busca.com', 'email já confirmado ou no CRM: entra na próxima sincronização'],
      ['f101@busca.com', 'email não confirmado: não abriu o link do material'],
    ],
  );
  assert.ok(!p2.includes('>próxima<'));
  assert.ok(p2.includes('anterior</a>'));
});

test('P4: status da coluna Twenty em /admin/contatos e /admin cobre os cinco estados e o sinal email_no_crm_ou_comprovado', async () => {
  const s = setup();
  seedConversions(s.db, [
    // 10 mais recentes (aparecem em /admin e em /admin/contatos):
    // 1. Pedido pendente recente de email com linha comprovada já sincronizada fora das 10 do resumo -> estado 4
    {
      id: 'st01',
      email: 'sync-antigo@x.com',
      action: 'material_request',
      material: 'planilha-custos-ia',
      verified: 0,
      occurred_at: '2026-11-12T11:50:00.000Z',
      synced_at: null,
    },
    // 2. Novo pedido pendente de email que já tem pedido anterior sincronizado (verified = 0, synced_at preenchido) -> estado 4
    {
      id: 'st02',
      email: 'pedido-ja-sync@x.com',
      action: 'material_request',
      material: 'planilha-custos-ia',
      verified: 0,
      occurred_at: '2026-11-12T11:40:00.000Z',
      synced_at: null,
    },
    // 3 e 4. Dois pedidos pendentes (verified = 0, synced_at nulo) do mesmo email, sem nenhuma outra linha -> ambos no estado 5
    {
      id: 'st03',
      email: 'dois-pedidos@x.com',
      action: 'material_request',
      material: 'planilha-custos-ia',
      verified: 0,
      occurred_at: '2026-11-12T11:30:00.000Z',
      synced_at: null,
    },
    {
      id: 'st04',
      email: 'dois-pedidos@x.com',
      action: 'material_request',
      material: 'mapa-decisao-ia',
      verified: 0,
      occurred_at: '2026-11-12T11:20:00.000Z',
      synced_at: null,
    },
    // 5 e 6. Confirmação (newsletter_confirm) anterior ao pedido -> pedido no estado 4, confirmação no estado 3
    {
      id: 'st05',
      email: 'confirmou-antes@x.com',
      action: 'material_request',
      material: 'planilha-custos-ia',
      verified: 0,
      occurred_at: '2026-11-12T11:10:00.000Z',
      synced_at: null,
    },
    {
      id: 'st06',
      email: 'confirmou-antes@x.com',
      action: 'newsletter_confirm',
      verified: 1,
      occurred_at: '2026-11-12T11:00:00.000Z',
      synced_at: null,
    },
    // 7. Só pedido, nunca confirmou (mesmo com outro email parecido de mesmo prefixo e outro domínio confirmado) -> estado 5
    {
      id: 'st07',
      email: 'so-pedido@x.com',
      action: 'material_request',
      material: 'planilha-custos-ia',
      verified: 0,
      occurred_at: '2026-11-12T10:00:00.000Z',
      synced_at: null,
    },
    // 8 e 9. Pedido anterior ao download para o mesmo email, ambos pendentes -> pedido no estado 4, download no estado 3
    {
      id: 'st08',
      email: 'pediu-e-baixou@x.com',
      action: 'material_download',
      material: 'planilha-custos-ia',
      file: 'planilha-custos-ia',
      verified: 1,
      occurred_at: '2026-11-12T09:05:00.000Z',
      synced_at: null,
    },
    {
      id: 'st09',
      email: 'pediu-e-baixou@x.com',
      action: 'material_request',
      material: 'planilha-custos-ia',
      verified: 0,
      occurred_at: '2026-11-12T09:00:00.000Z',
      synced_at: null,
    },
    // 10. Download pendente isolado -> estado 3
    {
      id: 'st10',
      email: 'so-download@x.com',
      action: 'material_download',
      material: 'mapa-decisao-ia',
      file: 'mapa-decisao-pdf',
      verified: 1,
      occurred_at: '2026-11-12T08:00:00.000Z',
      synced_at: null,
    },
    // Mais antigas (fora das 10 do resumo /admin):
    // 11 e 12. Emails parecidos (mesmo prefixo, outros domínios) comprovados não mudam so-pedido@x.com
    {
      id: 'st11',
      email: 'so-pedido@outro.com',
      action: 'material_download',
      material: 'planilha-custos-ia',
      file: 'planilha-custos-ia',
      verified: 1,
      occurred_at: '2026-11-12T07:00:00.000Z',
      synced_at: null,
    },
    {
      id: 'st12',
      email: 'so-pedido@x.com.br',
      action: 'material_download',
      material: 'planilha-custos-ia',
      file: 'planilha-custos-ia',
      verified: 1,
      occurred_at: '2026-11-12T06:00:00.000Z',
      synced_at: null,
    },
    // 13. Pedido já sincronizado (verified = 0, synced_at preenchido sem id) -> estado 2, e promove st02 ao estado 4
    {
      id: 'st13',
      email: 'pedido-ja-sync@x.com',
      action: 'material_request',
      material: 'planilha-custos-ia',
      verified: 0,
      occurred_at: '2026-11-11T05:00:00.000Z',
      synced_at: '2026-11-11T05:30:00.000Z',
      twenty_person_id: null,
    },
    // 14. Linha comprovada já sincronizada (synced_at e twenty_person_id preenchidos) mais antiga do que as 10 do resumo
    {
      id: 'st14',
      email: 'sync-antigo@x.com',
      action: 'material_download',
      material: 'mapa-decisao-ia',
      file: 'mapa-decisao-pdf',
      verified: 1,
      occurred_at: '2026-10-01T09:00:00.000Z',
      synced_at: '2026-10-01T09:30:00.000Z',
      twenty_person_id: 'old-person-123',
    },
  ]);

  const rowsOf = (html: string) => {
    const tbody = html.match(/<tbody>([\s\S]*?)<\/tbody>/)?.[1] ?? '';
    return [...tbody.matchAll(/<tr>([\s\S]*?)<\/tr>/g)].map((m) =>
      [...m[1].matchAll(/<td>([\s\S]*?)<\/td>/g)].map((c) => c[1]),
    );
  };

  for (const path of ['/admin/contatos', '/admin']) {
    const html = await (await s.req(path)).text();
    const rows = rowsOf(html);
    if (path === '/admin') {
      assert.equal(rows.length, 10);
      assert.ok(!html.includes('old-person-123'), 'linha sincronizada antiga fica fora das 10 do resumo');
    }
    const cellsFor = (email: string, actionText: string) => {
      const matched = rows.filter((r) => r[1] === email && r[2] === actionText);
      assert.ok(matched.length > 0, `${path}: linha ausente para ${email} (${actionText})`);
      return matched.map((r) => r[r.length - 1]);
    };
    assert.deepEqual(cellsFor('sync-antigo@x.com', 'pedido de material'), ['email já confirmado ou no CRM: entra na próxima sincronização'], path);
    assert.deepEqual(cellsFor('pedido-ja-sync@x.com', 'pedido de material').slice(0, 1), ['email já confirmado ou no CRM: entra na próxima sincronização'], path);
    assert.deepEqual(
      cellsFor('dois-pedidos@x.com', 'pedido de material'),
      ['email não confirmado: não abriu o link do material', 'email não confirmado: não abriu o link do material'],
      path,
    );
    assert.deepEqual(cellsFor('confirmou-antes@x.com', 'pedido de material'), ['email já confirmado ou no CRM: entra na próxima sincronização'], path);
    assert.deepEqual(cellsFor('confirmou-antes@x.com', 'inscrição nos Expert Insights'), ['email confirmado: entra no CRM na próxima sincronização'], path);
    assert.deepEqual(cellsFor('so-pedido@x.com', 'pedido de material'), ['email não confirmado: não abriu o link do material'], path);
    assert.deepEqual(cellsFor('pediu-e-baixou@x.com', 'pedido de material'), ['email já confirmado ou no CRM: entra na próxima sincronização'], path);
    assert.deepEqual(cellsFor('pediu-e-baixou@x.com', 'download'), ['email confirmado: entra no CRM na próxima sincronização'], path);
    assert.deepEqual(cellsFor('so-download@x.com', 'download'), ['email confirmado: entra no CRM na próxima sincronização'], path);
  }

  // Em /admin/contatos aparecem também as linhas mais antigas sincronizadas (estados 1 e 2)
  const todosHtml = await (await s.req('/admin/contatos')).text();
  const todosRows = rowsOf(todosHtml);
  assert.deepEqual(
    todosRows.filter((r) => r[1] === 'pedido-ja-sync@x.com').map((r) => r.at(-1)),
    ['email já confirmado ou no CRM: entra na próxima sincronização', 'sincronizada, sem link'],
  );
  assert.equal(
    todosRows.find((r) => r[1] === 'sync-antigo@x.com' && r[2] === 'download')?.at(-1),
    '<a href="https://crm.guifav.dev/object/person/old-person-123" target="_blank" rel="noopener noreferrer">abrir no Twenty</a>',
  );

  // Mesmo filtrando só linhas não comprovadas, pedidos de emails já confirmados ou no CRM continuam no estado 4
  const filtrado = await (await s.req('/admin/contatos?comprovado=nao')).text();
  const rowsFiltrado = rowsOf(filtrado);
  assert.equal(rowsFiltrado.length, 8);
  assert.equal(rowsFiltrado.find((r) => r[1] === 'sync-antigo@x.com')?.at(-1), 'email já confirmado ou no CRM: entra na próxima sincronização');
  assert.deepEqual(
    rowsFiltrado.filter((r) => r[1] === 'pedido-ja-sync@x.com').map((r) => r.at(-1)),
    ['email já confirmado ou no CRM: entra na próxima sincronização', 'sincronizada, sem link'],
  );
  assert.deepEqual(
    rowsFiltrado.filter((r) => r[1] === 'dois-pedidos@x.com').map((r) => r.at(-1)),
    ['email não confirmado: não abriu o link do material', 'email não confirmado: não abriu o link do material'],
  );
  assert.equal(rowsFiltrado.find((r) => r[1] === 'confirmou-antes@x.com')?.at(-1), 'email já confirmado ou no CRM: entra na próxima sincronização');
  assert.equal(rowsFiltrado.find((r) => r[1] === 'so-pedido@x.com')?.at(-1), 'email não confirmado: não abriu o link do material');
  assert.equal(rowsFiltrado.find((r) => r[1] === 'pediu-e-baixou@x.com')?.at(-1), 'email já confirmado ou no CRM: entra na próxima sincronização');
});

test('P4: funções isoladas recusam twenty_person_id e landing_path fora do padrão e cobrem os 5 estados de twentyCell', () => {
  assert.equal(twentyPersonUrl('abc-123', 'https://crm.guifav.dev'), 'https://crm.guifav.dev/object/person/abc-123');
  // Estado 1: twenty_person_id válido vence sempre
  assert.equal(
    twentyCell('abc-123', '2026-11-12T10:00:00Z', 1, 1, 'https://crm.guifav.dev'),
    '<a href="https://crm.guifav.dev/object/person/abc-123" target="_blank" rel="noopener noreferrer">abrir no Twenty</a>',
  );
  assert.equal(
    twentyCell('abc-123', null, 0, 0, 'https://crm.guifav.dev'),
    '<a href="https://crm.guifav.dev/object/person/abc-123" target="_blank" rel="noopener noreferrer">abrir no Twenty</a>',
  );
  // Estado 2: synced_at preenchido sem id válido
  for (const bad of ['', null, 'a b', 'a/b', '../x', 'a'.repeat(65), '<script>', 'id?x=1']) {
    assert.equal(twentyPersonUrl(bad, 'https://crm.guifav.dev'), null, String(bad));
    const cel = twentyCell(bad, '2026-11-12T10:00:00Z', 1, 1, 'https://crm.guifav.dev');
    assert.equal(cel, 'sincronizada, sem link', String(bad));
  }
  assert.equal(twentyCell(null, '2026-11-12T10:00:00Z', 0, 0, 'https://crm.guifav.dev'), 'sincronizada, sem link');
  // Estado 3: synced_at nulo e verified = 1
  assert.equal(twentyCell(null, null, 1, 1, 'https://crm.guifav.dev'), 'email confirmado: entra no CRM na próxima sincronização');
  assert.equal(twentyCell(null, null, 1, 0, 'https://crm.guifav.dev'), 'email confirmado: entra no CRM na próxima sincronização');
  // Estado 4: synced_at nulo, verified = 0 e email comprovado ou já sincronizado em outra linha
  assert.equal(twentyCell(null, null, 0, 1, 'https://crm.guifav.dev'), 'email já confirmado ou no CRM: entra na próxima sincronização');
  assert.equal(twentyCell(null, null, 0, true, 'https://crm.guifav.dev'), 'email já confirmado ou no CRM: entra na próxima sincronização');
  // Estado 5: synced_at nulo, verified = 0 e email sem outra linha comprovada nem sincronizada
  assert.equal(twentyCell(null, null, 0, 0, 'https://crm.guifav.dev'), 'email não confirmado: não abriu o link do material');
  assert.equal(twentyCell(null, null, 0, false, 'https://crm.guifav.dev'), 'email não confirmado: não abriu o link do material');

  assert.equal(landingCell('/blog/ok/', 'https://guifav.dev'), '<a href="https://guifav.dev/blog/ok/">/blog/ok/</a>');
  for (const badPath of ['javascript:alert(1)', '//evil.com/x', '/blog/a?q=1', '/blog/<script>']) {
    const out = landingCell(badPath, 'https://guifav.dev');
    assert.ok(!out.includes('<a '), badPath);
  }
});

// ------------------------------------------------------------------ Conteúdos (/admin/conteudos)
test('P5: conteúdos separa pedidos e downloads nas 3 janelas, mostra fora do catálogo, páginas de entrada e catálogo de posts', async () => {
  const s = setup();
  const iso = (deltaMs: number) => new Date(NOW + deltaMs).toISOString();

  // Sem catálogo ainda
  const vazio = await (await s.req('/admin/conteudos')).text();
  assert.ok(vazio.includes('O catálogo ainda não foi coletado.'));

  seedCatalog(
    s.db,
    [
      { path: '/blog/post-recente/', title: 'Post Recente', published_on: '2026-11-10' },
      { path: '/blog/post-antigo/', title: 'Post Antigo', published_on: '2026-10-01' },
    ],
    {
      last_attempt_at: '2026-11-12T11:17:00.000Z',
      last_success_at: '2026-11-12T10:17:00.000Z',
      last_error: 'tempo esgotado',
      item_count: 2,
    },
  );

  seedConversions(s.db, [
    // Planilha: 1 pedido em 7d, 1 em 20d, 1 em 45d (total 3); 1 download em 3d (total 1)
    { id: 'm1', email: 'a@x.com', action: 'material_request', material: 'planilha-custos-ia', occurred_at: iso(-2 * DAY_MS), landing_path: '/blog/post-recente/' },
    { id: 'm2', email: 'b@x.com', action: 'material_request', material: 'planilha-custos-ia', occurred_at: iso(-20 * DAY_MS), landing_path: '/blog/post-recente/' },
    { id: 'm3', email: 'c@x.com', action: 'material_request', material: 'planilha-custos-ia', occurred_at: iso(-45 * DAY_MS), landing_path: '/ferramentas/planilha-custos-ia/' },
    { id: 'm4', email: 'a@x.com', action: 'material_download', material: 'planilha-custos-ia', file: 'planilha-custos-ia', occurred_at: iso(-3 * DAY_MS), landing_path: '/blog/post-recente/' },
    // Mapa: 1 download de arquivo conhecido + 1 download de arquivo fora do catálogo
    { id: 'm5', email: 'd@x.com', action: 'material_download', material: 'mapa-decisao-ia', file: 'mapa-decisao-pdf', occurred_at: iso(-1 * DAY_MS), landing_path: '/sobre/' },
    { id: 'm6', email: 'e@x.com', action: 'material_download', material: 'mapa-decisao-ia', file: 'anexo-velho', occurred_at: iso(-10 * DAY_MS), landing_path: null },
    // Material fora do catálogo: 1 pedido e 1 download
    { id: 'm7', email: 'f@x.com', action: 'material_request', material: 'ebook-antigo', occurred_at: iso(-5 * DAY_MS), landing_path: null },
    { id: 'm8', email: 'f@x.com', action: 'material_download', material: 'ebook-antigo', file: 'ebook-antigo-pdf', occurred_at: iso(-5 * DAY_MS), landing_path: '/blog/post-recente/' },
    // Newsletter confirm num post
    { id: 'm9', email: 'g@x.com', action: 'newsletter_confirm', occurred_at: iso(-4 * DAY_MS), landing_path: '/blog/post-recente/' },
    // Pedido com material nulo: não pode sumir da visão de materiais
    { id: 'm10', email: 'h@x.com', action: 'material_request', material: null, occurred_at: iso(-2 * DAY_MS), landing_path: null },
  ]);

  const html = await (await s.req('/admin/conteudos')).text();
  assert.ok(html.includes('<p><a href="/admin">Resumo</a> · <a href="/admin/assinantes">Assinantes</a> · <a href="/admin/rascunhos">Rascunhos</a> · <a href="/admin/contatos">Contatos</a> · Conteúdos</p>'));
  // Pedidos e downloads em colunas separadas
  assert.ok(
    html.includes(
      '<tr><td><a href="https://guifav.dev/ferramentas/planilha-custos-ia/">Planilha de custos de IA em produção</a></td><td>1</td><td>2</td><td>3</td><td></td><td></td><td></td></tr>',
    ),
  );
  assert.ok(
    html.includes('<tr class="sub"><td>planilha-custos-ia.xlsx</td><td></td><td></td><td></td><td>1</td><td>1</td><td>1</td></tr>'),
  );
  // Fora do catálogo (arquivo de material conhecido, material desconhecido e material nulo)
  assert.ok(html.includes('<tr class="sub"><td>anexo-velho (fora do catálogo)</td><td></td><td></td><td></td><td>0</td><td>1</td><td>1</td></tr>'));
  assert.ok(html.includes('<tr><td>(sem material)</td><td>1</td><td>1</td><td>1</td><td></td><td></td><td></td></tr>'));
  assert.ok(html.includes('<tr><td>ebook-antigo (fora do catálogo)</td><td>1</td><td>1</td><td>1</td><td></td><td></td><td></td></tr>'));
  assert.ok(html.includes('<tr class="sub"><td>ebook-antigo-pdf (fora do catálogo)</td><td></td><td></td><td></td><td>1</td><td>1</td><td>1</td></tr>'));

  // Páginas de entrada: título do catálogo, título do MATERIAIS, caminho cru, e nulo
  assert.ok(html.includes('<th>Inscrições (30d)</th><th>Inscrições (total)</th>'));
  assert.ok(html.includes('<a href="https://guifav.dev/blog/post-recente/">Post Recente</a>'));
  assert.ok(html.includes('<a href="https://guifav.dev/ferramentas/planilha-custos-ia/">Planilha de custos de IA em produção</a>'));
  assert.ok(html.includes('<a href="https://guifav.dev/sobre/">/sobre/</a>'));
  assert.ok(html.includes('sem página de entrada registrada'));
  // Ordem das páginas de entrada: /blog/post-recente/ tem 5 conversões e aparece antes de null (3) e das outras (1)
  const idxRecente = html.indexOf('<h2>Páginas de entrada</h2>');
  const secEntrada = html.slice(idxRecente, html.indexOf('<h2>Posts do site</h2>'));
  assert.ok(secEntrada.indexOf('Post Recente') < secEntrada.indexOf('sem página de entrada registrada'));

  // Posts do site + estado do coletor (pedidos, downloads e inscrições em colunas separadas)
  assert.ok(html.includes('Catálogo de 2026-11-12 10:17 UTC, 2 posts.'));
  assert.ok(html.includes('A última coleta falhou em 2026-11-12 11:17 UTC: tempo esgotado'));
  const secPosts = html.slice(html.indexOf('<h2>Posts do site</h2>'));
  assert.ok(secPosts.includes('<th>Post</th><th>Data</th><th>Pedidos</th><th>Downloads</th><th>Inscrições</th>'));
  assert.ok(secPosts.indexOf('Post Recente') < secPosts.indexOf('Post Antigo'));
  assert.match(secPosts, /Post Recente<\/a><\/td>\s*<td>2026-11-10<\/td>\s*<td>2<\/td>\s*<td>2<\/td>\s*<td>1<\/td>/);
  assert.match(secPosts, /Post Antigo<\/a><\/td>\s*<td>2026-10-01<\/td>\s*<td>0<\/td>\s*<td>0<\/td>\s*<td>0<\/td>/);
});

test('P5: lista de posts em /admin/conteudos pagina de 100 em 100', async () => {
  const s = setup();
  const posts = Array.from({ length: 101 }, (_, i) => ({
    path: `/blog/post-${String(i).padStart(3, '0')}/`,
    title: `Post ${String(i).padStart(3, '0')}`,
    published_on: `2026-01-${String((i % 28) + 1).padStart(2, '0')}`,
  }));
  seedCatalog(s.db, posts, {
    last_attempt_at: '2026-11-12T10:00:00.000Z',
    last_success_at: '2026-11-12T10:00:00.000Z',
    item_count: 101,
  });
  const p1 = await (await s.req('/admin/conteudos')).text();
  assert.ok(p1.includes('101 posts, página 1 de 2.'));
  assert.ok(p1.includes('<a href="/admin/conteudos?p=2">próxima</a>'));
  const p2 = await (await s.req('/admin/conteudos?p=2')).text();
  assert.ok(p2.includes('101 posts, página 2 de 2.'));
  assert.ok(p2.includes('<a href="/admin/conteudos">anterior</a>'));
});

// ------------------------------------------------------------------ Coleta do catálogo
test('P6: coleta bem-sucedida grava todos os posts em lotes <= 100 parâmetros e atualiza collectors', async () => {
  const posts = Array.from({ length: 75 }, (_, i) => ({
    caminho: `/blog/artigo-${String(i).padStart(3, '0')}/`,
    titulo: `Artigo ${i}`,
    data: '2026-11-10',
  }));
  const s = setup({
    onFetch: (url, init) => {
      assert.equal(url, 'https://guifav.dev/blog/catalogo.json');
      assert.equal(init?.redirect, 'manual');
      return new Response(JSON.stringify({ posts }), { status: 200, headers: { 'content-type': 'application/json; charset=utf-8' } });
    },
  });
  seedCatalog(s.db, [{ path: '/blog/antigo/', title: 'Antigo', published_on: '2025-01-01' }]);

  const res = await coletarCatalogo(s.env, s.deps);
  assert.deepEqual(res, { ok: true, count: 75 });
  assert.ok(s.db.maxParams <= 100, `máximo de parâmetros por instrução: ${s.db.maxParams}`);
  const count = (s.db.raw.prepare('SELECT COUNT(*) AS n FROM content_catalog').get() as { n: number }).n;
  assert.equal(count, 75);
  assert.equal(s.db.raw.prepare("SELECT path FROM content_catalog WHERE path = '/blog/antigo/'").get(), undefined);
  const col = plain(s.db.raw.prepare("SELECT * FROM collectors WHERE source = 'catalogo_posts'").get());
  assert.deepEqual(col, {
    source: 'catalogo_posts',
    last_attempt_at: new Date(NOW).toISOString(),
    last_success_at: new Date(NOW).toISOString(),
    last_error: null,
    item_count: 75,
  });
});

test('P6: falhas na coleta (rede, 302, 404, content-type, prazo, corpo > 2 MiB, JSON, lista vazia, item inválido, duplicado, data inexistente) preservam catálogo anterior', { timeout: 5000 }, async () => {
  let cancelouGrande = false;
  const casos: [string, (url: string, init?: RequestInit) => Promise<Response> | Response, RegExp][] = [
    ['rede', () => { throw new TypeError('network down'); }, /falha de rede/],
    ['302', (_url, init) => {
      assert.equal(init?.redirect, 'manual');
      return new Response('secreto-nao-gravar', { status: 302, headers: { location: 'https://guifav.dev/outro' } });
    }, /site respondeu 302/],
    ['404', () => new Response('secreto-nao-gravar', { status: 404, headers: { 'content-type': 'application/json' } }), /site respondeu 404/],
    ['content-type html', () => new Response('<html>erro</html>', { status: 200, headers: { 'content-type': 'text/html' } }), /content-type JSON/],
    ['prazo', (_url, init) => {
      assert.ok(init?.signal, 'requisição com AbortSignal');
      segurarAteAbortar(init!.signal!);
      return new Promise<Response>((_, rej) => init!.signal!.addEventListener('abort', () => rej(new DOMException('prazo', 'TimeoutError'))));
    }, /tempo esgotado/],
    ['corpo > 2 MiB', () => {
      const chunk = new Uint8Array(65536).fill(97);
      let enviados = 0;
      const body = new ReadableStream<Uint8Array>({
        pull(c) {
          enviados += chunk.byteLength;
          c.enqueue(chunk);
          if (enviados > CATALOG_MAX_BYTES * 2) c.close();
        },
        cancel() {
          cancelouGrande = true;
        },
      });
      return new Response(body, { status: 200, headers: { 'content-type': 'application/json' } });
    }, /acima de 2 MiB/],
    ['JSON inválido', () => new Response('{quebrado', { status: 200, headers: { 'content-type': 'application/json' } }), /JSON inválido/],
    ['lista vazia', () => Response.json({ posts: [] }), /catálogo vazio/],
    ['caminho inválido', () => Response.json({ posts: [{ caminho: '/outro/x/', titulo: 'A', data: '2026-11-10' }] }), /caminho inválido/],
    ['slug começando com hífen', () => Response.json({ posts: [{ caminho: '/blog/-a/', titulo: 'A', data: '2026-11-10' }] }), /caminho inválido/],
    ['slug com maiúscula', () => Response.json({ posts: [{ caminho: '/blog/Post/', titulo: 'A', data: '2026-11-10' }] }), /caminho inválido/],
    ['slug com sublinhado', () => Response.json({ posts: [{ caminho: '/blog/a_b/', titulo: 'A', data: '2026-11-10' }] }), /caminho inválido/],
    ['título com controle', () => Response.json({ posts: [{ caminho: '/blog/ok/', titulo: 'A\nB', data: '2026-11-10' }] }), /título inválido/],
    ['título vazio', () => Response.json({ posts: [{ caminho: '/blog/ok/', titulo: '', data: '2026-11-10' }] }), /título inválido/],
    ['caminho repetido', () => Response.json({
      posts: [
        { caminho: '/blog/dup/', titulo: 'A', data: '2026-11-10' },
        { caminho: '/blog/dup/', titulo: 'B', data: '2026-11-11' },
      ],
    }), /caminho repetido/],
    ['data inexistente 2026-02-30', () => Response.json({ posts: [{ caminho: '/blog/ok/', titulo: 'A', data: '2026-02-30' }] }), /data inválida/],
    ['data inexistente 2026-02-29', () => Response.json({ posts: [{ caminho: '/blog/ok/', titulo: 'A', data: '2026-02-29' }] }), /data inválida/],
  ];

  const origErr = console.error;
  console.error = () => {};
  try {
    for (const [nome, onFetch, erroRe] of casos) {
      const s = setup({ onFetch });
      seedCatalog(s.db, [{ path: '/blog/previo/', title: 'Prévio', published_on: '2026-10-01' }], {
        last_attempt_at: '2026-11-11T10:00:00.000Z',
        last_success_at: '2026-11-11T10:00:00.000Z',
        item_count: 1,
      });
      const res = await coletarCatalogo(s.env, s.deps);
      assert.equal(res.ok, false, nome);
      assert.match(res.error, erroRe, nome);
      assert.ok(!res.error.includes('secreto-nao-gravar'), nome);
      // Catálogo anterior intacto
      const rows = plain(s.db.raw.prepare('SELECT path, title FROM content_catalog').all());
      assert.deepEqual(rows, [{ path: '/blog/previo/', title: 'Prévio' }], nome);
      // Collectors mantém last_success_at e item_count anteriores e grava last_attempt_at + last_error
      const col = plain(s.db.raw.prepare("SELECT * FROM collectors WHERE source = 'catalogo_posts'").get()) as Record<string, unknown>;
      assert.equal(col.last_attempt_at, new Date(NOW).toISOString(), nome);
      assert.equal(col.last_success_at, '2026-11-11T10:00:00.000Z', nome);
      assert.equal(col.item_count, 1, nome);
      assert.match(String(col.last_error), erroRe, nome);
    }
  } finally {
    console.error = origErr;
  }
  assert.equal(cancelouGrande, true, 'stream acima de 2 MiB foi cancelado');
  assert.equal(dataExiste('2024-02-29'), true, 'ano bissexto aceito');
  assert.equal(dataExiste('2026-04-31'), false);
  assert.throws(() => validarCatalogo({ posts: Array.from({ length: 5001 }, (_, i) => ({ caminho: `/blog/p-${i}/`, titulo: 'T', data: '2026-01-01' })) }), /5000/);
});

test('P6: padrão do slug alinha com check_site.py (a--b e a- aceitos; -a, maiúscula e _ recusados no coletor e no CHECK)', async () => {
  const posts = [
    { caminho: '/blog/a--b/', titulo: 'Hífen duplo', data: '2026-11-10' },
    { caminho: '/blog/a-/', titulo: 'Termina com hífen', data: '2026-11-09' },
  ];
  const s = setup({ onFetch: () => Response.json({ posts }) });
  const res = await coletarCatalogo(s.env, s.deps);
  assert.deepEqual(res, { ok: true, count: 2 });
  const saved = plain(s.db.raw.prepare('SELECT path FROM content_catalog ORDER BY path').all());
  assert.deepEqual(saved, [{ path: '/blog/a--b/' }, { path: '/blog/a-/' }]);

  const ins = s.db.raw.prepare("INSERT INTO content_catalog (path, title, published_on) VALUES (?, 'T', '2026-11-10')");
  for (const bad of ['/blog/-a/', '/blog/Post/', '/blog/a_b/']) {
    assert.throws(() => validarCatalogo({ posts: [{ caminho: bad, titulo: 'T', data: '2026-11-10' }] }), /caminho inválido/, bad);
    assert.throws(() => ins.run(bad), /CHECK/, bad);
  }
});

test('P6: ida e volta entre catalogoPosts do build do blog e coletarCatalogo do Worker', async () => {
  const origem = [
    { id: 'primeiro-post', data: { title: 'Primeiro post', date: new Date('2026-11-24T23:30:00-03:00') } },
    { id: 'a--b', data: { title: 'Segundo post', date: new Date('2026-11-12T12:00:00Z') } },
    { id: 'a-', data: { title: 'Terceiro post', date: new Date('2026-11-10T12:00:00Z') } },
  ];
  const gerado = catalogoPosts(origem, '/blog');
  const s = setup({
    onFetch: () => new Response(JSON.stringify(gerado), { status: 200, headers: { 'content-type': 'application/json; charset=utf-8' } }),
  });
  const res = await coletarCatalogo(s.env, s.deps);
  assert.deepEqual(res, { ok: true, count: 3 });
  const rows = plain(s.db.raw.prepare('SELECT path, title, published_on FROM content_catalog ORDER BY published_on DESC, path ASC').all());
  assert.deepEqual(
    rows,
    gerado.posts.map((p) => ({ path: p.caminho, title: p.titulo, published_on: p.data })),
  );
});

test('P6: DB.batch lançando depois do prazo de busca registra falha ao gravar e não tempo esgotado', { timeout: 5000 }, async () => {
  let signalUsado: AbortSignal | undefined;
  const s = setup({
    onFetch: (_url, init) => {
      signalUsado = init?.signal ?? undefined;
      return Response.json({ posts: [{ caminho: '/blog/ok/', titulo: 'Ok', data: '2026-11-10' }] });
    },
  });
  s.deps.catalogTimeoutMs = 25;
  s.env.DB.batch = async () => {
    await new Promise((r) => setTimeout(r, 60));
    throw new Error('D1 lento falhou');
  };
  const origErr = console.error;
  console.error = () => {};
  try {
    const res = await coletarCatalogo(s.env, s.deps);
    assert.equal(signalUsado?.aborted, false, 'prazo de busca foi desarmado antes de gravar');
    assert.equal(res.ok, false);
    assert.match(res.error, /^falha ao gravar/);
    const col = plain(s.db.raw.prepare("SELECT last_error FROM collectors WHERE source = 'catalogo_posts'").get()) as { last_error: string };
    assert.match(col.last_error, /^falha ao gravar/);
  } finally {
    console.error = origErr;
  }
});

test('P6: falha da coleta escreve console.error sem corpo; falha dupla em collectors gera segundo log e não lança', async () => {
  const logs: string[] = [];
  const origErr = console.error;
  console.error = (...args: unknown[]) => {
    logs.push(args.map(String).join(' '));
  };
  try {
    const s1 = setup({
      onFetch: () => new Response('corpo-secreto-nao-logar', { status: 503, headers: { 'content-type': 'application/json' } }),
    });
    const r1 = await coletarCatalogo(s1.env, s1.deps);
    assert.deepEqual(r1, { ok: false, error: 'site respondeu 503' });
    assert.equal(logs.length, 1);
    assert.match(logs[0], /site respondeu 503/);
    assert.ok(!logs[0].includes('corpo-secreto-nao-logar'));

    logs.length = 0;
    const s2 = setup({
      onFetch: () => new Response('corpo-secreto-nao-logar', { status: 502, headers: { 'content-type': 'application/json' } }),
    });
    const origPrepare = s2.env.DB.prepare;
    s2.env.DB.prepare = (sql: string) => {
      if (/INSERT INTO collectors/.test(sql)) {
        const st: D1Stmt = {
          bind: () => st,
          run: async () => { throw new Error('D1 indisponível para gui@exemplo.com'); },
          all: async () => ({ results: [] }),
          first: async () => null,
        };
        return st;
      }
      return origPrepare(sql);
    };
    const r2 = await coletarCatalogo(s2.env, s2.deps);
    assert.deepEqual(r2, { ok: false, error: 'site respondeu 502' });
    assert.equal(logs.length, 2);
    assert.match(logs[0], /site respondeu 502/);
    assert.ok(!logs[0].includes('corpo-secreto-nao-logar'));
    assert.match(logs[1], /não gravado/);
    assert.ok(!logs[1].includes('gui@exemplo.com'));
    assert.ok(logs[1].includes('<email>'));
  } finally {
    console.error = origErr;
  }
});

test('P6: falha do D1 no meio do batch faz rollback e mantém o catálogo anterior inteiro', async () => {
  const posts = Array.from({ length: 65 }, (_, i) => ({
    caminho: `/blog/novo-${String(i).padStart(3, '0')}/`,
    titulo: `Novo ${i}`,
    data: '2026-11-12',
  }));
  const s = setup({
    failInsertCatalogAt: 2, // 1º lote de INSERT passa, 2º falha dentro da transação
    onFetch: () => Response.json({ posts }),
  });
  seedCatalog(
    s.db,
    [
      { path: '/blog/velho-1/', title: 'Velho 1', published_on: '2026-01-01' },
      { path: '/blog/velho-2/', title: 'Velho 2', published_on: '2026-01-02' },
    ],
    { last_attempt_at: '2026-11-11T10:00:00.000Z', last_success_at: '2026-11-11T10:00:00.000Z', item_count: 2 },
  );

  const origErr = console.error;
  console.error = () => {};
  try {
    const res = await coletarCatalogo(s.env, s.deps);
    assert.equal(res.ok, false);
  } finally {
    console.error = origErr;
  }
  const rows = plain(s.db.raw.prepare('SELECT path FROM content_catalog ORDER BY path').all());
  assert.deepEqual(rows, [{ path: '/blog/velho-1/' }, { path: '/blog/velho-2/' }]);
  const col = plain(s.db.raw.prepare("SELECT * FROM collectors WHERE source = 'catalogo_posts'").get()) as Record<string, unknown>;
  assert.equal(col.last_success_at, '2026-11-11T10:00:00.000Z');
  assert.equal(col.item_count, 2);
  assert.ok(typeof col.last_error === 'string' && col.last_error.length > 0);
});

// ------------------------------------------------------------------ Heating em /admin/contatos (épico #197)

function seedHeating(
  db: FakeD1,
  items: {
    context?: string;
    email: string;
    score: number;
    band: 'frio' | 'morno' | 'quente';
    signals?: unknown;
    computed_at?: string;
    twenty_person_id?: string | null;
  }[],
) {
  const ins = db.raw.prepare(
    `INSERT INTO heating (context, email, score, band, signals, computed_at, twenty_person_id)
     VALUES (?, ?, ?, ?, ?, ?, ?)`,
  );
  for (const r of items) {
    ins.run(
      r.context ?? 'site',
      r.email,
      r.score,
      r.band,
      typeof r.signals === 'string' ? r.signals : JSON.stringify(r.signals ?? []),
      r.computed_at ?? '2026-11-12T10:53:00.000Z',
      r.twenty_person_id ?? null,
    );
  }
}

test('P7: /admin/contatos mostra coluna Heating do contexto site (ou "sem nota") e alterna para ?ordem=heating', async () => {
  const s = setup();
  seedConversions(s.db, [
    { id: 'h1', email: 'ana@x.com', action: 'material_download', material: 'planilha-custos-ia', occurred_at: '2026-11-12T10:00:00.000Z' },
    { id: 'h2', email: 'bia@x.com', action: 'newsletter_confirm', occurred_at: '2026-11-12T09:00:00.000Z' },
  ]);
  seedHeating(s.db, [
    {
      context: 'site',
      email: 'ana@x.com',
      score: 48,
      band: 'quente',
      signals: [{ sinal: 'material_baixado', rotulo: 'Download de material', pontos: 12 }],
      twenty_person_id: 'p-ana-1',
    },
    // Nota só de curso para bia@x.com não aparece na coluna Heating (contexto site) da lista de conversões.
    {
      context: 'curso:chatgpt-no-dia-a-dia',
      email: 'bia@x.com',
      score: 30,
      band: 'morno',
      signals: [{ sinal: 'curso_cadastro', rotulo: 'Cadastro no curso', pontos: 10 }],
    },
  ]);

  const html = await (await s.req('/admin/contatos')).text();
  assert.ok(html.includes('Por conversão · <a href="/admin/contatos?ordem=heating">Por heating</a>'));
  assert.ok(html.includes('<th>Heating</th><th>Twenty</th>'));
  const tbody = html.match(/<tbody>([\s\S]*?)<\/tbody>/)?.[1] ?? '';
  const rows = [...tbody.matchAll(/<tr>([\s\S]*?)<\/tr>/g)].map((m) =>
    [...m[1].matchAll(/<td>([\s\S]*?)<\/td>/g)].map((c) => c[1]),
  );
  assert.equal(rows.length, 2);
  assert.equal(rows[0][1], 'ana@x.com');
  assert.equal(rows[0][rows[0].length - 2], '48 (quente)');
  assert.equal(rows[1][1], 'bia@x.com');
  assert.equal(rows[1][rows[1].length - 2], 'sem nota');
});

test('P7: /admin/contatos?ordem=heating ordena por nota DESC e email, filtra contexto e faixa, escapa HTML e mostra link do Twenty', async () => {
  const s = setup();
  seedHeating(s.db, [
    {
      context: 'site',
      email: 'quente-b@x.com',
      score: 50,
      band: 'quente',
      signals: [
        { sinal: 'site_agende', rotulo: 'Clique em Agende', pontos: 50 },
        { sinal: 'material_baixado', rotulo: 'Download de material', pontos: 11.2 },
      ],
      computed_at: '2026-11-12T10:53:00.000Z',
      twenty_person_id: 'pid-quente-b',
    },
    {
      context: 'site',
      email: 'quente-a@x.com',
      score: 50,
      band: 'quente',
      signals: [{ sinal: 'site_contato', rotulo: 'Página de contato', pontos: 50 }],
      computed_at: '2026-11-12T10:53:00.000Z',
      twenty_person_id: null,
    },
    {
      context: 'site',
      email: '<script>x</script>@evil.com',
      score: 18,
      band: 'morno',
      signals: [{ sinal: 'material_baixado', rotulo: '<b>Malicioso</b>', pontos: 18.0 }],
      computed_at: '2026-11-12T10:53:00.000Z',
      twenty_person_id: null,
    },
    {
      context: 'curso:chatgpt-no-dia-a-dia',
      email: 'aluna@x.com',
      score: 25,
      band: 'morno',
      signals: [
        { sinal: 'curso_cadastro', rotulo: 'Cadastro no curso', pontos: 10 },
        { sinal: 'curso_aula', rotulo: 'Aula concluída no curso', pontos: 15 },
      ],
      computed_at: '2026-11-12T10:53:00.000Z',
      twenty_person_id: 'pid-aluna',
    },
    {
      context: 'curso:outro-curso',
      email: 'outro@x.com',
      score: 10,
      band: 'frio',
      signals: [{ sinal: 'curso_cadastro', rotulo: 'Cadastro no curso', pontos: 10 }],
      computed_at: '2026-11-12T10:53:00.000Z',
      twenty_person_id: null,
    },
  ]);

  const rowsOf = (html: string) => {
    const tbody = html.match(/<tbody>([\s\S]*?)<\/tbody>/)?.[1] ?? '';
    return [...tbody.matchAll(/<tr>([\s\S]*?)<\/tr>/g)].map((m) =>
      [...m[1].matchAll(/<td>([\s\S]*?)<\/td>/g)].map((c) => c[1]),
    );
  };

  // Visão padrão (contexto = site)
  const res = await s.req('/admin/contatos?ordem=heating');
  assert.equal(res.status, 200);
  const html = await res.text();
  assert.ok(html.includes('<a href="/admin/contatos">Por conversão</a> · Por heating'));
  assert.ok(html.includes('<option value="site" selected>Site</option>'));
  assert.ok(html.includes('<option value="curso:chatgpt-no-dia-a-dia">ChatGPT no dia a dia</option>'));
  assert.ok(html.includes('<option value="curso:outro-curso">curso:outro-curso</option>'));
  assert.ok(!html.includes('<script>x</script>'));
  assert.ok(!html.includes('<b>Malicioso</b>'));

  const rows = rowsOf(html);
  assert.equal(rows.length, 3);
  // Empate de nota (50): desempate por email ASC (quente-a antes de quente-b)
  assert.deepEqual(
    rows.map((r) => [r[0], r[1], r[2]]),
    [
      ['50', 'quente', 'quente-a@x.com'],
      ['50', 'quente', 'quente-b@x.com'],
      ['18', 'morno', '&lt;script&gt;x&lt;/script&gt;@evil.com'],
    ],
  );
  assert.equal(rows[0][5], 'sem Pessoa no CRM');
  assert.equal(rows[1][3], 'Clique em Agende (50,0); Download de material (11,2)');
  assert.equal(rows[1][4], '2026-11-12 10:53');
  assert.equal(
    rows[1][5],
    '<a href="https://crm.guifav.dev/object/person/pid-quente-b" target="_blank" rel="noopener noreferrer">abrir no Twenty</a>',
  );
  assert.equal(rows[2][3], '&lt;b&gt;Malicioso&lt;/b&gt; (18,0)');

  // Filtro por faixa
  const htmlQuente = await (await s.req('/admin/contatos?ordem=heating&faixa=quente')).text();
  assert.equal(rowsOf(htmlQuente).length, 2);
  const htmlFrio = await (await s.req('/admin/contatos?ordem=heating&faixa=frio')).text();
  assert.equal(rowsOf(htmlFrio).length, 0);

  // Filtro por contexto de curso
  const htmlCurso = await (await s.req('/admin/contatos?ordem=heating&contexto=curso:chatgpt-no-dia-a-dia')).text();
  const rowsCurso = rowsOf(htmlCurso);
  assert.equal(rowsCurso.length, 1);
  assert.equal(rowsCurso[0][2], 'aluna@x.com');
  assert.equal(rowsCurso[0][3], 'Cadastro no curso (10,0); Aula concluída no curso (15,0)');

  // Contexto desconhecido/fora da tabela volta para site
  const htmlInv = await (await s.req('/admin/contatos?ordem=heating&contexto=curso:inexistente&faixa=invalida')).text();
  assert.equal(rowsOf(htmlInv).length, 3);
});

test('P7: /admin/contatos?ordem=heating pagina de 100 em 100 mantendo contexto e faixa', async () => {
  const s = setup();
  const list = Array.from({ length: 101 }, (_, i) => ({
    context: 'curso:chatgpt-no-dia-a-dia',
    email: `a${String(i).padStart(3, '0')}@x.com`,
    score: 50,
    band: 'quente' as const,
    signals: [{ sinal: 'curso_aula', rotulo: 'Aula concluída no curso', pontos: 50 }],
  }));
  seedHeating(s.db, list);

  const p1 = await (await s.req('/admin/contatos?ordem=heating&contexto=curso:chatgpt-no-dia-a-dia&faixa=quente')).text();
  assert.ok(p1.includes('101 encontrados, página 1 de 2.'));
  assert.ok(p1.includes('href="/admin/contatos?ordem=heating&contexto=curso%3Achatgpt-no-dia-a-dia&faixa=quente&p=2">próxima</a>'));
  const p2 = await (await s.req('/admin/contatos?ordem=heating&contexto=curso:chatgpt-no-dia-a-dia&faixa=quente&p=2')).text();
  assert.ok(p2.includes('101 encontrados, página 2 de 2.'));
  assert.ok(p2.includes('a100@x.com'));
  assert.ok(p2.includes('href="/admin/contatos?ordem=heating&contexto=curso%3Achatgpt-no-dia-a-dia&faixa=quente">anterior</a>'));
});

test('P7: heatingScoreCell exibe 0 (frio) para spam e heatingSignalsCell limita a 3 sinais', async () => {
  assert.equal(heatingScoreCell(0, 'frio'), '0 (frio)');
  assert.equal(heatingScoreCell(null, 'frio'), 'sem nota');
  assert.equal(heatingScoreCell(undefined, 'frio'), 'sem nota');
  assert.equal(heatingScoreCell(10, null), 'sem nota');
  assert.equal(heatingScoreCell(10, ''), 'sem nota');

  const fourSignals = JSON.stringify([
    { sinal: 'site_agende', rotulo: 'Sinal 1', pontos: 50 },
    { sinal: 'material_baixado', rotulo: 'Sinal 2', pontos: 12 },
    { sinal: 'newsletter_confirmada', rotulo: 'Sinal 3', pontos: 10 },
    { sinal: 'site_visita', rotulo: 'Sinal 4', pontos: 2 },
  ]);
  const cell = heatingSignalsCell(fourSignals);
  assert.equal(cell, 'Sinal 1 (50,0); Sinal 2 (12,0); Sinal 3 (10,0)');
  assert.ok(!cell.includes('Sinal 4'));

  // Também na página real /admin/contatos e /admin/contatos?ordem=heating.
  const s = setup();
  seedConversions(s.db, [
    { id: 'sp1', email: 'spam@x.com', action: 'newsletter_confirm', occurred_at: '2026-11-12T10:00:00.000Z' },
  ]);
  seedHeating(s.db, [
    {
      context: 'site',
      email: 'spam@x.com',
      score: 0,
      band: 'frio',
      signals: [{ sinal: 'email_spam', rotulo: 'Reclamou de spam: não abordar', pontos: 0 }],
    },
    {
      context: 'site',
      email: 'quatro@x.com',
      score: 74,
      band: 'quente',
      signals: JSON.parse(fourSignals),
    },
  ]);
  const htmlConv = await (await s.req('/admin/contatos')).text();
  assert.ok(htmlConv.includes('<td>0 (frio)</td>'));
  const htmlHeat = await (await s.req('/admin/contatos?ordem=heating')).text();
  assert.ok(htmlHeat.includes('Sinal 1 (50,0); Sinal 2 (12,0); Sinal 3 (10,0)'));
  assert.ok(!htmlHeat.includes('Sinal 4'));
});

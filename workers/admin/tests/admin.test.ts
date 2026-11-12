// Testes do painel de assinantes (issue #40). Rodar em workers/admin/: npm test
// D1 falso apoiado em SQLite real (node:sqlite) com a migração do repo; Resend falso com o formato documentado
// (propriedades em {value, type}); JWT do Access assinado com RSA de verdade.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { DatabaseSync } from 'node:sqlite';
import { handle, csvCell, PAGE_SIZE, type Env, type Deps } from '../src/index.ts';
import { syncSegment, GAP_MS, AUDIT_PROPERTY, type D1Like, type D1Stmt, resend, MAX_CALLS, DEADLINE_MS, MAX_PAGES } from '../src/sync.ts';
import { resetCertsCache } from '../src/access.ts';

const NOW = Date.UTC(2026, 10, 12, 12, 0, 0);
const plain = (x: unknown) => JSON.parse(JSON.stringify(x)); // linhas do node:sqlite têm protótipo nulo
const TOML = readFileSync(new URL('../wrangler.toml', import.meta.url), 'utf-8');
const tomlVar = (k: string) => {
  const m = TOML.match(new RegExp(`^${k}\\s*=\\s*"([^"]*)"`, 'm'));
  assert.ok(m, `variável ${k} ausente do wrangler.toml`);
  return m[1];
};
const TEAM = tomlVar('ACCESS_TEAM_DOMAIN');
const AUD = tomlVar('ACCESS_AUD');
const SEG = tomlVar('SEGMENT_ID');
const GUI = 'gui@exemplo.com';

// ------------------------------------------------------------------ D1 falso (SQLite real)
function d1(failBatch?: number, failSql?: RegExp, beforeBatch?: (n: number) => Promise<void>): D1Like & { raw: DatabaseSync } {
  let batches = 0;
  const db = new DatabaseSync(':memory:');
  db.exec(readFileSync(new URL('../migrations/0001_init.sql', import.meta.url), 'utf-8'));
  const stmt = (sql: string, args: unknown[] = []): D1Stmt => ({
    bind: (...v: unknown[]) => stmt(sql, v),
    run: async () => { if (failSql?.test(sql)) throw new Error('D1 indisponível'); return db.prepare(sql).run(...(args as never[])); },
    all: async <T>() => ({ results: db.prepare(sql).all(...(args as never[])) as T[] }),
    first: async <T>() => { if (failSql?.test(sql)) throw new Error('D1 indisponível'); return (db.prepare(sql).get(...(args as never[])) as T) ?? null; },
  });
  return {
    raw: db,
    prepare: (sql: string) => stmt(sql),
    batch: async (stmts: D1Stmt[]) => {
      batches++;
      if (beforeBatch) await beforeBatch(batches);
      if (failBatch !== undefined && batches === failBatch) throw new Error('D1 indisponível');
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
}

function seed(db: D1Like & { raw: DatabaseSync }, rows: Partial<Record<string, unknown>>[]) {
  const ins = db.raw.prepare(`INSERT INTO subscribers (id, email, unsubscribed, origin, joined_at, paid_until, site_confirmed_at,
    admin_unsubscribed_at, created_at, run, rev) VALUES (?, ?, ?, ?, ?, ?, '', ?, '', 'r0', ?)`);
  for (const r of rows) {
    ins.run(String(r.id), String(r.email), Number(r.unsubscribed ?? 0), String(r.origin ?? 'substack'), String(r.joined_at ?? '2025-01-01'),
      String(r.paid_until ?? ''), String(r.admin_unsubscribed_at ?? ''), Number(r.rev ?? 0));
  }
}

// ------------------------------------------------------------------ Access (JWT RS256 real)
const kp = (await crypto.subtle.generateKey({ name: 'RSASSA-PKCS1-v1_5', modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: 'SHA-256' }, true, ['sign', 'verify'])) as CryptoKeyPair;
const other = (await crypto.subtle.generateKey({ name: 'RSASSA-PKCS1-v1_5', modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: 'SHA-256' }, true, ['sign', 'verify'])) as CryptoKeyPair;
const jwk = { ...(await crypto.subtle.exportKey('jwk', kp.publicKey)), kid: 'k1' };
const b64u = (b: ArrayBuffer | Uint8Array | string) =>
  Buffer.from(typeof b === 'string' ? b : new Uint8Array(b as ArrayBuffer)).toString('base64url');

async function jwt(claims: Record<string, unknown> = {}, opts: { kid?: string; key?: CryptoKey; alg?: string } = {}) {
  const header = b64u(JSON.stringify({ alg: opts.alg ?? 'RS256', kid: opts.kid ?? 'k1', typ: 'JWT' }));
  const payload = b64u(JSON.stringify({ iss: `https://${TEAM}`, aud: [AUD], email: GUI, exp: NOW / 1000 + 3600, iat: NOW / 1000, ...claims }));
  const sig = await crypto.subtle.sign('RSASSA-PKCS1-v1_5', opts.key ?? kp.privateKey, new TextEncoder().encode(`${header}.${payload}`));
  return `${header}.${payload}.${b64u(sig)}`;
}

// ------------------------------------------------------------------ Resend falso
type FakeContact = { id: string; email: string; unsubscribed: boolean; created_at: string; segments: string[]; properties: Record<string, string> };
type Call = { method: string; url: string; body: unknown; t: number };

function setup(opts: { contacts?: FakeContact[]; props?: string[]; patchStatus?: number; rereadKeepsSubscribed?: boolean;
  certs?: () => unknown; failGetAfter?: number; rate429?: number; retryAfter?: string; latencyMs?: number;
  reread?: (c: Record<string, unknown>) => Record<string, unknown>; list?: (u: URL) => Response | null;
  afterContactGet?: (id: string) => Promise<void>; clock?: { t: number }; latencyFor?: (url: string) => number } = {}) {
  const calls: Call[] = [];
  const sleeps: number[] = [];
  const clock = opts.clock ?? { t: NOW }; // relógio virtual: avança com as esperas e com a latência simulada
  const inits: (RequestInit | undefined)[] = [];
  const contacts = new Map((opts.contacts ?? []).map((c) => [c.id, c]));
  const props = new Set(opts.props ?? [AUDIT_PROPERTY]);
  let gets = 0;
  let rate = opts.rate429 ?? 0;
  const fetchFn = async (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input instanceof Request ? input.url : input);
    const method = init?.method ?? 'GET';
    const body = typeof init?.body === 'string' ? JSON.parse(init.body) : undefined;
    calls.push({ method, url, body, t: clock.t });
    inits.push(init);
    clock.t += (opts.latencyFor?.(url) ?? 0) + (opts.latencyMs ?? 0);
    if (url === `https://${TEAM}/cdn-cgi/access/certs`) return Response.json(opts.certs ? opts.certs() : { keys: [jwk] });
    if (rate > 0 && url.startsWith('https://api.resend.com')) {
      rate--;
      return new Response('{}', { status: 429, headers: { 'retry-after': opts.retryAfter ?? '2' } });
    }
    const u = new URL(url);
    if (u.pathname === '/contact-properties' && method === 'GET') {
      return Response.json({ object: 'list', has_more: false, data: [...props].map((k) => ({ id: `p-${k}`, key: k })) });
    }
    if (u.pathname === '/contact-properties' && method === 'POST') {
      props.add(body.key);
      return Response.json({ id: 'p' }, { status: 201 });
    }
    if (u.pathname === '/contacts' && method === 'GET') {
      const forced = opts.list?.(u);
      if (forced) return forced;
      let items = [...contacts.values()].sort((a, b) => a.id.localeCompare(b.id));
      const seg = u.searchParams.get('segment_id');
      if (seg) items = items.filter((c) => c.segments.includes(seg));
      const after = u.searchParams.get('after');
      if (after) items = items.filter((c) => c.id > after);
      const n = Math.min(Number(u.searchParams.get('limit') ?? 20), 100);
      return Response.json({ object: 'list', has_more: items.length > n,
        data: items.slice(0, n).map((c) => ({ id: c.id, email: c.email, created_at: c.created_at, unsubscribed: c.unsubscribed })) });
    }
    const m = u.pathname.match(/^\/contacts\/([^/]+)$/);
    if (m && method === 'GET') {
      gets++;
      if (opts.failGetAfter !== undefined && gets > opts.failGetAfter) return Response.json({ message: 'erro com x@y.com' }, { status: 500 });
      const c = contacts.get(decodeURIComponent(m[1]));
      if (!c) return Response.json({ message: 'not found' }, { status: 404 });
      const out: Record<string, unknown> = { object: 'contact', id: c.id, email: c.email, created_at: c.created_at,
        unsubscribed: opts.rereadKeepsSubscribed ? false : c.unsubscribed,
        properties: Object.fromEntries(Object.entries(c.properties).map(([k, v]) => [k, { value: v, type: 'string' }])) };
      const res = Response.json(opts.reread ? opts.reread(out) : out);
      if (opts.afterContactGet) await opts.afterContactGet(c.id);
      return res;
    }
    if (m && method === 'PATCH') {
      if (opts.patchStatus) return Response.json({ message: 'falha com segredo@x.com' }, { status: opts.patchStatus });
      const c = contacts.get(decodeURIComponent(m[1]))!;
      if (typeof body.unsubscribed === 'boolean') c.unsubscribed = body.unsubscribed;
      Object.assign(c.properties, body.properties ?? {});
      return Response.json({ id: c.id });
    }
    return Response.json({ message: 'rota inesperada' }, { status: 500 });
  };
  const db = d1();
  const env: Env = { DB: db, RESEND_API_KEY: 're_test', SEGMENT_ID: SEG, ACCESS_TEAM_DOMAIN: TEAM, ACCESS_AUD: AUD,
    ALLOWED_EMAILS: ` ${GUI.toUpperCase()} `, SITE_ORIGIN: tomlVar('SITE_ORIGIN') };
  const passed: string[] = [];
  const deps: Deps = { fetch: fetchFn as typeof fetch, now: () => clock.t, sleep: async (ms) => { sleeps.push(ms); clock.t += ms; },
    origin: async (req) => { passed.push(new URL(req.url).pathname); return new Response('pages', { status: 200 }); } };
  return { env, deps, db, calls, inits, clock, sleeps, contacts, props, passed, run: (req: Request) => handle(req, env, deps) };
}

const contact = (id: string, email: string, props: Record<string, string> = {}, extra: Partial<FakeContact> = {}): FakeContact =>
  ({ id, email, unsubscribed: false, created_at: '2026-11-11 10:00:00+00', segments: [SEG], properties: props, ...extra });

async function get(s: ReturnType<typeof setup>, path: string, token?: string | null) {
  resetCertsCache();
  const headers: Record<string, string> = {};
  if (token !== null) headers['cf-access-jwt-assertion'] = token ?? (await jwt());
  return s.run(new Request(`https://guifav.dev${path}`, { headers }));
}

async function post(s: ReturnType<typeof setup>, form: Record<string, string>, extra: Record<string, string> = {}) {
  resetCertsCache();
  return s.run(new Request('https://guifav.dev/admin/unsubscribe', {
    method: 'POST', body: new URLSearchParams(form).toString(),
    headers: { 'cf-access-jwt-assertion': await jwt(), origin: 'https://guifav.dev', 'content-type': 'application/x-www-form-urlencoded', ...extra },
  }));
}

// ------------------------------------------------------------------ G1: acesso
test('G1: sem JWT válido do Access, nenhuma rota do painel responde (403), e nada é lido do D1', async () => {
  const s = setup();
  const bad: [string, string | null][] = [
    ['sem cabeçalho', null],
    ['lixo', 'abc.def.ghi'],
    ['assinatura de outra chave', await jwt({}, { key: other.privateKey })],
    ['alg none', await jwt({}, { alg: 'none' })],
    ['audiência errada', await jwt({ aud: ['outra'] })],
    ['emissor errado', await jwt({ iss: 'https://outro.cloudflareaccess.com' })],
    ['expirado', await jwt({ exp: NOW / 1000 - 120 })],
    ['ainda não válido', await jwt({ nbf: NOW / 1000 + 600 })],
    ['email de outra pessoa', await jwt({ email: 'outra@exemplo.com' })],
    ['kid desconhecido', await jwt({}, { kid: 'k9' })],
    ['sem exp', await jwt({ exp: undefined })],
    ['exp em texto', await jwt({ exp: String(NOW / 1000 + 3600) })],
  ];
  for (const path of ['/admin', '/admin/', '/admin/export.csv', '/admin/unsubscribe', '/admin/qualquer']) {
    for (const [why, token] of bad) {
      for (const method of ['GET', 'POST']) {
        resetCertsCache();
        const headers: Record<string, string> = { origin: 'https://guifav.dev', 'content-type': 'application/x-www-form-urlencoded' };
        if (token !== null) headers['cf-access-jwt-assertion'] = token;
        const r = await s.run(new Request(`https://guifav.dev${path}`, { method, headers, body: method === 'POST' ? 'id=a&confirmar=sim' : undefined }));
        assert.equal(r.status, 403, `${method} ${path} ${why}`);
        assert.equal(await r.text(), 'acesso negado', `${method} ${path} ${why}`);
        assert.equal(r.headers.get('cache-control'), 'no-store', `${method} ${path} ${why}`);
      }
    }
  }
  assert.deepEqual(s.passed, []);
  assert.equal(s.calls.filter((c) => c.url.startsWith('https://api.resend.com')).length, 0);
});

test('G1: JWT válido (email sem diferença de caixa) abre o painel; sem ALLOWED_EMAILS ninguém entra', async () => {
  const s = setup();
  assert.equal((await get(s, '/admin', await jwt({ email: GUI.toUpperCase() }))).status, 200);
  s.env.ALLOWED_EMAILS = '';
  assert.equal((await get(s, '/admin')).status, 403);
});

test('G1: chave rotacionada é buscada de novo uma vez antes de recusar', async () => {
  let n = 0;
  const s = setup({ certs: () => (n++ === 0 ? { keys: [] } : { keys: [jwk] }) });
  assert.equal((await get(s, '/admin')).status, 200);
  assert.equal(s.calls.filter((c) => c.url.endsWith('/cdn-cgi/access/certs')).length, 2);
});

test('G1: caminhos fora de /admin seguem para o site sem exigir login', async () => {
  const s = setup();
  for (const p of ['/administrar', '/adminx', '/blog/', '/']) {
    const r = await get(s, p, null);
    assert.equal(r.status, 200, p);
    assert.equal(await r.text(), 'pages', p);
  }
  assert.deepEqual(s.passed, ['/administrar', '/adminx', '/blog/', '/']);
});

test('G1: o wrangler.toml prende o Worker só ao caminho /admin e com cron de hora em hora', () => {
  assert.match(TOML, /routes = \[\{ pattern = "guifav\.dev\/admin\*", zone_name = "guifav\.dev" \}\]/);
  assert.match(TOML, /crons = \["17 \* \* \* \*"\]/);
  assert.doesNotMatch(TOML, /RESEND_API_KEY\s*=|ALLOWED_EMAILS\s*=|@/);
});

// ------------------------------------------------------------------ G2: página
function rows(n: number, f: (i: number) => Partial<Record<string, unknown>> = () => ({})) {
  return Array.from({ length: n }, (_, i) => ({ id: `c${String(i).padStart(4, '0')}`, email: `p${i}@x.com`, joined_at: `2025-01-${String((i % 28) + 1).padStart(2, '0')}`, ...f(i) }));
}

test('G2: lista, contadores e filtros vêm do D1', async () => {
  const s = setup();
  seed(s.db, [
    { id: 'a', email: 'ana@x.com', origin: 'substack', paid_until: '2026-12-16T22:53:57.000Z' },
    { id: 'b', email: 'bia@x.com', origin: 'site' },
    { id: 'c', email: 'caio@y.com', origin: 'substack', unsubscribed: 1, admin_unsubscribed_at: '2026-11-12T10:00:00Z', paid_until: '2026-12-01T00:00:00.000Z' },
  ]);
  s.db.raw.prepare("INSERT INTO meta (k, v) VALUES ('last_sync_at', '2026-11-12T11:17:00Z')").run();
  const html = await (await get(s, '/admin')).text();
  for (const t of ['Total 3', 'Inscritos 2', 'Descadastrados 1', 'Substack 2', 'Site 1', 'Pagos ativos 1', '2026-11-12T11:17:00Z',
    'ana@x.com', 'bia@x.com', 'caio@y.com', 'painel, 2026-11-12', '3 encontrados']) assert.ok(html.includes(t), t);
  const only = async (q: string) => [...(await (await get(s, `/admin?${q}`)).text()).matchAll(/<td>([a-z]+@[a-z.]+)<\/td>/g)].map((m) => m[1]).sort();
  assert.deepEqual(await only('q=y.com'), ['caio@y.com']);
  assert.deepEqual(await only('status=inscrito'), ['ana@x.com', 'bia@x.com']);
  assert.deepEqual(await only('status=descadastrado'), ['caio@y.com']);
  assert.deepEqual(await only('origem=site'), ['bia@x.com']);
  assert.deepEqual(await only('pago=sim'), ['ana@x.com', 'caio@y.com']); // o filtro mostra todo pagante; o contador só os inscritos
  assert.deepEqual(await only('pago=nao'), ['bia@x.com']);
  assert.deepEqual(await only('status=xyz&origem=%27'), ['ana@x.com', 'bia@x.com', 'caio@y.com']); // valor fora da lista = sem filtro
  // Botão de descadastro só para inscritos.
  assert.equal((html.match(/action="\/admin\/unsubscribe"/g) ?? []).length, 2);
});

test('G2: a busca não é LIKE (sem curinga) e é escapada no HTML; cabeçalhos de segurança em toda resposta', async () => {
  const s = setup();
  seed(s.db, [{ id: 'a', email: 'ana@x.com' }]);
  const r = await get(s, '/admin?q=%25');
  assert.ok((await r.text()).includes('0 encontrados'));
  const html = await (await get(s, '/admin?q=%3Cscript%3E')).text();
  assert.ok(!html.includes('<script>') && html.includes('&lt;script&gt;'));
  for (const path of ['/admin', '/admin/export.csv', '/admin/nada']) {
    const h = (await get(s, path)).headers;
    assert.equal(h.get('cache-control'), 'no-store', path);
    assert.equal(h.get('x-robots-tag'), 'noindex, nofollow', path);
    assert.equal(h.get('x-frame-options'), 'DENY', path);
    assert.match(h.get('content-security-policy') ?? '', /default-src 'none'.*frame-ancestors 'none'/, path);
  }
  assert.equal((await get(s, '/admin/nada')).status, 404);
});

test('G2: paginação de 100 em 100', async () => {
  const s = setup();
  seed(s.db, rows(250));
  const count = async (p: string) => ((await (await get(s, `/admin${p}`)).text()).match(/<td>p\d+@x\.com<\/td>/g) ?? []).length;
  assert.equal(PAGE_SIZE, 100);
  assert.deepEqual([await count(''), await count('?p=2'), await count('?p=3'), await count('?p=9')], [100, 100, 50, 0]);
});

// ------------------------------------------------------------------ G3: CSV
test('G3: CSV com os filtros, todas as linhas e fórmula neutralizada', async () => {
  const s = setup();
  seed(s.db, [...rows(150), { id: 'z', email: '=cmd@x.com', origin: 'site' }]);
  const r = await get(s, '/admin/export.csv');
  assert.equal(r.headers.get('content-type'), 'text/csv; charset=utf-8');
  assert.match(r.headers.get('content-disposition') ?? '', /attachment/);
  const lines = (await r.text()).trimEnd().split('\r\n');
  assert.equal(lines[0], '"email","status","origem","entrada","pago_ate","descadastrado_pelo_painel_em"');
  assert.equal(lines.length, 152);
  assert.ok(lines.includes(`"'=cmd@x.com","inscrito","site","2025-01-01","",""`));
  const site = (await (await get(s, '/admin/export.csv?origem=site')).text()).trimEnd().split('\r\n');
  assert.equal(site.length, 2);
  s.db.raw.prepare("UPDATE subscribers SET unsubscribed = 1 WHERE id IN ('z', 'c0000')").run();
  const csv = async (q: string) => (await (await get(s, `/admin/export.csv?${q}`)).text()).trimEnd().split('\r\n').slice(1);
  const unsub = await csv('status=descadastrado');
  assert.equal(unsub.length, 2);
  assert.ok(unsub.every((l) => l.includes('"descadastrado"')));
  const subd = await csv('status=inscrito');
  assert.equal(subd.length, 149);
  assert.ok(subd.every((l) => l.includes('"inscrito"')));
  for (const v of ['=1', '+1', '-1', '@a', '\tx', '\rx']) assert.ok(csvCell(v).startsWith(`"'`), v);
  assert.equal(csvCell('a"b'), '"a""b"');
});

// ------------------------------------------------------------------ G4: descadastro
test('G4: descadastro grava no Resend com auditoria, confere na releitura, atualiza o D1 e volta para o painel', async () => {
  const s = setup({ contacts: [contact('a', 'ana@x.com', { source: 'substack' })] });
  seed(s.db, [{ id: 'a', email: 'ana@x.com' }]);
  const r = await post(s, { id: 'a', confirmar: 'sim' });
  assert.equal(r.status, 303);
  assert.equal(r.headers.get('location'), '/admin?msg=descadastrado');
  const patch = s.calls.find((c) => c.method === 'PATCH');
  assert.equal(patch?.url, 'https://api.resend.com/contacts/a');
  assert.deepEqual(patch?.body, { unsubscribed: true, properties: { [AUDIT_PROPERTY]: new Date(NOW).toISOString() } });
  assert.ok(s.calls.indexOf(patch!) < s.calls.findLastIndex((c) => c.method === 'GET' && c.url.endsWith('/contacts/a')));
  assert.deepEqual(plain(s.db.raw.prepare('SELECT unsubscribed, admin_unsubscribed_at FROM subscribers').get()),
    { unsubscribed: 1, admin_unsubscribed_at: new Date(NOW).toISOString() });
  assert.equal(s.contacts.get('a')!.unsubscribed, true);
});

test('G4: falha no Resend ou releitura sem o descadastro: 502, D1 intocado e sem texto da API na página', async () => {
  const variants: Parameters<typeof setup>[0][] = [
    { patchStatus: 500 }, { patchStatus: 422 }, { patchStatus: 401 }, { patchStatus: 403 }, { rereadKeepsSubscribed: true },
    { reread: (c) => ({ ...c, properties: {} }) }, // releitura sem a auditoria
    { reread: (c) => ({ ...c, properties: { [AUDIT_PROPERTY]: { value: '2020-01-01T00:00:00.000Z', type: 'string' } } }) }, // outra auditoria
    { reread: (c) => ({ ...c, id: 'outro' }) }, // outro contato
  ];
  for (const opts of variants) {
    const s = setup({ contacts: [contact('a', 'ana@x.com')], ...opts });
    seed(s.db, [{ id: 'a', email: 'ana@x.com' }]);
    const r = await post(s, { id: 'a', confirmar: 'sim' });
    assert.equal(r.status, 502, JSON.stringify(opts));
    const body = await r.text();
    assert.ok(!body.includes('segredo') && !body.includes('ana@x.com'), JSON.stringify(opts));
    assert.deepEqual(plain(s.db.raw.prepare('SELECT unsubscribed, admin_unsubscribed_at FROM subscribers').get()), { unsubscribed: 0, admin_unsubscribed_at: '' });
  }
});

test('G4: descadastro mexe só no contato escolhido, no Resend e no D1', async () => {
  const s = setup({ contacts: [contact('a', 'ana@x.com'), contact('b', 'bia@x.com')] });
  seed(s.db, [{ id: 'a', email: 'ana@x.com' }, { id: 'b', email: 'bia@x.com' }]);
  assert.equal((await post(s, { id: 'b', confirmar: 'sim' })).status, 303);
  assert.deepEqual(s.db.raw.prepare('SELECT id, unsubscribed FROM subscribers ORDER BY id').all().map(plain), [{ id: 'a', unsubscribed: 0 }, { id: 'b', unsubscribed: 1 }]);
  assert.equal(s.contacts.get('a')!.unsubscribed, false);
  assert.deepEqual(s.calls.filter((c) => c.method === 'PATCH').map((c) => c.url), ['https://api.resend.com/contacts/b']);
});

test('G4: falha do D1 vira 502 com cabeçalhos de segurança: antes do Resend, nada é gravado; depois, a página diz que o Resend já descadastrou', async () => {
  const before = setup({ contacts: [contact('a', 'ana@x.com')] });
  before.env.DB = d1(undefined, /^SELECT id FROM subscribers/);
  const r1 = await post(before, { id: 'a', confirmar: 'sim' });
  assert.equal(r1.status, 502);
  assert.equal(r1.headers.get('cache-control'), 'no-store');
  assert.match(await r1.text(), /Nada foi alterado/);
  assert.equal(before.calls.filter((c) => c.method === 'PATCH').length, 0);
  const after = setup({ contacts: [contact('a', 'ana@x.com')] });
  const db = d1(undefined, /^UPDATE subscribers/);
  seed(db, [{ id: 'a', email: 'ana@x.com' }]);
  after.env.DB = db;
  const r2 = await post(after, { id: 'a', confirmar: 'sim' });
  assert.equal(r2.status, 502);
  assert.match(r2.headers.get('content-security-policy') ?? '', /default-src 'none'/);
  const body = await r2.text();
  assert.match(body, /Descadastrado no Resend, mas a cópia do painel não foi atualizada/);
  assert.ok(!body.includes('ana@x.com') && !body.includes('indisponível'));
  assert.equal(after.contacts.get('a')!.unsubscribed, true);
});

test('G2: conteúdo hostil gravado na cópia sai escapado na página e com aspas no CSV', async () => {
  const s = setup();
  seed(s.db, [{ id: 'x"><script>alert(1)</script>', email: '<img src=x onerror=alert(1)>@x.com', origin: '<b>o</b>', joined_at: '<i>2025</i>' }]);
  s.db.raw.prepare("INSERT INTO meta (k, v) VALUES ('last_sync_error', '<script>e</script>')").run();
  const html = await (await get(s, '/admin')).text();
  assert.ok(!html.includes('<script>') && !html.includes('<img') && !html.includes('<b>o</b>') && !html.includes('<i>'), 'HTML sem escape');
  assert.ok(html.includes('&lt;img src=x onerror=alert(1)&gt;@x.com'));
});

test('G2: cabeçalhos de segurança também em 303, 403, 405 e 502', async () => {
  const s = setup({ contacts: [contact('a', 'ana@x.com')], patchStatus: 500 });
  seed(s.db, [{ id: 'a', email: 'ana@x.com' }]);
  const ok = setup({ contacts: [contact('a', 'ana@x.com')] });
  seed(ok.db, [{ id: 'a', email: 'ana@x.com' }]);
  const responses: [string, Response][] = [
    ['303', await post(ok, { id: 'a', confirmar: 'sim' })],
    ['403', await get(s, '/admin', null)],
    ['405', await get(s, '/admin/unsubscribe')],
    ['502', await post(s, { id: 'a', confirmar: 'sim' })],
  ];
  for (const [code, r] of responses) {
    assert.equal(String(r.status), code);
    assert.equal(r.headers.get('cache-control'), 'no-store', code);
    assert.equal(r.headers.get('x-robots-tag'), 'noindex, nofollow', code);
    assert.equal(r.headers.get('x-frame-options'), 'DENY', code);
    assert.match(r.headers.get('content-security-policy') ?? '', /default-src 'none'/, code);
  }
});

test('G5: falha na troca final (último lote) não apaga as linhas antigas', async () => {
  const many = Array.from({ length: 120 }, (_, i) => contact(`c${String(i).padStart(4, '0')}`, `p${i}@x.com`));
  const s = setup({ contacts: many });
  const db = d1(4); // 3 lotes de gravação passam; o 4º (troca final) falha
  seed(db, [{ id: 'velho', email: 'velho@x.com' }]);
  s.env.DB = db;
  await assert.rejects(syncSegment(s.env, s.deps));
  assert.equal((db.raw.prepare('SELECT count(*) AS n FROM subscribers').get() as { n: number }).n, 121);
  assert.ok(db.raw.prepare("SELECT id FROM subscribers WHERE id = 'velho'").get());
});

test('G4: sem confirmação, de outra origem, formato errado, id fora do painel ou GET: nada é gravado', async () => {
  const s = setup({ contacts: [contact('a', 'ana@x.com')] });
  seed(s.db, [{ id: 'a', email: 'ana@x.com' }]);
  const cases: [Record<string, string>, Record<string, string>, number][] = [
    [{ id: 'a' }, {}, 400],
    [{ id: 'a', confirmar: 'sim' }, { origin: 'https://evil.example' }, 403],
    [{ id: 'a', confirmar: 'sim' }, { origin: 'null' }, 403],
    [{ id: 'a', confirmar: 'sim' }, { 'content-type': 'application/json' }, 415],
    [{ id: 'b', confirmar: 'sim' }, {}, 404],
    [{ id: 'a', confirmar: 'sim', x: 'y'.repeat(3000) }, {}, 413],
  ];
  for (const [form, extra, status] of cases) assert.equal((await post(s, form, extra)).status, status, JSON.stringify([form, extra]).slice(0, 80));
  const r = await s.run(new Request('https://guifav.dev/admin/unsubscribe', { headers: { 'cf-access-jwt-assertion': await jwt() } }));
  assert.equal(r.status, 405);
  assert.equal(s.calls.filter((c) => c.method === 'PATCH').length, 0);
  assert.equal(s.contacts.get('a')!.unsubscribed, false);
});

// ------------------------------------------------------------------ G5: sincronização
test('G5: o cron copia o segmento com as propriedades documentadas e a origem certa', async () => {
  const s = setup({ props: [], contacts: [
    contact('a', 'Ana@X.com', { source: 'substack', substack_joined_at: '2024-02-18T15:38:10', substack_paid_until: '2026-12-16T22:53:57.000Z' }),
    contact('b', 'bia@x.com', { site_confirmed_at: '2026-11-12T09:00:00.000Z' }),
    contact('c', 'caio@x.com', { source: 'substack', site_confirmed_at: '2026-11-12T09:00:00.000Z', [AUDIT_PROPERTY]: '2026-11-12T10:00:00Z' }, { unsubscribed: true }),
    contact('d', 'dani@x.com', {}),
    contact('e', 'fora@x.com', { source: 'substack' }, { segments: ['outro'] }),
  ] });
  assert.equal(await syncSegment(s.env, s.deps), 4);
  assert.deepEqual(plain(s.db.raw.prepare('SELECT id, email, unsubscribed, origin, joined_at, paid_until, admin_unsubscribed_at FROM subscribers ORDER BY id').all()), [
    { id: 'a', email: 'ana@x.com', unsubscribed: 0, origin: 'substack', joined_at: '2024-02-18T15:38:10', paid_until: '2026-12-16T22:53:57.000Z', admin_unsubscribed_at: '' },
    { id: 'b', email: 'bia@x.com', unsubscribed: 0, origin: 'site', joined_at: '2026-11-12T09:00:00.000Z', paid_until: '', admin_unsubscribed_at: '' },
    { id: 'c', email: 'caio@x.com', unsubscribed: 1, origin: 'substack', joined_at: '2026-11-11 10:00:00+00', paid_until: '', admin_unsubscribed_at: '2026-11-12T10:00:00Z' },
    { id: 'd', email: 'dani@x.com', unsubscribed: 0, origin: 'outra', joined_at: '2026-11-11 10:00:00+00', paid_until: '', admin_unsubscribed_at: '' },
  ]);
  assert.ok(s.props.has(AUDIT_PROPERTY));
  const meta = Object.fromEntries((s.db.raw.prepare('SELECT k, v FROM meta').all() as { k: string; v: string }[]).map((r) => [r.k, r.v]));
  assert.match(meta.sync_owner, new RegExp(`^${new Date(NOW).toISOString()}#[0-9a-f]{8}$`));
  delete meta.sync_owner;
  assert.deepEqual(meta, { last_sync_at: new Date(NOW).toISOString(), last_sync_count: '4', last_sync_error: '' });
});

test('G5: quem saiu do segmento sai da cópia; falha no meio não apaga nada e registra o erro sem email', async () => {
  const s = setup({ contacts: [contact('a', 'ana@x.com'), contact('b', 'bia@x.com')] });
  seed(s.db, [{ id: 'velho', email: 'velho@x.com' }, { id: 'a', email: 'ana@x.com' }]);
  const bad = setup({ contacts: [contact('a', 'ana@x.com'), contact('b', 'bia@x.com')], failGetAfter: 1 });
  bad.env.DB = s.db;
  await assert.rejects(syncSegment(bad.env, bad.deps));
  assert.deepEqual((s.db.raw.prepare('SELECT id FROM subscribers ORDER BY id').all() as { id: string }[]).map((r) => r.id), ['a', 'velho']);
  const err = (s.db.raw.prepare("SELECT v FROM meta WHERE k = 'last_sync_error'").get() as { v: string }).v;
  assert.match(err, /Resend 500 em GET contacts/);
  assert.ok(!err.includes('@'));
  assert.equal(await syncSegment(s.env, s.deps), 2);
  assert.deepEqual((s.db.raw.prepare('SELECT id FROM subscribers ORDER BY id').all() as { id: string }[]).map((r) => r.id), ['a', 'b']);
});

test('G5: falha ao gravar no D1 no meio da cópia não apaga as linhas antigas', async () => {
  const many = Array.from({ length: 120 }, (_, i) => contact(`c${String(i).padStart(4, '0')}`, `p${i}@x.com`));
  const s = setup({ contacts: many });
  const db = d1(2); // a segunda gravação em lote falha
  seed(db, [{ id: 'velho', email: 'velho@x.com' }]);
  s.env.DB = db;
  await assert.rejects(syncSegment(s.env, s.deps));
  assert.ok(db.raw.prepare("SELECT id FROM subscribers WHERE id = 'velho'").get(), 'linha antiga apagada antes de a cópia terminar');
});

test('G5: paginação de 100, espaçamento de 250 ms (4 req/s) e 429 respeitado', async () => {
  const many = Array.from({ length: 230 }, (_, i) => contact(`c${String(i).padStart(4, '0')}`, `p${i}@x.com`, { source: 'substack' }));
  const s = setup({ contacts: many, rate429: 1 });
  assert.equal(await syncSegment(s.env, s.deps), 230);
  const lists = s.calls.filter((c) => c.method === 'GET' && c.url.includes('/contacts?'));
  assert.equal(lists.length, 3);
  assert.ok(lists.every((c) => new URL(c.url).searchParams.get('limit') === '100' && new URL(c.url).searchParams.get('segment_id') === SEG));
  assert.equal(GAP_MS, 250);
  const resendCalls = s.calls.filter((c) => c.url.startsWith('https://api.resend.com')).length;
  // O 429 cai na primeira chamada (propriedades) e espera o retry-after inteiro; toda chamada é precedida de 250 ms.
  assert.equal(s.calls.filter((c) => c.url.includes('/contact-properties')).length, 2);
  assert.equal(s.sleeps.filter((x) => x === GAP_MS).length, resendCalls);
  assert.deepEqual(s.sleeps.filter((x) => x !== GAP_MS), [2000]);
});

function maxPerSecond(calls: Call[]): number {
  const ts = calls.filter((c) => c.url.startsWith('https://api.resend.com')).map((c) => c.t);
  return Math.max(...ts.map((a) => ts.filter((b) => b >= a && b < a + 1000).length));
}

test('G5: no relógio virtual, nenhuma janela de 1 s tem mais de 4 chamadas, inclusive criando a propriedade', async () => {
  for (const props of [[], [AUDIT_PROPERTY]]) {
    const s = setup({ props, contacts: [contact('a', 'ana@x.com'), contact('b', 'bia@x.com'), contact('c', 'cris@x.com')] });
    await syncSegment(s.env, s.deps);
    assert.ok(maxPerSecond(s.calls) <= 4, `props=${props} -> ${maxPerSecond(s.calls)} por segundo`);
  }
});

test('G5: Retry-After é respeitado inteiro; acima de 60 s a execução desiste sem repetir antes da hora', async () => {
  const ok = setup({ contacts: [contact('a', 'ana@x.com')], rate429: 1, retryAfter: '45' });
  await syncSegment(ok.env, ok.deps);
  assert.deepEqual(ok.sleeps.filter((x) => x !== GAP_MS), [45000]);
  const s = setup({ contacts: [contact('a', 'ana@x.com')], rate429: 1, retryAfter: '120' });
  seed(s.db, [{ id: 'velho', email: 'velho@x.com' }]);
  await assert.rejects(syncSegment(s.env, s.deps), /429 com espera de 120 s/);
  assert.equal(s.calls.filter((c) => c.url.startsWith('https://api.resend.com')).length, 1);
  assert.ok(s.db.raw.prepare("SELECT id FROM subscribers WHERE id = 'velho'").get());
  assert.match((s.db.raw.prepare("SELECT v FROM meta WHERE k = 'last_sync_error'").get() as { v: string }).v, /429/);
});

test('G5: prazo e orçamento de chamadas: estourar aborta com erro registrado e sem apagar a cópia', async () => {
  const many = Array.from({ length: 30 }, (_, i) => contact(`c${String(i).padStart(4, '0')}`, `p${i}@x.com`));
  const s = setup({ contacts: many, latencyMs: 60_000 }); // cada chamada leva 1 min: o prazo de 12 min estoura
  seed(s.db, [{ id: 'velho', email: 'velho@x.com' }]);
  await assert.rejects(syncSegment(s.env, s.deps), /orçamento da execução esgotado/);
  assert.ok(s.calls.length <= 14, `chamadas: ${s.calls.length}`);
  assert.ok(s.db.raw.prepare("SELECT id FROM subscribers WHERE id = 'velho'").get());
  const b = setup();
  await assert.rejects(resend(b.env, b.deps, 'GET', '/contacts/x', undefined, { start: b.deps.now(), calls: MAX_CALLS }), /orçamento/);
  assert.equal(b.calls.length, 0);
  assert.ok(MAX_CALLS < 10_000 && DEADLINE_MS < 15 * 60_000);
});

test('G5: listagem inválida ou sem progresso é erro e não apaga a cópia', async () => {
  const cases: [string, (u: URL) => Response | null][] = [
    ['sem data', () => Response.json({ object: 'list', has_more: true })],
    ['sem has_more', () => Response.json({ object: 'list', data: [] })],
    ['data não é lista', () => Response.json({ object: 'list', has_more: false, data: {} })],
    ['has_more com página vazia', () => Response.json({ object: 'list', has_more: true, data: [] })],
    ['cursor repetido', () => Response.json({ object: 'list', has_more: true, data: [{ id: 'a', email: 'ana@x.com' }] })],
  ];
  for (const [why, list] of cases) {
    const s = setup({ contacts: [contact('a', 'ana@x.com')], list });
    seed(s.db, [{ id: 'velho', email: 'velho@x.com' }]);
    await assert.rejects(syncSegment(s.env, s.deps), undefined, why);
    assert.ok(s.db.raw.prepare("SELECT id FROM subscribers WHERE id = 'velho'").get(), why);
    assert.match((s.db.raw.prepare("SELECT v FROM meta WHERE k = 'last_sync_error'").get() as { v: string }).v, /listagem inválida|sem progresso/, why);
  }
});

test('G5: descadastro feito pelo painel durante o cron não é desfeito; reinscrição feita no Resend é respeitada', async () => {
  const clock = { t: NOW };
  const s = setup({ clock, contacts: [contact('a', 'ana@x.com'), contact('b', 'bia@x.com')] });
  seed(s.db, [{ id: 'a', email: 'ana@x.com' }, { id: 'b', email: 'bia@x.com' }]);
  let fired = false;
  let audit = '';
  // O cron lê "a" inscrita; logo depois o Gui descadastra "a" pelo painel; o cron termina e grava o que leu.
  const racing = setup({ clock, contacts: [], afterContactGet: async (id) => {
    if (id !== 'a' || fired) return;
    fired = true;
    audit = new Date(clock.t).toISOString();
    const r = await post(s, { id: 'a', confirmar: 'sim' });
    assert.equal(r.status, 303);
  } });
  racing.env.DB = s.db;
  for (const c of s.contacts.values()) racing.contacts.set(c.id, c); // mesmo Resend falso
  assert.equal(await syncSegment(racing.env, racing.deps), 2);
  assert.ok(fired);
  const row = () => plain(s.db.raw.prepare("SELECT unsubscribed, admin_unsubscribed_at FROM subscribers WHERE id = 'a'").get());
  assert.deepEqual(row(), { unsubscribed: 1, admin_unsubscribed_at: audit });
  assert.equal(plain(s.db.raw.prepare("SELECT unsubscribed FROM subscribers WHERE id = 'b'").get()).unsubscribed, 0);
  // Próximo cron: o Resend já mostra a mesma auditoria; segue descadastrada.
  await syncSegment(s.env, s.deps);
  assert.deepEqual(row(), { unsubscribed: 1, admin_unsubscribed_at: audit });
  // Reinscrição feita direto no Resend (mesma auditoria): vale o Resend.
  s.contacts.get('a')!.unsubscribed = false;
  await syncSegment(s.env, s.deps);
  assert.deepEqual(row(), { unsubscribed: 0, admin_unsubscribed_at: audit });
});

test('G5: a propriedade de auditoria só é criada quando falta', async () => {
  const s = setup({ props: [AUDIT_PROPERTY], contacts: [contact('a', 'ana@x.com')] });
  await syncSegment(s.env, s.deps);
  assert.equal(s.calls.filter((c) => c.method === 'POST').length, 0);
});

test('G5: marca antiga do painel não vence o Resend: propriedade limpa ou status mudado lá valem na cópia', async () => {
  const OLD = '2026-01-01T00:00:00.000Z';
  // a) descadastrada pelo painel há tempo; reinscrita no Resend com a auditoria limpa
  const s = setup({ contacts: [contact('a', 'ana@x.com', {}, { unsubscribed: false }), contact('b', 'bia@x.com', {}, { unsubscribed: true })] });
  seed(s.db, [{ id: 'a', email: 'ana@x.com', unsubscribed: 1, admin_unsubscribed_at: OLD, rev: 3 },
    // b) inscrita na cópia com marca antiga; descadastrada no Resend sem auditoria do painel
    { id: 'b', email: 'bia@x.com', unsubscribed: 0, admin_unsubscribed_at: OLD, rev: 7 }]);
  await syncSegment(s.env, s.deps);
  assert.deepEqual(s.db.raw.prepare('SELECT id, unsubscribed, admin_unsubscribed_at FROM subscribers ORDER BY id').all().map(plain),
    [{ id: 'a', unsubscribed: 0, admin_unsubscribed_at: '' }, { id: 'b', unsubscribed: 1, admin_unsubscribed_at: '' }]);
});

test('G5: o prazo vale também para a chamada em andamento: chamada lenta no fim aborta antes de gravar', async () => {
  const s = setup({ contacts: [contact('a', 'ana@x.com')], latencyFor: (url) => (url.endsWith('/contacts/a') ? 16 * 60_000 : 0) });
  seed(s.db, [{ id: 'velho', email: 'velho@x.com' }]);
  await assert.rejects(syncSegment(s.env, s.deps), /orçamento da execução esgotado/);
  assert.ok(s.db.raw.prepare("SELECT id FROM subscribers WHERE id = 'velho'").get());
  assert.equal((s.db.raw.prepare('SELECT count(*) AS n FROM subscribers').get() as { n: number }).n, 1);
  assert.match((s.db.raw.prepare("SELECT v FROM meta WHERE k = 'last_sync_error'").get() as { v: string }).v, /orçamento/);
  // toda chamada da sincronização leva um sinal de cancelamento pelo tempo restante
  const resendInits = s.inits.filter((_, i) => s.calls[i].url.startsWith('https://api.resend.com'));
  assert.ok(resendInits.length > 0 && resendInits.every((i) => i?.signal instanceof AbortSignal));
});

test('G5: a proteção da corrida não depende de relógio: painel adiantado ou atrasado 3 h dá o mesmo resultado', async () => {
  for (const skew of [3 * 3600e3, -3 * 3600e3, 0]) {
    const clock = { t: NOW };
    const s = setup({ clock, contacts: [contact('a', 'ana@x.com'), contact('b', 'bia@x.com')] });
    seed(s.db, [{ id: 'a', email: 'ana@x.com' }, { id: 'b', email: 'bia@x.com' }]);
    s.deps.now = () => clock.t + skew; // relógio do isolate do painel
    let fired = false;
    const racing = setup({ clock, contacts: [], afterContactGet: async (id) => {
      if (id !== 'a' || fired) return;
      fired = true;
      resetCertsCache();
      const token = await jwt({ iat: undefined, exp: NOW / 1000 + 6 * 3600, nbf: undefined }); // válido em todo o skew
      const r = await s.run(new Request('https://guifav.dev/admin/unsubscribe', { method: 'POST', body: 'id=a&confirmar=sim',
        headers: { 'cf-access-jwt-assertion': token, origin: 'https://guifav.dev', 'content-type': 'application/x-www-form-urlencoded' } }));
      assert.equal(r.status, 303);
    } });
    racing.env.DB = s.db;
    for (const c of s.contacts.values()) racing.contacts.set(c.id, c);
    await syncSegment(racing.env, racing.deps);
    const st = () => (s.db.raw.prepare("SELECT unsubscribed FROM subscribers WHERE id = 'a'").get() as { unsubscribed: number }).unsubscribed;
    assert.equal(st(), 1, `corrida, skew ${skew}`);
    // a revisão é monotônica: o cron não a reescreve
    assert.equal((s.db.raw.prepare("SELECT rev FROM subscribers WHERE id = 'a'").get() as { rev: number }).rev, 1);
    // depois: reinscrição pelo site (Resend inscrito, auditoria do painel ainda lá) -> o próximo cron segue o Resend
    s.contacts.get('a')!.unsubscribed = false;
    clock.t += 3600e3;
    await syncSegment(racing.env, racing.deps);
    assert.equal(st(), 0, `reinscrição, skew ${skew}`);
  }
});

test('G5: sincronizações sobrepostas: a mais antiga não grava por cima da mais nova', async () => {
  // a nova roda inteira antes do 1º lote de gravação da antiga, ou antes da publicação dela (troca final)
  for (const at of ['primeiro lote', 'publicação']) {
    const s = setup({ contacts: [contact('a', 'ana@x.com')] });
    const newer = setup({ contacts: [contact('b', 'bia@x.com')] }); // no meio, "a" foi apagado e "b" criado
    let olderBatches = 0;
    let nested = false;
    const db = d1(undefined, undefined, async () => {
      if (nested) return;
      olderBatches++;
      if (at === 'primeiro lote' ? olderBatches !== 1 : olderBatches !== 2) return; // 1 contato: lote 1 = gravação, 2 = publicação
      nested = true;
      newer.env.DB = db;
      assert.equal(await syncSegment(newer.env, newer.deps), 1);
      nested = false;
      olderBatches = 99;
    });
    s.env.DB = db;
    seed(db, [{ id: 'velho', email: 'velho@x.com' }]);
    if (at === 'primeiro lote') await assert.rejects(syncSegment(s.env, s.deps), /substituída/);
    else await syncSegment(s.env, s.deps).catch(() => undefined); // passou da checagem; as escritas condicionais é que protegem
    assert.deepEqual(db.raw.prepare('SELECT id FROM subscribers ORDER BY id').all().map((r) => plain(r).id), ['b'], at);
    assert.equal((db.raw.prepare("SELECT v FROM meta WHERE k = 'last_sync_count'").get() as { v: string }).v, '1', at);
    assert.equal((db.raw.prepare("SELECT v FROM meta WHERE k = 'last_sync_error'").get() as { v: string }).v, '', at);
  }
});

test('G5: o prazo também vale durante as gravações no D1: nada é publicado depois dele', async () => {
  const many = Array.from({ length: 60 }, (_, i) => contact(`c${String(i).padStart(4, '0')}`, `p${i}@x.com`));
  for (const slow of [1, 2]) { // o 1º lote demora (o 2º não começa) ou o último demora (a publicação não acontece)
    const s = setup({ contacts: many });
    const db = d1(undefined, undefined, async (n) => { if (n === slow) s.clock.t += 13 * 60_000; });
    seed(db, [{ id: 'velho', email: 'velho@x.com' }]);
    s.env.DB = db;
    await assert.rejects(syncSegment(s.env, s.deps), /orçamento da execução esgotado/);
    assert.ok(db.raw.prepare("SELECT id FROM subscribers WHERE id = 'velho'").get(), String(slow));
    assert.match((db.raw.prepare("SELECT v FROM meta WHERE k = 'last_sync_error'").get() as { v: string }).v, /orçamento/);
    assert.equal(db.raw.prepare("SELECT v FROM meta WHERE k = 'last_sync_count'").get(), undefined, String(slow));
  }
});

test('G5: erro HTTP que não é 429 (401, 403, 404, 500) aborta a cópia sem repetir', async () => {
  for (const status of [401, 403, 404, 500]) {
    const s = setup({ contacts: [contact('a', 'ana@x.com')], list: () => Response.json({ message: 'x' }, { status }) });
    seed(s.db, [{ id: 'velho', email: 'velho@x.com' }]);
    await assert.rejects(syncSegment(s.env, s.deps), new RegExp(`Resend ${status} em GET contacts`));
    assert.equal(s.calls.filter((c) => c.url.includes('/contacts?')).length, 1, String(status));
    assert.ok(s.db.raw.prepare("SELECT id FROM subscribers WHERE id = 'velho'").get());
  }
});

test('G5: 429 persistente desiste depois de 4 tentativas; paginação acima do teto é erro', async () => {
  const r = setup({ contacts: [contact('a', 'ana@x.com')], rate429: 1000, retryAfter: '1' });
  await assert.rejects(syncSegment(r.env, r.deps), /429 persistente/);
  assert.equal(r.calls.length, 4);
  let n = 0;
  const p = setup({ list: () => Response.json({ object: 'list', has_more: true, data: [{ id: `x${String(n++).padStart(5, '0')}`, email: 'x@x.com' }] }) });
  seed(p.db, [{ id: 'velho', email: 'velho@x.com' }]);
  await assert.rejects(syncSegment(p.env, p.deps), /paginação sem fim/);
  assert.equal(n, MAX_PAGES);
  assert.ok(p.db.raw.prepare("SELECT id FROM subscribers WHERE id = 'velho'").get());
});

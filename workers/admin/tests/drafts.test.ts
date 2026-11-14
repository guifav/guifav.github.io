// Testes dos rascunhos privados do blog (issue #62). Rodar em workers/admin/: npm test
// D1 falso em SQLite real (node:sqlite) com as migrações do repo; R2 falso em memória; JWT do Access assinado com RSA.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { DatabaseSync } from 'node:sqlite';
import { handle, type Env, type Deps } from '../src/index.ts';
import { PREVIEW_CSP, readManifest, sha256Hex, versionId, type R2Like } from '../src/drafts.ts';
import type { D1Like, D1Stmt } from '../src/sync.ts';
import { resetCertsCache } from '../src/access.ts';

const NOW = Date.UTC(2026, 10, 13, 20, 0, 0);
const TOML = readFileSync(new URL('../wrangler.toml', import.meta.url), 'utf-8');
const tomlVar = (k: string) => TOML.match(new RegExp(`^${k}\\s*=\\s*"([^"]*)"`, 'm'))![1];
const TEAM = tomlVar('ACCESS_TEAM_DOMAIN');
const AUD = tomlVar('ACCESS_AUD');
const ORIGIN = tomlVar('SITE_ORIGIN');
const GUI = 'gui@exemplo.com';
const SHA = (c: string) => c.repeat(64);

// ------------------------------------------------------------------ D1 falso com todas as migrações
function d1(failSql?: RegExp): D1Like & { raw: DatabaseSync } {
  const db = new DatabaseSync(':memory:');
  const dir = new URL('../migrations/', import.meta.url);
  for (const f of readdirSync(dir).filter((f) => f.endsWith('.sql')).sort()) db.exec(readFileSync(new URL(f, dir), 'utf-8'));
  const stmt = (sql: string, args: unknown[] = []): D1Stmt => ({
    bind: (...v: unknown[]) => stmt(sql, v),
    run: async () => { if (failSql?.test(sql)) throw new Error('D1 indisponível'); return db.prepare(sql).run(...(args as never[])); },
    all: async <T>() => ({ results: JSON.parse(JSON.stringify(db.prepare(sql).all(...(args as never[])))) as T[] }),
    first: async <T>() => {
      if (failSql?.test(sql)) throw new Error('D1 indisponível');
      const r = db.prepare(sql).get(...(args as never[]));
      return (r ? JSON.parse(JSON.stringify(r)) : null) as T;
    },
  });
  return { raw: db, prepare: (sql: string) => stmt(sql), batch: async (s: D1Stmt[]) => Promise.all(s.map((x) => x.run())) };
}

// ------------------------------------------------------------------ R2 falso
function r2() {
  const store = new Map<string, Uint8Array>();
  const body = (b: Uint8Array) => ({
    arrayBuffer: async () => b.slice().buffer,
    text: async () => new TextDecoder().decode(b),
  });
  const bucket: R2Like & { store: Map<string, Uint8Array>; gets: string[] } = {
    store,
    gets: [],
    async get(key) {
      bucket.gets.push(key);
      const b = store.get(key);
      return b ? body(b) : null;
    },
    async list({ prefix = '', delimiter } = {}) {
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
  };
  return bucket;
}

const enc = (s: string) => new TextEncoder().encode(s);
const HTML = (title = 'Post') =>
  `<!DOCTYPE html><html lang="pt-BR"><head><title>${title}</title></head><body class="x">\n<main><h1>${title}</h1>`
  + `<img src="/admin/rascunhos/sales-coach/img/sales-coach/a-728.webp"></main></body></html>`;

type Draft = { html: string; version: string; htmlSha: string; source: string };

/** Versão imutável em <slug>/v/<versão>/ e o manifesto apontando para ela, como o draft_preview.py envia. */
async function putDraft(b: ReturnType<typeof r2>, slug = 'sales-coach', opts: { html?: string; title?: string; source?: string;
  manifest?: Record<string, unknown>; files?: Record<string, string>; image?: string; skipManifest?: boolean } = {}): Promise<Draft> {
  const html = opts.html ?? HTML(opts.title ?? 'Como construí o Sales Coach');
  const htmlSha = await sha256Hex(html);
  const source = opts.source ?? SHA('a');
  const version = await versionId(htmlSha, source);
  b.store.set(`${slug}/v/${version}/index.html`, enc(html));
  b.store.set(`${slug}/v/${version}/img/${slug}/a-728.webp`, enc(opts.image ?? 'WEBP-BYTES'));
  const files = opts.files ?? { 'index.html': 'text/html; charset=utf-8', [`img/${slug}/a-728.webp`]: 'image/webp' };
  if (!opts.skipManifest) {
    b.store.set(`${slug}/manifest.json`, enc(JSON.stringify({ slug, title: opts.title ?? 'Como construí o Sales Coach',
      uploaded_at: '2026-11-13T19:00:00.000Z', source_sha256: source, html_sha256: htmlSha, version, files, ...(opts.manifest ?? {}) })));
  }
  return { html, version, htmlSha, source };
}

// ------------------------------------------------------------------ Access (JWT RS256 real)
const kp = (await crypto.subtle.generateKey({ name: 'RSASSA-PKCS1-v1_5', modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: 'SHA-256' }, true, ['sign', 'verify'])) as CryptoKeyPair;
const jwk = { ...(await crypto.subtle.exportKey('jwk', kp.publicKey)), kid: 'k1' };
const b64u = (b: ArrayBuffer | string) => Buffer.from(typeof b === 'string' ? b : new Uint8Array(b)).toString('base64url');
async function jwt(email = GUI) {
  const h = b64u(JSON.stringify({ alg: 'RS256', kid: 'k1', typ: 'JWT' }));
  const p = b64u(JSON.stringify({ iss: `https://${TEAM}`, aud: [AUD], email, exp: NOW / 1000 + 3600, iat: NOW / 1000 }));
  const sig = await crypto.subtle.sign('RSASSA-PKCS1-v1_5', kp.privateKey, new TextEncoder().encode(`${h}.${p}`));
  return `${h}.${p}.${b64u(sig)}`;
}

type Mail = { url: string; body: Record<string, unknown>; auth: string | null };

function setup(opts: { mailStatus?: number; mailThrows?: boolean; failSql?: RegExp; notify?: boolean } = {}) {
  const mails: Mail[] = [];
  const other: string[] = [];
  const fetchFn = async (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input instanceof Request ? input.url : input);
    if (url === `https://${TEAM}/cdn-cgi/access/certs`) return Response.json({ keys: [jwk] });
    if (url === 'https://api.resend.com/emails') {
      if (opts.mailThrows) throw new TypeError('network');
      mails.push({ url, body: JSON.parse(String(init?.body)), auth: new Headers(init?.headers).get('authorization') });
      return Response.json({ id: 'e1' }, { status: opts.mailStatus ?? 200 });
    }
    other.push(url);
    return Response.json({ message: 'rota inesperada' }, { status: 500 });
  };
  const db = d1(opts.failSql);
  const bucket = r2();
  const env: Env = { DB: db, RESEND_API_KEY: 're_test', SEGMENT_ID: 's', TOPIC_ID: 't', ACCESS_TEAM_DOMAIN: TEAM, ACCESS_AUD: AUD,
    ALLOWED_EMAILS: GUI, SITE_ORIGIN: ORIGIN, DRAFTS: bucket,
    DRAFT_NOTIFY_FROM: opts.notify === false ? '' : 'Painel <painel@guifav.dev>', DRAFT_NOTIFY_TO: opts.notify === false ? '' : 'roger@exemplo.com' };
  const deps: Deps = { fetch: fetchFn as typeof fetch, now: () => NOW, sleep: async () => {}, origin: async () => new Response('pages') };
  const req = async (path: string, init: { method?: string; body?: string; headers?: Record<string, string>; token?: string | null } = {}) => {
    resetCertsCache();
    const h: Record<string, string> = { ...(init.headers ?? {}) };
    if (init.token !== null) h['cf-access-jwt-assertion'] = init.token ?? (await jwt());
    return handle(new Request(`https://guifav.dev${path}`, { method: init.method ?? 'GET', body: init.body, headers: h }), env, deps);
  };
  const approve = async (slug: string, form: Record<string, string>, headers: Record<string, string> = {}) =>
    req(`/admin/rascunhos/${slug}/aprovar`, { method: 'POST', body: new URLSearchParams(form).toString(),
      headers: { origin: ORIGIN, 'content-type': 'application/x-www-form-urlencoded', ...headers } });
  const rows = () => JSON.parse(JSON.stringify(db.raw.prepare('SELECT * FROM draft_approvals ORDER BY id').all())) as Record<string, string>[];
  return { env, deps, db, bucket, mails, other, req, approve, rows };
}

// ------------------------------------------------------------------ R1: acesso
test('R1: sem JWT válido ou com email fora da lista, nenhuma rota de rascunho responde (403) e o R2 não é lido', async () => {
  const s = setup();
  await putDraft(s.bucket);
  for (const path of ['/admin/rascunhos', '/admin/rascunhos/sales-coach/', '/admin/rascunhos/sales-coach/img/sales-coach/a-728.webp']) {
    for (const token of [null, 'x.y.z', await jwt('outro@exemplo.com')]) {
      const r = await s.req(path, { token });
      assert.equal(r.status, 403, `${path} ${token}`);
    }
  }
  const r = await s.req('/admin/rascunhos/sales-coach/aprovar', { method: 'POST', token: null, body: 'confirmar=sim',
    headers: { origin: ORIGIN, 'content-type': 'application/x-www-form-urlencoded' } });
  assert.equal(r.status, 403);
  assert.deepEqual(s.bucket.gets, []);
  assert.deepEqual(s.rows(), []);
});

// ------------------------------------------------------------------ R2: lista
test('R2: lista vazia, e com rascunhos: título escapado, slug, data e estado; manifesto inválido some da lista', async () => {
  const s = setup();
  let r = await s.req('/admin/rascunhos');
  assert.equal(r.status, 200);
  assert.match(await r.text(), /Nenhum rascunho no momento/);
  await putDraft(s.bucket, 'sales-coach', { title: 'Sales <Coach> & "IA"' });
  s.bucket.store.set('quebrado/manifest.json', enc('{nao json'));
  s.bucket.store.set('quebrado/index.html', enc('<body>x</body>'));
  r = await s.req('/admin/rascunhos/');
  const body = await r.text();
  assert.match(body, /<a href="\/admin\/rascunhos\/sales-coach\/">Sales &lt;Coach&gt; &amp; &quot;IA&quot;<\/a>/);
  assert.match(body, /2026-11-13 19:00 UTC/);
  assert.match(body, /<td>aguardando aprovação<\/td>/);
  assert.doesNotMatch(body, /quebrado/);
  assert.equal(r.headers.get('cache-control'), 'private, no-store');
  assert.equal(r.headers.get('x-robots-tag'), 'noindex, nofollow');
  assert.match(r.headers.get('content-security-policy')!, /^default-src 'none'/);
  assert.equal((await s.req('/admin/rascunhos', { method: 'POST' })).status, 405);
});

test('R2: o painel de assinantes tem o link para os rascunhos', async () => {
  const s = setup();
  const body = await (await s.req('/admin')).text();
  assert.match(body, /<a href="\/admin\/rascunhos">Rascunhos<\/a>/);
});

// ------------------------------------------------------------------ R3: prévia
test('R3: prévia com a barra de aprovação logo depois do <body>, hash do HTML servido e CSP da prévia', async () => {
  const s = setup();
  const { html, version } = await putDraft(s.bucket);
  const r = await s.req('/admin/rascunhos/sales-coach/');
  assert.equal(r.status, 200);
  const out = await r.text();
  assert.ok(out.startsWith('<!DOCTYPE html><html lang="pt-BR"><head><title>Como construí o Sales Coach</title></head><body class="x"><div id="rascunho-barra"'));
  assert.ok(out.includes(`<input type="hidden" name="versao" value="${version}">`));
  assert.ok(out.includes('action="/admin/rascunhos/sales-coach/aprovar"'));
  assert.ok(out.includes('Aguardando a sua aprovação.'));
  // O resto do HTML sai intacto.
  assert.equal(out.replace(/<div id="rascunho-barra"[\s\S]*?<\/a><\/div>/, ''), html);
  assert.equal(r.headers.get('content-security-policy'), PREVIEW_CSP);
  assert.match(PREVIEW_CSP, /form-action 'self'/);
  assert.match(PREVIEW_CSP, /frame-ancestors 'none'/);
  assert.match(PREVIEW_CSP, /connect-src 'none'/);
  // r1 (Kimi P2): os iframes que o blog gera (blog.ts) e que o check_site aceita precisam carregar na prévia.
  const frame = PREVIEW_CSP.split('; ').find((d) => d.startsWith('frame-src '))!.split(' ').slice(1);
  assert.deepEqual(frame.sort(), ['https://creators.spotify.com', 'https://open.spotify.com', 'https://player.vimeo.com',
    'https://podcasters.spotify.com', 'https://www.youtube-nocookie.com', 'https://www.youtube.com']);
  assert.equal(r.headers.get('cache-control'), 'private, no-store');
  assert.equal(r.headers.get('x-robots-tag'), 'noindex, nofollow');
});

test('R3: sem barra final redireciona; slug desconhecido, inválido ou com manifesto inválido responde 404', async () => {
  const s = setup();
  await putDraft(s.bucket);
  const r = await s.req('/admin/rascunhos/sales-coach');
  assert.equal(r.status, 308);
  assert.equal(r.headers.get('location'), '/admin/rascunhos/sales-coach/');
  // r1 (Kimi e Astra P2): o 308 leva os mesmos cabeçalhos das outras respostas.
  assert.equal(r.headers.get('cache-control'), 'private, no-store');
  assert.equal(r.headers.get('x-robots-tag'), 'noindex, nofollow');
  assert.equal(r.headers.get('x-frame-options'), 'DENY');
  assert.equal(r.headers.get('x-content-type-options'), 'nosniff');
  assert.equal(r.headers.get('referrer-policy'), 'no-referrer');
  assert.match(r.headers.get('content-security-policy')!, /^default-src 'none'/);
  // Slug inválido nem redireciona.
  assert.equal((await s.req('/admin/rascunhos/Sales-Coach')).status, 404);
  for (const p of ['/admin/rascunhos/outro/', '/admin/rascunhos/Sales-Coach/', '/admin/rascunhos/sales_coach/', '/admin/rascunhos/sales%2Fcoach/']) {
    assert.equal((await s.req(p)).status, 404, p);
  }
  for (const [name, manifest] of Object.entries({
    'slug diferente': { slug: 'outro' },
    'sha inválido': { source_sha256: 'xyz' },
    'html_sha256 ausente': { html_sha256: undefined },
    'versão que não confere com html e fonte': { version: SHA('c') },
    'fonte trocada sem trocar a versão': { source_sha256: SHA('d') },
    'sem título': { title: 5 },
    'arquivo com ..': { files: { 'index.html': 'text/html; charset=utf-8', '../x': 'image/webp' } },
    'arquivo oculto': { files: { 'index.html': 'text/html; charset=utf-8', 'img/.env': 'text/plain' } },
    'index sem tipo html': { files: { 'index.html': 'text/plain' } },
    'files lista': { files: ['index.html'] },
  })) {
    const t = setup();
    await putDraft(t.bucket, 'sales-coach', { manifest });
    assert.equal((await t.req('/admin/rascunhos/sales-coach/')).status, 404, name);
  }
  const t = setup();
  t.bucket.store.set('sales-coach/index.html', enc('<html><body>x</body></html>'));
  assert.equal((await t.req('/admin/rascunhos/sales-coach/')).status, 404, 'sem manifesto');
  // HTML da versão diferente do html_sha256 do manifesto (objeto corrompido): 404, nada servido nem aprovável.
  const u = setup();
  const d = await putDraft(u.bucket);
  u.bucket.store.set(`sales-coach/v/${d.version}/index.html`, enc('<html><body>outro</body></html>'));
  assert.equal((await u.req('/admin/rascunhos/sales-coach/')).status, 404, 'html adulterado');
  assert.equal((await u.approve('sales-coach', { confirmar: 'sim', versao: d.version })).status, 404, 'html adulterado: aprovar');
  assert.deepEqual(u.rows(), []);
});

test('R3: readManifest devolve files sem protótipo e recusa html_sha256 fora do formato, mesmo com a versão coerente', async () => {
  const b = r2();
  await putDraft(b);
  const m = (await readManifest(b, 'sales-coach'))!;
  assert.equal(Object.getPrototypeOf(m.files), null);
  assert.equal('constructor' in m.files, false);
  const c = r2();
  const bad = 'xyz';
  await putDraft(c, 'sales-coach', { manifest: { html_sha256: bad, version: await versionId(bad, SHA('a')) } });
  assert.equal(await readManifest(c, 'sales-coach'), null);
});

// ------------------------------------------------------------------ R4: arquivos
test('R4: arquivo do manifesto sai com o tipo do manifesto; fora do manifesto, index, manifesto ou caminho estranho dá 404', async () => {
  const s = setup();
  const d = await putDraft(s.bucket);
  s.bucket.store.set(`sales-coach/v/${d.version}/img/sales-coach/fora.webp`, enc('X'));
  s.bucket.store.set(`sales-coach/v/${d.version}/segredo.txt`, enc('X'));
  // r1 (Kimi e Astra P2): nomes de propriedades herdadas de Object não estão no manifesto.
  for (const k of ['constructor', 'toString', '__proto__', 'hasOwnProperty', 'valueOf']) {
    s.bucket.store.set(`sales-coach/v/${d.version}/${k}`, enc('FORA-DO-MANIFESTO'));
  }
  // Arquivo de uma versão antiga, mesmo listado no manifesto atual por nome, só é lido da versão atual.
  s.bucket.store.set('sales-coach/img/sales-coach/a-728.webp', enc('LEGADO-FORA-DA-VERSAO'));
  const r = await s.req('/admin/rascunhos/sales-coach/img/sales-coach/a-728.webp');
  assert.equal(r.status, 200);
  assert.equal(r.headers.get('content-type'), 'image/webp');
  assert.equal(new TextDecoder().decode(await r.arrayBuffer()), 'WEBP-BYTES');
  assert.equal(r.headers.get('cache-control'), 'private, no-store');
  const before = s.bucket.gets.length;
  for (const p of ['img/sales-coach/fora.webp', 'segredo.txt', 'index.html', 'manifest.json', 'img/sales-coach/%2e%2e/x',
    'img//a-728.webp', '.hidden', 'img/sales-coach/a-728.webp/', 'constructor', 'toString', 'hasOwnProperty', 'valueOf',
    `v/${d.version}/index.html`]) {
    const r = await s.req(`/admin/rascunhos/sales-coach/${p}`);
    assert.equal(r.status, 404, p);
    assert.doesNotMatch(await r.text(), /FORA-DO-MANIFESTO|LEGADO/, p);
  }
  // Fora do manifesto, só o manifesto é lido: nenhum objeto do rascunho.
  assert.deepEqual(s.bucket.gets.slice(before).filter((k) => !k.endsWith('/manifest.json')), []);
  assert.equal((await s.req('/admin/rascunhos/sales-coach/img/sales-coach/a-728.webp', { method: 'POST' })).status, 405);
  // Caminho com ../ é normalizado pela URL antes de chegar: vira o manifesto, que nunca é servido.
  assert.equal((await s.req('/admin/rascunhos/sales-coach/img/../manifest.json')).status, 404);
});

// ------------------------------------------------------------------ R5: aprovação
test('R5: aprovação grava a versão vista, avisa o Roger e a prévia e a lista passam a mostrar aprovado', async () => {
  const s = setup();
  const { htmlSha: hash, version } = await putDraft(s.bucket);
  const r = await s.approve('sales-coach', { confirmar: 'sim', versao: version });
  assert.equal(r.status, 303);
  assert.equal(r.headers.get('location'), '/admin/rascunhos?msg=aprovado');
  const rows = s.rows();
  assert.equal(rows.length, 1);
  assert.deepEqual({ ...rows[0], id: undefined }, { id: undefined, slug: 'sales-coach', title: 'Como construí o Sales Coach', html_sha256: hash,
    source_sha256: SHA('a'), approved_at: '2026-11-13T20:00:00.000Z', approved_by: GUI, notified_at: '2026-11-13T20:00:00.000Z', notify_error: '' });
  assert.equal(s.mails.length, 1);
  assert.deepEqual(s.mails[0].body.to, ['roger@exemplo.com']);
  assert.equal(s.mails[0].body.from, 'Painel <painel@guifav.dev>');
  assert.equal(s.mails[0].body.subject, 'Rascunho aprovado: Como construí o Sales Coach');
  assert.match(String(s.mails[0].body.text), new RegExp(`Versão aprovada: ${version}`));
  assert.match(String(s.mails[0].body.text), new RegExp(`HTML aprovado \\(sha256\\): ${hash}`));
  assert.match(String(s.mails[0].body.text), new RegExp(`Fonte aprovada \\(sha256\\): ${SHA('a')}`));
  assert.equal(s.mails[0].auth, 'Bearer re_test');
  const prev = await (await s.req('/admin/rascunhos/sales-coach/')).text();
  assert.match(prev, /Aprovado em 2026-11-13 20:00 UTC/);
  assert.doesNotMatch(prev, /name="confirmar"/);
  const list = await (await s.req('/admin/rascunhos?msg=aprovado')).text();
  assert.match(list, /aprovado em 2026-11-13 20:00 UTC/);
  assert.match(list, /O Roger foi avisado por email/);
});

test('R5: sem confirmação, outra origem, formato errado, grande demais, hash velho, slug desconhecido ou GET: nada é gravado nem enviado', async () => {
  const s = setup();
  const { version: hash, htmlSha } = await putDraft(s.bucket);
  const cases: [string, Promise<Response>, number][] = [
    ['hash do HTML no lugar da versão', s.approve('sales-coach', { confirmar: 'sim', versao: htmlSha }), 409],
    ['campo antigo html', s.approve('sales-coach', { confirmar: 'sim', html: hash }), 409],
    ['sem confirmar', s.approve('sales-coach', { versao: hash }), 400],
    ['confirmar errado', s.approve('sales-coach', { confirmar: 'on', versao: hash }), 400],
    ['outra origem', s.approve('sales-coach', { confirmar: 'sim', versao: hash }, { origin: 'https://evil.example' }), 403],
    ['sem origem', s.req('/admin/rascunhos/sales-coach/aprovar', { method: 'POST', body: `confirmar=sim&versao=${hash}`,
      headers: { 'content-type': 'application/x-www-form-urlencoded' } }), 403],
    ['json', s.approve('sales-coach', { confirmar: 'sim', versao: hash }, { 'content-type': 'application/json' }), 415],
    ['grande', s.approve('sales-coach', { confirmar: 'sim', versao: hash, x: 'a'.repeat(3000) }), 413],
    ['hash velho', s.approve('sales-coach', { confirmar: 'sim', versao: SHA('b') }), 409],
    ['hash vazio', s.approve('sales-coach', { confirmar: 'sim' }), 409],
    ['outro slug', s.approve('outro', { confirmar: 'sim', versao: hash }), 404],
    ['GET', s.req('/admin/rascunhos/sales-coach/aprovar'), 405],
  ];
  for (const [name, p, status] of cases) assert.equal((await p).status, status, name);
  assert.deepEqual(s.rows(), []);
  assert.deepEqual(s.mails, []);
});

test('R5: versão nova depois da aprovação volta a pedir aprovação, e a nova aprovação vale só para ela', async () => {
  const s = setup();
  const h1 = (await putDraft(s.bucket)).version;
  assert.equal((await s.approve('sales-coach', { confirmar: 'sim', versao: h1 })).status, 303);
  const h2 = (await putDraft(s.bucket, 'sales-coach', { title: 'Sales Coach, versão 2' })).version;
  assert.notEqual(h1, h2);
  const prev = await (await s.req('/admin/rascunhos/sales-coach/')).text();
  assert.match(prev, /A versão mudou depois da última aprovação/);
  assert.ok(prev.includes(`value="${h2}"`));
  assert.match(await (await s.req('/admin/rascunhos')).text(), /a versão mudou depois da última aprovação/);
  // A aprovação antiga não serve para a versão nova.
  assert.equal((await s.approve('sales-coach', { confirmar: 'sim', versao: h1 })).status, 409);
  assert.equal((await s.approve('sales-coach', { confirmar: 'sim', versao: h2 })).status, 303);
  assert.equal(s.rows().length, 2);
});

test('R5 (r1, Kimi P1 e Astra P2): imagem trocada com o mesmo HTML não herda a aprovação; o formulário antigo dá 409', async () => {
  const s = setup();
  const a = await putDraft(s.bucket, 'sales-coach', { source: SHA('a') });
  assert.equal((await s.approve('sales-coach', { confirmar: 'sim', versao: a.version })).status, 303);
  // Só a imagem mudou: mesma página HTML, fonte nova.
  const b = await putDraft(s.bucket, 'sales-coach', { source: SHA('b'), image: 'WEBP-NOVO' });
  assert.equal(a.htmlSha, b.htmlSha);
  assert.notEqual(a.version, b.version);
  const prev = await (await s.req('/admin/rascunhos/sales-coach/')).text();
  assert.match(prev, /A versão mudou depois da última aprovação/);
  assert.ok(prev.includes(`name="versao" value="${b.version}"`));
  assert.match(await (await s.req('/admin/rascunhos')).text(), /a versão mudou depois da última aprovação/);
  assert.equal(new TextDecoder().decode(await (await s.req('/admin/rascunhos/sales-coach/img/sales-coach/a-728.webp')).arrayBuffer()), 'WEBP-NOVO');
  // Formulário aberto antes da troca: nada gravado.
  assert.equal((await s.approve('sales-coach', { confirmar: 'sim', versao: a.version })).status, 409);
  assert.deepEqual(s.rows().map((r) => r.source_sha256), [SHA('a')]);
  assert.equal((await s.approve('sales-coach', { confirmar: 'sim', versao: b.version })).status, 303);
  assert.deepEqual(s.rows().map((r) => [r.html_sha256, r.source_sha256]), [[a.htmlSha, SHA('a')], [b.htmlSha, SHA('b')]]);
});

test('R5 (r1, Astra P2): envio interrompido antes do manifesto deixa a versão anterior inteira, e a aprovação grava a fonte dela', async () => {
  const s = setup();
  const a = await putDraft(s.bucket, 'sales-coach', { source: SHA('a') });
  // A versão B sobe os arquivos, mas o manifesto (último) falha: ele continua apontando para A.
  await putDraft(s.bucket, 'sales-coach', { source: SHA('b'), title: 'Texto novo', skipManifest: true });
  const prev = await (await s.req('/admin/rascunhos/sales-coach/')).text();
  assert.doesNotMatch(prev, /Texto novo/);
  assert.ok(prev.includes(`name="versao" value="${a.version}"`));
  assert.equal((await s.approve('sales-coach', { confirmar: 'sim', versao: a.version })).status, 303);
  assert.deepEqual(s.rows().map((r) => [r.html_sha256, r.source_sha256]), [[a.htmlSha, SHA('a')]]);
});

test('R5: aviso por email falho fica registrado na aprovação, que continua valendo', async () => {
  for (const [name, opts, why] of [
    ['Resend 500', { mailStatus: 500 }, 'Resend respondeu 500'],
    ['rede', { mailThrows: true }, 'falha de rede'],
    ['sem configuração', { notify: false }, 'aviso sem configuração'],
  ] as const) {
    const s = setup(opts);
    const { version: hash, htmlSha } = await putDraft(s.bucket);
    const r = await s.approve('sales-coach', { confirmar: 'sim', versao: hash });
    assert.equal(r.headers.get('location'), '/admin/rascunhos?msg=aprovado-sem-aviso', name);
    const [row] = s.rows();
    assert.equal(row.notify_error, why, name);
    assert.equal(row.notified_at, '', name);
    assert.equal(row.html_sha256, htmlSha, name);
    assert.match(await (await s.req('/admin/rascunhos?msg=aprovado-sem-aviso')).text(), /o aviso por email ao Roger falhou/, name);
  }
});

test('R5: falha do D1 ao gravar a aprovação: 502 e nenhum email', async () => {
  const s = setup({ failSql: /INSERT INTO draft_approvals/ });
  const hash = (await putDraft(s.bucket)).version;
  const r = await s.approve('sales-coach', { confirmar: 'sim', versao: hash });
  assert.equal(r.status, 502);
  assert.match(await r.text(), /Nada foi aprovado/);
  assert.deepEqual(s.mails, []);
});

test('R5: falha ao registrar o aviso não desfaz a aprovação', async () => {
  const s = setup({ failSql: /UPDATE draft_approvals/ });
  const hash = (await putDraft(s.bucket)).version;
  const r = await s.approve('sales-coach', { confirmar: 'sim', versao: hash });
  assert.equal(r.status, 303);
  assert.equal(s.rows().length, 1);
  assert.equal(s.mails.length, 1);
});

// ------------------------------------------------------------------ R6: configuração
test('R6: wrangler.toml liga o bucket privado e a migração cria a tabela de aprovações', () => {
  assert.match(TOML, /\[\[r2_buckets\]\]\nbinding = "DRAFTS"\nbucket_name = "blog-rascunhos"/);
  assert.doesNotMatch(TOML, /DRAFT_NOTIFY_(FROM|TO)\s*=/); // segredos, não variáveis do repo
  const sql = readFileSync(new URL('../migrations/0002_draft_approvals.sql', import.meta.url), 'utf-8');
  for (const col of ['slug', 'title', 'html_sha256', 'source_sha256', 'approved_at', 'approved_by', 'notified_at', 'notify_error']) {
    assert.match(sql, new RegExp(`\\b${col} TEXT NOT NULL`), col);
  }
});

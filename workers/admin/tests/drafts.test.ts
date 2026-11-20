// Testes dos rascunhos privados do blog (issue #62). Rodar em workers/admin/: npm test
// D1 falso em SQLite real (node:sqlite) com as migrações do repo; R2 falso em memória; JWT do Access assinado com RSA.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { DatabaseSync } from 'node:sqlite';
import { handle, type Env, type Deps } from '../src/index.ts';
import { canonicais, execucaoDa, mergeado, PREVIEW_CSP, publishBranch, readManifest, sha256Hex, tituloNaLista, versionId, type R2Like } from '../src/drafts.ts';
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
function d1(falha: { sql?: RegExp } = {}): D1Like & { raw: DatabaseSync } {
  const db = new DatabaseSync(':memory:');
  const dir = new URL('../migrations/', import.meta.url);
  for (const f of readdirSync(dir).filter((f) => f.endsWith('.sql')).sort()) db.exec(readFileSync(new URL(f, dir), 'utf-8'));
  const stmt = (sql: string, args: unknown[] = []): D1Stmt => ({
    bind: (...v: unknown[]) => stmt(sql, v),
    run: async () => { if (falha.sql?.test(sql)) throw new Error('D1 indisponível'); return db.prepare(sql).run(...(args as never[])); },
    all: async <T>() => ({ results: JSON.parse(JSON.stringify(db.prepare(sql).all(...(args as never[])))) as T[] }),
    first: async <T>() => {
      if (falha.sql?.test(sql)) throw new Error('D1 indisponível');
      const r = db.prepare(sql).get(...(args as never[]));
      return (r ? JSON.parse(JSON.stringify(r)) : null) as T;
    },
  });
  return { raw: db, prepare: (sql: string) => stmt(sql), batch: async (s: D1Stmt[]) => Promise.all(s.map((x) => x.run())) };
}

// ------------------------------------------------------------------ R2 falso
function r2() {
  // Cada gravação ganha um instante crescente, como o uploaded do R2: o que foi gravado depois tem instante maior.
  const gravadoEm = new Map<string, number>();
  let relogioR2 = NOW;
  const store = new (class extends Map<string, Uint8Array> {
    set(k: string, v: Uint8Array) {
      gravadoEm.set(k, relogioR2++);
      return super.set(k, v);
    }
  })();
  const body = (b: Uint8Array) => ({
    arrayBuffer: async () => b.slice().buffer,
    text: async () => new TextDecoder().decode(b),
  });
  // pagina: tamanho máximo de página da listagem sem delimitador (como o limit do binding), para exercitar o cursor.
  const bucket: R2Like & { store: Map<string, Uint8Array>; gets: string[]; deletes: string[][]; pagina: number } = {
    store,
    gets: [],
    deletes: [],
    pagina: 1000,
    // #82, fatia 5: como o binding, aceita uma chave ou uma lista (até 1000) e ignora chave que não existe.
    async delete(keys) {
      const lista = Array.isArray(keys) ? keys : [keys];
      assert.ok(lista.length <= 1000, 'até 1000 chaves por chamada');
      bucket.deletes.push(lista);
      for (const k of lista) store.delete(k);
    },
    async get(key) {
      bucket.gets.push(key);
      const b = store.get(key);
      return b ? body(b) : null;
    },
    async list({ prefix = '', delimiter, cursor, limit = 1000 } = {}) {
      const keys = [...store.keys()].filter((k) => k.startsWith(prefix)).sort();
      if (!delimiter) {
        const ini = cursor ? Number(cursor) : 0;
        const fim = ini + Math.min(limit, bucket.pagina);
        const truncated = fim < keys.length;
        return { objects: keys.slice(ini, fim).map((key) => ({ key, uploaded: new Date(gravadoEm.get(key) ?? 0) })), delimitedPrefixes: [], truncated,
          ...(truncated ? { cursor: String(fim) } : {}) };
      }
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
async function jwt(email = GUI, agora = NOW) {
  const h = b64u(JSON.stringify({ alg: 'RS256', kid: 'k1', typ: 'JWT' }));
  const p = b64u(JSON.stringify({ iss: `https://${TEAM}`, aud: [AUD], email, exp: agora / 1000 + 3600, iat: agora / 1000 }));
  const sig = await crypto.subtle.sign('RSASSA-PKCS1-v1_5', kp.privateKey, new TextEncoder().encode(`${h}.${p}`));
  return `${h}.${p}.${b64u(sig)}`;
}

// disparoGravado: dispatched_at da última aprovação no momento do envio (r1: o disparo é gravado antes do email).
type Mail = { url: string; body: Record<string, unknown>; auth: string | null; disparoGravado?: string };

// #82: GitHub falso. Cada rota responde com um valor: número (status HTTP), 'erro' (rede caída), 'pendente' (sem
// resposta até o prazo), 'corpo-pendente' (200 cujo corpo nunca termina), { status, body } (corpo cru) ou a lista
// de PRs (pulls) e de execuções (runs do publicar.yml, ci das execuções do head do PR).
type GhValor = number | 'erro' | 'pendente' | 'corpo-pendente' | { status: number; body: string };
// total: total_count da listagem (runs e ci), para simular uma página incompleta; sem ele, é o tamanho da lista.
type GhOpts = { dispatch?: GhValor; pulls?: GhValor | unknown[]; runs?: GhValor | unknown[]; ci?: GhValor | unknown[]; total?: number };
type GhCall = { method: string; path: string; query: Record<string, string>; body: unknown; auth: string | null; ua: string | null;
  accept: string | null; version: string | null };
const GH_BASE = '/repos/guifav/guifav.github.io';

/** Fetch falso que só termina pelo aborto do prazo. O timer de AbortSignal.timeout não segura o processo do Node
 * (unref), e a partir do Node 22.23 o prazo do node:test também não: sem o timer comum, o laço de eventos esvazia
 * antes do aborto e o teste sai como cancelado. Ele dura mais que o timeout do teste, então, sem o prazo no fetch,
 * o teste continua falhando por timeout. */
function segurarAteAbortar(signal: AbortSignal) {
  const segura = setTimeout(() => {}, 10_000);
  signal.addEventListener('abort', () => clearTimeout(segura), { once: true });
}

function setup(opts: { mailStatus?: number; mailThrows?: boolean; mailPendente?: boolean; failSql?: RegExp; notify?: boolean; site?: Record<string, string | number | 'erro' | 'pendente' | 'corpo-pendente' | 'gigante'>;
  gh?: GhOpts; token?: string } = {}) {
  const ghOpts: GhOpts = { ...(opts.gh ?? {}) };
  // Posts no site, mutável no meio do teste como o ghOpts.
  const siteOpts: Record<string, string | number | 'erro' | 'pendente' | 'corpo-pendente' | 'gigante'> = { ...(opts.site ?? {}) };
  const gh: GhCall[] = [];
  const relogio = { t: NOW };
  // r1 (Astra e Kimi P2): quantos pedaços do corpo o painel puxou e se cancelou o resto.
  const corpo = { puxados: 0, cancelado: false };
  const mails: Mail[] = [];
  const other: string[] = [];
  // #80: posts publicados no site, por slug: HTML (200), status HTTP ou 'erro' (falha de rede). Sem entrada, 404.
  const site: string[] = [];
  const fetchFn = async (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input instanceof Request ? input.url : input);
    if (url === `https://${TEAM}/cdn-cgi/access/certs`) return Response.json({ keys: [jwk] });
    if (url === 'https://api.resend.com/emails') {
      const ultima = db.raw.prepare('SELECT dispatched_at FROM draft_approvals ORDER BY id DESC LIMIT 1').get() as { dispatched_at: string } | undefined;
      if (opts.mailThrows) throw new TypeError('network');
      if (opts.mailPendente) {
        assert.ok(init?.signal, 'aviso por email com prazo');
        segurarAteAbortar(init!.signal!);
        return new Promise<Response>((_, rej) => init!.signal!.addEventListener('abort', () => rej(new DOMException('prazo', 'TimeoutError'))));
      }
      mails.push({ url, body: JSON.parse(String(init?.body)), auth: new Headers(init?.headers).get('authorization'), disparoGravado: ultima?.dispatched_at });
      return Response.json({ id: 'e1' }, { status: opts.mailStatus ?? 200 });
    }
    if (url.startsWith('https://api.github.com/')) {
      const u = new URL(url);
      const h = new Headers(init?.headers);
      const method = init?.method ?? 'GET';
      gh.push({ method, path: u.pathname, query: Object.fromEntries(u.searchParams), body: init?.body ? JSON.parse(String(init.body)) : undefined,
        auth: h.get('authorization'), ua: h.get('user-agent'), accept: h.get('accept'), version: h.get('x-github-api-version') });
      assert.ok(init?.signal, 'chamada ao GitHub com prazo');
      let v: GhValor | unknown[];
      let lista = false;
      if (method === 'POST' && u.pathname === `${GH_BASE}/actions/workflows/publicar.yml/dispatches`) v = ghOpts.dispatch ?? 204;
      else if (method === 'GET' && u.pathname === `${GH_BASE}/pulls`) v = ghOpts.pulls ?? [];
      else if (method === 'GET' && u.pathname === `${GH_BASE}/actions/workflows/publicar.yml/runs`) {
        // O filtro created do GitHub (>=X ou X..Y), aplicado às execuções com created_at (r2: a janela precisa valer no falso).
        v = ghOpts.runs ?? [];
        const c = u.searchParams.get('created');
        if (Array.isArray(v) && c) {
          const [de, ate] = c.startsWith('>=') ? [c.slice(2), ''] : c.split('..');
          v = v.filter((x) => {
            const t = (x as { created_at?: string }).created_at;
            return !t || (Date.parse(t) >= Date.parse(de) && (!ate || Date.parse(t) <= Date.parse(ate)));
          });
        }
        lista = true;
      }
      else if (method === 'GET' && u.pathname === `${GH_BASE}/actions/runs`) [v, lista] = [ghOpts.ci ?? [], true];
      else {
        other.push(url);
        return Response.json({ message: 'rota inesperada' }, { status: 500 });
      }
      const abortou = () => new DOMException('prazo', 'TimeoutError');
      if (v === 'erro') throw new TypeError('network');
      if (v === 'pendente' || v === 'corpo-pendente') segurarAteAbortar(init!.signal!);
      if (v === 'pendente') return new Promise<Response>((_, rej) => init!.signal!.addEventListener('abort', () => rej(abortou())));
      if (v === 'corpo-pendente') {
        return new Response(new ReadableStream<Uint8Array>({
          start(c) {
            c.enqueue(new TextEncoder().encode('['));
            init!.signal!.addEventListener('abort', () => c.error(abortou()));
          },
        }), { status: 200 });
      }
      if (typeof v === 'number') return v === 204 ? new Response(null, { status: 204 }) : Response.json({ message: 'falhou' }, { status: v });
      if (Array.isArray(v)) return Response.json(lista ? { total_count: ghOpts.total ?? v.length, workflow_runs: v } : v);
      return new Response(v.body, { status: v.status });
    }
    const post = url.match(/^https:\/\/guifav\.dev\/blog\/([^/]+)\/$/);
    if (post) {
      site.push(url);
      assert.equal(init?.redirect, 'manual', 'consulta ao site sem seguir redirecionamento');
      assert.ok(init?.signal, 'consulta ao site com prazo');
      const v = siteOpts[post[1]];
      if (v === 'erro') throw new TypeError('network');
      // r1 (P3): sem resposta até o prazo, ou resposta 200 cujo corpo nunca termina; só o aborto encerra.
      const abortou = () => new DOMException('prazo', 'TimeoutError');
      if (v === 'pendente' || v === 'corpo-pendente') segurarAteAbortar(init!.signal!);
      if (v === 'pendente') {
        return new Promise<Response>((_, rej) => init!.signal!.addEventListener('abort', () => rej(abortou())));
      }
      if (v === 'corpo-pendente') {
        const body = new ReadableStream<Uint8Array>({
          start(c) {
            c.enqueue(new TextEncoder().encode('<html><head>'));
            init!.signal!.addEventListener('abort', () => c.error(abortou()));
          },
        });
        return new Response(body, { status: 200 });
      }
      if (v === 'gigante') {
        const pedaco = new TextEncoder().encode('<title>Gigante</title>'.padEnd(65536, ' '));
        const body = new ReadableStream<Uint8Array>({
          pull(c) {
            corpo.puxados++;
            if (corpo.puxados > 128) c.close();
            else c.enqueue(pedaco);
          },
          cancel() {
            corpo.cancelado = true;
          },
        }, { highWaterMark: 0 });
        return new Response(body, { status: 200 });
      }
      if (typeof v === 'number') return new Response('x', { status: v });
      if (typeof v === 'string') return new Response(v, { status: 200, headers: { 'content-type': 'text/html' } });
      return new Response('não encontrado', { status: 404 });
    }
    other.push(url);
    return Response.json({ message: 'rota inesperada' }, { status: 500 });
  };
  // r1 (Kimi P2): a falha do D1 pode ser ligada no meio do teste, para atingir só o "Disparar de novo".
  const falha: { sql?: RegExp } = { sql: opts.failSql };
  const db = d1(falha);
  const bucket = r2();
  const env: Env = { DB: db, RESEND_API_KEY: 're_test', SEGMENT_ID: 's', TOPIC_ID: 't', ACCESS_TEAM_DOMAIN: TEAM, ACCESS_AUD: AUD,
    ALLOWED_EMAILS: GUI, SITE_ORIGIN: ORIGIN, DRAFTS: bucket,
    DRAFT_NOTIFY_FROM: opts.notify === false ? '' : 'Painel <painel@guifav.dev>', DRAFT_NOTIFY_TO: opts.notify === false ? '' : 'roger@exemplo.com',
    GITHUB_DISPATCH_TOKEN: opts.token ?? 'gh-teste' };
  const deps: Deps = { fetch: fetchFn as typeof fetch, now: () => relogio.t, sleep: async () => {}, origin: async () => new Response('pages'),
    siteTimeoutMs: 50, githubTimeoutMs: 50, mailTimeoutMs: 50 };
  const req = async (path: string, init: { method?: string; body?: string; headers?: Record<string, string>; token?: string | null } = {}) => {
    resetCertsCache();
    const h: Record<string, string> = { ...(init.headers ?? {}) };
    if (init.token !== null) h['cf-access-jwt-assertion'] = init.token ?? (await jwt(GUI, relogio.t));
    return handle(new Request(`https://guifav.dev${path}`, { method: init.method ?? 'GET', body: init.body, headers: h }), env, deps);
  };
  const approve = async (slug: string, form: Record<string, string>, headers: Record<string, string> = {}) =>
    req(`/admin/rascunhos/${slug}/aprovar`, { method: 'POST', body: new URLSearchParams(form).toString(),
      headers: { origin: ORIGIN, 'content-type': 'application/x-www-form-urlencoded', ...headers } });
  const rows = () => JSON.parse(JSON.stringify(db.raw.prepare('SELECT * FROM draft_approvals ORDER BY id').all())) as Record<string, string>[];
  const avancar = (min: number) => { relogio.t += min * 60000; };
  return { env, deps, db, bucket, mails, other, site, siteOpts, corpo, req, approve, rows, gh, ghOpts, avancar, falha };
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
  // Com no-referrer o navegador manda Origin: null no POST do formulário e a aprovação dava 403 (bug real, 14/11).
  // same-origin manda o Origin do site no POST da própria página e nenhum referrer para fora.
  assert.equal(r.headers.get('referrer-policy'), 'same-origin');
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

test('R4 (#82): a fonte do rascunho (campo fonte, em v/<versão>/fonte/) nunca é servida; o manifesto com fonte continua válido', async () => {
  const s = setup();
  const fonte = { 'sales-coach.md': SHA('1'), 'img/a-728.webp': SHA('2') };
  const d = await putDraft(s.bucket, 'sales-coach', { manifest: { fonte } });
  s.bucket.store.set(`sales-coach/v/${d.version}/fonte/sales-coach.md`, enc('FONTE-MARKDOWN'));
  s.bucket.store.set(`sales-coach/v/${d.version}/fonte/img/a-728.webp`, enc('FONTE-IMAGEM'));
  // O manifesto é aceito como antes, e a fonte não entra no que o Worker conhece (files).
  const m = (await readManifest(s.bucket, 'sales-coach'))!;
  assert.ok(m, 'manifesto com fonte é válido');
  assert.equal('fonte' in m, false);
  assert.deepEqual(Object.keys(m.files), ['index.html', 'img/sales-coach/a-728.webp']);
  // Lista e prévia seguem iguais; a imagem da prévia continua servida.
  assert.match(await (await s.req('/admin/rascunhos')).text(), /sales-coach/);
  assert.equal((await s.req('/admin/rascunhos/sales-coach/')).status, 200);
  assert.equal((await s.req('/admin/rascunhos/sales-coach/img/sales-coach/a-728.webp')).status, 200);
  const before = s.bucket.gets.length;
  for (const p of ['fonte/sales-coach.md', 'fonte/img/a-728.webp', 'sales-coach.md', 'img/a-728.webp', `v/${d.version}/fonte/sales-coach.md`]) {
    const r = await s.req(`/admin/rascunhos/sales-coach/${p}`);
    assert.equal(r.status, 404, p);
    assert.doesNotMatch(await r.text(), /FONTE-/, p);
  }
  // Nenhum objeto da fonte foi sequer lido do R2.
  assert.deepEqual(s.bucket.gets.slice(before).filter((k) => k.includes('/fonte/')), []);
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
    source_sha256: SHA('a'), approved_at: '2026-11-13T20:00:00.000Z', approved_by: GUI, notified_at: '2026-11-13T20:00:00.000Z', notify_error: '',
    dispatched_at: '2026-11-13T20:00:00.000Z', dispatch_error: '' });
  assert.equal(s.mails.length, 1);
  assert.deepEqual(s.mails[0].body.to, ['roger@exemplo.com']);
  assert.equal(s.mails[0].body.from, 'Painel <painel@guifav.dev>');
  assert.equal(s.mails[0].body.subject, 'Rascunho aprovado, publicação disparada: Como construí o Sales Coach');
  assert.match(String(s.mails[0].body.text), new RegExp(`Versão aprovada: ${version}`));
  assert.match(String(s.mails[0].body.text), new RegExp(`HTML aprovado \\(sha256\\): ${hash}`));
  assert.match(String(s.mails[0].body.text), new RegExp(`Fonte aprovada \\(sha256\\): ${SHA('a')}`));
  assert.equal(s.mails[0].auth, 'Bearer re_test');
  const prev = await (await s.req('/admin/rascunhos/sales-coach/')).text();
  assert.match(prev, /Aprovado em 2026-11-13 20:00 UTC\. O estado da publicação está na lista de rascunhos\./);
  assert.doesNotMatch(prev, /Roger publica/);
  assert.doesNotMatch(prev, /name="confirmar"/);
  const list = await (await s.req('/admin/rascunhos?msg=aprovado')).text();
  assert.match(list, /aprovado em 2026-11-13 20:00 UTC/);
  assert.match(list, /A publicação foi disparada no GitHub e o Roger foi avisado por email/);
  assert.doesNotMatch(list, /Roger publica/);
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
  assert.deepEqual(s.gh, []);
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
  assert.deepEqual(s.gh, [], 'aprovação não gravada não dispara nada');
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
// ------------------------------------------------------------------ R7: versão LinkedIn (issue #73)
const LI = { kind: 'linkedin', parent: 'sales-coach' };

test('R7: readManifest aceita kind blog (padrão) e linkedin com parent; recusa kind desconhecido e parent fora do lugar', async () => {
  const s = setup();
  await putDraft(s.bucket);
  assert.equal((await readManifest(s.bucket, 'sales-coach'))!.kind, 'blog');
  assert.equal((await readManifest(s.bucket, 'sales-coach'))!.parent, null);
  await putDraft(s.bucket, 'sales-coach-linkedin', { manifest: LI });
  const li = (await readManifest(s.bucket, 'sales-coach-linkedin'))!;
  assert.equal(li.kind, 'linkedin');
  assert.equal(li.parent, 'sales-coach');
  for (const [name, extra] of [
    ['kind desconhecido', { kind: 'twitter' }],
    ['kind nulo (r1, Kimi e Astra)', { kind: null }],
    ['parent nulo num artigo', { kind: 'blog', parent: null }],
    ['blog com parent', { kind: 'blog', parent: 'sales-coach' }],
    ['parent sem kind', { parent: 'sales-coach' }],
    ['linkedin sem parent', { kind: 'linkedin' }],
    ['linkedin com parent inválido', { kind: 'linkedin', parent: 'Sales_Coach' }],
    ['linkedin com parent não string', { kind: 'linkedin', parent: 7 }],
    ['linkedin pai de si mesmo', { kind: 'linkedin', parent: 'outro' }],
  ] as const) {
    const b = setup().bucket;
    await putDraft(b, 'outro', { manifest: extra as Record<string, unknown> });
    assert.equal(await readManifest(b, 'outro'), null, name);
  }
});

test('R7: a lista põe o LinkedIn logo abaixo do artigo, com o estado vazio; LinkedIn sem artigo aparece sozinho', async () => {
  const s = setup();
  await putDraft(s.bucket, 'aaa-outro', { title: 'Outro artigo' });
  await putDraft(s.bucket, 'sales-coach');
  await putDraft(s.bucket, 'sales-coach-linkedin', { title: 'Sales Coach no LinkedIn', manifest: LI });
  await putDraft(s.bucket, 'solto-linkedin', { title: 'LinkedIn solto', manifest: { kind: 'linkedin', parent: 'nao-existe' } });
  const body = await (await s.req('/admin/rascunhos')).text();
  const rows = body.split('<tr').slice(2).map((r) => r);
  const idx = (re: RegExp) => rows.findIndex((r) => re.test(r));
  const blog = idx(/href="\/admin\/rascunhos\/sales-coach\/"/);
  const li = idx(/href="\/admin\/rascunhos\/sales-coach-linkedin\/"/);
  assert.ok(blog >= 0 && li === blog + 1, `LinkedIn logo depois do artigo: ${blog} ${li}`);
  assert.match(rows[li], /LinkedIn/);
  assert.match(rows[li], /<td><\/td><\/tr>/, 'estado vazio: a prévia é que diz só leitura (#92)');
  assert.doesNotMatch(rows[li], /aguardando aprovação/);
  assert.match(rows[li], /class="linkedin"/);
  const solto = idx(/solto-linkedin/);
  assert.ok(solto >= 0);
  assert.match(rows[solto], /artigo não encontrado/);
  assert.equal(rows.filter((r) => /sales-coach-linkedin/.test(r)).length, 1, 'aparece uma vez só');
});

test('R7 (r1, Kimi P2): dois LinkedIn do mesmo artigo ficam os dois abaixo dele; título escapado; artigo com HTML que não confere deixa o LinkedIn sozinho', async () => {
  const s = setup();
  await putDraft(s.bucket, 'sales-coach');
  await putDraft(s.bucket, 'sales-coach-li-a', { title: 'LI <a> & "b"', manifest: LI });
  await putDraft(s.bucket, 'sales-coach-li-b', { title: 'LI dois', manifest: LI });
  const quebrado = await putDraft(s.bucket, 'pai-quebrado');
  s.bucket.store.set(`pai-quebrado/v/${quebrado.version}/index.html`, enc('<html>outro HTML</html>'));
  await putDraft(s.bucket, 'filho-do-quebrado', { title: 'Filho', manifest: { kind: 'linkedin', parent: 'pai-quebrado' } });
  const body = await (await s.req('/admin/rascunhos')).text();
  const rows = body.split('<tr').slice(2);
  const at = (slug: string) => rows.findIndex((r) => r.includes(`href="/admin/rascunhos/${slug}/"`));
  const blog = at('sales-coach');
  assert.deepEqual([at('sales-coach-li-a'), at('sales-coach-li-b')].sort(), [blog + 1, blog + 2]);
  assert.match(body, /LI &lt;a&gt; &amp; &quot;b&quot;/);
  assert.doesNotMatch(body, /LI <a>/);
  assert.equal(at('pai-quebrado'), -1);
  assert.match(rows[at('filho-do-quebrado')], /artigo não encontrado/);
});

test('R7: a prévia do LinkedIn é só leitura, sem formulário, com link para o artigo; aprovar dá 400 e nada é gravado', async () => {
  const s = setup();
  await putDraft(s.bucket, 'sales-coach');
  const d = await putDraft(s.bucket, 'sales-coach-linkedin', { manifest: LI });
  const r = await s.req('/admin/rascunhos/sales-coach-linkedin/');
  assert.equal(r.status, 200);
  const html = await r.text();
  assert.match(html, /só leitura/);
  assert.match(html, /href="\/admin\/rascunhos\/sales-coach\/"/);
  assert.doesNotMatch(html, /<form/);
  assert.doesNotMatch(html, /Aprovar publicação/);
  const a = await s.approve('sales-coach-linkedin', { confirmar: 'sim', versao: d.version });
  assert.equal(a.status, 400);
  assert.match(await a.text(), /LinkedIn/);
  assert.deepEqual(s.rows(), []);
  assert.deepEqual(s.mails, []);
  // O artigo continua aprovável.
  const blog = await s.req('/admin/rascunhos/sales-coach/');
  assert.match(await blog.text(), /Aprovar publicação/);
});

// ------------------------------------------------------------------ R8: outros derivados do artigo, com rótulo (issue #77)
const RS = { kind: 'derivado', parent: 'sales-coach', rotulo: 'Roteiro do short' };

test('R8: readManifest aceita derivado com parent e rótulo; recusa rótulo ausente, inválido ou fora do lugar', async () => {
  const s = setup();
  await putDraft(s.bucket, 'sales-coach-short', { manifest: RS });
  const m = (await readManifest(s.bucket, 'sales-coach-short'))!;
  assert.deepEqual([m.kind, m.parent, m.rotulo], ['derivado', 'sales-coach', 'Roteiro do short']);
  await putDraft(s.bucket, 'sales-coach-linkedin', { manifest: LI });
  assert.equal((await readManifest(s.bucket, 'sales-coach-linkedin'))!.rotulo, 'LinkedIn');
  await putDraft(s.bucket, 'sales-coach');
  assert.equal((await readManifest(s.bucket, 'sales-coach'))!.rotulo, null);
  assert.equal((await readManifest(s.bucket, 'sales-coach-short'))!.kind, 'derivado');
  for (const [name, extra] of [
    ['derivado sem rótulo', { kind: 'derivado', parent: 'sales-coach' }],
    ['derivado sem parent', { kind: 'derivado', rotulo: 'Roteiro' }],
    ['rótulo vazio', { ...RS, rotulo: '' }],
    ['rótulo com 41 caracteres', { ...RS, rotulo: 'x'.repeat(41) }],
    ['rótulo não string', { ...RS, rotulo: 7 }],
    ['rótulo com quebra de linha', { ...RS, rotulo: 'Roteiro\ndo short' }],
    ['rótulo com controle C1', { ...RS, rotulo: 'Roteiro\u0085' }],
    ['rótulo com espaço na ponta', { ...RS, rotulo: ' Roteiro' }],
    ['rótulo com espaço não separável na ponta', { ...RS, rotulo: 'Roteiro ' }],
    ['rótulo com BOM na ponta', { ...RS, rotulo: '﻿Roteiro' }],
    ['rótulo num artigo', { kind: 'blog', rotulo: 'Roteiro' }],
    ['rótulo no linkedin', { ...LI, rotulo: 'Roteiro' }],
  ] as const) {
    const b = setup().bucket;
    await putDraft(b, 'outro', { manifest: extra as Record<string, unknown> });
    assert.equal(await readManifest(b, 'outro'), null, name);
  }
  // 40 pontos de código passam, mesmo fora do plano básico; espaço no meio passa.
  const b = setup().bucket;
  await putDraft(b, 'outro', { manifest: { ...RS, rotulo: '🎬'.repeat(40) } });
  assert.ok(await readManifest(b, 'outro'));
  await putDraft(b, 'outro', { manifest: { ...RS, rotulo: 'Vídeo do short (vertical)' } });
  assert.ok(await readManifest(b, 'outro'));
});

test('R8: LinkedIn e derivados ficam todos abaixo do artigo, com o rótulo escapado; derivado sem artigo aparece sozinho', async () => {
  const s = setup();
  await putDraft(s.bucket, 'sales-coach');
  await putDraft(s.bucket, 'sales-coach-linkedin', { manifest: LI });
  await putDraft(s.bucket, 'sales-coach-short', { title: 'Roteiro', manifest: RS });
  await putDraft(s.bucket, 'sales-coach-video', { title: 'Vídeo', manifest: { ...RS, rotulo: 'Vídeo <b> & "x"' } });
  await putDraft(s.bucket, 'solto-short', { title: 'Solto', manifest: { ...RS, parent: 'nao-existe' } });
  const body = await (await s.req('/admin/rascunhos')).text();
  const rows = body.split('<tr').slice(2);
  const at = (slug: string) => rows.findIndex((r) => r.includes(`href="/admin/rascunhos/${slug}/"`));
  const blog = at('sales-coach');
  assert.deepEqual([at('sales-coach-linkedin'), at('sales-coach-short'), at('sales-coach-video')].sort(), [blog + 1, blog + 2, blog + 3]);
  assert.match(rows[at('sales-coach-short')], /<span class="tag">Roteiro do short<\/span>/);
  assert.match(rows[at('sales-coach-short')], /class="derivado"/);
  assert.match(rows[at('sales-coach-short')], /<td><\/td><\/tr>/);
  assert.match(rows[at('sales-coach-video')], /Vídeo &lt;b&gt; &amp; &quot;x&quot;/);
  assert.doesNotMatch(body, /Vídeo <b>/);
  assert.match(rows[at('sales-coach-linkedin')], /class="linkedin"/);
  assert.match(rows[at('solto-short')], /artigo não encontrado/);
});

test('R8: a prévia do derivado é só leitura, com o rótulo na barra; aprovar dá 400 e nada é gravado', async () => {
  const s = setup();
  await putDraft(s.bucket, 'sales-coach');
  const d = await putDraft(s.bucket, 'sales-coach-short', { manifest: { ...RS, rotulo: 'Roteiro <i>' } });
  const r = await s.req('/admin/rascunhos/sales-coach-short/');
  assert.equal(r.status, 200);
  const html = await r.text();
  assert.match(html, /<strong>Roteiro &lt;i&gt;<\/strong><span>só leitura, sem aprovação<\/span>/);
  assert.match(html, /href="\/admin\/rascunhos\/sales-coach\/"/);
  assert.doesNotMatch(html, /<form/);
  const a = await s.approve('sales-coach-short', { confirmar: 'sim', versao: d.version });
  assert.equal(a.status, 400);
  assert.match(await a.text(), /só leitura/);
  assert.deepEqual(s.rows(), []);
  assert.deepEqual(s.mails, []);
});

// ------------------------------------------------------------------ R9: derivados de artigo já publicado (issue #80)
const pagina = (title: string) => `<!doctype html><html><head><title>${title}</title></head><body>post</body></html>`;

test('R9: rascunho do artigo fora do R2 e post no site: linha "publicado: ver o post" com os derivados abaixo, título escapado', async () => {
  const s = setup({ site: { 'sales-coach': pagina('Coach &amp; vendas &lt;b&gt; | Guilherme Favaron') } });
  await putDraft(s.bucket, 'aaa-outro', { title: 'Outro artigo' });
  await putDraft(s.bucket, 'sales-coach-linkedin', { title: 'No LinkedIn', manifest: LI });
  await putDraft(s.bucket, 'sales-coach-short', { title: 'Roteiro', manifest: RS });
  const body = await (await s.req('/admin/rascunhos')).text();
  const rows = body.split('<tr').slice(2);
  const at = (re: RegExp) => rows.findIndex((r) => re.test(r));
  const pai = at(/class="publicado"/);
  assert.ok(pai >= 0, 'linha do artigo publicado');
  assert.match(rows[pai], /<a href="https:\/\/guifav\.dev\/blog\/sales-coach\/">Coach &amp; vendas &lt;b&gt;<\/a>/);
  assert.match(rows[pai], /<td>publicado: <a href="https:\/\/guifav\.dev\/blog\/sales-coach\/">ver o post<\/a><\/td>/);
  assert.doesNotMatch(body, /publicado no site|tr\.publicado td\{color/, 'sem o verde nem o texto antigo (#92)');
  assert.doesNotMatch(rows[pai], /Guilherme Favaron/);
  assert.deepEqual([at(/sales-coach-linkedin/), at(/sales-coach-short/)].sort(), [pai + 1, pai + 2]);
  assert.doesNotMatch(body, /artigo não encontrado/);
  assert.deepEqual(s.site, ['https://guifav.dev/blog/sales-coach/'], 'uma consulta ao site por artigo pai');
  assert.match(body, /Os derivados de cada artigo \(LinkedIn, roteiros\) ficam logo abaixo dele, também depois da publicação\./);
});

test('R9: pai que não é rascunho nem post no ar continua sozinho, sem quebrar a lista (404, redirecionamento, erro de rede)', async () => {
  for (const v of [404, 301, 500, 'erro'] as const) {
    const s = setup({ site: { 'sales-coach': v } });
    await putDraft(s.bucket, 'sales-coach-short', { title: 'Roteiro', manifest: RS });
    const r = await s.req('/admin/rascunhos');
    assert.equal(r.status, 200, String(v));
    const body = await r.text();
    assert.match(body, /artigo não encontrado/, String(v));
    assert.doesNotMatch(body, /class="publicado"/, String(v));
  }
});

test('R9: post sem <title> usa o slug; com o rascunho do artigo ainda no R2, o site não é consultado', async () => {
  const s = setup({ site: { 'sales-coach': '<html><body>sem título</body></html>' } });
  await putDraft(s.bucket, 'sales-coach-short', { manifest: RS });
  const body = await (await s.req('/admin/rascunhos')).text();
  assert.match(body, /<a href="https:\/\/guifav\.dev\/blog\/sales-coach\/">sales-coach<\/a>/);
  const t = setup({ site: { 'sales-coach': pagina('x') } });
  await putDraft(t.bucket, 'sales-coach');
  await putDraft(t.bucket, 'sales-coach-short', { manifest: RS });
  await t.req('/admin/rascunhos');
  await t.req('/admin/rascunhos/sales-coach-short/');
  assert.deepEqual(t.site, []);
});

test('R9: a prévia do derivado de artigo publicado leva ao post no site; sem post, mantém o link do rascunho', async () => {
  const s = setup({ site: { 'sales-coach': pagina('Coach') } });
  await putDraft(s.bucket, 'sales-coach-short', { manifest: RS });
  const html = await (await s.req('/admin/rascunhos/sales-coach-short/')).text();
  assert.match(html, /<a href="https:\/\/guifav\.dev\/blog\/sales-coach\/" style="color:#fff">artigo no site<\/a>/);
  const t = setup();
  await putDraft(t.bucket, 'sales-coach-short', { manifest: RS });
  const h2 = await (await t.req('/admin/rascunhos/sales-coach-short/')).text();
  assert.match(h2, /<a href="\/admin\/rascunhos\/sales-coach\/" style="color:#fff">artigo do blog<\/a>/);
});

// timeout do teste: sem o prazo no fetch, a consulta nunca termina, e o teste falha em vez de travar a suíte.
test('R9 (r1, Astra e Kimi P3): prazo esgotado na resposta ou no meio do corpo deixa o derivado sozinho, sem quebrar a lista', { timeout: 3000 }, async () => {
  for (const v of ['pendente', 'corpo-pendente'] as const) {
    const s = setup({ site: { 'sales-coach': v } });
    await putDraft(s.bucket, 'sales-coach-short', { manifest: RS });
    const r = await s.req('/admin/rascunhos');
    assert.equal(r.status, 200, v);
    const body = await r.text();
    assert.match(body, /artigo não encontrado/, v);
    assert.doesNotMatch(body, /class="publicado"/, v);
  }
});

test('R9 (r1, Astra e Kimi P2): página enorme é lida só até 64 KiB e o resto do corpo é cancelado', async () => {
  const s = setup({ site: { 'sales-coach': 'gigante' } });
  await putDraft(s.bucket, 'sales-coach-short', { manifest: RS });
  const body = await (await s.req('/admin/rascunhos')).text();
  assert.match(body, /<a href="https:\/\/guifav\.dev\/blog\/sales-coach\/">Gigante<\/a>/);
  assert.ok(s.corpo.puxados <= 2, `pedaços puxados: ${s.corpo.puxados}`);
  assert.equal(s.corpo.cancelado, true);
});

test('R6: wrangler.toml liga o bucket privado e a migração cria a tabela de aprovações', () => {
  assert.match(TOML, /\[\[r2_buckets\]\]\nbinding = "DRAFTS"\nbucket_name = "blog-rascunhos"/);
  assert.doesNotMatch(TOML, /(DRAFT_NOTIFY_(FROM|TO)|GITHUB_DISPATCH_TOKEN)\s*=/); // segredos, não variáveis do repo
  assert.match(TOML, /^#\s+GITHUB_DISPATCH_TOKEN\s/m, 'o segredo do disparo está na lista de segredos do wrangler.toml');
  const disparo = readFileSync(new URL('../migrations/0003_draft_dispatch.sql', import.meta.url), 'utf-8');
  for (const col of ['dispatched_at', 'dispatch_error']) {
    assert.match(disparo, new RegExp(`ALTER TABLE draft_approvals ADD COLUMN ${col} TEXT NOT NULL DEFAULT ''`), col);
  }
  const sql = readFileSync(new URL('../migrations/0002_draft_approvals.sql', import.meta.url), 'utf-8');
  for (const col of ['slug', 'title', 'html_sha256', 'source_sha256', 'approved_at', 'approved_by', 'notified_at', 'notify_error']) {
    assert.match(sql, new RegExp(`\\b${col} TEXT NOT NULL`), col);
  }
});

// ------------------------------------------------------------------ R10: publicação disparada no GitHub (issue #82, fatia 4)
const linhaDe = (body: string, slug: string) => body.split('<tr').find((r) => r.includes(`href="/admin/rascunhos/${slug}/"`)) ?? '';
const disparos = (s: ReturnType<typeof setup>) => s.gh.filter((c) => c.method === 'POST');
const leituras = (s: ReturnType<typeof setup>) => s.gh.filter((c) => c.method === 'GET');
const pr = (n: number, state: 'open' | 'closed', merged_at: string | null, ref: string, sha = 'b'.repeat(40), base = 'main') =>
  ({ number: n, state, merged_at, head: { ref, sha }, base: { ref: base } });
const exec = (id: number, display_title: string, status: string, conclusion: string | null = null, workflow_id?: number) =>
  ({ id, display_title, status, conclusion, ...(workflow_id ? { workflow_id } : {}) });

test('R10: aprovar dispara o publicar.yml na main com slug e versão, grava o disparo e o email avisa que a publicação foi disparada', async () => {
  const s = setup();
  const { version } = await putDraft(s.bucket);
  const r = await s.approve('sales-coach', { confirmar: 'sim', versao: version });
  assert.equal(r.headers.get('location'), '/admin/rascunhos?msg=aprovado');
  const [d] = disparos(s);
  assert.equal(disparos(s).length, 1);
  assert.equal(d.path, `${GH_BASE}/actions/workflows/publicar.yml/dispatches`);
  assert.deepEqual(d.body, { ref: 'main', inputs: { slug: 'sales-coach', versao: version } });
  assert.equal(d.auth, 'Bearer gh-teste');
  assert.equal(d.accept, 'application/vnd.github+json');
  assert.equal(d.version, '2022-11-28');
  assert.ok(d.ua, 'User-Agent, exigido pela API do GitHub');
  const [row] = s.rows();
  assert.equal(row.dispatched_at, '2026-11-13T20:00:00.000Z');
  assert.equal(row.dispatch_error, '');
  const texto = String(s.mails[0].body.text);
  assert.match(texto, /A publicação foi disparada no GitHub \(publicar\.yml\)/);
  assert.ok(texto.includes(`publica/sales-coach-${version.slice(0, 8)}`), 'email com o branch da versão');
  assert.doesNotMatch(texto, /gh-teste/, 'o token nunca vai no email');
  // A lista mostra o estado logo depois do redirecionamento: disparado, sem execução ainda.
  const list = await (await s.req('/admin/rascunhos?msg=aprovado')).text();
  assert.match(list, /<p class="aviso ok" role="status">Aprovado\. A publicação foi disparada no GitHub/);
  assert.match(linhaDe(list, 'sales-coach'), /aprovado em 2026-11-13 20:00 UTC<br>preparando o PR \(disparado há menos de 1 min\)/);
  assert.doesNotMatch(list, /Roger publica/);
});

test('R10: disparo recusado pelo GitHub: motivo gravado e mostrado na hora, escapado, com o botão "Disparar de novo"; o email avisa a falha', async () => {
  const s = setup({ gh: { dispatch: { status: 422, body: JSON.stringify({ message: 'Workflow <b> não\n aceita disparo' }) } } });
  const { version } = await putDraft(s.bucket);
  const r = await s.approve('sales-coach', { confirmar: 'sim', versao: version });
  assert.equal(r.status, 303, 'a aprovação continua valendo');
  assert.equal(r.headers.get('location'), '/admin/rascunhos?msg=aprovado-sem-disparo');
  const [row] = s.rows();
  assert.equal(row.dispatch_error, 'GitHub respondeu 422: Workflow <b> não aceita disparo');
  assert.equal(row.dispatched_at, '');
  assert.equal(row.notify_error, '');
  assert.equal(s.mails[0].body.subject, 'Rascunho aprovado, disparo falhou: Como construí o Sales Coach');
  assert.match(String(s.mails[0].body.text), /O disparo da publicação no GitHub falhou \(GitHub respondeu 422: Workflow <b> não aceita disparo\)/);
  const list = await (await s.req('/admin/rascunhos?msg=aprovado-sem-disparo')).text();
  assert.match(list, /<p class="aviso erro" role="status">Aprovado e gravado, mas o disparo da publicação falhou/);
  const linha = linhaDe(list, 'sales-coach');
  assert.match(linha, /disparo falhou \(GitHub respondeu 422: Workflow &lt;b&gt; não aceita disparo\)/);
  assert.ok(linha.includes(`<form method="post" action="/admin/rascunhos/sales-coach/disparar"`), 'botão Disparar de novo');
  assert.ok(linha.includes(`<input type="hidden" name="versao" value="${version}"><button>Disparar de novo</button>`));
  // Com o GitHub fora do ar na hora da lista, o motivo do disparo continua visível, com o botão.
  s.ghOpts.pulls = 503;
  const fora = linhaDe(await (await s.req('/admin/rascunhos')).text(), 'sales-coach');
  assert.match(fora, /disparo falhou \(GitHub respondeu 422/);
  assert.match(fora, /Disparar de novo/);
  // Corpo que não é JSON: fica só o status.
  const t = setup({ gh: { dispatch: { status: 401, body: '<html>nao json</html>' } } });
  const v2 = (await putDraft(t.bucket)).version;
  await t.approve('sales-coach', { confirmar: 'sim', versao: v2 });
  assert.equal(t.rows()[0].dispatch_error, 'GitHub respondeu 401');
});

test('R10: rede caída e prazo esgotado no disparo ficam gravados como motivo, sem travar a aprovação', { timeout: 3000 }, async () => {
  for (const [v, why] of [['erro', 'falha de rede'], ['pendente', 'tempo esgotado']] as const) {
    const s = setup({ gh: { dispatch: v } });
    const { version } = await putDraft(s.bucket);
    const r = await s.approve('sales-coach', { confirmar: 'sim', versao: version });
    assert.equal(r.headers.get('location'), '/admin/rascunhos?msg=aprovado-sem-disparo', v);
    assert.equal(s.rows()[0].dispatch_error, why, v);
    assert.equal(s.mails.length, 1, `${v}: o email sai mesmo assim`);
    assert.match(linhaDe(await (await s.req('/admin/rascunhos')).text(), 'sales-coach'), new RegExp(`disparo falhou \\(${why}\\)`), v);
  }
});

test('R10: disparo e email falhando juntos: mensagem própria, os dois motivos gravados', async () => {
  const s = setup({ gh: { dispatch: 500 }, mailStatus: 500 });
  const { version } = await putDraft(s.bucket);
  const r = await s.approve('sales-coach', { confirmar: 'sim', versao: version });
  assert.equal(r.headers.get('location'), '/admin/rascunhos?msg=aprovado-sem-disparo-nem-aviso');
  assert.deepEqual([s.rows()[0].dispatch_error, s.rows()[0].notify_error], ['GitHub respondeu 500: falhou', 'Resend respondeu 500']);
  assert.match(await (await s.req('/admin/rascunhos?msg=aprovado-sem-disparo-nem-aviso')).text(), /<p class="aviso erro" role="status">.*o disparo da publicação e o aviso por email ao Roger falharam/);
});

test('R10: sem o token, nada vai ao GitHub: "disparo sem configuração" fica gravado e aparece na lista', async () => {
  const s = setup({ token: '' });
  const { version } = await putDraft(s.bucket);
  await s.approve('sales-coach', { confirmar: 'sim', versao: version });
  assert.equal(s.rows()[0].dispatch_error, 'disparo sem configuração');
  const linha = linhaDe(await (await s.req('/admin/rascunhos')).text(), 'sales-coach');
  assert.match(linha, /disparo falhou \(disparo sem configuração\)/);
  assert.deepEqual(s.gh, []);
});

test('R10: derivado (LinkedIn ou com rótulo) não dispara nada, nem na aprovação nem no "Disparar de novo"', async () => {
  const s = setup();
  await putDraft(s.bucket, 'sales-coach');
  const li = await putDraft(s.bucket, 'sales-coach-linkedin', { manifest: LI });
  const rs = await putDraft(s.bucket, 'sales-coach-short', { manifest: RS });
  for (const [slug, v] of [['sales-coach-linkedin', li.version], ['sales-coach-short', rs.version]] as const) {
    assert.equal((await s.approve(slug, { confirmar: 'sim', versao: v })).status, 400, slug);
    const r = await s.req(`/admin/rascunhos/${slug}/disparar`, { method: 'POST', body: `versao=${v}`,
      headers: { origin: ORIGIN, 'content-type': 'application/x-www-form-urlencoded' } });
    assert.equal(r.status, 400, `${slug}: disparar`);
    assert.match(await r.text(), /Nada foi disparado/);
  }
  assert.deepEqual(s.gh, []);
  assert.deepEqual(s.rows(), []);
});

const disparar = (s: ReturnType<typeof setup>, slug: string, form: Record<string, string>, headers: Record<string, string> = {}) =>
  s.req(`/admin/rascunhos/${slug}/disparar`, { method: 'POST', body: new URLSearchParams(form).toString(),
    headers: { origin: ORIGIN, 'content-type': 'application/x-www-form-urlencoded', ...headers } });

test('R10: "Disparar de novo" repete o disparo da versão aprovada, limpa o motivo e não manda outro email; nova falha atualiza o motivo', async () => {
  const s = setup({ gh: { dispatch: 500 } });
  const { version } = await putDraft(s.bucket);
  await s.approve('sales-coach', { confirmar: 'sim', versao: version });
  assert.equal(s.rows()[0].dispatch_error, 'GitHub respondeu 500: falhou');
  s.ghOpts.dispatch = 204;
  s.avancar(2);
  const r = await disparar(s, 'sales-coach', { versao: version });
  assert.equal(r.status, 303);
  assert.equal(r.headers.get('location'), '/admin/rascunhos?msg=disparado');
  assert.equal(disparos(s).length, 2);
  assert.deepEqual(disparos(s)[1].body, { ref: 'main', inputs: { slug: 'sales-coach', versao: version } });
  assert.deepEqual([s.rows()[0].dispatch_error, s.rows()[0].dispatched_at], ['', '2026-11-13T20:02:00.000Z']);
  assert.equal(s.rows().length, 1, 'o disparo de novo não cria outra aprovação');
  assert.equal(s.mails.length, 1, 'sem email no disparo de novo');
  const list = await (await s.req('/admin/rascunhos?msg=disparado')).text();
  assert.match(list, /<p class="aviso ok" role="status">Publicação disparada de novo no GitHub\.<\/p>/);
  assert.match(linhaDe(list, 'sales-coach'), /preparando o PR/);
  assert.doesNotMatch(linhaDe(list, 'sales-coach'), /Disparar de novo/);
  s.ghOpts.dispatch = 'erro';
  const f = await disparar(s, 'sales-coach', { versao: version });
  assert.equal(f.headers.get('location'), '/admin/rascunhos?msg=disparo-falhou');
  assert.deepEqual([s.rows()[0].dispatch_error, s.rows()[0].dispatched_at], ['falha de rede', '']);
  assert.match(await (await s.req('/admin/rascunhos?msg=disparo-falhou')).text(), /<p class="aviso erro" role="status">O disparo da publicação falhou de novo/);
});

test('R10: "Disparar de novo" é protegido como as outras ações POST: nada é disparado nem gravado fora da versão aprovada', async () => {
  const s = setup({ gh: { dispatch: 500 } });
  const { version, htmlSha } = await putDraft(s.bucket);
  await s.approve('sales-coach', { confirmar: 'sim', versao: version });
  await putDraft(s.bucket, 'outro-post', { title: 'Sem aprovação' });
  const outro = (await readManifest(s.bucket, 'outro-post'))!.version;
  const antes = JSON.stringify(s.rows());
  const n = s.gh.length;
  const cases: [string, Promise<Response>, number][] = [
    ['outra origem', disparar(s, 'sales-coach', { versao: version }, { origin: 'https://evil.example' }), 403],
    ['sem origem', s.req('/admin/rascunhos/sales-coach/disparar', { method: 'POST', body: `versao=${version}`,
      headers: { 'content-type': 'application/x-www-form-urlencoded' } }), 403],
    ['sem login', s.req('/admin/rascunhos/sales-coach/disparar', { method: 'POST', token: null, body: `versao=${version}`,
      headers: { origin: ORIGIN, 'content-type': 'application/x-www-form-urlencoded' } }), 403],
    ['json', disparar(s, 'sales-coach', { versao: version }, { 'content-type': 'application/json' }), 415],
    ['grande', disparar(s, 'sales-coach', { versao: version, x: 'a'.repeat(3000) }), 413],
    ['versão velha', disparar(s, 'sales-coach', { versao: SHA('b') }), 409],
    ['hash do HTML', disparar(s, 'sales-coach', { versao: htmlSha }), 409],
    ['sem versão', disparar(s, 'sales-coach', {}), 409],
    ['versão não aprovada', disparar(s, 'outro-post', { versao: outro }), 409],
    ['slug desconhecido', disparar(s, 'nao-existe', { versao: version }), 404],
    ['GET', s.req('/admin/rascunhos/sales-coach/disparar'), 405],
  ];
  for (const [name, p, status] of cases) assert.equal((await p).status, status, name);
  assert.equal(s.gh.length, n, 'nenhuma chamada ao GitHub');
  assert.equal(JSON.stringify(s.rows()), antes);
  // Versão nova depois da aprovação: a aprovação antiga não serve para disparar a nova.
  const nova = (await putDraft(s.bucket, 'sales-coach', { title: 'Versão nova' })).version;
  assert.equal((await disparar(s, 'sales-coach', { versao: nova })).status, 409);
  assert.equal(s.gh.length, n);
  // Mensagem desconhecida (até nome de propriedade herdada) não aparece nem quebra a lista.
  const lista = await s.req('/admin/rascunhos?msg=__proto__');
  assert.equal(lista.status, 200);
  assert.doesNotMatch(await lista.text(), /<p class="(ok|erro)">/);
});

test('R10: estados da publicação, calculados do D1 e do GitHub, com no máximo duas leituras do GitHub por rascunho aprovado', async () => {
  type Caso = { gh?: GhOpts; site?: Record<string, string | number>; min?: number; esperado: RegExp; botao: boolean; leituras: number; site_consultas?: number };
  const v = await versionId(await sha256Hex(HTML('Como construí o Sales Coach')), SHA('a'));
  const v8 = v.slice(0, 8);
  const ramo = `publica/sales-coach-${v8}`;
  const RUN = `Publica sales-coach (${v})`; // o run-name real do publicar.yml, com a versão inteira
  const execLink = '<a href="https://github.com/guifav/guifav.github.io/actions/runs/77">ver a execução</a>';
  const prLink = '<a href="https://github.com/guifav/guifav.github.io/pull/12">#12</a>';
  const casos: Record<string, Caso> = {
    'preparando, execução ainda não visível': { min: 3, esperado: /^preparando o PR \(disparado há 3 min\)$/, botao: false, leituras: 2 },
    'preparando, execução rodando': { min: 3, gh: { runs: [exec(77, RUN, 'in_progress')] },
      esperado: new RegExp(`^preparando o PR \\(disparado há 3 min\\): ${execLink}$`), botao: false, leituras: 2 },
    'preparando, execução na fila': { min: 1, gh: { runs: [exec(77, RUN, 'queued')] }, esperado: /^preparando o PR \(disparado há 1 min\): <a/, botao: false, leituras: 2 },
    'parado: nenhuma execução depois de 15 min': { min: 20, esperado: /^parado: nenhuma execução encontrada \(disparado há 20 min\)$/, botao: true, leituras: 2 },
    'parado há horas': { min: 125, esperado: /^parado: nenhuma execução encontrada \(disparado há 2 h\)$/, botao: true, leituras: 2 },
    'publicação recusada': { min: 4, gh: { runs: [exec(77, RUN, 'completed', 'failure')] }, esperado: new RegExp(`^publicação recusada: ${execLink}$`), botao: false, leituras: 2 },
    'execução sem PR': { min: 4, gh: { runs: [exec(77, RUN, 'completed', 'success')] }, esperado: new RegExp(`^execução terminou sem abrir o PR: ${execLink}$`), botao: true, leituras: 2 },
    'execução cancelada': { min: 4, gh: { runs: [exec(77, RUN, 'completed', 'cancelled')] }, esperado: new RegExp(`^execução interrompida \\(cancelled\\): ${execLink}$`), botao: true, leituras: 2 },
    'PR aberto, CI verde': { gh: { pulls: [pr(12, 'open', null, ramo)], ci: [exec(1, 'Pages', 'completed', 'success'), exec(2, 'x', 'completed', 'skipped')] },
      esperado: new RegExp(`^PR aberto ${prLink}, CI verde$`), botao: false, leituras: 2 },
    'PR aberto, CI vermelha': { gh: { pulls: [pr(12, 'open', null, ramo)], ci: [exec(1, 'Pages', 'completed', 'success'), exec(2, 'Pages', 'completed', 'failure')] },
      esperado: new RegExp(`^PR aberto ${prLink}, CI vermelha$`), botao: false, leituras: 2 },
    'PR aberto, CI rodando': { gh: { pulls: [pr(12, 'open', null, ramo)], ci: [exec(1, 'Pages', 'completed', 'success'), exec(2, 'Pages', 'in_progress')] },
      esperado: new RegExp(`^PR aberto ${prLink}, CI rodando$`), botao: false, leituras: 2 },
    'PR aberto, CI ainda não começou': { gh: { pulls: [pr(12, 'open', null, ramo)] }, esperado: new RegExp(`^PR aberto ${prLink}, CI pendente$`), botao: false, leituras: 2 },
    'PR aberto, CI indisponível': { gh: { pulls: [pr(12, 'open', null, ramo)], ci: 502 }, esperado: new RegExp(`^PR aberto ${prLink}, CI indisponível$`), botao: false, leituras: 2 },
    'PR aberto, CI verde depois de uma falha substituída no mesmo workflow (r1, Astra P2)': { gh: { pulls: [pr(12, 'open', null, ramo)],
      ci: [exec(9, 'Pages', 'completed', 'success', 100), exec(3, 'Pages', 'completed', 'failure', 100), exec(4, 'Outro', 'completed', 'success', 200)] },
      esperado: new RegExp(`^PR aberto ${prLink}, CI verde$`), botao: false, leituras: 2 },
    'PR aberto, reexecução de uma execução antiga, rodando (r2, Astra P2)': { gh: { pulls: [pr(12, 'open', null, ramo)],
      ci: [{ ...exec(20, 'Pages', 'completed', 'success', 100), run_started_at: '2026-11-13T20:10:00Z' },
        { ...exec(10, 'Pages', 'in_progress', null, 100), run_started_at: '2026-11-13T20:30:00Z' }] },
      esperado: new RegExp(`^PR aberto ${prLink}, CI rodando$`), botao: false, leituras: 2 },
    'PR aberto, reexecução de uma execução antiga que falhou': { gh: { pulls: [pr(12, 'open', null, ramo)],
      ci: [{ ...exec(20, 'Pages', 'completed', 'success', 100), run_started_at: '2026-11-13T20:10:00Z' },
        { ...exec(10, 'Pages', 'completed', 'failure', 100), run_started_at: '2026-11-13T20:30:00Z' }] },
      esperado: new RegExp(`^PR aberto ${prLink}, CI vermelha$`), botao: false, leituras: 2 },
    'PR aberto, CI vermelha quando a execução mais nova do workflow falhou': { gh: { pulls: [pr(12, 'open', null, ramo)],
      ci: [exec(3, 'Pages', 'completed', 'success', 100), exec(9, 'Pages', 'completed', 'failure', 100)] },
      esperado: new RegExp(`^PR aberto ${prLink}, CI vermelha$`), botao: false, leituras: 2 },
    // Caso real do teste de ponta a ponta (#82, PR #87): o pages.yml disparado pelo publicar.yml passou, e a execução
    // mais nova do mesmo workflow, pelo evento do PR aberto pelo bot, ficou esperando aprovação sem rodar.
    'PR aberto, CI verde com a execução do evento de PR esperando aprovação': { gh: { pulls: [pr(12, 'open', null, ramo)],
      ci: [exec(36, 'Pages', 'completed', 'success', 100), exec(39, 'Pages', 'completed', 'action_required', 100)] },
      esperado: new RegExp(`^PR aberto ${prLink}, CI verde$`), botao: false, leituras: 2 },
    'PR aberto, só a execução esperando aprovação: CI pendente': { gh: { pulls: [pr(12, 'open', null, ramo)],
      ci: [exec(39, 'Pages', 'completed', 'action_required', 100)] },
      esperado: new RegExp(`^PR aberto ${prLink}, CI pendente$`), botao: false, leituras: 2 },
    // r1 (Kimi P2): workflow que só tem execução esperando aprovação nunca rodou; o verde de outro workflow não cobre.
    'PR aberto, outro workflow só esperando aprovação: CI pendente': { gh: { pulls: [pr(12, 'open', null, ramo)],
      ci: [exec(36, 'Pages', 'completed', 'success', 100), exec(39, 'Testes', 'completed', 'action_required', 200)] },
      esperado: new RegExp(`^PR aberto ${prLink}, CI pendente$`), botao: false, leituras: 2 },
    'PR aberto, falha em um workflow e outro esperando aprovação: CI vermelha': { gh: { pulls: [pr(12, 'open', null, ramo)],
      ci: [exec(36, 'Pages', 'completed', 'failure', 100), exec(39, 'Testes', 'completed', 'action_required', 200)] },
      esperado: new RegExp(`^PR aberto ${prLink}, CI vermelha$`), botao: false, leituras: 2 },
    'PR aberto, página incompleta de execuções da CI (r1, Astra P2)': { gh: { pulls: [pr(12, 'open', null, ramo)], ci: [exec(1, 'Pages', 'completed', 'success')], total: 150 },
      esperado: new RegExp(`^PR aberto ${prLink}, CI indisponível$`), botao: false, leituras: 2 },
    'execução fora da primeira página (r1, Astra P2)': { min: 40, gh: { runs: [exec(5, `Publica outro-post (${SHA('0')})`, 'completed', 'success')], total: 150 },
      esperado: /^estado indisponível$/, botao: false, leituras: 2 },
    // r4 (Astra P2): achada numa página incompleta, pode não ser a tentativa vigente (a outra pode ter sido reexecutada).
    'execução achada numa página incompleta': { min: 3, gh: { runs: [exec(77, RUN, 'in_progress')], total: 150 },
      esperado: /^estado indisponível$/, botao: false, leituras: 2 },
    'PR aberto, CI responde lixo': { gh: { pulls: [pr(12, 'open', null, ramo)], ci: { status: 200, body: '{"total_count": 1}' } },
      esperado: new RegExp(`^PR aberto ${prLink}, CI indisponível$`), botao: false, leituras: 2 },
    'publicado: PR mergeado e post no ar': { gh: { pulls: [pr(12, 'closed', '2026-11-14T10:00:00Z', ramo)] }, site: { 'sales-coach': pagina('Coach') },
      esperado: /^publicado em 2026-11-14: <a href="https:\/\/guifav\.dev\/blog\/sales-coach\/">ver o post<\/a>$/, botao: false, leituras: 1, site_consultas: 1 },
    'PR mergeado, deploy ainda não terminou': { gh: { pulls: [pr(12, 'closed', '2026-11-14T10:00:00Z', ramo)] },
      esperado: new RegExp(`^PR ${prLink} mergeado, aguardando o deploy do site$`), botao: false, leituras: 1, site_consultas: 1 },
    'PR fechado sem merge': { gh: { pulls: [pr(12, 'closed', null, ramo)] }, esperado: new RegExp(`^PR ${prLink} fechado sem merge$`), botao: false, leituras: 1 },
    'GitHub fora do ar (500)': { gh: { pulls: 500 }, esperado: /^estado indisponível$/, botao: false, leituras: 1 },
    'GitHub fora do ar (rede)': { gh: { pulls: 'erro' }, esperado: /^estado indisponível$/, botao: false, leituras: 1 },
    'GitHub responde lixo': { gh: { pulls: { status: 200, body: '{"nao": "lista"}' } }, esperado: /^estado indisponível$/, botao: false, leituras: 1 },
    'execuções indisponíveis': { min: 3, gh: { runs: 500 }, esperado: /^estado indisponível$/, botao: false, leituras: 2 },
  };
  for (const [nome, c] of Object.entries(casos)) {
    const s = setup({ gh: c.gh, site: c.site });
    const { version } = await putDraft(s.bucket);
    assert.equal(version, v);
    await s.approve('sales-coach', { confirmar: 'sim', versao: version });
    s.avancar(c.min ?? 0);
    s.gh.length = 0;
    const r = await s.req('/admin/rascunhos');
    assert.equal(r.status, 200, nome);
    const linha = linhaDe(await r.text(), 'sales-coach');
    const cel = linha.slice(linha.lastIndexOf('<td>') + 4);
    const aprovadoEm = 'aprovado em 2026-11-13 20:00 UTC<br>';
    const estado = (cel.startsWith(aprovadoEm) ? cel.slice(aprovadoEm.length) : cel).match(/^(.*?)(?:<form|<\/td>)/)?.[1] ?? '';
    assert.match(estado, c.esperado, `${nome}: ${estado}`);
    // #92: publicado, a linha diz só "publicado em ..."; antes disso, o "aprovado em" vem primeiro.
    assert.equal(cel.startsWith(aprovadoEm), !/^publicado em /.test(estado), `${nome}: aprovado em`);
    assert.equal(/Disparar de novo/.test(linha), c.botao, `${nome}: botão`);
    assert.equal(leituras(s).length, c.leituras, `${nome}: leituras do GitHub`);
    assert.ok(leituras(s).every((x) => x.auth === 'Bearer gh-teste' && x.ua), `${nome}: token e User-Agent`);
    assert.equal(s.site.length, c.site_consultas ?? 0, `${nome}: consultas ao site`);
  }
});

test('R10: as consultas usam o branch da versão, a janela do disparo e o head do PR', async () => {
  const s = setup({ gh: { runs: [] } });
  const { version } = await putDraft(s.bucket);
  await s.approve('sales-coach', { confirmar: 'sim', versao: version });
  s.avancar(3);
  s.gh.length = 0;
  await s.req('/admin/rascunhos');
  const [pulls, runs] = leituras(s);
  assert.equal(pulls.path, `${GH_BASE}/pulls`);
  assert.equal(pulls.query.head, `guifav:publica/sales-coach-${version.slice(0, 8)}`);
  assert.equal(pulls.query.state, 'all');
  assert.equal(pulls.query.base, 'main');
  assert.equal(runs.path, `${GH_BASE}/actions/workflows/publicar.yml/runs`);
  assert.equal(runs.query.event, 'workflow_dispatch');
  assert.equal(runs.query.created, '>=2026-11-13T18:55:00Z', 'de cinco minutos antes do envio da versão em diante (r5)');
  assert.equal(runs.query.per_page, '100');
  const t = setup({ gh: { pulls: [pr(12, 'open', null, `publica/sales-coach-${version.slice(0, 8)}`, 'c'.repeat(40))] } });
  await putDraft(t.bucket);
  await t.approve('sales-coach', { confirmar: 'sim', versao: version });
  t.gh.length = 0;
  await t.req('/admin/rascunhos');
  assert.equal(leituras(t)[1].path, `${GH_BASE}/actions/runs`);
  assert.equal(leituras(t)[1].query.head_sha, 'c'.repeat(40));
  assert.equal(leituras(t)[1].query.per_page, '100');
});

test('R10: a execução certa é a do slug e da versão inteira; slug vizinho, outra versão e PR de outro branch não contam', async () => {
  const s = setup();
  const { version } = await putDraft(s.bucket);
  const v8 = version.slice(0, 8);
  const outra = SHA('d');
  s.ghOpts.pulls = [pr(30, 'open', null, 'publica/sales-coach-2-00000000'), pr(31, 'open', null, `publica/sales-${v8}`)];
  // Run-names no formato real do publicar.yml: "Publica <slug> (<versão inteira>)". A mais recente vem primeiro.
  s.ghOpts.runs = [
    // r1 (Kimi P2): o slug sales-coach-<v8>, de outra versão, não é sales-coach nesta versão.
    exec(9, `Publica sales-coach-${v8} (${outra})`, 'completed', 'failure'),
    exec(1, `Publica sales-coach-2 (${version})`, 'completed', 'failure'),
    exec(2, `Publica sales (${version})`, 'completed', 'failure'),
    exec(3, `Publica sales-coach (${version}0)`, 'completed', 'failure'),
    exec(4, `Publica sales-coach (${outra})`, 'completed', 'failure'),
    // Só a versão8 não basta: o contrato traz a versão inteira.
    exec(8, `Publica sales-coach (${v8})`, 'completed', 'failure'),
    { ...exec(5, `Publica sales-coach (${version})`, 'in_progress'), created_at: '2026-11-13T20:01:00Z' },
    { ...exec(6, `Publica sales-coach (${version})`, 'completed', 'failure'), created_at: '2026-11-13T20:00:30Z' },
  ];
  await s.approve('sales-coach', { confirmar: 'sim', versao: version });
  s.avancar(2);
  const linha = linhaDe(await (await s.req('/admin/rascunhos')).text(), 'sales-coach');
  // O tempo conta da execução escolhida (criada às 20h01), não do disparo gravado.
  assert.match(linha, /preparando o PR \(disparado há 1 min\): <a href="https:\/\/github\.com\/guifav\/guifav\.github\.io\/actions\/runs\/5">/);
  assert.doesNotMatch(linha, /#3[01]/);
});

test('R10: aprovação sem disparo registrado (anterior a esta fatia) mostra "publicação não disparada" com o botão; disparo manual depois aparece', async () => {
  const s = setup();
  const d = await putDraft(s.bucket);
  s.db.raw.prepare(`INSERT INTO draft_approvals (slug, title, html_sha256, source_sha256, approved_at, approved_by)
    VALUES ('sales-coach', 't', ?, ?, '2026-11-12T10:00:00.000Z', ?)`).run(d.htmlSha, d.source, GUI);
  const linha = linhaDe(await (await s.req('/admin/rascunhos')).text(), 'sales-coach');
  assert.match(linha, /aprovado em 2026-11-12 10:00 UTC<br>publicação não disparada<form/);
  assert.match(linha, /Disparar de novo/);
  assert.equal(leituras(s).length, 2, 'PR do branch e execuções desde a aprovação');
  assert.equal(leituras(s)[1].query.created, '>=2026-11-12T09:55:00Z', 'a janela começa no que vier primeiro: aqui, a aprovação');
  assert.equal(disparos(s).length, 0, 'abrir a lista nunca dispara nada');
  // Disparo manual (gh workflow run), que não passa pelo D1: a execução aparece com o link.
  s.ghOpts.runs = [{ ...exec(41, `Publica sales-coach (${d.version})`, 'in_progress'), created_at: '2026-11-13T19:50:00Z' }];
  const manual = linhaDe(await (await s.req('/admin/rascunhos')).text(), 'sales-coach');
  assert.match(manual, /preparando o PR \(disparado há 10 min\): <a href="https:\/\/github\.com\/guifav\/guifav\.github\.io\/actions\/runs\/41">/);
  assert.doesNotMatch(manual, /Disparar de novo/);
});

test('R10 (r2, Astra P2): disparo manual horas depois do disparo gravado aparece, com o tempo contado da execução', async () => {
  const s = setup();
  const { version } = await putDraft(s.bucket);
  await s.approve('sales-coach', { confirmar: 'sim', versao: version });
  s.avancar(125);
  // Execução do disparo da aprovação, recusada, e um disparo manual duas horas depois, rodando. A mais recente vale.
  s.ghOpts.runs = [
    { ...exec(52, `Publica sales-coach (${version})`, 'in_progress'), created_at: '2026-11-13T22:00:00Z' },
    { ...exec(51, `Publica sales-coach (${version})`, 'completed', 'failure'), created_at: '2026-11-13T20:00:05Z' },
    // Execução de antes do envio da versão (fora da janela) não conta.
    { ...exec(50, `Publica sales-coach (${version})`, 'completed', 'success'), created_at: '2026-11-13T18:00:00Z' },
  ];
  const linha = linhaDe(await (await s.req('/admin/rascunhos')).text(), 'sales-coach');
  assert.match(linha, /preparando o PR \(disparado há 5 min\): <a href="https:\/\/github\.com\/guifav\/guifav\.github\.io\/actions\/runs\/52">/);
  s.ghOpts.runs = s.ghOpts.runs.slice(1);
  assert.match(linhaDe(await (await s.req('/admin/rascunhos')).text(), 'sales-coach'), /publicação recusada: <a href="[^"]*\/runs\/51">/);
  s.ghOpts.runs = s.ghOpts.runs.slice(1);
  assert.match(linhaDe(await (await s.req('/admin/rascunhos')).text(), 'sales-coach'), /parado: nenhuma execução encontrada \(disparado há 2 h\)/);
});

test('R10: disparo com motivo gravado, mas o PR da versão existe (o GitHub aceitou depois do prazo): vale o PR', async () => {
  const s = setup({ gh: { dispatch: 'erro' } });
  const { version } = await putDraft(s.bucket);
  await s.approve('sales-coach', { confirmar: 'sim', versao: version });
  s.ghOpts.pulls = [pr(12, 'open', null, `publica/sales-coach-${version.slice(0, 8)}`)];
  s.ghOpts.ci = [exec(1, 'Pages', 'completed', 'success')];
  const linha = linhaDe(await (await s.req('/admin/rascunhos')).text(), 'sales-coach');
  assert.match(linha, /PR aberto <a href="https:\/\/github\.com\/guifav\/guifav\.github\.io\/pull\/12">#12<\/a>, CI verde/);
  assert.doesNotMatch(linha, /disparo falhou|Disparar de novo/);
});

test('R10: rascunho não aprovado, ou com a versão mudada depois da aprovação, não consulta o GitHub', async () => {
  const s = setup();
  const a = await putDraft(s.bucket);
  await putDraft(s.bucket, 'outro-post');
  await s.approve('sales-coach', { confirmar: 'sim', versao: a.version });
  await putDraft(s.bucket, 'sales-coach', { title: 'Versão nova' });
  s.gh.length = 0;
  const body = await (await s.req('/admin/rascunhos')).text();
  assert.match(linhaDe(body, 'sales-coach'), /a versão mudou depois da última aprovação/);
  assert.match(linhaDe(body, 'outro-post'), /<td>aguardando aprovação<\/td>/);
  assert.deepEqual(s.gh, []);
});

// timeout do teste: sem o prazo nas leituras do GitHub, a lista nunca termina, e o teste falha em vez de travar a suíte.
test('R10: prazo esgotado na leitura do GitHub (resposta ou corpo) mostra "estado indisponível" sem travar a lista', { timeout: 3000 }, async () => {
  for (const v of ['pendente', 'corpo-pendente'] as const) {
    const s = setup();
    const { version } = await putDraft(s.bucket);
    await s.approve('sales-coach', { confirmar: 'sim', versao: version });
    await putDraft(s.bucket, 'outro-post', { title: 'Outro' });
    s.ghOpts.pulls = v;
    const r = await s.req('/admin/rascunhos');
    assert.equal(r.status, 200, v);
    const body = await r.text();
    assert.match(linhaDe(body, 'sales-coach'), /<br>estado indisponível<\/td>/, v);
    assert.match(linhaDe(body, 'outro-post'), /aguardando aprovação/, `${v}: o resto da lista continua`);
  }
});

test('R10: sem o token, a lista não consulta o GitHub e diz por quê', async () => {
  const s = setup({ token: '' });
  const d = await putDraft(s.bucket);
  s.db.raw.prepare(`INSERT INTO draft_approvals (slug, title, html_sha256, source_sha256, approved_at, approved_by, dispatched_at)
    VALUES ('sales-coach', 't', ?, ?, '2026-11-12T10:00:00.000Z', ?, '2026-11-12T10:00:01.000Z')`).run(d.htmlSha, d.source, GUI);
  const linha = linhaDe(await (await s.req('/admin/rascunhos')).text(), 'sales-coach');
  assert.match(linha, /estado indisponível \(painel sem o token do GitHub\)/);
  assert.deepEqual(s.gh, []);
});

test('R10 (r1, Kimi P2, e contrato da fatia 3): o run-name casa o slug e a versão inteira, como palavras inteiras', () => {
  const v = 'abcde123'.padEnd(64, '0');
  const outra = 'deadbeef'.padEnd(64, '1');
  const m = { slug: 'mcp', version: v };
  for (const t of [`Publica mcp (${v})`, `Publica: mcp ${v}`, `${v} mcp`]) assert.equal(execucaoDa(m, t), true, t);
  for (const t of [`Publica mcp-abcde123 (${outra})`, `Publica mcp-aberto (${v})`, `Publica mcp (${v}0)`, `Publica mcp (f${v})`,
    `Publica mcpx (${v})`, 'Publica mcp (abcde123)', `publica/mcp-abcde123`, `Publica mcp (${v.slice(0, 63)})`, `Publica MCP (${v.toUpperCase()})`,
    `Publica mcp (${v}-x)`, 'Publica mcp', '', null, 7]) {
    assert.equal(execucaoDa(m, t), false, String(t));
  }
});

test('R10 (r1, Astra e Kimi P2): email sem resposta não segura a aprovação nem apaga o registro do disparo', { timeout: 3000 }, async () => {
  const s = setup({ mailPendente: true });
  const { version } = await putDraft(s.bucket);
  const r = await s.approve('sales-coach', { confirmar: 'sim', versao: version });
  assert.equal(r.headers.get('location'), '/admin/rascunhos?msg=aprovado-sem-aviso');
  const [row] = s.rows();
  assert.deepEqual([row.dispatched_at, row.dispatch_error, row.notify_error], ['2026-11-13T20:00:00.000Z', '', 'tempo esgotado']);
});

test('R10 (r1, Astra e Kimi P2): o disparo fica gravado antes do email; falha ao gravar o aviso não apaga o disparo', async () => {
  const s = setup({ failSql: /SET notified_at/ });
  const { version } = await putDraft(s.bucket);
  const r = await s.approve('sales-coach', { confirmar: 'sim', versao: version });
  assert.equal(r.headers.get('location'), '/admin/rascunhos?msg=aprovado');
  assert.deepEqual([s.rows()[0].dispatched_at, s.rows()[0].notified_at], ['2026-11-13T20:00:00.000Z', '']);
  assert.equal(s.mails[0].disparoGravado, '2026-11-13T20:00:00.000Z', 'o disparo já estava gravado quando o email saiu');
});

test('R10 (r1, Kimi P2): falha do D1 ao gravar o disparo, na aprovação ou no "Disparar de novo", é avisada na tela', async () => {
  const s = setup({ gh: { dispatch: 500 } });
  const { version } = await putDraft(s.bucket);
  await s.approve('sales-coach', { confirmar: 'sim', versao: version });
  s.ghOpts.dispatch = 204;
  s.falha.sql = /SET dispatched_at/;
  const r = await disparar(s, 'sales-coach', { versao: version });
  assert.equal(r.headers.get('location'), '/admin/rascunhos?msg=disparado&registro=falhou');
  assert.equal(disparos(s).length, 2, 'o disparo foi feito');
  assert.equal(s.rows()[0].dispatch_error, 'GitHub respondeu 500: falhou', 'o registro antigo ficou');
  const list = await (await s.req('/admin/rascunhos?msg=disparado&registro=falhou')).text();
  assert.match(list, /<p class="aviso erro" role="status">O resultado do disparo não foi gravado no painel, então a linha do rascunho pode mostrar o estado anterior\./);
  assert.doesNotMatch(await (await s.req('/admin/rascunhos?msg=disparado')).text(), /não foi gravado no painel/);
  // Na aprovação: o disparo vai, o registro dele falha, o email sai e a tela avisa.
  const t = setup({ failSql: /SET dispatched_at/ });
  const v2 = (await putDraft(t.bucket)).version;
  const a = await t.approve('sales-coach', { confirmar: 'sim', versao: v2 });
  assert.equal(a.headers.get('location'), '/admin/rascunhos?msg=aprovado&registro=falhou');
  assert.equal(disparos(t).length, 1);
  assert.equal(t.mails.length, 1);
  assert.equal(t.rows()[0].dispatched_at, '');
});

test('R10 (r2, Kimi P2): página cheia de 100 execuções no tamanho real da API (uns 12,5 KB cada) é lida; corpo gigante não', async () => {
  // Campos de verdade da API ocupam uns 12,5 KB por execução; o lastro simula esse peso.
  const pesada = (x: Record<string, unknown>) => ({ ...x, head_commit: { message: 'x'.repeat(12_500) } });
  const s = setup();
  const { version } = await putDraft(s.bucket);
  await s.approve('sales-coach', { confirmar: 'sim', versao: version });
  s.avancar(3);
  const outras = Array.from({ length: 99 }, (_, i) => pesada(exec(1000 + i, `Publica outro-${i} (${SHA('e')})`, 'completed', 'success')));
  s.ghOpts.runs = [pesada(exec(77, `Publica sales-coach (${version})`, 'in_progress')), ...outras]; // página cheia e completa
  assert.ok(JSON.stringify(s.ghOpts.runs).length > 1.2e6, 'página acima de 1,2 MB');
  assert.match(linhaDe(await (await s.req('/admin/rascunhos')).text(), 'sales-coach'), /preparando o PR \(disparado há 3 min\): <a href="[^"]*\/runs\/77">/);
  // CI: 100 execuções pesadas do head, todas verdes.
  const t = setup({ gh: { pulls: [pr(12, 'open', null, `publica/sales-coach-${version.slice(0, 8)}`)],
    ci: Array.from({ length: 100 }, (_, i) => pesada(exec(2000 + i, `wf-${i}`, 'completed', 'success', 3000 + i))) } });
  await putDraft(t.bucket);
  await t.approve('sales-coach', { confirmar: 'sim', versao: version });
  assert.match(linhaDe(await (await t.req('/admin/rascunhos')).text(), 'sales-coach'), /PR aberto <a [^>]*>#12<\/a>, CI verde/);
  // Corpo acima do teto (5 MiB): cortado, não é JSON, estado indisponível, sem quebrar a lista.
  const u = setup();
  await putDraft(u.bucket);
  await u.approve('sales-coach', { confirmar: 'sim', versao: version });
  u.ghOpts.pulls = { status: 200, body: JSON.stringify([{ lastro: 'x'.repeat(5 << 20) }]) };
  const r = await u.req('/admin/rascunhos');
  assert.equal(r.status, 200);
  assert.match(linhaDe(await r.text(), 'sales-coach'), /<br>estado indisponível<\/td>/);
});

test('R10 (r3, Astra P2): com motivo de disparo gravado, uma execução manual posterior aparece, junto com o motivo e o botão', async () => {
  const s = setup({ gh: { dispatch: 500 } });
  const { version } = await putDraft(s.bucket);
  await s.approve('sales-coach', { confirmar: 'sim', versao: version });
  s.avancar(120);
  s.ghOpts.runs = [{ ...exec(61, `Publica sales-coach (${version})`, 'completed', 'failure'), created_at: '2026-11-13T21:50:00Z' }];
  const linha = linhaDe(await (await s.req('/admin/rascunhos')).text(), 'sales-coach');
  assert.match(linha, /publicação recusada: <a href="[^"]*\/runs\/61">ver a execução<\/a>; o último disparo pelo painel falhou \(GitHub respondeu 500: falhou\)/);
  assert.match(linha, /Disparar de novo/);
  assert.equal(leituras(s)[1].query.created, '>=2026-11-13T18:55:00Z', 'a janela começa no envio da versão');
  // Sem execução, ou com as execuções indisponíveis ou numa página incompleta: o motivo gravado, com o botão.
  for (const runs of [[], 500, 'erro'] as const) {
    s.ghOpts.runs = runs as GhOpts['runs'];
    assert.match(linhaDe(await (await s.req('/admin/rascunhos')).text(), 'sales-coach'), /<br>disparo falhou \(GitHub respondeu 500: falhou\)<form/, String(runs));
  }
  s.ghOpts.runs = [exec(1, `Publica outro (${SHA('e')})`, 'completed', 'success')];
  s.ghOpts.total = 150;
  assert.match(linhaDe(await (await s.req('/admin/rascunhos')).text(), 'sales-coach'), /<br>disparo falhou \(GitHub respondeu 500: falhou\)<form/, 'página incompleta');
});

test('R10 (r3, Astra P2): a execução de publicação vigente é a da tentativa mais recente, mesmo reexecutada e fora de ordem', async () => {
  const s = setup();
  const { version } = await putDraft(s.bucket);
  await s.approve('sales-coach', { confirmar: 'sim', versao: version });
  s.avancar(30);
  // A 20 falhou às 20h10; a 10, criada antes, foi reexecutada às 20h29 e roda. A API devolve a 20 primeiro.
  s.ghOpts.runs = [
    { ...exec(20, `Publica sales-coach (${version})`, 'completed', 'failure'), created_at: '2026-11-13T20:05:00Z', run_started_at: '2026-11-13T20:10:00Z' },
    { ...exec(10, `Publica sales-coach (${version})`, 'in_progress'), created_at: '2026-11-13T20:00:10Z', run_started_at: '2026-11-13T20:29:00Z' },
  ];
  const linha = linhaDe(await (await s.req('/admin/rascunhos')).text(), 'sales-coach');
  assert.match(linha, /preparando o PR \(disparado há 1 min\): <a href="[^"]*\/runs\/10">/);
  // Sem a reexecução, vale a mais recente pela criação, mesmo que a API a devolva depois.
  s.ghOpts.runs = [
    { ...exec(10, `Publica sales-coach (${version})`, 'completed', 'failure'), created_at: '2026-11-13T20:00:10Z' },
    { ...exec(20, `Publica sales-coach (${version})`, 'in_progress'), created_at: '2026-11-13T20:05:00Z' },
  ];
  assert.match(linhaDe(await (await s.req('/admin/rascunhos')).text(), 'sales-coach'), /preparando o PR \(disparado há 25 min\): <a href="[^"]*\/runs\/20">/);
});

test('R10 (r4, Astra P2): execução antiga reexecutada depois de um novo disparo pelo painel continua na janela e vale', async () => {
  const s = setup();
  const { version } = await putDraft(s.bucket);
  await s.approve('sales-coach', { confirmar: 'sim', versao: version });
  s.avancar(10);
  assert.equal((await disparar(s, 'sales-coach', { versao: version })).headers.get('location'), '/admin/rascunhos?msg=disparado');
  assert.equal(s.rows()[0].dispatched_at, '2026-11-13T20:10:00.000Z');
  s.avancar(20);
  // A (do disparo da aprovação) foi cancelada e reexecutada às 20h29; B (do novo disparo, 20h10) falhou.
  s.ghOpts.runs = [
    { ...exec(31, `Publica sales-coach (${version})`, 'completed', 'failure'), created_at: '2026-11-13T20:10:02Z', run_started_at: '2026-11-13T20:10:05Z' },
    { ...exec(30, `Publica sales-coach (${version})`, 'in_progress'), created_at: '2026-11-13T20:00:02Z', run_started_at: '2026-11-13T20:29:00Z' },
  ];
  s.gh.length = 0;
  const linha = linhaDe(await (await s.req('/admin/rascunhos')).text(), 'sales-coach');
  assert.match(linha, /preparando o PR \(disparado há 1 min\): <a href="[^"]*\/runs\/30">/);
  assert.equal(leituras(s)[1].query.created, '>=2026-11-13T18:55:00Z', 'a janela começa no envio da versão, não no último disparo');
});

test('R10 (r4, Kimi P3): só vale o PR do branch da versão contra a main; o mesmo branch com outra base é ignorado', async () => {
  const s = setup();
  const { version } = await putDraft(s.bucket);
  const ramo = `publica/sales-coach-${version.slice(0, 8)}`;
  await s.approve('sales-coach', { confirmar: 'sim', versao: version });
  s.ghOpts.pulls = [pr(40, 'closed', '2026-11-14T10:00:00Z', ramo, 'b'.repeat(40), 'outra-base'), pr(41, 'open', null, ramo)];
  s.ghOpts.ci = [exec(1, 'Pages', 'completed', 'success')];
  const linha = linhaDe(await (await s.req('/admin/rascunhos')).text(), 'sales-coach');
  assert.match(linha, /PR aberto <a href="[^"]*\/pull\/41">#41<\/a>, CI verde/);
  s.ghOpts.pulls = [pr(40, 'closed', '2026-11-14T10:00:00Z', ramo, 'b'.repeat(40), 'outra-base')];
  assert.match(linhaDe(await (await s.req('/admin/rascunhos')).text(), 'sales-coach'), /preparando o PR/);
});

test('R10 (r5, Astra P2): segunda aprovação da mesma versão e disparo manual de antes da aprovação, reexecutados depois, continuam na janela', async () => {
  const s = setup();
  const { version } = await putDraft(s.bucket); // enviada às 19h
  // Disparo manual às 19h30, antes da aprovação: recusado, e reexecutado às 20h29, depois de duas aprovações.
  await s.approve('sales-coach', { confirmar: 'sim', versao: version });
  s.avancar(10);
  // Segunda aprovação da mesma versão às 20h10. Desde o PR #90 a rota não grava outra linha para a mesma versão, mas o
  // D1 de produção tem aprovações duplicadas de antes disso, e a janela tem de continuar certa com elas.
  assert.equal((await s.approve('sales-coach', { confirmar: 'sim', versao: version })).headers.get('location'), '/admin/rascunhos?msg=ja-aprovado');
  const r0 = s.rows()[0];
  s.db.raw.prepare(`INSERT INTO draft_approvals (slug, title, html_sha256, source_sha256, approved_at, approved_by, dispatched_at)
    VALUES ('sales-coach', 't', ?, ?, '2026-11-13T20:10:00.000Z', ?, '2026-11-13T20:10:02.000Z')`).run(r0.html_sha256, r0.source_sha256, GUI);
  assert.equal(s.rows().length, 2);
  s.avancar(20);
  s.ghOpts.runs = [
    { ...exec(31, `Publica sales-coach (${version})`, 'completed', 'failure'), created_at: '2026-11-13T20:10:02Z' },
    { ...exec(30, `Publica sales-coach (${version})`, 'in_progress'), created_at: '2026-11-13T20:00:02Z', run_started_at: '2026-11-13T20:29:00Z' },
  ];
  s.gh.length = 0;
  assert.match(linhaDe(await (await s.req('/admin/rascunhos')).text(), 'sales-coach'), /preparando o PR \(disparado há 1 min\): <a href="[^"]*\/runs\/30">/);
  assert.equal(leituras(s)[1].query.created, '>=2026-11-13T18:55:00Z');
  s.ghOpts.runs = [
    { ...exec(31, `Publica sales-coach (${version})`, 'completed', 'failure'), created_at: '2026-11-13T20:10:02Z' },
    { ...exec(29, `Publica sales-coach (${version})`, 'in_progress'), created_at: '2026-11-13T19:30:00Z', run_started_at: '2026-11-13T20:29:00Z' },
  ];
  assert.match(linhaDe(await (await s.req('/admin/rascunhos')).text(), 'sales-coach'), /preparando o PR \(disparado há 1 min\): <a href="[^"]*\/runs\/29">/);
});

test('R10 (r5): manifesto sem data válida de envio: a janela começa na aprovação', async () => {
  const s = setup();
  const { version } = await putDraft(s.bucket, 'sales-coach', { manifest: { uploaded_at: 'ontem' } });
  await s.approve('sales-coach', { confirmar: 'sim', versao: version });
  s.gh.length = 0;
  await s.req('/admin/rascunhos');
  assert.equal(leituras(s)[1].query.created, '>=2026-11-13T19:55:00Z');
});

test('R10 (r6, Astra P2): reenviar o mesmo conteúdo renova o uploaded_at, mas a janela continua na primeira aprovação da versão', async () => {
  const s = setup();
  const { version } = await putDraft(s.bucket); // enviada às 19h
  await s.approve('sales-coach', { confirmar: 'sim', versao: version }); // aprovada e disparada às 20h
  s.avancar(20);
  // Reenvio do mesmo HTML e da mesma fonte às 20h20: a versão não muda, o uploaded_at sim.
  const again = await putDraft(s.bucket, 'sales-coach', { manifest: { uploaded_at: '2026-11-13T20:20:00.000Z' } });
  assert.equal(again.version, version);
  s.avancar(10);
  s.ghOpts.runs = [{ ...exec(30, `Publica sales-coach (${version})`, 'in_progress'), created_at: '2026-11-13T20:00:02Z', run_started_at: '2026-11-13T20:29:00Z' }];
  s.gh.length = 0;
  assert.match(linhaDe(await (await s.req('/admin/rascunhos')).text(), 'sales-coach'), /preparando o PR \(disparado há 1 min\): <a href="[^"]*\/runs\/30">/);
  assert.equal(leituras(s)[1].query.created, '>=2026-11-13T19:55:00Z', 'a primeira aprovação (20h) vem antes do reenvio (20h20)');
  // Com duas aprovações da versão, vale a mais antiga. Desde o PR #91 a rota não grava a segunda (r1, Astra e Kimi P2);
  // ela é inserida direto, como as duplicadas que o D1 de produção já tem, e com data depois do reenvio, para que
  // trocar a mais antiga pela mais nova mude a janela.
  s.avancar(5);
  assert.equal((await s.approve('sales-coach', { confirmar: 'sim', versao: version })).headers.get('location'), '/admin/rascunhos?msg=ja-aprovado');
  const r0 = s.rows()[0];
  s.db.raw.prepare(`INSERT INTO draft_approvals (slug, title, html_sha256, source_sha256, approved_at, approved_by, dispatched_at)
    VALUES ('sales-coach', 't', ?, ?, '2026-11-13T20:35:00.000Z', ?, '2026-11-13T20:35:02.000Z')`).run(r0.html_sha256, r0.source_sha256, GUI);
  assert.equal(s.rows().length, 2);
  s.gh.length = 0;
  await s.req('/admin/rascunhos');
  assert.equal(leituras(s)[1].query.created, '>=2026-11-13T19:55:00Z');
});

// ------------------------------------------------------------------ #82, fatia 5: botão "Remover rascunho"
const POST_HTML = '<!DOCTYPE html><html><head><title>Como construí o Sales Coach | Guilherme Favaron</title>'
  + '<link rel="canonical" href="https://guifav.dev/blog/sales-coach/"></head><body></body></html>';

/** Artigo aprovado na versão vigente, com uma versão anterior e a fonte no bucket, mais um derivado e um rascunho cujo
 * slug começa igual ("sales-coach-2"), para provar que só o prefixo "sales-coach/" sai. */
async function cenarioPublicado(opts: Parameters<typeof setup>[0] = {}) {
  const s = setup({ site: { 'sales-coach': POST_HTML }, ...opts });
  // Vizinho gravado antes, cujas chaves cortadas no tamanho do slug parecem uma pasta de versão ("v/v/...").
  await putDraft(s.bucket, 'sales-coachxv', { title: 'Vizinho' });
  await putDraft(s.bucket, 'sales-coach', { title: 'Versão antiga', skipManifest: true });
  const d = await putDraft(s.bucket);
  s.bucket.store.set(`sales-coach/v/${d.version}/fonte/sales-coach.md`, enc('# fonte'));
  await putDraft(s.bucket, 'sales-coach-short', { manifest: RS });
  await putDraft(s.bucket, 'sales-coach-2', { title: 'Outro post' });
  s.db.raw.prepare(`INSERT INTO draft_approvals (slug, title, html_sha256, source_sha256, approved_at, approved_by, dispatched_at)
    VALUES ('sales-coach', 't', ?, ?, '2026-11-13T19:10:00.000Z', ?, '2026-11-13T19:10:02.000Z')`).run(d.htmlSha, d.source, GUI);
  const ramo = publishBranch({ slug: 'sales-coach', version: d.version });
  s.ghOpts.pulls = [pr(12, 'closed', '2026-11-13T19:30:00Z', ramo)];
  const remover = async (form: Record<string, string> = { versao: d.version, confirmar: 'sim' }, slug = 'sales-coach', headers: Record<string, string> = {}) =>
    s.req(`/admin/rascunhos/${slug}/remover`, { method: 'POST', body: new URLSearchParams(form).toString(),
      headers: { origin: ORIGIN, 'content-type': 'application/x-www-form-urlencoded', ...headers } });
  return { s, d, ramo, remover };
}
const chavesDe = (s: ReturnType<typeof setup>, slug: string) => [...s.bucket.store.keys()].filter((k) => k.startsWith(`${slug}/`));
const destino = (r: Response) => r.headers.get('location') ?? '';

test('R11: a lista mostra "Remover rascunho" só quando o PR da versão está mergeado e o post no ar', async () => {
  const { s, d, ramo } = await cenarioPublicado();
  const linha = linhaDe(await (await s.req('/admin/rascunhos')).text(), 'sales-coach');
  assert.match(linha, /publicado em 2026-11-13: <a href="https:\/\/guifav\.dev\/blog\/sales-coach\/">ver o post<\/a>/);
  assert.match(linha, new RegExp(`<form method="post" action="/admin/rascunhos/sales-coach/remover"[^>]*><input type="hidden" name="versao" value="${d.version}"><label><input type="checkbox" name="confirmar" value="sim" required> confirmo</label> <button>Remover rascunho</button></form>`));
  for (const [nome, pulls, site] of [['PR aberto', [pr(12, 'open', null, ramo)], POST_HTML], ['post fora do ar', [pr(12, 'closed', '2026-11-13T19:30:00Z', ramo)], 404]] as const) {
    const c = await cenarioPublicado({ site: { 'sales-coach': site } });
    c.s.ghOpts.pulls = [...pulls];
    assert.doesNotMatch(await (await c.s.req('/admin/rascunhos')).text(), /Remover rascunho/, nome);
  }
});

test('R11: remover tira o rascunho do artigo inteiro do R2; derivado e slug parecido ficam; nada é removido só por abrir a lista', async () => {
  const { s, remover } = await cenarioPublicado();
  await s.req('/admin/rascunhos');
  assert.equal(s.bucket.deletes.length, 0, 'abrir a lista não remove nada');
  assert.ok(chavesDe(s, 'sales-coach').length >= 5, 'manifesto, duas versões e a fonte');
  const r = await remover();
  assert.equal(r.status, 303);
  assert.equal(destino(r), '/admin/rascunhos?msg=removido');
  assert.deepEqual(chavesDe(s, 'sales-coach'), [], 'manifesto, versões anteriores e fonte');
  assert.deepEqual(s.bucket.deletes.at(-1), ['sales-coach/manifest.json'], 'o manifesto sai por último (r1, Codex P2)');
  assert.ok(chavesDe(s, 'sales-coach-short').length > 0, 'derivado continua');
  assert.ok(chavesDe(s, 'sales-coach-2').length > 0, 'slug que só começa igual continua');
  assert.equal(chavesDe(s, 'sales-coachxv').length, 3, 'vizinho mais antigo continua inteiro');
  assert.equal(s.rows().length, 1, 'a aprovação continua no D1');
  const lista = await (await s.req('/admin/rascunhos?msg=removido')).text();
  assert.match(lista, /Rascunho removido do R2/);
  assert.match(lista, /<tr class="publicado">/);
  assert.match(linhaDe(lista, 'sales-coach-short'), /<td><\/td><\/tr>/);
});

test('R11: remover recusa sem as confirmações, e nada sai', async () => {
  type Caso = [string, (c: Awaited<ReturnType<typeof cenarioPublicado>>) => Promise<Response>, number, RegExp];
  const casos: Caso[] = [
    ['sem confirmo', (c) => c.remover({ versao: c.d.version }), 400, /confirmo/],
    ['origem de fora', (c) => c.remover(undefined, undefined, { origin: 'https://exemplo.com' }), 403, /origem/],
    ['versão vista diferente', (c) => c.remover({ versao: SHA('9'), confirmar: 'sim' }), 409, /mudou/],
    // r3 (Kimi P2): derivado com versão própria, aprovação, PR mergeado e post no ar; só a regra do derivado o segura.
    ['derivado', async (c) => {
      const der = (await readManifest(c.s.bucket, 'sales-coach-short'))!;
      c.s.db.raw.prepare(`INSERT INTO draft_approvals (slug, title, html_sha256, source_sha256, approved_at, approved_by)
        VALUES ('sales-coach-short', 't', ?, ?, '2026-11-13T19:10:00.000Z', ?)`).run(der.html_sha256, der.source_sha256, GUI);
      (c.s.ghOpts.pulls as unknown[]).push(pr(14, 'closed', '2026-11-13T19:30:00Z', publishBranch(der)));
      // r4 (Astra e Kimi): o "post" do derivado também responde, com o canonical dele; sobra só a regra do derivado.
      c.s.siteOpts['sales-coach-short'] = POST_HTML.replaceAll('/blog/sales-coach/', '/blog/sales-coach-short/');
      return c.remover({ versao: der.version, confirmar: 'sim' }, 'sales-coach-short');
    }, 400, /derivado/],
    ['sem aprovação', async (c) => { c.s.db.raw.prepare('DELETE FROM draft_approvals').run(); return c.remover(); }, 409, /não está aprovada/],
    ['PR aberto', (c) => { c.s.ghOpts.pulls = [pr(12, 'open', null, c.ramo)]; return c.remover(); }, 409, /não está mergeado/],
    ['PR fechado sem merge', (c) => { c.s.ghOpts.pulls = [pr(12, 'closed', null, c.ramo)]; return c.remover(); }, 409, /não está mergeado/],
    ['PR mergeado em outra base', (c) => { c.s.ghOpts.pulls = [pr(12, 'closed', '2026-11-13T19:30:00Z', c.ramo, 'b'.repeat(40), 'outra')]; return c.remover(); }, 409, /não está mergeado/],
    ['PR de outro branch', (c) => { c.s.ghOpts.pulls = [pr(12, 'closed', '2026-11-13T19:30:00Z', 'publica/sales-coach-00000000')]; return c.remover(); }, 409, /não está mergeado/],
    ['GitHub fora do ar', (c) => { c.s.ghOpts.pulls = 502; return c.remover(); }, 503, /GitHub não respondeu/],
    ['GitHub sem resposta', (c) => { c.s.ghOpts.pulls = 'pendente'; return c.remover(); }, 503, /GitHub não respondeu/],
  ];
  for (const [nome, faz, status, msg] of casos) {
    const c = await cenarioPublicado();
    const antes = chavesDe(c.s, 'sales-coach').length;
    const r = await faz(c);
    assert.equal(r.status, status, nome);
    assert.match(await r.text(), msg, nome);
    assert.equal(chavesDe(c.s, 'sales-coach').length, antes, nome);
    assert.ok(chavesDe(c.s, 'sales-coach-short').length > 0, `${nome}: derivado intacto`);
    assert.equal(c.s.bucket.deletes.length, 0, nome);
  }
  // r3 (Astra P1): só a página do post de fato confirma; qualquer outro 200 não basta, nem para o botão aparecer.
  const semCanonical = '<!DOCTYPE html><html><head><title>Como construí o Sales Coach</title></head><body></body></html>';
  const outroCanonical = semCanonical.replace('</head>', '<link rel="canonical" href="https://guifav.dev/blog/outro/"></head>');
  // r4 (Astra e Kimi P1): canonical só em texto (comentário, script, title, textarea) ou um segundo canonical errado.
  const comentado = outroCanonical.replace('</head>', '<!-- <link rel="canonical" href="https://guifav.dev/blog/sales-coach/"> --></head>');
  const tudoComentado = '<!--' + POST_HTML + '-->';
  const noScript = semCanonical.replace('</head>', '<script>var x = \'<link rel="canonical" href="https://guifav.dev/blog/sales-coach/">\';</script></head>');
  const noTitulo = '<!DOCTYPE html><html><head><title><link rel="canonical" href="https://guifav.dev/blog/sales-coach/"></title></head></html>';
  const doisCanonicais = POST_HTML.replace('</head>', '<link rel="canonical" href="https://guifav.dev/blog/outro/"></head>');
  for (const site of [404, 301, 500, 'erro', 'pendente', semCanonical, outroCanonical, '', comentado, tudoComentado, noScript, noTitulo, doisCanonicais] as const) {
    const c = await cenarioPublicado({ site: { 'sales-coach': site } });
    const r = await c.remover();
    assert.equal(r.status, 409, `site ${JSON.stringify(site).slice(0, 40)}`);
    assert.equal(c.s.bucket.deletes.length, 0, `site ${JSON.stringify(site).slice(0, 40)}`);
    assert.doesNotMatch(linhaDe(await (await c.s.req('/admin/rascunhos')).text(), 'sales-coach'), /Remover rascunho/);
  }
  // 200 com o mesmo texto da página, mas sem ser HTML (JSON, texto puro, sem content-type): não confirma.
  for (const tipo of ['application/json', 'text/plain', null, 'text/html-not-html', 'text/htmlx; charset=utf-8']) {
    const c = await cenarioPublicado();
    const fetchOriginal = c.s.deps.fetch;
    c.s.deps.fetch = (async (i: string | URL | Request, init?: RequestInit) => {
      if (!String(i).startsWith('https://guifav.dev/blog/')) return fetchOriginal(i, init);
      const r = new Response(new TextEncoder().encode(POST_HTML), { status: 200 });
      if (tipo) r.headers.set('content-type', tipo);
      return r;
    }) as typeof fetch;
    assert.equal((await c.remover()).status, 409, `tipo ${tipo}`);
    assert.equal(c.s.bucket.deletes.length, 0, `tipo ${tipo}`);
  }
  const t = await cenarioPublicado({ token: '' });
  assert.equal((await t.remover()).status, 503, 'sem token');
  assert.equal(t.s.bucket.deletes.length, 0);
});

test('R11 (r4, Astra e Kimi P1): canonicais() lê só elementos <link rel="canonical"> de verdade', () => {
  const u = 'https://guifav.dev/blog/x/';
  assert.deepEqual(canonicais(`<head><link rel="canonical" href="${u}"></head>`), [u]);
  assert.deepEqual(canonicais(`<head><LINK HREF='${u}' REL='Canonical'></head>`), [u], 'maiúsculas, aspas simples, ordem trocada');
  assert.deepEqual(canonicais(`<link rel="alternate canonical" href=${u}>`), [u], 'rel com mais de uma palavra, href sem aspas');
  assert.deepEqual(canonicais(`<link rel="alternate" href="${u}"><link rel="stylesheet" href="${u}">`), []);
  assert.deepEqual(canonicais(`<!-- <link rel="canonical" href="${u}"> -->`), []);
  assert.deepEqual(canonicais(`<!-- sem fim <link rel="canonical" href="${u}">`), [], 'comentário sem fim apaga o resto');
  assert.deepEqual(canonicais(`<script><link rel="canonical" href="${u}"></script>`), []);
  assert.deepEqual(canonicais(`<SCRIPT type="x"><link rel="canonical" href="${u}"></SCRIPT >`), []);
  assert.deepEqual(canonicais(`<style>/* <link rel="canonical" href="${u}"> */</style>`), []);
  assert.deepEqual(canonicais(`<textarea><link rel="canonical" href="${u}"></textarea>`), []);
  assert.deepEqual(canonicais(`<title><link rel="canonical" href="${u}"></title>`), []);
  assert.deepEqual(canonicais(`<template><link rel="canonical" href="${u}"></template>`), []);
  assert.deepEqual(canonicais(`<noscript><link rel="canonical" href="${u}"></noscript>`), []);
  assert.deepEqual(canonicais(`<link data-rel="canonical" href="${u}">`), [], 'outro atributo terminado em rel');
  assert.deepEqual(canonicais(`<link rel="noncanonical" href="${u}">`), [], 'rel vale por palavra inteira');
  assert.deepEqual(canonicais(`<link rel="canonical" href="https://guifav.dev/blog/y/"><link rel="canonical" href="${u}">`), ['https://guifav.dev/blog/y/', u]);
  // A página real do post tem esta forma (conferido em guifav.dev/blog/mcp-ou-aplicacao-dados-da-empresa-na-ia/).
  assert.deepEqual(canonicais(POST_HTML), ['https://guifav.dev/blog/sales-coach/']);
});

test('R11 (r1 e r2, Astra e Kimi P2): resposta do GitHub incoerente ou com data inválida não conta como merge', async () => {
  const ruins: [string, Record<string, unknown>][] = [
    ['aberto com merged_at', { state: 'open', merged_at: '2026-11-13T19:30:00Z' }],
    ['sem state', { state: undefined, merged_at: '2026-11-13T19:30:00Z' }],
    ['merged_at que não é data', { state: 'closed', merged_at: 'não é uma data' }],
    ['merged_at com espaço', { state: 'closed', merged_at: '2026-11-13 19:30:00Z' }],
    ['merged_at impossível', { state: 'closed', merged_at: '2026-13-45T99:99:99Z' }],
    ['30 de fevereiro', { state: 'closed', merged_at: '2026-02-30T19:30:00Z' }],
    ['merged_at numérico', { state: 'closed', merged_at: 1763062200 }],
  ];
  for (const [nome, campos] of ruins) {
    assert.equal(mergeado({ ...pr(12, 'closed', '2026-11-13T19:30:00Z', 'x'), ...campos }), false, nome);
    const c = await cenarioPublicado();
    c.s.ghOpts.pulls = [{ ...pr(12, 'closed', '2026-11-13T19:30:00Z', c.ramo), ...campos }];
    assert.equal((await c.remover()).status, 409, nome);
    assert.equal(c.s.bucket.deletes.length, 0, nome);
    assert.equal(c.s.site.length, 0, `${nome}: sem merge, nem consulta o site`);
    assert.doesNotMatch(await (await c.s.req('/admin/rascunhos')).text(), /publicado em|Remover rascunho/, `${nome}: a lista não mostra publicado`);
  }
  assert.equal(mergeado(pr(12, 'closed', '2026-11-13T19:30:00Z', 'x')), true);
});

test('R11: rascunho com mais objetos que uma página sai inteiro; mais de 1000 saem em lotes; histórico grande demais fica', async () => {
  {
    const { s, d, remover } = await cenarioPublicado();
    for (let i = 0; i < 7; i++) s.bucket.store.set(`sales-coach/v/${d.version}/img/sales-coach/extra-${i}.webp`, enc('x'));
    s.bucket.pagina = 3;
    assert.equal(destino(await remover()), '/admin/rascunhos?msg=removido');
    assert.deepEqual(chavesDe(s, 'sales-coach'), []);
  }
  {
    const { s, d, remover } = await cenarioPublicado();
    for (let i = 0; i < 1100; i++) s.bucket.store.set(`sales-coach/v/${d.version}/img/sales-coach/x-${i}.webp`, enc('x'));
    assert.equal(destino(await remover()), '/admin/rascunhos?msg=removido');
    assert.deepEqual(chavesDe(s, 'sales-coach'), []);
    assert.ok(s.bucket.deletes.every((l) => l.length <= 1000));
  }
  {
    const { s, d, remover } = await cenarioPublicado();
    for (let i = 0; i < 9; i++) s.bucket.store.set(`sales-coach/v/${d.version}/img/sales-coach/extra-${i}.webp`, enc('x'));
    s.bucket.pagina = 3;
    s.deps.remocaoMaxPaginas = 2;
    const antes = chavesDe(s, 'sales-coach').length;
    assert.equal(destino(await remover()), '/admin/rascunhos?msg=remocao-grande', 'r3 (Astra P2): limite não é falha no meio');
    assert.equal(chavesDe(s, 'sales-coach').length, antes, 'nada sai');
    assert.match(await (await s.req('/admin/rascunhos?msg=remocao-grande')).text(), /Nada foi removido, e repetir não resolve/);
  }
});

test('R11 (r1, Codex P1): envio novo durante a remoção não é apagado', async () => {
  // O envio chega enquanto o painel consulta o site: versão nova inteira e manifesto novo. Nada do slug sai.
  {
    const { s, remover } = await cenarioPublicado();
    const fetchOriginal = s.deps.fetch;
    let nova: Draft | undefined;
    s.deps.fetch = (async (i: string | URL | Request, init?: RequestInit) => {
      if (String(i).startsWith('https://guifav.dev/blog/') && !nova) nova = await putDraft(s.bucket, 'sales-coach', { title: 'Correção' });
      return fetchOriginal(i, init);
    }) as typeof fetch;
    assert.equal(destino(await remover()), '/admin/rascunhos?msg=remocao-mudou');
    assert.equal(s.bucket.deletes.length, 0, 'manifesto mudou antes da remoção: nada sai');
    assert.equal((await readManifest(s.bucket, 'sales-coach'))?.version, nova!.version);
  }
  // O envio está no meio: objetos da versão nova já gravados, manifesto ainda o antigo. A versão aprovada e as
  // anteriores saem; a nova, gravada depois do manifesto vigente, fica inteira.
  {
    const { s, d, remover } = await cenarioPublicado();
    const fetchOriginal = s.deps.fetch;
    let nova: Draft | undefined;
    s.deps.fetch = (async (i: string | URL | Request, init?: RequestInit) => {
      if (String(i).startsWith('https://guifav.dev/blog/') && !nova) nova = await putDraft(s.bucket, 'sales-coach', { title: 'Em andamento', skipManifest: true });
      return fetchOriginal(i, init);
    }) as typeof fetch;
    assert.equal(destino(await remover()), '/admin/rascunhos?msg=removido');
    const restam = chavesDe(s, 'sales-coach');
    assert.ok(restam.length > 0 && restam.every((k) => k.startsWith(`sales-coach/v/${nova!.version}/`)), 'só a versão em andamento fica');
    assert.ok(!restam.some((k) => k.includes(d.version)));
  }
  // Reenvio em andamento de um conteúdo igual ao de uma versão antiga: a pasta dela fica com objetos de antes e de
  // depois do manifesto vigente, e não sai.
  {
    const { s, remover } = await cenarioPublicado();
    const vigente = (await readManifest(s.bucket, 'sales-coach'))!.version;
    const antiga = [...s.bucket.store.keys()].find((k) => k.startsWith('sales-coach/v/') && k.endsWith('/index.html') && !k.includes(vigente))!;
    const pastaAntiga = antiga.replace(/index\.html$/, '');
    const fetchOriginal = s.deps.fetch;
    let regravou = false;
    s.deps.fetch = (async (i: string | URL | Request, init?: RequestInit) => {
      if (String(i).startsWith('https://guifav.dev/blog/') && !regravou) {
        regravou = true;
        s.bucket.store.set(antiga, s.bucket.store.get(antiga)!);
      }
      return fetchOriginal(i, init);
    }) as typeof fetch;
    assert.equal(destino(await remover()), '/admin/rascunhos?msg=removido');
    assert.ok(chavesDe(s, 'sales-coach').length > 0 && chavesDe(s, 'sales-coach').every((k) => k.startsWith(pastaAntiga)), 'a pasta regravada fica inteira');
    assert.equal(chavesDe(s, 'sales-coach').length, 2, 'index.html e a imagem da versão antiga');
  }
  // O envio troca o manifesto entre a remoção das versões e a do manifesto: o manifesto novo fica.
  {
    const { s, remover } = await cenarioPublicado();
    const deleteOriginal = s.bucket.delete.bind(s.bucket);
    let nova: Draft | undefined;
    s.bucket.delete = async (keys: string | string[]) => {
      await deleteOriginal(keys);
      if (!nova) nova = await putDraft(s.bucket, 'sales-coach', { title: 'Chegou no fim' });
    };
    assert.equal(destino(await remover()), '/admin/rascunhos?msg=remocao-mudou');
    assert.equal((await readManifest(s.bucket, 'sales-coach'))?.version, nova!.version, 'manifesto novo continua');
  }
});

test('R11 (r1, Codex P2; r2, Astra P2): remoção que para no meio fica na lista com "prévia incompleta" e o botão termina', async () => {
  const { s, d, remover } = await cenarioPublicado();
  for (let i = 0; i < 5; i++) s.bucket.store.set(`sales-coach/v/${d.version}/img/sales-coach/extra-${i}.webp`, enc('x'));
  const deleteOriginal = s.bucket.delete.bind(s.bucket);
  // Os objetos saem; a remoção do manifesto falha.
  s.bucket.delete = async (keys: string | string[]) => {
    if (keys === 'sales-coach/manifest.json') throw new Error('R2 indisponível');
    return deleteOriginal(keys);
  };
  assert.equal(destino(await remover()), '/admin/rascunhos?msg=remocao-falhou');
  assert.ok(await readManifest(s.bucket, 'sales-coach'), 'o manifesto continua');
  const lista = await (await s.req('/admin/rascunhos?msg=remocao-falhou')).text();
  assert.match(lista, /A remoção parou no meio/);
  const linha = lista.split('<tr').find((r) => r.includes('action="/admin/rascunhos/sales-coach/remover"')) ?? '';
  assert.match(linha, /prévia incompleta/);
  assert.match(linha, /Remover rascunho/);
  assert.doesNotMatch(linha, /href="\/admin\/rascunhos\/sales-coach\/"/, 'sem link para a prévia que não existe mais');
  assert.match(linhaDe(lista, 'sales-coach-short'), /<td><\/td><\/tr>/, 'o derivado continua abaixo dele');
  s.bucket.delete = deleteOriginal;
  assert.equal(destino(await remover()), '/admin/rascunhos?msg=removido');
  assert.deepEqual(chavesDe(s, 'sales-coach'), []);
});

test('R11: o cron só faz a cópia dos assinantes; a remoção não é automática', () => {
  assert.match(TOML, /crons = \["17 \* \* \* \*"\]/);
});

// ------------------------------------------------------------------ aprovação dupla e retorno do clique (PR #90)
test('R12 (PR #90): aprovar de novo a mesma versão não grava, não dispara nem avisa; a mensagem diz que já estava aprovada', async () => {
  const s = setup();
  const { version } = await putDraft(s.bucket);
  const r1 = await s.approve('sales-coach', { confirmar: 'sim', versao: version });
  assert.equal(r1.headers.get('location'), '/admin/rascunhos?msg=aprovado');
  const r2 = await s.approve('sales-coach', { confirmar: 'sim', versao: version });
  assert.equal(r2.status, 303);
  assert.equal(r2.headers.get('location'), '/admin/rascunhos?msg=ja-aprovado');
  assert.equal(s.rows().length, 1, 'uma linha só');
  assert.equal(disparos(s).length, 1, 'um disparo só');
  assert.equal(s.mails.length, 1, 'um aviso só');
  assert.match(await (await s.req('/admin/rascunhos?msg=ja-aprovado')).text(), /<p class="aviso ok" role="status">Esta versão já estava aprovada/);
});

test('R12 (PR #90): dois cliques simultâneos gravam uma aprovação e disparam uma vez', async () => {
  const s = setup();
  const { version } = await putDraft(s.bucket);
  const rs = await Promise.all([1, 2, 3].map(() => s.approve('sales-coach', { confirmar: 'sim', versao: version })));
  assert.deepEqual(rs.map((r) => r.headers.get('location')).sort(),
    ['/admin/rascunhos?msg=aprovado', '/admin/rascunhos?msg=ja-aprovado', '/admin/rascunhos?msg=ja-aprovado']);
  assert.equal(s.rows().length, 1);
  assert.equal(disparos(s).length, 1);
});

test('R12 (PR #90): versão nova do mesmo rascunho continua aprovável depois da anterior', async () => {
  const s = setup();
  const a = await putDraft(s.bucket);
  assert.equal((await s.approve('sales-coach', { confirmar: 'sim', versao: a.version })).headers.get('location'), '/admin/rascunhos?msg=aprovado');
  const b = await putDraft(s.bucket, 'sales-coach', { title: 'Correção' });
  assert.equal((await s.approve('sales-coach', { confirmar: 'sim', versao: b.version })).headers.get('location'), '/admin/rascunhos?msg=aprovado');
  assert.equal(s.rows().length, 2);
  // Outro slug com o mesmo conteúdo também é outra aprovação.
  const c = await putDraft(s.bucket, 'outro', { title: 'Correção', source: b.source, html: b.html });
  assert.equal((await s.approve('outro', { confirmar: 'sim', versao: c.version })).headers.get('location'), '/admin/rascunhos?msg=aprovado');
  assert.equal(s.rows().length, 3);
});

test('R12 (PR #90): o botão de aprovar trava e avisa "Aprovando, aguarde..." assim que o formulário sai; a mensagem da lista vem destacada', async () => {
  const s = setup();
  await putDraft(s.bucket);
  const prev = await (await s.req('/admin/rascunhos/sales-coach/')).text();
  assert.match(prev, /<form method="post" action="\/admin\/rascunhos\/sales-coach\/aprovar"[^>]*onsubmit="var b=this\.querySelector\('button'\);b\.disabled=true;b\.textContent='Aprovando, aguarde\.\.\.'">/);
  assert.match(PREVIEW_CSP, /script-src 'self' 'unsafe-inline'/, 'a prévia aceita o script do botão');
  const list = await (await s.req('/admin/rascunhos?msg=aprovado')).text();
  assert.match(list, /<p class="aviso ok" role="status">Aprovado\./);
  assert.match(list, /\.aviso\{font-weight:600;padding:/);
});

test('R13 (#92): tituloNaLista tira do título do derivado o rótulo repetido, e só ele', () => {
  assert.equal(tituloNaLista('Roteiro do short: quem garante a resposta da IA?', 'Roteiro do short'), 'quem garante a resposta da IA?');
  assert.equal(tituloNaLista('Roteiro do vídeo: o que vem depois do transformer', 'Roteiro do vídeo (YouTube)'), 'o que vem depois do transformer');
  assert.equal(tituloNaLista('ROTEIRO DO VÍDEO (YouTube): x', 'Roteiro do vídeo (YouTube)'), 'x');
  assert.equal(tituloNaLista('Quem garante a resposta da IA', 'LinkedIn'), 'Quem garante a resposta da IA');
  assert.equal(tituloNaLista('Carrossel: dados', 'Roteiro do short'), 'Carrossel: dados', 'outro começo fica');
  assert.equal(tituloNaLista('Roteiro do short:', 'Roteiro do short'), 'Roteiro do short:', 'sem nada depois, fica inteiro');
  assert.equal(tituloNaLista('Roteiro do short sobre X: Y', 'Roteiro do short'), 'Roteiro do short sobre X: Y', 'prefixo maior que o rótulo fica');
  assert.equal(tituloNaLista('Roteiro: x', null), 'Roteiro: x');
});

test('R13 (#92): a lista não tem coluna Slug e mostra o título do derivado sem o rótulo; a prévia mantém o título inteiro', async () => {
  const s = setup();
  await putDraft(s.bucket, 'sales-coach');
  await putDraft(s.bucket, 'sales-coach-short', { title: 'Roteiro do short: vender <mais>', manifest: RS });
  const body = await (await s.req('/admin/rascunhos')).text();
  assert.match(body, /<thead><tr><th>Título<\/th><th>Prévia enviada<\/th><th>Estado<\/th><\/tr><\/thead>/);
  assert.doesNotMatch(body, /<td>sales-coach(-short)?<\/td>/, 'o slug não aparece como célula');
  const linha = linhaDe(body, 'sales-coach-short');
  assert.match(linha, /<span class="tag">Roteiro do short<\/span><a href="\/admin\/rascunhos\/sales-coach-short\/">vender &lt;mais&gt;<\/a><\/td><td>/);
  for (const r of body.split('<tr').slice(1)) assert.equal((r.match(/<t[dh]>/g) ?? []).length, 3, `três células: ${r.slice(0, 80)}`);
  const previa = await (await s.req('/admin/rascunhos/sales-coach-short/')).text();
  assert.match(previa, /Roteiro do short: vender &lt;mais&gt;|Roteiro do short: vender <mais>/);
});

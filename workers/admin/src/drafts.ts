// Rascunhos privados do blog (issue #62). Só o Gui vê, pelo mesmo Access do painel.
//   GET  /admin/rascunhos                   lista dos rascunhos, com o estado da aprovação
//   GET  /admin/rascunhos/<slug>/           prévia do post, com a barra de aprovação no topo
//   GET  /admin/rascunhos/<slug>/<arquivo>  imagens e vídeo do rascunho (só os do manifesto)
//   POST /admin/rascunhos/<slug>/aprovar    grava a aprovação da versão exata que foi vista e avisa o Roger
// A prévia é gerada e enviada ao R2 privado pelo blog-src/scripts/draft_preview.py, na KVM. O rascunho nunca
// entra no repo antes da aprovação; a publicação é um PR do Roger com o mesmo conteúdo (source_sha256).
import type { D1Like } from './sync.ts';

export interface R2Body {
  arrayBuffer(): Promise<ArrayBuffer>;
  text(): Promise<string>;
}
export interface R2Like {
  get(key: string): Promise<R2Body | null>;
  list(opts: { prefix?: string; delimiter?: string; cursor?: string; limit?: number }): Promise<{
    objects: { key: string }[];
    delimitedPrefixes: string[];
    truncated: boolean;
    cursor?: string;
  }>;
}

export interface DraftEnv {
  DB: D1Like;
  DRAFTS: R2Like;
  RESEND_API_KEY: string;
  SITE_ORIGIN: string;
  DRAFT_NOTIFY_FROM: string;
  DRAFT_NOTIFY_TO: string;
}

export interface DraftDeps {
  fetch: typeof fetch;
  now: () => number;
}

export interface Manifest {
  slug: string;
  title: string;
  uploaded_at: string;
  source_sha256: string;
  files: Record<string, string>; // caminho relativo -> content-type; inclui index.html
}

export const SLUG_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
// Cada segmento começa com letra ou número: sem ".", "..", arquivo oculto nem barra dupla.
const FILE_RE = /^[A-Za-z0-9][A-Za-z0-9._-]*(?:\/[A-Za-z0-9][A-Za-z0-9._-]*)*$/;
const SHA_RE = /^[0-9a-f]{64}$/;
const MAX_DRAFTS = 200;

export const PREVIEW_CSP = [
  "default-src 'self'",
  "img-src 'self' data:",
  "style-src 'self' 'unsafe-inline'",
  "script-src 'self' 'unsafe-inline'",
  "media-src 'self'",
  "font-src 'self' data:",
  'frame-src https://www.youtube-nocookie.com https://open.spotify.com',
  "connect-src 'none'",
  "form-action 'self'",
  "frame-ancestors 'none'",
  "base-uri 'none'",
].join('; ');

const ADMIN_CSP = "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'";

function headers(type: string, csp: string): Record<string, string> {
  return {
    'content-type': type,
    'cache-control': 'private, no-store',
    'x-robots-tag': 'noindex, nofollow',
    'x-frame-options': 'DENY',
    'x-content-type-options': 'nosniff',
    'referrer-policy': 'no-referrer',
    'content-security-policy': csp,
  };
}

const text = (status: number, body: string) => new Response(body, { status, headers: headers('text/plain; charset=utf-8', ADMIN_CSP) });

export function esc(s: unknown): string {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!);
}

export async function sha256Hex(data: ArrayBuffer | Uint8Array | string): Promise<string> {
  const bytes = typeof data === 'string' ? new TextEncoder().encode(data) : data;
  const digest = await crypto.subtle.digest('SHA-256', bytes as BufferSource);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

/** Manifesto válido do rascunho, ou null (ausente ou malformado: o rascunho não existe para o painel). */
export async function readManifest(bucket: R2Like, slug: string): Promise<Manifest | null> {
  if (!SLUG_RE.test(slug)) return null;
  const obj = await bucket.get(`${slug}/manifest.json`);
  if (!obj) return null;
  let m: unknown;
  try {
    m = JSON.parse(await obj.text());
  } catch {
    return null;
  }
  if (!m || typeof m !== 'object') return null;
  const r = m as Record<string, unknown>;
  if (r.slug !== slug || typeof r.title !== 'string' || typeof r.uploaded_at !== 'string') return null;
  if (typeof r.source_sha256 !== 'string' || !SHA_RE.test(r.source_sha256)) return null;
  if (!r.files || typeof r.files !== 'object' || Array.isArray(r.files)) return null;
  const files: Record<string, string> = {};
  for (const [k, v] of Object.entries(r.files as Record<string, unknown>)) {
    if (!FILE_RE.test(k) || typeof v !== 'string' || !v) return null;
    files[k] = v;
  }
  if (files['index.html'] !== 'text/html; charset=utf-8') return null;
  return { slug, title: r.title, uploaded_at: r.uploaded_at, source_sha256: r.source_sha256, files };
}

type Approval = { id: number; html_sha256: string; approved_at: string; approved_by: string; notified_at: string; notify_error: string };

async function approvals(db: D1Like, slug: string): Promise<Approval[]> {
  const r = await db
    .prepare('SELECT id, html_sha256, approved_at, approved_by, notified_at, notify_error FROM draft_approvals WHERE slug = ? ORDER BY id DESC')
    .bind(slug)
    .all<Approval>();
  return r.results;
}

/** Estado da versão atual: aprovada só se a aprovação é do mesmo HTML que está no R2 agora. */
function state(list: Approval[], html: string): { approved: Approval | null; stale: boolean } {
  const approved = list.find((a) => a.html_sha256 === html) ?? null;
  return { approved, stale: !approved && list.length > 0 };
}

const PAGE_STYLE = `body{font:15px/1.45 system-ui,sans-serif;margin:1.5rem;color:#111}table{border-collapse:collapse;width:100%}
th,td{border-bottom:1px solid #ddd;padding:.35rem .5rem;text-align:left;vertical-align:top}.ok{color:#060}.erro{color:#a00}`;

async function listPage(env: DraftEnv, msg: string): Promise<Response> {
  const prefixes: string[] = [];
  let cursor: string | undefined;
  do {
    const page = await env.DRAFTS.list({ delimiter: '/', cursor, limit: 1000 });
    prefixes.push(...page.delimitedPrefixes);
    cursor = page.truncated ? page.cursor : undefined;
  } while (cursor && prefixes.length < MAX_DRAFTS);
  const rows: string[] = [];
  for (const p of prefixes.slice(0, MAX_DRAFTS)) {
    const slug = p.replace(/\/$/, '');
    const m = await readManifest(env.DRAFTS, slug);
    if (!m) continue;
    const html = await env.DRAFTS.get(`${slug}/index.html`);
    if (!html) continue;
    const st = state(await approvals(env.DB, slug), await sha256Hex(await html.arrayBuffer()));
    const label = st.approved
      ? `aprovado em ${esc(st.approved.approved_at.slice(0, 16).replace('T', ' '))} UTC`
      : st.stale
        ? 'aguardando aprovação (a versão mudou depois da última aprovação)'
        : 'aguardando aprovação';
    rows.push(`<tr><td><a href="/admin/rascunhos/${slug}/">${esc(m.title)}</a></td><td>${esc(slug)}</td>
<td>${esc(m.uploaded_at.slice(0, 16).replace('T', ' '))} UTC</td><td>${label}</td></tr>`);
  }
  const body = `<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow"><title>Rascunhos | Insights</title><style>${PAGE_STYLE}</style></head><body>
<p><a href="/admin">Assinantes</a> · Rascunhos</p><h1>Rascunhos do blog</h1>
${msg ? `<p class="ok">${esc(msg)}</p>` : ''}
${rows.length ? `<table><thead><tr><th>Título</th><th>Slug</th><th>Prévia enviada</th><th>Estado</th></tr></thead><tbody>${rows.join('')}</tbody></table>`
    : '<p>Nenhum rascunho no momento.</p>'}
<p>Só você vê estas páginas. Nada daqui está publicado. Depois da aprovação, o Roger publica o post com o mesmo conteúdo.</p>
</body></html>`;
  return new Response(body, { status: 200, headers: headers('text/html; charset=utf-8', ADMIN_CSP) });
}

function approvalBar(m: Manifest, html: string, st: { approved: Approval | null; stale: boolean }): string {
  const box = 'position:sticky;top:0;z-index:2147483647;background:#111;color:#fff;font:14px/1.4 system-ui,sans-serif;padding:.6rem 1rem;'
    + 'display:flex;flex-wrap:wrap;gap:.75rem;align-items:center';
  const status = st.approved
    ? `Aprovado em ${esc(st.approved.approved_at.slice(0, 16).replace('T', ' '))} UTC. O Roger publica esta versão.`
    : `${st.stale ? 'A versão mudou depois da última aprovação. ' : ''}Aguardando a sua aprovação.`;
  const form = st.approved ? '' : `<form method="post" action="/admin/rascunhos/${m.slug}/aprovar" style="margin:0;display:flex;gap:.5rem;align-items:center">
<input type="hidden" name="html" value="${html}"><label style="color:#fff"><input type="checkbox" name="confirmar" value="sim" required> confirmo</label>
<button style="font:inherit;padding:.25rem .75rem">Aprovar publicação</button></form>`;
  return `<div id="rascunho-barra" style="${box}"><strong>Rascunho privado</strong><span>${status}</span>${form}
<a href="/admin/rascunhos" style="color:#fff">todos os rascunhos</a></div>`;
}

async function preview(env: DraftEnv, slug: string): Promise<Response> {
  const m = await readManifest(env.DRAFTS, slug);
  const obj = m && (await env.DRAFTS.get(`${slug}/index.html`));
  if (!m || !obj) return text(404, 'rascunho não encontrado');
  const raw = await obj.arrayBuffer();
  const hash = await sha256Hex(raw);
  const st = state(await approvals(env.DB, slug), hash);
  const html = new TextDecoder().decode(raw);
  const at = html.search(/<body\b[^>]*>/i);
  if (at < 0) return text(500, 'prévia sem <body>');
  const end = html.indexOf('>', at) + 1;
  const out = html.slice(0, end) + approvalBar(m, hash, st) + html.slice(end);
  return new Response(out, { status: 200, headers: headers('text/html; charset=utf-8', PREVIEW_CSP) });
}

async function asset(env: DraftEnv, slug: string, file: string): Promise<Response> {
  if (!FILE_RE.test(file) || file === 'index.html' || file === 'manifest.json') return text(404, 'não encontrado');
  const m = await readManifest(env.DRAFTS, slug);
  const type = m?.files[file];
  if (!m || !type) return text(404, 'não encontrado');
  const obj = await env.DRAFTS.get(`${slug}/${file}`);
  if (!obj) return text(404, 'não encontrado');
  return new Response(await obj.arrayBuffer(), { status: 200, headers: headers(type, PREVIEW_CSP) });
}

async function notify(env: DraftEnv, deps: DraftDeps, m: Manifest, approval: { at: string; by: string; html: string }): Promise<string> {
  if (!env.DRAFT_NOTIFY_TO || !env.DRAFT_NOTIFY_FROM || !env.RESEND_API_KEY) return 'aviso sem configuração';
  try {
    const res = await deps.fetch('https://api.resend.com/emails', {
      method: 'POST',
      headers: { authorization: `Bearer ${env.RESEND_API_KEY}`, 'content-type': 'application/json' },
      body: JSON.stringify({
        from: env.DRAFT_NOTIFY_FROM,
        to: [env.DRAFT_NOTIFY_TO],
        subject: `Rascunho aprovado: ${m.title}`,
        text: `O Gui aprovou a publicação do rascunho "${m.title}" (${m.slug}).\n\nAprovado em: ${approval.at}\nPor: ${approval.by}\n`
          + `HTML aprovado (sha256): ${approval.html}\nFonte aprovada (sha256): ${m.source_sha256}\n\nPrévia: ${env.SITE_ORIGIN}/admin/rascunhos/${m.slug}/\n`,
      }),
    });
    return res.ok ? '' : `Resend respondeu ${res.status}`;
  } catch {
    return 'falha de rede';
  }
}

async function approve(req: Request, env: DraftEnv, deps: DraftDeps, slug: string, email: string): Promise<Response> {
  // Formulário só da própria prévia: Origin igual ao do site (o Access já exige login; isto fecha CSRF).
  if (req.headers.get('origin') !== env.SITE_ORIGIN) return text(403, 'origem não permitida');
  if (!(req.headers.get('content-type') ?? '').startsWith('application/x-www-form-urlencoded')) return text(415, 'formato não suportado');
  const raw = await req.text();
  if (raw.length > 2048) return text(413, 'formulário grande demais');
  const form = new URLSearchParams(raw);
  if (form.get('confirmar') !== 'sim') return text(400, 'marque "confirmo" para aprovar');
  const seen = form.get('html') ?? '';
  const m = await readManifest(env.DRAFTS, slug);
  const obj = m && (await env.DRAFTS.get(`${slug}/index.html`));
  if (!m || !obj) return text(404, 'rascunho não encontrado');
  // Só vale para a versão que foi vista: se a prévia mudou depois que a página abriu, nada é gravado.
  const current = await sha256Hex(await obj.arrayBuffer());
  if (!SHA_RE.test(seen) || seen !== current) {
    return text(409, 'a prévia mudou depois que você abriu a página; recarregue e aprove de novo. Nada foi gravado.');
  }
  const at = new Date(deps.now()).toISOString();
  let id: number;
  try {
    const row = await env.DB.prepare(`INSERT INTO draft_approvals (slug, title, html_sha256, source_sha256, approved_at, approved_by)
      VALUES (?, ?, ?, ?, ?, ?) RETURNING id`).bind(slug, m.title, current, m.source_sha256, at, email).first<{ id: number }>();
    if (!row) throw new Error('sem id');
    id = row.id;
  } catch {
    return text(502, 'Não foi possível gravar a aprovação. Nada foi aprovado; tente de novo.');
  }
  // Aprovação já gravada; o aviso por email é melhor esforço, com o resultado na própria linha (diagnosticável).
  const err = await notify(env, deps, m, { at, by: email, html: current });
  try {
    await env.DB.prepare('UPDATE draft_approvals SET notified_at = ?, notify_error = ? WHERE id = ?')
      .bind(err ? '' : new Date(deps.now()).toISOString(), err, id).run();
  } catch {
    // A aprovação continua valendo; só o registro do aviso falhou.
  }
  return new Response(null, { status: 303, headers: { location: `/admin/rascunhos?msg=${err ? 'aprovado-sem-aviso' : 'aprovado'}`,
    ...headers('text/plain; charset=utf-8', ADMIN_CSP) } });
}

const MESSAGES: Record<string, string> = {
  aprovado: 'Aprovado. O Roger foi avisado por email e publica esta versão.',
  'aprovado-sem-aviso': 'Aprovado e gravado, mas o aviso por email ao Roger falhou. Avise no chat.',
};

/** Rotas de /admin/rascunhos, já autenticadas pelo chamador. null = não é rota de rascunho. */
export async function handleDrafts(req: Request, url: URL, env: DraftEnv, deps: DraftDeps, email: string): Promise<Response | null> {
  const path = url.pathname;
  if (path === '/admin/rascunhos' || path === '/admin/rascunhos/') {
    if (req.method !== 'GET') return text(405, 'método não permitido');
    return listPage(env, MESSAGES[url.searchParams.get('msg') ?? ''] ?? '');
  }
  const m = path.match(/^\/admin\/rascunhos\/([^/]+)(\/(.*))?$/);
  if (!m) return null;
  const slug = m[1];
  if (!SLUG_RE.test(slug)) return text(404, 'não encontrado');
  const rest = m[3];
  if (m[2] === undefined) {
    // Sem barra final, os caminhos relativos do post quebrariam: redireciona para a forma canônica.
    return req.method === 'GET' ? new Response(null, { status: 308, headers: { location: `/admin/rascunhos/${slug}/` } }) : text(405, 'método não permitido');
  }
  if (rest === '') return req.method === 'GET' ? preview(env, slug) : text(405, 'método não permitido');
  if (rest === 'aprovar') return req.method === 'POST' ? approve(req, env, deps, slug, email) : text(405, 'método não permitido');
  return req.method === 'GET' ? asset(env, slug, rest) : text(405, 'método não permitido');
}

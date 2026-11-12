// Painel de assinantes da newsletter em guifav.dev/admin (issue #40). Só o Gui: Cloudflare Access na frente e o
// JWT do Access conferido aqui de novo. Lê a cópia do segmento no D1 (cron de hora em hora, src/sync.ts).
//   GET  /admin              lista, busca, filtros e contadores
//   GET  /admin/export.csv   os mesmos filtros, em CSV
//   POST /admin/unsubscribe  descadastra no Resend, com auditoria no contato, e atualiza a cópia
// Qualquer outro caminho que a rota pegue (ex.: /administrar) segue para o site no GitHub Pages.
import { accessEmail } from './access.ts';
import { AUDIT_PROPERTY, resend, syncSegment, SyncError, type D1Like, type SyncDeps } from './sync.ts';

export interface Env {
  DB: D1Like;
  RESEND_API_KEY: string;
  SEGMENT_ID: string;
  ACCESS_TEAM_DOMAIN: string;
  ACCESS_AUD: string;
  ALLOWED_EMAILS: string; // segredo: emails separados por vírgula
  SITE_ORIGIN: string;
}

export type Deps = SyncDeps & { origin: (req: Request) => Promise<Response> };

export const PAGE_SIZE = 100;

const SECURITY_HEADERS: Record<string, string> = {
  'cache-control': 'no-store',
  'x-robots-tag': 'noindex, nofollow',
  'x-frame-options': 'DENY',
  'x-content-type-options': 'nosniff',
  'referrer-policy': 'no-referrer',
  'content-security-policy': "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
};

const respond = (status: number, body: string, type: string, extra: Record<string, string> = {}) =>
  new Response(body, { status, headers: { 'content-type': type, ...SECURITY_HEADERS, ...extra } });
const text = (status: number, body: string) => respond(status, body, 'text/plain; charset=utf-8');
const redirect = (location: string) => new Response(null, { status: 303, headers: { location, ...SECURITY_HEADERS } });

export function esc(s: unknown): string {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!);
}

/** Célula de CSV: aspas sempre e neutraliza fórmula (=, +, -, @, tab, CR) para quem abrir no Excel ou Sheets. */
export function csvCell(v: unknown): string {
  let s = String(v ?? '');
  if (/^[=+\-@\t\r]/.test(s)) s = `'${s}`;
  return `"${s.replace(/"/g, '""')}"`;
}

export type Filters = { q: string; status: string; origin: string; paid: string; page: number };

export function filtersFrom(url: URL): Filters {
  const pick = (k: string, allowed: string[]) => (allowed.includes(url.searchParams.get(k) ?? '') ? url.searchParams.get(k)! : '');
  const page = Math.max(1, Math.min(10_000, Number.parseInt(url.searchParams.get('p') ?? '1', 10) || 1));
  return {
    q: (url.searchParams.get('q') ?? '').trim().toLowerCase().slice(0, 200),
    status: pick('status', ['inscrito', 'descadastrado']),
    origin: pick('origem', ['substack', 'site', 'outra']),
    paid: pick('pago', ['sim', 'nao']),
    page,
  };
}

const WHERE = `WHERE (?1 = '' OR instr(email, ?1) > 0)
  AND (?2 = '' OR (?2 = 'inscrito' AND unsubscribed = 0) OR (?2 = 'descadastrado' AND unsubscribed = 1))
  AND (?3 = '' OR origin = ?3)
  AND (?4 = '' OR (?4 = 'sim' AND paid_until != '') OR (?4 = 'nao' AND paid_until = ''))`;

type Row = { id: string; email: string; unsubscribed: number; origin: string; joined_at: string; paid_until: string; admin_unsubscribed_at: string };

async function query(db: D1Like, f: Filters, all: boolean) {
  const args = [f.q, f.status, f.origin, f.paid];
  const limit = all ? '' : ` LIMIT ${PAGE_SIZE} OFFSET ${(f.page - 1) * PAGE_SIZE}`;
  const rows = await db.prepare(`SELECT id, email, unsubscribed, origin, joined_at, paid_until, admin_unsubscribed_at
    FROM subscribers ${WHERE} ORDER BY joined_at DESC, email${limit}`).bind(...args).all<Row>();
  const n = await db.prepare(`SELECT count(*) AS n FROM subscribers ${WHERE}`).bind(...args).first<{ n: number }>();
  return { rows: rows.results, filtered: n?.n ?? 0 };
}

async function counters(db: D1Like) {
  const c = await db.prepare(`SELECT count(*) AS total,
      sum(unsubscribed = 0) AS inscritos, sum(unsubscribed = 1) AS descadastrados,
      sum(origin = 'substack') AS substack, sum(origin = 'site') AS site, sum(origin = 'outra') AS outra,
      sum(paid_until != '' AND unsubscribed = 0) AS pagos
    FROM subscribers`).first<Record<string, number | null>>();
  const meta = await db.prepare('SELECT k, v FROM meta').all<{ k: string; v: string }>();
  const m = Object.fromEntries(meta.results.map((r) => [r.k, r.v]));
  return { c: c ?? {}, m };
}

const LABEL: Record<string, string> = { substack: 'Substack', site: 'site', outra: 'outra' };

function page(f: Filters, data: Awaited<ReturnType<typeof query>>, k: Awaited<ReturnType<typeof counters>>, msg: string): string {
  const qs = (o: Partial<Filters>) => {
    const p = new URLSearchParams();
    const v = { ...f, ...o };
    if (v.q) p.set('q', v.q);
    if (v.status) p.set('status', v.status);
    if (v.origin) p.set('origem', v.origin);
    if (v.paid) p.set('pago', v.paid);
    if (v.page > 1) p.set('p', String(v.page));
    const s = p.toString();
    return s ? `?${s}` : '';
  };
  const opt = (name: string, cur: string, items: [string, string][]) =>
    `<select name="${name}">${items.map(([v, l]) => `<option value="${v}"${v === cur ? ' selected' : ''}>${l}</option>`).join('')}</select>`;
  const n = (x: number | null | undefined) => String(x ?? 0);
  const rows = data.rows.map((r) => `<tr>
    <td>${esc(r.email)}</td>
    <td>${r.unsubscribed ? 'descadastrado' + (r.admin_unsubscribed_at ? ` (painel, ${esc(r.admin_unsubscribed_at.slice(0, 10))})` : '') : 'inscrito'}</td>
    <td>${esc(LABEL[r.origin] ?? r.origin)}</td>
    <td>${esc(r.joined_at.slice(0, 10))}</td>
    <td>${esc(r.paid_until.slice(0, 10))}</td>
    <td>${r.unsubscribed ? '' : `<form method="post" action="/admin/unsubscribe"><input type="hidden" name="id" value="${esc(r.id)}"><label><input type="checkbox" name="confirmar" value="sim" required> confirmo</label> <button>Descadastrar</button></form>`}</td>
  </tr>`).join('');
  const pages = Math.max(1, Math.ceil(data.filtered / PAGE_SIZE));
  const err = k.m.last_sync_error ? `<p class="erro">Última sincronização com erro: ${esc(k.m.last_sync_error)}</p>` : '';
  return `<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow"><title>Assinantes | IA Aplicada</title>
<style>body{font:15px/1.45 system-ui,sans-serif;margin:1.5rem;color:#111}table{border-collapse:collapse;width:100%}th,td{border-bottom:1px solid #ddd;padding:.35rem .5rem;text-align:left;vertical-align:top}
.k{display:flex;flex-wrap:wrap;gap:1rem;margin:.5rem 0 1rem}.k div{background:#f4f4f4;padding:.4rem .7rem;border-radius:6px}.erro{color:#a00}.ok{color:#060}form.f{display:flex;flex-wrap:wrap;gap:.5rem;margin-bottom:1rem}
td form{margin:0}</style></head><body>
<h1>Assinantes da IA Aplicada</h1>
${msg ? `<p class="ok">${esc(msg)}</p>` : ''}${err}
<div class="k"><div>Total ${n(k.c.total)}</div><div>Inscritos ${n(k.c.inscritos)}</div><div>Descadastrados ${n(k.c.descadastrados)}</div>
<div>Substack ${n(k.c.substack)}</div><div>Site ${n(k.c.site)}</div><div>Outra origem ${n(k.c.outra)}</div><div>Pagos ativos ${n(k.c.pagos)}</div></div>
<p>Atualizado do Resend em ${esc(k.m.last_sync_at || 'nunca')}. Atualização automática a cada hora.</p>
<form class="f" method="get" action="/admin"><input name="q" value="${esc(f.q)}" placeholder="buscar email" aria-label="buscar email">
${opt('status', f.status, [['', 'todos os status'], ['inscrito', 'inscritos'], ['descadastrado', 'descadastrados']])}
${opt('origem', f.origin, [['', 'todas as origens'], ['substack', 'Substack'], ['site', 'site'], ['outra', 'outra']])}
${opt('pago', f.paid, [['', 'pagos e gratuitos'], ['sim', 'pagos'], ['nao', 'gratuitos']])}
<button>Filtrar</button> <a href="/admin">limpar</a> <a href="/admin/export.csv${qs({ page: 1 })}">exportar CSV</a></form>
<p>${data.filtered} encontrados${pages > 1 ? `, página ${f.page} de ${pages}` : ''}.</p>
<table><thead><tr><th>Email</th><th>Status</th><th>Origem</th><th>Entrada</th><th>Pago até</th><th></th></tr></thead><tbody>${rows}</tbody></table>
<p>${f.page > 1 ? `<a href="/admin${qs({ page: f.page - 1 })}">anterior</a> ` : ''}${f.page < pages ? `<a href="/admin${qs({ page: f.page + 1 })}">próxima</a>` : ''}</p>
</body></html>`;
}

async function unsubscribe(req: Request, env: Env, deps: Deps): Promise<Response> {
  // Formulário só do próprio painel: Origin igual ao do site (o Access já exige login; isto fecha CSRF).
  if (req.headers.get('origin') !== env.SITE_ORIGIN) return text(403, 'origem não permitida');
  if (!(req.headers.get('content-type') ?? '').startsWith('application/x-www-form-urlencoded')) return text(415, 'formato não suportado');
  const raw = await req.text();
  if (raw.length > 2048) return text(413, 'formulário grande demais');
  const form = new URLSearchParams(raw);
  const id = form.get('id') ?? '';
  if (form.get('confirmar') !== 'sim') return text(400, 'marque "confirmo" para descadastrar');
  const row = await env.DB.prepare('SELECT id FROM subscribers WHERE id = ?').bind(id).first<{ id: string }>();
  if (!row) return text(404, 'contato não encontrado no painel');
  const at = new Date(deps.now()).toISOString();
  const path = `/contacts/${encodeURIComponent(id)}`;
  try {
    await resend(env, deps, 'PATCH', path, { unsubscribed: true, properties: { [AUDIT_PROPERTY]: at } });
    // Sucesso só com prova: a releitura no Resend mostra o descadastro.
    const after = await resend(env, deps, 'GET', path);
    if (after.unsubscribed !== true) throw new SyncError('releitura sem o descadastro');
  } catch (e) {
    const why = e instanceof SyncError ? e.message : 'falha de rede';
    return respond(502, `<!doctype html><meta charset="utf-8"><p>Não foi possível descadastrar (${esc(why)}). Nada foi alterado no painel. <a href="/admin">voltar</a></p>`, 'text/html; charset=utf-8');
  }
  await env.DB.prepare('UPDATE subscribers SET unsubscribed = 1, admin_unsubscribed_at = ? WHERE id = ?').bind(at, id).run();
  return redirect('/admin?msg=descadastrado');
}

export function isAdminPath(pathname: string): boolean {
  return pathname === '/admin' || pathname.startsWith('/admin/');
}

export async function handle(req: Request, env: Env, deps: Deps): Promise<Response> {
  const url = new URL(req.url);
  if (!isAdminPath(url.pathname)) return deps.origin(req);
  const email = await accessEmail(req, {
    teamDomain: env.ACCESS_TEAM_DOMAIN,
    aud: env.ACCESS_AUD,
    allowedEmails: (env.ALLOWED_EMAILS ?? '').split(',').map((s) => s.trim().toLowerCase()).filter(Boolean),
  }, deps.fetch, deps.now());
  if (!email) return text(403, 'acesso negado');

  const p = url.pathname.replace(/\/+$/, '') || '/admin';
  if (p === '/admin' && req.method === 'GET') {
    const f = filtersFrom(url);
    const msg = url.searchParams.get('msg') === 'descadastrado' ? 'Descadastrado no Resend.' : '';
    return respond(200, page(f, await query(env.DB, f, false), await counters(env.DB), msg), 'text/html; charset=utf-8');
  }
  if (p === '/admin/export.csv' && req.method === 'GET') {
    const { rows } = await query(env.DB, filtersFrom(url), true);
    const head = ['email', 'status', 'origem', 'entrada', 'pago_ate', 'descadastrado_pelo_painel_em'];
    const lines = rows.map((r) => [r.email, r.unsubscribed ? 'descadastrado' : 'inscrito', r.origin, r.joined_at, r.paid_until,
      r.admin_unsubscribed_at].map(csvCell).join(','));
    return respond(200, [head.map(csvCell).join(','), ...lines].join('\r\n') + '\r\n', 'text/csv; charset=utf-8',
      { 'content-disposition': 'attachment; filename="assinantes-ia-aplicada.csv"' });
  }
  if (p === '/admin/unsubscribe') {
    return req.method === 'POST' ? unsubscribe(req, env, deps) : text(405, 'método não permitido');
  }
  return text(404, 'não encontrado');
}

const deps = (): Deps => ({
  fetch: (i, init) => fetch(i, init),
  now: Date.now,
  sleep: (ms) => new Promise((r) => setTimeout(r, ms)),
  origin: (req) => fetch(req),
});

export default {
  fetch: (req: Request, env: Env) => handle(req, env, deps()),
  scheduled: async (_c: unknown, env: Env, ctx: { waitUntil(p: Promise<unknown>): void }) => {
    ctx.waitUntil(syncSegment(env, deps()));
  },
};

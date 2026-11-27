// Painel do site em guifav.dev/admin (issues #40, #62, #82, #193). Só o Gui: Cloudflare Access na frente e o
// JWT do Access conferido aqui de novo. Lê só o D1 e o R2 do próprio Worker ao abrir.
//   GET  /admin              resumo do painel (redireciona 303 para /admin/assinantes com filtros/msg de assinantes)
//   GET  /admin/assinantes   lista de assinantes, busca, filtros e contadores (cópia do Resend, src/sync.ts)
//   GET  /admin/export.csv   os mesmos filtros dos assinantes, em CSV
//   POST /admin/unsubscribe  descadastra no Resend, com auditoria no contato, e atualiza a cópia
//   GET  /admin/contatos     conversões do D1, filtros, paginação e links para o Twenty (src/painel.ts)
//   GET  /admin/conteudos    materiais e arquivos, páginas de entrada e posts do catálogo (src/painel.ts)
//   /admin/rascunhos...      rascunhos privados do blog, aprovação e disparo da publicação (src/drafts.ts, issues #62 e #82)
// Cron (17 * * * *): copia o segmento do Resend (src/sync.ts) e o catálogo de posts (src/catalogo.ts), independentes.
// Qualquer outro caminho que a rota pegue (ex.: /administrar) segue para o site no GitHub Pages.
import { accessEmail } from './access.ts';
import { coletarCatalogo, type CatalogDeps } from './catalogo.ts';
import { handleDrafts, type DraftDeps, type R2Like } from './drafts.ts';
import { esc, htmlPage, redirect, respond, text } from './layout.ts';
import { contatosPage, conteudosPage, resumoPage } from './painel.ts';
import { AUDIT_PROPERTY, prop, resend, syncSegment, SyncError, topicSubscription, type D1Like, type SyncDeps } from './sync.ts';

export { esc };

export interface Env {
  DB: D1Like;
  RESEND_API_KEY: string;
  SEGMENT_ID: string;
  TOPIC_ID: string;
  ACCESS_TEAM_DOMAIN: string;
  ACCESS_AUD: string;
  ALLOWED_EMAILS: string; // segredo: emails separados por vírgula
  SITE_ORIGIN: string;
  TWENTY_ORIGIN: string; // base do Twenty na KVM (ex.: https://crm.guifav.dev)
  DRAFTS: R2Like; // bucket R2 privado blog-rascunhos
  DRAFT_NOTIFY_FROM: string; // segredo: remetente do aviso de rascunho aprovado
  DRAFT_NOTIFY_TO: string; // segredo: quem recebe o aviso (o Roger)
  GITHUB_DISPATCH_TOKEN: string; // segredo: token fino só deste repositório, só com Actions (disparo da publicação, #82)
}

export type Deps = SyncDeps &
  Pick<DraftDeps, 'siteTimeoutMs' | 'githubTimeoutMs' | 'mailTimeoutMs' | 'remocaoMaxPaginas'> &
  Pick<CatalogDeps, 'catalogTimeoutMs'> & { origin: (req: Request) => Promise<Response> };

export const PAGE_SIZE = 100;

const SUBSCRIBER_QUERY_KEYS = ['q', 'status', 'origem', 'pago', 'p', 'msg'] as const;

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
  const body = `<h1>Assinantes dos Expert Insights</h1>
${msg ? `<p class="ok">${esc(msg)}</p>` : ''}${err}
<div class="k"><div>Total ${n(k.c.total)}</div><div>Inscritos ${n(k.c.inscritos)}</div><div>Descadastrados ${n(k.c.descadastrados)}</div>
<div>Substack ${n(k.c.substack)}</div><div>Site ${n(k.c.site)}</div><div>Outra origem ${n(k.c.outra)}</div><div>Pagos ativos ${n(k.c.pagos)}</div></div>
<p>Atualizado do Resend em ${esc(k.m.last_sync_at || 'nunca')}. Atualização automática a cada hora.</p>
<form class="f" method="get" action="/admin/assinantes"><input name="q" value="${esc(f.q)}" placeholder="buscar email" aria-label="buscar email">
${opt('status', f.status, [['', 'todos os status'], ['inscrito', 'inscritos'], ['descadastrado', 'descadastrados']])}
${opt('origem', f.origin, [['', 'todas as origens'], ['substack', 'Substack'], ['site', 'site'], ['outra', 'outra']])}
${opt('pago', f.paid, [['', 'pagos e gratuitos'], ['sim', 'pagos'], ['nao', 'gratuitos']])}
<button>Filtrar</button> <a href="/admin/assinantes">limpar</a> <a href="/admin/export.csv${qs({ page: 1 })}">exportar CSV</a></form>
<p>${data.filtered} encontrados${pages > 1 ? `, página ${f.page} de ${pages}` : ''}.</p>
<table><thead><tr><th>Email</th><th>Status</th><th>Origem</th><th>Entrada</th><th>Pago até</th><th></th></tr></thead><tbody>${rows}</tbody></table>
<p>${f.page > 1 ? `<a href="/admin/assinantes${qs({ page: f.page - 1 })}">anterior</a> ` : ''}${f.page < pages ? `<a href="/admin/assinantes${qs({ page: f.page + 1 })}">próxima</a>` : ''}</p>`;
  return htmlPage({ title: 'Assinantes | Expert Insights', atual: 'assinantes', body });
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
  if (!env.TOPIC_ID) return text(500, 'painel sem configuração do tópico; nada foi alterado');
  const fail = (msg: string) => respond(502, `<!doctype html><meta charset="utf-8"><p>${esc(msg)} <a href="/admin/assinantes">voltar</a></p>`,
    'text/html; charset=utf-8');
  let row: { id: string } | null;
  try {
    row = await env.DB.prepare('SELECT id FROM subscribers WHERE id = ?').bind(id).first<{ id: string }>();
  } catch {
    return fail('Não foi possível ler a cópia do painel. Nada foi alterado.');
  }
  if (!row) return text(404, 'contato não encontrado no painel');
  const at = new Date(deps.now()).toISOString();
  const path = `/contacts/${encodeURIComponent(id)}`;
  try {
    // Só da newsletter (tópico), não da conta inteira: a pessoa continua recebendo os outros envios.
    // Auditoria primeiro: se a saída do tópico falhar, nada indica descadastro no painel.
    await resend(env, deps, 'PATCH', path, { properties: { [AUDIT_PROPERTY]: at } });
    await resend(env, deps, 'PATCH', `${path}/topics`, [{ id: env.TOPIC_ID, subscription: 'opt_out' }]);
    // Sucesso só com prova: a releitura mostra o mesmo contato com esta auditoria e fora do tópico.
    const after = await resend(env, deps, 'GET', path);
    if (after.id !== id || prop(after, AUDIT_PROPERTY) !== at) throw new SyncError('releitura sem a auditoria');
    if ((await topicSubscription(env, deps, id)) !== 'opt_out') throw new SyncError('releitura ainda no tópico');
  } catch (e) {
    const why = e instanceof SyncError ? e.message : 'falha de rede';
    return fail(`Não foi possível descadastrar (${why}). Nada foi alterado no painel.`);
  }
  try {
    // rev + 1: o cron que tirou as revisões antes desta gravação mantém este status (já confirmado no Resend).
    await env.DB.prepare('UPDATE subscribers SET unsubscribed = 1, admin_unsubscribed_at = ?, rev = rev + 1 WHERE id = ?').bind(at, id).run();
  } catch {
    return fail('Descadastrado no Resend, mas a cópia do painel não foi atualizada; a próxima sincronização (a cada hora) corrige.');
  }
  return redirect('/admin/assinantes?msg=descadastrado');
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

  const draft = await handleDrafts(req, url, env, deps, email);
  if (draft) return draft;

  const p = url.pathname.replace(/\/+$/, '') || '/admin';
  if (p === '/admin') {
    if (req.method !== 'GET') return text(405, 'método não permitido');
    if (SUBSCRIBER_QUERY_KEYS.some((k) => url.searchParams.has(k))) {
      return redirect(`/admin/assinantes${url.search}`);
    }
    return resumoPage(env, deps.now(), () => counters(env.DB));
  }
  if (p === '/admin/assinantes') {
    if (req.method !== 'GET') return text(405, 'método não permitido');
    const f = filtersFrom(url);
    const msg = url.searchParams.get('msg') === 'descadastrado' ? 'Descadastrado no Resend.' : '';
    return respond(200, page(f, await query(env.DB, f, false), await counters(env.DB), msg), 'text/html; charset=utf-8');
  }
  if (p === '/admin/contatos') {
    if (req.method !== 'GET') return text(405, 'método não permitido');
    return contatosPage(url, env);
  }
  if (p === '/admin/conteudos') {
    if (req.method !== 'GET') return text(405, 'método não permitido');
    return conteudosPage(url, env, deps.now());
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
    const d = deps();
    ctx.waitUntil(syncSegment(env, d));
    ctx.waitUntil(coletarCatalogo(env, d));
  },
};

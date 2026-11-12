// Cópia do segmento do Resend para o D1 (cron do Worker do painel, issue #40).
// O Resend não devolve propriedades na listagem: um GET por contato, espaçado para ficar bem abaixo do limite de
// 10 req/s da conta, que é compartilhado com o Worker de inscrição. A troca é atômica do ponto de vista do painel:
// as linhas antigas só saem depois que todas as novas foram gravadas; falha no meio não apaga nada.

export interface D1Stmt {
  bind(...v: unknown[]): D1Stmt;
  run(): Promise<unknown>;
  all<T = Record<string, unknown>>(): Promise<{ results: T[] }>;
  first<T = Record<string, unknown>>(): Promise<T | null>;
}
export interface D1Like {
  prepare(sql: string): D1Stmt;
  batch(stmts: D1Stmt[]): Promise<unknown[]>;
}

export interface SyncDeps {
  fetch: typeof fetch;
  now: () => number;
  sleep: (ms: number) => Promise<void>;
}

export interface SyncEnv {
  DB: D1Like;
  RESEND_API_KEY: string;
  SEGMENT_ID: string;
}

export const RESEND = 'https://api.resend.com';
export const PAGE = 100;
export const MAX_PAGES = 200;
export const GAP_MS = 250; // 4 req/s
const MAX_RETRIES = 4;
const BATCH = 50;
export const AUDIT_PROPERTY = 'admin_unsubscribed_at';

export class SyncError extends Error {}

type Contact = { id: string; email: string; unsubscribed?: boolean; created_at?: string; properties?: Record<string, unknown> };

export function prop(c: { properties?: Record<string, unknown> }, key: string): string {
  let v = (c.properties ?? {})[key];
  if (v && typeof v === 'object' && 'value' in (v as object)) v = (v as { value: unknown }).value;
  return v === null || v === undefined ? '' : String(v);
}

export function rowFrom(c: Contact, run: string) {
  const source = prop(c, 'source');
  const site = prop(c, 'site_confirmed_at');
  const origin = source === 'substack' ? 'substack' : site ? 'site' : 'outra';
  const created = String(c.created_at ?? '');
  const joined = origin === 'substack' ? prop(c, 'substack_joined_at') || created : origin === 'site' ? site : created;
  return {
    id: c.id, email: String(c.email).toLowerCase(), unsubscribed: c.unsubscribed ? 1 : 0, origin, joined_at: joined,
    paid_until: prop(c, 'substack_paid_until'), site_confirmed_at: site, admin_unsubscribed_at: prop(c, AUDIT_PROPERTY),
    created_at: created, run,
  };
}

export async function resend(env: SyncEnv, deps: SyncDeps, method: string, path: string, body?: unknown): Promise<Record<string, unknown>> {
  for (let i = 0; i < MAX_RETRIES; i++) {
    const res = await deps.fetch(`${RESEND}${path}`, {
      method,
      headers: { authorization: `Bearer ${env.RESEND_API_KEY}`, 'content-type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (res.status === 429) {
      const ra = Number(res.headers.get('retry-after'));
      await deps.sleep(Number.isFinite(ra) && ra > 0 ? Math.min(ra, 60) * 1000 : 1000);
      continue;
    }
    // Só o código e a rota fixa: nunca o caminho (pode ter id ou email) nem o texto da API.
    if (!res.ok) throw new SyncError(`Resend ${res.status} em ${method} ${path.split('/')[1].split('?')[0]}`);
    const data = await res.json();
    return data && typeof data === 'object' ? (data as Record<string, unknown>) : {};
  }
  throw new SyncError(`Resend 429 persistente em ${method} ${path.split('/')[1].split('?')[0]}`);
}

async function paged(env: SyncEnv, deps: SyncDeps, route: string, extra = ''): Promise<Record<string, unknown>[]> {
  const out: Record<string, unknown>[] = [];
  let after = '';
  for (let i = 0; i < MAX_PAGES; i++) {
    const q = new URLSearchParams({ limit: String(PAGE) });
    if (extra) q.set('segment_id', extra);
    if (after) q.set('after', after);
    const data = await resend(env, deps, 'GET', `/${route}?${q}`);
    await deps.sleep(GAP_MS);
    const page = Array.isArray(data.data) ? (data.data as Record<string, unknown>[]) : [];
    out.push(...page);
    const last = page.length ? String(page[page.length - 1].id ?? '') : '';
    if (data.has_more !== true || !last || last === after) return out;
    after = last;
  }
  throw new SyncError(`paginação sem fim em ${route}`);
}

/** Garante a propriedade de auditoria do descadastro pelo painel (o Resend recusa gravar propriedade inexistente). */
export async function ensureAuditProperty(env: SyncEnv, deps: SyncDeps): Promise<boolean> {
  const props = await paged(env, deps, 'contact-properties');
  if (props.some((p) => p.key === AUDIT_PROPERTY)) return false;
  await resend(env, deps, 'POST', '/contact-properties', { key: AUDIT_PROPERTY, type: 'string' });
  return true;
}

export async function syncSegment(env: SyncEnv, deps: SyncDeps): Promise<number> {
  const run = new Date(deps.now()).toISOString();
  try {
    await ensureAuditProperty(env, deps);
    const listed = await paged(env, deps, 'contacts', env.SEGMENT_ID);
    const rows = [];
    for (const c of listed) {
      if (typeof c.id !== 'string' || !c.id) throw new SyncError('contato sem id na listagem');
      const full = (await resend(env, deps, 'GET', `/contacts/${encodeURIComponent(c.id)}`)) as Contact;
      await deps.sleep(GAP_MS);
      rows.push(rowFrom({ ...full, id: c.id }, run));
    }
    const up = env.DB.prepare(
      `INSERT INTO subscribers (id, email, unsubscribed, origin, joined_at, paid_until, site_confirmed_at, admin_unsubscribed_at, created_at, run)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
       ON CONFLICT(id) DO UPDATE SET email=excluded.email, unsubscribed=excluded.unsubscribed, origin=excluded.origin,
         joined_at=excluded.joined_at, paid_until=excluded.paid_until, site_confirmed_at=excluded.site_confirmed_at,
         admin_unsubscribed_at=excluded.admin_unsubscribed_at, created_at=excluded.created_at, run=excluded.run`);
    for (let i = 0; i < rows.length; i += BATCH) {
      await env.DB.batch(rows.slice(i, i + BATCH).map((r) => up.bind(r.id, r.email, r.unsubscribed, r.origin, r.joined_at,
        r.paid_until, r.site_confirmed_at, r.admin_unsubscribed_at, r.created_at, r.run)));
    }
    // Só depois de gravar tudo: sai quem não está mais no segmento.
    await env.DB.batch([
      env.DB.prepare('DELETE FROM subscribers WHERE run != ?').bind(run),
      setMeta(env.DB, 'last_sync_at', run),
      setMeta(env.DB, 'last_sync_count', String(rows.length)),
      setMeta(env.DB, 'last_sync_error', ''),
    ]);
    return rows.length;
  } catch (e) {
    const msg = e instanceof SyncError ? e.message : `falha inesperada (${(e as Error)?.name ?? 'erro'})`;
    await env.DB.batch([setMeta(env.DB, 'last_sync_error', `${run}: ${msg}`)]);
    throw e;
  }
}

export function setMeta(db: D1Like, k: string, v: string): D1Stmt {
  return db.prepare('INSERT INTO meta (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v').bind(k, v);
}

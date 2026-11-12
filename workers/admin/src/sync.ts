// Cópia do segmento do Resend para o D1 (cron do Worker do painel, issue #40).
// O Resend não devolve propriedades nem tópicos na listagem: dois GET por contato (o contato e os tópicos dele),
// espaçados para ficar bem abaixo do limite de 10 req/s da conta, que é compartilhado com o Worker de inscrição. A troca é atômica do ponto de vista do painel:
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
  TOPIC_ID: string;
}

export const RESEND = 'https://api.resend.com';
export const PAGE = 100;
export const MAX_PAGES = 200;
export const GAP_MS = 250; // 4 req/s, para toda chamada ao Resend
const MAX_RETRIES = 4;
export const MAX_RETRY_AFTER_S = 60; // espera maior que isso: aborta e deixa para a próxima execução
export const DEADLINE_MS = 12 * 60 * 1000; // folga sobre os 15 min do cron
export const MAX_CALLS = 9000; // folga sobre os 10.000 subrequests do plano pago
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

// lastStart: início da chamada anterior. O espaçamento conta de início a início, para a latência da API não se somar
// à espera (medido em produção em 2026-11-12: espera depois da resposta dava ~2,2 req/s e 10 min para 647 contatos).
export type Budget = { start: number; calls: number; lastStart?: number };

export function checkDeadline(deps: SyncDeps, budget: Budget): void {
  if (deps.now() - budget.start > DEADLINE_MS) throw new SyncError('orçamento da execução esgotado');
}

export async function resend(env: SyncEnv, deps: SyncDeps, method: string, path: string, body?: unknown, budget?: Budget): Promise<Record<string, unknown>> {
  for (let i = 0; i < MAX_RETRIES; i++) {
    if (budget) {
      if (++budget.calls > MAX_CALLS) throw new SyncError('orçamento da execução esgotado');
      // Espaçamento central: nenhuma chamada da sincronização começa a menos de 250 ms do início da anterior.
      const wait = budget.lastStart === undefined ? 0 : budget.lastStart + GAP_MS - deps.now();
      if (wait > 0) await deps.sleep(wait);
      checkDeadline(deps, budget); // depois da espera, que pode atravessar o prazo: nenhuma chamada começa depois dele
      budget.lastStart = deps.now();
    }
    const res = await deps.fetch(`${RESEND}${path}`, {
      method,
      // Chamada em andamento também respeita o prazo: cancelada pelo tempo restante (corpo incluído).
      signal: budget ? AbortSignal.timeout(Math.max(1, DEADLINE_MS - (deps.now() - budget.start))) : undefined,
      headers: { authorization: `Bearer ${env.RESEND_API_KEY}`, 'content-type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (res.status === 429) {
      const ra = Number(res.headers.get('retry-after'));
      const wait = Number.isFinite(ra) && ra > 0 ? ra : 1;
      // Respeita o Retry-After inteiro; se for longo demais para o cron, desiste em vez de repetir antes da hora.
      if (wait > MAX_RETRY_AFTER_S) throw new SyncError(`Resend 429 com espera de ${wait} s em ${method} ${path.split('/')[1].split('?')[0]}`);
      await deps.sleep(wait * 1000);
      continue;
    }
    // Só o código e a rota fixa: nunca o caminho (pode ter id ou email) nem o texto da API.
    if (!res.ok) throw new SyncError(`Resend ${res.status} em ${method} ${path.split('/')[1].split('?')[0]}`);
    const data = await res.json();
    return data && typeof data === 'object' ? (data as Record<string, unknown>) : {};
  }
  throw new SyncError(`Resend 429 persistente em ${method} ${path.split('/')[1].split('?')[0]}`);
}

async function paged(env: SyncEnv, deps: SyncDeps, budget: Budget, route: string, extra = ''): Promise<Record<string, unknown>[]> {
  const out: Record<string, unknown>[] = [];
  let after = '';
  for (let i = 0; i < MAX_PAGES; i++) {
    const q = new URLSearchParams({ limit: String(PAGE) });
    if (extra) q.set('segment_id', extra);
    if (after) q.set('after', after);
    const data = await resend(env, deps, 'GET', `/${route}?${q}`, undefined, budget);
    // Só um término explícito vale: resposta sem lista, sem has_more booleano ou com cursor que não avança é erro,
    // e a cópia antiga fica intacta (a remoção só roda depois de uma listagem completa).
    if (!Array.isArray(data.data) || typeof data.has_more !== 'boolean') throw new SyncError(`listagem inválida em ${route}`);
    const page = data.data as Record<string, unknown>[];
    out.push(...page);
    if (!data.has_more) return out;
    const last = page.length ? String(page[page.length - 1].id ?? '') : '';
    if (!last || last === after) throw new SyncError(`paginação sem progresso em ${route}`);
    after = last;
  }
  throw new SyncError(`paginação sem fim em ${route}`);
}

/** Garante a propriedade de auditoria do descadastro pelo painel (o Resend recusa gravar propriedade inexistente). */
export async function ensureAuditProperty(env: SyncEnv, deps: SyncDeps, budget: Budget): Promise<boolean> {
  const props = await paged(env, deps, budget, 'contact-properties');
  if (props.some((p) => p.key === AUDIT_PROPERTY)) return false;
  await resend(env, deps, 'POST', '/contact-properties', { key: AUDIT_PROPERTY, type: 'string' }, budget);
  return true;
}

// Status do contato no tópico da newsletter. Formato real (2026-11-12): o tópico aparece mesmo sem estado explícito,
// com o padrão opt_in. Resposta sem lista, sem o tópico ou com valor desconhecido é erro (nada é publicado).
export async function topicSubscription(env: SyncEnv, deps: SyncDeps, id: string, budget?: Budget): Promise<'opt_in' | 'opt_out'> {
  const data = await resend(env, deps, 'GET', `/contacts/${encodeURIComponent(id)}/topics?limit=100`, undefined, budget);
  if (!Array.isArray(data.data)) throw new SyncError('tópicos do contato em formato inválido');
  const t = (data.data as Record<string, unknown>[]).find((x) => x?.id === env.TOPIC_ID);
  if (!t || (t.subscription !== 'opt_in' && t.subscription !== 'opt_out')) throw new SyncError('tópico da newsletter ausente no contato');
  return t.subscription;
}

export async function syncSegment(env: SyncEnv, deps: SyncDeps): Promise<number> {
  const run = new Date(deps.now()).toISOString();
  // Identidade desta execução. Quem começa por último é o dono: toda escrita é condicionada a isso, e uma execução
  // antiga que termine depois de uma nova não grava nada por cima dela.
  const runId = `${run}#${crypto.randomUUID().slice(0, 8)}`;
  const budget: Budget = { start: deps.now(), calls: 0 };
  try {
    if (!env.TOPIC_ID) throw new SyncError('sem TOPIC_ID: o status da newsletter depende do tópico');
    await setMeta(env.DB, 'sync_owner', runId).run();
    await ensureAuditProperty(env, deps, budget);
    const listed = await paged(env, deps, budget, 'contacts', env.SEGMENT_ID);
    // Revisões locais ANTES de qualquer GET: um descadastro gravado pelo painel depois disso muda a revisão, e esse
    // status vence a leitura deste ciclo. Ordem por contador no D1, sem comparar relógios de isolates.
    const revs = new Map(((await env.DB.prepare('SELECT id, rev FROM subscribers').all<{ id: string; rev: number }>()).results ?? [])
      .map((r) => [r.id, Number(r.rev)]));
    const rows = [];
    for (const c of listed) {
      if (typeof c.id !== 'string' || !c.id) throw new SyncError('contato sem id na listagem');
      const full = (await resend(env, deps, 'GET', `/contacts/${encodeURIComponent(c.id)}`, undefined, budget)) as Contact;
      // Descadastrado da newsletter: saiu do tópico dela, ou da conta inteira (status global do Resend).
      const out = (await topicSubscription(env, deps, c.id, budget)) === 'opt_out';
      rows.push({ ...rowFrom({ ...full, id: c.id, unsubscribed: Boolean(full.unsubscribed) || out }, runId), rev: revs.get(c.id) ?? 0 });
    }
    const owned = "EXISTS (SELECT 1 FROM meta WHERE k = 'sync_owner' AND v = ?)";
    const up = env.DB.prepare(
      `INSERT INTO subscribers (id, email, unsubscribed, origin, joined_at, paid_until, site_confirmed_at, admin_unsubscribed_at, created_at, run, rev)
       SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ? WHERE ${owned}
       ON CONFLICT(id) DO UPDATE SET email=excluded.email, origin=excluded.origin,
         joined_at=excluded.joined_at, paid_until=excluded.paid_until, site_confirmed_at=excluded.site_confirmed_at,
         created_at=excluded.created_at, run=excluded.run,
         -- Revisão mudou desde antes da leitura: o painel descadastrou no meio, e o status local (já confirmado no
         -- Resend) vence; fora disso vale o Resend, mesmo com a auditoria limpa ou o status mudado lá.
         unsubscribed = CASE WHEN subscribers.rev != excluded.rev THEN subscribers.unsubscribed ELSE excluded.unsubscribed END,
         admin_unsubscribed_at = CASE WHEN subscribers.rev != excluded.rev THEN subscribers.admin_unsubscribed_at ELSE excluded.admin_unsubscribed_at END`);
    for (let i = 0; i < rows.length; i += BATCH) {
      checkDeadline(deps, budget); // nenhum lote começa depois do prazo
      await env.DB.batch(rows.slice(i, i + BATCH).map((r) => up.bind(r.id, r.email, r.unsubscribed, r.origin, r.joined_at,
        r.paid_until, r.site_confirmed_at, r.admin_unsubscribed_at, r.created_at, r.run, r.rev, runId)));
    }
    const owner = await env.DB.prepare("SELECT v FROM meta WHERE k = 'sync_owner'").first<{ v: string }>();
    if (owner?.v !== runId) throw new SyncError('execução substituída por outra mais nova');
    checkDeadline(deps, budget); // nem a publicação: conferido logo antes da transação final
    // Só depois de gravar tudo, na mesma transação dos metadados e ainda condicionado ao dono: sai quem não está mais no segmento.
    await env.DB.batch([
      env.DB.prepare(`DELETE FROM subscribers WHERE run != ? AND ${owned}`).bind(runId, runId),
      setMeta(env.DB, 'last_sync_at', run, runId),
      setMeta(env.DB, 'last_sync_count', String(rows.length), runId),
      setMeta(env.DB, 'last_sync_ms', String(deps.now() - budget.start), runId),
      setMeta(env.DB, 'last_sync_error', '', runId),
    ]);
    return rows.length;
  } catch (e) {
    const msg = e instanceof SyncError ? e.message : `falha inesperada (${(e as Error)?.name ?? 'erro'})`;
    // Uma execução substituída não sobrescreve o estado publicado pela mais nova.
    await env.DB.batch([setMeta(env.DB, 'last_sync_error', `${run}: ${msg}`, runId)]);
    throw e;
  }
}

export function setMeta(db: D1Like, k: string, v: string, owner?: string): D1Stmt {
  if (owner === undefined) return db.prepare('INSERT INTO meta (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v').bind(k, v);
  return db.prepare(`INSERT INTO meta (k, v) SELECT ?, ? WHERE EXISTS (SELECT 1 FROM meta WHERE k = 'sync_owner' AND v = ?)
    ON CONFLICT(k) DO UPDATE SET v=excluded.v`).bind(k, v, owner);
}

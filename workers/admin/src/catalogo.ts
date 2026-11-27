// Coleta do catálogo de posts do blog (issue #193, fase 1 do épico #192).
// O build do blog publica /blog/catalogo.json a cada merge; este coletor lê o JSON no cron de hora em hora, valida
// todos os itens e substitui `content_catalog` numa única transação, registrando o estado em `collectors`.
// Qualquer falha preserva o catálogo anterior e registra só a tentativa e o motivo curto.
import type { D1Like, D1Stmt } from './sync.ts';

export const CATALOG_SOURCE = 'catalogo_posts';
export const CATALOG_TIMEOUT_MS = 10_000;
export const CATALOG_MAX_BYTES = 2 << 20; // 2 MiB
export const CATALOG_MAX_ITEMS = 5000;
// Cada linha usa 3 parâmetros; 30 linhas por INSERT = 90 parâmetros, abaixo do limite de 100 do D1.
export const CATALOG_INSERT_CHUNK = 30;

// Mesmo padrão do CHECK da migração 0009 e do SLUG_RE de drafts.ts: /blog/<slug>/ com até 200 caracteres.
export const CATALOG_PATH_RE = /^\/blog\/[a-z0-9]+(?:-[a-z0-9]+)*\/$/;
const CONTROL_RE = /[\u0000-\u001f\u007f-\u009f]/;

export interface CatalogEnv {
  DB: D1Like;
  SITE_ORIGIN: string;
}

export interface CatalogDeps {
  fetch: typeof fetch;
  now: () => number;
  catalogTimeoutMs?: number;
}

export interface CatalogItem {
  caminho: string;
  titulo: string;
  data: string;
}

export class CatalogError extends Error {}

/** Data AAAA-MM-DD que existe no calendário (rejeita 2026-02-30, 2026-02-29, 2026-13-01, etc.). */
export function dataExiste(s: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(s)) return false;
  const [y, m, d] = s.split('-').map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d));
  return dt.getUTCFullYear() === y && dt.getUTCMonth() === m - 1 && dt.getUTCDate() === d;
}

/** Valida o JSON de /blog/catalogo.json por inteiro antes de tocar no D1. */
export function validarCatalogo(raw: unknown): CatalogItem[] {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new CatalogError('JSON fora do formato esperado');
  const posts = (raw as { posts?: unknown }).posts;
  if (!Array.isArray(posts)) throw new CatalogError('campo posts ausente ou inválido');
  if (posts.length === 0) throw new CatalogError('catálogo vazio');
  if (posts.length > CATALOG_MAX_ITEMS) throw new CatalogError(`catálogo acima de ${CATALOG_MAX_ITEMS} itens`);
  const seen = new Set<string>();
  const out: CatalogItem[] = [];
  for (let i = 0; i < posts.length; i++) {
    const item = posts[i];
    if (!item || typeof item !== 'object' || Array.isArray(item)) throw new CatalogError(`item ${i} inválido`);
    const r = item as Record<string, unknown>;
    const caminho = r.caminho;
    const titulo = r.titulo;
    const data = r.data;
    if (typeof caminho !== 'string' || caminho.length > 200 || !CATALOG_PATH_RE.test(caminho)) {
      throw new CatalogError(`caminho inválido no item ${i}`);
    }
    if (seen.has(caminho)) throw new CatalogError(`caminho repetido no item ${i}`);
    seen.add(caminho);
    if (typeof titulo !== 'string' || titulo.length < 1 || titulo.length > 300 || CONTROL_RE.test(titulo)) {
      throw new CatalogError(`título inválido no item ${i}`);
    }
    if (typeof data !== 'string' || !dataExiste(data)) {
      throw new CatalogError(`data inválida no item ${i}`);
    }
    out.push({ caminho, titulo, data });
  }
  return out;
}

/** Lê o corpo até `limite` bytes; se passar, cancela o stream e lança CatalogError. */
async function lerCorpoLimitado(res: Response, limite: number): Promise<string> {
  if (!res.body) {
    const buf = await res.arrayBuffer();
    if (buf.byteLength > limite) throw new CatalogError('corpo acima de 2 MiB');
    return new TextDecoder().decode(buf);
  }
  const reader = res.body.getReader();
  const partes: Uint8Array[] = [];
  let total = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > limite) throw new CatalogError('corpo acima de 2 MiB');
      partes.push(value);
    }
  } finally {
    await reader.cancel().catch(() => {});
  }
  const bytes = new Uint8Array(total);
  let at = 0;
  for (const p of partes) {
    bytes.set(p, at);
    at += p.byteLength;
  }
  return new TextDecoder().decode(bytes);
}

async function registrarErro(db: D1Like, attemptAt: string, erro: string): Promise<void> {
  await db
    .prepare(
      `INSERT INTO collectors (source, last_attempt_at, last_error)
       VALUES (?, ?, ?)
       ON CONFLICT(source) DO UPDATE SET
         last_attempt_at = excluded.last_attempt_at,
         last_error = excluded.last_error`,
    )
    .bind(CATALOG_SOURCE, attemptAt, erro.slice(0, 200))
    .run();
}

export type ColetaResult = { ok: true; count: number } | { ok: false; error: string };

/** Baixa /blog/catalogo.json, valida e grava no D1 numa única transação. Nunca apaga o catálogo anterior em caso de erro. */
export async function coletarCatalogo(env: CatalogEnv, deps: CatalogDeps): Promise<ColetaResult> {
  const nowIso = new Date(deps.now()).toISOString();
  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort(new DOMException('prazo', 'TimeoutError')), deps.catalogTimeoutMs ?? CATALOG_TIMEOUT_MS);
  try {
    const url = `${env.SITE_ORIGIN}/blog/catalogo.json`;
    const res = await deps.fetch(url, { headers: { accept: 'application/json' }, signal: ac.signal });
    if (res.status !== 200) {
      res.body?.cancel().catch(() => {});
      throw new CatalogError(`site respondeu ${res.status}`);
    }
    const ct = (res.headers.get('content-type') ?? '').split(';')[0].trim().toLowerCase();
    if (ct !== 'application/json') {
      res.body?.cancel().catch(() => {});
      throw new CatalogError('resposta sem content-type JSON');
    }
    const rawText = await lerCorpoLimitado(res, CATALOG_MAX_BYTES);
    let parsed: unknown;
    try {
      parsed = JSON.parse(rawText);
    } catch {
      throw new CatalogError('JSON inválido');
    }
    const items = validarCatalogo(parsed);
    const stmts: D1Stmt[] = [env.DB.prepare('DELETE FROM content_catalog')];
    for (let i = 0; i < items.length; i += CATALOG_INSERT_CHUNK) {
      const slice = items.slice(i, i + CATALOG_INSERT_CHUNK);
      const placeholders = slice.map(() => '(?, ?, ?)').join(', ');
      const args: unknown[] = [];
      for (const it of slice) args.push(it.caminho, it.titulo, it.data);
      stmts.push(env.DB.prepare(`INSERT INTO content_catalog (path, title, published_on) VALUES ${placeholders}`).bind(...args));
    }
    stmts.push(
      env.DB
        .prepare(
          `INSERT INTO collectors (source, last_attempt_at, last_success_at, last_error, item_count)
           VALUES (?, ?, ?, NULL, ?)
           ON CONFLICT(source) DO UPDATE SET
             last_attempt_at = excluded.last_attempt_at,
             last_success_at = excluded.last_success_at,
             last_error = NULL,
             item_count = excluded.item_count`,
        )
        .bind(CATALOG_SOURCE, nowIso, nowIso, items.length),
    );
    await env.DB.batch(stmts);
    return { ok: true, count: items.length };
  } catch (e) {
    let msg: string;
    if (e instanceof CatalogError) {
      msg = e.message;
    } else if (ac.signal.aborted || (e instanceof Error && (e.name === 'TimeoutError' || e.name === 'AbortError'))) {
      msg = 'tempo esgotado';
    } else if (e instanceof TypeError) {
      msg = 'falha de rede';
    } else {
      msg = `falha ao gravar (${e instanceof Error ? e.name : 'erro'})`;
    }
    try {
      await registrarErro(env.DB, nowIso, msg);
    } catch {
      // Falha dupla (ex.: D1 fora do ar): devolve o erro original sem derrubar o resto do cron.
    }
    return { ok: false, error: msg };
  } finally {
    clearTimeout(timer);
  }
}

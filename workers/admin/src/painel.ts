// Telas do painel do site (issue #193, fase 1 do épico #192): resumo (/admin), contatos (/admin/contatos) e conteúdos
// (/admin/conteudos). Todas leem só o D1 e (no resumo) o R2 de rascunhos; nenhuma chama serviço externo ao abrir.
import { MATERIAIS, arquivo, material } from '../../newsletter/src/materiais.ts';
import { CANAIS, entradaValida } from '../../newsletter/src/origem.ts';
import { CATALOG_SOURCE } from './catalogo.ts';
import { rascunhosAguardando, type R2Like } from './drafts.ts';
import { esc, formatDateTimeUtc, htmlPage, respond } from './layout.ts';
import type { D1Like } from './sync.ts';

export const PAINEL_PAGE_SIZE = 100;
export const TWENTY_ID_RE = /^[0-9A-Za-z-]{1,64}$/;

const DAY_MS = 86_400_000;
// Normaliza timestamps ISO UTC sem milissegundos ("...Z" de 20 chars) para "...000Z", evitando perda de precisão de
// ponto flutuante do strftime/julianday na comparação lexicográfica exata com Date.toISOString().
const TS = "CASE WHEN length(occurred_at) = 20 AND substr(occurred_at, 20, 1) = 'Z' THEN substr(occurred_at, 1, 19) || '.000Z' ELSE occurred_at END";
const TS_C = "CASE WHEN length(c.occurred_at) = 20 AND substr(c.occurred_at, 20, 1) = 'Z' THEN substr(c.occurred_at, 1, 19) || '.000Z' ELSE c.occurred_at END";

export interface PainelEnv {
  DB: D1Like;
  DRAFTS: R2Like;
  SITE_ORIGIN: string;
  TWENTY_ORIGIN: string;
}

export interface SubscriberCounters {
  c: Record<string, number | null>;
  m: Record<string, string>;
}

export const ACTIONS = ['material_request', 'material_download', 'newsletter_confirm'] as const;

const ACTION_LABELS: Record<string, string> = {
  material_request: 'pedido de material',
  material_download: 'download',
  newsletter_confirm: 'inscrição nos Expert Insights',
};

export function actionLabel(action: string): string {
  return ACTION_LABELS[action] ?? action;
}

/** URL da Pessoa no Twenty, ou null se o id não casa com ^[0-9A-Za-z-]{1,64}$ ou falta a base. */
export function twentyPersonUrl(personId: string | null | undefined, twentyOrigin: string): string | null {
  if (!personId || !TWENTY_ID_RE.test(personId)) return null;
  const base = twentyOrigin.replace(/\/+$/, '');
  if (!base) return null;
  return `${base}/object/person/${encodeURIComponent(personId)}`;
}

/** Célula da coluna Twenty (HTML escapado), usada em /admin e /admin/contatos. */
export function twentyCell(
  personId: string | null | undefined,
  syncedAt: string | null | undefined,
  verified: number,
  emailNoCrmOuComprovado: number | boolean,
  twentyOrigin: string,
): string {
  const url = twentyPersonUrl(personId, twentyOrigin);
  if (url) return `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">abrir no Twenty</a>`;
  if (syncedAt) return 'no CRM, sem link';
  if (verified === 1) return 'email confirmado: entra no CRM na próxima sincronização';
  if (emailNoCrmOuComprovado) return 'email já confirmado ou no CRM: entra na próxima sincronização';
  return 'email não confirmado: não abriu o link do material';
}

/** Célula de página de entrada: só vira link se o caminho casar com o CHECK da migração 0006. */
export function landingCell(path: string | null | undefined, siteOrigin: string, label?: string): string {
  if (!path) return '';
  const texto = label ?? path;
  if (!entradaValida(path)) return esc(texto);
  const href = `${siteOrigin.replace(/\/+$/, '')}${path}`;
  const link = `<a href="${esc(href)}">${esc(texto)}</a>`;
  return label && label !== path ? `${link}<br><small class="muted">${esc(path)}</small>` : link;
}

/** Célula de material e arquivo pelo catálogo MATERIAIS; ids desconhecidos saem com "(fora do catálogo)". */
export function materialCell(matId: string | null | undefined, fileId: string | null | undefined): string {
  const partes: string[] = [];
  const mat = matId ? material(matId) : undefined;
  if (matId) {
    partes.push(mat ? esc(mat.titulo) : `${esc(matId)} (fora do catálogo)`);
  }
  if (fileId) {
    const arq = mat?.arquivos.find((a) => a.id === fileId) ?? arquivo(fileId)?.arquivo;
    partes.push(arq ? `<small class="muted">${esc(arq.nome)}</small>` : `<small class="muted">${esc(fileId)} (fora do catálogo)</small>`);
  }
  return partes.join('<br>');
}

export function formatOrigin(channel: string | null | undefined, source: string | null | undefined): string {
  if (!channel) return 'sem medição';
  return source ? `${channel} / ${source}` : channel;
}

export function formatUtm(source: string | null | undefined, medium: string | null | undefined, campaign: string | null | undefined): string {
  return [source, medium, campaign].filter(Boolean).join(' / ');
}

function num(x: number | null | undefined): number {
  return Number(x ?? 0);
}

type CollectorRow = { last_attempt_at: string; last_success_at: string | null; last_error: string | null; item_count: number | null };

async function readCollector(db: D1Like): Promise<CollectorRow | null> {
  return db
    .prepare('SELECT last_attempt_at, last_success_at, last_error, item_count FROM collectors WHERE source = ?')
    .bind(CATALOG_SOURCE)
    .first<CollectorRow>();
}

function formatCatalogDate(iso: string | null | undefined): string {
  return iso ? `${formatDateTimeUtc(iso)} UTC` : '';
}

async function tryRead<T>(fn: () => Promise<T>): Promise<{ ok: true; value: T } | { ok: false }> {
  try {
    return { ok: true, value: await fn() };
  } catch {
    return { ok: false };
  }
}

// ------------------------------------------------------------------ Resumo (/admin)

type ConversionSummary = {
  req_7: number | null;
  dl_7: number | null;
  nl_7: number | null;
  req_30: number | null;
  dl_30: number | null;
  nl_30: number | null;
};

type RecentRow = {
  id: string;
  email: string;
  action: string;
  material: string | null;
  file: string | null;
  verified: number;
  occurred_at: string;
  synced_at: string | null;
  twenty_person_id: string | null;
  email_no_crm_ou_comprovado: number;
};

export async function resumoPage(
  env: PainelEnv,
  nowMs: number,
  loadSubscribers: () => Promise<SubscriberCounters>,
): Promise<Response> {
  const corte7 = new Date(nowMs - 7 * DAY_MS).toISOString();
  const corte30 = new Date(nowMs - 30 * DAY_MS).toISOString();

  const [subsRes, draftsRes, convRes, recentRes, catalogRes] = await Promise.all([
    tryRead(loadSubscribers),
    tryRead(() => rascunhosAguardando(env)),
    tryRead(() =>
      env.DB.prepare(
        `SELECT
           SUM(CASE WHEN action = 'material_request' AND ${TS} >= ?1 THEN 1 ELSE 0 END) AS req_7,
           SUM(CASE WHEN action = 'material_download' AND ${TS} >= ?1 THEN 1 ELSE 0 END) AS dl_7,
           SUM(CASE WHEN action = 'newsletter_confirm' AND ${TS} >= ?1 THEN 1 ELSE 0 END) AS nl_7,
           SUM(CASE WHEN action = 'material_request' AND ${TS} >= ?2 THEN 1 ELSE 0 END) AS req_30,
           SUM(CASE WHEN action = 'material_download' AND ${TS} >= ?2 THEN 1 ELSE 0 END) AS dl_30,
           SUM(CASE WHEN action = 'newsletter_confirm' AND ${TS} >= ?2 THEN 1 ELSE 0 END) AS nl_30
         FROM conversions
         WHERE ${TS} >= ?2`,
      )
        .bind(corte7, corte30)
        .first<ConversionSummary>(),
    ),
    tryRead(async () =>
      (
        await env.DB.prepare(
          `SELECT p.id, p.email, p.action, p.material, p.file, p.verified, p.occurred_at, p.synced_at, p.twenty_person_id,
                  EXISTS (
                    SELECT 1 FROM conversions c2
                    WHERE c2.email = p.email AND c2.id != p.id AND (c2.verified = 1 OR c2.synced_at IS NOT NULL)
                  ) AS email_no_crm_ou_comprovado
           FROM (
             SELECT id, email, action, material, file, verified, occurred_at, synced_at, twenty_person_id
             FROM conversions
             ORDER BY occurred_at DESC, id
             LIMIT 10
           ) p
           ORDER BY p.occurred_at DESC, p.id`,
        ).all<RecentRow>()
      ).results,
    ),
    tryRead(() => readCollector(env.DB)),
  ]);

  let cardSubs: string;
  if (subsRes.ok) {
    const { c, m } = subsRes.value;
    const err = m.last_sync_error ? `<p class="erro">Última sincronização com erro: ${esc(m.last_sync_error)}</p>` : '';
    cardSubs = `<div class="card"><h2><a href="/admin/assinantes">Assinantes</a></h2>
<p>Inscritos: <strong>${num(c.inscritos)}</strong> · Descadastrados: ${num(c.descadastrados)} · Total: ${num(c.total)}</p>
<p class="muted">Atualizado do Resend em ${esc(m.last_sync_at || 'nunca')}.</p>${err}</div>`;
  } else {
    cardSubs = `<div class="card"><h2><a href="/admin/assinantes">Assinantes</a></h2><p class="erro">indisponível agora</p></div>`;
  }

  let cardDrafts: string;
  if (draftsRes.ok) {
    const n = draftsRes.value;
    cardDrafts = `<div class="card"><h2><a href="/admin/rascunhos">Rascunhos</a></h2>
<p><strong>${n}</strong> ${n === 1 ? 'artigo aguardando aprovação' : 'artigos aguardando aprovação'}</p></div>`;
  } else {
    cardDrafts = `<div class="card"><h2><a href="/admin/rascunhos">Rascunhos</a></h2><p class="erro">indisponível agora</p></div>`;
  }

  let cardConv: string;
  if (convRes.ok) {
    const r = convRes.value;
    cardConv = `<div class="card"><h2><a href="/admin/contatos">Conversões</a></h2>
<p><strong>Últimos 7 dias:</strong> ${num(r?.req_7)} pedidos, ${num(r?.dl_7)} downloads, ${num(r?.nl_7)} inscrições</p>
<p><strong>Últimos 30 dias:</strong> ${num(r?.req_30)} pedidos, ${num(r?.dl_30)} downloads, ${num(r?.nl_30)} inscrições</p></div>`;
  } else {
    cardConv = `<div class="card"><h2><a href="/admin/contatos">Conversões</a></h2><p class="erro">indisponível agora</p></div>`;
  }

  let cardCatalog: string;
  if (catalogRes.ok) {
    const col = catalogRes.value;
    const statusLine = col?.last_success_at
      ? `<p>Catálogo de ${esc(formatCatalogDate(col.last_success_at))}${col.item_count !== null ? `, ${col.item_count} ${col.item_count === 1 ? 'post' : 'posts'}` : ''}.</p>`
      : '<p>O catálogo ainda não foi coletado.</p>';
    const errLine = col?.last_error
      ? `<p class="erro">A última coleta falhou em ${esc(formatCatalogDate(col.last_attempt_at))}: ${esc(col.last_error)}</p>`
      : '';
    cardCatalog = `<div class="card"><h2><a href="/admin/conteudos">Catálogo de posts</a></h2>${statusLine}${errLine}</div>`;
  } else {
    cardCatalog = `<div class="card"><h2><a href="/admin/conteudos">Catálogo de posts</a></h2><p class="erro">indisponível agora</p></div>`;
  }

  let recentBlock: string;
  if (recentRes.ok) {
    if (recentRes.value.length === 0) {
      recentBlock = '<p>Nenhuma conversão registrada.</p>';
    } else {
      const rows = recentRes.value
        .map(
          (r) => `<tr>
  <td>${esc(formatDateTimeUtc(r.occurred_at))}</td>
  <td>${esc(r.email)}</td>
  <td>${esc(actionLabel(r.action))}</td>
  <td>${materialCell(r.material, r.file)}</td>
  <td>${twentyCell(r.twenty_person_id, r.synced_at, r.verified, r.email_no_crm_ou_comprovado, env.TWENTY_ORIGIN)}</td>
</tr>`,
        )
        .join('');
      recentBlock = `<table><thead><tr><th>Data (UTC)</th><th>Email</th><th>Ação</th><th>Material</th><th>Twenty</th></tr></thead><tbody>${rows}</tbody></table>`;
    }
  } else {
    recentBlock = '<p class="erro">indisponível agora</p>';
  }

  const body = `<h1>Painel</h1>
<div class="cards">${cardSubs}${cardDrafts}${cardConv}${cardCatalog}</div>
<h2>Conversões recentes</h2>
${recentBlock}
<p><a href="/admin/contatos">ver todos os contatos</a></p>`;

  return respond(200, htmlPage({ title: 'Resumo | Painel', atual: 'resumo', body }), 'text/html; charset=utf-8');
}

// ------------------------------------------------------------------ Contatos (/admin/contatos)

export interface ContatosFilters {
  q: string;
  acao: string;
  material: string;
  comprovado: string;
  canal: string;
  page: number;
}

const MATERIAL_IDS = MATERIAIS.map((m) => m.id);
const CANAL_OPTIONS: string[] = [...CANAIS, 'sem'];

export function contatosFiltersFrom(url: URL): ContatosFilters {
  const pick = (k: string, allowed: readonly string[]) => (allowed.includes(url.searchParams.get(k) ?? '') ? url.searchParams.get(k)! : '');
  const page = Math.max(1, Math.min(10_000, Number.parseInt(url.searchParams.get('p') ?? '1', 10) || 1));
  return {
    q: (url.searchParams.get('q') ?? '').trim().toLowerCase().slice(0, 200),
    acao: pick('acao', ACTIONS),
    material: pick('material', MATERIAL_IDS),
    comprovado: pick('comprovado', ['sim', 'nao']),
    canal: pick('canal', CANAL_OPTIONS),
    page,
  };
}

const CONTATOS_WHERE = `WHERE (?1 = '' OR instr(lower(email), ?1) > 0)
  AND (?2 = '' OR action = ?2)
  AND (?3 = '' OR material = ?3)
  AND (?4 = '' OR (?4 = 'sim' AND verified = 1) OR (?4 = 'nao' AND verified = 0))
  AND (?5 = '' OR (?5 = 'sem' AND origin_channel IS NULL) OR (?5 != 'sem' AND origin_channel = ?5))`;

type ContatoRow = {
  id: string;
  email: string;
  action: string;
  material: string | null;
  file: string | null;
  verified: number;
  occurred_at: string;
  synced_at: string | null;
  origin_channel: string | null;
  origin_source: string | null;
  utm_source: string | null;
  utm_medium: string | null;
  utm_campaign: string | null;
  landing_path: string | null;
  twenty_person_id: string | null;
  email_no_crm_ou_comprovado: number;
};

export async function contatosPage(url: URL, env: PainelEnv): Promise<Response> {
  const f = contatosFiltersFrom(url);
  const args = [f.q, f.acao, f.material, f.comprovado, f.canal];
  const offset = (f.page - 1) * PAINEL_PAGE_SIZE;
  const rows = (
    await env.DB.prepare(
      `SELECT p.id, p.email, p.action, p.material, p.file, p.verified, p.occurred_at, p.synced_at,
              p.origin_channel, p.origin_source, p.utm_source, p.utm_medium, p.utm_campaign, p.landing_path, p.twenty_person_id,
              EXISTS (
                SELECT 1 FROM conversions c2
                WHERE c2.email = p.email AND c2.id != p.id AND (c2.verified = 1 OR c2.synced_at IS NOT NULL)
              ) AS email_no_crm_ou_comprovado
       FROM (
         SELECT id, email, action, material, file, verified, occurred_at, synced_at,
                origin_channel, origin_source, utm_source, utm_medium, utm_campaign, landing_path, twenty_person_id
         FROM conversions
         ${CONTATOS_WHERE}
         ORDER BY occurred_at DESC, id
         LIMIT ${PAINEL_PAGE_SIZE} OFFSET ${offset}
       ) p
       ORDER BY p.occurred_at DESC, p.id`,
    )
      .bind(...args)
      .all<ContatoRow>()
  ).results;
  const totalRow = await env.DB.prepare(`SELECT COUNT(*) AS n FROM conversions ${CONTATOS_WHERE}`).bind(...args).first<{ n: number }>();
  const filtered = num(totalRow?.n);
  const pages = Math.max(1, Math.ceil(filtered / PAINEL_PAGE_SIZE));

  const qs = (o: Partial<ContatosFilters>) => {
    const p = new URLSearchParams();
    const v = { ...f, ...o };
    if (v.q) p.set('q', v.q);
    if (v.acao) p.set('acao', v.acao);
    if (v.material) p.set('material', v.material);
    if (v.comprovado) p.set('comprovado', v.comprovado);
    if (v.canal) p.set('canal', v.canal);
    if (v.page > 1) p.set('p', String(v.page));
    const s = p.toString();
    return s ? `?${s}` : '';
  };

  const opt = (name: string, cur: string, items: [string, string][]) =>
    `<select name="${name}">${items.map(([v, l]) => `<option value="${esc(v)}"${v === cur ? ' selected' : ''}>${esc(l)}</option>`).join('')}</select>`;

  const trs = rows
    .map(
      (r) => `<tr>
  <td>${esc(formatDateTimeUtc(r.occurred_at))}</td>
  <td>${esc(r.email)}</td>
  <td>${esc(actionLabel(r.action))}</td>
  <td>${materialCell(r.material, r.file)}</td>
  <td>${r.verified === 1 ? 'comprovado' : 'só pedido'}</td>
  <td>${esc(formatOrigin(r.origin_channel, r.origin_source))}</td>
  <td>${esc(formatUtm(r.utm_source, r.utm_medium, r.utm_campaign))}</td>
  <td>${landingCell(r.landing_path, env.SITE_ORIGIN)}</td>
  <td>${twentyCell(r.twenty_person_id, r.synced_at, r.verified, r.email_no_crm_ou_comprovado, env.TWENTY_ORIGIN)}</td>
</tr>`,
    )
    .join('');

  const body = `<h1>Contatos</h1>
<form class="f" method="get" action="/admin/contatos">
  <input name="q" value="${esc(f.q)}" placeholder="buscar email" aria-label="buscar email">
  ${opt('acao', f.acao, [
    ['', 'todas as ações'],
    ['material_request', 'pedido de material'],
    ['material_download', 'download'],
    ['newsletter_confirm', 'inscrição nos Expert Insights'],
  ])}
  ${opt('material', f.material, [['', 'todos os materiais'], ...MATERIAIS.map((m): [string, string] => [m.id, m.titulo])])}
  ${opt('comprovado', f.comprovado, [
    ['', 'todos'],
    ['sim', 'comprovado'],
    ['nao', 'só pedido'],
  ])}
  ${opt('canal', f.canal, [['', 'todos os canais'], ...CANAIS.map((c): [string, string] => [c, c]), ['sem', 'sem medição']])}
  <button>Filtrar</button> <a href="/admin/contatos">limpar</a>
</form>
<p>${filtered} encontrados, página ${f.page} de ${pages}.</p>
<table><thead><tr><th>Data (UTC)</th><th>Email</th><th>Ação</th><th>Material e arquivo</th><th>Comprovado</th><th>Origem</th><th>UTM</th><th>Página de entrada</th><th>Twenty</th></tr></thead><tbody>${trs}</tbody></table>
<p>${f.page > 1 ? `<a href="/admin/contatos${qs({ page: f.page - 1 })}">anterior</a> ` : ''}${f.page < pages ? `<a href="/admin/contatos${qs({ page: f.page + 1 })}">próxima</a>` : ''}</p>`;

  return respond(200, htmlPage({ title: 'Contatos | Painel', atual: 'contatos', body }), 'text/html; charset=utf-8');
}

// ------------------------------------------------------------------ Conteúdos (/admin/conteudos)

type Counts = { d7: number; d30: number; total: number };
const ZERO: Counts = { d7: 0, d30: 0, total: 0 };

type MatReqRow = { material: string | null; d7: number | null; d30: number | null; total: number | null };
type MatDlRow = { material: string | null; file: string | null; d7: number | null; d30: number | null; total: number | null };
type LandingRow = {
  landing_path: string | null;
  post_title: string | null;
  req_30: number | null;
  req_total: number | null;
  dl_30: number | null;
  dl_total: number | null;
  nl_30: number | null;
  nl_total: number | null;
  total: number | null;
};
type CatalogPostRow = {
  path: string;
  title: string;
  published_on: string;
  pedidos: number | null;
  downloads: number | null;
  inscricoes: number | null;
};

function addCounts(a: Counts, b: Counts): Counts {
  return { d7: a.d7 + b.d7, d30: a.d30 + b.d30, total: a.total + b.total };
}

export async function conteudosPage(url: URL, env: PainelEnv, nowMs: number): Promise<Response> {
  const corte7 = new Date(nowMs - 7 * DAY_MS).toISOString();
  const corte30 = new Date(nowMs - 30 * DAY_MS).toISOString();
  const page = Math.max(1, Math.min(10_000, Number.parseInt(url.searchParams.get('p') ?? '1', 10) || 1));
  const offset = (page - 1) * PAINEL_PAGE_SIZE;

  const [reqRows, dlRows, landingRows, collector, postCountRow, postRows] = await Promise.all([
    env.DB.prepare(
      `SELECT material,
              SUM(CASE WHEN ${TS} >= ?1 THEN 1 ELSE 0 END) AS d7,
              SUM(CASE WHEN ${TS} >= ?2 THEN 1 ELSE 0 END) AS d30,
              COUNT(*) AS total
       FROM conversions
       WHERE action = 'material_request'
       GROUP BY material`,
    )
      .bind(corte7, corte30)
      .all<MatReqRow>(),
    env.DB.prepare(
      `SELECT material, file,
              SUM(CASE WHEN ${TS} >= ?1 THEN 1 ELSE 0 END) AS d7,
              SUM(CASE WHEN ${TS} >= ?2 THEN 1 ELSE 0 END) AS d30,
              COUNT(*) AS total
       FROM conversions
       WHERE action = 'material_download'
       GROUP BY material, file`,
    )
      .bind(corte7, corte30)
      .all<MatDlRow>(),
    env.DB.prepare(
      `SELECT c.landing_path, cc.title AS post_title,
              SUM(CASE WHEN c.action = 'material_request' AND ${TS_C} >= ?1 THEN 1 ELSE 0 END) AS req_30,
              SUM(CASE WHEN c.action = 'material_request' THEN 1 ELSE 0 END) AS req_total,
              SUM(CASE WHEN c.action = 'material_download' AND ${TS_C} >= ?1 THEN 1 ELSE 0 END) AS dl_30,
              SUM(CASE WHEN c.action = 'material_download' THEN 1 ELSE 0 END) AS dl_total,
              SUM(CASE WHEN c.action = 'newsletter_confirm' AND ${TS_C} >= ?1 THEN 1 ELSE 0 END) AS nl_30,
              SUM(CASE WHEN c.action = 'newsletter_confirm' THEN 1 ELSE 0 END) AS nl_total,
              COUNT(*) AS total
       FROM conversions c
       LEFT JOIN content_catalog cc ON cc.path = c.landing_path
       GROUP BY c.landing_path, cc.title
       ORDER BY total DESC, c.landing_path IS NULL ASC, c.landing_path ASC`,
    )
      .bind(corte30)
      .all<LandingRow>(),
    readCollector(env.DB),
    env.DB.prepare('SELECT COUNT(*) AS n FROM content_catalog').first<{ n: number }>(),
    env.DB.prepare(
      `SELECT c.path, c.title, c.published_on,
              COALESCE(k.pedidos, 0) AS pedidos,
              COALESCE(k.downloads, 0) AS downloads,
              COALESCE(k.inscricoes, 0) AS inscricoes
       FROM content_catalog c
       LEFT JOIN (
         SELECT landing_path,
                SUM(CASE WHEN action = 'material_request' THEN 1 ELSE 0 END) AS pedidos,
                SUM(CASE WHEN action = 'material_download' THEN 1 ELSE 0 END) AS downloads,
                SUM(CASE WHEN action = 'newsletter_confirm' THEN 1 ELSE 0 END) AS inscricoes
         FROM conversions
         WHERE landing_path IS NOT NULL
         GROUP BY landing_path
       ) k ON k.landing_path = c.path
       ORDER BY c.published_on DESC, c.path ASC
       LIMIT ${PAINEL_PAGE_SIZE} OFFSET ${offset}`,
    ).all<CatalogPostRow>(),
  ]);

  // 1. Materiais e arquivos: pedidos e downloads em colunas separadas, nunca somados.
  const reqByMat = new Map<string, Counts>();
  for (const r of reqRows.results) {
    const key = r.material ?? '';
    reqByMat.set(key, addCounts(reqByMat.get(key) ?? ZERO, { d7: num(r.d7), d30: num(r.d30), total: num(r.total) }));
  }
  const knownMatIds = new Set(MATERIAIS.map((m) => m.id));
  const dlByMat = new Map<string, Map<string, Counts>>();
  const addDl = (matKey: string, fileKey: string, c: Counts) => {
    let m = dlByMat.get(matKey);
    if (!m) {
      m = new Map();
      dlByMat.set(matKey, m);
    }
    m.set(fileKey, addCounts(m.get(fileKey) ?? ZERO, c));
  };
  for (const r of dlRows.results) {
    const c: Counts = { d7: num(r.d7), d30: num(r.d30), total: num(r.total) };
    const matKey = r.material ?? (r.file ? arquivo(r.file)?.material.id : undefined) ?? '';
    const fileKey = r.file ?? '';
    addDl(matKey, fileKey, c);
  }

  const matTrs: string[] = [];
  const siteBase = env.SITE_ORIGIN.replace(/\/+$/, '');

  for (const m of MATERIAIS) {
    const req = reqByMat.get(m.id) ?? ZERO;
    matTrs.push(
      `<tr><td><a href="${esc(siteBase + m.pagina)}">${esc(m.titulo)}</a></td><td>${req.d7}</td><td>${req.d30}</td><td>${req.total}</td><td></td><td></td><td></td></tr>`,
    );
    const knownFiles = new Set(m.arquivos.map((a) => a.id));
    const filesMap = dlByMat.get(m.id);
    for (const a of m.arquivos) {
      const dl = filesMap?.get(a.id) ?? ZERO;
      matTrs.push(
        `<tr class="sub"><td>${esc(a.nome)}</td><td></td><td></td><td></td><td>${dl.d7}</td><td>${dl.d30}</td><td>${dl.total}</td></tr>`,
      );
    }
    if (filesMap) {
      for (const [fId, dl] of [...filesMap.entries()].sort(([a], [b]) => a.localeCompare(b))) {
        if (knownFiles.has(fId)) continue;
        const label = fId ? `${esc(fId)} (fora do catálogo)` : '(sem arquivo)';
        matTrs.push(
          `<tr class="sub"><td>${label}</td><td></td><td></td><td></td><td>${dl.d7}</td><td>${dl.d30}</td><td>${dl.total}</td></tr>`,
        );
      }
    }
  }

  // Materiais gravados no D1 que não existem no catálogo MATERIAIS (inclusive material nulo).
  const unknownMats = new Set<string>();
  for (const k of reqByMat.keys()) if (!knownMatIds.has(k)) unknownMats.add(k);
  for (const k of dlByMat.keys()) if (!knownMatIds.has(k)) unknownMats.add(k);
  for (const matId of [...unknownMats].sort()) {
    const req = reqByMat.get(matId) ?? ZERO;
    const matLabel = matId ? `${esc(matId)} (fora do catálogo)` : '(sem material)';
    matTrs.push(
      `<tr><td>${matLabel}</td><td>${req.d7}</td><td>${req.d30}</td><td>${req.total}</td><td></td><td></td><td></td></tr>`,
    );
    const filesMap = dlByMat.get(matId);
    if (filesMap) {
      for (const [fId, dl] of [...filesMap.entries()].sort(([a], [b]) => a.localeCompare(b))) {
        const known = fId ? arquivo(fId)?.arquivo : undefined;
        const fLabel = known ? esc(known.nome) : fId ? `${esc(fId)} (fora do catálogo)` : '(sem arquivo)';
        matTrs.push(
          `<tr class="sub"><td>${fLabel}</td><td></td><td></td><td></td><td>${dl.d7}</td><td>${dl.d30}</td><td>${dl.total}</td></tr>`,
        );
      }
    }
  }

  // 2. Páginas de entrada
  const materialByPath = new Map(MATERIAIS.map((m) => [m.pagina, m.titulo]));
  const landingTrs = landingRows.results
    .map((r) => {
      const celPagina =
        r.landing_path === null
          ? 'sem página de entrada registrada'
          : landingCell(r.landing_path, env.SITE_ORIGIN, r.post_title ?? materialByPath.get(r.landing_path) ?? r.landing_path);
      return `<tr>
  <td>${celPagina}</td>
  <td>${num(r.req_30)}</td>
  <td>${num(r.req_total)}</td>
  <td>${num(r.dl_30)}</td>
  <td>${num(r.dl_total)}</td>
  <td>${num(r.nl_30)}</td>
  <td>${num(r.nl_total)}</td>
</tr>`;
    })
    .join('');

  // 3. Posts do site (catálogo)
  const totalPosts = num(postCountRow?.n);
  const totalPages = Math.max(1, Math.ceil(totalPosts / PAINEL_PAGE_SIZE));
  const catalogStatus = collector?.last_success_at
    ? `<p>Catálogo de ${esc(formatCatalogDate(collector.last_success_at))}, ${totalPosts} ${totalPosts === 1 ? 'post' : 'posts'}.</p>`
    : '<p>O catálogo ainda não foi coletado.</p>';
  const catalogErr = collector?.last_error
    ? `<p class="erro">A última coleta falhou em ${esc(formatCatalogDate(collector.last_attempt_at))}: ${esc(collector.last_error)}</p>`
    : '';

  const postTrs = postRows.results
    .map(
      (p) => `<tr>
  <td><a href="${esc(siteBase + p.path)}">${esc(p.title)}</a></td>
  <td>${esc(p.published_on)}</td>
  <td>${num(p.pedidos)}</td>
  <td>${num(p.downloads)}</td>
  <td>${num(p.inscricoes)}</td>
</tr>`,
    )
    .join('');

  const postsSection =
    totalPosts > 0
      ? `<p>${totalPosts} ${totalPosts === 1 ? 'post' : 'posts'}, página ${page} de ${totalPages}.</p>
<table><thead><tr><th>Post</th><th>Data</th><th>Pedidos</th><th>Downloads</th><th>Inscrições</th></tr></thead><tbody>${postTrs}</tbody></table>
<p>${page > 1 ? `<a href="/admin/conteudos${page - 1 > 1 ? `?p=${page - 1}` : ''}">anterior</a> ` : ''}${page < totalPages ? `<a href="/admin/conteudos?p=${page + 1}">próxima</a>` : ''}</p>`
      : '';

  const body = `<h1>Conteúdos</h1>
<h2>Materiais</h2>
<table><thead><tr><th>Material e arquivo</th><th>Pedidos (7d)</th><th>Pedidos (30d)</th><th>Pedidos (total)</th><th>Downloads (7d)</th><th>Downloads (30d)</th><th>Downloads (total)</th></tr></thead><tbody>${matTrs.join('')}</tbody></table>
<h2>Páginas de entrada</h2>
${landingTrs ? `<table><thead><tr><th>Página</th><th>Pedidos (30d)</th><th>Pedidos (total)</th><th>Downloads (30d)</th><th>Downloads (total)</th><th>Inscrições (30d)</th><th>Inscrições (total)</th></tr></thead><tbody>${landingTrs}</tbody></table>` : '<p>Nenhuma conversão registrada.</p>'}
<h2>Posts do site</h2>
${catalogStatus}${catalogErr}${postsSection}`;

  return respond(200, htmlPage({ title: 'Conteúdos | Painel', atual: 'conteudos', body }), 'text/html; charset=utf-8');
}

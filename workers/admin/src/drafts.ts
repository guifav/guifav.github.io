// Rascunhos privados do blog (issue #62). Só o Gui vê, pelo mesmo Access do painel.
//   GET  /admin/rascunhos                   lista dos rascunhos, com o estado da aprovação e da publicação
//   GET  /admin/rascunhos/<slug>/           prévia do post, com a barra de aprovação no topo
//   GET  /admin/rascunhos/<slug>/<arquivo>  imagens e vídeo do rascunho (só os do manifesto)
//   POST /admin/rascunhos/<slug>/aprovar    grava a aprovação da versão exata que foi vista, dispara a publicação e avisa o Roger
//   POST /admin/rascunhos/<slug>/disparar   dispara de novo a publicação da versão aprovada (#82)
// Cada envio é uma versão imutável em <slug>/v/<versão>/, e o manifesto <slug>/manifest.json (gravado por último)
// aponta para ela. A versão é o sha256 do HTML junto com o da fonte (Markdown + imagens): a aprovação vale para
// as duas coisas, e um envio interrompido nunca mistura o HTML de uma versão com a fonte de outra.
// A prévia é gerada e enviada ao R2 privado pelo blog-src/scripts/draft_preview.py, na KVM. O rascunho nunca
// entra no repo antes da aprovação. A aprovação dispara o workflow publicar.yml (#82), que baixa a fonte da versão,
// confere o hash contra a aprovação no D1 e abre o PR de publicação; a lista lê o estado direto do GitHub.
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
  // #82: token fino do dono do repositório, só neste repositório, só com Actions (disparo e leitura do estado).
  GITHUB_DISPATCH_TOKEN: string;
}

export interface DraftDeps {
  fetch: typeof fetch;
  now: () => number;
  // #80: prazo da consulta ao site (resposta e corpo). Os testes encurtam; em produção, 4 s.
  siteTimeoutMs?: number;
  // #82: prazo de cada chamada à API do GitHub (resposta e corpo). Os testes encurtam; em produção, 5 s.
  githubTimeoutMs?: number;
  // #82 (r1, Astra e Kimi P2): prazo do aviso por email ao Resend. Os testes encurtam; em produção, 5 s.
  mailTimeoutMs?: number;
}

export interface Manifest {
  slug: string;
  title: string;
  uploaded_at: string;
  source_sha256: string;
  html_sha256: string;
  version: string; // versionId(html_sha256, source_sha256)
  files: Record<string, string>; // caminho relativo -> content-type; inclui index.html (objeto sem protótipo)
  // Rascunhos derivados de um artigo, só leitura e aninhados abaixo dele (parent): 'linkedin' é o texto para a
  // newsletter do LinkedIn (#73); 'derivado' é qualquer outro artefato do mesmo artigo (roteiro de short, vídeo,
  // carrossel...), identificado por um rótulo livre, para um artefato novo não exigir mudança de código (#77).
  kind: 'blog' | 'linkedin' | 'derivado';
  parent: string | null;
  // Texto da etiqueta na lista e da barra da prévia: 'LinkedIn' no linkedin, o rótulo enviado no derivado, null no artigo.
  rotulo: string | null;
}

/** Rótulo de um derivado: de 1 a 40 caracteres (pontos de código), sem caractere de controle (C0, DEL, C1) e sem espaço
 * nas pontas. A mesma expressão está no draft_preview.py (ROTULO_RE), com o conjunto de espaços escrito por extenso para
 * os dois lados concordarem: trim() do JavaScript e strip() do Python não removem os mesmos caracteres. */
export const ROTULO_RE =
  /^(?!.*[\u0000-\u001f\u007f-\u009f])(?![ \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff])(?!.*[ \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]$).{1,40}$/su;
export function rotuloValido(v: unknown): v is string {
  return typeof v === 'string' && ROTULO_RE.test(v);
}

/** Identidade da versão: o HTML e a fonte juntos. O draft_preview.py calcula igual. */
export async function versionId(html: string, source: string): Promise<string> {
  return sha256Hex(`html:${html}\nsource:${source}`);
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
  // Os mesmos hosts de iframe que o check_site aceita no blog (IFRAME_HOSTS) e que o blog.ts gera.
  'frame-src https://open.spotify.com https://creators.spotify.com https://podcasters.spotify.com https://www.youtube.com '
    + 'https://www.youtube-nocookie.com https://player.vimeo.com',
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
    'referrer-policy': 'same-origin', // no-referrer faz o POST do formulário sair com Origin: null (403)
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
  for (const k of ['source_sha256', 'html_sha256', 'version']) {
    if (typeof r[k] !== 'string' || !SHA_RE.test(r[k] as string)) return null;
  }
  const source = r.source_sha256 as string;
  const html = r.html_sha256 as string;
  if (r.version !== (await versionId(html, source))) return null;
  if (!r.files || typeof r.files !== 'object' || Array.isArray(r.files)) return null;
  // Sem protótipo: "constructor", "toString" e afins não existem aqui (r1, Kimi e Astra P2).
  const files: Record<string, string> = Object.create(null);
  for (const [k, v] of Object.entries(r.files as Record<string, unknown>)) {
    if (!FILE_RE.test(k) || typeof v !== 'string' || !v) return null;
    files[k] = v;
  }
  if (files['index.html'] !== 'text/html; charset=utf-8') return null;
  // Manifesto sem kind é artigo (os enviados antes do #73). parent existe só nos derivados e aponta para outro slug.
  // Só a ausência vira blog: kind null é malformado (r1, Kimi e Astra).
  const kind = r.kind === undefined ? 'blog' : r.kind;
  if (kind !== 'blog' && kind !== 'linkedin' && kind !== 'derivado') return null;
  let parent: string | null = null;
  if (kind !== 'blog') {
    if (typeof r.parent !== 'string' || !SLUG_RE.test(r.parent) || r.parent === slug) return null;
    parent = r.parent;
  } else if (r.parent !== undefined) {
    return null;
  }
  // rotulo existe só no derivado (#77); no linkedin a etiqueta é fixa.
  let rotulo: string | null = null;
  if (kind === 'derivado') {
    if (!rotuloValido(r.rotulo)) return null;
    rotulo = r.rotulo;
  } else if (r.rotulo !== undefined) {
    return null;
  } else if (kind === 'linkedin') {
    rotulo = 'LinkedIn';
  }
  return { slug, title: r.title, uploaded_at: r.uploaded_at, source_sha256: source, html_sha256: html, version: r.version as string, files,
    kind, parent, rotulo };
}

const objectKey = (m: Manifest, file: string) => `${m.slug}/v/${m.version}/${file}`;

/** HTML da versão do manifesto, conferido contra o html_sha256 dele; null se faltar ou não bater. */
async function versionHtml(env: DraftEnv, m: Manifest): Promise<ArrayBuffer | null> {
  const obj = await env.DRAFTS.get(objectKey(m, 'index.html'));
  if (!obj) return null;
  const raw = await obj.arrayBuffer();
  return (await sha256Hex(raw)) === m.html_sha256 ? raw : null;
}

type Approval = { id: number; html_sha256: string; source_sha256: string; approved_at: string; approved_by: string; notified_at: string;
  notify_error: string; dispatched_at: string; dispatch_error: string };

async function approvals(db: D1Like, slug: string): Promise<Approval[]> {
  const r = await db
    .prepare(`SELECT id, html_sha256, source_sha256, approved_at, approved_by, notified_at, notify_error, dispatched_at, dispatch_error
      FROM draft_approvals WHERE slug = ? ORDER BY id DESC`)
    .bind(slug)
    .all<Approval>();
  return r.results;
}

/** Estado da versão atual: aprovada só se a aprovação é do mesmo HTML E da mesma fonte (r1: imagem trocada com o
 * mesmo HTML não herda a aprovação). */
function state(list: Approval[], m: Manifest): { approved: Approval | null; stale: boolean; firstApprovedAt: string } {
  const desta = list.filter((a) => a.html_sha256 === m.html_sha256 && a.source_sha256 === m.source_sha256);
  // list vem do id mais novo ao mais velho: a primeira é a aprovação vigente, a última é a mais antiga desta versão (#82).
  const approved = desta[0] ?? null;
  return { approved, stale: !approved && list.length > 0, firstApprovedAt: desta.at(-1)?.approved_at ?? '' };
}

const PAGE_STYLE = `body{font:15px/1.45 system-ui,sans-serif;margin:1.5rem;color:#111}table{border-collapse:collapse;width:100%}
th,td{border-bottom:1px solid #ddd;padding:.35rem .5rem;text-align:left;vertical-align:top}.ok{color:#060}.erro{color:#a00}
tr.linkedin td,tr.derivado td{background:#f6f8fb}tr.publicado td{color:#060}tr.linkedin td:first-child,tr.derivado td:first-child{padding-left:1.75rem}.tag{font-size:12px;border:1px solid #0a66c2;color:#0a66c2;border-radius:4px;padding:0 .3rem;margin-right:.35rem}`;

/** Artigo já publicado no site, usado como pai dos derivados depois que o rascunho dele sai do R2 (#80). */
interface Publicado {
  slug: string;
  title: string;
  url: string;
}

/** Até `limite` bytes do corpo, lidos aos poucos; o resto é cancelado sem ser baixado (r1, Astra e Kimi P2). */
async function inicioDoCorpo(res: Response, limite: number): Promise<string> {
  if (!res.body) return '';
  const reader = res.body.getReader();
  const partes: Uint8Array[] = [];
  let total = 0;
  try {
    while (total < limite) {
      const { done, value } = await reader.read();
      if (done) break;
      partes.push(value);
      total += value.byteLength;
    }
  } finally {
    reader.cancel().catch(() => {});
  }
  const bytes = new Uint8Array(Math.min(total, limite));
  let at = 0;
  for (const p of partes) {
    const n = Math.min(p.byteLength, bytes.byteLength - at);
    bytes.set(p.subarray(0, n), at);
    at += n;
    if (at >= bytes.byteLength) break;
  }
  return new TextDecoder().decode(bytes);
}

const ENTIDADES: Record<string, string> = { '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&#39;': "'", '&#x27;': "'" };

/** O post /blog/<slug>/ no site, se responder 200 dentro do prazo; o título vem do <title> da página, sem o nome do
 * autor no fim. Qualquer outra resposta (404, redirecionamento, erro de rede, tempo esgotado) é "não publicado": o
 * derivado fica sozinho com "artigo não encontrado", como antes, e nada some em silêncio. */
async function publicado(env: DraftEnv, deps: DraftDeps, slug: string): Promise<Publicado | null> {
  if (!SLUG_RE.test(slug)) return null;
  const url = `${env.SITE_ORIGIN}/blog/${slug}/`;
  try {
    const res = await deps.fetch(url, { redirect: 'manual', headers: { accept: 'text/html' }, signal: AbortSignal.timeout(deps.siteTimeoutMs ?? 4000) });
    if (res.status !== 200) {
      res.body?.cancel().catch(() => {});
      return null;
    }
    const head = await inicioDoCorpo(res, 65536);
    const m = head.match(/<title>([^<]{1,300})<\/title>/i);
    const title = m ? m[1].replace(/&(amp|lt|gt|quot|#39|#x27);/g, (e) => ENTIDADES[e]).replace(/\s*\|\s*Guilherme Favaron\s*$/, '').trim() : '';
    return { slug, title: title || slug, url };
  } catch {
    return null;
  }
}

// ------------------------------------------------------------------ Publicação pelo GitHub (#82, fatia 4)
// Contrato com o publicar.yml (fatia 3): workflow_dispatch na main com os inputs slug e versao (64 hex); run-name
// "Publica <slug> (<versão inteira>)"; PR aberto pelo github-actions[bot] a partir do branch determinístico
// publica/<slug>-<versão8>; idempotente (branch ou PR da versão já existente termina sem erro).
const GITHUB_REPO = 'guifav/guifav.github.io';
const GITHUB_API = `https://api.github.com/repos/${GITHUB_REPO}`;
const GITHUB_WEB = `https://github.com/${GITHUB_REPO}`;
export const PUBLISH_WORKFLOW = 'publicar.yml';
// Sem execução visível depois deste tempo, o disparo é tratado como parado e o botão "Disparar de novo" aparece.
const PARADO_MIN = 15;

export const publishBranch = (m: Pick<Manifest, 'slug' | 'version'>) => `publica/${m.slug}-${m.version.slice(0, 8)}`;

function githubHeaders(env: DraftEnv): Record<string, string> {
  return {
    authorization: `Bearer ${env.GITHUB_DISPATCH_TOKEN}`,
    accept: 'application/vnd.github+json',
    'x-github-api-version': '2022-11-28',
    'user-agent': 'guifav-blog-admin', // a API do GitHub recusa pedido sem User-Agent
  };
}

/** Dispara o publicar.yml para a versão aprovada. '' quando o GitHub aceitou; senão, o motivo, para gravar e mostrar. */
async function dispatch(env: DraftEnv, deps: DraftDeps, m: Manifest): Promise<string> {
  if (!env.GITHUB_DISPATCH_TOKEN) return 'disparo sem configuração';
  try {
    const res = await deps.fetch(`${GITHUB_API}/actions/workflows/${PUBLISH_WORKFLOW}/dispatches`, {
      method: 'POST',
      headers: { ...githubHeaders(env), 'content-type': 'application/json' },
      body: JSON.stringify({ ref: 'main', inputs: { slug: m.slug, versao: m.version } }),
      signal: AbortSignal.timeout(deps.githubTimeoutMs ?? 5000),
    });
    if (res.ok) {
      res.body?.cancel().catch(() => {});
      return '';
    }
    let detail = '';
    try {
      const j = JSON.parse(await inicioDoCorpo(res, 4096)) as { message?: unknown };
      if (typeof j?.message === 'string') detail = j.message.replace(/\s+/g, ' ').trim().slice(0, 200);
    } catch {
      // Corpo que não é JSON (ou cortado): fica só o status.
    }
    return `GitHub respondeu ${res.status}${detail ? `: ${detail}` : ''}`;
  } catch (e) {
    return e instanceof Error && e.name === 'TimeoutError' ? 'tempo esgotado' : 'falha de rede';
  }
}

// Teto do corpo de uma leitura do GitHub. Uma execução de Actions pesa uns 12,5 KB no JSON da API, então a página de
// 100 execuções passa de 1,2 MB (medido na API real, r2, Kimi P2); 4 MiB dá folga de três vezes.
const GITHUB_MAX_BODY = 4 << 20;

/** GET na API do GitHub com prazo e corpo limitado (GITHUB_MAX_BODY); null em qualquer falha (rede, prazo, status, JSON). */
async function githubGet(env: DraftEnv, deps: DraftDeps, path: string): Promise<unknown> {
  try {
    const res = await deps.fetch(`${GITHUB_API}${path}`, { headers: githubHeaders(env), signal: AbortSignal.timeout(deps.githubTimeoutMs ?? 5000) });
    if (res.status !== 200) {
      res.body?.cancel().catch(() => {});
      return null;
    }
    return JSON.parse(await inicioDoCorpo(res, GITHUB_MAX_BODY)); // corpo maior que o limite chega cortado e não é JSON válido
  } catch {
    return null;
  }
}

/** O run-name é da execução desta versão? O publicar.yml usa "Publica <slug> (<versão>)", com a versão inteira (64 hex),
 * porque as expressões do Actions não cortam texto. O run-name é lido em palavras (letras, números e "-") e precisa ter
 * uma palavra igual ao slug e outra igual à versão inteira. Palavras inteiras, e não trechos: "mcp" não casa com
 * "mcp-aberto", e um slug terminado em hex de outra versão não passa pelo slug mais curto (r1, Kimi P2). A versão,
 * como palavra inteira, tem fronteira de não hex dos dois lados. */
export function execucaoDa(m: Pick<Manifest, 'slug' | 'version'>, displayTitle: unknown): boolean {
  if (typeof displayTitle !== 'string') return false;
  const palavras = new Set(displayTitle.split(/[^A-Za-z0-9-]+/).filter(Boolean));
  return palavras.has(m.slug) && palavras.has(m.version);
}

/** Início da última tentativa de uma execução: reexecutar mantém o id, mas renova o run_started_at (r2 e r3, Astra P2).
 * Sem data válida, 0 (o id desempata). */
function quando(x: Record<string, unknown>): number {
  const t = Date.parse(typeof x.run_started_at === 'string' ? x.run_started_at : typeof x.created_at === 'string' ? x.created_at : '');
  return Number.isFinite(t) ? t : 0;
}

/** A execução mais recente pela última tentativa, com o id como desempate. */
const maisRecente = (a: Record<string, unknown>, b: Record<string, unknown>) =>
  quando(a) > quando(b) || (quando(a) === quando(b) && (a.id as number) > (b.id as number)) ? a : b;

const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const isId = (v: unknown): v is number => Number.isSafeInteger(v) && (v as number) > 0;

function ha(desde: string, now: number): string {
  const min = Math.max(0, Math.floor((now - Date.parse(desde)) / 60000));
  if (!Number.isFinite(min) || min < 1) return 'há menos de 1 min';
  return min < 60 ? `há ${min} min` : `há ${Math.floor(min / 60)} h`;
}

/** Estado da publicação de uma versão aprovada, já em HTML escapado, e se cabe o botão "Disparar de novo". */
interface Publicacao {
  html: string;
  redisparar: boolean;
}

/** CI do head do PR: as execuções de Actions daquele commit (o publicar.yml dispara o pages.yml no branch). */
async function ciDoPr(env: DraftEnv, deps: DraftDeps, sha: string): Promise<string> {
  const r = await githubGet(env, deps, `/actions/runs?per_page=100&head_sha=${sha}`);
  if (!isObj(r) || !Array.isArray(r.workflow_runs)) return 'CI indisponível';
  // Página incompleta: sem todas as execuções, não dá para afirmar a cor (r1, Astra P2).
  if (typeof r.total_count === 'number' && r.total_count > r.workflow_runs.length) return 'CI indisponível';
  // Vale a execução mais recente de cada workflow: uma falha substituída por um novo disparo do mesmo workflow no
  // mesmo commit não deixa a CI vermelha (r1, Astra P2). Recente pela última tentativa: reexecutar mantém o id da
  // execução, mas renova o run_started_at, e a API devolve o estado dessa tentativa (r2, Astra P2). Sem data, o id
  // desempata.
  // Execução parada em "aprovação necessária" (action_required) não rodou: o PR aberto pelo bot gera uma pelo evento de
  // PR, que o GitHub segura, ao lado da que o publicar.yml dispara. Ela não entra na conta (teste de ponta a ponta, #82).
  const vigente = new Map<string, Record<string, unknown>>();
  for (const x of r.workflow_runs) {
    if (!isObj(x) || !isId(x.id) || x.conclusion === 'action_required') continue;
    const wf = isId(x.workflow_id) ? `id:${x.workflow_id}` : `nome:${String(x.name ?? '')}`;
    const atual = vigente.get(wf);
    vigente.set(wf, atual ? maisRecente(x, atual) : x);
  }
  const runs = [...vigente.values()];
  if (!runs.length) return 'CI pendente';
  if (runs.some((x) => x.status !== 'completed')) return 'CI rodando';
  return runs.every((x) => x.conclusion === 'success' || x.conclusion === 'skipped' || x.conclusion === 'neutral') ? 'CI verde' : 'CI vermelha';
}

/** Estado calculado na hora, do D1 e do GitHub: uma chamada (PR do branch da versão) e, no máximo, uma segunda (CI do
 * PR aberto ou execução do publicar.yml). PR mergeado consulta o site, como a linha "publicado no site" do #80. */
async function publicacao(env: DraftEnv, deps: DraftDeps, m: Manifest, a: Approval, firstApprovedAt: string): Promise<Publicacao> {
  const falhou = { html: `disparo falhou (${esc(a.dispatch_error)})`, redisparar: true };
  if (!env.GITHUB_DISPATCH_TOKEN) return a.dispatch_error ? falhou : { html: 'estado indisponível (painel sem o token do GitHub)', redisparar: false };
  const branch = publishBranch(m);
  // Só o PR do branch da versão contra a main: o mesmo branch retargetado para outra base não publica (r4, Kimi P3).
  const pulls = await githubGet(env, deps, `/pulls?state=all&per_page=5&base=main&head=${encodeURIComponent(`guifav:${branch}`)}`);
  // GitHub fora do ar: o motivo de um disparo falho continua visível (e com o botão); sem ele, "estado indisponível".
  if (!Array.isArray(pulls)) return a.dispatch_error ? falhou : { html: 'estado indisponível', redisparar: false };
  const pr = pulls.find((p): p is Record<string, unknown> => isObj(p) && isId(p.number) && isObj(p.head) && p.head.ref === branch
    && isObj(p.base) && p.base.ref === 'main');
  if (pr) {
    const n = pr.number as number;
    const link = `<a href="${GITHUB_WEB}/pull/${n}">#${n}</a>`;
    if (typeof pr.merged_at === 'string' && pr.merged_at) {
      const post = await publicado(env, deps, m.slug);
      if (post) return { html: `publicado em ${esc(pr.merged_at.slice(0, 10))}: <a href="${esc(post.url)}">ver o post</a>`, redisparar: false };
      return { html: `PR ${link} mergeado, aguardando o deploy do site`, redisparar: false };
    }
    if (pr.state !== 'open') return { html: `PR ${link} fechado sem merge`, redisparar: false };
    const sha = isObj(pr.head) && typeof pr.head.sha === 'string' && /^[0-9a-f]{40}$/.test(pr.head.sha) ? pr.head.sha : '';
    return { html: `PR aberto ${link}, ${sha ? await ciDoPr(env, deps, sha) : 'CI indisponível'}`, redisparar: false };
  }
  // Execuções do publicar.yml desde o que vier primeiro entre o envio desta versão ao R2 (uploaded_at do manifesto) e a
  // primeira aprovação dela, 5 min antes contra relógio adiantado, e sem limite depois. Assim nada da versão fica de
  // fora: nem um disparo manual posterior (r2, Astra P2), nem uma execução antiga reexecutada depois de um novo
  // disparo (r4), de uma segunda aprovação (r5) ou de um reenvio do mesmo conteúdo, que renova o uploaded_at sem mudar
  // a versão (r6, Astra P2), nem um disparo manual de antes da aprovação reexecutado depois (r5). Limite conhecido,
  // registrado no README: um disparo manual de antes da aprovação, seguido de um reenvio do mesmo conteúdo e de uma
  // reexecução, fica fora da janela. Vale a execução com a tentativa mais recente (r3, Astra P2). Sem todas as
  // execuções da janela numa página, não dá para garantir qual é a vigente: "estado indisponível", ou o motivo
  // gravado (r4, Astra P2).
  const desdeD1 = a.dispatched_at || a.approved_at;
  const t = Math.min(...[m.uploaded_at, firstApprovedAt || a.approved_at].map((d) => Date.parse(d)).filter(Number.isFinite));
  const iso = (ms: number) => `${new Date(ms).toISOString().slice(0, 19)}Z`;
  const created = Number.isFinite(t) ? `&created=${encodeURIComponent(`>=${iso(t - 5 * 60000)}`)}` : '';
  const r = await githubGet(env, deps, `/actions/workflows/${PUBLISH_WORKFLOW}/runs?event=workflow_dispatch&per_page=100${created}`);
  if (!isObj(r) || !Array.isArray(r.workflow_runs)) return a.dispatch_error ? falhou : { html: 'estado indisponível', redisparar: false };
  const run = r.workflow_runs.filter((x): x is Record<string, unknown> => isObj(x) && isId(x.id) && execucaoDa(m, x.display_title))
    .reduce<Record<string, unknown> | undefined>((acc, x) => (acc ? maisRecente(x, acc) : x), undefined);
  if (typeof r.total_count === 'number' && r.total_count > r.workflow_runs.length) {
    // Só a primeira página: não dá para afirmar que a execução não existe, nem que a achada é a vigente.
    return a.dispatch_error ? falhou : { html: 'estado indisponível', redisparar: false };
  }
  if (run && a.dispatch_error) {
    // Execução visível (um disparo manual, por exemplo) e o último disparo pelo painel falhou: as duas coisas aparecem,
    // e o botão continua, porque nada garante qual delas é a mais nova.
    const st = estadoDaExecucao(run, desdeD1, deps.now());
    return { html: `${st.html}; o último disparo pelo painel falhou (${esc(a.dispatch_error)})`, redisparar: true };
  }
  if (a.dispatch_error) return falhou;
  if (!run) {
    // Aprovação de antes desta fatia, ou registro do disparo que falhou depois do clique: nada indica disparo.
    if (!a.dispatched_at) return { html: 'publicação não disparada', redisparar: true };
    const desde = ha(a.dispatched_at, deps.now());
    return Date.parse(a.dispatched_at) > deps.now() - PARADO_MIN * 60000
      ? { html: `preparando o PR (disparado ${desde})`, redisparar: false }
      : { html: `parado: nenhuma execução encontrada (disparado ${desde})`, redisparar: true };
  }
  return estadoDaExecucao(run, desdeD1, deps.now());
}

/** Estado a partir da execução do publicar.yml. O "há N min" vem da última tentativa dela, que pode ser um disparo
 * manual ou uma reexecução posterior ao disparo gravado no D1. */
function estadoDaExecucao(run: Record<string, unknown>, desdeD1: string, now: number): Publicacao {
  const desde = ha(quando(run) ? new Date(quando(run)).toISOString() : desdeD1, now);
  const exec = `<a href="${GITHUB_WEB}/actions/runs/${run.id as number}">ver a execução</a>`;
  if (run.status !== 'completed') return { html: `preparando o PR (disparado ${desde}): ${exec}`, redisparar: false };
  if (run.conclusion === 'failure') return { html: `publicação recusada: ${exec}`, redisparar: false };
  if (run.conclusion === 'success') return { html: `execução terminou sem abrir o PR: ${exec}`, redisparar: true };
  const como = typeof run.conclusion === 'string' && /^[a-z_]{1,30}$/.test(run.conclusion) ? ` (${run.conclusion})` : '';
  return { html: `execução interrompida${como}: ${exec}`, redisparar: true };
}

async function listPage(env: DraftEnv, deps: DraftDeps, msg: Mensagem | undefined, registroFalhou: boolean): Promise<Response> {
  const prefixes: string[] = [];
  let cursor: string | undefined;
  do {
    const page = await env.DRAFTS.list({ delimiter: '/', cursor, limit: 1000 });
    prefixes.push(...page.delimitedPrefixes);
    cursor = page.truncated ? page.cursor : undefined;
  } while (cursor && prefixes.length < MAX_DRAFTS);
  const drafts: Manifest[] = [];
  for (const p of prefixes.slice(0, MAX_DRAFTS)) {
    const slug = p.replace(/\/$/, '');
    const m = await readManifest(env.DRAFTS, slug);
    if (!m) continue;
    if (!(await versionHtml(env, m))) continue;
    drafts.push(m);
  }
  // #73 e #77: cada derivado (LinkedIn e os demais) vai logo abaixo do artigo dele. #80: se o rascunho do artigo já saiu
  // do R2 e o post está no site, os derivados ficam abaixo de uma linha "publicado no site"; sem nenhum dos dois, o
  // derivado aparece sozinho. Uma consulta ao site por artigo pai.
  const blogs = new Set(drafts.filter((m) => m.kind === 'blog').map((m) => m.slug));
  const semRascunho = [...new Set(drafts.filter((m) => m.kind !== 'blog' && m.parent && !blogs.has(m.parent)).map((m) => m.parent as string))];
  const noSite = new Map<string, Publicado>();
  for (const p of await Promise.all(semRascunho.map((s) => publicado(env, deps, s)))) if (p) noSite.set(p.slug, p);
  const children = new Map<string, Manifest[]>();
  for (const m of drafts) {
    if (m.kind !== 'blog' && m.parent && (blogs.has(m.parent) || noSite.has(m.parent))) children.set(m.parent, [...(children.get(m.parent) ?? []), m]);
  }
  const when = (m: Manifest) => `${esc(m.uploaded_at.slice(0, 16).replace('T', ' '))} UTC`;
  const childRow = (m: Manifest, orphan: boolean) => `<tr class="${m.kind}"><td><span class="tag">${esc(m.rotulo ?? '')}</span>`
    + `<a href="/admin/rascunhos/${m.slug}/">${esc(m.title)}</a></td><td>${esc(m.slug)}</td><td>${when(m)}</td>`
    + `<td>só leitura${orphan ? ' (artigo não encontrado)' : ''}</td></tr>`;
  // #82: o estado da publicação de cada artigo aprovado vem do GitHub, em paralelo; os não aprovados não consultam nada.
  const states = new Map<string, ReturnType<typeof state>>();
  for (const m of drafts) if (m.kind === 'blog') states.set(m.slug, state(await approvals(env.DB, m.slug), m));
  const pubs = new Map<string, Publicacao>();
  await Promise.all(drafts.map(async (m) => {
    const a = states.get(m.slug)?.approved;
    if (m.kind === 'blog' && a) pubs.set(m.slug, await publicacao(env, deps, m, a, states.get(m.slug)!.firstApprovedAt));
  }));
  const rows: string[] = [];
  for (const m of drafts) {
    if (m.kind !== 'blog') {
      if (!m.parent || !(blogs.has(m.parent) || noSite.has(m.parent))) rows.push(childRow(m, true));
      continue;
    }
    const st = states.get(m.slug)!;
    const pub = pubs.get(m.slug);
    const label = st.approved && pub
      ? `aprovado em ${esc(st.approved.approved_at.slice(0, 16).replace('T', ' '))} UTC<br>${pub.html}${pub.redisparar
        ? `<form method="post" action="/admin/rascunhos/${m.slug}/disparar" style="margin:.25rem 0 0">`
          + `<input type="hidden" name="versao" value="${m.version}"><button>Disparar de novo</button></form>`
        : ''}`
      : st.stale
        ? 'aguardando aprovação (a versão mudou depois da última aprovação)'
        : 'aguardando aprovação';
    rows.push(`<tr><td><a href="/admin/rascunhos/${m.slug}/">${esc(m.title)}</a></td><td>${esc(m.slug)}</td>
<td>${when(m)}</td><td>${label}</td></tr>`);
    for (const c of children.get(m.slug) ?? []) rows.push(childRow(c, false));
  }
  for (const p of noSite.values()) {
    rows.push(`<tr class="publicado"><td><a href="${esc(p.url)}">${esc(p.title)}</a></td><td>${esc(p.slug)}</td><td></td><td>publicado no site</td></tr>`);
    for (const c of children.get(p.slug) ?? []) rows.push(childRow(c, false));
  }
  const body = `<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow"><title>Rascunhos | Insights</title><style>${PAGE_STYLE}</style></head><body>
<p><a href="/admin">Assinantes</a> · Rascunhos</p><h1>Rascunhos do blog</h1>
${msg ? `<p class="${msg.erro ? 'erro' : 'ok'}">${esc(msg.texto)}</p>` : ''}
${registroFalhou ? `<p class="erro">${esc(REGISTRO_FALHOU)}</p>` : ''}
${rows.length ? `<table><thead><tr><th>Título</th><th>Slug</th><th>Prévia enviada</th><th>Estado</th></tr></thead><tbody>${rows.join('')}</tbody></table>`
    : '<p>Nenhum rascunho no momento.</p>'}
<p>Só você vê estas páginas. Os rascunhos não estão publicados: a aprovação dispara no GitHub a publicação da versão aprovada, que confere o conteúdo e abre o PR, e o estado dela aparece nesta lista. A linha "publicado no site" aparece só para manter os derivados de um artigo já publicado agrupados abaixo dele.</p>
</body></html>`;
  return new Response(body, { status: 200, headers: headers('text/html; charset=utf-8', ADMIN_CSP) });
}

function approvalBar(m: Manifest, st: { approved: Approval | null; stale: boolean }): string {
  const box = 'position:sticky;top:0;z-index:2147483647;background:#111;color:#fff;font:14px/1.4 system-ui,sans-serif;padding:.6rem 1rem;'
    + 'display:flex;flex-wrap:wrap;gap:.75rem;align-items:center';
  const status = st.approved
    ? `Aprovado em ${esc(st.approved.approved_at.slice(0, 16).replace('T', ' '))} UTC. O estado da publicação está na lista de rascunhos.`
    : `${st.stale ? 'A versão mudou depois da última aprovação. ' : ''}Aguardando a sua aprovação.`;
  const form = st.approved ? '' : `<form method="post" action="/admin/rascunhos/${m.slug}/aprovar" style="margin:0;display:flex;gap:.5rem;align-items:center">
<input type="hidden" name="versao" value="${m.version}"><label style="color:#fff"><input type="checkbox" name="confirmar" value="sim" required> confirmo</label>
<button style="font:inherit;padding:.25rem .75rem">Aprovar publicação</button></form>`;
  return `<div id="rascunho-barra" style="${box}"><strong>Rascunho privado</strong><span>${status}</span>${form}
<a href="/admin/rascunhos" style="color:#fff">todos os rascunhos</a></div>`;
}

function readOnlyBar(m: Manifest, pai: { href: string; label: string }): string {
  const box = 'position:sticky;top:0;z-index:2147483647;background:#0a66c2;color:#fff;font:14px/1.4 system-ui,sans-serif;padding:.6rem 1rem;'
    + 'display:flex;flex-wrap:wrap;gap:.75rem;align-items:center';
  const nome = m.kind === 'linkedin' ? 'Texto para o LinkedIn' : esc(m.rotulo ?? '');
  return `<div id="rascunho-barra" style="${box}"><strong>${nome}</strong><span>só leitura, sem aprovação</span>
<a href="${esc(pai.href)}" style="color:#fff">${pai.label}</a><a href="/admin/rascunhos" style="color:#fff">todos os rascunhos</a></div>`;
}

async function preview(env: DraftEnv, deps: DraftDeps, slug: string): Promise<Response> {
  const m = await readManifest(env.DRAFTS, slug);
  const raw = m && (await versionHtml(env, m));
  if (!m || !raw) return text(404, 'rascunho não encontrado');
  const st = m.kind !== 'blog' ? { approved: null, stale: false } : state(await approvals(env.DB, slug), m);
  const html = new TextDecoder().decode(raw);
  const at = html.search(/<body\b[^>]*>/i);
  if (at < 0) return text(500, 'prévia sem <body>');
  const end = html.indexOf('>', at) + 1;
  // #80: com o rascunho do artigo já fora do R2, o link do derivado leva ao post no site.
  let pai = { href: `/admin/rascunhos/${m.parent}/`, label: 'artigo do blog' };
  if (m.kind !== 'blog' && m.parent) {
    const rascunho = await readManifest(env.DRAFTS, m.parent);
    const p = rascunho && (await versionHtml(env, rascunho)) ? null : await publicado(env, deps, m.parent);
    if (p) pai = { href: p.url, label: 'artigo no site' };
  }
  const out = html.slice(0, end) + (m.kind !== 'blog' ? readOnlyBar(m, pai) : approvalBar(m, st)) + html.slice(end);
  return new Response(out, { status: 200, headers: headers('text/html; charset=utf-8', PREVIEW_CSP) });
}

async function asset(env: DraftEnv, slug: string, file: string): Promise<Response> {
  if (!FILE_RE.test(file) || file === 'index.html' || file === 'manifest.json') return text(404, 'não encontrado');
  const m = await readManifest(env.DRAFTS, slug);
  if (!m || !Object.hasOwn(m.files, file)) return text(404, 'não encontrado');
  const type = m.files[file];
  const obj = await env.DRAFTS.get(objectKey(m, file));
  if (!obj) return text(404, 'não encontrado');
  return new Response(await obj.arrayBuffer(), { status: 200, headers: headers(type, PREVIEW_CSP) });
}

async function notify(env: DraftEnv, deps: DraftDeps, m: Manifest, approval: { at: string; by: string }, dispatchError: string): Promise<string> {
  if (!env.DRAFT_NOTIFY_TO || !env.DRAFT_NOTIFY_FROM || !env.RESEND_API_KEY) return 'aviso sem configuração';
  // #82: o email deixa de ser o gatilho da publicação e vira aviso do que aconteceu com o disparo.
  const disparo = dispatchError
    ? `O disparo da publicação no GitHub falhou (${dispatchError}). O Gui pode disparar de novo pelo painel, no botão "Disparar de novo" da lista.\n`
    : `A publicação foi disparada no GitHub (${PUBLISH_WORKFLOW}). A Action confere a fonte contra esta aprovação e abre o PR a partir do branch ${publishBranch(m)}.\n`;
  try {
    const res = await deps.fetch('https://api.resend.com/emails', {
      method: 'POST',
      signal: AbortSignal.timeout(deps.mailTimeoutMs ?? 5000),
      headers: { authorization: `Bearer ${env.RESEND_API_KEY}`, 'content-type': 'application/json' },
      body: JSON.stringify({
        from: env.DRAFT_NOTIFY_FROM,
        to: [env.DRAFT_NOTIFY_TO],
        subject: `${dispatchError ? 'Rascunho aprovado, disparo falhou' : 'Rascunho aprovado, publicação disparada'}: ${m.title}`,
        text: `O Gui aprovou a publicação do rascunho "${m.title}" (${m.slug}).\n\n${disparo}\nAprovado em: ${approval.at}\nPor: ${approval.by}\n`
          + `Versão aprovada: ${m.version}\nHTML aprovado (sha256): ${m.html_sha256}\nFonte aprovada (sha256): ${m.source_sha256}\n\nPrévia: ${env.SITE_ORIGIN}/admin/rascunhos/${m.slug}/\n`,
      }),
    });
    return res.ok ? '' : `Resend respondeu ${res.status}`;
  } catch (e) {
    return e instanceof Error && e.name === 'TimeoutError' ? 'tempo esgotado' : 'falha de rede';
  }
}

/** Corpo de um formulário do próprio painel: Origin igual ao do site (o Access já exige login; isto fecha CSRF),
 * urlencoded e pequeno. Uma Response quando o pedido é recusado. */
async function formOf(req: Request, env: DraftEnv): Promise<URLSearchParams | Response> {
  if (req.headers.get('origin') !== env.SITE_ORIGIN) return text(403, 'origem não permitida');
  if (!(req.headers.get('content-type') ?? '').startsWith('application/x-www-form-urlencoded')) return text(415, 'formato não suportado');
  const raw = await req.text();
  if (raw.length > 2048) return text(413, 'formulário grande demais');
  return new URLSearchParams(raw);
}

const seeOther = (msg: string, registroFalhou = false) => new Response(null, { status: 303, headers: {
  location: `/admin/rascunhos?msg=${msg}${registroFalhou ? '&registro=falhou' : ''}`, ...headers('text/plain; charset=utf-8', ADMIN_CSP) } });

/** Grava o resultado do disparo na linha da aprovação; false se o D1 falhou (o painel avisa, em vez de calar). */
async function gravaDisparo(env: DraftEnv, deps: DraftDeps, id: number, dispatchError: string): Promise<boolean> {
  try {
    await env.DB.prepare('UPDATE draft_approvals SET dispatched_at = ?, dispatch_error = ? WHERE id = ?')
      .bind(dispatchError ? '' : new Date(deps.now()).toISOString(), dispatchError, id).run();
    return true;
  } catch {
    return false;
  }
}

async function approve(req: Request, env: DraftEnv, deps: DraftDeps, slug: string, email: string): Promise<Response> {
  const form = await formOf(req, env);
  if (form instanceof Response) return form;
  if (form.get('confirmar') !== 'sim') return text(400, 'marque "confirmo" para aprovar');
  const seen = form.get('versao') ?? '';
  const m = await readManifest(env.DRAFTS, slug);
  if (!m || !(await versionHtml(env, m))) return text(404, 'rascunho não encontrado');
  if (m.kind === 'linkedin') return text(400, 'o texto do LinkedIn é só leitura e não tem aprovação. Nada foi gravado.');
  if (m.kind === 'derivado') return text(400, `${m.rotulo} é só leitura e não tem aprovação. Nada foi gravado.`);
  // Só vale para a versão que foi vista (HTML e fonte): se a prévia mudou depois que a página abriu, nada é gravado.
  if (!SHA_RE.test(seen) || seen !== m.version) {
    return text(409, 'a prévia mudou depois que você abriu a página; recarregue e aprove de novo. Nada foi gravado.');
  }
  const at = new Date(deps.now()).toISOString();
  let id: number;
  try {
    const row = await env.DB.prepare(`INSERT INTO draft_approvals (slug, title, html_sha256, source_sha256, approved_at, approved_by)
      VALUES (?, ?, ?, ?, ?, ?) RETURNING id`).bind(slug, m.title, m.html_sha256, m.source_sha256, at, email).first<{ id: number }>();
    if (!row) throw new Error('sem id');
    id = row.id;
  } catch {
    return text(502, 'Não foi possível gravar a aprovação. Nada foi aprovado; tente de novo.');
  }
  // Aprovação já gravada. O disparo (#82) e o aviso por email são melhor esforço, com o resultado na própria linha:
  // o motivo de um disparo falho aparece na lista na hora, com o botão "Disparar de novo".
  // O resultado do disparo é gravado antes do email: um aviso lento ou falho não apaga o registro (r1, Astra e Kimi P2).
  const dispatchError = await dispatch(env, deps, m);
  const gravou = await gravaDisparo(env, deps, id, dispatchError);
  const err = await notify(env, deps, m, { at, by: email }, dispatchError);
  try {
    await env.DB.prepare('UPDATE draft_approvals SET notified_at = ?, notify_error = ? WHERE id = ?')
      .bind(err ? '' : new Date(deps.now()).toISOString(), err, id).run();
  } catch {
    // A aprovação continua valendo; só o registro do aviso falhou.
  }
  return seeOther(dispatchError ? (err ? 'aprovado-sem-disparo-nem-aviso' : 'aprovado-sem-disparo') : (err ? 'aprovado-sem-aviso' : 'aprovado'), !gravou);
}

/** "Disparar de novo" (#82): só para a versão atual, e só se ela está aprovada. O publicar.yml é idempotente, então
 * repetir o disparo de uma versão que já tem PR não cria outro. Sem email: o aviso ao Roger saiu na aprovação. */
async function redispatch(req: Request, env: DraftEnv, deps: DraftDeps, slug: string): Promise<Response> {
  const form = await formOf(req, env);
  if (form instanceof Response) return form;
  const seen = form.get('versao') ?? '';
  const m = await readManifest(env.DRAFTS, slug);
  if (!m || !(await versionHtml(env, m))) return text(404, 'rascunho não encontrado');
  if (m.kind !== 'blog') return text(400, `${m.rotulo ?? 'Este rascunho'} é só leitura e não é publicado. Nada foi disparado.`);
  if (!SHA_RE.test(seen) || seen !== m.version) {
    return text(409, 'a prévia mudou depois que você abriu a página; recarregue a lista. Nada foi disparado.');
  }
  const st = state(await approvals(env.DB, slug), m);
  if (!st.approved) return text(409, 'esta versão não está aprovada. Nada foi disparado.');
  const dispatchError = await dispatch(env, deps, m);
  // Registro falho não é calado (r1, Kimi P2): a mensagem avisa que a lista pode estar mostrando o estado anterior.
  const gravou = await gravaDisparo(env, deps, st.approved.id, dispatchError);
  return seeOther(dispatchError ? 'disparo-falhou' : 'disparado', !gravou);
}

interface Mensagem {
  texto: string;
  erro: boolean;
}

const MESSAGES: Record<string, Mensagem> = {
  aprovado: { texto: 'Aprovado. A publicação foi disparada no GitHub e o Roger foi avisado por email.', erro: false },
  'aprovado-sem-aviso': { texto: 'Aprovado e publicação disparada, mas o aviso por email ao Roger falhou. Avise no chat.', erro: true },
  'aprovado-sem-disparo': { texto: 'Aprovado e gravado, mas o disparo da publicação falhou. O motivo está na linha do rascunho; use "Disparar de novo".', erro: true },
  'aprovado-sem-disparo-nem-aviso': { texto: 'Aprovado e gravado, mas o disparo da publicação e o aviso por email ao Roger falharam. O motivo do disparo está na linha do rascunho; use "Disparar de novo".', erro: true },
  disparado: { texto: 'Publicação disparada de novo no GitHub.', erro: false },
  'disparo-falhou': { texto: 'O disparo da publicação falhou de novo. O motivo está na linha do rascunho.', erro: true },
};

const REGISTRO_FALHOU = 'O resultado do disparo não foi gravado no painel, então a linha do rascunho pode mostrar o estado anterior. '
  + 'Confira no GitHub; disparar de novo é seguro.';

/** Rotas de /admin/rascunhos, já autenticadas pelo chamador. null = não é rota de rascunho. */
export async function handleDrafts(req: Request, url: URL, env: DraftEnv, deps: DraftDeps, email: string): Promise<Response | null> {
  const path = url.pathname;
  if (path === '/admin/rascunhos' || path === '/admin/rascunhos/') {
    if (req.method !== 'GET') return text(405, 'método não permitido');
    const k = url.searchParams.get('msg') ?? '';
    return listPage(env, deps, Object.hasOwn(MESSAGES, k) ? MESSAGES[k] : undefined, url.searchParams.get('registro') === 'falhou');
  }
  const m = path.match(/^\/admin\/rascunhos\/([^/]+)(\/(.*))?$/);
  if (!m) return null;
  const slug = m[1];
  if (!SLUG_RE.test(slug)) return text(404, 'não encontrado');
  const rest = m[3];
  if (m[2] === undefined) {
    // Sem barra final, os caminhos relativos do post quebrariam: redireciona para a forma canônica.
    // Mesmos cabeçalhos das outras respostas: sem eles o 308 é cacheável (r1, Kimi e Astra P2).
    return req.method === 'GET'
      ? new Response(null, { status: 308, headers: { location: `/admin/rascunhos/${slug}/`, ...headers('text/plain; charset=utf-8', ADMIN_CSP) } })
      : text(405, 'método não permitido');
  }
  if (rest === '') return req.method === 'GET' ? preview(env, deps, slug) : text(405, 'método não permitido');
  if (rest === 'aprovar') return req.method === 'POST' ? approve(req, env, deps, slug, email) : text(405, 'método não permitido');
  if (rest === 'disparar') return req.method === 'POST' ? redispatch(req, env, deps, slug) : text(405, 'método não permitido');
  return req.method === 'GET' ? asset(env, slug, rest) : text(405, 'método não permitido');
}

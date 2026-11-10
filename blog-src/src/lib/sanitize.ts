// Limpeza do HTML do corpo dos posts, por lista do que é permitido.
//
// O markdown dos posts vem do importador do Substack (#19) e pode trazer HTML cru. Com
// rawHtml: true, o Satteri reinterpreta esse HTML como o navegador faria (um "</article>" solto
// some, tags abertas em um bloco e fechadas em outro viram elementos). Este plugin então:
//
// - REJEITA (o build falha, com o arquivo e as tags) todo elemento fora da lista, iframe fora dos
//   hosts permitidos e img/source com URL ativa. Nada some em silêncio e nada passa: o importador
//   tem de gerar só HTML da lista. O plugin não remove nem desembrulha elementos, porque o Satteri
//   descarta as alterações pendentes dos filhos de um nó substituído (um <script> dentro de um
//   <article> desembrulhado voltava intacto);
// - REMOVE atributos fora da lista por tag (on*, srcdoc, style que não seja do realce, data-*),
//   URLs com esquema ativo e comentários. Isso não perde conteúdo.

export const MARKDOWN_FEATURES = { rawHtml: true } as const;

// Nomes comparados em minúsculas, com hífen preservado (h-ref não é href). Os únicos aliases são
// os do HAST e do realce de código, que entregam className ou class, dataLanguage ou data-language.
const ATTR_ALIASES: Record<string, string> = { classname: 'class', datalanguage: 'data-language' };
const attrName = (key: string): string => {
  const k = key.toLowerCase();
  return ATTR_ALIASES[k] ?? k;
};
const GLOBAL_ATTRS = ['id', 'class', 'title', 'lang', 'dir'];
const TAG_ATTRS: Record<string, string[]> = {
  a: ['href', 'rel', 'target', 'hreflang'],
  img: ['src', 'srcset', 'sizes', 'alt', 'width', 'height', 'loading', 'decoding'],
  source: ['src', 'srcset', 'sizes', 'type', 'media'],
  iframe: ['src', 'width', 'height', 'allow', 'allowfullscreen', 'loading', 'referrerpolicy'],
  td: ['colspan', 'rowspan', 'align'],
  th: ['colspan', 'rowspan', 'align', 'scope'],
  col: ['span'],
  colgroup: ['span'],
  ol: ['start', 'reversed', 'type'],
  li: ['value'],
  time: ['datetime'],
  blockquote: ['cite'],
  q: ['cite'],
  details: ['open'],
  // Saída do realce de código (Shiki com duas paletas).
  video: ['src', 'controls', 'preload', 'width', 'height', 'poster', 'playsinline', 'muted', 'loop'],
  pre: ['style', 'tabindex', 'data-language'],
  code: ['style', 'data-language'],
  span: ['style'],
};
const KEEP = new Set([
  'p', 'br', 'hr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'a', 'strong', 'b', 'em', 'i', 'u', 's', 'del', 'ins',
  'mark', 'sub', 'sup', 'small', 'code', 'pre', 'kbd', 'samp', 'var', 'blockquote', 'q', 'cite', 'abbr', 'ul', 'ol',
  'li', 'dl', 'dt', 'dd', 'figure', 'figcaption', 'img', 'picture', 'source', 'table', 'thead', 'tbody', 'tfoot',
  'tr', 'th', 'td', 'caption', 'colgroup', 'col', 'div', 'span', 'section', 'aside', 'iframe', 'details', 'summary',
  'time', 'wbr', 'video',
]);
const URL_ATTRS = new Set(['href', 'src', 'cite', 'poster']);

export const IFRAME_HOSTS = new Set([
  'open.spotify.com', 'creators.spotify.com', 'podcasters.spotify.com',
  'www.youtube.com', 'www.youtube-nocookie.com', 'player.vimeo.com',
]);

// Estilo só como o Shiki gera: variáveis de cor ("--shiki-light:#24292E") e "overflow-x: auto".
function shikiStyle(style: string): boolean {
  return style
    .split(';')
    .map((d) => d.trim())
    .filter(Boolean)
    .every((d) => /^--shiki-[a-z-]+\s*:\s*#[0-9a-fA-F]{3,8}$/.test(d) || /^overflow-x\s*:\s*auto$/.test(d));
}

/** URL relativa, http(s) ou mailto. Remove espaços e controles antes de olhar o esquema, como o navegador. */
export function safeUrl(url: string): boolean {
  const u = url.replace(/[\u0000-\u0020\u007f]/g, '').toLowerCase();
  const m = u.match(/^([a-z][a-z0-9+.-]*):/);
  if (!m) return true;
  return m[1] === 'http' || m[1] === 'https' || m[1] === 'mailto';
}

/** URLs de um srcset, pela regra do HTML: a URL vai até um espaço; vírgula no fim da URL encerra o candidato. */
export function parseSrcset(value: string): string[] {
  const urls: string[] = [];
  let i = 0;
  const ws = /[\t\n\f\r ]/;
  while (i < value.length) {
    while (i < value.length && (ws.test(value[i]) || value[i] === ',')) i++;
    if (i >= value.length) break;
    let j = i;
    while (j < value.length && !ws.test(value[j])) j++;
    let url = value.slice(i, j);
    i = j;
    if (url.endsWith(',')) {
      url = url.replace(/,+$/, '');
    } else {
      // Descritores até a próxima vírgula fora de parênteses.
      let depth = 0;
      while (i < value.length) {
        const c = value[i];
        if (c === '(') depth++;
        else if (c === ')') depth = Math.max(0, depth - 1);
        else if (c === ',' && depth === 0) break;
        i++;
      }
    }
    if (url) urls.push(url);
  }
  return urls;
}

function iframeAllowed(src: unknown): boolean {
  if (typeof src !== 'string') return false;
  try {
    const u = new URL(src);
    return u.protocol === 'https:' && IFRAME_HOSTS.has(u.hostname);
  } catch {
    return false;
  }
}

export interface Decision {
  action: 'keep' | 'reject';
  /** Motivo da rejeição. */
  reason?: string;
  /** Propriedades a remover (nomes como estão no nó). */
  remove: string[];
  /** Propriedades a definir. */
  set: Record<string, string>;
}

const reject = (reason: string): Decision => ({ action: 'reject', reason, remove: [], set: {} });

export function sanitizeElement(tag: string, props: Record<string, unknown>): Decision {
  const t = tag.toLowerCase();
  if (!KEEP.has(t)) return reject('elemento fora da lista');
  if (t === 'iframe' && !iframeAllowed(props.src)) return reject('iframe fora dos hosts permitidos (https de Spotify, YouTube ou Vimeo)');
  const allowed = new Set([...GLOBAL_ATTRS, ...(TAG_ATTRS[t] ?? [])]);
  const remove: string[] = [];
  for (const [key, value] of Object.entries(props)) {
    const k = attrName(key);
    if (!allowed.has(k)) {
      remove.push(key);
      continue;
    }
    const v = Array.isArray(value) ? value.join(' ') : String(value ?? '');
    const media = t === 'img' || t === 'source' || t === 'video';
    if (URL_ATTRS.has(k) && !safeUrl(v)) {
      if (media && (k === 'src' || k === 'poster')) return reject('URL de imagem ou vídeo com esquema ativo');
      remove.push(key);
    } else if (k === 'srcset' && !parseSrcset(v).every(safeUrl)) {
      // Como em src: imagem responsiva com URL ativa falha o build, não some em silêncio.
      return reject('srcset com URL de esquema ativo');
    } else if (k === 'style' && !shikiStyle(v)) {
      remove.push(key);
    }
  }
  const set: Record<string, string> = {};
  if (t === 'a' && 'target' in props && !remove.includes('target')) set.rel = 'noopener';
  return { action: 'keep', remove, set };
}

interface HastLike {
  tagName: string;
  properties?: Record<string, unknown>;
  children?: unknown[];
}

interface Ctx {
  readonly data: Record<string, unknown>;
  readonly fileURL: URL | undefined;
  removeNode(node: unknown): void;
  setProperty(node: unknown, key: string, value: unknown): void;
}

const REJECTED = '__guifavSanitizeRejected';

function rejected(ctx: Ctx): string[] {
  return ((ctx.data[REJECTED] as string[] | undefined) ??= []);
}

/** Plugin hast do Satteri (markdown.processor no astro.config.mjs). */
export function sanitizePlugin() {
  return {
    name: 'guifav-sanitize',
    before(_root: unknown, ctx: Ctx) {
      ctx.data[REJECTED] = [];
    },
    element: {
      filter: [] as string[],
      visit(node: HastLike, ctx: Ctx) {
        const d = sanitizeElement(node.tagName, node.properties ?? {});
        if (d.action === 'reject') {
          rejected(ctx).push(`<${node.tagName}> (${d.reason})`);
          return;
        }
        for (const k of d.remove) ctx.setProperty(node, k, null);
        for (const [k, v] of Object.entries(d.set)) ctx.setProperty(node, k, v);
      },
    },
    // Com rawHtml: true não sobram nós raw; se aparecer um, o build falha.
    raw(_node: unknown, ctx: Ctx) {
      rejected(ctx).push('HTML cru não interpretado');
    },
    comment(node: unknown, ctx: Ctx) {
      ctx.removeNode(node);
    },
    after(_root: unknown, ctx: Ctx) {
      const bad = [...new Set(rejected(ctx))];
      if (bad.length) {
        const file = ctx.fileURL ? decodeURIComponent(ctx.fileURL.pathname) : 'post';
        throw new Error(`${file}: HTML fora da lista permitida do blog (src/lib/sanitize.ts): ${bad.join('; ')}`);
      }
    },
  };
}

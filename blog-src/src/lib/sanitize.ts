// Limpeza do HTML do corpo dos posts, por lista do que é permitido.
//
// O markdown dos posts vem do importador do Substack (#19) e pode trazer HTML cru. Com
// rawHtml: true, o Satteri reinterpreta esse HTML como o navegador faria (um "</article>" solto
// some, tags abertas em um bloco e fechadas em outro viram elementos), e este plugin percorre os
// elementos: tag fora da lista sai, atributo fora da lista sai, URL com esquema ativo sai e iframe
// só fica se for https de um host permitido. Roda depois do realce de código e antes do resto.

export const MARKDOWN_FEATURES = { rawHtml: true } as const;

/** Saem com todo o conteúdo. */
const DROP = new Set([
  'script', 'style', 'noscript', 'template', 'object', 'embed', 'applet', 'param', 'form', 'input', 'button',
  'textarea', 'select', 'option', 'optgroup', 'datalist', 'fieldset', 'label', 'output', 'base', 'meta', 'link',
  'title', 'head', 'frame', 'frameset', 'svg', 'math', 'canvas', 'dialog', 'portal', 'slot', 'audio', 'video', 'track',
]);

// Nomes comparados em minúsculas e sem hífen: className, class, data-language e dataLanguage
// aparecem conforme o nó veio do markdown ou do realce de código.
const GLOBAL_ATTRS = ['id', 'classname', 'class', 'title', 'lang', 'dir'];
const TAG_ATTRS: Record<string, string[]> = {
  a: ['href', 'rel', 'target', 'hreflang'],
  img: ['src', 'srcset', 'sizes', 'alt', 'width', 'height', 'loading', 'decoding'],
  source: ['srcset', 'sizes', 'type', 'media'],
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
  pre: ['style', 'tabindex', 'datalanguage'],
  code: ['style', 'datalanguage'],
  span: ['style'],
};
const KEEP = new Set([
  'p', 'br', 'hr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'a', 'strong', 'b', 'em', 'i', 'u', 's', 'del', 'ins',
  'mark', 'sub', 'sup', 'small', 'code', 'pre', 'kbd', 'samp', 'var', 'blockquote', 'q', 'cite', 'abbr', 'ul', 'ol',
  'li', 'dl', 'dt', 'dd', 'figure', 'figcaption', 'img', 'picture', 'source', 'table', 'thead', 'tbody', 'tfoot',
  'tr', 'th', 'td', 'caption', 'colgroup', 'col', 'div', 'span', 'section', 'aside', 'iframe', 'details', 'summary',
  'time', 'wbr',
]);
const URL_ATTRS = new Set(['href', 'src', 'cite']);

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
  action: 'keep' | 'drop' | 'unwrap';
  /** Propriedades a remover (nomes como estão no nó). */
  remove: string[];
  /** Propriedades a definir. */
  set: Record<string, string>;
}

export function sanitizeElement(tag: string, props: Record<string, unknown>): Decision {
  const t = tag.toLowerCase();
  if (DROP.has(t)) return { action: 'drop', remove: [], set: {} };
  if (!KEEP.has(t)) return { action: 'unwrap', remove: [], set: {} };
  if (t === 'iframe' && !iframeAllowed(props.src)) return { action: 'drop', remove: [], set: {} };
  const allowed = new Set([...GLOBAL_ATTRS, ...(TAG_ATTRS[t] ?? [])]);
  const remove: string[] = [];
  for (const [key, value] of Object.entries(props)) {
    const k = key.toLowerCase().replace(/-/g, '');
    if (!allowed.has(k)) {
      remove.push(key);
      continue;
    }
    const v = Array.isArray(value) ? value.join(' ') : String(value ?? '');
    if (URL_ATTRS.has(k) && !safeUrl(v)) {
      if (t === 'img' && k === 'src') return { action: 'drop', remove: [], set: {} };
      remove.push(key);
    } else if (k === 'srcset' && !parseSrcset(v).every(safeUrl)) {
      remove.push(key);
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
  removeNode(node: unknown): void;
  replaceNode(node: unknown, newNode: unknown): void;
  setProperty(node: unknown, key: string, value: unknown): void;
}

/** Plugin hast do Satteri (markdown.processor no astro.config.mjs). */
export function sanitizePlugin() {
  return {
    name: 'guifav-sanitize',
    element: {
      filter: [] as string[],
      visit(node: HastLike, ctx: Ctx) {
        const d = sanitizeElement(node.tagName, node.properties ?? {});
        if (d.action === 'drop') return ctx.removeNode(node);
        if (d.action === 'unwrap') return ctx.replaceNode(node, (node.children ?? []) as unknown[]);
        for (const k of d.remove) ctx.setProperty(node, k, null);
        for (const [k, v] of Object.entries(d.set)) ctx.setProperty(node, k, v);
      },
    },
    // Com rawHtml: true não sobram nós raw nem comentários úteis; os dois saem.
    raw(node: unknown, ctx: Ctx) {
      ctx.removeNode(node);
    },
    comment(node: unknown, ctx: Ctx) {
      ctx.removeNode(node);
    },
  };
}

// Funções puras usadas pelas páginas. Testadas com node --test (tests/).

/** Valor da meta robots: noindex antes do cutover ou quando o post pede index: false. */
export function robotsContent(index: boolean, preCutover: boolean): string {
  return preCutover || !index ? 'noindex, follow' : 'index, follow';
}

/** Junta o base do Astro ('/blog' ou '/blog/') a um caminho, sempre com uma barra só. */
export function withBase(base: string, path: string): string {
  const b = base.endsWith('/') ? base : `${base}/`;
  const p = path.replace(/^\/+/, '');
  return `${b}${p}`;
}

/** Caminho público de um post: /blog/<slug>/ */
export function postPath(base: string, slug: string): string {
  return withBase(base, `${slug}/`);
}

/** Caminho da página N da lista (1 é a raiz do blog). */
export function pagePath(base: string, page: number): string {
  if (!Number.isInteger(page) || page < 1) throw new Error(`página inválida: ${page}`);
  return page === 1 ? withBase(base, '') : withBase(base, `pagina/${page}/`);
}

/** Quantas páginas a lista tem. */
export function pageCount(total: number, perPage: number): number {
  return Math.max(1, Math.ceil(total / perPage));
}

/** URL absoluta a partir de um caminho que já inclui o base. */
export function absoluteUrl(site: string, path: string): string {
  return new URL(path, site).href;
}

const DATE_FMT = new Intl.DateTimeFormat('pt-BR', {
  day: 'numeric',
  month: 'long',
  year: 'numeric',
  timeZone: 'UTC',
});

/** Data em português, no fuso UTC, para não trocar o dia conforme o fuso do build. */
export function formatDate(d: Date): string {
  return DATE_FMT.format(d);
}

/**
 * URL do player embutido do Spotify a partir do link do episódio.
 * Aceita open.spotify.com/episode/<id> (também localizado, /intl-pt/episode/<id>) e o link do Spotify for Creators
 * (podcasters.spotify.com ou creators.spotify.com /pod/show|profile/<show>/episodes/<ep>).
 * Retorna null se o link não for de episódio do Spotify.
 */
export function spotifyEmbedUrl(url: string): string | null {
  let u: URL;
  try {
    u = new URL(url);
  } catch {
    return null;
  }
  if (u.protocol !== 'https:') return null;
  if (u.hostname === 'open.spotify.com') {
    // O Spotify também gera links localizados: /intl-pt/episode/<id>.
    const m = u.pathname.match(/^\/(?:intl-[a-z]{2,3}(?:-[a-z0-9]{2,4})?\/)?(?:embed\/)?episode\/([A-Za-z0-9]+)\/?$/);
    return m ? `https://open.spotify.com/embed/episode/${m[1]}` : null;
  }
  if (u.hostname === 'podcasters.spotify.com' || u.hostname === 'creators.spotify.com') {
    const m = u.pathname.match(/^\/pod\/(?:show|profile)\/([A-Za-z0-9_-]+)\/(?:embed\/)?episodes\/([A-Za-z0-9_-]+)\/?$/);
    return m ? `https://creators.spotify.com/pod/profile/${m[1]}/embed/episodes/${m[2]}` : null;
  }
  return null;
}

export interface PostLdInput {
  title: string;
  description: string;
  date: Date;
  updated?: Date;
  url: string;
  image?: string;
  /** A Person da home (mesmo @id), com nome e URL para o Google resolver o autor na própria página. */
  author: { id: string; name: string; url: string };
  /** @id do WebSite da home (index.html). */
  websiteId: string;
  /** Níveis acima do post na trilha (home e blog); o post entra por último, com o título. */
  crumbs: { name: string; url: string }[];
}

/**
 * JSON-LD do post (#105): um @graph com o BlogPosting, a BreadcrumbList e um nó Person mínimo.
 * A Person completa (sameAs, worksFor) continua só na home; aqui vão @id, nome e URL, para que o avaliador
 * do Google, que lê uma página por vez, resolva author.name e author.url sem seguir o @id até a home.
 * O publisher é a própria Person: o blog é pessoal, não de uma organização.
 */
export function postLdGraph(p: PostLdInput): Record<string, unknown> {
  const posting: Record<string, unknown> = {
    '@type': 'BlogPosting',
    headline: p.title,
    description: p.description,
    datePublished: p.date.toISOString(),
    dateModified: (p.updated ?? p.date).toISOString(),
    author: { '@id': p.author.id },
    publisher: { '@id': p.author.id },
    isPartOf: { '@id': p.websiteId },
    mainEntityOfPage: p.url,
    url: p.url,
    inLanguage: 'pt-BR',
  };
  if (p.image) posting.image = p.image;
  const trail = [...p.crumbs, { name: p.title, url: p.url }];
  const breadcrumb = {
    '@type': 'BreadcrumbList',
    itemListElement: trail.map((c, i) => ({ '@type': 'ListItem', position: i + 1, name: c.name, item: c.url })),
  };
  const person = { '@type': 'Person', '@id': p.author.id, name: p.author.name, url: p.author.url };
  return { '@context': 'https://schema.org', '@graph': [posting, breadcrumb, person] };
}

/** Serializa JSON-LD para dentro de <script>, sem permitir fechar a tag. */
export function jsonLdScript(data: unknown): string {
  return JSON.stringify(data).replace(/</g, '\\u003c');
}

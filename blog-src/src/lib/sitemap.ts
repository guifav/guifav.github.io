// Entradas do sitemap do blog (/blog/sitemap.xml), separadas da rota para serem testadas.
//
// Entram a capa e os posts indexáveis. A paginação (/blog/pagina/N/) fica de fora: continua publicada e
// rastreável pelos links, mas não é destino de busca.
// lastmod:
// - post: o mais recente entre o frontmatter (updated ?? date) e o último commit do .md depois de
//   INDEXABLE_SINCE (lib/lastmod.ts);
// - capa: sem lastmod. Ela mostra só a primeira página e muda com o template, a ordem e as remoções;
//   nenhuma data de post representa isso. O IndexNow compara o HTML publicado com o do build (scripts/indexnow.py).
import { absoluteUrl, pagePath, postPath } from './blog.ts';
import { postLastmod } from './lastmod.ts';

export interface SitemapPost {
  id: string;
  data: { date: Date; updated?: Date; index: boolean };
}

export interface SitemapEntry {
  loc: string;
  lastmod?: Date;
}

/** `git` mapeia o nome do arquivo (<slug>.md, regra do lib/postfiles) para a data ISO do último commit. */
export function sitemapEntries(site: string, base: string, posts: SitemapPost[], git: Map<string, string>): SitemapEntry[] {
  const out: SitemapEntry[] = [{ loc: absoluteUrl(site, pagePath(base, 1)) }];
  for (const p of posts) {
    if (!p.data.index) continue;
    out.push({ loc: absoluteUrl(site, postPath(base, p.id)), lastmod: postLastmod(p.data.updated ?? p.data.date, git.get(`${p.id}.md`)) });
  }
  return out;
}

function esc(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

export function sitemapXml(entries: SitemapEntry[]): string {
  const body = entries
    .map((u) => `  <url><loc>${esc(u.loc)}</loc>${u.lastmod ? `<lastmod>${u.lastmod.toISOString()}</lastmod>` : ''}</url>`)
    .join('\n');
  return `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${body}\n</urlset>\n`;
}

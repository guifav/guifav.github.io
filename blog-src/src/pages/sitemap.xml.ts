// Sitemap do blog: /blog/sitemap.xml, citado pelo índice /sitemap.xml (scripts/build_sitemaps.py).
// Entram a capa do blog e os posts indexáveis. Ficam de fora quem tem index: false e as páginas de
// paginação (/blog/pagina/N/): continuam publicadas e rastreáveis pelos links, mas não são destino de busca.
// lastmod: o mais recente entre o frontmatter e o último commit do post depois de INDEXABLE_SINCE (lib/lastmod).
import type { APIContext } from 'astro';
import { INDEXABLE_SINCE } from '../consts';
import { absoluteUrl, pagePath, postPath } from '../lib/blog';
import { gitPostDates, postLastmod } from '../lib/lastmod';
import { sortedPosts } from '../lib/posts';

function esc(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

export async function GET(context: APIContext) {
  const base = import.meta.env.BASE_URL;
  const site = context.site!.href;
  const all = await sortedPosts();
  const git = gitPostDates('src/content/posts', INDEXABLE_SINCE);
  // O arquivo de cada post é <slug>.md direto em src/content/posts (regra do lib/postfiles).
  const lastmod = new Map(all.map((p) => [p.id, postLastmod(p.data.updated ?? p.data.date, git.get(`${p.id}.md`))]));
  // A capa lista todos os posts, inclusive os com index: false.
  const newest = all.reduce<Date | null>((m, p) => {
    const d = lastmod.get(p.id)!;
    return !m || d > m ? d : m;
  }, null);
  const urls: { loc: string; lastmod?: Date }[] = [{ loc: absoluteUrl(site, pagePath(base, 1)), lastmod: newest ?? undefined }];
  for (const p of all.filter((p) => p.data.index)) urls.push({ loc: absoluteUrl(site, postPath(base, p.id)), lastmod: lastmod.get(p.id) });
  const body = urls
    .map((u) => `  <url><loc>${esc(u.loc)}</loc>${u.lastmod ? `<lastmod>${u.lastmod.toISOString()}</lastmod>` : ''}</url>`)
    .join('\n');
  const xml = `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${body}\n</urlset>\n`;
  return new Response(xml, { headers: { 'Content-Type': 'application/xml; charset=utf-8' } });
}

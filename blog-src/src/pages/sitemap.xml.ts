// Sitemap do blog: /blog/sitemap.xml. Fica de fora quem tem index: false.
// Até o cutover nada aponta para este arquivo (o robots.txt da raiz só passa a citá-lo na #24).
import type { APIContext } from 'astro';
import { POSTS_PER_PAGE } from '../consts';
import { absoluteUrl, pageCount, pagePath, postPath } from '../lib/blog';
import { sortedPosts } from '../lib/posts';

function esc(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

export async function GET(context: APIContext) {
  const base = import.meta.env.BASE_URL;
  const site = context.site!.href;
  const all = await sortedPosts();
  const indexed = all.filter((p) => p.data.index);
  const newest = all.reduce<Date | null>((m, p) => {
    const d = p.data.updated ?? p.data.date;
    return !m || d > m ? d : m;
  }, null);
  const urls: { loc: string; lastmod?: Date }[] = [];
  const pages = pageCount(all.length, POSTS_PER_PAGE);
  for (let i = 1; i <= pages; i++) urls.push({ loc: absoluteUrl(site, pagePath(base, i)), lastmod: i === 1 ? newest ?? undefined : undefined });
  for (const p of indexed) urls.push({ loc: absoluteUrl(site, postPath(base, p.id)), lastmod: p.data.updated ?? p.data.date });
  const body = urls
    .map((u) => `  <url><loc>${esc(u.loc)}</loc>${u.lastmod ? `<lastmod>${u.lastmod.toISOString()}</lastmod>` : ''}</url>`)
    .join('\n');
  const xml = `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${body}\n</urlset>\n`;
  return new Response(xml, { headers: { 'Content-Type': 'application/xml; charset=utf-8' } });
}

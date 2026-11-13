// Sitemap do blog: /blog/sitemap.xml, citado pelo índice /sitemap.xml (scripts/build_sitemaps.py).
// As regras (o que entra e o lastmod) estão em lib/sitemap.ts, com testes.
import type { APIContext } from 'astro';
import { DRAFT_PREVIEW, INDEXABLE_SINCE } from '../consts';
import { POSTS_DIR, gitPostDates } from '../lib/lastmod';
import { sortedPosts } from '../lib/posts';
import { sitemapEntries, sitemapXml } from '../lib/sitemap';

export async function GET(context: APIContext) {
  // Na prévia de rascunho (issue #62) o build roda numa cópia sem git e o sitemap é descartado: sem datas do git.
  const entries = sitemapEntries(context.site!.href, import.meta.env.BASE_URL, await sortedPosts(),
    DRAFT_PREVIEW ? new Map() : gitPostDates(POSTS_DIR, INDEXABLE_SINCE));
  return new Response(sitemapXml(entries), { headers: { 'Content-Type': 'application/xml; charset=utf-8' } });
}

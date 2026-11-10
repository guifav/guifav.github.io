// RSS do blog: /blog/rss.xml, com os posts mais recentes.
import rss from '@astrojs/rss';
import type { APIContext } from 'astro';
import { BLOG_DESCRIPTION, RSS_LIMIT } from '../consts';
import { absoluteUrl, postPath, withBase } from '../lib/blog';
import { sortedPosts } from '../lib/posts';

export async function GET(context: APIContext) {
  const base = import.meta.env.BASE_URL;
  const site = context.site!.href;
  const posts = (await sortedPosts()).slice(0, RSS_LIMIT);
  return rss({
    title: 'Blog de Guilherme Favaron',
    description: BLOG_DESCRIPTION,
    site: absoluteUrl(site, base),
    xmlns: { atom: 'http://www.w3.org/2005/Atom' },
    customData: `<language>pt-br</language><atom:link href="${absoluteUrl(site, withBase(base, 'rss.xml'))}" rel="self" type="application/rss+xml"/>`,
    trailingSlash: true,
    items: posts.map((p) => ({
      title: p.data.title,
      description: p.data.description,
      pubDate: p.data.date,
      link: absoluteUrl(site, postPath(base, p.id)),
    })),
  });
}

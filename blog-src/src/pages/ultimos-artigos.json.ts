// Últimos artigos para a home do site (issue #136): /blog/ultimos-artigos.json, lido pelo scripts/assemble_site.py.
import { HOME_LATEST } from '../consts';
import { homeLatest } from '../lib/home';
import { sortedPosts } from '../lib/posts';

export async function GET() {
  const posts = homeLatest(await sortedPosts(), HOME_LATEST, import.meta.env.BASE_URL);
  return new Response(JSON.stringify({ posts }, null, 2) + '\n', {
    headers: { 'Content-Type': 'application/json; charset=utf-8' },
  });
}

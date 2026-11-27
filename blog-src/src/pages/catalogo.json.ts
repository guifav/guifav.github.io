// Catálogo completo de posts do blog (issue #193): /blog/catalogo.json, coletado pelo cron do Worker `blog-admin`.
import { catalogoPosts } from '../lib/catalogo';
import { sortedPosts } from '../lib/posts';

export async function GET() {
  const body = catalogoPosts(await sortedPosts(), import.meta.env.BASE_URL);
  return new Response(JSON.stringify(body, null, 2) + '\n', {
    headers: { 'Content-Type': 'application/json; charset=utf-8' },
  });
}

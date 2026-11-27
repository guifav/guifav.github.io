// Catálogo completo de posts do blog para o painel /admin/conteudos (issue #193).
// O build do blog publica /blog/catalogo.json a cada merge, e o cron do Worker `blog-admin` copia para o D1.
// Entra todo post publicado, indexável ou não e de qualquer tipo, para resolver títulos de páginas de entrada no painel.
import { postPath } from './blog.ts';

export interface CatalogoPost {
  id: string;
  data: {
    title: string;
    date: Date;
  };
}

export interface CatalogoItem {
  caminho: string;
  titulo: string;
  data: string; // AAAA-MM-DD, em UTC
}

/** Todos os posts publicados, na mesma ordem de sortedPosts (mais recente primeiro; mesma data desempata pelo slug). */
export function catalogoPosts(posts: CatalogoPost[], base: string): { posts: CatalogoItem[] } {
  return {
    posts: [...posts]
      .sort((a, b) => b.data.date.getTime() - a.data.date.getTime() || a.id.localeCompare(b.id))
      .map((p) => ({
        caminho: postPath(base, p.id),
        titulo: p.data.title,
        data: p.data.date.toISOString().slice(0, 10),
      })),
  };
}

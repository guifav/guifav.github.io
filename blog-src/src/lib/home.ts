// Últimos artigos da home do site (issue #136). O build do blog publica a lista em /blog/ultimos-artigos.json e o
// scripts/assemble_site.py a escreve na home (index.html, entre os marcadores <!-- ultimos-artigos -->), porque a home
// é HTML escrito à mão e a lista precisa estar no HTML que o rastreador lê, sem JavaScript.
import { formatDate, postPath } from './blog.ts';
import { isListed, type TopicPost } from './topics.ts';

export interface HomePost extends TopicPost {
  data: TopicPost['data'] & { title: string; description: string };
}

export interface HomeLatestItem {
  title: string;
  path: string;
  date: string; // AAAA-MM-DD, em UTC
  date_label: string; // a data como o blog mostra
  description: string;
}

/** Os n posts listados (indexáveis e fora dos podcasts) mais recentes, na ordem do blog: data e depois slug. */
export function homeLatest(posts: HomePost[], n: number, base: string): HomeLatestItem[] {
  return posts
    .filter(isListed)
    .sort((a, b) => b.data.date.getTime() - a.data.date.getTime() || a.id.localeCompare(b.id))
    .slice(0, n)
    .map((p) => ({
      title: p.data.title,
      path: postPath(base, p.id),
      date: p.data.date.toISOString().slice(0, 10),
      date_label: formatDate(p.data.date),
      description: p.data.description,
    }));
}

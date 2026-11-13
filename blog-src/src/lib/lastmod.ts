// lastmod dos posts no sitemap do blog.
//
// O frontmatter (updated ?? date) só muda quando alguém lembra de mudar. Para o sitemap e o IndexNow
// enxergarem qualquer edição, o lastmod é o mais recente entre o frontmatter e o último commit do arquivo
// depois de INDEXABLE_SINCE. Commits anteriores (importação e limpeza, com o blog ainda em noindex) não
// contam: o Google viu essas páginas pela primeira vez já na versão atual.
import { execFileSync } from 'node:child_process';

const MARK = '@@';

/** Pasta dos posts, relativa a blog-src (o cwd do build do Astro e do npm test). */
export const POSTS_DIR = 'src/content/posts';

/** Saída de `git log --format=@@%cI --name-only` (mais recente primeiro) -> nome do arquivo -> data ISO. */
export function parseGitLog(out: string): Map<string, string> {
  const dates = new Map<string, string>();
  let current: string | null = null;
  for (const raw of out.split('\n')) {
    const line = raw.trim();
    if (!line) continue;
    if (line.startsWith(MARK)) {
      current = line.slice(MARK.length);
      continue;
    }
    const name = line.split('/').pop()!;
    if (current && !dates.has(name)) dates.set(name, current);
  }
  return dates;
}

/** O mais recente entre a data do frontmatter e a do commit (quando houver). */
export function postLastmod(front: Date, gitIso?: string): Date {
  if (!gitIso) return front;
  const g = new Date(gitIso);
  if (Number.isNaN(g.getTime())) throw new Error(`data de commit inválida: ${gitIso}`);
  return g > front ? g : front;
}

/** Commits dos posts desde `since`, por nome de arquivo. Falha alto em clone raso ou fora do git. */
export function gitPostDates(dir: string, since: string, run = gitRun): Map<string, string> {
  if (run(['rev-parse', '--is-shallow-repository']).trim() !== 'false') {
    throw new Error('clone raso ou fora do git: o lastmod dos posts sairia errado (use fetch-depth: 0)');
  }
  return parseGitLog(run(['log', `--since=${since}`, `--format=${MARK}%cI`, '--name-only', '--', dir]));
}

function gitRun(args: string[]): string {
  return execFileSync('git', args, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
}

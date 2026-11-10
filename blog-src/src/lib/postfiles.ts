// Checagem dos arquivos de post antes do build.
//
// O glob loader do Astro usa o campo slug como id da entrada. Dois arquivos com o mesmo slug
// viram uma entrada só, sem erro no build: um post some em silêncio. Exigir arquivo <slug>.md
// direto em src/content/posts/ torna o slug único pelo próprio sistema de arquivos, e esta
// checagem garante a regra.

export interface PostFile {
  /** Caminho relativo a src/content/posts, com '/'. */
  path: string;
  text: string;
}

/** Slug do bloco de frontmatter inicial, ou null se não houver. */
export function frontmatterSlug(text: string): string | null {
  const m = text.match(/^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/);
  if (!m) return null;
  for (const line of m[1].split(/\r?\n/)) {
    const km = line.match(/^slug:\s*(.*?)\s*$/);
    if (km) return km[1].replace(/^(['"])(.*)\1$/, '$2');
  }
  return null;
}

/**
 * O check_site.py lê slug e index do frontmatter com um parser simples. Para os dois lerem o mesmo
 * valor que o Astro, esses campos só aparecem uma vez e em formato simples, sem comentário.
 */
function simpleScalarProblems(text: string): string[] {
  const m = text.match(/^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/);
  if (!m) return [];
  const lines = m[1].split(/\r?\n/);
  const out: string[] = [];
  const rules: [string, RegExp][] = [
    ['slug', /^slug: (?:[a-z0-9-]+|"[a-z0-9-]+"|'[a-z0-9-]+')$/],
    ['index', /^index: (?:true|false)$/],
  ];
  for (const [key, ok] of rules) {
    const found = lines.filter((l) => new RegExp(`^${key}\\s*:`).test(l));
    if (found.length > 1) out.push(`campo ${key} aparece mais de uma vez`);
    for (const l of found) if (!ok.test(l)) out.push(`campo ${key} fora do formato simples: ${JSON.stringify(l)}`);
  }
  return out;
}

export function checkPostFiles(files: PostFile[]): string[] {
  const problems: string[] = [];
  const bySlug = new Map<string, string[]>();
  for (const f of files) {
    if (f.path.includes('/')) {
      problems.push(`${f.path}: post em subpasta; use src/content/posts/<slug>.md`);
      continue;
    }
    for (const p of simpleScalarProblems(f.text)) problems.push(`${f.path}: ${p}`);
    const slug = frontmatterSlug(f.text);
    if (!slug) {
      problems.push(`${f.path}: sem slug no frontmatter`);
      continue;
    }
    const stem = f.path.replace(/\.md$/, '');
    if (stem !== slug) problems.push(`${f.path}: nome do arquivo difere do slug "${slug}"; renomeie para ${slug}.md`);
    bySlug.set(slug, [...(bySlug.get(slug) ?? []), f.path]);
  }
  for (const [slug, paths] of bySlug) {
    if (paths.length > 1) problems.push(`slug duplicado "${slug}": ${paths.join(', ')}`);
  }
  return problems;
}

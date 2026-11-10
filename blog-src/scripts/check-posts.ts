// Roda antes do build (npm run build): falha se algum post fugir da regra <slug>.md.
import { readdirSync, readFileSync } from 'node:fs';
import { join, relative, sep } from 'node:path';
import { checkPostFiles, checkPublicFiles, type PostFile } from '../src/lib/postfiles.ts';

const DIR = 'src/content/posts';

function walk(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = join(dir, e.name);
    if (e.isDirectory()) return walk(p);
    return e.name.endsWith('.md') ? [p] : [];
  });
}

const files: PostFile[] = walk(DIR).map((p) => ({
  path: relative(DIR, p).split(sep).join('/'),
  text: readFileSync(p, 'utf-8'),
}));
const PUBLIC = 'public';
const allFiles = (dir: string): string[] =>
  readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = join(dir, e.name);
    return e.isDirectory() ? allFiles(p) : [p];
  });
const publicFiles = allFiles(PUBLIC).map((p) => relative(PUBLIC, p).split(sep).join('/'));

const problems = [...checkPostFiles(files), ...checkPublicFiles(publicFiles)];
for (const p of problems) console.error(`ERRO: ${p}`);
if (problems.length) process.exit(1);
console.log(`check-posts: ${files.length} posts, nomes de arquivo iguais aos slugs, sem duplicata; ${publicFiles.length} arquivos em public/, todos mídia`);

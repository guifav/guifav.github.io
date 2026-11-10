// Roda antes do build (npm run build). Falha se algum post fugir da regra <slug>.md, e se
// public/ tiver algo além de mídia da lista ou link simbólico (contrato, barreira 2).
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
const rel = (p: string) => relative(PUBLIC, p).split(sep).join('/');
// Link simbólico não entra em public/: o Vite segue o link ao copiar, então um diretório
// simbólico com nome de mídia (review.webp -> pasta com index.html) publicaria HTML.
const publicSymlinks: string[] = [];
const allFiles = (dir: string): string[] =>
  readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = join(dir, e.name);
    if (e.isSymbolicLink()) {
      publicSymlinks.push(rel(p));
      return [];
    }
    return e.isDirectory() ? allFiles(p) : [p];
  });
const publicFiles = allFiles(PUBLIC).map(rel);

const problems = [
  ...checkPostFiles(files),
  ...checkPublicFiles(publicFiles),
  ...publicSymlinks.map((p) => `public/${p}: link simbólico não é aceito em public/; ver CONTRATO-DE-CONTEUDO.md`),
];
for (const p of problems) console.error(`ERRO: ${p}`);
if (problems.length) process.exit(1);
console.log(`check-posts: ${files.length} posts, nomes de arquivo iguais aos slugs, sem duplicata; ${publicFiles.length} arquivos em public/, todos mídia`);

// Roda o scripts/check-posts.ts de verdade (o que o npm run build chama antes do astro build),
// numa pasta temporária com src/content/posts e public/.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';

const SCRIPT = fileURLToPath(new URL('../scripts/check-posts.ts', import.meta.url));
const POST = '---\ntitle: x\ndescription: d\ndate: 2026-11-10\nslug: um-post\n---\ntexto\n';

function run(publicFiles: string[]) {
  const dir = mkdtempSync(join(tmpdir(), 'check-posts-'));
  try {
    mkdirSync(join(dir, 'src/content/posts'), { recursive: true });
    writeFileSync(join(dir, 'src/content/posts/um-post.md'), POST);
    for (const f of publicFiles) {
      mkdirSync(join(dir, 'public', f, '..'), { recursive: true });
      writeFileSync(join(dir, 'public', f), 'x');
    }
    if (!publicFiles.length) mkdirSync(join(dir, 'public'));
    return spawnSync(process.execPath, [SCRIPT], { cwd: dir, encoding: 'utf-8' });
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

test('check-posts.ts: public/ só com mídia passa', () => {
  const r = run(['img/um-post/capa.webp', 'video/v.mp4']);
  assert.equal(r.status, 0, r.stderr);
});

test('check-posts.ts: HTML, SVG ou JS em public/ falham antes do build', () => {
  for (const f of ['marked.html', 'img/um-post/d.svg', 'app.js']) {
    const r = run(['img/um-post/capa.webp', f]);
    assert.equal(r.status, 1, f);
    assert.ok(r.stderr.includes(`public/${f}`), r.stderr);
  }
});

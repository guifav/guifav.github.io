// Renderiza cada portfolio/mockups/<slug>.html como portfolio/img/<slug>.webp.
//
// Uso:
//   npm run render            -> todos os mockups
//   npm run render -- tinna   -> só os slugs informados
//
// Cada HTML declara o próprio viewport em <meta name="mockup-viewport" content="1440x900">.
// Renderiza a 2x e salva com 1600 px de largura. Requer cwebp no PATH (brew install webp).

import { chromium } from 'playwright';
import { execFileSync } from 'node:child_process';
import { readdirSync, readFileSync, mkdtempSync, rmSync } from 'node:fs';
import { join, dirname, basename } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { tmpdir } from 'node:os';

const root = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const srcDir = join(root, 'portfolio', 'mockups');
const outDir = join(root, 'portfolio', 'img');

const wanted = process.argv.slice(2);
const files = readdirSync(srcDir)
  .filter((f) => f.endsWith('.html'))
  .filter((f) => wanted.length === 0 || wanted.includes(basename(f, '.html')));

if (files.length === 0) {
  console.error('Nenhum mockup encontrado para:', wanted.join(', ') || '(todos)');
  process.exit(1);
}

function viewportOf(html) {
  const m = html.match(/<meta\s+name="mockup-viewport"\s+content="(\d+)x(\d+)"/);
  if (!m) throw new Error('falta <meta name="mockup-viewport" content="LxA">');
  return { width: Number(m[1]), height: Number(m[2]) };
}

const tmp = mkdtempSync(join(tmpdir(), 'mockups-'));
const browser = await chromium.launch();
let failed = 0;
try {
  for (const file of files) {
    const slug = basename(file, '.html');
    const path = join(srcDir, file);
    try {
      const viewport = viewportOf(readFileSync(path, 'utf8'));
      const page = await browser.newPage({ viewport, deviceScaleFactor: 2 });
      const errors = [];
      page.on('pageerror', (e) => errors.push(e.message));
      page.on('requestfailed', (r) => errors.push(`request failed: ${r.url()}`));
      await page.goto(pathToFileURL(path).href, { waitUntil: 'networkidle' });
      await page.evaluate(() => document.fonts.ready);
      if (errors.length) throw new Error(errors.join('; '));
      const png = join(tmp, `${slug}.png`);
      await page.screenshot({ path: png, fullPage: false });
      await page.close();
      const out = join(outDir, `${slug}.webp`);
      execFileSync('cwebp', ['-quiet', '-q', '80', '-resize', '1600', '0', png, '-o', out]);
      console.log(`ok  ${slug} -> portfolio/img/${slug}.webp`);
    } catch (e) {
      failed += 1;
      console.error(`ERRO ${slug}: ${e.message}`);
    }
  }
} finally {
  await browser.close();
  rmSync(tmp, { recursive: true, force: true });
}
process.exit(failed ? 1 : 0);

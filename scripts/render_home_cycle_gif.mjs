// Gera o GIF baixável do ciclo de dados e de IA da home (assets/ciclo-dados-ia.gif) e o scripts/home_cycle_gif.json,
// que guarda o hash da fonte do desenho. O test_build_home_cycle.py falha se o SVG ou o CSS do ciclo mudarem sem
// um GIF novo.
//
// Roda na máquina de quem mexe no diagrama, não na CI (precisa de navegador e de ffmpeg):
//   NODE_PATH=<pasta node_modules com o pacote playwright> node scripts/render_home_cycle_gif.mjs
// Exemplo com o playwright do cache do npx: NODE_PATH=$(dirname $(ls -d ~/.npm/_npx/*/node_modules/playwright | head -1))
//
// Os quadros são fotografados com o relógio parado (CSS e SMIL avançados à mão), então o resultado não depende
// da velocidade da máquina. O GIF tem LOOP segundos (5) e repete sem emenda.
import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const LOOP = 5;      // segundos; igual ao LOOP do build_home_cycle.py
const FPS = 12;
const OUT_GIF = join(ROOT, 'assets', 'ciclo-dados-ia.gif');
const OUT_META = join(ROOT, 'scripts', 'home_cycle_gif.json');

const require = createRequire(join(process.cwd(), 'noop.js'));
let chromium;
try {
  ({ chromium } = require('playwright'));
} catch {
  console.error('render_home_cycle_gif: o pacote playwright não foi encontrado; aponte NODE_PATH para a pasta node_modules que o tem.');
  process.exit(1);
}

const work = mkdtempSync(join(tmpdir(), 'ciclo-gif-'));
try {
  const page = join(work, 'ciclo.html');
  const out = execFileSync('python3', [join(ROOT, 'scripts', 'build_home_cycle.py'), '--gif-page', page], { encoding: 'utf-8' });
  const hash = /hash ([0-9a-f]{12})/.exec(out)[1];
  const fullHash = execFileSync('python3', ['-c', 'import sys; sys.path.insert(0, "scripts"); import build_home_cycle as b; print(b.gif_hash())'], { cwd: ROOT, encoding: 'utf-8' }).trim();
  if (!fullHash.startsWith(hash)) throw new Error('hash da fonte do GIF não bate');

  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1000, height: 750 }, deviceScaleFactor: 1, reducedMotion: 'no-preference' });
  const tab = await ctx.newPage();
  await tab.goto(pathToFileURL(page).href);
  await tab.evaluate(() => document.fonts.ready);
  const frames = LOOP * FPS;
  for (let i = 0; i < frames; i++) {
    const t = i / FPS;
    await tab.evaluate((sec) => {
      const svg = document.querySelector('svg');
      svg.pauseAnimations();
      svg.setCurrentTime(sec);
      for (const a of document.getAnimations()) { a.pause(); a.currentTime = sec * 1000; }
    }, t);
    await tab.screenshot({ path: join(work, `f${String(i).padStart(3, '0')}.png`), clip: { x: 0, y: 0, width: 1000, height: 750 } });
  }
  await browser.close();

  const pal = '[0:v]split[a][b];[a]palettegen=max_colors=96:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle';
  execFileSync('ffmpeg', ['-loglevel', 'error', '-y', '-framerate', String(FPS), '-i', join(work, 'f%03d.png'), '-filter_complex', pal, '-loop', '0', OUT_GIF]);
  writeFileSync(OUT_META, JSON.stringify({ source_sha256: fullHash, loop_seconds: LOOP, fps: FPS, frames }, null, 2) + '\n');
  console.log(`render_home_cycle_gif: ${OUT_GIF} (${(statSync(OUT_GIF).size / 1024).toFixed(0)} KB, ${frames} quadros)`);
} finally {
  rmSync(work, { recursive: true, force: true });
}

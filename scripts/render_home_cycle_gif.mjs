// Gera o GIF baixável do ciclo de dados e de IA da home (assets/ciclo-dados-ia.gif) e o scripts/home_cycle_gif.json,
// com o hash da fonte do desenho (SVG do desktop, CSS do diagrama e tema claro do site.css) e o hash do próprio GIF.
// O test_build_home_cycle.py falha se o desenho mudar sem um GIF novo, ou se o GIF não for o registrado.
//
// Roda na máquina de quem mexe no diagrama, não na CI (precisa de navegador e de ffmpeg):
//   NODE_PATH=<pasta node_modules com o pacote playwright> node scripts/render_home_cycle_gif.mjs
// Exemplo com o playwright do cache do npx: NODE_PATH=$(dirname $(ls -d ~/.npm/_npx/*/node_modules/playwright | head -1))
//
// O tempo de cada quadro é fixo: o relógio do CSS e do SMIL é parado e avançado à mão. Os pixels e os bytes, porém,
// dependem do que está instalado (a fonte Inter, se houver, e as versões do Chromium e do ffmpeg, que ficam no json).
import { execFileSync } from 'node:child_process';
import { copyFileSync, mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const OUT_GIF = join(ROOT, 'assets', 'ciclo-dados-ia.gif');
const OUT_META = join(ROOT, 'scripts', 'home_cycle_gif.json');
const W = 1000, H = 750;

function fail(msg) {
  console.error(`render_home_cycle_gif: ${msg}`);
  process.exitCode = 1;
}

const require = createRequire(join(process.cwd(), 'noop.js'));
let chromium, ffmpegVersion;
try {
  ({ chromium } = require('playwright'));
} catch {
  fail('o pacote playwright não foi encontrado; aponte NODE_PATH para a pasta node_modules que o tem.');
  process.exit(1);
}
try {
  ffmpegVersion = execFileSync('ffmpeg', ['-version'], { encoding: 'utf-8' }).split('\n')[0].trim();
} catch (e) {
  fail(e.code === 'ENOENT' ? 'ffmpeg não está instalado ou não está no PATH.' : `ffmpeg falhou: ${e.message}`);
  process.exit(1);
}

const work = mkdtempSync(join(tmpdir(), 'ciclo-gif-'));
let browser;
try {
  const page = join(work, 'ciclo.html');
  let info;
  try {
    info = JSON.parse(execFileSync('python3', [join(ROOT, 'scripts', 'build_home_cycle.py'), '--gif-page', page], { encoding: 'utf-8' }));
  } catch (e) {
    throw new Error(`build_home_cycle.py --gif-page não devolveu o JSON esperado: ${e.message}`);
  }
  const { source_sha256: sourceHash, loop_seconds: loop, fps } = info;
  if (!/^[0-9a-f]{64}$/.test(sourceHash) || !(loop > 0) || !(fps > 0)) throw new Error(`JSON inesperado: ${JSON.stringify(info)}`);

  browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: W, height: H }, deviceScaleFactor: 1, colorScheme: 'light', reducedMotion: 'no-preference' });
  const tab = await ctx.newPage();
  await tab.goto(pathToFileURL(page).href);
  await tab.evaluate(() => document.fonts.ready);
  const frames = Math.round(loop * fps);
  for (let i = 0; i < frames; i++) {
    // segundo ciclo (de loop a 2 * loop): nenhum atraso inicial cai dentro da captura
    const t = loop + i / fps;
    await tab.evaluate((sec) => {
      const svg = document.querySelector('svg');
      svg.pauseAnimations();
      svg.setCurrentTime(sec);
      for (const a of document.getAnimations()) { a.pause(); a.currentTime = sec * 1000; }
    }, t);
    await tab.screenshot({ path: join(work, `f${String(i).padStart(3, '0')}.png`), clip: { x: 0, y: 0, width: W, height: H } });
  }
  const chromiumVersion = browser.version();
  await browser.close();
  browser = undefined;

  const tmpGif = join(work, 'ciclo.gif');
  const pal = '[0:v]split[a][b];[a]palettegen=max_colors=96:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle';
  execFileSync('ffmpeg', ['-loglevel', 'error', '-y', '-framerate', String(fps), '-i', join(work, 'f%03d.png'), '-filter_complex', pal, '-loop', '0', tmpGif]);
  copyFileSync(tmpGif, OUT_GIF);
  const gifHash = createHash('sha256').update(readFileSync(OUT_GIF)).digest('hex');
  const meta = { source_sha256: sourceHash, gif_sha256: gifHash, width: W, height: H, loop_seconds: loop, fps, frames,
    chromium: chromiumVersion, ffmpeg: ffmpegVersion };
  writeFileSync(OUT_META, JSON.stringify(meta, null, 2) + '\n');
  console.log(`render_home_cycle_gif: ${OUT_GIF} (${(statSync(OUT_GIF).size / 1024).toFixed(0)} KB, ${frames} quadros)`);
} catch (e) {
  fail(e.message);
} finally {
  if (browser) await browser.close().catch(() => {});
  rmSync(work, { recursive: true, force: true });
}

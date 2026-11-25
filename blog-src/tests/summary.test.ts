// Renderização real do campo summary ("Em resumo") em src/pages/[slug].astro (#108).
// Roda um astro build em diretório temporário com os arquivos reais do blog + dois posts sintéticos.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, readdirSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';

const BLOG_ROOT = fileURLToPath(new URL('..', import.meta.url));
const ASTRO_BIN = join(BLOG_ROOT, 'node_modules/.bin/astro');

function buildPosts(extraPosts: Record<string, string>): Record<string, string> {
  const dir = mkdtempSync(join(tmpdir(), 'blog-summary-'));
  try {
    for (const name of ['node_modules', 'package.json', 'astro.config.mjs']) {
      symlinkSync(join(BLOG_ROOT, name), join(dir, name));
    }
    mkdirSync(join(dir, 'src/pages'), { recursive: true });
    mkdirSync(join(dir, 'src/content/posts'), { recursive: true });
    for (const item of ['layouts', 'components', 'styles', 'lib', 'consts.ts', 'content.config.ts']) {
      symlinkSync(join(BLOG_ROOT, 'src', item), join(dir, 'src', item));
    }
    symlinkSync(join(BLOG_ROOT, 'src/pages/[slug].astro'), join(dir, 'src/pages/[slug].astro'));
    for (const file of readdirSync(join(BLOG_ROOT, 'src/content/posts'))) {
      symlinkSync(join(BLOG_ROOT, 'src/content/posts', file), join(dir, 'src/content/posts', file));
    }
    for (const [slug, md] of Object.entries(extraPosts)) {
      writeFileSync(join(dir, 'src/content/posts', `${slug}.md`), md, 'utf-8');
    }
    const r = spawnSync(ASTRO_BIN, ['build'], {
      cwd: dir,
      encoding: 'utf-8',
      env: { ...process.env, ASTRO_TELEMETRY_DISABLED: '1' },
    });
    assert.equal(r.status, 0, `astro build falhou:\n${r.stdout}\n${r.stderr}`);
    const out: Record<string, string> = {};
    for (const slug of Object.keys(extraPosts)) {
      out[slug] = readFileSync(join(dir, 'dist', slug, 'index.html'), 'utf-8');
    }
    return out;
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

test('[slug].astro: renderiza "Em resumo" acima de .post-body e <figure>, sem substituir description (#108)', () => {
  const descCom = 'Descrição de meta para busca no Google com mais de setenta caracteres para o post com resumo.';
  const sumText = 'Resumo factual em duas frases com <script>alert(1)</script> & texto puro. Responde direto à pergunta.';
  const descSem = 'Descrição de meta para o post sem campo summary, preservada intacta no HTML gerado.';

  const pages = buildPosts({
    'teste-com-summary-108': [
      '---',
      'title: "Post de teste com summary"',
      `description: "${descCom}"`,
      `summary: "${sumText}"`,
      'date: 2026-11-24T12:00:00Z',
      'slug: teste-com-summary-108',
      'type: newsletter',
      'index: false',
      '---',
      '',
      '<figure><img src="/blog/img/a.webp" alt="Exemplo"></figure>',
      '',
      'Parágrafo inicial do corpo.',
    ].join('\n'),
    'teste-sem-summary-108': [
      '---',
      'title: "Post de teste sem summary"',
      `description: "${descSem}"`,
      'date: 2026-11-24T12:00:00Z',
      'slug: teste-sem-summary-108',
      'type: newsletter',
      'index: false',
      '---',
      '',
      '<figure><img src="/blog/img/a.webp" alt="Exemplo"></figure>',
      '',
      'Corpo sem resumo.',
    ].join('\n'),
    'teste-summary-multilinha-108': [
      '---',
      'title: "Post de teste com summary multilinha"',
      `description: "${descSem}"`,
      'summary:',
      '  Resumo indentado na linha seguinte.',
      '  Segunda frase do resumo.',
      'date: 2026-11-24T12:00:00Z',
      'slug: teste-summary-multilinha-108',
      'type: newsletter',
      'index: false',
      '---',
      '',
      'Corpo com resumo multilinha.',
    ].join('\n'),
  });

  const com = pages['teste-com-summary-108'];
  const summaryIdx = com.indexOf('<section class="post-summary" aria-label="Em resumo">');
  const bodyIdx = com.indexOf('<div class="post-body">');
  const figureIdx = com.indexOf('<figure>');

  assert.ok(summaryIdx > -1, 'bloco .post-summary ausente quando summary existe');
  assert.ok(bodyIdx > -1, '.post-body ausente');
  assert.ok(figureIdx > -1, '<figure> ausente');
  assert.ok(summaryIdx < bodyIdx, '.post-summary precisa vir antes de .post-body');
  assert.ok(summaryIdx < figureIdx, '.post-summary precisa vir antes de qualquer <figure>');
  assert.ok(com.includes('<p class="post-summary-label">Em resumo</p>'), 'rótulo "Em resumo" ausente');
  assert.ok(
    com.includes('Resumo factual em duas frases com &lt;script&gt;alert(1)&lt;/script&gt; &amp; texto puro.'),
    'conteúdo do summary não apareceu escapado no HTML',
  );
  assert.equal(com.includes('<script>alert(1)</script>'), false, 'HTML cru não pode vazar do summary');

  // summary nunca substitui description em meta tags nem no JSON-LD.
  assert.ok(com.includes(`<meta name="description" content="${descCom}">`), 'meta description alterada');
  assert.ok(com.includes(`<meta property="og:description" content="${descCom}">`), 'og:description alterada');
  assert.ok(com.includes(`<meta name="twitter:description" content="${descCom}">`), 'twitter:description alterada');
  const ldMatch = com.match(/<script data-site-script type="application\/ld\+json">([\s\S]*?)<\/script>/);
  assert.ok(ldMatch, 'JSON-LD ausente');
  const ld = JSON.parse(ldMatch[1]);
  const posting = ld['@graph'].find((n: { '@type'?: string }) => n['@type'] === 'BlogPosting');
  assert.equal(posting.description, descCom);

  const sem = pages['teste-sem-summary-108'];
  assert.equal(sem.includes('post-summary'), false, 'bloco .post-summary não pode aparecer sem summary');
  assert.equal(sem.includes('Em resumo'), false, 'rótulo "Em resumo" não pode aparecer sem summary');
  assert.ok(sem.includes(`<meta name="description" content="${descSem}">`), 'meta description alterada sem summary');

  const multi = pages['teste-summary-multilinha-108'];
  assert.ok(multi.includes('<section class="post-summary" aria-label="Em resumo">'), 'summary multilinha ausente');
  assert.ok(multi.includes('Resumo indentado na linha seguinte. Segunda frase do resumo.'), 'texto multilinha incorreto');
});

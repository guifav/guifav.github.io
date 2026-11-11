// O astro.config.mjs liga a limpeza do HTML (rawHtml + plugin de sanitização) no processador de
// markdown. Sem este teste, tirar o plugin do config passaria despercebido: os posts de teste não
// têm HTML ativo.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import config from '../astro.config.mjs';
import { markdownToHtml } from 'satteri';

test('astro.config: markdown passa pelo rawHtml e pelo plugin de limpeza', () => {
  const processor = config.markdown?.processor as { name: string; options: { features: { rawHtml?: boolean }; hastPlugins: unknown[] } };
  assert.equal(processor.name, 'satteri');
  assert.equal(processor.options.features.rawHtml, true);
  const names = processor.options.hastPlugins.map((p) => (p as { name?: string }).name);
  assert.ok(names.includes('guifav-sanitize'), JSON.stringify(names));
});

test('astro.config: base /blog com barra final e saída estática', () => {
  assert.equal(config.base, '/blog');
  assert.equal(config.trailingSlash, 'always');
  assert.equal(config.output, 'static');
});

test('astro.config: sem pontuação tipográfica, o texto dos posts sai como foi escrito (#19)', async () => {
  // O Satteri liga smartPunctuation por padrão: "..." vira … e aspas retas viram curvas.
  const processor = config.markdown?.processor as { options: { features: Record<string, unknown> } };
  assert.equal(processor.options.features.smartPunctuation, false);
  const { html } = await markdownToHtml('ele disse "sim"... e -- depois', { features: processor.options.features });
  assert.equal(html.trim(), '<p>ele disse "sim"... e -- depois</p>');
});

test('astro.config: CSS sempre embutido no HTML (contrato: sob /blog/ só .html, .xml e mídia)', () => {
  // Sem isto, o Astro passa a gerar /blog/_astro/*.css quando a folha cresce além do limite de inline.
  assert.equal(config.build?.inlineStylesheets, 'always');
});

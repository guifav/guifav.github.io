// O astro.config.mjs liga a limpeza do HTML (rawHtml + plugin de sanitização) no processador de
// markdown. Sem este teste, tirar o plugin do config passaria despercebido: os posts de teste não
// têm HTML ativo.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import config from '../astro.config.mjs';

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

test('astro.config: CSS sempre embutido no HTML (contrato: sob /blog/ só .html, .xml e mídia)', () => {
  // Sem isto, o Astro passa a gerar /blog/_astro/*.css quando a folha cresce além do limite de inline.
  assert.equal(config.build?.inlineStylesheets, 'always');
});

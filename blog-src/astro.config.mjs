// Blog em guifav.dev/blog (épico #16, issue #17).
// Saída estática; o workflow do Pages copia dist/ para _site/blog/.
import { defineConfig } from 'astro/config';
import { satteri } from '@astrojs/markdown-satteri';
import { MARKDOWN_FEATURES, sanitizePlugin } from './src/lib/sanitize.ts';

export default defineConfig({
  site: 'https://guifav.dev',
  base: '/blog',
  trailingSlash: 'always',
  output: 'static',
  build: {
    format: 'directory',
    // CSS sempre dentro do HTML: o contrato só permite .html, .xml e mídia sob /blog/.
    inlineStylesheets: 'always',
  },
  markdown: {
    // HTML cru dos posts é reinterpretado como o navegador faria e limpo por lista do que é
    // permitido (src/lib/sanitize.ts). O realce de código roda antes da limpeza.
    processor: satteri({ features: MARKDOWN_FEATURES, hastPlugins: [sanitizePlugin()] }),
    shikiConfig: {
      // Duas paletas: o CSS escolhe pela data-theme do body, igual ao resto do site.
      themes: { light: 'github-light', dark: 'github-dark' },
      defaultColor: false,
    },
  },
});

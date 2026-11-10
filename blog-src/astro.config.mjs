// Blog em guifav.dev/blog (épico #16, issue #17).
// Saída estática; o workflow do Pages copia dist/ para _site/blog/.
import { defineConfig } from 'astro/config';

export default defineConfig({
  site: 'https://guifav.dev',
  base: '/blog',
  trailingSlash: 'always',
  output: 'static',
  build: {
    format: 'directory',
  },
  markdown: {
    shikiConfig: {
      // Duas paletas: o CSS escolhe pela data-theme do body, igual ao resto do site.
      themes: { light: 'github-light', dark: 'github-dark' },
      defaultColor: false,
    },
  },
});

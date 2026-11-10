// Constantes do blog. Mudanças de política (indexação, formulário) passam por aqui.

export const SITE_URL = 'https://guifav.dev';
export const BLOG_NAME = 'Blog';
export const AUTHOR_NAME = 'Guilherme Favaron';
export const AUTHOR_ID = 'https://guifav.dev/#person';
export const BLOG_DESCRIPTION =
  'Artigos de Guilherme Favaron sobre IA aplicada, dados e engenharia de software, com código, arquitetura e decisões técnicas.';
export const GA4_ID = 'G-K7Y87QNCX8';
export const POSTS_PER_PAGE = 20;
export const RSS_LIMIT = 50;

// Até o cutover (issue #24) o blog inteiro sai com noindex e nada no site aponta para ele.
// O PR do cutover troca para false.
export const PRE_CUTOVER_NOINDEX = true;

// O formulário de inscrição chega na issue #21. Até lá o espaço do post fica vazio.
export const NEWSLETTER_FORM_ENABLED = false;

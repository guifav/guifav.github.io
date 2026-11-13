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

// Antes do cutover (issue #24) o blog inteiro saía com noindex e nada no site apontava para ele. Em 2026-11-11
// o Gui antecipou essa parte: o blog entra no menu da home e do portfólio e passa a ser indexável. Podcasts
// (index: false) e as páginas da inscrição seguem com noindex. Redirects e Search Console continuam na #24.
export const PRE_CUTOVER_NOINDEX = false;

// Momento em que o blog passou a ser indexável (commit a1e09bc). Commits de posts depois disso contam no
// lastmod do sitemap (lib/lastmod.ts); os anteriores, de importação e limpeza, não.
export const INDEXABLE_SINCE = '2026-11-11T23:27:33Z';

// Formulário de inscrição (issue #21). Fica desligado até o Worker estar no ar e testado; o PR que
// liga também preenche a chave pública do Turnstile.
export const NEWSLETTER_FORM_ENABLED = true;
// Link "Newsletter" do rodapé: o Substack enquanto o formulário novo está desligado (é o único jeito de
// assinar até lá); com o formulário ligado, a página de assinatura do blog. Sem passo manual no cutover.
export const NEWSLETTER_SUBSTACK_URL = 'https://guilhermefavaron.com.br';
export const NEWSLETTER_ENDPOINT = 'https://insights.guifav.dev/subscribe';
export const TURNSTILE_SITE_KEY = '0x4AAAAAAF8raRhCn4ciudgW';

// Testes das funções puras do blog. Rodar: npm test (node --test, com remoção de tipos do Node 22).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  robotsContent,
  withBase,
  postPath,
  pagePath,
  pageCount,
  absoluteUrl,
  formatDate,
  spotifyEmbedUrl,
  postLdGraph,
  topicLdGraph,
  jsonLdScript,
} from '../src/lib/blog.ts';
import { AUTHOR_ID, AUTHOR_NAME, AUTHOR_URL, WEBSITE_ID } from '../src/consts.ts';

test('robots: noindex antes do cutover, mesmo com index true', () => {
  assert.equal(robotsContent(true, true), 'noindex, follow');
  assert.equal(robotsContent(false, true), 'noindex, follow');
});

test('robots: depois do cutover, só index:false sai do índice', () => {
  assert.equal(robotsContent(true, false), 'index, follow');
  assert.equal(robotsContent(false, false), 'noindex, follow');
});

test('withBase aceita base com e sem barra final', () => {
  assert.equal(withBase('/blog', 'rss.xml'), '/blog/rss.xml');
  assert.equal(withBase('/blog/', '/rss.xml'), '/blog/rss.xml');
  assert.equal(withBase('/blog/', ''), '/blog/');
});

test('caminhos de post e de página', () => {
  assert.equal(postPath('/blog/', 'meu-post'), '/blog/meu-post/');
  assert.equal(pagePath('/blog', 1), '/blog/');
  assert.equal(pagePath('/blog', 3), '/blog/pagina/3/');
  assert.throws(() => pagePath('/blog', 0));
  assert.throws(() => pagePath('/blog', 1.5));
});

test('pageCount arredonda para cima e nunca é zero', () => {
  assert.equal(pageCount(0, 20), 1);
  assert.equal(pageCount(20, 20), 1);
  assert.equal(pageCount(21, 20), 2);
  assert.equal(pageCount(233, 20), 12);
});

test('absoluteUrl usa o domínio do site', () => {
  assert.equal(absoluteUrl('https://guifav.dev', '/blog/x/'), 'https://guifav.dev/blog/x/');
});

test('formatDate usa UTC e português', () => {
  // 23h em São Paulo já é dia seguinte em UTC: o post publicado à 01:00Z continua no dia certo.
  assert.equal(formatDate(new Date('2026-09-02T01:00:00Z')), '2 de setembro de 2026');
});

test('spotifyEmbedUrl: link localizado (/intl-pt/) vira o embed canônico', () => {
  const embed = 'https://open.spotify.com/embed/episode/2ETPXIcx6RwKqdEj17z0xa';
  assert.equal(spotifyEmbedUrl('https://open.spotify.com/intl-pt/episode/2ETPXIcx6RwKqdEj17z0xa'), embed);
  assert.equal(spotifyEmbedUrl('https://open.spotify.com/intl-pt-br/episode/2ETPXIcx6RwKqdEj17z0xa?si=abc'), embed);
  assert.equal(spotifyEmbedUrl('https://open.spotify.com/intl-ja/embed/episode/2ETPXIcx6RwKqdEj17z0xa'), embed);
  assert.equal(spotifyEmbedUrl('https://open.spotify.com/intl-pt/show/2ETPXIcx6RwKqdEj17z0xa'), null);
  assert.equal(spotifyEmbedUrl('https://open.spotify.com/xx/episode/2ETPXIcx6RwKqdEj17z0xa'), null);
});

test('spotifyEmbedUrl: open.spotify.com', () => {
  assert.equal(
    spotifyEmbedUrl('https://open.spotify.com/episode/4rOoJ6Egrf8K2IrywzwOMk'),
    'https://open.spotify.com/embed/episode/4rOoJ6Egrf8K2IrywzwOMk',
  );
  assert.equal(
    spotifyEmbedUrl('https://open.spotify.com/episode/4rOoJ6Egrf8K2IrywzwOMk?si=abc'),
    'https://open.spotify.com/embed/episode/4rOoJ6Egrf8K2IrywzwOMk',
  );
});

test('spotifyEmbedUrl: link do Spotify for Creators (feed do Anchor)', () => {
  const ep = 'Decifrando-a-Mente-da-IA-Como-o-Claude-Pensa-Antes-de-Falar-e3184tj';
  const want = `https://creators.spotify.com/pod/profile/ia-aplicada/embed/episodes/${ep}`;
  assert.equal(spotifyEmbedUrl(`https://podcasters.spotify.com/pod/show/ia-aplicada/episodes/${ep}`), want);
  assert.equal(spotifyEmbedUrl(`https://creators.spotify.com/pod/profile/ia-aplicada/episodes/${ep}`), want);
});

test('spotifyEmbedUrl recusa o que não é episódio do Spotify', () => {
  for (const bad of [
    'http://open.spotify.com/episode/abc',
    'https://open.spotify.com/show/abc',
    'https://evil.example/episode/abc',
    'https://open.spotify.com.evil.example/episode/abc',
    'javascript:alert(1)',
    'não é url',
    'https://podcasters.spotify.com/pod/show/ia-aplicada',
  ]) {
    assert.equal(spotifyEmbedUrl(bad), null, bad);
  }
});

const LD_INPUT = {
  title: 'T',
  description: 'D',
  date: new Date('2026-01-01T00:00:00Z'),
  url: 'https://guifav.dev/blog/t/',
  author: { id: 'https://guifav.dev/#person', name: 'Guilherme Favaron', url: 'https://guifav.dev/' },
  websiteId: 'https://guifav.dev/#website',
  crumbs: [
    { name: 'Guilherme Favaron', url: 'https://guifav.dev/' },
    { name: 'Insights', url: 'https://guifav.dev/blog/' },
  ],
};

function nodes(ld: Record<string, unknown>): Record<string, any>[] {
  assert.equal(ld['@context'], 'https://schema.org');
  assert.ok(Array.isArray(ld['@graph']));
  return ld['@graph'] as Record<string, any>[];
}

function byType(ld: Record<string, unknown>, type: string): Record<string, any> {
  const found = nodes(ld).filter((n) => n['@type'] === type);
  assert.equal(found.length, 1, type);
  return found[0];
}

test('postLdGraph (#105): @graph com BlogPosting, BreadcrumbList e Person, nessa ordem e sem outros nós', () => {
  const ld = postLdGraph(LD_INPUT);
  assert.deepEqual(Object.keys(ld), ['@context', '@graph']);
  assert.deepEqual(nodes(ld).map((n) => n['@type']), ['BlogPosting', 'BreadcrumbList', 'Person']);
  // os nós do @graph não repetem o @context
  for (const n of nodes(ld)) assert.equal('@context' in n, false);
});

test('postLdGraph: autor e publisher são a Person da home; isPartOf é o WebSite da home', () => {
  const post = byType(postLdGraph(LD_INPUT), 'BlogPosting');
  assert.deepEqual(post.author, { '@id': 'https://guifav.dev/#person' });
  assert.deepEqual(post.publisher, { '@id': 'https://guifav.dev/#person' });
  assert.deepEqual(post.isPartOf, { '@id': 'https://guifav.dev/#website' });
  assert.equal(post.headline, 'T');
  assert.equal(post.description, 'D');
  assert.equal(post.url, 'https://guifav.dev/blog/t/');
  assert.equal(post.mainEntityOfPage, 'https://guifav.dev/blog/t/');
  assert.equal(post.inLanguage, 'pt-BR');
  assert.equal(post.datePublished, '2026-01-01T00:00:00.000Z');
  assert.equal(post.dateModified, '2026-01-01T00:00:00.000Z');
  assert.equal('image' in post, false);
});

test('postLdGraph: nó Person mínimo, para o Google resolver author.name e author.url na própria página', () => {
  assert.deepEqual(byType(postLdGraph(LD_INPUT), 'Person'), {
    '@type': 'Person',
    '@id': 'https://guifav.dev/#person',
    name: 'Guilherme Favaron',
    url: 'https://guifav.dev/',
  });
});

test('postLdGraph: BreadcrumbList Home > Blog > título, com posição e URL de cada nível', () => {
  assert.deepEqual(byType(postLdGraph(LD_INPUT), 'BreadcrumbList').itemListElement, [
    { '@type': 'ListItem', position: 1, name: 'Guilherme Favaron', item: 'https://guifav.dev/' },
    { '@type': 'ListItem', position: 2, name: 'Insights', item: 'https://guifav.dev/blog/' },
    { '@type': 'ListItem', position: 3, name: 'T', item: 'https://guifav.dev/blog/t/' },
  ]);
});

test('postLdGraph usa updated e image quando existem', () => {
  const post = byType(
    postLdGraph({ ...LD_INPUT, updated: new Date('2026-02-01T00:00:00Z'), image: 'https://guifav.dev/blog/img/x.webp' }),
    'BlogPosting',
  );
  assert.equal(post.dateModified, '2026-02-01T00:00:00.000Z');
  assert.equal(post.datePublished, '2026-01-01T00:00:00.000Z');
  assert.equal(post.image, 'https://guifav.dev/blog/img/x.webp');
});

test('constantes do JSON-LD (#105): os @id da home e a URL da Person', () => {
  assert.equal(AUTHOR_ID, 'https://guifav.dev/#person');
  assert.equal(WEBSITE_ID, 'https://guifav.dev/#website');
  assert.equal(AUTHOR_URL, 'https://guifav.dev/');
  assert.equal(AUTHOR_NAME, 'Guilherme Favaron');
});

const TOPIC_LD_INPUT = {
  name: 'Agentes de IA em produção',
  description: 'Artigos sobre agentes de IA em produção na empresa.',
  url: 'https://guifav.dev/blog/tema/agentes-de-ia/',
  author: { id: 'https://guifav.dev/#person', name: 'Guilherme Favaron', url: 'https://guifav.dev/' },
  websiteId: 'https://guifav.dev/#website',
  crumbs: [
    { name: 'Guilherme Favaron', url: 'https://guifav.dev/' },
    { name: 'Insights', url: 'https://guifav.dev/blog/' },
  ],
  items: [
    { name: 'Primeiro post', url: 'https://guifav.dev/blog/primeiro/' },
    { name: 'Segundo post', url: 'https://guifav.dev/blog/segundo/' },
  ],
};

test('topicLdGraph (#105): @graph com CollectionPage, BreadcrumbList e Person, nessa ordem e sem outros nós', () => {
  const ld = topicLdGraph(TOPIC_LD_INPUT);
  assert.deepEqual(Object.keys(ld), ['@context', '@graph']);
  assert.deepEqual(nodes(ld).map((n) => n['@type']), ['CollectionPage', 'BreadcrumbList', 'Person']);
  for (const n of nodes(ld)) assert.equal('@context' in n, false);
});

test('topicLdGraph: CollectionPage com @id, url, isPartOf, author, breadcrumb e ItemList em mainEntity', () => {
  const col = byType(topicLdGraph(TOPIC_LD_INPUT), 'CollectionPage');
  assert.equal(col['@id'], 'https://guifav.dev/blog/tema/agentes-de-ia/');
  assert.equal(col.url, 'https://guifav.dev/blog/tema/agentes-de-ia/');
  assert.equal(col.name, 'Agentes de IA em produção');
  assert.equal(col.description, 'Artigos sobre agentes de IA em produção na empresa.');
  assert.equal(col.inLanguage, 'pt-BR');
  assert.deepEqual(col.isPartOf, { '@id': 'https://guifav.dev/#website' });
  assert.deepEqual(col.author, { '@id': 'https://guifav.dev/#person' });
  assert.deepEqual(col.breadcrumb, { '@id': 'https://guifav.dev/blog/tema/agentes-de-ia/#breadcrumb' });
  assert.deepEqual(col.mainEntity, {
    '@type': 'ItemList',
    itemListOrder: 'https://schema.org/ItemListOrderAscending',
    numberOfItems: 2,
    itemListElement: [
      { '@type': 'ListItem', position: 1, name: 'Primeiro post', url: 'https://guifav.dev/blog/primeiro/' },
      { '@type': 'ListItem', position: 2, name: 'Segundo post', url: 'https://guifav.dev/blog/segundo/' },
    ],
  });
  // Cada ListItem traz só @type, position, name e url (sem entidade aninhada nem campos extras).
  for (const item of col.mainEntity.itemListElement) {
    assert.deepEqual(Object.keys(item).sort(), ['@type', 'name', 'position', 'url']);
  }
});

test('topicLdGraph: BreadcrumbList com @id e trilha Home > Insights > tema', () => {
  const breadcrumb = byType(topicLdGraph(TOPIC_LD_INPUT), 'BreadcrumbList');
  assert.equal(breadcrumb['@id'], 'https://guifav.dev/blog/tema/agentes-de-ia/#breadcrumb');
  assert.deepEqual(breadcrumb.itemListElement, [
    { '@type': 'ListItem', position: 1, name: 'Guilherme Favaron', item: 'https://guifav.dev/' },
    { '@type': 'ListItem', position: 2, name: 'Insights', item: 'https://guifav.dev/blog/' },
    { '@type': 'ListItem', position: 3, name: 'Agentes de IA em produção', item: 'https://guifav.dev/blog/tema/agentes-de-ia/' },
  ]);
});

test('topicLdGraph: nó Person mínimo igual ao dos posts', () => {
  assert.deepEqual(byType(topicLdGraph(TOPIC_LD_INPUT), 'Person'), {
    '@type': 'Person',
    '@id': 'https://guifav.dev/#person',
    name: 'Guilherme Favaron',
    url: 'https://guifav.dev/',
  });
});

test('jsonLdScript não deixa o conteúdo fechar a tag script', () => {
  const s = jsonLdScript({ headline: '</script><script>alert(1)</script>' });
  assert.equal(s.includes('</script>'), false);
  assert.deepEqual(JSON.parse(s), { headline: '</script><script>alert(1)</script>' });
});

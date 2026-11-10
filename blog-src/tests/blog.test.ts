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
  blogPostingLd,
  jsonLdScript,
} from '../src/lib/blog.ts';

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

test('blogPostingLd: autor aponta para a Person da home e dateModified cai para a data', () => {
  const ld = blogPostingLd({
    title: 'T',
    description: 'D',
    date: new Date('2026-01-01T00:00:00Z'),
    url: 'https://guifav.dev/blog/t/',
    authorId: 'https://guifav.dev/#person',
  });
  assert.equal(ld['@type'], 'BlogPosting');
  assert.deepEqual(ld.author, { '@id': 'https://guifav.dev/#person' });
  assert.equal(ld.dateModified, '2026-01-01T00:00:00.000Z');
  assert.equal('image' in ld, false);
});

test('blogPostingLd usa updated quando existe', () => {
  const ld = blogPostingLd({
    title: 'T',
    description: 'D',
    date: new Date('2026-01-01T00:00:00Z'),
    updated: new Date('2026-02-01T00:00:00Z'),
    url: 'u',
    image: 'https://guifav.dev/blog/img/x.webp',
    authorId: 'a',
  });
  assert.equal(ld.dateModified, '2026-02-01T00:00:00.000Z');
  assert.equal(ld.image, 'https://guifav.dev/blog/img/x.webp');
});

test('jsonLdScript não deixa o conteúdo fechar a tag script', () => {
  const s = jsonLdScript({ headline: '</script><script>alert(1)</script>' });
  assert.equal(s.includes('</script>'), false);
  assert.deepEqual(JSON.parse(s), { headline: '</script><script>alert(1)</script>' });
});

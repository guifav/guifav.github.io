// Testes do catálogo de posts publicado em /blog/catalogo.json (issue #193).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { catalogoPosts, type CatalogoPost } from '../src/lib/catalogo.ts';

type PostTeste = CatalogoPost & { data: CatalogoPost['data'] & { index?: boolean; type?: string } };

function post(id: string, day: number, extra: Partial<PostTeste['data']> = {}): PostTeste {
  return {
    id,
    data: { date: new Date(Date.UTC(2026, 10, day, 15)), title: `T ${id}`, index: true, type: 'newsletter', ...extra },
  };
}

test('inclui todos os posts publicados, inclusive fora do índice e podcasts, dos mais recentes aos mais antigos', () => {
  const entrada = [
    post('a', 1),
    post('pod', 9, { type: 'podcast', index: false }),
    post('fora', 8, { index: false }),
    post('b', 5, { type: 'podcast', index: true }),
    post('c', 3),
  ];
  const copia = [...entrada];
  const res = catalogoPosts(entrada, '/blog');
  assert.deepEqual(res.posts.map((p) => p.caminho), [
    '/blog/pod/',
    '/blog/fora/',
    '/blog/b/',
    '/blog/c/',
    '/blog/a/',
  ]);
  assert.deepEqual(entrada, copia, 'não altera a ordem da lista recebida');
});

test('mesma data: desempata pelo slug, como sortedPosts', () => {
  const res = catalogoPosts([post('z', 4), post('m', 4)], '/blog');
  assert.deepEqual(res.posts.map((p) => p.caminho), ['/blog/m/', '/blog/z/']);
});

test('campos do item: caminho com a base, título e data AAAA-MM-DD em UTC', () => {
  const res = catalogoPosts([post('x', 24, { date: new Date('2026-11-24T23:30:00-03:00') })], '/blog/');
  assert.deepEqual(res, {
    posts: [{ caminho: '/blog/x/', titulo: 'T x', data: '2026-11-25' }],
  });
});

test('base sem barra final gera o mesmo caminho', () => {
  const res = catalogoPosts([post('primeiro-post', 10)], '/blog');
  assert.deepEqual(res.posts, [{ caminho: '/blog/primeiro-post/', titulo: 'T primeiro-post', data: '2026-11-10' }]);
});

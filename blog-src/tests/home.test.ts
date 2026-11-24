// Testes dos últimos artigos da home do site (issue #136).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { homeLatest, type HomePost } from '../src/lib/home.ts';

function post(id: string, day: number, extra: Partial<HomePost['data']> = {}): HomePost {
  return {
    id,
    data: { date: new Date(Date.UTC(2026, 10, day, 15)), index: true, type: 'newsletter', title: `T ${id}`,
      description: `D ${id}`, ...extra },
  };
}

test('os mais recentes primeiro, sem podcast nem post fora do índice, até n', () => {
  const posts = [post('a', 1), post('b', 5), post('pod', 9, { type: 'podcast', index: false }),
    post('fora', 8, { index: false }), post('c', 3), post('d', 7)];
  assert.deepEqual(homeLatest(posts, 3, '/blog').map((p) => p.path), ['/blog/d/', '/blog/b/', '/blog/c/']);
});

test('mesma data: desempata pelo slug, como o blog', () => {
  assert.deepEqual(homeLatest([post('z', 4), post('m', 4)], 2, '/blog').map((p) => p.path), ['/blog/m/', '/blog/z/']);
});

test('podcast indexável também fica de fora', () => {
  assert.deepEqual(homeLatest([post('p', 9, { type: 'podcast' }), post('a', 1)], 5, '/blog').map((p) => p.path),
    ['/blog/a/']);
});

test('campos do item: título, caminho, data ISO em UTC, data do blog e description', () => {
  const [item] = homeLatest([post('x', 24, { date: new Date('2026-11-24T23:30:00-03:00') })], 1, '/blog/');
  assert.deepEqual(item, { title: 'T x', path: '/blog/x/', date: '2026-11-25', date_label: '25 de novembro de 2026',
    description: 'D x' });
});

// Testes das entradas do sitemap do blog (src/lib/sitemap.ts). Rodar: npm test.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readdirSync } from 'node:fs';
import { sitemapEntries, sitemapXml, type SitemapPost } from '../src/lib/sitemap.ts';
import { POSTS_DIR, gitPostDates } from '../src/lib/lastmod.ts';

const SITE = 'https://guifav.dev/';
const BASE = '/blog/';
const d = (s: string) => new Date(s);
const POSTS: SitemapPost[] = [
  { id: 'novo', data: { date: d('2026-11-01T00:00:00Z'), index: true } },
  { id: 'editado', data: { date: d('2025-01-01T00:00:00Z'), updated: d('2025-06-01T00:00:00Z'), index: true } },
  { id: 'podcast', data: { date: d('2026-10-01T00:00:00Z'), index: false } },
];

function byLoc(git: Map<string, string>) {
  return Object.fromEntries(sitemapEntries(SITE, BASE, POSTS, git).map((e) => [e.loc, e.lastmod?.toISOString() ?? null]));
}

test('capa sem lastmod, posts indexáveis com o frontmatter, podcast fora, sem paginação', () => {
  assert.deepEqual(byLoc(new Map()), {
    'https://guifav.dev/blog/': null,
    'https://guifav.dev/blog/novo/': '2026-11-01T00:00:00.000Z',
    'https://guifav.dev/blog/editado/': '2025-06-01T00:00:00.000Z',
  });
});

test('edição sem updated: o commit depois do corte vira o lastmod do post (e só dele)', () => {
  const got = byLoc(new Map([['editado.md', '2026-11-20T10:00:00-03:00'], ['podcast.md', '2026-11-21T10:00:00-03:00']]));
  assert.equal(got['https://guifav.dev/blog/editado/'], '2026-11-20T13:00:00.000Z');
  assert.equal(got['https://guifav.dev/blog/novo/'], '2026-11-01T00:00:00.000Z');
  assert.equal(got['https://guifav.dev/blog/'], null);
  assert.equal(got['https://guifav.dev/blog/podcast/'], undefined);
});

test('o commit é casado pelo nome <slug>.md', () => {
  const got = byLoc(new Map([['outro.md', '2026-12-01T00:00:00Z'], ['novo.txt', '2026-12-01T00:00:00Z']]));
  assert.equal(got['https://guifav.dev/blog/novo/'], '2026-11-01T00:00:00.000Z');
});

test('XML: escapa e omite lastmod vazio', () => {
  const xml = sitemapXml([{ loc: 'https://guifav.dev/blog/a&b/' }, { loc: 'https://guifav.dev/blog/c/', lastmod: d('2026-01-01T00:00:00Z') }]);
  assert.ok(xml.includes('<url><loc>https://guifav.dev/blog/a&amp;b/</loc></url>'));
  assert.ok(xml.includes('<url><loc>https://guifav.dev/blog/c/</loc><lastmod>2026-01-01T00:00:00.000Z</lastmod></url>'));
  assert.ok(xml.startsWith('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'));
});

test('POSTS_DIR existe no cwd do build e é onde estão os posts', () => {
  assert.ok(existsSync(POSTS_DIR), POSTS_DIR);
  assert.ok(readdirSync(POSTS_DIR).filter((f: string) => f.endsWith('.md')).length > 100);
  // O git enxerga os commits desse caminho (um commit qualquer, sem corte de data).
  assert.ok(gitPostDates(POSTS_DIR, '2000-01-01T00:00:00Z').size > 100);
});

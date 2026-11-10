// Testes do esquema do frontmatter e da checagem dos arquivos de post (rodam antes do build).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { postSchema } from '../src/lib/schema.ts';
import { checkPostFiles, frontmatterSlug } from '../src/lib/postfiles.ts';

const base = {
  title: 't',
  description: 'd',
  date: new Date('2026-11-08T12:00:00Z'),
  slug: 'um-post',
};

test('esquema aceita data do YAML (Date) e string ISO', () => {
  assert.ok(postSchema.safeParse(base).success);
  assert.ok(postSchema.safeParse({ ...base, date: '2026-11-08' }).success);
  assert.ok(postSchema.safeParse({ ...base, date: '2026-11-08T12:00:00-03:00' }).success);
});

test('esquema rejeita data nula, booleana, numérica, inválida ou fora do ISO', () => {
  for (const date of [null, false, true, 0, 1731067200000, '', 'ontem', '08/11/2026', '2026-13-40', new Date('x')]) {
    assert.equal(postSchema.safeParse({ ...base, date }).success, false, `date=${String(date)}`);
  }
  assert.equal(postSchema.safeParse({ ...base, updated: null }).success, false);
});

test('esquema exige spotify_url de episódio do Spotify em https', () => {
  const ok = 'https://podcasters.spotify.com/pod/show/ia-aplicada/episodes/Ep-e3184tj';
  assert.ok(postSchema.safeParse({ ...base, type: 'podcast', spotify_url: ok }).success);
  for (const spotify_url of [
    'javascript:alert(1)',
    'http://open.spotify.com/episode/abc',
    'https://evil.example/episode/abc',
    'https://open.spotify.com/show/abc',
  ]) {
    assert.equal(postSchema.safeParse({ ...base, type: 'podcast', spotify_url }).success, false, spotify_url);
  }
  assert.equal(postSchema.safeParse({ ...base, type: 'podcast' }).success, false);
});

test('esquema mantém as regras de slug, capa e updated', () => {
  assert.equal(postSchema.safeParse({ ...base, slug: 'Com Espaço' }).success, false);
  assert.equal(postSchema.safeParse({ ...base, cover: 'img/../x.webp' }).success, false);
  assert.equal(postSchema.safeParse({ ...base, updated: '2026-11-01' }).success, false);
  assert.equal(postSchema.safeParse({ ...base, index: 'false' }).success, false);
});

const md = (slug: string) => `---\ntitle: x\nslug: ${slug}\n---\ncorpo\n`;

test('frontmatterSlug lê o slug só do cabeçalho', () => {
  assert.equal(frontmatterSlug(md('a-b')), 'a-b');
  assert.equal(frontmatterSlug('---\nslug: "a-b"\n---\n'), 'a-b');
  assert.equal(frontmatterSlug('---\ntitle: x\n---\nslug: fora\n'), null);
  assert.equal(frontmatterSlug('sem cabeçalho'), null);
});

test('checkPostFiles: nome do arquivo igual ao slug, sem duplicata', () => {
  assert.deepEqual(checkPostFiles([{ path: 'a.md', text: md('a') }, { path: 'b.md', text: md('b') }]), []);
  const dup = checkPostFiles([{ path: 'a.md', text: md('a') }, { path: 'a-copia.md', text: md('a') }]);
  assert.ok(dup.some((p) => p.includes('duplicado')), dup.join('\n'));
  assert.ok(dup.some((p) => p.includes('a-copia.md')), dup.join('\n'));
});

test('checkPostFiles: slug e index só em formato simples (o check_site.py lê esses campos)', () => {
  const t = (fm: string) => checkPostFiles([{ path: 'a.md', text: `---\ntitle: x\n${fm}\n---\n` }]);
  assert.deepEqual(t('slug: a'), []);
  assert.deepEqual(t('slug: "a"\nindex: false'), []);
  assert.ok(t('slug: a\nindex: false # comentário').some((p) => p.includes('index')));
  assert.ok(t('slug: a\nindex: no').some((p) => p.includes('index')));
  assert.ok(t('slug: a # x').length > 0);
  assert.ok(t('slug: a\nslug: b').some((p) => p.includes('mais de uma')));
});

test('checkPostFiles: subpasta, slug ausente e nomes que só diferem na caixa', () => {
  assert.ok(checkPostFiles([{ path: 'sub/a.md', text: md('a') }]).some((p) => p.includes('subpasta')));
  assert.ok(checkPostFiles([{ path: 'a.md', text: '---\ntitle: x\n---\n' }]).some((p) => p.includes('sem slug')));
  assert.ok(checkPostFiles([{ path: 'A.md', text: md('a') }]).length > 0);
});

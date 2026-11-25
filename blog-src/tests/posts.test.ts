// Testes do esquema do frontmatter e da checagem dos arquivos de post (rodam antes do build).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { postSchema, TOPIC_IDS } from '../src/lib/schema.ts';
import { checkPostFiles, checkPublicFiles, frontmatterSlug } from '../src/lib/postfiles.ts';

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

test('esquema rejeita data inexistente no calendário (string)', () => {
  for (const date of ['2026-02-29', '2026-02-30', '2026-04-31', '2026-00-10', '2026-13-01', '2026-01-32', '2026-02-29T12:00:00Z', '2026-01-01T24:00:00Z', '2026-01-01T23:60:00Z']) {
    assert.equal(postSchema.safeParse({ ...base, date }).success, false, date);
    assert.equal(postSchema.safeParse({ ...base, updated: date }).success, false, `updated ${date}`);
  }
  for (const date of ['2024-02-29', '2026-04-30', '2026-12-31T23:59:59.999-03:00']) {
    assert.ok(postSchema.safeParse({ ...base, date }).success, date);
  }
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

test('esquema: topics é opcional e só aceita os 5 temas da #104, sem repetição', () => {
  assert.deepEqual([...TOPIC_IDS], [
    'ia-sobre-dados-da-empresa',
    'custos-e-modelos-de-ia',
    'agentes-de-ia',
    'implantacao-de-ia',
    'engenharia-de-software-com-ia',
  ]);
  const sem = postSchema.safeParse(base);
  assert.ok(sem.success);
  assert.equal(sem.data.topics, undefined);
  // Cada um dos 5 temas sozinho e uma lista com dois (revisão do #124: um mutante que aceitava só os 3 primeiros
  // sobrevivia ao teste antigo).
  for (const topics of [...TOPIC_IDS.map((t) => [t]), ['ia-sobre-dados-da-empresa', 'custos-e-modelos-de-ia']]) {
    const r = postSchema.safeParse({ ...base, topics });
    assert.ok(r.success, JSON.stringify(topics));
    assert.deepEqual(r.data.topics, topics);
  }
  for (const topics of [[], ['fundamentos'], ['agentes-de-ia-em-producao'], ['Agentes-de-IA'], 'agentes-de-ia', ['agentes-de-ia', 'agentes-de-ia'], [null], null]) {
    assert.equal(postSchema.safeParse({ ...base, topics }).success, false, JSON.stringify(topics));
  }
  // topics: null (o YAML de "topics:" sem valor) é recusado; um .nullish() no esquema sobrevivia (revisão do #124).
  assert.equal(postSchema.safeParse({ ...base, topics: null }).success, false);
});

test('esquema: summary é opcional, curto e independente da description', () => {
  const r = postSchema.safeParse({ ...base, summary: 'Duas frases. Respondem a pergunta.' });
  assert.ok(r.success);
  assert.equal(r.data.summary, 'Duas frases. Respondem a pergunta.');
  assert.equal(r.data.description, 'd');
  assert.equal(postSchema.safeParse(base).data?.summary, undefined);
  assert.ok(postSchema.safeParse({ ...base, summary: 'x'.repeat(500) }).success);
  for (const summary of ['', '   ', 'x'.repeat(501), 42, null]) {
    assert.equal(postSchema.safeParse({ ...base, summary }).success, false, JSON.stringify(summary));
  }
  // O summary nunca ocupa o lugar da description.
  const { description: _, ...semDescription } = base;
  assert.equal(postSchema.safeParse({ ...semDescription, summary: 'resumo' }).success, false);
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
  assert.deepEqual(t('slug: a\ntopics: [agentes-de-ia]'), []);
  assert.deepEqual(t('slug: a\ntopics: [agentes-de-ia, ia-sobre-dados-da-empresa]'), []);
  for (const fm of ['topics:\n  - agentes-de-ia-em-producao', 'topics: [agentes-de-ia] # x', "topics: ['agentes-de-ia-em-producao']", 'topics: [a,b]', 'topics: [a]\ntopics: [b]']) {
    assert.ok(t(`slug: a\n${fm}`).some((p) => p.includes('topics')), fm);
  }
  assert.ok(t('slug: a\nslug: b').some((p) => p.includes('mais de uma')));
  // summary e chaves de topo (#108, revisão do #158): rejeitar antes do build grafias YAML que desviam do formato
  // simples (espaço antes de :, chave entre aspas, escape, chave explícita ?, flow mapping ou duplicata).
  assert.deepEqual(t('slug: a\nsummary: "Resposta direta."'), []);
  assert.deepEqual(t('slug: a\nsummary: >\n  Resposta dobrada.'), []);
  assert.deepEqual(t('slug: a\nsummary:\n  Resposta na linha seguinte.'), []);
  for (const fm of [
    'summary : "Resposta."',
    '"summary": "Resposta."',
    "'summary': 'Resposta.'",
    '"\\x73ummary": "Resposta."',
    '? summary\n: "Resposta."',
    '{slug: a, summary: "Resposta."}',
    'summary: "A"\nsummary: "B"',
  ]) {
    assert.ok(t(`slug: a\n${fm}`).length > 0, fm);
  }
});

test('checkPostFiles: data inexistente no calendário falha antes do YAML virar outra data', () => {
  const t = (fm: string) => checkPostFiles([{ path: 'a.md', text: `---\ntitle: x\nslug: a\n${fm}\n---\n` }]);
  assert.deepEqual(t('date: 2026-02-28'), []);
  assert.deepEqual(t('date: "2024-02-29T12:00:00Z"\nupdated: 2026-11-10'), []);
  for (const fm of ['date: 2026-02-30', 'date: "2026-02-29"', "date: '2026-04-31'", 'updated: 2026-02-30T10:00:00Z', 'date: 2026-02-30 # x']) {
    assert.ok(t(fm).some((p) => p.includes('data')), fm);
  }
});

test('checkPostFiles: subpasta, slug ausente e nomes que só diferem na caixa', () => {
  assert.ok(checkPostFiles([{ path: 'sub/a.md', text: md('a') }]).some((p) => p.includes('subpasta')));
  assert.ok(checkPostFiles([{ path: 'a.md', text: '---\ntitle: x\n---\n' }]).some((p) => p.includes('sem slug')));
  assert.ok(checkPostFiles([{ path: 'A.md', text: md('a') }]).length > 0);
});

test('checkPublicFiles: public/ só aceita mídia da lista (contrato, barreira 2)', () => {
  assert.deepEqual(checkPublicFiles(['img/a/capa.webp', 'img/a/b.PNG', 'img/a/c.jpg', 'img/a/d.jpeg', 'img/e.gif', 'img/f.avif', 'video/v.mp4', 'video/w.webm']), []);
  for (const bad of ['marked.html', 'review.HTML', 'x.htm', 'img/a/d.svg', 'app.js', 'm.mjs', 's.css', 'favicon.ico', 'dados.json', 'semextensao', '.htaccess', 'a.webp.html']) {
    const p = checkPublicFiles([bad]);
    assert.ok(p.length === 1 && p[0].includes(bad), `${bad}: ${JSON.stringify(p)}`);
  }
});

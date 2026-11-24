// Testes dos temas (#104): definição, conferência contra os posts e o bloco "Leia também".
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { TOPIC_IDS, type TopicId } from '../src/lib/schema.ts';
import {
  MIN_TOPIC_POSTS,
  TOPICS,
  WAITING_TOPICS,
  isListed,
  relatedPosts,
  topicPath,
  topicPosts,
  topicProblems,
  type Topic,
  type TopicPost,
  type WaitingTopic,
} from '../src/lib/topics.ts';

const A = 'ia-sobre-dados-da-empresa' as TopicId;
const B = 'agentes-de-ia' as TopicId;
const W = 'engenharia-de-software-com-ia' as TopicId;

function post(id: string, day: number, extra: Partial<TopicPost['data']> = {}): TopicPost {
  return { id, data: { date: new Date(Date.UTC(2026, 0, day)), index: true, type: 'newsletter', ...extra } };
}

function topic(id: TopicId, order: string[]): Topic {
  const posts = order.map((slug) => ({ slug, question: `${slug}?` }));
  return { id, name: id, about: id, short: id, description: 'd', intro: 'i', pageCta: ['x'], blockCta: null, posts };
}

const WAIT: WaitingTopic[] = [{ id: W, name: 'w', reason: 'poucos posts' }];

// Cinco temas de teste: A e B com posts, os outros três só para cobrir TOPIC_IDS.
function fixture(aOrder = ['a1', 'a2', 'a3', 'a4', 'a5']) {
  const posts = [
    ...['a1', 'a2', 'a3', 'a4', 'a5'].map((s, i) => post(s, 10 + i, { topics: [A] })),
    ...['b1', 'b2', 'b3', 'b4', 'b5'].map((s, i) => post(s, 20 + i, { topics: [B] })),
    post('solto-1', 1),
    post('solto-2', 3),
    post('solto-3', 4),
    post('solto-4', 30),
    post('podcast', 2, { type: 'podcast', index: false }),
    post('fora', 2, { index: false }),
  ];
  const others = TOPIC_IDS.filter((t) => t !== A && t !== B && t !== W).map((t, k) => {
    const ids = [1, 2, 3, 4, 5].map((n) => `${t}-${n}`);
    posts.push(...ids.map((s, i) => post(s, 40 + k * 5 + i, { topics: [t] })));
    return topic(t, ids);
  });
  const topics = [topic(A, aOrder), topic(B, ['b1', 'b2', 'b3', 'b4', 'b5']), ...others];
  return { posts, topics };
}

test('TOPICS tem os 4 temas com página e WAITING_TOPICS o tema 5, com nome, textos e chamada', () => {
  assert.deepEqual(TOPICS.map((t) => t.id), TOPIC_IDS.slice(0, 4));
  assert.deepEqual(WAITING_TOPICS.map((t) => t.id), ['engenharia-de-software-com-ia']);
  for (const t of TOPICS) {
    assert.ok(t.name && t.short && t.about, t.id);
    const words = t.intro.split(/\s+/).length;
    assert.ok(words >= 60 && words <= 110, `${t.id}: abertura com ${words} palavras (60 a 110, agy na #104)`);
    // O tema 4 fica em 73 caracteres, para manter "implementação" e "sistemas legados" (o Fable aceitou o corte no Google).
    assert.ok(`Artigos sobre ${t.about} | Guilherme Favaron`.length <= 75, `${t.id}: título longo`);
    for (const e of t.posts) assert.ok(e.question.endsWith('?'), `${t.id}: ${e.slug} sem pergunta`);
    assert.ok(t.description.length >= 120 && t.description.length <= 160, `${t.id}: description com ${t.description.length}`);
    assert.ok(t.pageCta.some((p) => typeof p !== 'string'), `${t.id}: chamada da página sem link`);
    assert.ok(!/—/.test(JSON.stringify(t)), `${t.id}: travessão no texto`);
  }
});

test('topicPath monta /blog/tema/<id>/', () => {
  assert.equal(topicPath('/blog/', A), '/blog/tema/ia-sobre-dados-da-empresa/');
  assert.equal(topicPath('/blog', A), '/blog/tema/ia-sobre-dados-da-empresa/');
});

test('isListed: só post indexável que não é podcast', () => {
  assert.ok(isListed(post('x', 1)));
  assert.equal(isListed(post('x', 1, { index: false })), false);
  assert.equal(isListed(post('x', 1, { type: 'podcast' })), false);
});

test('topicProblems: vazio quando a ordem bate com os posts e todo tema tem 5', () => {
  const { posts, topics } = fixture();
  assert.deepEqual(topicProblems(posts, topics, WAIT), []);
});

test('topicProblems: post do tema fora da ordem, ordem sem o tema, repetição e tema faltando', () => {
  let { posts, topics } = fixture(['a1', 'a2', 'a3', 'a4']);
  assert.ok(topicProblems(posts, topics, WAIT).some((p) => p.includes('a5 tem o tema mas falta')));
  ({ posts, topics } = fixture(['a1', 'a2', 'a3', 'a4', 'a5', 'solto-1']));
  assert.ok(topicProblems(posts, topics, WAIT).some((p) => p.includes('solto-1 está na ordem de leitura sem ter o tema')));
  ({ posts, topics } = fixture(['a1', 'a2', 'a3', 'a4', 'a5', 'a1']));
  assert.ok(topicProblems(posts, topics, WAIT).some((p) => p.includes('repetido na ordem')));
  ({ posts, topics } = fixture());
  assert.ok(topicProblems(posts, topics.slice(1), WAIT).some((p) => p.includes(`tema ${A} sem definição em TOPICS nem em WAITING_TOPICS`)));
  assert.ok(topicProblems(posts, [...topics, topics[0]], WAIT).some((p) => p.includes('tema repetido em TOPICS e WAITING_TOPICS')));
});

test('topicProblems: tema com menos de 5 posts indexáveis falha o build (#104)', () => {
  const { posts, topics } = fixture(['a1', 'a2', 'a3', 'a4']);
  const semA5 = posts.filter((p) => p.id !== 'a5');
  const problems = topicProblems(semA5, topics, WAIT);
  assert.deepEqual(problems, [`tema ${A}: 4 posts indexáveis, o mínimo para ter página é ${MIN_TOPIC_POSTS}`]);
});

test('topicProblems: post fora do índice ou podcast não pode ter tema (#107)', () => {
  const { posts, topics } = fixture();
  posts.push(post('podado', 5, { index: false, topics: [A] }), post('ep', 5, { type: 'podcast', topics: [A] }));
  const problems = topicProblems(posts, topics, WAIT);
  assert.ok(problems.includes('podado: post fora do índice ou podcast com topics'), problems.join('\n'));
  assert.ok(problems.includes('ep: post fora do índice ou podcast com topics'), problems.join('\n'));
});

test('topicPosts: na ordem de leitura, não por data, com a pergunta de cada post', () => {
  const { posts, topics } = fixture(['a3', 'a1', 'a5', 'a2', 'a4']);
  assert.deepEqual(topicPosts(posts, topics[0]).map((e) => [e.post.id, e.question]),
    [['a3', 'a3?'], ['a1', 'a1?'], ['a5', 'a5?'], ['a2', 'a2?'], ['a4', 'a4?']]);
});

test('topicProblems: tema em espera não pode ser usado em topics', () => {
  const { posts, topics } = fixture();
  posts.push(post('cedo', 5, { topics: [W] }));
  assert.deepEqual(topicProblems(posts, topics, WAIT), [`cedo: tema ${W} ainda sem página (poucos posts)`]);
});

test('relatedPosts com tema: os 3 seguintes na ordem de leitura, voltando ao começo', () => {
  const { posts, topics } = fixture(['a3', 'a1', 'a5', 'a2', 'a4']);
  const by = (id: string) => posts.find((p) => p.id === id)!;
  assert.deepEqual(relatedPosts(by('a3'), posts, topics).map((p) => p.id), ['a1', 'a5', 'a2']);
  assert.deepEqual(relatedPosts(by('a2'), posts, topics).map((p) => p.id), ['a4', 'a3', 'a1']);
  assert.deepEqual(relatedPosts(by('a4'), posts, topics).map((p) => p.id), ['a3', 'a1', 'a5']);
});

test('relatedPosts com dois temas usa o primeiro, o tema principal', () => {
  const { posts, topics } = fixture();
  const a1 = posts.find((p) => p.id === 'a1')!;
  a1.data.topics = [A, B];
  topics[1].posts = [...topics[1].posts, { slug: 'a1', question: 'a1?' }];
  assert.deepEqual(topicProblems(posts, topics, WAIT), []);
  assert.deepEqual(relatedPosts(a1, posts, topics).map((p) => p.id), ['a2', 'a3', 'a4']);
});

test('relatedPosts sem tema: os 3 indexáveis mais próximos por data, sem podcast nem post fora do índice', () => {
  const { posts, topics } = fixture();
  const by = (id: string) => posts.find((p) => p.id === id)!;
  // solto-2 é do dia 3: dias 4 (solto-3), 1 (solto-1); o podcast e o "fora", do dia 2, não entram; depois a1 (dia 10).
  assert.deepEqual(relatedPosts(by('solto-2'), posts, topics).map((p) => p.id), ['solto-3', 'solto-1', 'a1']);
  // Empate na distância: o mais recente primeiro.
  const semTema = [post('x', 10), post('antes', 9), post('depois', 11), post('longe', 30)];
  assert.deepEqual(relatedPosts(semTema[0], semTema, topics, 2).map((p) => p.id), ['depois', 'antes']);
});

test('relatedPosts: podcast e post fora do índice ficam sem bloco', () => {
  const { posts, topics } = fixture();
  assert.deepEqual(relatedPosts(posts.find((p) => p.id === 'podcast')!, posts, topics), []);
  assert.deepEqual(relatedPosts(posts.find((p) => p.id === 'fora')!, posts, topics), []);
});

// A classificação real: os topics do frontmatter batem com TOPICS (o build também confere, no getStaticPaths).
test('classificação real: frontmatter dos posts e ordem de leitura de TOPICS conferem', () => {
  const dir = new URL('../src/content/posts/', import.meta.url);
  const posts: TopicPost[] = readdirSync(dir)
    .filter((f) => f.endsWith('.md'))
    .map((f) => {
      const fm = readFileSync(new URL(f, dir), 'utf8').split('\n---')[0];
      const topics = fm.match(/^topics: \[(.*)\]$/m)?.[1].split(',').map((s) => s.trim() as TopicId);
      return {
        id: f.replace(/\.md$/, ''),
        data: {
          date: new Date(fm.match(/^date: "?([^"\n]+)/m)![1]),
          index: !/^index: false$/m.test(fm),
          type: /^type: podcast$/m.test(fm) ? 'podcast' : 'newsletter',
          ...(topics ? { topics } : {}),
        },
      };
    });
  assert.deepEqual(topicProblems(posts), []);
  for (const t of TOPICS) assert.ok(t.posts.length >= MIN_TOPIC_POSTS, t.id);
});

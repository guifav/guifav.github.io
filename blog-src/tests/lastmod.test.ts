// Testes do lastmod dos posts (src/lib/lastmod.ts). Rodar: npm test.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { gitPostDates, parseGitLog, postLastmod } from '../src/lib/lastmod.ts';

test('parseGitLog: data mais recente de cada arquivo, pelo nome', () => {
  const out = [
    '@@2026-11-20T10:00:00-03:00', '', 'src/content/posts/a.md', 'src/content/posts/b.md', '',
    '@@2026-11-15T09:00:00-03:00', '', 'src/content/posts/a.md', 'src/content/posts/c.md', '',
  ].join('\n');
  assert.deepEqual([...parseGitLog(out)], [
    ['a.md', '2026-11-20T10:00:00-03:00'],
    ['b.md', '2026-11-20T10:00:00-03:00'],
    ['c.md', '2026-11-15T09:00:00-03:00'],
  ]);
  assert.equal(parseGitLog('').size, 0);
});

test('postLastmod: o mais recente entre frontmatter e commit', () => {
  const front = new Date('2026-09-02T00:00:00Z');
  assert.equal(postLastmod(front).toISOString(), '2026-09-02T00:00:00.000Z');
  assert.equal(postLastmod(front, '2026-11-20T10:00:00-03:00').toISOString(), '2026-11-20T13:00:00.000Z');
  // updated posto à mão no futuro em relação ao commit vence
  assert.equal(postLastmod(new Date('2026-12-01T00:00:00Z'), '2026-11-20T10:00:00-03:00').toISOString(), '2026-12-01T00:00:00.000Z');
  assert.throws(() => postLastmod(front, 'ontem'), /data de commit inválida/);
});

test('gitPostDates: recusa clone raso ou fora do git e pede só o que veio depois de since', () => {
  for (const shallow of ['true\n', '', 'fatal: not a git repository']) {
    assert.throws(() => gitPostDates('src/content/posts', 'S', () => shallow), /clone raso ou fora do git/);
  }
  const calls: string[][] = [];
  const run = (args: string[]) => {
    calls.push(args);
    return args[0] === 'rev-parse' ? 'false\n' : '@@2026-11-20T10:00:00-03:00\n\nsrc/content/posts/a.md\n';
  };
  assert.deepEqual([...gitPostDates('src/content/posts', '2026-11-11T23:27:33Z', run)], [['a.md', '2026-11-20T10:00:00-03:00']]);
  assert.deepEqual(calls[1], ['log', '--since=2026-11-11T23:27:33Z', '--format=@@%cI', '--name-only', '--', 'src/content/posts']);
});

test('gitPostDates no repo real: o histórico existe e não é raso', () => {
  // Roda contra o git deste clone (o build exige histórico completo).
  assert.ok(gitPostDates('src/content/posts', '2026-11-11T23:27:33Z') instanceof Map);
});

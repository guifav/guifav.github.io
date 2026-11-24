// Testes da origem das conversões (issue #114): validação no Worker, link de confirmação com a origem assinada, CHECK
// do D1 e o ponta a ponta (track.js, formulário, Worker e D1). Rodar em workers/newsletter/: npm test
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseOrigem, encodeOrigem, decodeOrigem, CAMPOS, CANAIS, FONTES, type Origem } from '../src/origem.ts';
import { signLink, verifyConfirmLink, verifyLink } from '../src/token.ts';
import { recordConversion } from '../src/conversoes.ts';
import { d1, rows, capturaErros } from './d1.ts';
import { pontaAPonta, PROIBIDOS } from './ponta-a-ponta.ts';

const NOW = Date.UTC(2026, 10, 24, 12, 0, 0);
const SECRET = 'segredo-de-teste-com-tamanho-suficiente-0123456789';
const form = (o: Record<string, string>) => new URLSearchParams(o);
const BOA = { origem_canal: 'busca', origem_fonte: 'google', utm_source: 'google', utm_medium: 'organic', utm_campaign: 'lancamento',
  origem_entrada: '/blog/rag/' };
const NADA: Origem = { canal: 'desconhecida', fonte: null, utmSource: null, utmMedium: null, utmCampaign: null, entrada: null };

test('O1: parseOrigem aceita os campos válidos', () => {
  assert.deepEqual(parseOrigem(form(BOA)),
    { canal: 'busca', fonte: 'google', utmSource: 'google', utmMedium: 'organic', utmCampaign: 'lancamento', entrada: '/blog/rag/' });
  for (const canal of CANAIS) assert.equal(parseOrigem(form({ origem_canal: canal })).canal, canal);
  for (const fonte of FONTES) assert.equal(parseOrigem(form({ origem_canal: 'social', origem_fonte: fonte })).fonte, fonte);
  assert.deepEqual(Object.values(CAMPOS), ['origem_canal', 'origem_fonte', 'utm_source', 'utm_medium', 'utm_campaign', 'origem_entrada']);
});

test('O1: sem os campos (sem JavaScript) ou com canal fora da lista, o canal é "desconhecida" e o resto é nulo', () => {
  assert.deepEqual(parseOrigem(form({})), NADA);
  assert.deepEqual(parseOrigem(form({ origem_canal: 'BUSCA' })).canal, 'desconhecida');
  assert.deepEqual(parseOrigem(form({ origem_canal: 'https://google.com/' })).canal, 'desconhecida');
  // Direto e desconhecida não têm fonte, mesmo que o campo venha.
  assert.equal(parseOrigem(form({ origem_canal: 'direto', origem_fonte: 'google' })).fonte, null);
  assert.equal(parseOrigem(form({ origem_canal: 'busca', origem_fonte: 'altavista' })).fonte, null);
});

test('O2: UTM fora de [a-z0-9._-]{1,64} é recusado campo a campo', () => {
  for (const ruim of ['Google', 'a b', 'a+b', 'ação', 'a/b', 'https://x.com', 'a?b', 'a=b', 'a&b', 'a@b.com', 'x'.repeat(65), '', ' a',
    '%20', 'termo~buscado']) {
    for (const campo of ['utm_source', 'utm_medium', 'utm_campaign'] as const) {
      const o = parseOrigem(form({ ...BOA, [campo]: ruim }));
      const k = ({ utm_source: 'utmSource', utm_medium: 'utmMedium', utm_campaign: 'utmCampaign' } as const)[campo];
      assert.equal(o[k], null, `${campo}=${ruim}`);
      assert.equal(o.canal, 'busca', 'o resto fica');
      assert.equal(o.entrada, '/blog/rag/');
    }
  }
  assert.equal(parseOrigem(form({ ...BOA, utm_campaign: 'a'.repeat(64) })).utmCampaign, 'a'.repeat(64));
});

test('O2: a página de entrada só vale como caminho, sem query, fragmento, esquema ou caractere estranho', () => {
  for (const ruim of ['/blog/x/?q=termo', '/blog/x/#a', 'https://guifav.dev/blog/', 'blog/x/', '/a b', '/a%40b', '/a@b', '//evil.example', '/a//b',
    '/' + 'a'.repeat(200), '']) {
    assert.equal(parseOrigem(form({ ...BOA, origem_entrada: ruim })).entrada, null, ruim);
  }
  for (const bom of ['/', '/blog/', '/blog/x', '/seo-geo-aeo-framework/framework-seo-aeo.html', '/' + 'a'.repeat(199)]) {
    assert.equal(parseOrigem(form({ ...BOA, origem_entrada: bom })).entrada, bom);
  }
});

test('O3: a origem vai e volta no parâmetro do link só na forma canônica', () => {
  const o = parseOrigem(form(BOA));
  assert.equal(encodeOrigem(o), 'busca~google~google~organic~lancamento~/blog/rag/');
  assert.deepEqual(decodeOrigem(encodeOrigem(o)), o);
  assert.deepEqual(decodeOrigem(encodeOrigem(NADA)), NADA);
  for (const ruim of ['busca~google', 'xxx~~~~~', 'direto~google~~~~', 'busca~google~A~~~', '~~~~~', 'busca~google~~~~/a?b']) {
    assert.equal(decodeOrigem(ruim), null, ruim);
  }
});

test('O4: link de confirmação com a origem: ela volta na verificação e não muda sem invalidar o link', async () => {
  const o = parseOrigem(form(BOA));
  const q = await signLink('leitor@exemplo.com', NOW, SECRET, o);
  const p = new URLSearchParams(q);
  assert.deepEqual([...p.keys()], ['e', 'x', 'o', 's']);
  assert.deepEqual(await verifyConfirmLink(p, SECRET, NOW), { email: 'leitor@exemplo.com', origem: o });
  assert.equal(await verifyLink(p, SECRET, NOW), 'leitor@exemplo.com');
  const trocada = new URLSearchParams(q);
  trocada.set('o', 'pago~google~~~~/');
  assert.equal(await verifyConfirmLink(trocada, SECRET, NOW), null, 'origem trocada');
  const sem = new URLSearchParams(q);
  sem.delete('o');
  assert.equal(await verifyConfirmLink(sem, SECRET, NOW), null, 'origem tirada (vira payload v1 com outra assinatura)');
  const dupla = new URLSearchParams(q);
  dupla.append('o', p.get('o')!);
  assert.equal(await verifyConfirmLink(dupla, SECRET, NOW), null, 'origem repetida');
});

test('O4: link v1 (emitido antes da #114) continua valendo, sem origem', async () => {
  const p = new URLSearchParams(await signLink('leitor@exemplo.com', NOW, SECRET));
  assert.equal(p.has('o'), false);
  assert.deepEqual(await verifyConfirmLink(p, SECRET, NOW), { email: 'leitor@exemplo.com', origem: null });
});

test('O5: o CHECK do D1 recusa URL, query, termo buscado e rótulo fora da lista, mesmo que o Worker errasse', async () => {
  const db = d1(undefined, undefined);
  const base = { acao: 'material_request' as const, email: 'leitor@exemplo.com', material: 'planilha-custos-ia', interesses: [], nowMs: NOW,
    verified: false };
  const ruins: Partial<Origem>[] = [
    { canal: 'buscador' as never }, { fonte: 'altavista' as never }, { utmSource: 'Google' }, { utmMedium: 'a b' },
    { utmCampaign: 'https://google.com/search?q=termo' }, { entrada: '/blog/?q=termo' }, { entrada: 'https://guifav.dev/' },
    { entrada: '/a%20b' }, { entrada: '//evil.example' }, { utmSource: 'x'.repeat(65) }, { utmCampaign: '' },
  ];
  let n = 0;
  const erros = await capturaErros(async () => {
    for (const r of ruins) {
      await recordConversion(db, { ...base, id: `id-${n++}`, origem: { canal: 'busca', fonte: 'google', utmSource: null, utmMedium: null,
        utmCampaign: null, entrada: null, ...r } as Origem });
    }
  });
  assert.equal(rows(db.raw, 'conversions').length, 0);
  assert.equal(erros.length, ruins.length);
  assert.ok(erros.every((e) => e.startsWith('conversão não gravada (material_request): CHECK constraint failed')), erros.join('\n'));
  await recordConversion(db, { ...base, id: 'id-ok', origem: parseOrigem(form(BOA)) });
  assert.equal(rows(db.raw, 'conversions').length, 1);
});

test('P1: ponta a ponta, uma inscrição e um pedido de material gravam canal, fonte, UTM e entrada, e nada além disso', async () => {
  const { campos, linhas } = await pontaAPonta();
  // O track.js pôs os seis campos e nada mais: o gclid só classificou a visita como paga, e o termo ficou de fora.
  assert.deepEqual(campos.inscricao, { origem_canal: 'pago', origem_fonte: 'google', utm_source: '', utm_medium: '', utm_campaign: '',
    origem_entrada: '/blog/rag-em-producao/' });
  assert.deepEqual(campos.material, { origem_canal: 'assistente', origem_fonte: 'chatgpt', utm_source: 'chatgpt.com', utm_medium: '',
    utm_campaign: '', origem_entrada: '/mapa-decisao-ia/' });
  const origem = (l: Record<string, unknown>) => [l.action, l.origin_channel, l.origin_source, l.utm_source, l.utm_medium, l.utm_campaign,
    l.landing_path];
  assert.deepEqual(linhas.map(origem), [
    ['newsletter_confirm', 'pago', 'google', null, null, null, '/blog/rag-em-producao/'],
    ['material_request', 'assistente', 'chatgpt', 'chatgpt.com', null, null, '/mapa-decisao-ia/'],
    ['material_download', 'assistente', 'chatgpt', 'chatgpt.com', null, null, '/mapa-decisao-ia/'],
    ['material_download', 'assistente', 'chatgpt', 'chatgpt.com', null, null, '/mapa-decisao-ia/'],
    ['newsletter_confirm', 'assistente', 'chatgpt', 'chatgpt.com', null, null, '/mapa-decisao-ia/'],
  ]);
  // Só as colunas de antes e as seis da origem: nenhuma coluna nova além delas.
  assert.deepEqual(Object.keys(linhas[0]), ['id', 'email', 'action', 'material', 'interests', 'occurred_at', 'synced_at', 'verified',
    'ga_client_id', 'request_id', 'file', 'origin_channel', 'origin_source', 'utm_source', 'utm_medium', 'utm_campaign', 'landing_path']);
  const tudo = JSON.stringify(linhas);
  for (const p of PROIBIDOS) assert.ok(!tudo.includes(p), `o D1 não guarda ${p}`);
});

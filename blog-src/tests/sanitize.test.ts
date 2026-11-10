// Testes da limpeza do HTML do corpo dos posts (plugin do Satteri usado pelo Astro).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { markdownToHtml } from 'satteri';
import { sanitizeElement, safeUrl, parseSrcset, sanitizePlugin, MARKDOWN_FEATURES } from '../src/lib/sanitize.ts';

async function render(md: string): Promise<string> {
  const r = await markdownToHtml(md, {
    features: MARKDOWN_FEATURES,
    hastPlugins: [sanitizePlugin()],
    fileURL: new URL('file:///posts/um-post.md'),
  } as Parameters<typeof markdownToHtml>[1]);
  return r.html;
}

async function rejects(md: string, ...expected: string[]): Promise<void> {
  await assert.rejects(render(md), (e: Error) => {
    assert.ok(e.message.includes('um-post.md'), e.message);
    for (const x of expected) assert.ok(e.message.includes(x), `${x} ausente em: ${e.message}`);
    return true;
  }, md);
}

test('safeUrl: relativos, http(s) e mailto passam; esquemas ativos não', () => {
  for (const u of ['/blog/x/', 'img/a.webp', '#nota', '?q=1', 'https://a.b/c', 'http://a.b', 'mailto:a@b.c', '//cdn.x/y.webp']) {
    assert.equal(safeUrl(u), true, u);
  }
  for (const u of ['javascript:alert(1)', ' JavaScript:x', 'java\tscript:x', 'vbscript:x', 'data:text/html,x', 'data:image/png;base64,AA', 'file:///etc/passwd']) {
    assert.equal(safeUrl(u), false, u);
  }
});

test('parseSrcset segue a sintaxe do HTML: vírgula dentro da URL e qualquer espaço como separador', () => {
  assert.deepEqual(parseSrcset('/a.webp 728w, /b.webp 1456w'), ['/a.webp', '/b.webp']);
  assert.deepEqual(
    parseSrcset('https://substackcdn.com/image/fetch/w_728,c_limit,f_auto/https%3A%2F%2Fx.png 728w, https://substackcdn.com/image/fetch/w_1456,c_limit/y.png 1456w'),
    ['https://substackcdn.com/image/fetch/w_728,c_limit,f_auto/https%3A%2F%2Fx.png', 'https://substackcdn.com/image/fetch/w_1456,c_limit/y.png'],
  );
  assert.deepEqual(parseSrcset('/a.webp\t728w,\n/b.webp 2x'), ['/a.webp', '/b.webp']);
  assert.deepEqual(parseSrcset('/a.webp'), ['/a.webp']);
  // Pela especificação, a URL vai até o espaço: vírgula colada faz parte dela.
  assert.deepEqual(parseSrcset('/a.webp,/b.webp 2x'), ['/a.webp,/b.webp']);
  assert.deepEqual(parseSrcset('/a.webp, /b.webp 2x'), ['/a.webp', '/b.webp']);
  assert.deepEqual(parseSrcset('/a.webp 1x,/b.webp 2x'), ['/a.webp', '/b.webp']);
});

test('sanitizeElement: elemento fora da lista é rejeitado (o build falha), nunca removido ou desembrulhado', () => {
  for (const t of ['script', 'style', 'object', 'embed', 'form', 'input', 'base', 'meta', 'link', 'svg', 'math', 'template',
    'noscript', 'frame', 'xmp', 'plaintext', 'listing', 'noembed', 'noframes', 'textarea', 'title', 'article', 'marquee', 'center',
    'font', 'my-widget', 'audio']) {
    assert.equal(sanitizeElement(t, {}).action, 'reject', t);
  }
  for (const t of ['figure', 'picture', 'video', 'source', 'iframe', 'div', 'span']) {
    assert.notEqual(sanitizeElement(t, t === 'iframe' ? { src: 'https://www.youtube.com/embed/x' } : {}).action, 'reject', t);
  }
});

test('sanitizeElement: remove on*, srcdoc, style fora do realce e atributos fora da lista', () => {
  const r = sanitizeElement('img', { src: '/a.webp', srcSet: '/a.webp 1x', alt: 'x', onError: 'x()', style: 'color:red', dataX: '1' });
  assert.equal(r.action, 'keep');
  assert.deepEqual([...r.remove].sort(), ['dataX', 'onError', 'style']);
  const f = sanitizeElement('iframe', { src: 'https://www.youtube.com/embed/x', srcDoc: '<script>1</script>', allowFullScreen: true });
  assert.equal(f.action, 'keep');
  assert.ok(f.remove.includes('srcDoc'));
  assert.deepEqual(sanitizeElement('span', { style: '--shiki-light:#24292E;--shiki-dark:#E1E4E8' }).remove, []);
  assert.deepEqual(sanitizeElement('span', { style: '--shiki-light:url(javascript:x)' }).remove, ['style']);
  // Saída real do realce no build do Astro: chave "class" (não className) e overflow-x no pre.
  assert.deepEqual(sanitizeElement('span', { class: 'line' }).remove, []);
  assert.deepEqual(
    sanitizeElement('pre', {
      class: 'astro-code astro-code-themes github-light github-dark',
      style: '--shiki-light:#24292e;--shiki-dark:#e1e4e8;--shiki-light-bg:#fff;--shiki-dark-bg:#24292e; overflow-x: auto;',
      tabindex: '0',
      'data-language': 'python',
    }).remove,
    [],
  );
  assert.deepEqual(sanitizeElement('pre', { style: 'overflow-x: auto; position: fixed' }).remove, ['style']);
  assert.deepEqual(sanitizeElement('p', { style: 'overflow-x: auto' }).remove, ['style']);
  // Nomes exatos: só os aliases do HAST e do realce; hífen não é ignorado.
  assert.deepEqual(sanitizeElement('a', { 'h-ref': '/x', 'tar-get': '_blank', 'cl-ass': 'y', href: '/blog/' }).remove.sort(), ['cl-ass', 'h-ref', 'tar-get']);
  assert.deepEqual(sanitizeElement('code', { className: ['c'], dataLanguage: 'py' }).remove, []);
  assert.deepEqual(sanitizeElement('code', { 'data-lang-uage': 'py' }).remove, ['data-lang-uage']);
});

test('sanitizeElement: iframe só de host permitido e em https; URL ativa derruba img e iframe', () => {
  for (const src of [
    'https://open.spotify.com/embed/episode/abc',
    'https://creators.spotify.com/pod/profile/x/embed/episodes/y',
    'https://podcasters.spotify.com/pod/show/x/embed/episodes/y',
    'https://www.youtube.com/embed/x',
    'https://www.youtube-nocookie.com/embed/x',
    'https://player.vimeo.com/video/1',
  ]) {
    assert.equal(sanitizeElement('iframe', { src }).action, 'keep', src);
  }
  for (const src of ['http://www.youtube.com/embed/x', 'https://evil.example/x', 'https://youtube.com.evil.example/x', '']) {
    assert.equal(sanitizeElement('iframe', { src }).action, 'reject', src);
  }
  assert.equal(sanitizeElement('iframe', {}).action, 'reject');
  assert.equal(sanitizeElement('img', { src: 'javascript:x' }).action, 'reject');
  assert.equal(sanitizeElement('source', { src: 'javascript:x' }).action, 'reject');
  assert.equal(sanitizeElement('video', { poster: 'javascript:x' }).action, 'reject');
  // srcset com URL ativa em img ou source falha o build, como src (nada some em silêncio).
  assert.equal(sanitizeElement('img', { src: '/a.webp', srcSet: '/a.webp 1x, javascript:x 2x' }).action, 'reject');
  assert.equal(sanitizeElement('source', { srcSet: 'data:image/svg+xml,x 1x', type: 'image/svg+xml' }).action, 'reject');
  // video com src: o vídeo local passa; com esquema ativo, falha o build.
  assert.deepEqual(sanitizeElement('video', { src: '/blog/video/v.mp4', controls: true }), { action: 'keep', remove: [], set: {} });
  assert.equal(sanitizeElement('video', { src: 'javascript:x' }).action, 'reject');
  // URL de CDN com vírgula e parâmetro "chave:valor" é uma URL https só (regra do HTML): o srcset fica.
  assert.deepEqual(sanitizeElement('img', { src: '/a.webp', srcSet: 'https://cdn.example/fetch/w_728,dpr:2/x.png 728w, /a.webp 1456w' }), { action: 'keep', remove: [], set: {} });
  assert.deepEqual(sanitizeElement('a', { href: 'javascript:x' }).remove, ['href']);
});

test('sanitizeElement: link com target ganha rel noopener', () => {
  const r = sanitizeElement('a', { href: 'https://x.y', target: '_blank' });
  assert.equal(r.set.rel, 'noopener');
});

test('render: elemento fora da lista falha com o arquivo e a tag, inclusive aninhado ou desconhecido', async () => {
  await rejects('<script>window.a=1</script>\n', '<script>');
  await rejects('<article><script>window.a=1</script></article>\n', '<article>', '<script>');
  await rejects('<center><iframe src="https://evil.example/"></iframe></center>\n', '<center>', '<iframe>');
  await rejects('<marquee><img src="x" onerror="b()"><p>t</p></marquee>\n', '<marquee>');
  await rejects('<xmp>&lt;/xmp&gt;&lt;script&gt;c()&lt;/script&gt;</xmp>\n', '<xmp>');
  await rejects('<p>a</p>\n\n<svg><script>window.d=1</script></svg>\n', '<svg>');
  await rejects('<form action="/x"><input></form>\n', '<form>');
  await rejects('<style>body{display:none}</style>\n', '<style>');
  await rejects('<img src="javascript:alert(1)">\n', '<img>');
  await rejects('<iframe src="https://evil.example/x"></iframe>\n', '<iframe>');
});

test('render: atributos ativos saem sem perder conteúdo (on*, srcdoc, javascript:, </article> solto)', async () => {
  const html = await render(
    'Texto\n\n</article>\n\n' +
      '<img src="/blog/img/x/a.webp" onerror="window.b=1">\n\n' +
      '<iframe src="https://www.youtube.com/embed/x" srcdoc="&lt;script&gt;window.c=1&lt;/script&gt;"></iframe>\n\n' +
      '<a href="javascript:alert(1)">x</a> <a href="JaVaScRiPt:alert(1)">y</a> <div data-x="1" style="color:red">z</div>\n',
  );
  for (const bad of ['onerror', 'srcdoc', '</article>', 'javascript:', 'JaVaScRiPt', 'window.', 'data-x', 'style=']) {
    assert.equal(html.toLowerCase().includes(bad.toLowerCase()), false, `${bad} em: ${html}`);
  }
  assert.ok(html.includes('<img src="/blog/img/x/a.webp">'), html);
  assert.ok(html.includes('<iframe src="https://www.youtube.com/embed/x"></iframe>'), html);
  assert.ok(html.includes('<a>x</a>') && html.includes('<div>z</div>'), html);
});

test('plugin: nó raw (HTML não interpretado) também falha o build', () => {
  const plugin = sanitizePlugin();
  const ctx = { data: {} as Record<string, unknown>, fileURL: new URL('file:///posts/um-post.md'), removeNode() {}, setProperty() {} };
  plugin.before({}, ctx);
  plugin.raw({ type: 'raw', value: '<script>x()</script>' }, ctx);
  assert.throws(() => plugin.after({}, ctx), /um-post\.md.*HTML cru não interpretado/);
  plugin.before({}, ctx);
  assert.doesNotThrow(() => plugin.after({}, ctx));
});

test('render: video com src local passa; srcset ativo falha o build', async () => {
  const html = await render('<video src="/blog/video/review.mp4" controls preload="none"></video>\n');
  assert.ok(html.includes('<video src="/blog/video/review.mp4" controls preload="none"></video>'), html);
  await rejects('<img src="/blog/img/x/a.webp" srcset="javascript:alert(1) 1x, /blog/img/x/b.webp 2x" alt="x">\n', '<img>');
  await rejects('<picture><source srcset="data:image/svg+xml,x 1x" type="image/svg+xml"><img src="/blog/img/x/a.webp" alt="x"></picture>\n', '<source>');
});

test('render: vídeo local da #19 passa com os atributos permitidos', async () => {
  const html = await render(
    '<p>antes</p>\n<video controls preload="none" width="1502" height="1080" poster="/blog/img/x/p.webp" onplay="x()">\n' +
      '  <source src="/blog/video/review.mp4" type="video/mp4">\n</video>\n<p>depois</p>\n',
  );
  for (const ok of ['<video', 'controls', 'preload="none"', 'width="1502"', 'poster="/blog/img/x/p.webp"', '<source src="/blog/video/review.mp4" type="video/mp4">', 'depois']) {
    assert.ok(html.includes(ok), `${ok} sumiu de: ${html}`);
  }
  assert.equal(html.includes('onplay'), false, html);
});

test('render: mantém o HTML legítimo dos posts do Substack', async () => {
  const html = await render(
    '<figure><picture><source type="image/webp" srcset="/blog/img/x/a-728.webp 728w, /blog/img/x/a-1456.webp 1456w" sizes="100vw">' +
      '<img src="/blog/img/x/a-1456.webp" alt="Diagrama" width="1456" height="800" loading="lazy"></picture>' +
      '<figcaption>Legenda <a href="https://example.com" target="_blank">fonte</a></figcaption></figure>\n\n' +
      '<table><thead><tr><th colspan="2">A</th></tr></thead><tbody><tr><td>1</td><td>2</td></tr></tbody></table>\n\n' +
      '<blockquote><p>Citação</p></blockquote>\n\n<details><summary>Mais</summary><p>x</p></details>\n\n' +
      '<iframe src="https://www.youtube-nocookie.com/embed/abc" width="560" height="315" allowfullscreen title="Vídeo"></iframe>\n',
  );
  for (const ok of [
    '<picture>', 'srcset="/blog/img/x/a-728.webp 728w, /blog/img/x/a-1456.webp 1456w"', 'sizes="100vw"', 'alt="Diagrama"',
    'width="1456"', 'loading="lazy"', '<figcaption>', 'rel="noopener"', 'colspan="2"', '<blockquote>', '<details>',
    'allowfullscreen', 'title="Vídeo"',
  ]) {
    assert.ok(html.includes(ok), `${ok} sumiu de: ${html}`);
  }
});

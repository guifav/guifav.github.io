// Testes da limpeza do HTML do corpo dos posts (plugin do Satteri usado pelo Astro).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { markdownToHtml } from 'satteri';
import { sanitizeElement, safeUrl, parseSrcset, sanitizePlugin, MARKDOWN_FEATURES } from '../src/lib/sanitize.ts';

async function render(md: string): Promise<string> {
  const r = await markdownToHtml(md, { features: MARKDOWN_FEATURES, hastPlugins: [sanitizePlugin()] });
  return r.html;
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

test('sanitizeElement: tags perigosas saem com o conteúdo; desconhecidas viram só o conteúdo', () => {
  for (const t of ['script', 'style', 'object', 'embed', 'form', 'input', 'base', 'meta', 'link', 'svg', 'math', 'template', 'noscript', 'frame']) {
    assert.equal(sanitizeElement(t, {}).action, 'drop', t);
  }
  assert.equal(sanitizeElement('marquee', {}).action, 'unwrap');
  assert.equal(sanitizeElement('figure', {}).action, 'keep');
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
    assert.equal(sanitizeElement('iframe', { src }).action, 'drop', src);
  }
  assert.equal(sanitizeElement('iframe', {}).action, 'drop');
  assert.equal(sanitizeElement('img', { src: 'javascript:x' }).action, 'drop');
  assert.equal(sanitizeElement('img', { src: '/a.webp', srcSet: '/a.webp 1x, javascript:x 2x' }).remove.includes('srcSet'), true);
  // URL de CDN com vírgula e parâmetro "chave:valor" é uma URL https só (regra do HTML): o srcset fica.
  assert.deepEqual(sanitizeElement('img', { src: '/a.webp', srcSet: 'https://cdn.example/fetch/w_728,dpr:2/x.png 728w, /a.webp 1456w' }).remove, []);
  assert.deepEqual(sanitizeElement('a', { href: 'javascript:x' }).remove, ['href']);
});

test('sanitizeElement: link com target ganha rel noopener', () => {
  const r = sanitizeElement('a', { href: 'https://x.y', target: '_blank' });
  assert.equal(r.set.rel, 'noopener');
});

test('render: barra script, handlers, srcdoc, </article> solto e iframe de fora', async () => {
  const html = await render(
    'Texto\n\n</article>\n\n<script>window.a=1</script>\n\n' +
      '<img src="/blog/img/x/a.webp" onerror="window.b=1">\n\n' +
      '<iframe src="https://www.youtube.com/embed/x" srcdoc="&lt;script&gt;window.c=1&lt;/script&gt;"></iframe>\n\n' +
      '<iframe src="https://evil.example/x"></iframe>\n\n' +
      '<a href="javascript:alert(1)">x</a> <a href="JaVaScRiPt:alert(1)">y</a>\n\n' +
      '<svg><script>window.d=1</script></svg><object data="x.swf"></object><form action="/x"><input></form>\n\n' +
      '<style>body{display:none}</style>\n',
  );
  for (const bad of ['<script', 'onerror', 'srcdoc', '</article>', 'evil.example', 'javascript:', 'JaVaScRiPt', '<svg', '<object', '<form', '<input', '<style', 'window.']) {
    assert.equal(html.toLowerCase().includes(bad.toLowerCase()), false, `${bad} em: ${html}`);
  }
  assert.ok(html.includes('<img src="/blog/img/x/a.webp">'), html);
  assert.ok(html.includes('<iframe src="https://www.youtube.com/embed/x"></iframe>'), html);
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

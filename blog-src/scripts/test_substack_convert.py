"""Testes do conversor de HTML do Substack para Markdown (issue #19).

Rodar em blog-src/: python3 scripts/test_substack_convert.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import substack_convert as C  # noqa: E402

SLUGS = {"post-existente", "outro-post", "o-que-e-ia"}


def conv(html, slug="um-post", images=None, **kw):
    ctx = C.Context(slug=slug, slugs=SLUGS, images=images or {}, **kw)
    return C.convert(html, ctx), ctx


class TextTest(unittest.TestCase):
    def test_paragraphs_and_headings(self):
        md, _ = conv("<h2>Título</h2><p>Um parágrafo.</p><h3>Sub</h3><p>Outro.</p>")
        self.assertEqual(md, "## Título\n\nUm parágrafo.\n\n### Sub\n\nOutro.\n")

    def test_h1_in_body_becomes_h2(self):
        md, ctx = conv('<h1 style="text-align: justify;">GPT-2</h1><p style="text-align: justify;">x</p>')
        self.assertEqual(md, "## GPT-2\n\nx\n")
        self.assertEqual(ctx.stats["h1_para_h2"], 1)

    def test_markdown_syntax_in_text_is_escaped(self):
        md, _ = conv("<p>a*b_c [d] `e` &lt;tag&gt; x~y 5 | 6 \\ fim &amp;copy;</p>")
        self.assertEqual(md, "a\\*b\\_c \\[d\\] \\`e\\` \\<tag\\> x\\~y 5 \\| 6 \\\\ fim \\&copy;\n")

    def test_line_start_markers_are_escaped(self):
        md, _ = conv("<p>1990. Ano</p><p># não é título</p><p>- não é lista</p><p>+ nem</p><p>&gt; nem citação</p><p>2) item</p>")
        self.assertEqual(md, "1990\\. Ano\n\n\\# não é título\n\n\\- não é lista\n\n\\+ nem\n\n\\> nem citação\n\n2\\) item\n")

    def test_bare_domains_do_not_autolink(self):
        # O texto corrido com "www." ou "https://" não pode virar link (G3).
        # O Satteri detecta o autolink depois de resolver escapes e entidades; só um comentário HTML
        # vazio quebra a sequência sem mudar o texto visível.
        md, _ = conv("<p>acesse: www.guilhermefavaron.com.br ou https://exemplo.com/x e fim</p>")
        self.assertEqual(md, "acesse: www<!---->.guilhermefavaron.com.br ou https<!---->://exemplo.com/x e fim\n")

    def test_whitespace_is_collapsed_and_nbsp_kept(self):
        md, _ = conv("<p>  um\n   dois\u00a0três  </p>")
        self.assertEqual(md, "um dois\u00a0três\n")

    def test_br(self):
        md, _ = conv("<p>linha um<br>linha dois</p>")
        self.assertEqual(md, "linha um<br>linha dois\n")

    def test_hr(self):
        # "***" e não "---": no começo do corpo, "---" logo depois do frontmatter fazia a figura seguinte sumir.
        md, _ = conv("<div><hr></div><p>a</p><div><hr></div><p>b</p>")
        self.assertEqual(md, "***\n\na\n\n***\n\nb\n")


class InlineTest(unittest.TestCase):
    def test_strong_em_code(self):
        md, _ = conv("<p><strong>forte</strong> e <em>ênfase</em> e <code>x = 1</code></p>")
        self.assertEqual(md, "**forte** e *ênfase* e `x = 1`\n")

    def test_whitespace_moves_outside_delimiters(self):
        md, _ = conv("<p>a<strong> forte </strong>b</p>")
        self.assertEqual(md, "a **forte** b\n")

    def test_strong_that_would_not_parse_falls_back_to_html(self):
        # "**Nota:**texto": o ** de fechamento vem depois de pontuação e antes de letra, e não fecha.
        md, _ = conv("<p><strong>Nota:</strong>texto</p>")
        self.assertEqual(md, "<strong>Nota:</strong>texto\n")

    def test_empty_strong_is_dropped(self):
        md, _ = conv("<p>a<strong> </strong>b</p>")
        self.assertEqual(md, "a b\n")

    def test_code_with_backticks(self):
        md, _ = conv("<p><code>a`b</code></p>")
        self.assertEqual(md, "``a`b``\n")

    def test_link(self):
        md, _ = conv('<p><a href="https://arxiv.org/abs/1">artigo [1]</a></p>')
        self.assertEqual(md, "[artigo \\[1\\]](https://arxiv.org/abs/1)\n")

    def test_link_with_parens_or_spaces(self):
        md, _ = conv('<p><a href="https://pt.wikipedia.org/wiki/A_(b)">w</a></p>')
        self.assertEqual(md, "[w](<https://pt.wikipedia.org/wiki/A_(b)>)\n")


class LinkRulesTest(unittest.TestCase):
    def t(self, href, expected, rule=None):
        md, ctx = conv(f'<p><a href="{href}">x</a></p>')
        self.assertEqual(md, f"[x]({expected})\n" if expected else "x\n", href)
        if rule:
            self.assertEqual(ctx.stats[rule], 1, href)

    def test_r1_p_slug(self):
        self.t("https://www.guilhermefavaron.com.br/p/post-existente", "/blog/post-existente/", "R1")
        self.t("https://guilhermefavaron.com.br/p/outro-post?utm_source=x#sec", "/blog/outro-post/", "R1")
        self.t("https://iaaplicada.substack.com/p/post-existente", "/blog/post-existente/", "R1")

    def test_r2_post_wix_path(self):
        self.t("https://www.guilhermefavaron.com.br/post/post-existente", "/blog/post-existente/", "R2")

    def test_r3_unknown_slug_kept_and_reported(self):
        md, ctx = conv('<p><a href="https://www.guilhermefavaron.com.br/post/nao-existe">x</a></p>')
        self.assertEqual(md, "[x](https://www.guilhermefavaron.com.br/post/nao-existe)\n")
        self.assertEqual(ctx.unresolved, ["https://www.guilhermefavaron.com.br/post/nao-existe"])

    def test_r3_similar_slug_when_unambiguous(self):
        md, ctx = conv('<p><a href="https://www.guilhermefavaron.com.br/post/o-que-e-ia-2">x</a></p>',
                       aliases={"o-que-e-ia-2": "o-que-e-ia"})
        self.assertEqual(md, "[x](/blog/o-que-e-ia/)\n")
        self.assertEqual(ctx.stats["R3"], 1)

    def test_r4_root(self):
        for h in ("http://www.guilhermefavaron.com.br", "https://guilhermefavaron.com.br/", "https://www.guilhermefavaron.com.br/?x=1"):
            self.t(h, "/blog/")

    def test_other_site_paths_are_reported(self):
        md, ctx = conv('<p><a href="https://www.guilhermefavaron.com.br/pagina-inicial/hashtags/FF0000">x</a></p>')
        self.assertEqual(ctx.unresolved, ["https://www.guilhermefavaron.com.br/pagina-inicial/hashtags/FF0000"])

    def test_r5_dead_fragment_is_unwrapped(self):
        md, ctx = conv('<p><a href="#zznim366494">Sumário</a> e <a href="#">vazio</a></p>')
        self.assertEqual(md, "Sumário e vazio\n")
        self.assertEqual(ctx.stats["R5"], 2)

    def test_live_fragment_fails(self):
        # O Markdown não preserva o id do título: uma âncora viva quebraria em silêncio.
        with self.assertRaises(C.ConvertError):
            conv('<h2 id="sec">S</h2><p><a href="#sec">ir</a></p>')

    def test_r6_manual_replacement(self):
        md, ctx = conv('<p><a href="https://download-files.wixmp.com/raw/x.pdf?token=1">pdf</a></p>',
                       replacements={"https://download-files.wixmp.com/raw/x.pdf?token=1": "https://arxiv.org/abs/1706.03762"})
        self.assertEqual(md, "[pdf](https://arxiv.org/abs/1706.03762)\n")
        self.assertEqual(ctx.stats["R6"], 1)

    def test_other_substack_links_kept_and_reported(self):
        # Critério da #19: nenhum href para substack.com fora do relatório.
        md, ctx = conv('<p><a href="https://garymarcus.substack.com/p/x">g</a></p>')
        self.assertEqual(md, "[g](https://garymarcus.substack.com/p/x)\n")
        self.assertEqual(ctx.unresolved, ["https://garymarcus.substack.com/p/x"])

    def test_external_and_mailto_unchanged(self):
        self.t("https://garymarcus.substack.com/p/x", "https://garymarcus.substack.com/p/x")
        self.t("mailto:a@b.org", "mailto:a@b.org")


class BlockTest(unittest.TestCase):
    def test_lists_with_paragraphs_and_nesting(self):
        md, _ = conv("<ul><li><p>um</p></li><li><p>dois</p><ol start=\"3\"><li><p>três</p></li><li><p>quatro</p></li></ol></li></ul>")
        self.assertEqual(md, "- um\n\n- dois\n\n  3. três\n  4. quatro\n")

    def test_tight_list(self):
        md, _ = conv("<ul><li><p>um</p></li><li>dois <strong>forte</strong></li></ul>")
        self.assertEqual(md, "- um\n- dois **forte**\n")

    def test_heading_ending_with_hash(self):
        md, _ = conv("<h2>Linguagem C #</h2>")
        self.assertEqual(md, "## Linguagem C \\#\n")

    def test_email_in_text_does_not_autolink(self):
        md, _ = conv("<p>escreva para a@b.org</p>")
        self.assertEqual(md, "escreva para a<!---->\\@b.org\n")

    def test_list_item_with_two_paragraphs(self):
        md, _ = conv("<ol><li><p>a</p><p>b</p></li></ol>")
        self.assertEqual(md, "1. a\n\n   b\n")

    def test_blockquote(self):
        md, _ = conv("<blockquote><p>a</p><p>b</p></blockquote>")
        self.assertEqual(md, "> a\n>\n> b\n")

    def test_pre_code_fence_with_language(self):
        html = ('<div class="highlighted_code_block" data-attrs="{&quot;language&quot;:&quot;python&quot;,&quot;nodeId&quot;:null}" '
                'data-component-name="HighlightedCodeBlockToDOM"><pre class="shiki"><code class="language-python">'
                '<span>x = 1\n</span><span>\nprint(x)</span></code></pre></div>')
        md, _ = conv(html)
        self.assertEqual(md, "```python\nx = 1\n\nprint(x)\n```\n")

    def test_pre_with_link_keeps_link_as_html(self):
        html = '<pre><code>\nO equivalente:\n\n <a href="https://bing.com/webmasters/">bing.com/webmasters/</a> &lt;x&gt;\n</code></pre>'
        md, _ = conv(html)
        self.assertEqual(md, '<pre><code>O equivalente:\n\n <a href="https://bing.com/webmasters/">bing.com/webmasters/</a> &lt;x&gt;</code></pre>\n')

    def test_pre_with_strong_keeps_formatting_as_html(self):
        md, _ = conv("<pre><code>GET /x\n<strong>200 OK</strong> &amp; fim</code></pre>")
        self.assertEqual(md, "<pre><code>GET /x\n<strong>200 OK</strong> &amp; fim</code></pre>\n")

    def test_nested_code_in_pre(self):
        md, _ = conv("<pre><code><code>x = 1</code></code></pre>")
        self.assertEqual(md, "```\nx = 1\n```\n")

    def test_pre_with_unknown_element_fails(self):
        with self.assertRaises(C.ConvertError):
            conv("<pre><code>a <img src=x> b</code></pre>")

    def test_plain_pre_and_fence_longer_than_content(self):
        md, _ = conv("<pre><code>a ``` b</code></pre>")
        self.assertEqual(md, "````\na ``` b\n````\n")

    def test_pullquote_and_callout(self):
        md, _ = conv('<div class="pullquote"><p>Frase <em>forte</em>.</p></div><div class="callout-block" data-callout="true"><p>Nota.</p></div>')
        self.assertEqual(md, '<aside class="pullquote">\n<p>Frase <em>forte</em>.</p>\n</aside>\n\n'
                             '<aside class="callout">\n<p>Nota.</p>\n</aside>\n')

    def test_subscription_widget_and_subscribe_buttons_removed(self):
        html = ('<div class="subscription-widget-wrap-editor" data-component-name="SubscribeWidgetToDOM"><div class="subscription-widget show-subscribe">'
                '<div class="preamble"><p class="cta-caption">Assine</p></div><form class="subscription-widget-subscribe">'
                '<input type="email" class="email-input" name="email"><input type="submit" class="button primary" value="Subscribe"></form></div></div>'
                '<p class="button-wrapper" data-component-name="ButtonCreateButton"><a class="button primary" href="https://iaaplicada.substack.com/subscribe?"><span>Assine já</span></a></p>'
                '<p>fim</p>')
        md, ctx = conv(html)
        self.assertEqual(md, "fim\n")
        self.assertEqual(ctx.stats["removidos"], 2)

    def test_content_button_becomes_link_paragraph(self):
        html = ('<p class="button-wrapper" data-component-name="ButtonCreateButton"><a class="button primary" '
                'href="https://github.com/guifav/pipeline_machine_learning"><span>Link Para Códigos</span></a></p>')
        md, _ = conv(html)
        self.assertEqual(md, "[Link Para Códigos](https://github.com/guifav/pipeline_machine_learning)\n")

    def test_youtube_embed(self):
        html = ('<div id="youtube2-abc" class="youtube-wrap" data-attrs="{&quot;videoId&quot;:&quot;abcDEF12345&quot;}" data-component-name="Youtube2ToDOM">'
                '<div class="youtube-inner"><iframe src="https://www.youtube-nocookie.com/embed/abcDEF12345?rel=0&amp;autoplay=0" frameborder="0" '
                'loading="lazy" gesture="media" allow="autoplay; fullscreen" allowautoplay="true" allowfullscreen="true" width="728" height="409">'
                '</iframe></div></div>')
        md, _ = conv(html)
        self.assertEqual(md, '<div class="video"><iframe src="https://www.youtube-nocookie.com/embed/abcDEF12345?rel=0" title="Vídeo do YouTube" '
                             'loading="lazy" allow="fullscreen; picture-in-picture" allowfullscreen referrerpolicy="strict-origin-when-cross-origin">'
                             '</iframe></div>\n')

    def test_spotify_iframe(self):
        html = '<iframe class="spotify-wrap podcast" data-attrs="{}" src="https://open.spotify.com/embed/episode/4PujOwxaXDeXtoyWtRHdsF" frameborder="0" loading="lazy"></iframe>'
        md, _ = conv(html)
        self.assertEqual(md, '<div class="podcast-embed"><iframe src="https://open.spotify.com/embed/episode/4PujOwxaXDeXtoyWtRHdsF" '
                             'title="Episódio no Spotify" height="102" loading="lazy" allow="encrypted-media" '
                             'referrerpolicy="strict-origin-when-cross-origin"></iframe></div>\n')

    def test_digest_post_embed_by_post_id(self):
        # O canonical_url do embed guarda o slug da época, que pode ter mudado; o id do post não muda.
        html = ('<div class="digest-post-embed" data-attrs="{&quot;title&quot;:&quot;Livro: Desbloqueando&quot;,&quot;id&quot;:179961940,'
                '&quot;canonical_url&quot;:&quot;https://www.guilhermefavaron.com.br/p/slug-antigo&quot;}"></div>')
        md, ctx = conv(html, post_ids={"179961940": "o-que-e-ia"})
        self.assertEqual(md, "[Livro: Desbloqueando](/blog/o-que-e-ia/)\n")
        self.assertEqual(ctx.stats["R1"], 1)

    def test_digest_post_embed_unknown_id_fails(self):
        html = '<div class="digest-post-embed" data-attrs="{&quot;title&quot;:&quot;T&quot;,&quot;id&quot;:1}"></div>'
        with self.assertRaises(C.ConvertError):
            conv(html, post_ids={})

    def test_link_with_only_nbsp_is_kept(self):
        md, _ = conv('<p>x <a href="http://rows.com/">\u00a0</a> y</p>')
        self.assertEqual(md, "x [\u00a0](http://rows.com/) y\n")

    def test_native_video(self):
        html = '<div class="native-video-embed" data-component-name="VideoPlaceholder" data-attrs="{&quot;mediaUploadId&quot;:&quot;02c5&quot;}"></div>'
        md, _ = conv(html, video={"src": "/blog/video/um-post.mp4", "poster": "/blog/img/um-post/video-poster.webp", "width": 1502, "height": 1080})
        self.assertEqual(md, '<video src="/blog/video/um-post.mp4" poster="/blog/img/um-post/video-poster.webp" width="1502" height="1080" '
                             'controls preload="none" playsinline></video>\n')

    def test_native_video_without_file_fails(self):
        html = '<div class="native-video-embed" data-component-name="VideoPlaceholder" data-attrs="{}"></div>'
        with self.assertRaises(C.ConvertError):
            conv(html)

    def test_unknown_component_fails(self):
        with self.assertRaises(C.ConvertError):
            conv('<div class="poll-embed" data-component-name="PollToDOM"><p>x</p></div>')

    def test_unknown_tag_fails(self):
        with self.assertRaises(C.ConvertError):
            conv("<p><marquee>x</marquee></p>")


IMG_ATTRS = ("{&quot;src&quot;:&quot;https://substack-post-media.s3.amazonaws.com/public/images/8b00b728-157f_1200x814.jpeg&quot;,"
             "&quot;width&quot;:1200,&quot;height&quot;:814,&quot;alt&quot;:&quot;Diagrama&quot;,&quot;href&quot;:%s}")


def figure(href="null", caption=True, link_href="https://substackcdn.com/image/fetch/x"):
    cap = '<figcaption class="image-caption">Fonte: <a href="https://arxiv.org/abs/2">paper</a></figcaption>' if caption else ""
    return ('<div class="captioned-image-container"><figure>'
            f'<a class="image-link image2 is-viewable-img" target="_blank" href="{link_href}" data-component-name="Image2ToDOM">'
            '<div class="image2-inset"><picture><source type="image/webp" srcset="https://substackcdn.com/x 424w">'
            f'<img src="https://substackcdn.com/x" data-attrs="{IMG_ATTRS % href}" class="sizing-normal" alt="Diagrama" width="1200" height="814" loading="lazy"></picture>'
            '<div class="image-link-expand"><div class="pencraft"><button class="restack-image"><svg><path d="M1"></path></svg></button></div></div>'
            f'</div></a>{cap}</figure></div>')


IMAGES = {"https://substack-post-media.s3.amazonaws.com/public/images/8b00b728-157f_1200x814.jpeg":
          C.Image(files=[(728, "img/um-post/8b00b728-728.webp"), (1200, "img/um-post/8b00b728-1200.webp")], width=1200, height=814)}


class ImageTest(unittest.TestCase):
    def test_captioned_image(self):
        md, ctx = conv(figure(), images=IMAGES)
        self.assertEqual(md, '<figure>\n<img src="/blog/img/um-post/8b00b728-1200.webp" srcset="/blog/img/um-post/8b00b728-728.webp 728w, '
                             '/blog/img/um-post/8b00b728-1200.webp 1200w" sizes="(max-width: 760px) 100vw, 720px" width="1200" height="814" '
                             'alt="Diagrama" loading="lazy" decoding="async">\n'
                             '<figcaption>Fonte: <a href="https://arxiv.org/abs/2">paper</a></figcaption>\n</figure>\n')
        self.assertEqual(ctx.stats["R7"], 1)
        self.assertEqual(ctx.stats["imagens"], 1)

    def test_image_linked_to_youtube_keeps_link(self):
        md, ctx = conv(figure(href="&quot;https://youtu.be/abc&quot;", caption=False, link_href="https://youtu.be/abc"), images=IMAGES)
        self.assertTrue(md.startswith('<figure>\n<a href="https://youtu.be/abc"><img src="/blog/img/um-post/8b00b728-1200.webp"'), md)
        self.assertTrue(md.endswith('decoding="async"></a>\n</figure>\n'), md)
        self.assertEqual(ctx.stats["R7"], 0)

    def test_missing_image_file_fails(self):
        with self.assertRaises(C.ConvertError):
            conv(figure(), images={})

    def test_broken_original_drops_image_keeps_caption(self):
        html = figure().replace(IMG_ATTRS % "null", "{&quot;src&quot;:&quot;undefined&quot;}").replace('src="https://substackcdn.com/x" data', 'src="undefined" data')
        md, ctx = conv(html, images=IMAGES)
        self.assertEqual(md, '<figure>\n<figcaption>Fonte: <a href="https://arxiv.org/abs/2">paper</a></figcaption>\n</figure>\n')
        self.assertEqual(ctx.stats["imagens_quebradas_no_original"], 1)

    def test_alt_and_caption_are_escaped_in_html(self):
        imgs = {k: v for k, v in IMAGES.items()}
        html = figure(caption=False).replace("&quot;alt&quot;:&quot;Diagrama&quot;", "&quot;alt&quot;:&quot;a \\&quot;b\\&quot; &lt;c&gt;&quot;")
        md, _ = conv(html, images=imgs)
        self.assertIn('alt="a &quot;b&quot; &lt;c&gt;"', md)


class FrontmatterTest(unittest.TestCase):
    def test_frontmatter(self):
        fm = C.frontmatter({"title": 'Título "com aspas"', "subtitle": None, "description": "Desc",
                            "post_date": "2024-11-06T14:28:22.000Z", "updated_at": "2026-03-10T03:09:28.966Z",
                            "slug": "um-post", "type": "podcast"},
                           cover="img/um-post/capa.jpg", spotify_url="https://open.spotify.com/episode/2ETPXIcx6RwKqdEj17z0xa")
        self.assertEqual(fm, '---\ntitle: "Título \\"com aspas\\""\ndescription: "Desc"\ndate: 2024-11-06T14:28:22.000Z\n'
                             'updated: 2026-03-10T03:09:28.966Z\nslug: um-post\ncover: img/um-post/capa.jpg\ntype: podcast\n'
                             'spotify_url: "https://open.spotify.com/episode/2ETPXIcx6RwKqdEj17z0xa"\nindex: false\n---\n')

    def test_newsletter_with_subtitle_is_indexable(self):
        fm = C.frontmatter({"title": "T", "subtitle": "S", "description": "D", "post_date": "2024-01-01T00:00:00.000Z",
                            "updated_at": "2024-01-02T00:00:00.000Z", "slug": "t", "type": "newsletter"})
        self.assertIn('subtitle: "S"\n', fm)
        self.assertNotIn("index:", fm)
        self.assertIn("type: newsletter\n", fm)

    def test_page_becomes_newsletter(self):
        fm = C.frontmatter({"title": "Livro", "subtitle": None, "description": "D", "post_date": "2024-01-01T00:00:00.000Z",
                            "updated_at": "2024-01-02T00:00:00.000Z", "slug": "livro", "type": "page"})
        self.assertIn("type: newsletter\n", fm)


class PodcastMatchTest(unittest.TestCase):
    def test_title_match_with_date_tiebreak_and_bijection(self):
        posts = [{"slug": "a-276", "title": "ChatGPT Plus: Preço", "post_date": "2024-09-04T10:00:00.000Z"},
                 {"slug": "a-aa0", "title": "ChatGPT Plus: Preço", "post_date": "2024-10-04T10:00:00.000Z"},
                 {"slug": "b", "title": "Llama 4: O Rebanho", "post_date": "2025-04-01T10:00:00.000Z"}]
        eps = [{"title": "ChatGPT Plus: Preço", "publishOn": "2024-10-04T12:00:00.000Z", "spotifyUrl": "https://open.spotify.com/episode/AAAAAAAAAAAAAAAAAAAAAA"},
               {"title": "ChatGPT Plus: Preço", "publishOn": "2024-09-04T12:00:00.000Z", "spotifyUrl": "https://open.spotify.com/episode/BBBBBBBBBBBBBBBBBBBBBB"},
               {"title": "Llama 4 - o rebanho", "publishOn": "2025-04-01T11:00:00.000Z", "spotifyUrl": "https://open.spotify.com/episode/CCCCCCCCCCCCCCCCCCCCCC"}]
        m = C.match_podcasts(posts, eps)
        self.assertEqual(m, {"a-276": "https://open.spotify.com/episode/BBBBBBBBBBBBBBBBBBBBBB",
                             "a-aa0": "https://open.spotify.com/episode/AAAAAAAAAAAAAAAAAAAAAA",
                             "b": "https://open.spotify.com/episode/CCCCCCCCCCCCCCCCCCCCCC"})

    def test_missing_or_far_episode_fails(self):
        posts = [{"slug": "x", "title": "Sem par", "post_date": "2024-09-04T10:00:00.000Z"}]
        with self.assertRaises(C.ConvertError):
            C.match_podcasts(posts, [])
        eps = [{"title": "Sem par", "publishOn": "2024-12-04T12:00:00.000Z", "spotifyUrl": "https://open.spotify.com/episode/AAAAAAAAAAAAAAAAAAAAAA"}]
        with self.assertRaises(C.ConvertError):
            C.match_podcasts(posts, eps)


if __name__ == "__main__":
    unittest.main()

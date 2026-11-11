"""Testes de newsletter_draft.py (issue #21, parte B). Sem rede: a API do Resend e o site são simulados.

    python3 blog-src/scripts/test_newsletter_draft.py
"""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import newsletter_draft as N  # noqa: E402

SEG = "seg-blog"
CFG = N.Config(api_key="re_teste", segment_id=SEG, sender="IA Aplicada <newsletter@expert.guifav.dev>",
               reply_to="contact@guifav.dev")


def page(body: str, *, slug="um-post", title="Um post", sub="Subtítulo do post", desc="Descrição curta.",
         cover=True, podcast="") -> str:
    url = cover if isinstance(cover, str) else f"https://guifav.dev/blog/img/{slug}/capa.jpg"
    og = f'<meta property="og:image" content="{url}">' if cover else ""
    pod = (f'<div class="podcast-embed"><iframe src="https://open.spotify.com/embed/episode/{podcast}" '
           f'title="Episódio" height="102"></iframe></div>') if podcast else ""
    return (
        '<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">'
        f'<meta name="description" content="{desc}">'
        f'<link rel="canonical" href="https://guifav.dev/blog/{slug}/">{og}'
        '<meta name="robots" content="noindex, follow"></head><body><main>'
        '<article class="post"><header class="post-head"><p class="post-crumb"><a href="/blog/">Blog</a></p>'
        f'<h1>{title}</h1><p class="post-sub">{sub}</p>'
        '<p class="post-meta"><span>Guilherme Favaron</span> &middot; <time datetime="2026-05-14T01:52:42.739Z">14 de maio de 2026</time></p>'
        f'</header>{pod}<div class="post-body">{body}</div></article></main></body></html>'
    )


def conv(body: str, **kw):
    return N.render_email(N.parse_post(page(body, **kw), "um-post"))


class ParseTest(unittest.TestCase):
    def test_reads_header_fields(self):
        p = N.parse_post(page("<p>oi</p>"), "um-post")
        self.assertEqual(p.title, "Um post")
        self.assertEqual(p.subtitle, "Subtítulo do post")
        self.assertEqual(p.description, "Descrição curta.")
        self.assertEqual(p.url, "https://guifav.dev/blog/um-post/")
        self.assertEqual(p.cover, "https://guifav.dev/blog/img/um-post/capa.jpg")
        self.assertEqual(p.spotify, "")

    def test_canonical_must_match_slug(self):
        with self.assertRaises(N.DraftError):
            N.parse_post(page("<p>oi</p>", slug="outro"), "um-post")

    def test_page_without_post_body_fails(self):
        with self.assertRaises(N.DraftError):
            N.parse_post(page("<p>oi</p>").replace('class="post-body"', 'class="x"'), "um-post")

    def test_podcast_player_becomes_spotify_link(self):
        p = N.parse_post(page("<p>oi</p>", podcast="51QWgKwqaADiMzhZKaywvY"), "um-post")
        self.assertEqual(p.spotify, "https://open.spotify.com/episode/51QWgKwqaADiMzhZKaywvY")
        html, text = N.render_email(p)
        self.assertIn('href="https://open.spotify.com/episode/51QWgKwqaADiMzhZKaywvY"', html)
        self.assertIn("https://open.spotify.com/episode/51QWgKwqaADiMzhZKaywvY", text)


class ConvertTest(unittest.TestCase):
    def test_paragraph_heading_lists_and_emphasis(self):
        html, text = conv("<h2 id=\"a\">Seção</h2><p>Texto <strong>forte</strong> e <em>leve</em>.</p>"
                          "<ul><li>um</li><li>dois</li></ul><ol start=\"3\"><li>três</li></ol>")
        self.assertIn("Seção</h2>", html)
        self.assertNotIn('id="a"', html)
        self.assertIn("<strong", html)
        self.assertIn('start="3"', html)
        self.assertIn("Seção", text)
        self.assertIn("- um", text)
        self.assertIn("3. três", text)

    def test_links_are_absolute(self):
        html, text = conv('<p><a href="/blog/outro/">outro</a> <a href="#sec">aqui</a> '
                          '<a href="https://example.com/x">fora</a> <a href="mailto:a@b.com">mail</a></p>')
        self.assertIn('href="https://guifav.dev/blog/outro/"', html)
        self.assertIn('href="https://guifav.dev/blog/um-post/#sec"', html)
        self.assertIn('href="https://example.com/x"', html)
        self.assertIn('href="mailto:a@b.com"', html)
        self.assertIn("outro (https://guifav.dev/blog/outro/)", text)
        self.assertNotIn('href="/', html)

    def test_active_scheme_fails(self):
        with self.assertRaises(N.DraftError):
            conv('<p><a href="javascript:alert(1)">x</a></p>')
        with self.assertRaises(N.DraftError):
            conv('<p><a href="//evil.example/x">x</a></p>')

    def test_image_absolute_without_srcset_and_capped_width(self):
        html, _ = conv('<figure><img src="/blog/img/um-post/a-1456.webp" srcset="/blog/img/um-post/a-728.webp 728w" '
                       'sizes="100vw" width="1456" height="800" alt="Diagrama" loading="lazy" decoding="async">'
                       '<figcaption>Legenda</figcaption></figure>')
        self.assertIn('src="https://guifav.dev/blog/img/um-post/a-1456.webp"', html)
        self.assertNotIn("srcset", html)
        self.assertNotIn("loading=", html)
        self.assertIn('width="560"', html)
        self.assertIn('alt="Diagrama"', html)
        self.assertIn("Legenda", html)

    def test_small_image_keeps_its_width(self):
        html, _ = conv('<figure><img src="/blog/img/um-post/b.gif" width="300" height="200" alt=""></figure>')
        self.assertIn('width="300"', html)

    def test_youtube_and_spotify_iframes_become_links(self):
        html, text = conv('<div class="video"><iframe src="https://www.youtube-nocookie.com/embed/abcDEF12345" '
                          'title="v" loading="lazy" allowfullscreen></iframe></div>'
                          '<div class="podcast-embed"><iframe src="https://open.spotify.com/embed/episode/XYZ123" title="e"></iframe></div>')
        self.assertNotIn("<iframe", html)
        self.assertIn('href="https://www.youtube.com/watch?v=abcDEF12345"', html)
        self.assertIn('href="https://open.spotify.com/episode/XYZ123"', html)
        self.assertIn("https://www.youtube.com/watch?v=abcDEF12345", text)
        self.assertIn("https://open.spotify.com/episode/XYZ123", text)

    def test_unknown_iframe_host_fails(self):
        with self.assertRaises(N.DraftError):
            conv('<div class="video"><iframe src="https://evil.example/embed/x"></iframe></div>')

    def test_video_becomes_poster_linked_to_post(self):
        html, _ = conv('<video src="/blog/video/um-post/v.mp4" poster="/blog/img/um-post/p.webp" width="1502" '
                       'height="1080" controls preload="none" playsinline></video>')
        self.assertNotIn("<video", html)
        self.assertIn('href="https://guifav.dev/blog/um-post/"', html)
        self.assertIn('src="https://guifav.dev/blog/img/um-post/p.webp"', html)

    def test_code_block_keeps_text_and_newlines_without_shiki_spans(self):
        html, text = conv('<pre class="astro-code" style="--shiki-light:#fff" tabindex="0" data-language="py"><code>'
                          '<span class="line"><span style="--shiki-light:#D73A49">def</span> f():</span>\n'
                          '<span class="line">    return 1 &lt; 2</span></code></pre>')
        self.assertNotIn("shiki", html)
        self.assertNotIn("<span", html)
        self.assertIn("def f():\n    return 1 &lt; 2", html)
        self.assertIn("def f():\n    return 1 < 2", text)

    def test_code_html_block_keeps_link_and_bold(self):
        html, text = conv('<pre class="code-html">Veja <a href="https://bing.com/webmasters/">Bing</a> e <strong>isto</strong>\nfim</pre>')
        self.assertIn('href="https://bing.com/webmasters/"', html)
        self.assertIn("<strong>isto</strong>", html)
        self.assertIn("Bing (https://bing.com/webmasters/)", text)
        self.assertIn("isto\nfim", text)
        with self.assertRaises(N.DraftError):
            conv('<pre class="code-html"><img src="/blog/img/x.webp" width="1" height="1" alt=""></pre>')

    def test_pullquote_callout_blockquote_hr_br(self):
        html, text = conv('<div class="pullquote"><p>Citação</p></div><div class="callout"><p>Caixa</p></div>'
                          '<blockquote><p>Bloco</p></blockquote><hr><p>a<br>b</p>')
        self.assertIn("Citação", html)
        self.assertIn("Caixa", html)
        self.assertIn("<blockquote", html)
        self.assertIn("<hr", html)
        self.assertIn("a<br>b", html)
        self.assertNotIn('class="', html.split("<body", 1)[1])

    def test_unknown_tag_or_class_fails(self):
        for bad in ('<p><script>alert(1)</script></p>', '<table><tr><td>x</td></tr></table>',
                    '<div class="desconhecida"><p>x</p></div>', '<p><span class="x">y</span></p>',
                    '<p onclick="x()">y</p>'):
            with self.assertRaises(N.DraftError, msg=bad):
                conv(bad)

    def test_text_is_escaped(self):
        html, _ = conv("<p>a &lt;b&gt; &amp; c</p>", title='Título com &quot;aspas&quot; &amp; &lt;tags&gt;')
        self.assertIn("a &lt;b&gt; &amp; c", html)
        self.assertIn("Título com &quot;aspas&quot; &amp; &lt;tags&gt;", html)


class EnvelopeTest(unittest.TestCase):
    def test_header_cta_footer_and_unsubscribe_in_html_and_text(self):
        html, text = conv("<p>Corpo.</p>")
        for s in ("Um post", "Subtítulo do post", "https://guifav.dev/blog/img/um-post/capa.jpg",
                  "Ler no site", 'href="https://guifav.dev/blog/um-post/"', "{{{RESEND_UNSUBSCRIBE_URL}}}",
                  'href="https://guifav.dev/blog/privacidade/"', "Você recebe este email porque assinou"):
            self.assertIn(s, html)
        for s in ("Um post", "Corpo.", "https://guifav.dev/blog/um-post/", "{{{RESEND_UNSUBSCRIBE_URL}}}"):
            self.assertIn(s, text)
        self.assertIn("Descrição curta.", html)  # preheader

    def test_no_em_dash_in_template(self):
        html, text = conv("<p>Corpo.</p>")
        self.assertNotIn("\u2014", html + text)


class SizeTest(unittest.TestCase):
    def test_small_post_is_full(self):
        p = N.parse_post(page("<p>Curto.</p>"), "um-post")
        html, text = N.render_email(p)
        self.assertNotIn(N.TRUNCATED_NOTE, html)

    def test_big_post_is_cut_below_gmail_limit_with_link(self):
        body = "".join(f"<p>Parágrafo {i} " + "texto " * 60 + "</p>" for i in range(400))
        html, text = N.render_email(N.parse_post(page(body), "um-post"))
        self.assertLess(len(html.encode("utf-8")), N.GMAIL_CLIP_BYTES)
        self.assertIn(N.TRUNCATED_NOTE, html)
        self.assertIn(N.TRUNCATED_NOTE, text)
        self.assertIn("Parágrafo 0 ", html)
        self.assertNotIn("Parágrafo 399 ", html)
        self.assertNotIn("Parágrafo 399 ", text)
        self.assertIn("{{{RESEND_UNSUBSCRIBE_URL}}}", html)

    def test_cut_happens_between_blocks(self):
        body = "".join(f"<p>P{i} " + "x" * 2000 + "</p>" for i in range(80))
        html, _ = N.render_email(N.parse_post(page(body), "um-post"))
        self.assertEqual(html.count("<p") - html.count("</p>"), 0)


class FakeResend:
    def __init__(self, broadcasts=None, fail=None):
        self.broadcasts = broadcasts or []
        self.calls = []
        self.fail = fail

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, json.loads(body) if body else None))
        if self.fail:
            return self.fail, {"message": "erro"}
        if method == "GET" and url == "https://api.resend.com/broadcasts":
            return 200, {"object": "list", "has_more": False, "data": self.broadcasts}
        if method == "POST" and url == "https://api.resend.com/broadcasts":
            return 201, {"id": "b-novo"}
        if method == "PATCH" and url.startswith("https://api.resend.com/broadcasts/"):
            return 200, {"id": url.rsplit("/", 1)[1]}
        return 500, {"message": "inesperado"}


def post():
    return N.parse_post(page("<p>Corpo.</p>"), "um-post")


class DraftTest(unittest.TestCase):
    def test_creates_one_draft_with_exact_fields_and_never_send(self):
        f = FakeResend()
        r = N.create_draft(N.Api(CFG, f), post(), CFG)
        self.assertEqual(r, ("criado", "b-novo"))
        self.assertEqual([(m, u) for m, u, _ in f.calls],
                         [("GET", "https://api.resend.com/broadcasts"), ("POST", "https://api.resend.com/broadcasts")])
        body = f.calls[1][2]
        self.assertEqual(set(body), {"segment_id", "from", "reply_to", "subject", "html", "text", "name"})
        self.assertEqual(body["segment_id"], SEG)
        self.assertEqual(body["from"], "IA Aplicada <newsletter@expert.guifav.dev>")
        self.assertEqual(body["reply_to"], "contact@guifav.dev")
        self.assertEqual(body["subject"], "Um post")
        self.assertEqual(body["name"], "blog/um-post")

    def test_existing_draft_is_kept_without_changes(self):
        f = FakeResend([{"id": "b1", "name": "blog/um-post", "status": "draft"}])
        self.assertEqual(N.create_draft(N.Api(CFG, f), post(), CFG), ("existe", "b1"))
        self.assertEqual([m for m, _, _ in f.calls], ["GET"])

    def test_update_rewrites_only_a_draft(self):
        f = FakeResend([{"id": "b1", "name": "blog/um-post", "status": "draft"}])
        self.assertEqual(N.create_draft(N.Api(CFG, f), post(), CFG, update=True), ("atualizado", "b1"))
        self.assertEqual([(m, u) for m, u, _ in f.calls][-1], ("PATCH", "https://api.resend.com/broadcasts/b1"))
        self.assertEqual(set(f.calls[-1][2]), {"from", "reply_to", "subject", "html", "text"})

    def test_sent_or_scheduled_broadcast_blocks(self):
        for st in ("sent", "queued", "scheduled", "sending"):
            f = FakeResend([{"id": "b1", "name": "blog/um-post", "status": st}])
            with self.assertRaises(N.DraftError, msg=st):
                N.create_draft(N.Api(CFG, f), post(), CFG, update=True)
            self.assertEqual([m for m, _, _ in f.calls], ["GET"], st)

    def test_two_broadcasts_with_the_same_name_block(self):
        f = FakeResend([{"id": "b1", "name": "blog/um-post", "status": "draft"},
                        {"id": "b2", "name": "blog/um-post", "status": "draft"}])
        with self.assertRaises(N.DraftError):
            N.create_draft(N.Api(CFG, f), post(), CFG)

    def test_api_error_is_reported(self):
        with self.assertRaises(N.DraftError):
            N.create_draft(N.Api(CFG, FakeResend(fail=422)), post(), CFG)

    def test_api_refuses_any_send_path_or_flag(self):
        # O transporte falha o teste se for chamado: a recusa precisa acontecer antes de qualquer rede.
        def never(*a):
            raise AssertionError(f"transporte chamado: {a[:2]}")
        api = N.Api(CFG, never)
        for method, path, body in (("POST", "/broadcasts/b1/send", None), ("POST", "/emails", {"to": "x"}),
                                   ("POST", "/broadcasts", {"send": True}), ("POST", "/broadcasts", {"send": False}),
                                   ("PATCH", "/broadcasts/b1", {"scheduled_at": "in 1 hour"}),
                                   ("POST", "/broadcasts", {"scheduled_at": "in 1 hour"}),
                                   ("DELETE", "/broadcasts/b1", None), ("GET", "/broadcasts/b1/send", None)):
            with self.assertRaises(N.DraftError, msg=(method, path)):
                api.request(method, path, body)

    def test_source_has_no_send_call(self):
        src = open(N.__file__, encoding="utf-8").read()
        self.assertNotIn('"send":', src)
        self.assertNotIn('"/emails', src)
        self.assertNotIn("/send", src.split('"""', 2)[2])


class ConfigTest(unittest.TestCase):
    def test_config_from_env_requires_key_and_segment(self):
        with self.assertRaises(N.DraftError):
            N.Config.from_env({"NEWSLETTER_SEGMENT_ID": "s"})
        with self.assertRaises(N.DraftError):
            N.Config.from_env({"RESEND_API_KEY": "k"})
        c = N.Config.from_env({"RESEND_API_KEY": "k", "NEWSLETTER_SEGMENT_ID": "s"})
        self.assertEqual(c.sender, "IA Aplicada <newsletter@expert.guifav.dev>")
        self.assertEqual(c.reply_to, "contact@guifav.dev")

    def test_sender_must_be_on_expert_subdomain(self):
        with self.assertRaises(N.DraftError):
            N.Config.from_env({"RESEND_API_KEY": "k", "NEWSLETTER_SEGMENT_ID": "s",
                               "NEWSLETTER_FROM": "IA Aplicada <newsletter@guifav.dev>"})



# ---------------------------------------------------------------- rodada 1 do gate (#33)

class StatefulResend:
    """Resend simulado com estado; o GET espera o outro processo numa barreira, para forçar a corrida."""

    def __init__(self):
        self.broadcasts = []
        self.lock = threading.Lock()
        self.barrier = threading.Barrier(2, timeout=1)
        self.posts = 0

    def __call__(self, method, url, headers, body):
        if method == "GET":
            with self.lock:
                snap = list(self.broadcasts)
            try:
                self.barrier.wait()
            except threading.BrokenBarrierError:
                pass
            return 200, {"object": "list", "has_more": False, "data": snap}
        if method == "POST":
            with self.lock:
                self.posts += 1
                b = {"id": f"b{self.posts}", "name": json.loads(body)["name"], "status": "draft"}
                self.broadcasts.append(b)
            return 201, {"id": b["id"]}
        return 500, {}


class RaceTest(unittest.TestCase):
    def test_two_concurrent_runs_create_a_single_draft(self):
        f = StatefulResend()
        out = []
        with tempfile.TemporaryDirectory() as d:
            def run():
                out.append(N.create_draft(N.Api(CFG, f), post(), CFG, lock_dir=d))
            ts = [threading.Thread(target=run) for _ in range(2)]
            [t.start() for t in ts]
            [t.join(10) for t in ts]
        self.assertEqual(f.posts, 1)
        self.assertEqual(sorted(w for w, _ in out), ["criado", "existe"])


class TextFidelityTest(unittest.TestCase):
    def test_image_link_keeps_destination_in_text(self):
        html, text = conv('<p><a href="https://youtu.be/CCXTyC09zv0"><img src="/blog/img/um-post/v.webp" width="10" height="10" alt="Vídeo"></a></p>'
                          '<p><a href="https://example.com/sem-alt"><img src="/blog/img/um-post/w.webp" width="10" height="10" alt=""></a></p>')
        self.assertIn("Vídeo (https://youtu.be/CCXTyC09zv0)", text)
        self.assertIn("https://example.com/sem-alt", text)

    def test_spaces_around_link_survive_in_text(self):
        _, text = conv('<p><a href="https://claude.ai/">Claude</a> e<a href="https://chatgpt.com/">\u00a0ChatGPT</a> fim</p>')
        self.assertIn("Claude (https://claude.ai/) e ChatGPT (https://chatgpt.com/) fim", text)

    def test_code_keeps_blank_lines_in_text(self):
        html, text = conv('<pre><code>print("""a\n\n\nb""")</code></pre><p>x</p>\n\n\n<p>y</p>')
        self.assertIn('print("""a\n\n\nb""")', text)
        self.assertNotIn("x\n\n\ny", text)

    def test_text_has_subtitle_reason_and_privacy(self):
        _, text = conv("<p>Corpo.</p>")
        for s in ("Subtítulo do post", "Você recebe este email porque assinou", "Privacidade: https://guifav.dev/blog/privacidade/"):
            self.assertIn(s, text)


class CoverTest(unittest.TestCase):
    def test_cover_goes_through_url_policy(self):
        for bad in ("javascript:alert(1)", "data:image/svg+xml,abc", "//evil.example/a.jpg", "http://example.com/a.jpg"):
            with self.assertRaises(N.DraftError, msg=bad):
                conv("<p>x</p>", cover=bad)
        html, _ = conv("<p>x</p>", cover="/blog/img/um-post/capa.jpg")
        self.assertIn('src="https://guifav.dev/blog/img/um-post/capa.jpg"', html)


class LimitTest(unittest.TestCase):
    def test_huge_description_does_not_blow_the_limit(self):
        html, _ = conv("<p>Pequeno.</p>", desc="x" * 110000)
        self.assertLess(len(html.encode("utf-8")), N.GMAIL_CLIP_BYTES - N.SIZE_MARGIN)
        self.assertIn("Pequeno.", html)

    def test_huge_title_fails_clearly(self):
        with self.assertRaisesRegex(N.DraftError, "título e subtítulo"):
            conv("<p>x</p>", title="T" * 120000)

    def test_first_block_bigger_than_budget_fails(self):
        with self.assertRaisesRegex(N.DraftError, "primeiro bloco"):
            conv("<p>" + "á" * 200000 + "</p><p>depois</p>")

    def test_kept_blocks_are_the_same_in_html_and_text(self):
        body = "".join(f"<p>B{i:03d} " + "y" * 3000 + "</p>" for i in range(60))
        html, text = conv(body)
        last_html = max(int(x) for x in __import__("re").findall(r"B(\d{3}) ", html))
        last_text = max(int(x) for x in __import__("re").findall(r"B(\d{3}) ", text))
        self.assertEqual(last_html, last_text)


class FetchTest(unittest.TestCase):
    class Resp:
        def __init__(self, status, url, body=b"<html></html>"):
            self.status, self._url, self._body = status, url, body

        def geturl(self):
            return self._url

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def test_live_requires_200_at_the_post_url_without_redirect(self):
        url = "https://guifav.dev/blog/um-post/"
        seen = []

        def ok(req, timeout=0):
            seen.append(req.full_url)
            return self.Resp(200, url, b"pagina")
        with mock.patch.object(N.urllib.request, "urlopen", ok):
            self.assertEqual(N.fetch_page("um-post", "live", ""), "pagina")
        self.assertEqual(seen, [url])
        with mock.patch.object(N.urllib.request, "urlopen", lambda req, timeout=0: self.Resp(200, "https://guifav.dev/blog/")):
            with self.assertRaises(N.DraftError):
                N.fetch_page("um-post", "live", "")

        def notfound(req, timeout=0):
            raise urllib.error.HTTPError(url, 404, "nf", {}, __import__("io").BytesIO(b""))
        with mock.patch.object(N.urllib.request, "urlopen", notfound):
            with self.assertRaises(N.DraftError):
                N.fetch_page("um-post", "live", "")

    def test_slug_is_validated(self):
        with self.assertRaises(N.DraftError):
            N.fetch_page("../etc", "live", "")



# ---------------------------------------------------------------- rodada 2 do gate (#33)

class Round2Test(unittest.TestCase):
    def test_content_cell_breaks_long_words(self):
        # overflow-wrap não entra no cálculo de largura mínima da tabela; word-break:break-word entra (P2 do Astra).
        html, _ = conv('<p><a href="https://example.com/x">https://example.com/' + "a" * 300 + "</a></p>")
        cell = html.split("serif;", 1)[1].split('">', 1)[0]
        self.assertIn("word-break:break-word", cell)

    def test_code_in_list_and_leading_indent_are_literal_in_text(self):
        _, t1 = conv("<pre><code>    a\n    b</code></pre>")
        self.assertIn("    a\n    b", t1)
        _, t2 = conv('<ul><li><pre><code>print("""a\n\n\nb""")</code></pre></li></ul>')
        self.assertIn('print("""a\n\n\nb""")', t2)

    def test_limits_with_literal_numbers(self):
        body = "".join(f"<p>Parágrafo {i} " + "texto " * 60 + "</p>" for i in range(400))
        html, _ = conv(body)
        self.assertLess(len(html.encode("utf-8")), 100_352)        # 102 KB do Gmail menos 4 KB de folga
        self.assertGreater(len(html.encode("utf-8")), 95_000)       # e o corte aproveita o espaço
        html, _ = conv("<p>x</p>", desc="d" * 500)
        pre = html.split('opacity:0;">', 1)[1].split("</div>", 1)[0]
        self.assertEqual(len(pre), 200)
        self.assertTrue(pre.endswith("..."))
        html, _ = conv("<p>x</p>", desc="e" * 200)
        self.assertEqual(html.split('opacity:0;">', 1)[1].split("</div>", 1)[0], "e" * 200)

    def test_attributes_are_escaped(self):
        html, _ = conv('<figure><img src="/blog/img/um-post/a.webp" width="10" height="10" alt="diz &quot;oi&quot; &amp; &lt;b&gt;"></figure>')
        self.assertIn('alt="diz &quot;oi&quot; &amp; &lt;b&gt;"', html)

    def test_body_image_scheme_policy(self):
        for bad in ("javascript:alert(1)", "http://example.com/a.png", "data:image/png;base64,AAAA", "//evil.example/a.png"):
            with self.assertRaises(N.DraftError, msg=bad):
                conv(f'<figure><img src="{bad}" width="10" height="10" alt=""></figure>')

    def test_any_status_other_than_draft_blocks(self):
        for st in ("canceled", "failed", "desconhecido", None):
            f = FakeResend([{"id": "b1", "name": "blog/um-post", "status": st}])
            with self.assertRaises(N.DraftError, msg=st):
                N.create_draft(N.Api(CFG, f), post(), CFG, update=True)

    def test_live_rejects_non_200_success_codes(self):
        url = "https://guifav.dev/blog/um-post/"
        for st in (204, 206, 203):
            with mock.patch.object(N.urllib.request, "urlopen", lambda req, timeout=0, st=st: FetchTest.Resp(st, url)):
                with self.assertRaises(N.DraftError, msg=st):
                    N.fetch_page("um-post", "live", "")

    def test_sender_must_be_a_single_address_on_the_exact_domain(self):
        base = {"RESEND_API_KEY": "k", "NEWSLETTER_SEGMENT_ID": "s"}
        for ok in ("IA Aplicada <newsletter@expert.guifav.dev>", "newsletter@expert.guifav.dev",
                   "Guilherme Favaron <gui.f+blog@expert.guifav.dev>"):
            self.assertEqual(N.Config.from_env({**base, "NEWSLETTER_FROM": ok}).sender, ok)
        for bad in ("IA <newsletter@expert.guifav.dev>\n", "newsletter@guifav.dev\nBcc: destino@expert.guifav.dev",
                    "a@guifav.dev, b@expert.guifav.dev", "IA <newsletter@expert.guifav.dev.evil>",
                    "IA <newsletter@guifav.dev> expert.guifav.dev", "newsletter@sub.expert.guifav.dev",
                    "IA <a@expert.guifav.dev", "x <a@b> <c@expert.guifav.dev>", "IA\r\n <a@expert.guifav.dev>"):
            with self.assertRaises(N.DraftError, msg=repr(bad)):
                N.Config.from_env({**base, "NEWSLETTER_FROM": bad})


if __name__ == "__main__":
    unittest.main()

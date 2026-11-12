"""Testes do verificador do site montado (scripts/check_site.py).

Rodar: python3 scripts/test_check_site.py
"""
import os
import re
import tempfile
import unittest
from pathlib import Path

import check_site as C

ROBOTS_NOINDEX = '<meta name="robots" content="noindex, follow">'
ROBOTS_INDEX = '<meta name="robots" content="index, follow">'


def page(body: str = "", robots: str = ROBOTS_NOINDEX) -> str:
    return f"<!DOCTYPE html><html><head>{robots}</head><body>{body}</body></html>"


SITEMAP = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{}</urlset>'
)
RSS = '<?xml version="1.0"?><rss version="2.0"><channel><title>t</title></channel></rss>'


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        r = Path(self.tmp.name)
        self.site = r / "_site"
        self.posts = r / "posts"
        self.posts.mkdir()
        self.w("index.html", page('<a href="/portfolio/">p</a><img src="/assets/a.png">', robots=""))
        self.w("portfolio/index.html", page('<a href="/">home</a>', robots=""))
        self.w("assets/a.png", "png")
        self.w("404.html", page('<a href="/">home</a>', robots='<meta name="robots" content="noindex">'))
        self.w("blog/index.html", page('<a href="/blog/um-post/">um</a><a href="/portfolio/">p</a>'))
        self.w("blog/um-post/index.html", page('<a href="/blog/">blog</a><img src="/blog/img/um-post/a.webp">'))
        self.w("blog/img/um-post/a.webp", "webp")
        self.w("blog/rss.xml", RSS)
        self.w("blog/sitemap.xml", SITEMAP.format(
            "<url><loc>https://guifav.dev/blog/</loc></url>"
            "<url><loc>https://guifav.dev/blog/um-post/</loc></url>"
        ))
        (self.posts / "um-post.md").write_text("---\ntitle: x\nslug: um-post\n---\ntexto\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def w(self, rel: str, text: str) -> None:
        p = self.site / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def problems(self, pre_cutover: bool = True) -> list[str]:
        return C.check(self.site, self.posts, pre_cutover=pre_cutover)


_OLD_URL_IN_SOURCE = re.compile(
    r"""(?:https?://(?:www\.)?|(?<![\w./-])www\.)guilhermefavaron\.com\.br[^\s)"'<>\]]*""", re.I)


def old_links_in_sources(posts: Path) -> list[tuple[str, str]]:
    """Toda URL do domínio antigo nos fontes (inline, referência, autolink, solta, www.), com a página de destino."""
    # Pontuação final fica fora do link, como no GFM.
    return [(f"blog/{f.stem}/index.html", m.group(0).rstrip(".,;:!?*_~"))
            for f in sorted(posts.glob("*.md*")) for m in _OLD_URL_IN_SOURCE.finditer(f.read_text(encoding="utf-8"))]


class OldDomainTest(Base):
    """Depois do cutover (#24), nada no site aponta para o domínio antigo do Substack."""

    def flagged(self, rel: str, html: str) -> list[str]:
        self.w(rel, html)
        return [p for p in self.problems(pre_cutover=False) if "domínio antigo" in p]

    def test_encoded_and_fqdn_links_are_flagged_everywhere(self):
        # #50 r1: a busca literal não vê entidade HTML nem percent-encoding; o parser WHATWG mantém o ponto final.
        for url in ("https://guilhermefavaron&#46;com.br/p/x", "https://%67uilhermefavaron.com.br/p/x",
                    "https://guilhermefavaron.com.br./p/x", "https://www.guilhermefavaron.com.br./p/x",
                    "https://www.guilhermefavaron.com.br\u3002/p/x"):
            for rel, robots in (("index.html", ""), ("blog/um-post/index.html", ROBOTS_INDEX)):
                with self.subTest(url=url, rel=rel):
                    self.setUp()
                    got = self.flagged(rel, page(f'<a href="{url}">n</a>', robots=robots))
                    self.assertTrue(any("link para o domínio antigo" in g for g in got), got)

    def test_links_and_embeds_to_the_old_domain_are_flagged(self):
        for host in ("guilhermefavaron.com.br", "www.guilhermefavaron.com.br", "WWW.GuilhermeFavaron.com.br"):
            for rel, html in (
                ("index.html", page(f'<a href="https://{host}/p/x">n</a>', robots="")),
                ("portfolio/index.html", page(f'<iframe src="https://{host}/embed"></iframe>', robots="")),
                ("blog/um-post/index.html", page(f'<a href="//{host}/post/y">n</a>', robots=ROBOTS_INDEX)),
                ("blog/index.html", page(f'<iframe src="https://{host}/embed"></iframe>', robots=ROBOTS_INDEX)),
                ("blog/um-post/index.html", page(f'<form method="post" action="https://{host}/x"></form>', robots=ROBOTS_INDEX)),
            ):
                with self.subTest(host=host, rel=rel):
                    self.setUp()
                    got = self.flagged(rel, html)
                    self.assertTrue(any("link para o domínio antigo" in g for g in got), got)
                    if rel.startswith("blog/"):
                        self.assertEqual(len(got), 1, got)

    def test_text_mention_inside_a_post_is_not_a_link(self):
        self.assertEqual(self.flagged("blog/um-post/index.html",
                                      page("<p>antes em guilhermefavaron.com.br</p>", robots=ROBOTS_INDEX)), [])

    def test_any_mention_outside_the_blog_is_flagged_including_json_ld(self):
        for host in ("guilhermefavaron.com.br", "GuilhermeFavaron.COM.br"):
            with self.subTest(host=host):
                self.setUp()
                ld = f'<script type="application/ld+json">{{"sameAs":["https://{host}"]}}</script>'
                self.assertEqual(len(self.flagged("index.html", page(ld, robots=""))), 1)

    def test_encoded_formaction_outside_the_blog_is_flagged(self):
        # #50 r2: formaction era só "conteúdo ativo", que fora do blog não é barrado; o destino escapava.
        for url in ("https://%67uilhermefavaron.com.br/subscribe", "https://guilhermefavaron&#46;com.br/subscribe"):
            for rel in ("index.html", "portfolio/index.html"):
                with self.subTest(url=url, rel=rel):
                    self.setUp()
                    html = page(f'<form action="/x"><button formaction="{url}">ok</button></form>', robots="")
                    got = self.flagged(rel, html)
                    self.assertTrue(any("link para o domínio antigo" in g for g in got), got)

    def test_meta_refresh_to_the_old_domain_is_flagged(self):
        # O navegador navega para essa URL; só entra em p.refs (não é atributo de URL).
        html = page('<meta http-equiv="refresh" content="0; url=https://www.guilhermefavaron.com.br/p/x">', robots=ROBOTS_INDEX)
        self.assertEqual(len(self.flagged("blog/um-post/index.html", html)), 1)

    def test_known_dead_links_from_the_import_are_allowed_only_on_their_page(self):
        rel, url = C.OLD_DOMAIN_DEAD_LINKS[0]
        self.w("blog/" + rel.split("/")[1] + "/index.html", "")
        self.assertEqual(self.flagged(rel, page(f'<a href="{url}">x</a>', robots=ROBOTS_INDEX)), [])
        self.assertEqual(len(self.flagged("blog/um-post/index.html", page(f'<a href="{url}">x</a>', robots=ROBOTS_INDEX))), 1)

    def test_other_old_link_on_an_allowed_page_is_flagged(self):
        rel, url = C.OLD_DOMAIN_DEAD_LINKS[0]
        for other in ("https://www.guilhermefavaron.com.br", url + "/", url.replace("https://www.", "https://")):
            with self.subTest(other=other):
                self.setUp()
                self.assertEqual(len(self.flagged(rel, page(f'<a href="{other}">x</a>', robots=ROBOTS_INDEX))), 1)

    def test_source_scan_catches_every_markdown_link_form(self):
        # #50 r2: link por referência, autolink, URL solta e www. (o GFM transforma os dois últimos em link).
        with tempfile.TemporaryDirectory() as d:
            Path(d, "p.md").write_text(
                "[a](https://guilhermefavaron.com.br/a) [b][r] <https://www.guilhermefavaron.com.br/c>\n"
                "solta https://GuilhermeFavaron.com.br/d e www.guilhermefavaron.com.br/e; texto guilhermefavaron.com.br\n\n"
                "[r]: https://www.guilhermefavaron.com.br/b\n", encoding="utf-8")
            got = [u for _, u in old_links_in_sources(Path(d))]
        self.assertEqual(got, ["https://guilhermefavaron.com.br/a", "https://www.guilhermefavaron.com.br/c",
                               "https://GuilhermeFavaron.com.br/d", "www.guilhermefavaron.com.br/e",
                               "https://www.guilhermefavaron.com.br/b"])

    def test_known_dead_list_matches_the_post_sources(self):
        # Referência independente da lista: toda URL do domínio antigo nos fontes dos posts importados.
        # 7 pares (8 ocorrências: um deles aparece duas vezes no mesmo post) que já davam 404 no Substack antes do
        # cutover (caminhos da era Wix); a importação não altera texto.
        found = old_links_in_sources(Path(__file__).resolve().parent.parent / "blog-src/src/content/posts")
        self.assertEqual(len(found), 8)
        self.assertEqual(set(found), set(C.OLD_DOMAIN_DEAD_LINKS))
        self.assertTrue(all(rel.startswith("blog/") for rel, _ in C.OLD_DOMAIN_DEAD_LINKS))
        self.assertEqual(len(C.OLD_DOMAIN_DEAD_LINKS), len(set(C.OLD_DOMAIN_DEAD_LINKS)))

    def test_not_checked_before_the_cutover(self):
        self.w("index.html", page('<a href="https://guilhermefavaron.com.br">n</a>', robots=""))
        self.assertFalse(any("domínio antigo" in p for p in self.problems(pre_cutover=True)))


class CheckTest(Base):
    def test_clean_site_passes(self):
        self.assertEqual(self.problems(), [])

    def test_broken_internal_link_in_blog(self):
        self.w("blog/um-post/index.html", page('<a href="/blog/nao-existe/">x</a>'))
        self.assertTrue(any("nao-existe" in p for p in self.problems()))

    def test_broken_asset_in_blog(self):
        self.w("blog/um-post/index.html", page('<img src="/blog/img/um-post/sumiu.webp">'))
        self.assertTrue(any("sumiu.webp" in p for p in self.problems()))

    def test_link_to_hidden_folder_is_broken(self):
        self.w("blog/um-post/index.html", page('<a href="/docs/plan.md">x</a>'))
        self.assertTrue(any("/docs/plan.md" in p for p in self.problems()))

    def test_query_fragment_and_external_links_ignored(self):
        self.w("blog/um-post/index.html", page(
            '<a href="/blog/?x=1#topo">a</a><a href="https://example.com/x">b</a>'
            '<a href="mailto:a@b.c">c</a><a href="#nota">d</a><a href="//cdn.example/x.js">e</a>'
        ))
        self.assertEqual(self.problems(), [])

    def test_link_without_trailing_slash_to_directory_is_flagged(self):
        # O Pages redireciona /blog/um-post para /blog/um-post/, mas é um salto a mais.
        self.w("blog/index.html", page('<a href="/blog/um-post">um</a>'))
        self.assertTrue(any("barra final" in p for p in self.problems()))

    def test_relative_links_resolve_from_page(self):
        self.w("blog/um-post/index.html", page('<img src="../img/um-post/a.webp"><a href="../">b</a>'))
        self.assertEqual(self.problems(), [])
        self.w("blog/um-post/index.html", page('<img src="../img/um-post/nada.webp">'))
        self.assertTrue(any("nada.webp" in p for p in self.problems()))

    def test_srcset_entries_are_checked(self):
        self.w("blog/um-post/index.html", page(
            '<img src="/blog/img/um-post/a.webp" srcset="/blog/img/um-post/a.webp 728w, /blog/img/um-post/b.webp 1456w">'
        ))
        self.assertTrue(any("b.webp" in p for p in self.problems()))

    def test_pre_cutover_every_blog_page_needs_noindex(self):
        self.w("blog/um-post/index.html", page('<a href="/blog/">b</a>', robots=ROBOTS_INDEX))
        self.assertTrue(any("noindex" in p and "um-post" in p for p in self.problems(pre_cutover=True)))
        self.assertEqual(self.problems(pre_cutover=False), [])

    def test_missing_robots_meta_in_blog_is_flagged_pre_cutover(self):
        self.w("blog/um-post/index.html", page('<a href="/blog/">b</a>', robots=""))
        self.assertTrue(any("noindex" in p for p in self.problems(pre_cutover=True)))

    def test_pre_cutover_site_cannot_link_to_blog(self):
        self.w("portfolio/index.html", page('<a href="/blog/">blog</a>', robots=""))
        self.assertTrue(any("aponta para o blog" in p for p in self.problems(pre_cutover=True)))
        self.assertEqual(self.problems(pre_cutover=False), [])

    def test_refresh_url_follows_the_html_rule(self):
        # Rodada 9 do #32 (Kimi): o navegador aceita a URL do refresh com ou sem "url=".
        cases = {"0;url=/a": "/a", "0; URL = '/a'": "/a", "0;https://x.com/y": "https://x.com/y", "0,/b": "/b",
                 "0 /c": "/c", "0; url=\"/d\" resto": "/d", "1.5; url=/e": "/e", "5": None, "0;": None,
                 "x;url=/a": None, "0;url": "url"}
        for content, want in cases.items():
            self.assertEqual(C.refresh_url(content), want, content)

    def test_meta_refresh_to_blog_counts_as_link_pre_cutover(self):
        self.w("index.html", page('<meta http-equiv="refresh" content="0;/blog/"><img src="/assets/a.png">', robots=""))
        self.assertTrue(any("aponta para o blog" in p for p in self.problems(pre_cutover=True)))

    def test_meta_refresh_to_missing_page_is_broken(self):
        self.w("blog/um-post/index.html", page('<meta http-equiv="Refresh" content="0; url=/blog/nao-existe/">'
                                               '<img src="/blog/img/um-post/a.webp">'))
        self.assertTrue(any("nao-existe" in p for p in self.problems()))

    def test_protocol_relative_urls_are_internal(self):
        self.w("index.html", page('<a href="//guifav.dev/nao-existe/">x</a>', robots=""))
        self.assertTrue(any("nao-existe" in p for p in self.problems()))
        self.w("index.html", page('<a href="//guifav.dev/blog/">x</a>', robots=""))
        self.assertTrue(any("aponta para o blog" in p for p in self.problems()))

    def test_dot_segments_are_normalized(self):
        self.w("index.html", page('<a href="https://guifav.dev/portfolio/../blog/">x</a>', robots=""))
        self.assertTrue(any("aponta para o blog" in p for p in self.problems()))
        self.w("index.html", page('<a href="portfolio/./../blog/">x</a>', robots=""))
        self.assertTrue(any("aponta para o blog" in p for p in self.problems()))
        self.w("index.html", page('<a href="/assets/../portfolio/">x</a>', robots=""))
        self.assertEqual(self.problems(), [])

    def test_percent_encoded_dot_segments_cannot_escape_site(self):
        outside = self.site.parent / "blog-src" / "package.json"
        outside.parent.mkdir(parents=True, exist_ok=True)
        outside.write_text("{}", encoding="utf-8")
        for ref in ("https://guifav.dev/../blog-src/package.json", "/%2e%2e/blog-src/package.json", "/%2E%2E/blog-src/package.json"):
            self.w("index.html", page(f'<a href="{ref}">x</a>', robots=""))
            self.assertTrue(any("quebrado" in p for p in self.problems()), ref)

    def body(self, html: str) -> None:
        self.w("blog/um-post/index.html", page(
            f'<article><div class="post-body">{html}</div></article><a href="/blog/">b</a>'))

    def test_post_body_rejects_script_handlers_and_active_urls(self):
        for bad in (
            "<script>alert(1)</script>",
            '<img src="/blog/img/um-post/a.webp" onerror="x()">',
            '<a href="javascript:alert(1)">x</a>',
            '<a href="JavaScript:alert(1)">x</a>',
            '<img src="data:image/svg+xml,&lt;svg&gt;">',
            '<iframe src="https://evil.example/x"></iframe>',
            '<object data="/x.swf"></object>',
        ):
            self.body(bad)
            self.assertTrue(any("conteúdo ativo" in p for p in self.problems()), bad)

    def test_whole_page_is_scanned_not_a_text_slice(self):
        # Um "</article>" escrito pelo post (ou num comentário) não encerra a verificação.
        for trick in ("<!-- </article> -->", "</article>"):
            self.body(f"<p>a</p>{trick}<script>window.x=1</script>")
            self.assertTrue(any("conteúdo ativo" in p for p in self.problems()), trick)

    def test_scripts_must_come_from_the_template(self):
        self.w("blog/um-post/index.html", page('<script data-site-script>ok()</script><a href="/blog/">b</a>'))
        self.assertEqual(self.problems(), [])
        self.w("blog/um-post/index.html", page('<script data-site-script type="application/ld+json">{}</script><script>x()</script><a href="/blog/">b</a>'))
        self.assertTrue(any("<script> fora do template" in p for p in self.problems()))

    def test_newsletter_form_from_template_is_allowed(self):
        # O formulário da #21 vem do template: marcado, POST e só para o Worker da newsletter.
        form = ('<form data-site-form method="post" action="https://insights.guifav.dev/subscribe">'
                '<input type="email" name="email"><button type="submit">Assinar</button></form>')
        self.w("blog/um-post/index.html", page(f'<main><article><aside class="subscribe">{form}</aside></article></main><a href="/blog/">b</a>'))
        self.assertEqual(self.problems(), [])

    def test_form_needs_marker_post_and_the_newsletter_action(self):
        for form in ('<form method="post" action="https://insights.guifav.dev/subscribe"></form>',
                     '<form data-site-form method="post" action="https://evil.example/subscribe"></form>',
                     '<form data-site-form method="get" action="https://insights.guifav.dev/subscribe"></form>',
                     '<form data-site-form method="post" action="https://insights.guifav.dev/subscribe" '
                     'formaction="https://evil.example/"></form>'):
            self.w("blog/um-post/index.html", page(f'<main>{form}</main><a href="/blog/">b</a>'))
            self.assertTrue(any("<form>" in p for p in self.problems()), form)

    def test_marked_form_inside_post_body_is_still_content(self):
        self.body('<div><form data-site-form method="post" action="https://insights.guifav.dev/subscribe"></form></div>')
        self.assertTrue(any("<form>" in p for p in self.problems()))

    def test_formaction_on_button_is_flagged(self):
        self.body('<button formaction="https://evil.example/">x</button>')
        self.assertTrue(any("formaction" in p for p in self.problems()))

    def test_srcdoc_is_active_even_with_allowed_src(self):
        self.body('<iframe src="https://www.youtube.com/embed/abc" srcdoc="&lt;script&gt;x()&lt;/script&gt;"></iframe>')
        self.assertTrue(any("srcdoc" in p for p in self.problems()))

    def test_spotify_for_creators_legacy_host_is_allowed(self):
        self.body('<iframe src="https://podcasters.spotify.com/pod/show/ia-aplicada/embed/episodes/Ep-e3184tj"></iframe>')
        self.assertEqual(self.problems(), [])

    def test_srcset_follows_html_syntax(self):
        self.w("blog/img/um-post/b.webp", "webp")
        self.body('<img src="/blog/img/um-post/a.webp" srcset="https://substackcdn.com/image/fetch/w_728,c_limit,f_auto/x.png 728w, '
                  'https://substackcdn.com/image/fetch/w_1456,c_limit/y.png 1456w">')
        self.assertEqual(self.problems(), [])
        self.body('<img src="/blog/img/um-post/a.webp" srcset="/blog/img/um-post/a.webp\t728w,\n/blog/img/um-post/b.webp 1456w">')
        self.assertEqual(self.problems(), [])
        self.body('<img src="/blog/img/um-post/a.webp" srcset="/blog/img/um-post/a.webp 728w, /blog/img/um-post/c.webp 1456w">')
        self.assertTrue(any("c.webp" in p for p in self.problems()))

    def test_percent_encoded_host_is_internal(self):
        self.w("index.html", page('<a href="//guifav%2Edev/blog/">x</a>', robots=""))
        self.assertTrue(any("aponta para o blog" in p for p in self.problems()))
        self.w("index.html", page('<a href="https://GUIFAV.dev./nao-existe/">x</a>', robots=""))
        self.assertTrue(any("nao-existe" in p for p in self.problems()))

    def test_trailing_dot_segment_resolves_to_directory(self):
        for ref in ("/portfolio/%2e", "/portfolio/.", "/portfolio/x/.."):
            self.w("index.html", page(f'<a href="{ref}">x</a>', robots=""))
            self.assertEqual(self.problems(), [], ref)

    def test_duplicate_attribute_first_one_wins_like_the_browser(self):
        self.body('<iframe src="javascript:parent.x=1" src="https://www.youtube.com/embed/abc"></iframe>')
        self.assertTrue(any("conteúdo ativo" in p for p in self.problems()))
        self.w("blog/um-post/index.html", page('<a href="/blog/">b</a>',
               robots='<meta name="robots" content="index, follow"><meta name="robots" content="noindex">'))
        self.assertEqual(self.problems(), [])
        self.w("blog/um-post/index.html", page('<a href="/blog/">b</a>',
               robots='<meta name="robots" content="index" content="noindex">'))
        self.assertTrue(any("noindex" in p for p in self.problems()))

    def test_whatwg_host_and_separator_quirks_are_internal(self):
        for ref in ("//guifav.dev\\blog/", "https://guifav\u3002dev/blog/", "https://guifav\uff0edev/blog/",
                    "https://guifav\uff61dev/blog/", "https://gui\tfav.dev/blog/", "https:\\\\guifav.dev\\blog\\",
                    "https://\uff47uifav.dev/blog/"):
            self.w("index.html", page(f'<a href="{ref}">x</a>', robots=""))
            self.assertTrue(any("aponta para o blog" in p for p in self.problems()), repr(ref))

    def test_htm_and_uppercase_html_files_are_checked(self):
        for name in ("blog/extra.HTML", "blog/extra.htm", "blog/extra.xhtml"):
            self.w(name, "<html><body><script>x()</script></body></html>")
            self.assertTrue(any("extra" in p and "noindex" in p for p in self.problems()), name)
            self.assertTrue(any("extra" in p and "conteúdo ativo" in p for p in self.problems()), name)
            (self.site / name).unlink()

    def test_svg_files_are_not_published_in_blog(self):
        self.w("blog/img/um-post/diagrama.svg", '<svg xmlns="http://www.w3.org/2000/svg"></svg>')
        self.assertTrue(any("diagrama.svg" in p for p in self.problems()))

    def test_marked_script_inside_main_is_still_content(self):
        self.w("blog/um-post/index.html", page(
            '<script data-site-script>tema()</script><main><article><div class="post-body">'
            '<script data-site-script>x()</script></div></article></main><script data-site-script>fim()</script>'
            '<a href="/blog/">b</a>'))
        self.assertTrue(any("dentro de <main>" in p for p in self.problems()))
        self.w("blog/um-post/index.html", page(
            '<script data-site-script>tema()</script><main><p>ok</p></main><script data-site-script>fim()</script><a href="/blog/">b</a>'))
        self.assertEqual(self.problems(), [])

    def test_whatwg_special_scheme_without_slashes_is_internal(self):
        # http:guifav.dev/x e http:/guifav.dev/x: o navegador recupera a autoridade (esquema especial).
        self.w("index.html", page('<a href="http:guifav.dev/blog/">x</a>', robots=""))
        self.assertTrue(any("aponta para o blog" in p for p in self.problems()))
        self.w("index.html", page('<a href="http:/guifav.dev/nao-existe/">x</a>', robots=""))
        self.assertTrue(any("nao-existe" in p for p in self.problems()))

    def test_whatwg_repeated_or_trailing_unicode_dots_in_host(self):
        for ref in ("https://guifav.dev\u3002.:443/blog/", "https://guifav.dev\u3002\u3002/blog/"):
            self.w("index.html", page(f'<a href="{ref}">x</a>', robots=""))
            self.assertTrue(any("aponta para o blog" in p for p in self.problems()), repr(ref))

    def test_iframe_host_comes_from_the_whatwg_parser(self):
        # A barra invertida encerra a autoridade: o host efetivo é example.org.
        self.body('<iframe src="https://example.org\\@www.youtube.com/embed/x"></iframe>')
        self.assertTrue(any("conteúdo ativo" in p and "iframe" in p for p in self.problems()))

    def test_fails_closed_without_node(self):
        old = os.environ.get("CHECK_SITE_NODE")
        os.environ["CHECK_SITE_NODE"] = "/nao/existe/node"
        try:
            with self.assertRaises(C.ResolverError):
                self.problems()
        finally:
            if old is None:
                del os.environ["CHECK_SITE_NODE"]
            else:
                os.environ["CHECK_SITE_NODE"] = old

    def test_resolver_output_must_be_well_formed(self):
        # Substituto do node: lê os pares e devolve uma lista do tamanho certo, com itens malformados
        # (ou uma saída que nem é JSON). Todos precisam falhar fechado, com ResolverError.
        fake = self.site.parent / "fake_node.py"
        cases = ["'nao-e-json'"] + [f"json.dumps([{item}] * len(pairs))" for item in (
            "{}", "False", "[]", "''", "{'protocol': 'https:', 'hostname': 1, 'pathname': '/'}",
            "{'protocol': 'https:', 'hostname': 'guifav.dev'}")]
        for out in cases:
            fake.write_text("#!/usr/bin/env python3\nimport json, sys\npairs = json.load(sys.stdin)\n"
                            f"print({out})\n", encoding="utf-8")
            fake.chmod(0o755)
            old = os.environ.get("CHECK_SITE_NODE")
            os.environ["CHECK_SITE_NODE"] = str(fake)
            try:
                with self.assertRaises(C.ResolverError, msg=out):
                    self.problems()
            finally:
                if old is None:
                    del os.environ["CHECK_SITE_NODE"]
                else:
                    os.environ["CHECK_SITE_NODE"] = old

    def test_video_poster_must_exist(self):
        self.body('<video controls poster="/blog/img/um-post/nao-existe.webp"></video>')
        self.assertTrue(any("nao-existe.webp" in p for p in self.problems()))
        self.body('<video controls poster="/blog/img/um-post/a.webp"></video>')
        self.assertEqual(self.problems(), [])

    def test_blog_only_publishes_html_xml_and_listed_media(self):
        for name in ("blog/app.js", "blog/x.css", "blog/img/um-post/d.svg", "blog/img/um-post/f.ico", "blog/x.HTML", "blog/dados.json"):
            self.w(name, "x")
            self.assertTrue(any(name in p and "fora da lista" in p for p in self.problems()), name)
            (self.site / name).unlink()
        for name in ("blog/img/um-post/b.png", "blog/img/um-post/c.JPG", "blog/video/v.mp4", "blog/video/v.webm"):
            self.w(name, "x")
            self.assertFalse(any(name in p for p in self.problems()), name)
            (self.site / name).unlink()

    def test_post_body_allows_regular_markup_and_allowed_iframes(self):
        self.body('<figure><img src="/blog/img/um-post/a.webp" alt="x"><figcaption>c</figcaption></figure>'
                  '<pre><code>x</code></pre><table><tr><td>1</td></tr></table>'
                  '<iframe src="https://open.spotify.com/embed/episode/abc"></iframe>'
                  '<iframe src="https://www.youtube-nocookie.com/embed/abc"></iframe>')
        self.assertEqual(self.problems(), [])

    def test_javascript_url_anywhere_in_blog_is_flagged(self):
        self.w("blog/index.html", page('<a href="/blog/um-post/">um</a><a href=" javascript:x()">j</a>'))
        self.assertTrue(any("conteúdo ativo" in p for p in self.problems()))

    def test_noindex_must_be_a_whole_directive(self):
        self.w("blog/um-post/index.html", page('<a href="/blog/">b</a>', robots='<meta name="robots" content="not-noindex, follow">'))
        self.assertTrue(any("noindex" in p for p in self.problems()))
        self.w("blog/um-post/index.html", page('<a href="/blog/">b</a>', robots='<meta name="robots" content="NONE">'))
        self.assertEqual(self.problems(), [])

    def test_pre_cutover_404_cannot_link_to_blog(self):
        self.w("404.html", page('<a href="/blog/">blog</a>', robots=""))
        self.assertTrue(any("aponta para o blog" in p for p in self.problems(pre_cutover=True)))

    def test_invalid_rss_xml(self):
        self.w("blog/rss.xml", "<rss><channel>")
        self.assertTrue(any("rss.xml" in p for p in self.problems()))

    def test_sitemap_loc_must_exist(self):
        self.w("blog/sitemap.xml", SITEMAP.format("<url><loc>https://guifav.dev/blog/fantasma/</loc></url>"))
        self.assertTrue(any("fantasma" in p for p in self.problems()))

    def test_sitemap_loc_must_be_on_blog(self):
        self.w("blog/sitemap.xml", SITEMAP.format("<url><loc>https://outro.dev/blog/um-post/</loc></url>"))
        self.assertTrue(any("outro.dev" in p for p in self.problems()))

    def test_sitemap_loc_must_be_https_and_under_blog(self):
        # Página que existe no site, mas fora do blog, e URL do blog em http: as duas são erro.
        for loc in ("https://guifav.dev/portfolio/", "http://guifav.dev/blog/um-post/"):
            self.w("blog/sitemap.xml", SITEMAP.format(f"<url><loc>{loc}</loc></url>"))
            self.assertTrue(any("fora do blog" in p and loc in p for p in self.problems()), loc)

    def test_every_post_needs_a_published_page(self):
        (self.posts / "outro.md").write_text("---\ntitle: y\nslug: outro\n---\n", encoding="utf-8")
        self.assertTrue(any("sem página publicada" in p and "outro" in p for p in self.problems()))

    def test_duplicate_slug_in_posts_is_flagged(self):
        (self.posts / "copia.md").write_text("---\ntitle: y\nslug: um-post\n---\n", encoding="utf-8")
        self.assertTrue(any("slug duplicado" in p for p in self.problems()))

    def test_sitemap_excludes_index_false_posts(self):
        (self.posts / "um-post.md").write_text("---\ntitle: x\nslug: um-post\nindex: false\n---\n", encoding="utf-8")
        self.assertTrue(any("index: false" in p for p in self.problems()))

    def test_private_newsletter_pages_need_noindex_in_both_modes(self):
        # Páginas da newsletter (#21): sempre noindex e fora do sitemap, também depois do cutover.
        for name in sorted(C.BLOG_PRIVATE_PAGES):
            self.w(f"blog/{name}/index.html", page('<a href="/blog/">b</a>', robots=ROBOTS_INDEX))
            for pre in (True, False):
                self.assertTrue(any(name in p and "noindex" in p for p in self.problems(pre_cutover=pre)), (name, pre))
            self.w(f"blog/{name}/index.html", page('<a href="/blog/">b</a>'))
            self.assertEqual(self.problems(pre_cutover=False), [], name)

    def test_private_newsletter_pages_cannot_be_in_sitemap(self):
        self.w("blog/assinar/index.html", page('<a href="/blog/">b</a>'))
        self.w("blog/sitemap.xml", SITEMAP.format("<url><loc>https://guifav.dev/blog/assinar/</loc></url>"))
        for pre in (True, False):
            self.assertTrue(any("sitemap" in p and "assinar" in p for p in self.problems(pre_cutover=pre)), pre)

    def test_private_pages_set_is_pinned(self):
        self.assertEqual(C.BLOG_PRIVATE_PAGES, frozenset({"assinar", "inscricao-enviada", "inscricao-confirmada", "inscricao-invalida", "privacidade"}))

    def test_index_false_post_with_noindex_after_cutover_is_required(self):
        (self.posts / "um-post.md").write_text("---\ntitle: x\nslug: um-post\nindex: false\n---\n", encoding="utf-8")
        self.w("blog/sitemap.xml", SITEMAP.format("<url><loc>https://guifav.dev/blog/</loc></url>"))
        self.w("blog/um-post/index.html", page('<a href="/blog/">b</a>', robots=ROBOTS_INDEX))
        self.assertTrue(any("index: false" in p for p in self.problems(pre_cutover=False)))


class FrontmatterTest(unittest.TestCase):
    def test_reads_slug_and_index(self):
        fm = C.frontmatter("---\ntitle: \"a: b\"\nslug: meu-post\nindex: false\n---\ncorpo\nindex: true\n")
        self.assertEqual(fm["slug"], "meu-post")
        self.assertEqual(fm["index"], "false")

    def test_yaml_comments_and_quotes(self):
        fm = C.frontmatter("---\nindex: false # fora do índice\nslug: 'meu-post'  # x\ntitle: \"a # b\"\n---\n")
        self.assertEqual(fm["index"], "false")
        self.assertEqual(fm["slug"], "meu-post")
        self.assertEqual(fm["title"], "a # b")

    def test_no_frontmatter(self):
        self.assertEqual(C.frontmatter("sem cabeçalho"), {})


class PreCutoverFlagTest(unittest.TestCase):
    def test_reads_flag_from_consts(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "consts.ts"
            p.write_text("export const PRE_CUTOVER_NOINDEX = true;\n", encoding="utf-8")
            self.assertTrue(C.read_pre_cutover(p))
            p.write_text("export const PRE_CUTOVER_NOINDEX = false;\n", encoding="utf-8")
            self.assertFalse(C.read_pre_cutover(p))
            p.write_text("export const OUTRA = true;\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                C.read_pre_cutover(p)


if __name__ == "__main__":
    unittest.main(verbosity=2)

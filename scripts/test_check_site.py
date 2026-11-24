"""Testes do verificador do site montado (scripts/check_site.py).

Rodar: python3 scripts/test_check_site.py
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import re
import tempfile
import unittest
from pathlib import Path

import check_site as C

ROBOTS_NOINDEX = '<meta name="robots" content="noindex, follow">'
ROBOTS_INDEX = '<meta name="robots" content="index, follow">'


# Títulos dos testes que não tratam dessa regra: a home tem texto exato; o post vai sem o sufixo (#103); as demais
# páginas usam o sufixo. TitleAndGroupTest testa a regra com títulos próprios.
FIXTURE_TITLE = "<title>Guilherme Favaron | Teste | Guilherme Favaron</title>"
HOME_FIXTURE_TITLE = "<title>Guilherme Favaron | Builder e CTO</title>"
POST_FIXTURE_TITLE = "<title>Teste</title>"


def page(body: str = "", robots: str = ROBOTS_NOINDEX, title: str = FIXTURE_TITLE) -> str:
    return f"<!DOCTYPE html><html><head>{title}{robots}</head><body>{body}</body></html>"


SITEMAP = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{}</urlset>'
)
SITEMAP_INDEX = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{}</sitemapindex>'
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
        self.w("robots.txt", "User-agent: *\nDisallow: /tools/\n\nSitemap: https://guifav.dev/sitemap.xml\n")
        self.w("sitemap.xml", SITEMAP_INDEX.format(
            "<sitemap><loc>https://guifav.dev/sitemap-site.xml</loc></sitemap>"
            "<sitemap><loc>https://guifav.dev/blog/sitemap.xml</loc></sitemap>"
        ))
        self.w("sitemap-site.xml", SITEMAP.format(
            "<url><loc>https://guifav.dev/</loc><lastmod>2026-11-12T10:00:00-03:00</lastmod></url>"
            "<url><loc>https://guifav.dev/portfolio/</loc><lastmod>2026-11-12T10:00:00-03:00</lastmod></url>"
        ))

    def tearDown(self):
        self.tmp.cleanup()

    def w(self, rel: str, text: str) -> None:
        if rel == "index.html":
            text = text.replace(FIXTURE_TITLE, HOME_FIXTURE_TITLE)
        elif C.expected_content_group(rel) == "insights-artigo":
            text = text.replace(FIXTURE_TITLE, POST_FIXTURE_TITLE)
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
            # A confirmação leva o marcador do sign_up (issue #60), que o check_tracking exige ali.
            body = '<a href="/blog/">b</a>' + ('<article data-ga-event="sign_up"></article>' if name == "inscricao-confirmada" else "")
            self.w(f"blog/{name}/index.html", page(body, robots=ROBOTS_INDEX))
            for pre in (True, False):
                self.assertTrue(any(name in p and "noindex" in p for p in self.problems(pre_cutover=pre)), (name, pre))
            self.w(f"blog/{name}/index.html", page(body))
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


class RootSitemapTest(Base):
    """Índice /sitemap.xml, /sitemap-site.xml completo e robots.txt citando só o índice."""

    def has(self, *words: str) -> bool:
        return any(all(w in p for w in words) for p in self.problems(pre_cutover=False))

    def test_base_is_clean(self):
        self.assertEqual(self.problems(pre_cutover=False), [])

    def test_blog_sitemap_cannot_list_pagination(self):
        self.w("blog/pagina/2/index.html", page("x", robots=ROBOTS_INDEX))
        self.w("blog/sitemap.xml", SITEMAP.format("<url><loc>https://guifav.dev/blog/pagina/2/</loc></url>"))
        self.assertTrue(self.has("paginação", "/blog/pagina/2/"))

    def test_index_must_exist_and_be_an_index(self):
        (self.site / "sitemap.xml").unlink()
        self.assertTrue(self.has("sitemap.xml inválido"))
        self.w("sitemap.xml", SITEMAP.format("<url><loc>https://guifav.dev/</loc></url>"))
        self.assertTrue(self.has("sitemap.xml", "sitemapindex"))

    def test_index_must_cite_exactly_both_sitemaps(self):
        for body in ("<sitemap><loc>https://guifav.dev/sitemap-site.xml</loc></sitemap>",
                     "<sitemap><loc>https://guifav.dev/sitemap-site.xml</loc></sitemap>"
                     "<sitemap><loc>https://guifav.dev/blog/sitemap.xml</loc></sitemap>"
                     "<sitemap><loc>https://outro.dev/sitemap.xml</loc></sitemap>"):
            with self.subTest(body=body):
                self.w("sitemap.xml", SITEMAP_INDEX.format(body))
                self.assertTrue(self.has("o índice precisa citar exatamente"))

    def test_site_sitemap_must_be_urlset(self):
        self.w("sitemap-site.xml", SITEMAP_INDEX.format(""))
        self.assertTrue(self.has("sitemap-site.xml", "urlset"))
        (self.site / "sitemap-site.xml").unlink()
        self.assertTrue(self.has("sitemap-site.xml inválido"))

    def test_new_root_page_must_enter_site_sitemap(self):
        self.w("nova/index.html", page("x", robots=""))
        self.assertTrue(self.has("falta página indexável", "https://guifav.dev/nova/"))

    def test_excluded_pages_must_stay_out(self):
        cases = {
            "404.html": ("404.html", page("x", robots="")),
            "disallow": ("tools/x.html", page("x", robots="")),
            "noindex": ("rascunho/index.html", page("x", robots=ROBOTS_NOINDEX)),
        }
        url = {"404.html": "https://guifav.dev/404.html", "disallow": "https://guifav.dev/tools/x.html",
               "noindex": "https://guifav.dev/rascunho/"}
        for name, (rel, html) in cases.items():
            with self.subTest(name=name):
                base_sitemap = (self.site / "sitemap-site.xml").read_text(encoding="utf-8")
                self.w(rel, html)
                self.assertFalse(self.has("falta página indexável"), name)
                self.w("sitemap-site.xml", SITEMAP.format(
                    "<url><loc>https://guifav.dev/</loc><lastmod>2026-11-12T10:00:00-03:00</lastmod></url>"
                    "<url><loc>https://guifav.dev/portfolio/</loc><lastmod>2026-11-12T10:00:00-03:00</lastmod></url>"
                    f"<url><loc>{url[name]}</loc><lastmod>2026-11-12T10:00:00-03:00</lastmod></url>"))
                self.assertTrue(self.has("não deve ser indexado", url[name]), name)
                self.w("sitemap-site.xml", base_sitemap)
                if rel != "404.html":
                    (self.site / rel).unlink()

    def test_canonical_pointing_elsewhere_is_flagged(self):
        for href in ("https://guifav.github.io/portfolio/", "https://guifav.dev/outra/", "http://guifav.dev/portfolio/"):
            with self.subTest(href=href):
                self.w("portfolio/index.html", page(f'<link rel="canonical" href="{href}"><a href="/">h</a>', robots=""))
                self.assertTrue(self.has("portfolio/index.html", "canonical aponta para outra URL"))

    def test_self_canonical_is_fine(self):
        self.w("portfolio/index.html", page('<link rel="canonical" href="https://guifav.dev/portfolio/"><a href="/">h</a>', robots=""))
        self.w("index.html", page('<link rel="canonical" href="https://guifav.dev"><a href="/portfolio/">p</a><img src="/assets/a.png">', robots=""))
        self.assertEqual(self.problems(pre_cutover=False), [])

    def test_lastmod_accepts_fractions_and_utc(self):
        for lm in ("2026-11-12T10:00:00.123Z", "2026-11-12T10:00:00+00:00", "2026-11-12T10:00:00.5-03:00"):
            with self.subTest(lm=lm):
                self.w("sitemap-site.xml", SITEMAP.format(
                    f"<url><loc>https://guifav.dev/</loc><lastmod>{lm}</lastmod></url>"
                    "<url><loc>https://guifav.dev/portfolio/</loc><lastmod>2026-11-12T10:00:00Z</lastmod></url>"))
                self.assertEqual(self.problems(pre_cutover=False), [])

    def test_blog_sitemap_must_list_every_indexable_post(self):
        self.w("blog/sitemap.xml", SITEMAP.format("<url><loc>https://guifav.dev/blog/</loc></url>"))
        self.assertTrue(self.has("falta página indexável do blog", "https://guifav.dev/blog/um-post/"))
        self.w("blog/sitemap.xml", SITEMAP.format("<url><loc>https://guifav.dev/blog/um-post/</loc></url>"))
        self.assertTrue(self.has("falta página indexável do blog", "https://guifav.dev/blog/"))

    def test_blog_sitemap_noindex_post_not_required(self):
        (self.posts / "um-post.md").write_text("---\ntitle: x\nslug: um-post\nindex: false\n---\n", encoding="utf-8")
        self.w("blog/sitemap.xml", SITEMAP.format("<url><loc>https://guifav.dev/blog/</loc></url>"))
        self.assertFalse(self.has("falta página indexável do blog"))

    def test_blog_sitemap_duplicate(self):
        self.w("blog/sitemap.xml", SITEMAP.format(
            "<url><loc>https://guifav.dev/blog/</loc></url>" + "<url><loc>https://guifav.dev/blog/um-post/</loc></url>" * 2))
        self.assertTrue(self.has("URL repetida no sitemap do blog"))

    def test_lastmod_required_and_w3c(self):
        for lm in ("", "<lastmod>12/11/2026</lastmod>", "<lastmod>2026-11-12</lastmod>",
                   "<lastmod>2026-13-40T25:61:61+99:99</lastmod>", "<lastmod>2026-02-30T10:00:00Z</lastmod>",
                   "<lastmod>2026-11-12T10:00:00+00:99</lastmod>", "<lastmod>2026-11-12T10:00:00+24:00</lastmod>"):
            with self.subTest(lm=lm):
                self.w("sitemap-site.xml", SITEMAP.format(
                    f"<url><loc>https://guifav.dev/</loc>{lm}</url>"
                    "<url><loc>https://guifav.dev/portfolio/</loc><lastmod>2026-11-12T10:00:00Z</lastmod></url>"))
                self.assertTrue(self.has("lastmod", "https://guifav.dev/"))

    def test_duplicate_loc(self):
        self.w("sitemap-site.xml", SITEMAP.format(
            "<url><loc>https://guifav.dev/</loc><lastmod>2026-11-12T10:00:00Z</lastmod></url>" * 2
            + "<url><loc>https://guifav.dev/portfolio/</loc><lastmod>2026-11-12T10:00:00Z</lastmod></url>"))
        self.assertTrue(self.has("URL repetida"))

    def test_robots_must_cite_only_the_index(self):
        for txt in ("User-agent: *\nDisallow:\n",
                    "User-agent: *\nSitemap: https://guifav.dev/sitemap.xml\nSitemap: https://guifav.dev/blog/sitemap.xml\n",
                    "User-agent: *\nSitemap: https://guifav.dev/blog/sitemap.xml\n"):
            with self.subTest(txt=txt):
                self.w("robots.txt", txt)
                self.assertTrue(self.has("robots.txt", "só o índice"))
        (self.site / "robots.txt").unlink()
        self.assertTrue(self.has("robots.txt", "só o índice"))


class ClarityTest(Base):
    """Toda página com o GA4 do site também tem o Microsoft Clarity."""

    GA = '<script async src="https://www.googletagmanager.com/gtag/js?id=G-K7Y87QNCX8"></script>'
    CL = '<script>(function(c,l,a,r,i,t,y){t.src="https://www.clarity.ms/tag/"+i;})(window,document,"clarity","script","zcs8f7r329");</script>'

    def flagged(self) -> bool:
        return any("tem o GA4 mas não o Microsoft Clarity" in p for p in self.problems(pre_cutover=False))

    def test_ga_without_clarity_is_flagged(self):
        self.w("index.html", page(self.GA + '<a href="/portfolio/">p</a><img src="/assets/a.png">', robots=""))
        self.assertTrue(self.flagged())

    def test_ga_with_clarity_ok(self):
        self.w("index.html", page(self.GA + self.CL + '<a href="/portfolio/">p</a><img src="/assets/a.png">', robots=""))
        self.assertFalse(self.flagged())

    def test_clarity_with_other_project_is_flagged(self):
        self.w("index.html", page(self.GA + self.CL.replace("zcs8f7r329", "outro") + '<a href="/portfolio/">p</a><img src="/assets/a.png">', robots=""))
        self.assertTrue(self.flagged())

    def test_page_without_ga_needs_nothing(self):
        self.assertFalse(self.flagged())

    def test_commented_or_inert_clarity_is_flagged(self):
        # r1 (Astra P3): snippet comentado, em JSON-LD ou só no texto não roda.
        for name, cl in {"comentado": "<!-- " + self.CL + " -->",
                         "json": self.CL.replace("<script>", '<script type="application/ld+json">'),
                         "texto": "<p>" + self.CL.replace("<script>", "").replace("</script>", "") + "</p>"}.items():
            with self.subTest(name=name):
                self.w("index.html", page(self.GA + cl + '<a href="/portfolio/">p</a><img src="/assets/a.png">', robots=""))
                self.assertTrue(self.flagged(), name)

    def test_typed_javascript_clarity_ok(self):
        self.w("index.html", page(self.GA + self.CL.replace("<script>", '<script type="text/javascript" data-site-script>')
                                  + '<a href="/portfolio/">p</a><img src="/assets/a.png">', robots=""))
        self.assertFalse(self.flagged())


class TitleAndGroupTest(unittest.TestCase):
    """Títulos "Página | Guilherme Favaron" (home com o nome primeiro, post sem o nome) e content_group do GA4 por tipo."""

    GA = '<script async src="https://www.googletagmanager.com/gtag/js?id=G-K7Y87QNCX8"></script>'

    @staticmethod
    def doc(title="", head=""):
        return f"<!DOCTYPE html><html><head>{title}{head}</head><body></body></html>"

    def check(self, rel, html):
        return C.check_titles_and_groups(rel, html)

    def cfg(self, group_js):
        return f"<script>window.dataLayer=[];function gtag(){{}};gtag('js',new Date());gtag('config','G-K7Y87QNCX8'{group_js});</script>"

    def test_title_forms(self):
        cases = [("index.html", "Guilherme Favaron | Builder e CTO", True),
                 ("index.html", "Guilherme Favaron, builder e CTO", False),
                 ("index.html", "Builder e CTO | Guilherme Favaron", False),
                 ("index.html", "Guilherme Favaron | Subtítulo errado", False),
                 ("portfolio/index.html", "Portfólio | Guilherme Favaron", True),
                 ("portfolio/index.html", "Portfólio, Guilherme Favaron", False),
                 ("llm-ranking/index.html", "Ranking: Qual é o Melhor LLM?", False),
                 # Post (#103, Gui em 23/11): sem o nome do site, nem no fim nem no começo.
                 ("blog/um-post/index.html", "Um post", True),
                 ("blog/um-post/index.html", "Um post sobre Guilherme Favaron", True),
                 ("blog/um-post/index.html", "Um post | Guilherme Favaron", False),
                 ("blog/um-post/index.html", "Um post | Guilherme Favaron ", False),  # espaço em volta não conta
                 ("blog/um-post/index.html", "Guilherme Favaron | Um post", False),
                 ("blog/um-post/index.html", "", False),
                 # As outras páginas do blog mantêm o sufixo.
                 ("blog/index.html", "Insights | Guilherme Favaron", True),
                 ("blog/index.html", "Insights", False),
                 ("blog/pagina/2/index.html", "Insights, página 2 | Guilherme Favaron", True),
                 ("blog/assinar/index.html", "Assine os Expert Insights", False),
                 ("404.html", "Página não encontrada | Guilherme Favaron", True)]
        for rel, title, ok in cases:
            with self.subTest(rel=rel, title=title):
                self.assertEqual(self.check(rel, self.doc(f"<title>{title}</title>")) == [], ok)

    def test_entities_are_decoded(self):
        self.assertEqual(self.check("portfolio/index.html", self.doc("<title>A &amp; B &#124; Guilherme Favaron</title>")), [])
        self.assertTrue(self.check("blog/x/index.html", self.doc("<title>A &amp; B &#124; Guilherme Favaron</title>")))

    def test_missing_or_commented_title(self):
        fake_title = '<script type="application/ld+json">{"x":"<title>X | Guilherme Favaron</title>"}</script>'
        for html in (self.doc(), self.doc("<!-- <title>X | Guilherme Favaron</title> -->"), self.doc(fake_title)):
            with self.subTest(html=html):
                self.assertTrue(any("sem <title>" in p for p in self.check("blog/x/index.html", html)))

    def test_mockups_are_exempt(self):
        self.assertEqual(self.check("portfolio/mockups/crm.html", self.doc("<title>Pessoas · CRM</title>")), [])
        self.assertEqual(self.check("portfolio/mockups/crm.html", self.doc()), [])

    def test_og_and_twitter_must_match_title(self):
        t = "<title>Portfólio | Guilherme Favaron</title>"
        ok = '<meta property="og:title" content="Portfólio | Guilherme Favaron"><meta name="twitter:title" content="Portfólio | Guilherme Favaron">'
        self.assertEqual(self.check("portfolio/index.html", self.doc(t, ok)), [])
        for prop in ("og:title", "twitter:title"):
            with self.subTest(prop=prop):
                bad = ok.replace(f'"{prop}" content="Portfólio | Guilherme Favaron"', f'"{prop}" content="Portfólio, Guilherme Favaron"')
                self.assertTrue(any(prop in p for p in self.check("portfolio/index.html", self.doc(t, bad))))

    def test_social_meta_ignores_comments_and_checks_every_live_tag(self):
        t = "<title>Portfólio | Guilherme Favaron</title>"
        good = '<meta property="og:title" content="Portfólio | Guilherme Favaron">'
        bad = '<meta property="og:title" content="Título antigo">'
        self.assertTrue(self.check("portfolio/index.html", self.doc(t, f"<!-- {good} -->{bad}")))
        self.assertTrue(self.check("portfolio/index.html", self.doc(t, good + bad)))
        self.assertEqual(self.check("portfolio/index.html", self.doc(t, f"<!-- {bad} -->{good}")), [])

    def test_expected_groups(self):
        E = C.expected_content_group
        for rel, g in (("index.html", "home"), ("portfolio/index.html", "portfolio"), ("blog/index.html", "insights-lista"),
                       ("blog/pagina/2/index.html", "insights-lista"), ("blog/pagina/12/index.html", "insights-lista"),
                       ("blog/assinar/index.html", "expert-insights"), ("blog/privacidade/index.html", "expert-insights"),
                       ("blog/inscricao-enviada/index.html", "expert-insights"),
                       ("blog/inscricao-confirmada/index.html", "expert-insights"),
                       ("blog/inscricao-invalida/index.html", "expert-insights"),
                       ("blog/o-exame-virou-conspiracao/index.html", "insights-artigo"),
                       ("planilha-custos-ia/index.html", "materiais"), ("mapa-decisao-ia/enviado/index.html", "materiais"),
                       ("planilha-custos-ia/x/index.html", None),
                       ("llm-ranking/index.html", None), ("blog/pagina/x/index.html", None),
                       ("blog/a/b/index.html", None),
                       ("blog/tema/ia-sobre-dados-da-empresa/index.html", "insights-lista"),
                       ("blog/tema/a/b/index.html", None), ("blog/tema/Maiuscula/index.html", None)):
            with self.subTest(rel=rel):
                self.assertEqual(E(rel), g)
        self.assertEqual(set(C.CONTENT_GROUPS), {"home", "portfolio", "insights-lista", "insights-artigo", "expert-insights", "materiais"})

    def test_group_literal_and_define_vars(self):
        title = "<title>Guilherme Favaron | Builder e CTO</title>"
        self.assertEqual(self.check("index.html", self.doc(title, self.GA + self.cfg(",{content_group:'home'}"))), [])
        astro = ('<script>(function(){const GA4_ID = "G-K7Y87QNCX8";\nconst CONTENT_GROUP = "insights-artigo";\n'
                 "gtag('config', GA4_ID, { content_group: CONTENT_GROUP });})();</script>")
        self.assertEqual(self.check("blog/um-post/index.html", self.doc("<title>X</title>", self.GA + astro)), [])

    def test_group_missing_wrong_duplicated_or_inert(self):
        title = "<title>Guilherme Favaron | Builder e CTO</title>"
        bad = {"sem grupo": self.cfg(""),
               "grupo errado": self.cfg(",{content_group:'portfolio'}"),
               "grupo inventado": self.cfg(",{content_group:'casa'}"),
               "duas vezes": self.cfg(",{content_group:'home'}") + self.cfg(",{content_group:'home'}"),
               "comentado": "<!-- " + self.cfg(",{content_group:'home'}") + " -->",
               "json-ld": self.cfg(",{content_group:'home'}").replace("<script>", '<script type="application/ld+json">'),
               "json-ld com data-type": self.cfg(",{content_group:'home'}").replace("<script>", '<script data-type="text/javascript" type="application/ld+json">'),
               "fora do config": "<script>gtag('event','x',{content_group:'home'});</script>",
               "variável sem valor": "<script>gtag('config','G-K7Y87QNCX8',{content_group:GRUPO});</script>",
               # nome de variável igual ao grupo esperado, sem const: no navegador é ReferenceError, não 'home'
               "variável homônima": "<script>gtag('config','G-K7Y87QNCX8',{content_group:home});</script>"}
        for name, js in bad.items():
            with self.subTest(name=name):
                self.assertTrue(any("content_group" in p for p in self.check("index.html", self.doc(title, self.GA + js))), name)

    def test_group_must_belong_to_every_live_config_call(self):
        title = "<title>Guilherme Favaron | Builder e CTO</title>"
        valid = "gtag('config','G-K7Y87QNCX8',{content_group:'home'});"
        bad = {
            "grupo só no evento": "gtag('config','G-K7Y87QNCX8');gtag('event','x',{content_group:'home'});",
            "segundo config sem grupo": valid + "gtag('config','G-K7Y87QNCX8');",
            "config em comentário de linha": "// " + valid,
            "config em comentário de bloco": "/* " + valid + " */",
            "grupo em comentário": "gtag('config','G-K7Y87QNCX8'); // content_group:'home'",
            "grupo em comentário no objeto": "gtag('config','G-K7Y87QNCX8',{/* content_group:'home' */});",
            "const em comentário": "gtag('config','G-K7Y87QNCX8',{content_group:CONTENT_GROUP}); /* const CONTENT_GROUP=\"home\"; */",
            "comentário antes do config sem grupo": valid + "gtag(/* init */'config','G-K7Y87QNCX8');",
            "comentário depois do config sem grupo": valid + "gtag('config' /* init */,'G-K7Y87QNCX8');",
            "grupo aninhado": "gtag('config','G-K7Y87QNCX8',{custom_map:{content_group:'home'}});",
            "código em string": 'const exemplo = "' + valid + '";',
        }
        for name, js in bad.items():
            with self.subTest(name=name):
                html = self.doc(title, self.GA + "<script>" + js + "</script>")
                self.assertTrue(any("content_group" in p for p in self.check("index.html", html)), name)
        with_event = valid + "gtag('event','x',{content_group:'portfolio'});"
        self.assertEqual(self.check("index.html", self.doc(title, self.GA + "<script>" + with_event + "</script>")), [])
        commented_ok = "gtag(/* init */'config' /* id */,'G-K7Y87QNCX8',{send_page_view:true,/* x */content_group:'home'});"
        self.assertEqual(self.check("index.html", self.doc(title, self.GA + "<script>" + commented_ok + "</script>")), [])

    def test_template_and_noscript_do_not_supply_group(self):
        title = "<title>Guilherme Favaron | Builder e CTO</title>"
        for container in ("template", "noscript"):
            with self.subTest(container=container):
                inert = f"<{container}>{self.cfg(',{content_group:\'home\'}')}</{container}>"
                self.assertTrue(self.check("index.html", self.doc(title, self.GA + inert)))

    def test_ga_page_outside_the_map_is_flagged(self):
        html = self.doc("<title>LLM Ranking | Guilherme Favaron</title>", self.GA + self.cfg(",{content_group:'home'}"))
        self.assertTrue(any("sem grupo de conteúdo previsto" in p for p in self.check("llm-ranking/index.html", html)))

    def test_no_ga_no_group_needed(self):
        self.assertEqual(self.check("llm-ranking/index.html", self.doc("<title>LLM Ranking | Guilherme Favaron</title>")), [])

    def test_check_runs_the_rule_on_every_page(self):
        with tempfile.TemporaryDirectory() as d:
            site = Path(d) / "_site"; (site / "blog" / "um").mkdir(parents=True)
            (site / "blog" / "um" / "index.html").write_text(self.doc("<title>Um | Guilherme Favaron</title>"), encoding="utf-8")
            (site / "blog" / "index.html").write_text(self.doc("<title>Sem barra</title>"), encoding="utf-8")
            posts = Path(d) / "posts"; posts.mkdir()
            problems = C.check(site, posts, pre_cutover=False)
            self.assertTrue(any("blog/index.html: o título precisa terminar em" in p for p in problems))
            self.assertTrue(any("blog/um/index.html: o título do post vai sem o nome do site" in p for p in problems))


class TitleAndGroupSourcesTest(unittest.TestCase):
    """Fontes: Base.astro exige contentGroup e cada página do blog passa o seu; consts.ts e check_site têm os mesmos grupos."""

    REPO = Path(__file__).resolve().parent.parent

    def read(self, rel):
        return (self.REPO / rel).read_text(encoding="utf-8")

    def test_base_requires_group_and_sends_it(self):
        base = self.read("blog-src/src/layouts/Base.astro")
        self.assertIn("  contentGroup: ContentGroup;\n", base)  # obrigatório: sem "?"
        self.assertIn("define:vars={{ GA4_ID, CONTENT_GROUP: contentGroup }}", base)
        self.assertIn("gtag('config', GA4_ID, { content_group: CONTENT_GROUP });", base)

    def test_each_page_passes_its_group(self):
        for rel, g in (("blog-src/src/layouts/PostList.astro", "insights-lista"),
                       ("blog-src/src/pages/[slug].astro", "insights-artigo"),
                       ("blog-src/src/pages/assinar.astro", "expert-insights"),
                       ("blog-src/src/pages/privacidade.astro", "expert-insights"),
                       ("blog-src/src/pages/inscricao-enviada.astro", "expert-insights"),
                       ("blog-src/src/pages/inscricao-confirmada.astro", "expert-insights"),
                       ("blog-src/src/pages/inscricao-invalida.astro", "expert-insights")):
            with self.subTest(rel=rel):
                self.assertIn(f'contentGroup="{g}"', self.read(rel))

    def test_groups_match_consts(self):
        consts = self.read("blog-src/src/consts.ts")
        m = re.search(r"CONTENT_GROUPS = \[([^\]]*)\] as const", consts)
        self.assertEqual(tuple(re.findall(r"'([^']+)'", m.group(1))), C.CONTENT_GROUPS)


class ClaritySourcesTest(unittest.TestCase):
    """Fontes do repo: tag nas páginas com GA, formulários mascarados e a política de privacidade citando o Clarity."""

    REPO = Path(__file__).resolve().parent.parent

    def read(self, rel: str) -> str:
        return (self.REPO / rel).read_text(encoding="utf-8")

    def test_root_pages(self):
        for rel in ("index.html", "portfolio/index.html", *sorted(C.MATERIAL_PAGES)):
            with self.subTest(rel=rel):
                t = self.read(rel)
                self.assertIn(C.GA4_TAG, t)
                for m in C.CLARITY_MARKERS:
                    self.assertIn(m, t)

    def test_blog_layout_and_const(self):
        self.assertIn("export const CLARITY_ID = 'zcs8f7r329';", self.read("blog-src/src/consts.ts"))
        base = self.read("blog-src/src/layouts/Base.astro")
        self.assertIn("define:vars={{ CLARITY_ID }}", base)
        self.assertIn('"https://www.clarity.ms/tag/"+i', base)

    def test_forms_masked(self):
        self.assertIn('action="https://insights.guifav.dev/subscribe" data-clarity-mask="true"', self.read("index.html"))
        for m in C.MATERIALS:
            self.assertIn('data-ga-client-id data-clarity-mask="true"', self.read(f"{m}/index.html"))
        self.assertIn('data-clarity-mask="true"', self.read("blog-src/src/components/SubscribeForm.astro"))

    def test_privacy_mentions_clarity(self):
        t = self.read("blog-src/src/pages/privacidade.astro")
        self.assertIn("Microsoft Clarity", t)
        # r1 (Kimi e Astra P2): a política cita os cookies e o identificador, e não promete anonimato.
        for must in ("_clck", "_clsk", "identificador", "privacy.microsoft.com"):
            self.assertIn(must, t)
        self.assertNotIn("sem identificar quem visita", t)


class NamingSourcesTest(unittest.TestCase):
    """Textos ao leitor sem "newsletter" e sem "IA Aplicada" como nome da lista (Gui, 12/11/2026).

    Exceções: o card "Newsletter no LinkedIn: O CTO na Mesa" (nome do produto do LinkedIn), o canal "YouTube: IA
    Aplicada" e as frases que contam a mudança do Substack.
    """

    REPO = Path(__file__).resolve().parent.parent
    FILES = ("index.html", "portfolio/index.html", "404.html", "blog-src/src/layouts/Base.astro",
             "blog-src/src/layouts/PostList.astro", "blog-src/src/components/SubscribeForm.astro",
             "blog-src/src/pages/[slug].astro", "blog-src/src/pages/assinar.astro",
             "blog-src/src/pages/inscricao-enviada.astro", "blog-src/src/pages/inscricao-confirmada.astro",
             "blog-src/src/pages/inscricao-invalida.astro", "blog-src/src/pages/privacidade.astro",
             "blog-src/src/pages/rss.xml.ts", "workers/newsletter/src/email.ts", "workers/admin/src/index.ts")
    ALLOWED = ("Newsletter no LinkedIn: O CTO na Mesa", "LinkedIn newsletter: O CTO na Mesa", "YouTube: IA Aplicada",
               "assinava a IA Aplicada no Substack", "linkedin.com/newsletters/",
               # o que a fábrica de conteúdo produz (portfólio), não o nome da lista
               "vídeo, newsletters, e-mails", "cuts, newsletters, emails")

    @staticmethod
    def visible(text: str) -> str:
        """Tira comentários, identificadores e classes: sobra o que pode chegar ao leitor."""
        text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
        text = re.sub(r"^[ \t]*//[^\n]*", "", text, flags=re.M)  # só a linha do comentário
        # Só os identificadores internos conhecidos; "NEWSLETTER" solto em caixa alta é texto e tem de ser pego.
        text = re.sub(r"\bNEWSLETTER_(?:FORM_ENABLED|ENDPOINT|SUBSTACK_URL)\b|newsletter-box|workers/newsletter|class=\"[^\"]*\"",
                      "", text)
        return text

    def test_no_newsletter_or_ia_aplicada_as_list_name(self):
        for rel in self.FILES:
            with self.subTest(rel=rel):
                t = self.visible((self.REPO / rel).read_text(encoding="utf-8"))
                for ok in self.ALLOWED:
                    t = t.replace(ok, "")
                self.assertNotRegex(t, r"(?i)newsletter")
                self.assertNotIn("IA Aplicada", t)

    def test_visible_keeps_text_and_drops_only_code(self):
        v = self.visible
        for shown in ("<h1>NEWSLETTER</h1>", "<p>Assine a newsletter</p>", '<aside aria-label="Newsletter">',
                      "<title>Assinantes da newsletter IA Aplicada</title>"):
            with self.subTest(shown=shown):
                self.assertRegex(v(shown), r"(?i)newsletter")
        for code in ("{NEWSLETTER_FORM_ENABLED ? a : b}", "const NEWSLETTER_ENDPOINT = x;", '<div class="newsletter-box">',
                     "<!-- newsletter -->", "// Página da newsletter (issue #21)"):
            with self.subTest(code=code):
                self.assertNotRegex(v(code), r"(?i)newsletter")
        self.assertIn("<p>depois</p>", v("// newsletter\n<p>depois</p>"))  # o comentário não engole o resto

    def test_names_present(self):
        consts = (self.REPO / "blog-src/src/consts.ts").read_text(encoding="utf-8")
        self.assertIn("export const BLOG_NAME = 'Insights';", consts)
        self.assertIn("export const EMAIL_LIST_NAME = 'Expert Insights';", consts)
        home = (self.REPO / "index.html").read_text(encoding="utf-8")
        for s in ('data-i18n="nav_blog">Insights</a>', '"nav_blog": "Insights"', "<h3>Expert Insights</h3>",
                  '<a href="/blog/assinar/">Expert Insights</a>'):
            self.assertIn(s, home)
        self.assertIn('FROM = "Guilherme Favaron | Expert Insights <insights@guifav.dev>"',
                      (self.REPO / "workers/newsletter/wrangler.toml").read_text(encoding="utf-8"))
        admin = (self.REPO / "workers/admin/src/index.ts").read_text(encoding="utf-8")
        for s in ("<title>Assinantes | Expert Insights</title>", "<h1>Assinantes dos Expert Insights</h1>"):
            self.assertIn(s, admin)
        verify = (self.REPO / "blog-src/scripts/verify_import.py").read_text(encoding="utf-8")
        self.assertIn('FOOTER_LIST_NAME = "Expert Insights"', verify)
        base = (self.REPO / "blog-src/src/layouts/Base.astro").read_text(encoding="utf-8")
        self.assertIn("'assinar/')}>{EMAIL_LIST_NAME}</a>", base)  # o mesmo texto que o verify_import procura


class TrackingTest(unittest.TestCase):
    """Eventos próprios do GA4 (issue #60): track.js nas páginas com GA4, CTAs com posição e marcador do sign_up."""

    GA = '<script async src="https://www.googletagmanager.com/gtag/js?id=G-K7Y87QNCX8"></script>'
    TR = '<script src="/assets/track.js?v=1"></script>'

    def doc(self, body="", ga=True, track=1):
        head = (self.GA if ga else "")
        return f"<!DOCTYPE html><html><head>{head}</head><body>{body}{self.TR * track}</body></html>"

    def check(self, html, rel="index.html"):
        return C.check_tracking(rel, html)

    def test_track_once_on_every_ga_page_and_never_without_ga(self):
        self.assertEqual(self.check(self.doc()), [])
        self.assertEqual(self.check(self.doc(ga=False, track=0)), [])
        for name, html in {"sem": self.doc(track=0), "duas": self.doc(track=2),
                           "sem GA": self.doc(ga=False, track=1)}.items():
            with self.subTest(name=name):
                self.assertTrue(any("track.js" in p for p in self.check(html)), name)

    def test_inert_or_foreign_track_does_not_count(self):
        for name, tag in {
            "comentado": "<!-- " + self.TR + " -->",
            "json": self.TR.replace("<script ", '<script type="application/json" '),
            "template": "<template>" + self.TR + "</template>",
            "noscript": "<noscript>" + self.TR + "</noscript>",
            "outro host": '<script src="https://exemplo.com/assets/track.js"></script>',
            "outro caminho": '<script src="/x/assets/track.js"></script>',
            "outro arquivo": '<script src="/assets/track.js.bak"></script>',
            "http": '<script src="http://guifav.dev/assets/track.js"></script>',
        }.items():
            with self.subTest(name=name):
                self.assertTrue(any("track.js uma vez, carrega 0" in p for p in self.check(self.doc(tag, track=0))), name)
        for name, tag in {"absoluto": '<script src="https://guifav.dev/assets/track.js?v=2"></script>',
                          "sem versão": '<script data-site-script src="/assets/track.js"></script>',
                          "aspas simples": "<script src='/assets/track.js?v=3'></script>",
                          "tipo js": '<script type="text/javascript" src="/assets/track.js"></script>'}.items():
            with self.subTest(name=name):
                self.assertEqual(self.check(self.doc(tag, track=0)), [], name)

    def test_cta_in_menu_and_footer_take_position_from_structure(self):
        ok = ('<nav><a href="mailto:contact@guifav.dev">Contato</a>'
              '<a href="https://calendar.app.google/x">Agende</a></nav>'
              '<footer><a href="mailto:contact@guifav.dev">Contato</a></footer>')
        self.assertEqual(self.check(self.doc(ok)), [])
        for where in ("nav", "footer"):
            with self.subTest(where=where):
                html = self.doc(f'<{where}><a href="mailto:contact@guifav.dev" data-cta-location="hero">c</a></{where}>')
                self.assertTrue(any("não leva data-cta-location" in p for p in self.check(html)), where)

    def test_cta_in_page_needs_location_from_list(self):
        for loc in C.CTA_LOCATIONS:
            with self.subTest(loc=loc):
                html = self.doc(f'<main><a href="https://calendar.app.google/x" data-cta-location="{loc}">a</a></main>')
                self.assertEqual(self.check(html), [])
        self.assertEqual(C.CTA_LOCATIONS, ("hero", "projetos", "consultoria", "chamada-final"))
        for name, a in {
            "sem atributo": '<a href="mailto:contact@guifav.dev">c</a>',
            "vazio": '<a href="mailto:contact@guifav.dev" data-cta-location="">c</a>',
            "fora da lista": '<a href="https://calendar.app.google/x" data-cta-location="topo">a</a>',
            "conteudo fora do post": '<a href="https://calendar.app.google/x" data-cta-location="conteudo">a</a>',
            "mailto com variação": '<a href="MAILTO:Contact@GuiFav.dev?subject=Oi">c</a>',
            "mailto codificado": '<a href="mailto:contact%40guifav.dev">c</a>',
        }.items():
            with self.subTest(name=name):
                self.assertTrue(any("sem data-cta-location da lista" in p for p in self.check(self.doc(f"<main>{a}</main>"))), name)

    def test_non_cta_links_and_post_body_are_free(self):
        body = ('<main><a href="mailto:outro@guifav.dev">o</a><a href="http://calendar.app.google/x">h</a>'
                '<a href="https://calendar.google.com/x">g</a><a href="/portfolio/">p</a>'
                '<div class="post-body"><p><a href="https://calendar.app.google/y">no post</a>'
                '<a href="mailto:contact@guifav.dev">no post</a></p></div></main>')
        self.assertEqual(self.check(self.doc(body)), [])
        # Depois que o div.post-body fecha, a regra volta a valer.
        after = self.doc('<main><div class="post-body"><div>x</div></div><a href="mailto:contact@guifav.dev">c</a></main>')
        self.assertTrue(any("sem data-cta-location da lista" in p for p in self.check(after)))
        bad = self.doc('<div class="post-body"><a href="mailto:contact@guifav.dev" data-cta-location="topo">c</a></div>')
        self.assertTrue(any("sem data-cta-location da lista" in p for p in self.check(bad)))

    def test_same_event_and_position_twice_is_flagged(self):
        a = '<a href="https://calendar.app.google/x" data-cta-location="projetos">a</a>'
        self.assertTrue(any("repetido" in p for p in self.check(self.doc(f"<main>{a}{a}</main>"))))
        mixed = ('<main><a href="https://calendar.app.google/x" data-cta-location="chamada-final">a</a>'
                 '<a href="mailto:contact@guifav.dev" data-cta-location="chamada-final">c</a></main>')
        self.assertEqual(self.check(self.doc(mixed)), [])

    def test_cta_rule_only_on_ga_pages(self):
        self.assertEqual(self.check(self.doc('<main><a href="mailto:contact@guifav.dev">c</a></main>', ga=False, track=0)), [])

    def test_signup_marker_only_on_confirmation_page(self):
        mark = '<article data-ga-event="sign_up">x</article>'
        self.assertEqual(self.check(self.doc(mark), rel=C.SIGNUP_PAGE), [])
        self.assertEqual(C.SIGNUP_PAGE, "blog/inscricao-confirmada/index.html")
        for name, (rel, body) in {
            "falta": (C.SIGNUP_PAGE, ""),
            "duas vezes": (C.SIGNUP_PAGE, mark + mark),
            "outro valor": (C.SIGNUP_PAGE, '<article data-ga-event="signup">x</article>'),
            "outra página": ("blog/assinar/index.html", mark),
            "home": ("index.html", '<div data-ga-event="purchase"></div>'),
        }.items():
            with self.subTest(name=name):
                self.assertTrue(any("data-ga-event" in p for p in self.check(self.doc(body), rel=rel)), name)

    def test_material_form_carries_ga_client_id(self):
        # Issue #99: o formulário de material leva data-ga-client-id e o campo oculto ga_cid, uma vez.
        rel = "planilha-custos-ia/index.html"
        form = ('<form id="receber" {attr}method="post" action="https://insights.guifav.dev/material">'
                '<input type="hidden" name="material" value="planilha-custos-ia">{cid}</form>')
        cid = '<input type="hidden" name="ga_cid" value="">'
        self.assertEqual(self.check(self.doc(form.format(attr="data-ga-client-id ", cid=cid)), rel=rel), [])
        for name, body in {
            "sem atributo": form.format(attr="", cid=cid),
            "sem campo": form.format(attr="data-ga-client-id ", cid=""),
            "campo duas vezes": form.format(attr="data-ga-client-id ", cid=cid * 2),
            "sem formulário": "",
            "formulário comentado": "<!-- " + form.format(attr="data-ga-client-id ", cid=cid) + " -->",
            "campo comentado": form.format(attr="data-ga-client-id ", cid="<!-- " + cid + " -->"),
        }.items():
            with self.subTest(name=name):
                self.assertTrue(any("formulário de material" in p for p in self.check(self.doc(body), rel=rel)), name)

    def test_generate_lead_only_on_material_sent_pages_with_the_folder_id(self):
        # Issue #99: generate_lead na página de enviado de cada material, com data-material-id igual ao da pasta.
        lead = lambda m: f'<header data-ga-event="generate_lead" data-material-id="{m}">x</header>'
        for m in C.MATERIALS:
            self.assertEqual(self.check(self.doc(lead(m)), rel=f"{m}/enviado/index.html"), [], m)
        enviado = "planilha-custos-ia/enviado/index.html"
        for name, (rel, body) in {
            "falta": (enviado, ""),
            "duas vezes": (enviado, lead("planilha-custos-ia") * 2),
            "outro material": (enviado, lead("mapa-decisao-ia")),
            "sem id": (enviado, '<header data-ga-event="generate_lead">x</header>'),
            "fora do enviado": ("planilha-custos-ia/index.html", lead("planilha-custos-ia")),
            "home": ("index.html", lead("planilha-custos-ia")),
        }.items():
            with self.subTest(name=name):
                self.assertTrue(any("data-ga-event" in p or "generate_lead" in p
                                    for p in self.check(self.doc(body), rel=rel)), name)


class GaHostGuardTest(unittest.TestCase):
    """#66: fora de guifav.dev o gtag.js não envia nada (prévias locais entravam como visita)."""

    GA = '<script async src="https://www.googletagmanager.com/gtag/js?id=G-K7Y87QNCX8"></script>'
    GUARD = "window['ga-disable-G-K7Y87QNCX8']=location.hostname.replace(/\\.$/,'')!=='guifav.dev';"
    CONFIG = "window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}gtag('js',new Date());gtag('config','G-K7Y87QNCX8',{content_group:'home'});"

    def doc(self, *scripts, ga=True):
        body = "".join(f"<script>{s}</script>" for s in scripts)
        return f"<!DOCTYPE html><html><head>{self.GA if ga else ''}{body}</head><body></body></html>"

    def flagged(self, html):
        return [p for p in C.check_ga_host_guard("index.html", html) if "ga-disable" in p]

    def test_guard_before_config_ok(self):
        self.assertEqual(self.flagged(self.doc(self.GUARD + self.CONFIG)), [])
        self.assertEqual(self.flagged(self.doc(self.GUARD, self.CONFIG)), [])
        spaced = "window[ 'ga-disable-G-K7Y87QNCX8' ] = location.hostname.replace( /\\.$/ , '' ) !== 'guifav.dev' ;\n"
        self.assertEqual(self.flagged(self.doc(spaced + self.CONFIG)), [])
        # Só o gtag('config') conta como limite; gtag('js') antes da linha não importa (mutante H6).
        js_first = "window.dataLayer=[];function gtag(){dataLayer.push(arguments);}gtag('js',new Date());"
        self.assertEqual(self.flagged(self.doc(js_first + self.GUARD + "gtag('config','G-K7Y87QNCX8');")), [])
        # Comentário dentro da linha é só comentário: a linha vale (mutante H1).
        commented = "window['ga-disable-G-K7Y87QNCX8'] /* prévias */ = location.hostname.replace(/\\.$/,'') !== 'guifav.dev';"
        self.assertEqual(self.flagged(self.doc(commented + self.CONFIG)), [])

    def test_page_without_ga_needs_no_guard(self):
        self.assertEqual(self.flagged(self.doc(ga=False)), [])

    def test_missing_late_or_wrong_guard_is_flagged(self):
        g = self.GUARD
        for name, html in {
            "sem": self.doc(self.CONFIG),
            "depois do config": self.doc(self.CONFIG + g),
            "script seguinte": self.doc(self.CONFIG, g),
            "duas vezes": self.doc(g + g + self.CONFIG),
            "comentário de linha": self.doc("// " + g + "\n" + self.CONFIG),
            "comentário de bloco": self.doc("/* " + g + " */" + self.CONFIG),
            "comentário HTML": "<!-- <script>" + g + "</script> -->" + self.doc(self.CONFIG),
            "script de dado": self.doc(self.CONFIG).replace("<head>", '<head><script type="application/json">' + g + "</script>"),
            "outro host": self.doc(g.replace("guifav.dev", "guifav.github.io") + self.CONFIG),
            "igualdade invertida": self.doc(g.replace("!==", "===") + self.CONFIG),
            "outro ID": self.doc(g.replace("K7Y87QNCX8", "XXXXXXXXXX") + self.CONFIG),
            "false fixo": self.doc("window['ga-disable-G-K7Y87QNCX8']=false;" + self.CONFIG),
            # r1 (Kimi P2): sem tirar o ponto final, https://guifav.dev./ (200, mesmo HTML) perdia a coleta.
            "sem o ponto final": self.doc("window['ga-disable-G-K7Y87QNCX8']=location.hostname!=='guifav.dev';" + self.CONFIG),
            "dentro de string": self.doc('var s="' + g.replace("'", "\\'") + '";' + self.CONFIG),
            "dentro de template": self.doc("var s=`" + g + "`;" + self.CONFIG),
        }.items():
            with self.subTest(name=name):
                self.assertTrue(self.flagged(html), name)

    def test_guard_semantics_in_node(self):
        # O que a linha faz em cada host, avaliada no node (r1, Kimi P2: guifav.dev. é o mesmo site).
        node = shutil.which("node")
        if not node:
            self.skipTest("node ausente")
        js = ("const out={};for(const h of ['guifav.dev','guifav.dev.','localhost','127.0.0.1','guifav.github.io',"
              "'www.guifav.dev','guifav.dev.evil.com','xguifav.dev']){const window={};const location={hostname:h};"
              + self.GUARD + "out[h]=window['ga-disable-G-K7Y87QNCX8'];}console.log(JSON.stringify(out));")
        got = json.loads(subprocess.run([node, "-e", js], capture_output=True, text=True, check=True).stdout)
        self.assertEqual(got, {"guifav.dev": False, "guifav.dev.": False, "localhost": True, "127.0.0.1": True,
                               "guifav.github.io": True, "www.guifav.dev": True, "guifav.dev.evil.com": True,
                               "xguifav.dev": True})

    def test_sources_carry_the_guard_before_config(self):
        root = Path(__file__).resolve().parent.parent
        for rel, config in (("index.html", "gtag('config','G-K7Y87QNCX8'"),
                            ("portfolio/index.html", "gtag('config','G-K7Y87QNCX8'"),
                            *((p, "gtag('config','G-K7Y87QNCX8'") for p in sorted(C.MATERIAL_PAGES)),
                            ("blog-src/src/layouts/Base.astro", "gtag('config', GA4_ID")):
            with self.subTest(rel=rel):
                t = (root / rel).read_text(encoding="utf-8")
                compact = re.sub(r"\s+", "", t)
                self.assertEqual(compact.count(re.sub(r"\s+", "", self.GUARD)), 1)
                self.assertLess(compact.index(re.sub(r"\s+", "", self.GUARD)), compact.index(re.sub(r"\s+", "", config)))


class ConsentTest(unittest.TestCase):
    """#68: Consent Mode v2 avançado (padrão negado antes do config), consent.js uma vez, Clarity só com aceite e o
    link "Cookies" no rodapé de toda página com GA4."""

    GA = '<script async src="https://www.googletagmanager.com/gtag/js?id=G-K7Y87QNCX8"></script>'
    DEFAULT = ("gtag('consent','default',{ad_storage:'denied',ad_user_data:'denied',ad_personalization:'denied',"
               "analytics_storage:'denied'});")
    INIT = "window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}"
    CONFIG = "gtag('js',new Date());gtag('config','G-K7Y87QNCX8',{content_group:'home'});"
    CLARITY = ("window.guifavClarity=function(){if(window.clarity)return;(function(c,l,a,r,i,t,y){t=l.createElement(r);"
               "t.src=\"https://www.clarity.ms/tag/\"+i;})(window,document,\"clarity\",\"script\",\"zcs8f7r329\");};"
               "if(window.guifavConsent==='granted')window.guifavClarity();")
    CJS = '<script src="/assets/consent.js?v=1"></script>'
    FOOT = '<footer><a href="/blog/privacidade/#cookies" data-consent-open>Cookies</a></footer>'

    def doc(self, ga_js=None, clarity=None, cjs=1, foot=None, ga=True):
        ga_js = self.INIT + self.DEFAULT + self.CONFIG if ga_js is None else ga_js
        head = (self.GA + f"<script>{ga_js}</script><script>{self.CLARITY if clarity is None else clarity}</script>") if ga else ""
        return (f"<!DOCTYPE html><html><head>{head}</head><body>{self.FOOT if foot is None else foot}"
                f"{self.CJS * cjs}</body></html>")

    def flagged(self, html):
        return C.check_consent("index.html", html)

    def test_complete_page_ok_and_page_without_ga_needs_nothing(self):
        self.assertEqual(self.flagged(self.doc()), [])
        # Espaços, aspas duplas, chaves entre aspas e outra ordem das chaves também valem.
        alt = ('gtag( "consent" , "default" , { "analytics_storage" : "denied", ad_personalization: "denied", '
               'ad_user_data:"denied", ad_storage:"denied", wait_for_update: 500 } );')
        self.assertEqual(self.flagged(self.doc(ga_js=self.INIT + alt + self.CONFIG)), [])
        self.assertEqual(self.flagged(self.doc(ga=False, cjs=0, foot="<footer></footer>")), [])

    def test_default_missing_late_partial_granted_or_inert_is_flagged(self):
        d, i, c = self.DEFAULT, self.INIT, self.CONFIG
        for name, js in {
            "sem padrão": i + c,
            "depois do config": i + c + d,
            "dois padrões": i + d + d + c,
            "falta analytics_storage": i + d.replace(",analytics_storage:'denied'", "") + c,
            "falta ad_user_data": i + d.replace("ad_user_data:'denied',", "") + c,
            "falta ad_personalization": i + d.replace("ad_personalization:'denied',", "") + c,
            "falta ad_storage": i + d.replace("ad_storage:'denied',", "", 1) + c,
            "ad_storage concedido": i + d.replace("ad_storage:'denied'", "ad_storage:'granted'", 1) + c,
            "analytics concedido": i + d.replace("analytics_storage:'denied'", "analytics_storage:'granted'") + c,
            "update no lugar de default": i + d.replace("'default'", "'update'") + c,
            "comentado": i + "/* " + d + " */" + c,
            "em string": i + 'var s="' + d.replace("'", "\\'") + '";' + c,
        }.items():
            with self.subTest(name=name):
                self.assertTrue(any("consent" in p and "default" in p for p in self.flagged(self.doc(ga_js=js))), name)

    def test_consent_js_once_only_with_ga(self):
        for name, html in {"sem": self.doc(cjs=0), "duas": self.doc(cjs=2),
                           "sem GA": self.doc(ga=False, cjs=1, foot="<footer></footer>")}.items():
            with self.subTest(name=name):
                self.assertTrue(any("consent.js" in p for p in self.flagged(html)), name)
        commented = self.doc(cjs=0).replace("</body>", "<!-- " + self.CJS + " --></body>")
        self.assertTrue(any("consent.js" in p for p in self.flagged(commented)))

    def test_clarity_only_behind_consent(self):
        loose = ('(function(c,l,a,r,i,t,y){t=l.createElement(r);t.src="https://www.clarity.ms/tag/"+i;})'
                 '(window,document,"clarity","script","zcs8f7r329");')
        for name, cl in {"solto": loose,
                         "sem a guarda": self.CLARITY.replace("if(window.guifavConsent==='granted')", ""),
                         "guarda invertida": self.CLARITY.replace("==='granted'", "!=='granted'"),
                         "guarda comentada": self.CLARITY.replace("if(window.guifavConsent==='granted')window.guifavClarity();",
                                                                  "/* if(window.guifavConsent==='granted') */window.guifavClarity();")}.items():
            with self.subTest(name=name):
                self.assertTrue(any("Clarity" in p for p in self.flagged(self.doc(clarity=cl))), name)

    def test_footer_cookies_link_once(self):
        for name, foot in {"sem rodapé": "", "rodapé sem link": "<footer><a href='/'>x</a></footer>",
                           "fora do rodapé": "<main><a href='/blog/privacidade/#cookies' data-consent-open>Cookies</a></main><footer></footer>",
                           "dois links": "<footer>" + 2 * "<a href='/blog/privacidade/#cookies' data-consent-open>Cookies</a>" + "</footer>",
                           "sem href": "<footer><a data-consent-open>Cookies</a></footer>"}.items():
            with self.subTest(name=name):
                self.assertTrue(any("Cookies" in p for p in self.flagged(self.doc(foot=foot))), name)


class ConsentSourcesTest(unittest.TestCase):
    """As três origens com GA4 carregam o mesmo consent.js e levam o padrão negado antes do config."""

    def read(self, rel):
        return (Path(__file__).resolve().parent.parent / rel).read_text(encoding="utf-8")

    def test_consent_js_same_version_everywhere(self):
        src = re.search(r"CONSENT_SRC = '([^']+)'", self.read("blog-src/src/consts.ts")).group(1)
        self.assertRegex(src, r"^/assets/consent\.js\?v=\d{12}$")
        for rel in ("index.html", "portfolio/index.html", *sorted(C.MATERIAL_PAGES)):
            with self.subTest(rel=rel):
                self.assertEqual(self.read(rel).count(f'<script src="{src}"></script>'), 1)
        self.assertEqual(self.read("blog-src/src/layouts/Base.astro").count(
            "{!DRAFT_PREVIEW && <script data-site-script is:inline src={CONSENT_SRC}></script>}"), 1)

    def test_policy_has_cookies_section(self):
        t = self.read("blog-src/src/pages/privacidade.astro")
        self.assertIn('<h2 id="cookies">', t)
        for must in ("consentimento", "Sim", "Não", "Cookies", "_ga", "_clck"):
            self.assertIn(must, t)


class TrackingWiredTest(Base):
    def test_check_runs_tracking_rule(self):
        ga = TrackingTest.GA
        self.w("portfolio/index.html", page(ga + '<a href="/">home</a>', robots=""))
        self.assertTrue(any("portfolio/index.html: página com GA4 precisa carregar" in p
                            for p in self.problems(pre_cutover=False)))

    def test_check_runs_consent_rule(self):
        ga = TrackingTest.GA
        self.w("portfolio/index.html", page(ga + '<a href="/">home</a>', robots=""))
        self.assertTrue(any("portfolio/index.html: página com GA4 precisa do gtag('consent','default'" in p
                            for p in self.problems(pre_cutover=False)))

    def test_check_runs_ga_host_guard_rule(self):
        ga = TrackingTest.GA
        self.w("portfolio/index.html", page(ga + '<a href="/">home</a>', robots=""))
        self.assertTrue(any("portfolio/index.html: página com GA4 precisa de window['ga-disable" in p
                            for p in self.problems(pre_cutover=False)))


class TrackingSourcesTest(unittest.TestCase):
    """Fontes: track.js com a mesma versão nas três origens, CTAs da home e do portfólio e o marcador do sign_up."""

    REPO = Path(__file__).resolve().parent.parent

    def read(self, rel):
        return (self.REPO / rel).read_text(encoding="utf-8")

    def test_same_track_src_everywhere(self):
        m = re.search(r"export const TRACK_SRC = '([^']+)';", self.read("blog-src/src/consts.ts"))
        self.assertTrue(m)
        src = m.group(1)
        self.assertTrue(src.startswith(C.TRACK_PATH + "?v="))
        for rel in ("index.html", "portfolio/index.html", *sorted(C.MATERIAL_PAGES)):
            with self.subTest(rel=rel):
                t = self.read(rel)
                self.assertEqual(t.count(f'<script src="{src}"></script>'), 1)
                # Depois do site.js, para ler o idioma e o tema que ele aplica.
                self.assertLess(t.index("/assets/site.js"), t.index(src))
        base = self.read("blog-src/src/layouts/Base.astro")
        self.assertEqual(base.count("<script data-site-script is:inline src={TRACK_SRC}></script>"), 1)
        self.assertLess(base.index("localStorage.setItem('theme', next)"), base.index("src={TRACK_SRC}"))

    def test_cta_locations_in_sources(self):
        home = self.read("index.html")
        for loc in ("hero", "projetos", "consultoria"):
            self.assertEqual(home.count(f'data-cta-location="{loc}"'), 1, loc)
        self.assertEqual(self.read("portfolio/index.html").count('data-cta-location="chamada-final"'), 2)

    def test_signup_marker_in_source(self):
        self.assertIn('<article class="post page" data-ga-event="sign_up">',
                      self.read("blog-src/src/pages/inscricao-confirmada.astro"))

    def test_privacy_mentions_events(self):
        t = self.read("blog-src/src/pages/privacidade.astro")
        self.assertIn("alguns cliques: contato, agendamento, links do rodapé, troca de idioma e de tema, a "
                      "confirmação da inscrição e o pedido e o download de materiais (sem o email)", t)

    def test_privacy_discloses_google_ads(self):
        # #64: GA4 vinculado ao Google Ads com publicidade personalizada (Gui, 13/11/2026). A política diz o recurso,
        # os cookies, como o Google usa os dados e como desativar.
        t = self.read("blog-src/src/pages/privacidade.astro")
        for must in ("Google Ads", "publicidade personalizada", "<code>_ga</code>", "<code>_ga_K7Y87QNCX8</code>",
                     "medir campanhas", "mostrar anúncios do guifav.dev a quem já visitou o site",
                     # r1 (Kimi e Astra P2): o uso conjunto de cookies do site e de publicidade do Google, exigido pela
                     # política de recursos de publicidade do Google Analytics.
                     "usam em conjunto os cookies do Google Analytics, que são do site, e os cookies de publicidade do Google",
                     "O seu email nunca é enviado ao Google.", "desativar os anúncios personalizados",
                     "https://adssettings.google.com/", "https://tools.google.com/dlpage/gaoptout",
                     "https://policies.google.com/technologies/partner-sites"):
            self.assertIn(must, t)
        self.assertIn("Atualizada em 24 de novembro de 2026.", t)  # #68: seção Cookies; 23/11: Materiais para baixar; 24/11: registro no CRM (#97)

    def test_privacy_says_material_record_goes_to_the_crm(self):
        # #97 fase 2: o registro dos materiais vai ao Twenty. O download cria o contato; o pedido sem download só entra
        # se o email já for contato; a remoção apaga pedidos e downloads nos dois lugares e o contato só a pedido.
        t = self.read("blog-src/src/pages/privacidade.astro")
        for must in ("O registro vai também para o meu CRM",
                     "o download cria lá um contato só com o seu email, se ele ainda não existir",
                     "o pedido sem download só entra no CRM se o seu email já for um contato",
                     "apago os pedidos e downloads, no Cloudflare e no CRM. O contato continua no CRM com o resumo que veio "
                     "deles (os temas de interesse, a data da última ação, se o email foi comprovado e o identificador do "
                     "Google Analytics), e só é apagado, com esse resumo, se você pedir.",
                     # r1 (Astra P2): a data da página mudou; o início do registro é o deploy do Worker, não a versão.
                     "Pedidos e downloads feitos antes das 17h31 de 23 de novembro de 2026 (horário de Brasília) não foram "
                     "registrados.",
                     "que guardo com o pedido, também no CRM,",
                     '<h2 id="quem-processa">Quem processa</h2>', 'href="#quem-processa"',
                     "o CRM onde ficam os inscritos da lista e o registro dos pedidos e downloads de materiais e das "
                     "confirmações de inscrição"):
            self.assertIn(must, t)
        # r1 (Kimi P1): a remoção não apaga o contato nem o resumo dele; a frase antiga prometia mais do que o código faz.
        self.assertNotIn("e também o contato, se você pedir", t)
        self.assertNotIn("anteriores a esta versão da página", t)


class ToolsTest(unittest.TestCase):
    """Issue #104, item 1: os quatro links de "Ferramentas e guias" na seção da home e no rodapé da home e do blog."""

    GA = TrackingTest.GA

    def links(self, hrefs=C.TOOLS_LINKS):
        return "".join(f'<a href="{h}">x</a>' for h in hrefs)

    def doc(self, section=None, footer=None, ga=True):
        sec = "" if section is None else f'<section id="{C.TOOLS_SECTION_ID}">{section}</section>'
        foot = "" if footer is None else f"<footer>{footer}</footer>"
        return f"<!DOCTYPE html><html><head>{self.GA if ga else ''}</head><body>{sec}<main></main>{foot}</body></html>"

    def test_home_and_blog_complete_pass(self):
        self.assertEqual(C.check_tools("index.html", self.doc(self.links(), self.links())), [])
        self.assertEqual(C.check_tools("blog/um-post/index.html", self.doc(footer=self.links())), [])
        self.assertEqual(C.check_tools("blog/index.html", self.doc(footer=self.links())), [])

    def test_each_link_missing_from_home_section_or_footer_fails(self):
        for missing in C.TOOLS_LINKS:
            rest = [h for h in C.TOOLS_LINKS if h != missing]
            with self.subTest(missing=missing):
                sec = C.check_tools("index.html", self.doc(self.links(rest), self.links()))
                self.assertTrue(any("seção" in p and missing in p for p in sec), sec)
                foot = C.check_tools("index.html", self.doc(self.links(), self.links(rest)))
                self.assertTrue(any("rodapé" in p and missing in p for p in foot), foot)
                blog = C.check_tools("blog/um-post/index.html", self.doc(footer=self.links(rest)))
                self.assertTrue(any("rodapé" in p and missing in p for p in blog), blog)

    def test_home_without_section_fails(self):
        self.assertTrue(C.check_tools("index.html", self.doc(footer=self.links())))

    def missing(self, problems, where):
        """Os links que a regra deu como ausentes no bloco `where` ("rodapé" ou "seção")."""
        tail = " sem o link de Ferramentas e guias "
        out = []
        for p in problems:
            msg = p.partition(": ")[2]
            if msg.startswith(where) and tail in msg:
                out.append(msg.split(tail, 1)[1])
        return sorted(out)

    def test_links_outside_the_footer_do_not_count(self):
        html = self.doc(footer="").replace("<footer></footer>", f"<main>{self.links()}</main><footer></footer>")
        problems = C.check_tools("blog/um-post/index.html", html)
        self.assertEqual(self.missing(problems, "rodapé"), sorted(C.TOOLS_LINKS), problems)

    def test_inert_links_inside_the_footer_do_not_count(self):
        # Dentro do <footer>: o link só conta como <a> com href, fora de comentário.
        for name, footer in {
            "comentado": f"<!-- {self.links()} -->",
            "sem href": "".join(f"<a>{h}</a>" for h in C.TOOLS_LINKS),
            "href vazio": "".join(f'<a href="">{h}</a>' for h in C.TOOLS_LINKS),
        }.items():
            with self.subTest(name=name):
                problems = C.check_tools("blog/um-post/index.html", self.doc(footer=footer))
                self.assertEqual(self.missing(problems, "rodapé"), sorted(C.TOOLS_LINKS), problems)
                problems = C.check_tools("index.html", self.doc(footer + self.links(), footer))
                self.assertEqual(self.missing(problems, "rodapé"), sorted(C.TOOLS_LINKS), problems)
                self.assertEqual(self.missing(problems, "seção"), [], problems)

    def test_links_in_template_or_hidden_do_not_count(self):
        # Follow-up do PR #116: <template>, hidden no link e hidden num elemento acima não contam.
        for name, wrap in {
            "template": lambda x: f"<template>{x}</template>",
            "hidden no link": lambda x: x.replace("<a ", "<a hidden "),
            "hidden acima": lambda x: f"<div hidden><p>{x}</p></div>",
            "hidden acima, com vazio dentro": lambda x: f'<div hidden><img src="/x.png">{x}<br></div>',
        }.items():
            with self.subTest(name=name):
                problems = C.check_tools("blog/um-post/index.html", self.doc(footer=wrap(self.links())))
                self.assertEqual(self.missing(problems, "rodapé"), sorted(C.TOOLS_LINKS), problems)
                problems = C.check_tools("index.html", self.doc(wrap(self.links()), self.links()))
                self.assertEqual(self.missing(problems, "seção"), sorted(C.TOOLS_LINKS), problems)

    def test_links_after_a_closed_hidden_or_void_element_count(self):
        footer = f'<div hidden>x</div><img src="/x.png"><br/><template><a href="/y/">y</a></template>{self.links()}'
        self.assertEqual(C.check_tools("blog/um-post/index.html", self.doc(footer=footer)), [])

    def test_nofollow_link_fails(self):
        for rel in ("nofollow", "noopener nofollow", "NOFOLLOW"):
            with self.subTest(rel=rel):
                footer = self.links(C.TOOLS_LINKS[1:]) + f'<a href="{C.TOOLS_LINKS[0]}" rel="{rel}">x</a>'
                problems = C.check_tools("blog/um-post/index.html", self.doc(footer=footer))
                self.assertEqual(problems, [f"blog/um-post/index.html: rodapé com o link de Ferramentas e guias "
                                            f"{C.TOOLS_LINKS[0]} em rel=nofollow"])
        footer = self.links(C.TOOLS_LINKS[1:]) + f'<a href="{C.TOOLS_LINKS[0]}" rel="noopener">x</a>'
        self.assertEqual(C.check_tools("blog/um-post/index.html", self.doc(footer=footer)), [])

    def test_section_and_footer_do_not_stand_in_for_each_other(self):
        # Link da seção não vale pelo rodapé...
        problems = C.check_tools("index.html", self.doc(self.links(), ""))
        self.assertEqual(len(problems), len(C.TOOLS_LINKS), problems)
        self.assertEqual(self.missing(problems, "rodapé"), sorted(C.TOOLS_LINKS), problems)
        # ...nem o do rodapé pela seção.
        problems = C.check_tools("index.html", self.doc("", self.links()))
        self.assertEqual(len(problems), len(C.TOOLS_LINKS), problems)
        self.assertEqual(self.missing(problems, "seção"), sorted(C.TOOLS_LINKS), problems)

    def test_repeated_link_in_block_fails(self):
        dup = self.links() + self.links(C.TOOLS_LINKS[:1])
        self.assertTrue(any("2 vezes" in p for p in C.check_tools("blog/um-post/index.html", self.doc(footer=dup))))

    def test_rule_only_on_home_and_blog_pages_with_ga4(self):
        self.assertEqual(C.check_tools("index.html", self.doc(ga=False)), [])
        self.assertEqual(C.check_tools("blog/um-post/index.html", self.doc(ga=False)), [])
        self.assertEqual(C.check_tools("portfolio/index.html", self.doc()), [])
        self.assertEqual(C.check_tools("mapa-decisao-ia/index.html", self.doc()), [])


class RelatedTest(unittest.TestCase):
    """Issue #104: o bloco "Leia também" em todo post indexável, com 3 posts e o link do tema, e em nenhum outro."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.site = Path(self.tmp.name) / "_site"
        self.posts = Path(self.tmp.name) / "posts"
        self.posts.mkdir()
        for s in ("a", "b", "c", "d"):
            self.post(s)
        self.post("tem-tema", "topics: [agentes-de-ia, ia-sobre-dados-da-empresa]")
        self.post("podcast", "type: podcast\nindex: false", block=None)
        self.post("fora", "index: false", block=None)
        self.post("ep-indexavel", "type: podcast", block=None)  # podcast sem index: false também fica sem o bloco

    def tearDown(self):
        self.tmp.cleanup()

    def block(self, slugs=("a", "b", "c"), extra=""):
        items = "".join(f'<li><a href="/blog/{s}/">{s}</a></li>' for s in slugs)
        return f'<aside class="related" aria-label="Leia também"><h2>Leia também</h2><ul>{items}</ul>{extra}</aside>'

    def post(self, slug, fm="", block=""):
        (self.posts / f"{slug}.md").write_text(f"---\ntitle: x\nslug: {slug}\n{fm}\n---\ntexto\n", encoding="utf-8")
        if block == "":
            others = [s for s in ("a", "b", "c", "d") if s != slug][:3]
            extra = '<p>Tema: <a href="/blog/tema/agentes-de-ia/">t</a></p>' if "topics" in fm else ""
            block = self.block(others, extra)
        self.page(slug, block or "")

    def page(self, slug, body):
        f = self.site / "blog" / slug / "index.html"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(page(f'<div class="post-body"><p>x</p></div>{body}'), encoding="utf-8")

    def problems(self):
        return C.check_related(self.site, self.posts)

    def test_complete_passes(self):
        self.assertEqual(self.problems(), [])

    def test_indexable_post_without_block_fails(self):
        self.page("a", "")
        self.assertEqual(self.problems(), ["blog/a/index.html: post indexável precisa de um bloco Leia também, tem 0"])

    def test_block_in_comment_or_with_other_class_does_not_count(self):
        for body in (f"<!-- {self.block(('b', 'c', 'd'))} -->", self.block(("b", "c", "d")).replace('"related"', '"relacionados"')):
            with self.subTest(body=body[:30]):
                self.page("a", body)
                self.assertEqual(self.problems(), ["blog/a/index.html: post indexável precisa de um bloco Leia também, tem 0"])

    def test_two_blocks_fail(self):
        self.page("a", self.block(("b", "c", "d")) * 2)
        self.assertEqual(self.problems(), ["blog/a/index.html: post indexável precisa de um bloco Leia também, tem 2"])

    def test_fewer_than_three_indexable_posts_fail(self):
        for slugs, bad in ((("b", "c"), []), (("b", "c", "podcast"), ["podcast"]), (("b", "c", "a"), ["a"]),
                           (("b", "c", "b"), [])):
            with self.subTest(slugs=slugs):
                self.page("a", self.block(slugs))
                got = self.problems()
                self.assertIn("blog/a/index.html: bloco Leia também com 2 posts indexáveis, o mínimo é 3", got)
                for s in bad:
                    self.assertIn(f"blog/a/index.html: bloco Leia também aponta para {s}, que é o próprio post ou está "
                                  "fora do índice", got)

    def test_post_with_topic_needs_the_main_topic_link(self):
        self.page("tem-tema", self.block(("a", "b", "c")))
        self.assertEqual(self.problems(), ["blog/tem-tema/index.html: bloco Leia também sem o link para o tema "
                                           "agentes-de-ia"])
        # O link do segundo tema não vale pelo do principal.
        self.page("tem-tema", self.block(("a", "b", "c"), '<a href="/blog/tema/ia-sobre-dados-da-empresa/">t</a>'))
        self.assertEqual(len(self.problems()), 1)

    def test_podcast_and_noindex_post_without_block(self):
        self.page("podcast", self.block())
        self.page("fora", self.block())
        self.assertEqual(self.problems(), [
            "blog/fora/index.html: podcast ou post com index: false com o bloco Leia também",
            "blog/podcast/index.html: podcast ou post com index: false com o bloco Leia também",
        ])

    def test_podcast_without_index_false_has_no_block_and_does_not_count(self):
        # O tipo sozinho já tira o post do bloco e da lista de relacionados (revisão do agy no #129).
        self.page("ep-indexavel", self.block())
        self.assertEqual(self.problems(), ["blog/ep-indexavel/index.html: podcast ou post com index: false com o "
                                           "bloco Leia também"])
        self.page("ep-indexavel", "")
        self.page("a", self.block(("b", "c", "ep-indexavel")))
        self.assertIn("blog/a/index.html: bloco Leia também com 2 posts indexáveis, o mínimo é 3", self.problems())

    def test_only_links_in_the_list_count_as_posts(self):
        # Link de post fora do <ul> (no título ou num parágrafo) não completa os 3 (revisão do Claude no #129).
        extra = '<p><a href="/blog/d/">d</a></p>'
        self.page("a", self.block(("b", "c"), extra).replace("<h2>Leia também</h2>", '<h2><a href="/blog/d/">x</a></h2>'))
        self.assertEqual(self.problems(), ["blog/a/index.html: bloco Leia também com 2 posts indexáveis, o mínimo é 3"])
        # O link do tema vale no título, fora do <ul>.
        self.page("a", self.block(("b", "c", "d")))
        self.page("tem-tema", self.block(("a", "b", "c")).replace(
            "<h2>Leia também</h2>", '<h2>Mais artigos sobre <a href="/blog/tema/agentes-de-ia/">t</a></h2>'))
        self.assertEqual(self.problems(), [])

    def test_post_topics_reads_only_the_simple_format(self):
        self.assertEqual(C.post_topics({"topics": "[a, b-c]"}), ["a", "b-c"])
        for raw in ("", "a", "[a,b]", "[A]", "[]"):
            self.assertEqual(C.post_topics({"topics": raw}), [], raw)


class TopicPagesTest(Base):
    """Issue #104: as páginas de tema no sitemap do blog, sem noindex depois do cutover e com canonical próprio."""

    URL = "https://guifav.dev/blog/tema/agentes-de-ia/"

    def setUp(self):
        super().setUp()
        self.tema()
        self.sitemap(with_topic=True)

    def tema(self, robots=ROBOTS_INDEX, canonical=URL):
        link = "" if canonical is None else f'<link rel="canonical" href="{canonical}">'
        self.w("blog/tema/agentes-de-ia/index.html",
               f"<!DOCTYPE html><html><head>{FIXTURE_TITLE}{robots}{link}</head><body>"
               '<a href="/blog/um-post/">p</a></body></html>')

    def sitemap(self, with_topic):
        extra = f"<url><loc>{self.URL}</loc></url>" if with_topic else ""
        self.w("blog/sitemap.xml", SITEMAP.format(
            "<url><loc>https://guifav.dev/blog/</loc></url>" + extra +
            "<url><loc>https://guifav.dev/blog/um-post/</loc></url>"))

    def test_complete_passes(self):
        self.assertEqual(self.problems(pre_cutover=False), [])

    def test_topic_page_missing_from_sitemap_fails(self):
        self.sitemap(with_topic=False)
        self.assertEqual(self.problems(pre_cutover=False), [f"sitemap: falta página indexável do blog: {self.URL}"])

    def test_topic_page_with_noindex_fails_after_cutover(self):
        self.tema(robots=ROBOTS_NOINDEX)
        self.assertEqual(self.problems(pre_cutover=False),
                         ["blog/tema/agentes-de-ia/index.html: página de tema com noindex"])

    def test_topic_page_needs_its_own_canonical(self):
        for canonical in (None, "https://guifav.dev/blog/", "https://guifav.dev/blog/tema/agentes-de-ia"):
            with self.subTest(canonical=canonical):
                self.tema(canonical=canonical)
                got = self.problems(pre_cutover=False)
                self.assertTrue(any(p.startswith("blog/tema/agentes-de-ia/index.html: página de tema sem canonical")
                                    for p in got), got)


class RelatedWiredTest(Base):
    def test_main_runs_the_related_rule(self):
        consts = Path(self.tmp.name) / "consts.ts"
        consts.write_text("export const PRE_CUTOVER_NOINDEX = false;\n", encoding="utf-8")
        self.assertEqual(self.problems(pre_cutover=False), [])  # check() sozinho não tem a regra
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = C.main(["--site", str(self.site), "--posts", str(self.posts), "--consts", str(consts)])
        self.assertEqual(code, 1)
        self.assertIn("blog/um-post/index.html: post indexável precisa de um bloco Leia também, tem 0", err.getvalue())


class ToolsWiredTest(Base):
    def test_check_runs_tools_rule(self):
        self.w("blog/um-post/index.html", page(TrackingTest.GA + '<footer><a href="/blog/">blog</a></footer>'))
        self.assertTrue(any("blog/um-post/index.html: rodapé sem o link" in p
                            for p in self.problems(pre_cutover=False)))


class ToolsSourcesTest(unittest.TestCase):
    """Fontes: a seção e o rodapé da home e o rodapé do Base.astro levam os quatro links, e as páginas existem."""

    REPO = Path(__file__).resolve().parent.parent

    def read(self, rel):
        return (self.REPO / rel).read_text(encoding="utf-8")

    def test_home_section_and_footer(self):
        home = self.read("index.html")
        self.assertEqual(C.check_tools("index.html", home), [])
        self.assertEqual(home.count(f'<section id="{C.TOOLS_SECTION_ID}">'), 1)

    def test_blog_footer(self):
        footer = re.search(r"<footer>.*?</footer>", self.read("blog-src/src/layouts/Base.astro"), flags=re.S)
        self.assertTrue(footer)
        for href in C.TOOLS_LINKS:
            with self.subTest(href=href):
                self.assertEqual(footer.group(0).count(f'href="{href}"'), 1)
                # O destino é só em português; na home em inglês, o hreflang avisa (r1, agy P3).
                self.assertEqual(footer.group(0).count(f'href="{href}" hreflang="pt-BR"'), 1)

    def test_home_has_two_navs_each_labelled(self):
        # r1 (agy P3): com o nav do rodapé, o do topo também precisa de nome acessível, traduzido em inglês.
        home = self.read("index.html")
        self.assertEqual(re.findall(r"<nav\b[^>]*>", home),
                         ['<nav aria-label="Principal" data-i18n-aria="nav_aria">',
                          '<nav class="footer-tools" aria-label="Ferramentas e guias" data-i18n-aria="ft_aria">'])
        en = json.loads(re.search(r'<script type="application/json" id="i18n-en">(.*?)</script>', home, re.S).group(1))
        self.assertEqual((en["nav_aria"], en["ft_aria"]), ("Main", "Tools and guides"))

    def test_both_footers_carry_the_same_tools_line(self):
        def tools_nav(text):
            m = re.search(r'<nav class="footer-tools".*?</nav>', text, flags=re.S)
            self.assertTrue(m)
            # A home traduz por data-i18n; o blog só tem português. O resto da linha é igual.
            return re.sub(r' data-i18n(?:-aria)?="[^"]*"', "", m.group(0))
        self.assertEqual(tools_nav(self.read("index.html")), tools_nav(self.read("blog-src/src/layouts/Base.astro")))

    def test_targets_exist_in_sources(self):
        for rel in ("mapa-decisao-ia/index.html", "planilha-custos-ia/index.html",
                    "seo-geo-aeo-framework/framework-seo-aeo.html",
                    "blog-src/src/content/posts/livro-ia-inteligencia-artificial-portugues-gratuito.md"):
            with self.subTest(rel=rel):
                self.assertTrue((self.REPO / rel).is_file())


class SeoTest(unittest.TestCase):
    """#103: WebSite na home; description, título e alt dos posts (falha só em post novo com description curta)."""

    WEBSITE = ('<script type="application/ld+json">{"@context":"https://schema.org","@type":"WebSite",'
               '"name":"Guilherme Favaron","url":"https://guifav.dev/"}</script>')
    OK_DESC = "d" * 140

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        r = Path(self.tmp.name)
        self.site, self.posts = r / "_site", r / "posts"
        self.posts.mkdir()
        self.home(self.WEBSITE)

    def tearDown(self):
        self.tmp.cleanup()

    def home(self, head):
        (self.site).mkdir(exist_ok=True)
        (self.site / "index.html").write_text(f"<html><head>{head}</head><body></body></html>", encoding="utf-8")

    def post(self, slug="p", date="2026-11-20T12:00:00Z", desc=OK_DESC, title="Um título curto", body="", index=True):
        fm = f"---\ntitle: x\nslug: {slug}\ndate: {date}\n" + ("" if index else "index: false\n") + "---\n"
        (self.posts / f"{slug}.md").write_text(fm, encoding="utf-8")
        d = self.site / "blog" / slug
        d.mkdir(parents=True, exist_ok=True)
        (d / "index.html").write_text(
            f'<html><head><title>{title}</title><meta name="description" content="{desc}"></head>'
            f'<body><img src="/logo.png"><article class="post">{body}</article></body></html>', encoding="utf-8")

    def seo(self):
        return C.check_seo(self.site, self.posts)

    def test_new_post_with_short_description_fails(self):
        self.post(desc="d" * 69)
        problems, warn_new, _ = self.seo()
        self.assertEqual(len(problems), 1)
        self.assertIn("post novo com description de 69 caracteres", problems[0])
        self.assertEqual(warn_new, [])  # a falha não se repete como aviso

    def test_new_post_limits(self):
        for n, fails, warns in ((70, False, True), (119, False, True), (120, False, False), (160, False, False),
                                (161, False, True), (0, True, False)):
            with self.subTest(n=n):
                self.post(desc="d" * n)
                problems, warn_new, _ = self.seo()
                self.assertEqual(bool(problems), fails)
                self.assertEqual(bool(warn_new), warns)

    def test_new_post_date_is_compared_in_utc(self):
        # r1 (agy P3): como blog.ts e schema.ts, a data com fuso vira UTC antes da comparação.
        for date, new in (("2026-11-11T23:30:00-03:00", True), ("2026-11-12T01:00:00+03:00", False),
                          ("2026-11-12T00:00:00.000Z", True), ("2026-11-11T23:59:59Z", False)):
            with self.subTest(date=date):
                self.post(date=date, desc="curta")
                self.assertEqual(bool(self.seo()[0]), new)

    def test_cutover_date_is_the_boundary(self):
        self.post(date="2026-11-12", desc="curta")
        self.assertTrue(self.seo()[0])
        self.post(date="2026-11-11T23:00:00Z", desc="curta")
        problems, warn_new, warn_old = self.seo()
        self.assertEqual((problems, warn_new), ([], []))
        self.assertEqual(len(warn_old), 1)

    def test_imported_post_only_warns(self):
        self.post(date="2024-03-01T10:00:00.000Z", desc="Introdução", title="t" * 61, body='<img src="a.webp" alt="">')
        problems, warn_new, warn_old = self.seo()
        self.assertEqual((problems, warn_new), ([], []))
        self.assertEqual(len(warn_old), 3)

    def test_imported_noindex_post_is_skipped_but_new_is_not(self):
        self.post(date="2024-03-01", desc="x", index=False)
        self.assertEqual(self.seo(), ([], [], []))
        self.post(date="2026-11-20", desc="x", index=False)
        self.assertTrue(self.seo()[0])

    def test_title_and_alt_warnings(self):
        body = '<p><img src="a.webp" alt="Gráfico"><img src="b.webp" alt=" "><img src="c.webp"></p>'
        self.post(title="t" * 61, body=body)
        _, warn_new, _ = self.seo()
        self.assertTrue(any("título com 61 caracteres" in w for w in warn_new))
        self.assertTrue(any("2 de 3 imagens sem texto alternativo" in w for w in warn_new))
        self.post(title="t" * 60, body='<img src="a.webp" alt="Gráfico">')
        self.assertEqual(self.seo(), ([], [], []))

    def test_literal_gt_in_description_does_not_cut_the_tag(self):
        # r1 (agy P2): o Base.astro não escapa ">" em atributo; a description inteira conta, não "0 caracteres".
        desc = "RAG -> SQL: " + "d" * 128
        self.post(desc=desc)
        self.assertEqual(self.seo(), ([], [], []))
        self.assertEqual(C._meta_contents(f'<meta name="description" content="{desc}">', "description"), [desc])
        self.assertEqual(C._meta_contents('<meta content="a > b" property="og:title">', "og:title"), ["a > b"])

    def test_description_comes_from_the_published_html(self):
        self.post(desc="A &amp; " + "d" * 132)  # 140 caracteres depois de decodificar a entidade
        self.assertEqual(self.seo(), ([], [], []))

    def test_unreadable_date_fails(self):
        self.post(date="ontem")
        self.assertTrue(any("date do post ilegível" in p for p in self.seo()[0]))

    def test_home_needs_the_website_json_ld(self):
        self.assertEqual(self.seo(), ([], [], []))
        bad = {"sem": "",
               "comentado": f"<!-- {self.WEBSITE} -->",
               "nome errado": self.WEBSITE.replace("Guilherme Favaron", "guifav"),
               "url sem barra": self.WEBSITE.replace('"https://guifav.dev/"', '"https://guifav.dev"'),
               "outro tipo": self.WEBSITE.replace("WebSite", "Person"),
               "json inválido": self.WEBSITE.replace("}", ""),
               # r1 (agy P3): @context na raiz, @id que não é o da Person e @graph em lista.
               "sem @context": self.WEBSITE.replace('"@context":"https://schema.org",', ""),
               "@context errado": self.WEBSITE.replace("https://schema.org", "https://example.org"),
               "@id da Person": self.WEBSITE.replace('"@type":"WebSite",', '"@type":"WebSite","@id":"https://guifav.dev/#person",'),
               "@graph que não é lista": self.WEBSITE.replace('{"@context":"https://schema.org",',
                                                              '{"@context":"https://schema.org","@graph":{')
                                                     .replace("</script>", "}</script>")}
        for name, head in bad.items():
            with self.subTest(name=name):
                self.home(head)
                self.assertTrue(any("WebSite" in p for p in self.seo()[0]), name)
        self.home(self.WEBSITE.replace('{"@context":"https://schema.org",', '{"@context":"https://schema.org","@graph":[{')
                  .replace("</script>", "]}</script>"))
        self.assertEqual(self.seo()[0], [])

    def test_repo_home_has_the_website(self):
        home = (Path(__file__).resolve().parent.parent / "index.html").read_text(encoding="utf-8")
        self.assertTrue(C._home_has_website(home))

    def test_main_fails_only_on_new_post_and_prints_warnings(self):
        import contextlib
        import io
        consts = Path(self.tmp.name) / "consts.ts"
        consts.write_text("export const PRE_CUTOVER_NOINDEX = false;\n", encoding="utf-8")
        args = ["--site", str(self.site), "--posts", str(self.posts), "--consts", str(consts)]
        from unittest import mock
        self.post(slug="antigo", date="2024-03-01", desc="curta")  # importado: só aviso, agrupado na CI
        # isola a regra da #103 das demais; o formato do aviso depende de GITHUB_ACTIONS, que a CI define
        with mock.patch.object(C, "check", lambda *a, **k: []), \
                mock.patch.object(C, "check_related", lambda *a, **k: []):  # bloco Leia também (#104)
            for gh, new_mark, group in (("", "AVISO: ", None), ("true", "::warning title=check_site (#103)::", "::group::")):
                for desc, code in (("d" * 69, 1), ("d" * 165, 0), (self.OK_DESC, 0)):
                    with self.subTest(gh=gh, desc=len(desc)), mock.patch.dict(os.environ, {"GITHUB_ACTIONS": gh}):
                        self.post(desc=desc)
                        out, err = io.StringIO(), io.StringIO()
                        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                            self.assertEqual(C.main(args), code)
                        self.assertEqual("ERRO" in err.getvalue(), code == 1)
                        self.assertEqual(new_mark in out.getvalue(), len(desc) == 165)
                        self.assertIn("AVISO (importado): blog/antigo/index.html", out.getvalue())
                        if group:
                            self.assertIn(group + "Avisos dos posts importados (1)", out.getvalue())
                            self.assertIn("::endgroup::", out.getvalue())


TOKENS_CSS = (":root{--bg:#fafafa;--text:#171717;--hl:#FFBE00;--hl-text:#8a6400}"
              '[data-theme="dark"]{--bg:#0a0a0a;--text:#fafafa;--hl:#FFBE00;--hl-text:#FFBE00}')


class ContrastUnitTest(unittest.TestCase):
    """#119: contraste WCAG entre color e fundo de cada seletor, nos dois temas, na ordem da cascata."""

    def test_ratio_known_values(self):
        self.assertAlmostEqual(C.contrast_ratio((0, 0, 0), (255, 255, 255)), 21.0, places=2)
        self.assertAlmostEqual(C.contrast_ratio((255, 190, 0), (255, 190, 0)), 1.0, places=2)

    def test_resolve_var_hex_rgb_and_fallback(self):
        toks = {"--a": "var(--b)", "--b": "#fff"}
        self.assertEqual(C.css_color("var(--a)", toks), (255, 255, 255))
        self.assertEqual(C.css_color("var(--nada, #000)", toks), (0, 0, 0))
        self.assertEqual(C.css_color("rgb(255, 190, 0)", toks), (255, 190, 0))
        self.assertEqual(C.css_color("#fb0", toks), (255, 187, 0))

    def test_translucent_and_unknown_are_not_judged(self):
        self.assertIsNone(C.css_color("rgba(0,0,0,.5)", {}))
        self.assertIsNone(C.css_color("var(--nada)", {}))
        self.assertIsNone(C.css_color("currentColor", {}))
        self.assertEqual(C.contrast_problems([TOKENS_CSS + ".x{color:var(--text);background:rgba(255,190,0,.2)}"]), [])

    def test_same_color_text_and_background_gives_one_to_one(self):
        # O caso da #119: no escuro --hl-text é igual a --hl.
        css = TOKENS_CSS + ".b{background:var(--hl);color:var(--hl-text)}"
        found = {(sel, theme) for sel, theme, *_ in C.contrast_problems([css])}
        self.assertIn((".b", "escuro"), found)
        ratio = {t: r for s, t, r, *_ in C.contrast_problems([css])}
        self.assertAlmostEqual(ratio["escuro"], 1.0, places=2)

    def test_light_theme_also_judged(self):
        found = {t for _, t, *_ in C.contrast_problems([TOKENS_CSS + ".b{background:var(--hl);color:var(--hl-text)}"])}
        self.assertIn("claro", found)  # #8a6400 sobre #FFBE00 dá 3,2:1

    def test_good_pair_passes_both_themes(self):
        self.assertEqual(C.contrast_problems([TOKENS_CSS + ".b{background:var(--hl);color:#171717}"]), [])

    def test_later_sheet_wins_the_cascade(self):
        good = TOKENS_CSS + ".b{background:var(--hl);color:#171717}"
        bad = ".b{color:var(--hl-text)}"
        self.assertTrue(C.contrast_problems([good, bad]))
        self.assertEqual(C.contrast_problems([bad.replace("--hl-text", "--text") + TOKENS_CSS, good]), [])

    def test_background_from_one_rule_color_from_another(self):
        css = TOKENS_CSS + ".b{background:var(--hl)}.b{color:var(--hl-text)}"
        self.assertTrue(C.contrast_problems([css]))

    def test_dark_prefixed_selector_only_in_dark(self):
        css = TOKENS_CSS + '.b{background:#fff;color:#000}[data-theme="dark"] .b{color:#fff}'
        self.assertEqual([(s, t) for s, t, *_ in C.contrast_problems([css])], [(".b", "escuro")])

    def test_hover_inherits_the_base_color(self):
        css = TOKENS_CSS + ".b{background:#fff;color:var(--hl-text)}.b:hover{background:var(--hl)}"
        self.assertIn((".b:hover", "escuro"), {(s, t) for s, t, *_ in C.contrast_problems([css])})

    def test_media_rules_count_and_prefers_dark_tokens_are_dark(self):
        css = (":root{--fg:#000;--bg2:#fff}@media (prefers-color-scheme: dark){:root{--fg:#fff;--bg2:#fff}}"
               "@media (max-width:600px){.m{color:var(--fg);background:var(--bg2)}}")
        self.assertEqual([(s, t) for s, t, *_ in C.contrast_problems([css])], [(".m", "escuro")])

    def test_comments_and_keyframes_ignored(self):
        css = TOKENS_CSS + "/* .b{color:#fff;background:#fff} */@keyframes k{from{color:#fff;background:#fff}}"
        self.assertEqual(C.contrast_problems([css]), [])

    def test_grouped_selectors_are_split(self):
        css = TOKENS_CSS + ".a,.b{background:var(--hl)}.b{color:var(--hl-text)}.a{color:#171717}"
        self.assertEqual({s for s, *_ in C.contrast_problems([css])}, {".b"})


class ContrastSiteTest(Base):
    """A regra lê as folhas de cada página na ordem do documento: <link> internos e <style>."""

    def setUp(self):
        super().setUp()
        self.w("assets/site.css", TOKENS_CSS + ".subscribe-row button{background:var(--hl);color:#171717}")

    def test_inline_style_after_link_breaks_the_button(self):
        self.w("blog/um-post/index.html", page('<link rel="stylesheet" href="/assets/site.css?v=1">'
                                               '<style>.subscribe-row button{color:var(--hl-text)}</style>'))
        probs = C.check_contrast(self.site)
        self.assertTrue(any(p.startswith("blog/um-post/index.html: contraste 1.00:1") and "tema escuro" in p
                            and ".subscribe-row button" in p for p in probs), probs)

    def test_link_after_style_restores_it(self):
        self.w("blog/um-post/index.html", page('<style>.subscribe-row button{color:var(--hl-text)}</style>'
                                               '<link rel="stylesheet" href="/assets/site.css">'))
        self.assertEqual(C.check_contrast(self.site), [])

    def test_external_stylesheet_ignored_and_relative_resolved(self):
        self.w("blog/x.css", ".subscribe-row button{color:var(--hl-text)}")
        self.w("blog/index.html", page('<link rel="stylesheet" href="https://fonts.example/a.css">'
                                       '<link rel="stylesheet" href="/assets/site.css"><link href="x.css" rel="stylesheet">'))
        self.assertTrue(any(p.startswith("blog/index.html:") for p in C.check_contrast(self.site)))

    def test_external_url_with_the_same_path_is_not_the_site_css(self):
        # Um CSS de outro host com o mesmo caminho não é o do site: a página não entra na regra.
        self.w("blog/um-post/index.html", page('<link rel="stylesheet" href="https://cdn.example/assets/site.css">'
                                               '<style>.subscribe-row button{color:var(--hl-text)}</style>'))
        self.assertEqual(C.check_contrast(self.site), [])

    def test_same_sheets_reported_once_with_page_count(self):
        bad = page('<link rel="stylesheet" href="/assets/site.css"><style>.subscribe-row button{color:var(--hl-text)}</style>')
        self.w("blog/index.html", bad)
        self.w("blog/um-post/index.html", bad)
        probs = [p for p in C.check_contrast(self.site) if "tema escuro" in p]
        self.assertEqual(len(probs), 1, probs)
        self.assertIn("(e mais 1 página com as mesmas folhas)", probs[0])

    def test_pages_without_the_site_css_are_not_judged(self):
        # Mockups do portfólio e o /llm-ranking/ têm paleta própria: a regra é sobre o CSS do site.
        self.w("blog/um-post/index.html", page('<style>' + TOKENS_CSS + '.b{background:var(--hl);color:var(--hl)}</style>'))
        self.assertEqual(C.check_contrast(self.site), [])
        self.w("blog/um-post/index.html", page('<link rel="stylesheet" href="/assets/site.css">'
                                               '<style>.b{background:var(--hl);color:var(--hl)}</style>'))
        self.assertTrue(C.check_contrast(self.site))

    def test_main_runs_the_contrast_rule(self):
        consts = Path(self.tmp.name) / "consts.ts"
        consts.write_text("export const PRE_CUTOVER_NOINDEX = true;\n", encoding="utf-8")
        self.w("404.html", page('<a href="/">home</a><link rel="stylesheet" href="/assets/site.css">'
                                '<style>.subscribe-row button{color:var(--hl-text)}</style>',
                                robots='<meta name="robots" content="noindex">'))
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = C.main(["--site", str(self.site), "--posts", str(self.posts), "--consts", str(consts)])
        self.assertEqual(code, 1)
        self.assertIn("404.html: contraste 1.00:1", err.getvalue())


class ContrastSourcesTest(unittest.TestCase):
    """Fontes reais: o CSS do site e o do blog, na ordem do Base.astro (site.css por <link>, blog.css inline depois)."""

    REPO = Path(__file__).resolve().parent.parent

    def sheets(self):
        return [(self.REPO / "assets/site.css").read_text(encoding="utf-8"),
                (self.REPO / "blog-src/src/styles/blog.css").read_text(encoding="utf-8")]

    def test_no_pair_below_the_minimum(self):
        self.assertEqual(C.contrast_problems(self.sheets()), [])

    def test_subscribe_button_in_both_themes(self):
        rules = C.css_cascade(self.sheets())
        for theme in C.THEMES:
            for sel in (".subscribe-row button", ".subscribe-row button:hover"):
                with self.subTest(theme=theme, sel=sel):
                    fg, bg = rules[theme][sel]
                    self.assertGreaterEqual(C.contrast_ratio(fg, bg), C.CONTRAST_MIN)


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Testes do verificador do site montado (scripts/check_site.py).

Rodar: python3 scripts/test_check_site.py
"""
import ast
import contextlib
import inspect
import io
import json
import os
import shutil
import subprocess
import re
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

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
        self.w("robots.txt", "User-agent: *\nDisallow: /_mockups/\n\nSitemap: https://guifav.dev/sitemap.xml\n")
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


_LEGACY_URL_IN_SOURCE = re.compile(
    r"""(?:(?:https?:)?//|(?<![\w./-])www\.)(?:[\w-]+\.)*(?:mindapps\.ai|ia-aplicada\.substack\.com)(?![\w-])[^\s)"'<>\]]*""",
    re.I)


def legacy_links_in_sources(posts: Path) -> list[tuple[str, str]]:
    """Links para mindapps.ai (e subdomínios) e ia-aplicada.substack.com nos posts indexáveis (#105)."""
    out = []
    for f in sorted(posts.glob("*.md*")):
        text = f.read_text(encoding="utf-8")
        if C.frontmatter(text).get("index") == "false":
            continue
        out += [(f"blog/{f.stem}/index.html", m.group(0).rstrip(".,;:!?*_~")) for m in _LEGACY_URL_IN_SOURCE.finditer(text)]
    return out


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

    def test_links_once_allowed_from_the_import_are_flagged_on_their_own_page(self):
        # #105 (PR B): os 8 links da era Wix saíram dos posts, e a lista de exceção da importação acabou.
        for rel, url in (("blog/data-pipelines-empresariais-como/index.html",
                          "https://www.guilhermefavaron.com.br/post/machine-learning-producao-modelos-empresariais-roi"),
                         ("blog/modernizacao-de-sistemas-legados/index.html", "https://www.guilhermefavaron.com.br/pagina-inicial")):
            with self.subTest(rel=rel):
                self.setUp()
                self.assertEqual(len(self.flagged(rel, page(f'<a href="{url}">x</a>', robots=ROBOTS_INDEX))), 1)

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

    def test_post_sources_have_no_old_domain_link(self):
        # #105 (PR B): nenhum post, indexável ou não, linka o domínio antigo (inline, referência, autolink, URL solta
        # ou www.). Menção em texto sem esquema nem www. não é link e fica para a tabela de aprovação do Gui.
        found = old_links_in_sources(Path(__file__).resolve().parent.parent / "blog-src/src/content/posts")
        self.assertEqual(found, [])
        self.assertFalse(hasattr(C, "OLD_DOMAIN_DEAD_LINKS"))

    def test_indexable_post_sources_have_no_legacy_project_link(self):
        # #105 (PR B, achado P3 do Claude): mindapps.ai (e subdomínios) e o Substack antigo não são o domínio do
        # cutover, então o check_site não os vê; a guarda fica nos fontes dos posts indexáveis. Só conta o que vira
        # link (esquema, // ou www.); "via mindapps.ai" em texto corrido fica para a tabela do Gui.
        found = legacy_links_in_sources(Path(__file__).resolve().parent.parent / "blog-src/src/content/posts")
        self.assertEqual(found, [])

    def test_legacy_scan_catches_link_forms_and_skips_text_and_noindex(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "p.md").write_text(
                "---\ntitle: x\n---\n[a](https://mindapps.ai/lab) <http://academia-ia.mindapps.ai/> www.mindapps.ai/x "
                "https://ia-aplicada.substack.com/p/y [b][r]\ntexto via mindapps.ai e ia-aplicada.substack.com "
                '[c](//academia-ia.mindapps.ai) <a href="//mindapps.ai/x">d</a>\n\n'
                "[r]: https://IA-APLICADA.substack.com/\n", encoding="utf-8")
            Path(d, "pod.md").write_text("---\ntitle: x\nindex: false\n---\n[a](https://mindapps.ai/)\n", encoding="utf-8")
            got = [u for _, u in legacy_links_in_sources(Path(d))]
        self.assertEqual(got, ["https://mindapps.ai/lab", "http://academia-ia.mindapps.ai/", "www.mindapps.ai/x",
                               "https://ia-aplicada.substack.com/p/y", "//academia-ia.mindapps.ai", "//mindapps.ai/x",
                               "https://IA-APLICADA.substack.com/"])

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

    def test_blog_catalog_json_is_the_only_json_allowed(self):
        # Barreira 2 (#193): só o caminho exato blog/catalogo.json passa; qualquer outro .json sob /blog/ falha.
        self.w("blog/catalogo.json", '{"posts":[]}')
        self.assertEqual(self.problems(), [])
        (self.site / "blog/catalogo.json").unlink()
        for name in ("blog/dados.json", "blog/sub/catalogo.json", "blog/CATALOGO.JSON"):
            self.w(name, "{}")
            self.assertTrue(any(name in p and "extensão fora da lista do blog" in p for p in self.problems()), name)
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
        self.assertEqual(C.BLOG_PRIVATE_PAGES, frozenset({"assinar", "inscricao-enviada", "inscricao-confirmada", "inscricao-invalida"}))

    def test_index_false_post_with_noindex_after_cutover_is_required(self):
        (self.posts / "um-post.md").write_text("---\ntitle: x\nslug: um-post\nindex: false\n---\n", encoding="utf-8")
        self.w("blog/sitemap.xml", SITEMAP.format("<url><loc>https://guifav.dev/blog/</loc></url>"))
        self.w("blog/um-post/index.html", page('<a href="/blog/">b</a>', robots=ROBOTS_INDEX))
        self.assertTrue(any("index: false" in p for p in self.problems(pre_cutover=False)))

    def test_worker_proxied_paths_pass_and_unknown_neighbors_fail(self):
        # #166: /cursos/chatgpt-no-dia-a-dia é servido por Worker na Cloudflare (fora do _site).
        self.w("cursos/index.html", page('<a href="/cursos/chatgpt-no-dia-a-dia">a</a>', robots=ROBOTS_NOINDEX))
        self.assertEqual(self.problems(pre_cutover=False), [])
        for bad in ("/cursos/chatgpt-no-dia-a-dia-outro", "/cursos/chatgpt-no-dia-a-dia/",
                    "/cursos/chatgpt-no-dia-a-dia/aula-1", "/cursos/academia-ia"):
            with self.subTest(bad=bad):
                self.w("cursos/index.html", page(f'<a href="{bad}">a</a>', robots=ROBOTS_NOINDEX))
                self.assertTrue(any("link interno quebrado" in p and bad in p
                                    for p in self.problems(pre_cutover=False)))


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
            "disallow": ("_mockups/x.html", page("x", robots="")),
            "noindex": ("rascunho/index.html", page("x", robots=ROBOTS_NOINDEX)),
        }
        url = {"404.html": "https://guifav.dev/404.html", "disallow": "https://guifav.dev/_mockups/x.html",
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

    def test_robots_cannot_block_sitemap_urls(self):
        # Bloquear /blog/ ou um post em blog/sitemap.xml falha.
        for robots in (
            "User-agent: *\nDisallow: /blog/\n\nSitemap: https://guifav.dev/sitemap.xml\n",
            "User-agent: *\nDisallow: /blog/um-post/\n\nSitemap: https://guifav.dev/sitemap.xml\n",
            "User-agent: Googlebot\nDisallow: /blog/\n\nUser-agent: *\nDisallow: /admin/\n\nSitemap: https://guifav.dev/sitemap.xml\n",
            "User-agent: bingbot\nDisallow: /portfolio/\n\nUser-agent: *\nDisallow: /admin/\n\nSitemap: https://guifav.dev/sitemap.xml\n",
        ):
            with self.subTest(robots=robots):
                self.w("robots.txt", robots)
                self.assertTrue(self.has("bloqueada pelo robots.txt"), self.problems(pre_cutover=False))
        # Allow mais específico que Disallow libera a URL do sitemap.
        self.w(
            "robots.txt",
            "User-agent: *\nDisallow: /portfolio/\nAllow: /portfolio/\n\nSitemap: https://guifav.dev/sitemap.xml\n",
        )
        self.assertFalse(self.has("bloqueada pelo robots.txt"))


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
                 ("ferramentas/llm-ranking/index.html", "Ranking: Qual é o Melhor LLM?", False),
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
                       ("blog/assinar/index.html", "expert-insights"),
                       ("privacidade/index.html", "legal"), ("termos/index.html", "legal"),
                       ("prioridade/index.html", "legal"), ("prioridade/x/index.html", None),
                       ("privacidade/x/index.html", None), ("termos/x/index.html", None),
                       ("blog/inscricao-enviada/index.html", "expert-insights"),
                       ("blog/inscricao-confirmada/index.html", "expert-insights"),
                       ("blog/inscricao-invalida/index.html", "expert-insights"),
                       ("blog/o-exame-virou-conspiracao/index.html", "insights-artigo"),
                       ("ferramentas/planilha-custos-ia/index.html", "materiais"), ("ferramentas/mapa-decisao-ia/enviado/index.html", "materiais"),
                       ("materiais/livro-desbloqueando-a-inteligencia-artificial/index.html", "materiais"),
                       ("materiais/livro-desbloqueando-a-inteligencia-artificial/enviado/index.html", "materiais"),
                       ("materiais/livro-desbloqueando-a-inteligencia-artificial/x/index.html", None),
                       ("consultoria/index.html", "consultoria"), ("consultoria/x/index.html", None),
                       ("ferramentas/planilha-custos-ia/x/index.html", None),
                       ("ferramentas/index.html", "ferramentas"),
                       ("ferramentas/llm-ranking/index.html", "ferramentas"), ("ferramentas/llm-ranking/x/index.html", None),
                       ("cursos/index.html", "cursos"), ("cursos/x/index.html", None),
                       ("tools/index.html", None), ("tools/llm-ranking/index.html", None),
                       ("blog/pagina/x/index.html", None),
                       ("blog/a/b/index.html", None),
                       ("blog/tema/ia-sobre-dados-da-empresa/index.html", "insights-lista"),
                       ("blog/tema/a/b/index.html", None), ("blog/tema/Maiuscula/index.html", None)):
            with self.subTest(rel=rel):
                self.assertEqual(E(rel), g)
        self.assertEqual(set(C.CONTENT_GROUPS), {"home", "portfolio", "insights-lista", "insights-artigo", "expert-insights",
                                                 "materiais", "consultoria", "ferramentas", "legal", "cursos"})

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
        html = self.doc("<title>Ferramenta | Guilherme Favaron</title>", self.GA + self.cfg(",{content_group:'home'}"))
        self.assertTrue(any("sem grupo de conteúdo previsto" in p for p in self.check("ferramenta-nova/index.html", html)))

    def test_llm_ranking_and_tools_index_are_ferramentas(self):
        for rel in ("ferramentas/index.html", "ferramentas/llm-ranking/index.html"):
            html = self.doc("<title>Ferramentas | Guilherme Favaron</title>", self.GA + self.cfg(",{content_group:'ferramentas'}"))
            self.assertEqual(self.check(rel, html), [], rel)
            html = self.doc("<title>Ferramentas | Guilherme Favaron</title>", self.GA + self.cfg(",{content_group:'materiais'}"))
            self.assertTrue(any("esperado ['ferramentas']" in p for p in self.check(rel, html)), rel)

    def test_courses_page_is_cursos(self):
        html = self.doc("<title>Cursos | Guilherme Favaron</title>", self.GA + self.cfg(",{content_group:'cursos'}"))
        self.assertEqual(self.check("cursos/index.html", html), [])
        bad = self.doc("<title>Cursos | Guilherme Favaron</title>", self.GA + self.cfg(",{content_group:'ferramentas'}"))
        self.assertTrue(any("esperado ['cursos']" in p for p in self.check("cursos/index.html", bad)))

    def test_no_ga_no_group_needed(self):
        self.assertEqual(self.check("ferramentas/llm-ranking/index.html", self.doc("<title>LLM Ranking | Guilherme Favaron</title>")), [])

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
        for rel in ("index.html", "portfolio/index.html", C.CONSULTING_PAGE, *sorted(C.MATERIAL_PAGES), *sorted(C.TOOLS_PAGES), C.COURSES_PAGE, *C.LEGAL_PAGES, C.PRIORITY_PAGE):
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
        for path in C.MATERIALS.values():
            self.assertIn('data-ga-client-id data-clarity-mask="true"', self.read(f"{path}/index.html"))
        self.assertIn('data-clarity-mask="true"', self.read("blog-src/src/components/SubscribeForm.astro"))
        self.assertIn('data-clarity-mask="true"', self.read(C.PRIORITY_PAGE))

    def test_privacy_mentions_clarity(self):
        t = self.read("privacidade/index.html")
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
    FILES = ("index.html", "portfolio/index.html", "consultoria/index.html", "ferramentas/index.html", "cursos/index.html", "404.html",
             "blog-src/src/layouts/Base.astro",
             "blog-src/src/layouts/PostList.astro", "blog-src/src/components/SubscribeForm.astro",
             "blog-src/src/pages/[slug].astro", "blog-src/src/pages/assinar.astro",
             "blog-src/src/pages/inscricao-enviada.astro", "blog-src/src/pages/inscricao-confirmada.astro",
             "blog-src/src/pages/inscricao-invalida.astro", "privacidade/index.html", "termos/index.html", "prioridade/index.html",
             "blog-src/src/pages/rss.xml.ts", "workers/newsletter/src/email.ts", "workers/admin/src/index.ts",
             "workers/admin/src/painel.ts", "workers/admin/src/layout.ts")
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
        text = re.sub(
            r"\bNEWSLETTER_(?:FORM_ENABLED|ENDPOINT|SUBSTACK_URL)\b|\bnewsletter_confirm\b|newsletter-box|workers/newsletter|\.\./\.\./newsletter/src|class=\"[^\"]*\"",
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
                     "<!-- newsletter -->", "// Página da newsletter (issue #21)",
                     "action = 'newsletter_confirm'", "import { CANAIS } from '../../newsletter/src/origem.ts';"):
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
        for s in ("title: 'Assinantes | Expert Insights'", "<h1>Assinantes dos Expert Insights</h1>"):
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
        self.assertEqual(C.CTA_LOCATIONS, ("hero", "projetos", "consultoria", "chamada-final", "consultoria-pagina",
                                           "como-contratar"))
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
        # Issues #99 e #183: o formulário de material leva data-ga-client-id e o campo oculto ga_cid, uma vez.
        cid = '<input type="hidden" name="ga_cid" value="">'
        for m, rel in (("planilha-custos-ia", "ferramentas/planilha-custos-ia/index.html"), (C.BOOK_ID, C.BOOK_PAGE)):
            form = (f'<form id="receber" {{attr}}method="post" action="https://insights.guifav.dev/material">'
                    f'<input type="hidden" name="material" value="{m}">{{cid}}</form>')
            with self.subTest(rel=rel):
                self.assertEqual(self.check(self.doc(form.format(attr="data-ga-client-id ", cid=cid)), rel=rel), [])
            for name, body in {
                "sem atributo": form.format(attr="", cid=cid),
                "sem campo": form.format(attr="data-ga-client-id ", cid=""),
                "campo duas vezes": form.format(attr="data-ga-client-id ", cid=cid * 2),
                "dois formulários": form.format(attr="data-ga-client-id ", cid=cid) * 2,
                "sem formulário": "",
                "formulário comentado": "<!-- " + form.format(attr="data-ga-client-id ", cid=cid) + " -->",
                "campo comentado": form.format(attr="data-ga-client-id ", cid="<!-- " + cid + " -->"),
            }.items():
                with self.subTest(rel=rel, name=name):
                    self.assertTrue(any("formulário de material" in p for p in self.check(self.doc(body), rel=rel)), name)

    def test_generate_lead_only_on_material_sent_pages_with_the_folder_id(self):
        # Issues #99 e #183: generate_lead na página de enviado de cada material, com data-material-id igual ao id no catálogo.
        lead = lambda m: f'<header data-ga-event="generate_lead" data-material-id="{m}">x</header>'
        for m, path in C.MATERIALS.items():
            self.assertEqual(self.check(self.doc(lead(m)), rel=f"{path}/enviado/index.html"), [], m)
        enviado = "ferramentas/planilha-custos-ia/enviado/index.html"
        livro_enviado = f"materiais/{C.BOOK_ID}/enviado/index.html"
        for name, (rel, body) in {
            "falta": (enviado, ""),
            "falta livro": (livro_enviado, ""),
            "duas vezes": (enviado, lead("planilha-custos-ia") * 2),
            "outro material": (enviado, lead("mapa-decisao-ia")),
            "outro material no livro": (livro_enviado, lead("mapa-decisao-ia")),
            "sem id": (enviado, '<header data-ga-event="generate_lead">x</header>'),
            "fora do enviado": ("ferramentas/planilha-custos-ia/index.html", lead("planilha-custos-ia")),
            "livro fora do enviado": (C.BOOK_PAGE, lead(C.BOOK_ID)),
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
                            (C.CONSULTING_PAGE, "gtag('config','G-K7Y87QNCX8'"),
                            *((p, "gtag('config','G-K7Y87QNCX8'") for p in sorted(C.MATERIAL_PAGES)),
                            *((p, "gtag('config','G-K7Y87QNCX8'") for p in sorted(C.TOOLS_PAGES)),
                            (C.COURSES_PAGE, "gtag('config','G-K7Y87QNCX8'"),
                            *((p, "gtag('config','G-K7Y87QNCX8'") for p in C.LEGAL_PAGES),
                            (C.PRIORITY_PAGE, "gtag('config','G-K7Y87QNCX8'"),
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
    FOOT = '<footer><a href="/privacidade/#cookies" data-consent-open>Cookies</a></footer>'

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
                           "fora do rodapé": "<main><a href='/privacidade/#cookies' data-consent-open>Cookies</a></main><footer></footer>",
                           "dois links": "<footer>" + 2 * "<a href='/privacidade/#cookies' data-consent-open>Cookies</a>" + "</footer>",
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
        for rel in ("index.html", "portfolio/index.html", C.CONSULTING_PAGE, *sorted(C.MATERIAL_PAGES), *sorted(C.TOOLS_PAGES), C.COURSES_PAGE, *C.LEGAL_PAGES, C.PRIORITY_PAGE):
            with self.subTest(rel=rel):
                self.assertEqual(self.read(rel).count(f'<script src="{src}"></script>'), 1)
        self.assertEqual(self.read("blog-src/src/layouts/Base.astro").count(
            "{!DRAFT_PREVIEW && <script data-site-script is:inline src={CONSENT_SRC}></script>}"), 1)

    def test_policy_has_cookies_section(self):
        t = self.read("privacidade/index.html")
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
        for rel in ("index.html", "portfolio/index.html", C.CONSULTING_PAGE, *sorted(C.MATERIAL_PAGES), *sorted(C.TOOLS_PAGES), C.COURSES_PAGE, *C.LEGAL_PAGES, C.PRIORITY_PAGE):
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
        # #136: a home nova não tem CTA no hero nem o carrossel (foi para a /consultoria/); agendar e escrever ficam na
        # /consultoria/.
        for loc, n in (("hero", 0), ("projetos", 0), ("consultoria", 1)):
            self.assertEqual(home.count(f'data-cta-location="{loc}"'), n, loc)
        self.assertEqual(self.read("portfolio/index.html").count('data-cta-location="chamada-final"'), 2)
        consulting = self.read(C.CONSULTING_PAGE)
        for loc, n in (("consultoria-pagina", 1), ("como-contratar", 2), ("projetos", 1), ("chamada-final", 2)):
            self.assertEqual(consulting.count(f'data-cta-location="{loc}"'), n, loc)

    def test_signup_marker_in_source(self):
        self.assertIn('<article class="post page" data-ga-event="sign_up">',
                      self.read("blog-src/src/pages/inscricao-confirmada.astro"))

    def test_home_gif_download_marker_in_source(self):
        # #148: o link do GIF do ciclo na home leva data-ga-download para o track.js emitir file_download.
        self.assertIn(
            '<a class="dflow-dl" href="/assets/ciclo-dados-ia.gif" download="ciclo-de-dados-e-ia-guifav.gif" '
            'data-ga-download="ciclo-dados-ia">',
            self.read("index.html"),
        )

    def test_privacy_mentions_events(self):
        t = self.read("privacidade/index.html")
        self.assertIn("alguns cliques: contato, agendamento, links do rodapé, troca de idioma e de tema, a "
                      "confirmação da inscrição, o pedido e o download de materiais e o download do GIF do "
                      "diagrama da home (sem o email)", t)

    def test_privacy_discloses_google_ads(self):
        # #64: GA4 vinculado ao Google Ads com publicidade personalizada (Gui, 13/11/2026). A política diz o recurso,
        # os cookies, como o Google usa os dados e como desativar.
        t = self.read("privacidade/index.html")
        for must in ("Google Ads", "publicidade personalizada", "<code>_ga</code>", "<code>_ga_K7Y87QNCX8</code>",
                     "medir campanhas", "mostrar anúncios do guifav.dev a quem já visitou o site",
                     # r1 (Kimi e Astra P2): o uso conjunto de cookies do site e de publicidade do Google, exigido pela
                     # política de recursos de publicidade do Google Analytics.
                     "usam em conjunto os cookies do Google Analytics, que são do site, e os cookies de publicidade do Google",
                     "O seu email nunca é enviado ao Google.", "desativar os anúncios personalizados",
                     "https://adssettings.google.com/", "https://tools.google.com/dlpage/gaoptout",
                     "https://policies.google.com/technologies/partner-sites"):
            self.assertIn(must, t)
        self.assertIn("Atualizada em 27 de novembro de 2026.", t)  # #68: Cookies; 23/11: Materiais; 24/11: CRM (#97) e /privacidade/ (#146); 26/11: terceiros (#154) e remoção (#134); 27/11: prioridade e cursos (#197)

    def test_privacy_says_material_record_goes_to_the_crm(self):
        # #97 fase 2 e #134: o registro dos materiais vai ao Twenty. O download cria o contato; o pedido sem download só
        # entra se o email já for contato; a remoção apaga pedidos e downloads nos dois lugares, guarda a marca da remoção
        # por 8 dias para links antigos não refazerem o registro, e o contato só sai a pedido.
        t = self.read("privacidade/index.html")
        for must in ("O registro vai também para o meu CRM",
                     "o download cria lá um contato só com o seu email, se ele ainda não existir",
                     "o pedido sem download só entra no CRM se o seu email já for um contato",
                     "apago os pedidos e downloads, no Cloudflare e no CRM.",
                     "Para que um link antigo não refaça o registro, guardo no Cloudflare só uma marca da remoção: um "
                     "código calculado a partir do seu email, que sem a minha chave não permite descobrir o endereço, e "
                     "a data. Os links enviados antes da remoção, inclusive o de confirmação dos Expert Insights, deixam "
                     "de valer de vez, e a marca é apagada automaticamente depois de 8 dias, quando nenhum deles valeria "
                     "mais.",
                     "O contato continua no CRM com o resumo que veio deles (os temas de interesse, a data da última "
                     "ação, se o email foi comprovado e o identificador do Google Analytics), e só é apagado, com esse "
                     "resumo, se você pedir.",
                     # r1 (Astra P2): a data da página mudou; o início do registro é o deploy do Worker, não a versão.
                     "Pedidos e downloads feitos antes das 17h31 de 23 de novembro de 2026 (horário de Brasília) não foram "
                     "registrados.",
                     "que guardo com o pedido, também no CRM,",
                     '<h2 id="quem-processa">Quem processa</h2>', 'href="#quem-processa"',
                     "o CRM onde ficam os inscritos da lista, o registro dos pedidos e downloads de materiais e das "
                     "confirmações de inscrição, as inscrições nos cursos e as notas de prioridade e de engajamento"):
            self.assertIn(must, t)
        # r1 (Kimi P1): a remoção não apaga o contato nem o resumo dele; a frase antiga prometia mais do que o código faz.
        self.assertNotIn("e também o contato, se você pedir", t)
        self.assertNotIn("anteriores a esta versão da página", t)

    def test_privacy_describes_origin(self):
        # #114 e #146: a política mantém a descrição da origem da visita (sem URL completa, termo nem id de navegador).
        t = self.read("privacidade/index.html")
        for must in ('<h2 id="origem">De onde você chegou</h2>',
                     "Não guardo com a origem o endereço completo de onde você veio, o que você buscou nem identificador do seu navegador.",
                     "enquanto a aba está aberta (não é cookie e some ao fechar)",
                     "com ou sem o aceite dos cookies",
                     "log de envios do Resend",
                     "cliques de contato e de agendamento",
                     "legítimo interesse (Lei 13.709/2018, art. 7º, IX)"):
            self.assertIn(must, t)


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
        self.assertEqual(C.check_tools("ferramentas/mapa-decisao-ia/index.html", self.doc()), [])

    def test_tools_index_requires_four_links_and_collection_page(self):
        ld = ('<script type="application/ld+json">{"@context":"https://schema.org","@type":"CollectionPage",'
              '"url":"https://guifav.dev/ferramentas/","isPartOf":{"@id":"https://guifav.dev/#website"}}</script>')
        body = "<main>" + self.links(C.TOOLS_INDEX_LINKS) + "</main>"
        ok = f"<!DOCTYPE html><html><head>{TrackingTest.GA}{ld}</head><body>{body}</body></html>"
        self.assertEqual(C.check_tools("ferramentas/index.html", ok), [])
        self.assertTrue(any("CollectionPage" in p for p in C.check_tools(
            "ferramentas/index.html", f"<!DOCTYPE html><html><head>{TrackingTest.GA}</head><body>{body}</body></html>")))
        self.assertTrue(any(C.TOOLS_INDEX_LINKS[0] in p for p in C.check_tools(
            "ferramentas/index.html", f"<!DOCTYPE html><html><head>{TrackingTest.GA}{ld}</head><body><main></main></body></html>")))


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
        for slugs, bad in ((("b", "c"), []), (("b", "c", "podcast"), ["podcast"]), (("b", "c", "fora"), ["fora"]),
                           (("b", "c", "a"), ["a"]), (("b", "c", "b"), [])):
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

    def test_topic_page_cannot_link_to_index_false_post(self):
        # #107: post com index: false fica fora das páginas de tema, tanto com link relativo quanto absoluto.
        (self.posts / "podado.md").write_text("---\ntitle: p\nslug: podado\nindex: false\n---\n", encoding="utf-8")
        self.w("blog/podado/index.html", page('<a href="/blog/">blog</a>', robots=ROBOTS_NOINDEX))
        for href in ("/blog/podado/", "https://guifav.dev/blog/podado/", "../../podado/"):
            with self.subTest(href=href):
                self.w("blog/tema/agentes-de-ia/index.html",
                       f"<!DOCTYPE html><html><head>{FIXTURE_TITLE}{ROBOTS_INDEX}"
                       f'<link rel="canonical" href="{self.URL}"></head><body>'
                       f'<a href="/blog/um-post/">p</a><a href="{href}">podado</a></body></html>')
                got = self.problems(pre_cutover=False)
                self.assertEqual(got, ["blog/tema/agentes-de-ia/index.html: página de tema lista post com index: false: podado"])


class RelatedWiredTest(Base):
    def test_main_runs_the_related_rule(self):
        consts = Path(self.tmp.name) / "consts.ts"
        consts.write_text("export const PRE_CUTOVER_NOINDEX = false;\n", encoding="utf-8")
        self.assertEqual(self.problems(pre_cutover=False), [])  # check() sozinho não tem a regra
        err = io.StringIO()
        with isolate_main_rule("check_related"), contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
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
        self.assertFalse((self.REPO / "tools").exists())
        self.assertEqual(C.check_tools("ferramentas/index.html", self.read("ferramentas/index.html")), [])
        for rel in ("ferramentas/index.html", "ferramentas/mapa-decisao-ia/index.html", "ferramentas/planilha-custos-ia/index.html",
                    "ferramentas/llm-ranking/index.html", "ferramentas/seo-geo-aeo-framework/framework-seo-aeo.html",
                    "cursos/index.html",
                    C.BOOK_PAGE):
            with self.subTest(rel=rel):
                self.assertTrue((self.REPO / rel).is_file())
        for rel in ("index.html", "404.html", "portfolio/index.html", "consultoria/index.html",
                    "cursos/index.html", "privacidade/index.html", "termos/index.html",
                    "ferramentas/index.html", "ferramentas/llm-ranking/index.html",
                    "ferramentas/mapa-decisao-ia/index.html", "ferramentas/mapa-decisao-ia/enviado/index.html",
                    "ferramentas/planilha-custos-ia/index.html", "ferramentas/planilha-custos-ia/enviado/index.html",
                    "ferramentas/seo-geo-aeo-framework/framework-seo-aeo.html",
                    C.BOOK_PAGE, f"materiais/{C.BOOK_ID}/enviado/index.html",
                    "blog-src/src/layouts/Base.astro", "blog-src/src/lib/topics.ts"):
            with self.subTest(no_old_tools=rel):
                self.assertNotIn("/tools/", self.read(rel))


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
        with isolate_main_rule("check_seo"):
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


def post_graph(slug: str = "p", title: str = "Um post") -> dict:
    """O @graph que o [slug].astro emite (#105), para os testes partirem de um post válido."""
    url = f"https://guifav.dev/blog/{slug}/"
    return {"@context": "https://schema.org", "@graph": [
        {"@type": "BlogPosting", "headline": title, "description": "d", "datePublished": "2026-01-01T00:00:00.000Z",
         "dateModified": "2026-01-01T00:00:00.000Z", "author": {"@id": "https://guifav.dev/#person"},
         "publisher": {"@id": "https://guifav.dev/#person"}, "isPartOf": {"@id": "https://guifav.dev/#website"},
         "mainEntityOfPage": url, "url": url, "inLanguage": "pt-BR"},
        {"@type": "BreadcrumbList", "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Guilherme Favaron", "item": "https://guifav.dev/"},
            {"@type": "ListItem", "position": 2, "name": "Insights", "item": "https://guifav.dev/blog/"},
            {"@type": "ListItem", "position": 3, "name": title, "item": url}]},
        {"@type": "Person", "@id": "https://guifav.dev/#person", "name": "Guilherme Favaron", "url": "https://guifav.dev/"},
    ]}


def ld_script(data) -> str:
    return f'<script data-site-script type="application/ld+json">{json.dumps(data, ensure_ascii=False)}</script>'


class PostLdTest(unittest.TestCase):
    """#105: todo post indexável sai com o @graph de BlogPosting, BreadcrumbList e Person mínima."""

    HOME = ('<script type="application/ld+json">{"@context":"https://schema.org","@type":"Person",'
            '"@id":"https://guifav.dev/#person","name":"Guilherme de Mauro Favaron","url":"https://guifav.dev"}</script>'
            '<script type="application/ld+json">{"@context":"https://schema.org","@type":"WebSite",'
            '"@id":"https://guifav.dev/#website","name":"Guilherme Favaron","url":"https://guifav.dev/",'
            '"publisher":{"@id":"https://guifav.dev/#person"}}</script>')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        r = Path(self.tmp.name)
        self.site, self.posts = r / "_site", r / "posts"
        self.posts.mkdir()
        self.home(self.HOME)

    def tearDown(self):
        self.tmp.cleanup()

    def home(self, head):
        self.site.mkdir(exist_ok=True)
        (self.site / "index.html").write_text(f"<html><head>{head}</head><body></body></html>", encoding="utf-8")

    def post(self, head, slug="p", index=True):
        fm = f"---\ntitle: x\nslug: {slug}\n" + ("" if index else "index: false\n") + "---\n"
        (self.posts / f"{slug}.md").write_text(fm, encoding="utf-8")
        d = self.site / "blog" / slug
        d.mkdir(parents=True, exist_ok=True)
        (d / "index.html").write_text(f"<html><head>{head}</head><body><h1>x</h1></body></html>", encoding="utf-8")

    def ld(self):
        return C.check_post_ld(self.site, self.posts)

    def test_valid_post_passes(self):
        self.post(ld_script(post_graph()))
        self.assertEqual(self.ld(), [])

    def test_escaped_lt_from_jsonldscript_is_read(self):
        # O jsonLdScript do blog troca "<" por \u003c; o título com "<" continua batendo com a trilha.
        self.post(ld_script(post_graph(title="A < B")).replace("A < B", "A \\u003c B"))
        self.assertEqual(self.ld(), [])

    def test_indexable_post_without_graph_fails(self):
        self.post("")
        self.assertEqual(self.ld(), ["blog/p/index.html: post indexável sem o JSON-LD do post (#105): nenhum BlogPosting"])

    def test_noindex_post_is_not_judged(self):
        self.post("", index=False)
        self.assertEqual(self.ld(), [])

    def test_graph_that_does_not_run_is_not_counted(self):
        # #136: o _ld_blocks ignora JSON-LD que o navegador não lê como dado; o post precisa do @graph de verdade
        script = ld_script(post_graph())
        for nome, head in (("noscript", f"<noscript>{script}</noscript>"),
                           ("template", f"<template>{script}</template>"),
                           ("comentário", f"<!-- {script} -->"),
                           ("data-type", script.replace(' type="application/ld+json"', ' data-type="application/ld+json"'))):
            with self.subTest(onde=nome):
                self.post(head)
                self.assertEqual(self.ld(), ["blog/p/index.html: post indexável sem o JSON-LD do post (#105): nenhum BlogPosting"])

    def test_post_without_page_is_left_to_check(self):
        (self.posts / "sem-pagina.md").write_text("---\ntitle: x\nslug: sem-pagina\n---\n", encoding="utf-8")
        self.assertEqual(self.ld(), [])

    def _mutate(self, fn):
        g = post_graph()
        fn(g)
        return g

    def test_each_broken_piece_fails_with_its_reason(self):
        def node(g, t):
            return next(n for n in g["@graph"] if n.get("@type") == t)

        def drop(g, t):
            g["@graph"] = [n for n in g["@graph"] if n.get("@type") != t]

        def crumbs(g):
            return node(g, "BreadcrumbList")["itemListElement"]

        cases = {
            "sem Person": (lambda g: drop(g, "Person"), "0 nós Person"),
            "Person com outro nome": (lambda g: node(g, "Person").update(name="Guilherme de Mauro Favaron"),
                                      "a Person do @graph"),
            "Person com url sem barra": (lambda g: node(g, "Person").update(url="https://guifav.dev"), "a Person do @graph"),
            "Person com outro @id": (lambda g: node(g, "Person").update({"@id": "https://guifav.dev/#autor"}), "0 nós Person"),
            "Person sem @type": (lambda g: node(g, "Person").pop("@type"), "a Person do @graph"),
            "author de outro @id": (lambda g: node(g, "BlogPosting").update(author={"@id": "https://x.org/#p"}),
                                    "author não aponta"),
            "author ausente": (lambda g: node(g, "BlogPosting").pop("author"), "author não aponta"),
            "author em texto": (lambda g: node(g, "BlogPosting").update(author="Guilherme Favaron"), "author não aponta"),
            "publisher ausente": (lambda g: node(g, "BlogPosting").pop("publisher"), "publisher não aponta"),
            "publisher GRI": (lambda g: node(g, "BlogPosting").update(
                publisher={"@type": "Organization", "name": "GRI Institute", "url": "https://www.griinstitute.org/"}),
                "publisher não aponta"),
            "isPartOf ausente": (lambda g: node(g, "BlogPosting").pop("isPartOf"), "isPartOf não aponta"),
            "isPartOf de outro @id": (lambda g: node(g, "BlogPosting").update(isPartOf={"@id": "https://guifav.dev/#blog"}),
                                      "isPartOf não aponta"),
            "url de outro post": (lambda g: node(g, "BlogPosting").update(url="https://guifav.dev/blog/q/"),
                                  "url diferente"),
            "mainEntityOfPage de outro post": (
                lambda g: node(g, "BlogPosting").update(mainEntityOfPage="https://guifav.dev/blog/q/"),
                "mainEntityOfPage diferente"),
            # o último nível da trilha também em branco para bater com o headline; o isolamento vem da asserção do motivo "headline vazio"
            "headline em branco": (lambda g: (node(g, "BlogPosting").update(headline=" "), crumbs(g)[2].update(name=" ")),
                                   "headline vazio"),
            "sem BreadcrumbList": (lambda g: drop(g, "BreadcrumbList"), "0 BreadcrumbList"),
            "trilha com 2 níveis": (lambda g: crumbs(g).pop(1), "BreadcrumbList fora da trilha"),
            "trilha com 4 níveis": (lambda g: crumbs(g).insert(2, {"@type": "ListItem", "position": 3, "name": "Tema",
                                                                   "item": "https://guifav.dev/blog/tema/x/"}),
                                    "BreadcrumbList fora da trilha"),
            "nível a mais depois do post": (lambda g: crumbs(g).append(dict(crumbs(g)[2], position=4)),
                                            "BreadcrumbList fora da trilha"),
            "posição fora de ordem": (lambda g: crumbs(g)[1].update(position=3), "BreadcrumbList fora da trilha"),
            "posição em texto": (lambda g: crumbs(g)[0].update(position="1"), "BreadcrumbList fora da trilha"),
            "blog sem barra": (lambda g: crumbs(g)[1].update(item="https://guifav.dev/blog"), "BreadcrumbList fora da trilha"),
            "home sem barra": (lambda g: crumbs(g)[0].update(item="https://guifav.dev"), "BreadcrumbList fora da trilha"),
            "último nível de outro post": (lambda g: crumbs(g)[2].update(item="https://guifav.dev/blog/q/"),
                                           "BreadcrumbList fora da trilha"),
            "último nível com outro nome": (lambda g: crumbs(g)[2].update(name="Outro"), "BreadcrumbList fora da trilha"),
            "nível sem nome": (lambda g: crumbs(g)[1].update(name=""), "BreadcrumbList fora da trilha"),
            "nível que não é ListItem": (lambda g: crumbs(g)[0].update({"@type": "Thing"}), "BreadcrumbList fora da trilha"),
            "duas BreadcrumbList": (lambda g: g["@graph"].append(dict(node(g, "BreadcrumbList"))), "2 BreadcrumbList"),
            "duas Person com o @id": (lambda g: g["@graph"].append(dict(node(g, "Person"))), "2 nós Person"),
        }
        for name, (fn, reason) in cases.items():
            with self.subTest(name=name):
                self.post(ld_script(self._mutate(fn)))
                got = self.ld()
                self.assertEqual(len(got), 1, got)
                self.assertTrue(got[0].startswith("blog/p/index.html: "), got)
                self.assertIn(reason, got[0])

    def test_graph_shape(self):
        g = post_graph()
        flat = [dict(n, **{"@context": "https://schema.org"}) for n in g["@graph"]]
        cases = {
            "nós em scripts separados, sem @graph": ("".join(ld_script(n) for n in flat), "não está num @graph"),
            "lista de raízes sem @graph": (ld_script(flat), "não está num @graph"),
            "comentado": (f"<!-- {ld_script(g)} -->", "nenhum BlogPosting"),
            "@context errado": (ld_script(dict(g, **{"@context": "https://example.org"})), "nenhum BlogPosting"),
            "sem @context": (ld_script({"@graph": g["@graph"]}), "nenhum BlogPosting"),
            "@graph que não é lista": (ld_script({"@context": "https://schema.org", "@graph": g["@graph"][0]}),
                                       "nenhum BlogPosting"),
            "json inválido": (ld_script(g).replace("}", "", 1), "nenhum BlogPosting"),
            "dois BlogPosting": (ld_script(g) + ld_script(dict(g["@graph"][0], **{"@context": "https://schema.org"})),
                                 "2 BlogPosting"),
            "Person em outro script": (ld_script(dict(g, **{"@graph": g["@graph"][:2]}))
                                       + ld_script(dict(g["@graph"][2], **{"@context": "https://schema.org"})),
                                       "0 nós Person"),
            "outro tipo": (ld_script(g).replace('"BlogPosting"', '"Article"'), "nenhum BlogPosting"),
        }
        for name, (head, reason) in cases.items():
            with self.subTest(name=name):
                self.post(head)
                got = self.ld()
                self.assertEqual(len(got), 1, got)
                self.assertTrue(got[0].startswith("blog/p/index.html: post indexável sem o JSON-LD do post (#105): "), got)
                self.assertIn(reason, got[0])

    def test_ispartof_follows_the_home_website_id(self):
        # O @id do isPartOf é conferido contra o WebSite da home, não contra uma constante solta.
        self.home(self.HOME.replace("https://guifav.dev/#website", "https://guifav.dev/#site"))
        self.post(ld_script(post_graph()))
        got = self.ld()
        self.assertEqual(len(got), 1, got)
        self.assertIn("isPartOf", got[0])
        self.assertIn("https://guifav.dev/#site", got[0])
        g = post_graph()
        g["@graph"][0]["isPartOf"] = {"@id": "https://guifav.dev/#site"}
        self.post(ld_script(g))
        self.assertEqual(self.ld(), [])

    def test_home_website_without_id_fails_once(self):
        for bad_home in (
            self.HOME.replace('"@id":"https://guifav.dev/#website",', ""),
            self.HOME.replace("https://guifav.dev/#website", "   "),
        ):
            with self.subTest(bad_home=bad_home):
                self.home(bad_home)
                self.post(ld_script(post_graph()))
                self.post(ld_script(post_graph("q")), slug="q")
                got = self.ld()
                self.assertEqual(got, ["index.html: sem o WebSite com @id para o isPartOf dos posts (#105)"])

    def test_home_website_id_ignores_other_websites(self):
        # Só vale o WebSite que o check_seo reconhece (nome e URL da home), e o @id da Person nunca serve.
        other = ('<script type="application/ld+json">{"@context":"https://schema.org","@type":"WebSite",'
                 '"@id":"https://guifav.dev/#outro","name":"Outro","url":"https://guifav.dev/"}</script>')
        self.assertEqual(C._home_website_id(other + self.HOME), "https://guifav.dev/#website")
        self.assertIsNone(C._home_website_id(other))
        self.assertIsNone(C._home_website_id(self.HOME.replace("https://guifav.dev/#website", "https://guifav.dev/#person")))
        self.assertIsNone(C._home_website_id(self.HOME.replace("https://guifav.dev/#website", "   ")))
        self.assertIsNone(C._home_website_id(self.HOME.replace('"https://guifav.dev/#website"', "1")))

    def test_repo_home_and_blog_consts_agree_on_the_website_id(self):
        root = Path(__file__).resolve().parent.parent
        home_id = C._home_website_id((root / "index.html").read_text(encoding="utf-8"))
        self.assertEqual(home_id, "https://guifav.dev/#website")
        consts = (root / "blog-src/src/consts.ts").read_text(encoding="utf-8")
        self.assertIn(f"export const WEBSITE_ID = '{home_id}';", consts)
        self.assertIn("export const AUTHOR_ID = 'https://guifav.dev/#person';", consts)
        self.assertIn("export const AUTHOR_URL = 'https://guifav.dev/';", consts)
        self.assertIn("export const AUTHOR_NAME = 'Guilherme Favaron';", consts)

    def test_slug_template_builds_the_graph(self):
        src = (Path(__file__).resolve().parent.parent / "blog-src/src/pages/[slug].astro").read_text(encoding="utf-8")
        self.assertIn("const ld = postLdGraph({", src)
        self.assertIn("jsonLd={ld}", src)

    def test_main_runs_the_rule(self):
        consts = Path(self.tmp.name) / "consts.ts"
        consts.write_text("export const PRE_CUTOVER_NOINDEX = false;\n", encoding="utf-8")
        args = ["--site", str(self.site), "--posts", str(self.posts), "--consts", str(consts)]
        with isolate_main_rule("check_post_ld"):
            for head, code in (("", 1), (ld_script(post_graph()), 0)):
                with self.subTest(code=code):
                    self.post(head)
                    out, err = io.StringIO(), io.StringIO()
                    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                        self.assertEqual(C.main(args), code)
                    self.assertEqual("JSON-LD do post (#105)" in err.getvalue(), code == 1)


def topic_graph(slug: str = "agentes-de-ia", name: str = "Agentes de IA em produção",
                items: list[tuple[str, str]] | None = None) -> dict:
    """O @graph que o tema/[tema].astro emite (#105), para os testes partirem de um tema válido."""
    url = f"https://guifav.dev/blog/tema/{slug}/"
    crumb_id = f"{url}#breadcrumb"
    entries = [("Primeiro post", "https://guifav.dev/blog/p1/"), ("Segundo post", "https://guifav.dev/blog/p2/")] if items is None else items
    return {"@context": "https://schema.org", "@graph": [
        {"@type": "CollectionPage", "@id": url, "url": url, "name": name,
         "description": "Artigos sobre agentes de IA.", "inLanguage": "pt-BR",
         "isPartOf": {"@id": "https://guifav.dev/#website"},
         "author": {"@id": "https://guifav.dev/#person"},
         "breadcrumb": {"@id": crumb_id},
         "mainEntity": {
             "@type": "ItemList",
             "itemListOrder": "https://schema.org/ItemListOrderAscending",
             "numberOfItems": len(entries),
             "itemListElement": [
                 {"@type": "ListItem", "position": i, "name": t, "url": u}
                 for i, (t, u) in enumerate(entries, 1)
             ],
         }},
        {"@type": "BreadcrumbList", "@id": crumb_id, "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Guilherme Favaron", "item": "https://guifav.dev/"},
            {"@type": "ListItem", "position": 2, "name": "Insights", "item": "https://guifav.dev/blog/"},
            {"@type": "ListItem", "position": 3, "name": name, "item": url},
        ]},
        {"@type": "Person", "@id": "https://guifav.dev/#person", "name": "Guilherme Favaron", "url": "https://guifav.dev/"},
    ]}


class TopicLdTest(unittest.TestCase):
    """#105: toda página de tema sai com @graph de CollectionPage, ItemList, BreadcrumbList e Person mínima."""

    HOME = PostLdTest.HOME
    DEFAULT_BODY = (
        '<ol class="post-list topic-list">'
        '<li class="post-item"><a href="/blog/p1/"><h2>Primeiro post</h2></a></li>'
        '<li class="post-item"><a href="/blog/p2/"><h2>Segundo post</h2></a></li>'
        "</ol>"
    )

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.site = Path(self.tmp.name) / "_site"
        self.home(self.HOME)

    def tearDown(self):
        self.tmp.cleanup()

    def home(self, head):
        self.site.mkdir(exist_ok=True)
        (self.site / "index.html").write_text(f"<html><head>{head}</head><body></body></html>", encoding="utf-8")

    def topic(self, head, body=DEFAULT_BODY, slug="agentes-de-ia"):
        d = self.site / "blog" / "tema" / slug
        d.mkdir(parents=True, exist_ok=True)
        (d / "index.html").write_text(f"<html><head>{head}</head><body>{body}</body></html>", encoding="utf-8")

    def ld(self):
        return C.check_topic_ld(self.site)

    def _mutate(self, fn):
        g = topic_graph()
        fn(g)
        return g

    def test_valid_topic_passes(self):
        self.topic(ld_script(topic_graph()))
        self.assertEqual(self.ld(), [])

    def test_topic_without_collection_page_fails(self):
        self.topic("")
        self.assertEqual(
            self.ld(),
            ["blog/tema/agentes-de-ia/index.html: página de tema sem o JSON-LD do tema (#105): nenhum CollectionPage"],
        )

    def test_each_broken_piece_fails_with_its_reason(self):
        def node(g, t):
            return next(n for n in g["@graph"] if n.get("@type") == t)

        def drop(g, t):
            g["@graph"] = [n for n in g["@graph"] if n.get("@type") != t]

        def crumbs(g):
            return node(g, "BreadcrumbList")["itemListElement"]

        def items(g):
            return node(g, "CollectionPage")["mainEntity"]["itemListElement"]

        def swap_items(g):
            els = items(g)
            els[0], els[1] = els[1], els[0]
            els[0]["position"], els[1]["position"] = 1, 2

        def drop_last_item(g):
            ml = node(g, "CollectionPage")["mainEntity"]
            ml["itemListElement"].pop()
            ml["numberOfItems"] = len(ml["itemListElement"])

        def add_extra_item(g):
            ml = node(g, "CollectionPage")["mainEntity"]
            ml["itemListElement"].append(
                {"@type": "ListItem", "position": 3, "name": "Terceiro", "url": "https://guifav.dev/blog/p3/"}
            )
            ml["numberOfItems"] = len(ml["itemListElement"])

        cases = {
            "sem Person": (lambda g: drop(g, "Person"), "0 nós Person"),
            "Person errada": (lambda g: node(g, "Person").update(name="Outro"), "a Person do @graph"),
            "author ausente": (lambda g: node(g, "CollectionPage").pop("author"), "author não aponta"),
            "isPartOf errado": (lambda g: node(g, "CollectionPage").update(isPartOf={"@id": "https://guifav.dev/#outro"}),
                                "isPartOf não aponta"),
            "@id errado": (lambda g: node(g, "CollectionPage").update({"@id": "https://guifav.dev/blog/tema/outro/"}),
                           "@id diferente"),
            "url errada": (lambda g: node(g, "CollectionPage").update(url="https://guifav.dev/blog/tema/outro/"),
                           "url diferente"),
            "name vazio": (lambda g: (node(g, "CollectionPage").update(name=" "), crumbs(g)[2].update(name=" ")),
                           "name vazio"),
            "description vazio": (lambda g: node(g, "CollectionPage").update(description=""), "description vazio"),
            "inLanguage errado": (lambda g: node(g, "CollectionPage").update(inLanguage="en"), "inLanguage diferente"),
            "breadcrumb @id ref errada": (
                lambda g: node(g, "CollectionPage").update(breadcrumb={"@id": "https://guifav.dev/#bc"}),
                "breadcrumb não aponta",
            ),
            "sem BreadcrumbList": (lambda g: drop(g, "BreadcrumbList"), "0 BreadcrumbList"),
            "BreadcrumbList @id errado": (
                lambda g: node(g, "BreadcrumbList").update({"@id": "https://guifav.dev/#bc"}),
                "BreadcrumbList com @id diferente",
            ),
            "trilha com 2 níveis": (lambda g: crumbs(g).pop(1), "BreadcrumbList fora da trilha"),
            "trilha nível 1 errado": (lambda g: crumbs(g)[0].update(name="Home"), "BreadcrumbList fora da trilha"),
            "trilha nível 2 errado": (lambda g: crumbs(g)[1].update(name="Blog"), "BreadcrumbList fora da trilha"),
            "trilha nível 3 nome diferente": (lambda g: crumbs(g)[2].update(name="Outro"), "BreadcrumbList fora da trilha"),
            "trilha posição em texto": (lambda g: crumbs(g)[0].update(position="1"), "BreadcrumbList fora da trilha"),
            "sem mainEntity": (lambda g: node(g, "CollectionPage").pop("mainEntity"), "mainEntity sem ItemList"),
            "itemListOrder errado": (
                lambda g: node(g, "CollectionPage")["mainEntity"].update(itemListOrder="https://schema.org/ItemListUnordered"),
                "itemListOrder diferente",
            ),
            "numberOfItems errado": (
                lambda g: node(g, "CollectionPage")["mainEntity"].update(numberOfItems=99),
                "numberOfItems",
            ),
            "ListItem com campo extra": (lambda g: items(g)[0].update(description="extra"), "ListItem inválido"),
            "ListItem sem name": (lambda g: items(g)[0].pop("name"), "ListItem inválido"),
            "ItemList fora de ordem": (swap_items, "ItemList com URLs diferentes da lista visível"),
            "ItemList com post a menos": (drop_last_item, "ItemList com URLs diferentes da lista visível"),
            "ItemList com post a mais": (add_extra_item, "ItemList com URLs diferentes da lista visível"),
        }
        for name, (fn, reason) in cases.items():
            with self.subTest(name=name):
                self.topic(ld_script(self._mutate(fn)))
                got = self.ld()
                self.assertEqual(len(got), 1, got)
                self.assertTrue(got[0].startswith("blog/tema/agentes-de-ia/index.html: "), got)
                self.assertIn(reason, got[0])

    def test_graph_shape_and_visible_list_guards(self):
        g = topic_graph()
        flat = [dict(n, **{"@context": "https://schema.org"}) for n in g["@graph"]]
        self.topic("".join(ld_script(n) for n in flat))
        self.assertTrue(any("não está num @graph" in p for p in self.ld()))

        self.topic(ld_script(g) + ld_script(dict(g["@graph"][0], **{"@context": "https://schema.org"})))
        self.assertTrue(any("2 CollectionPage" in p for p in self.ld()))

        for bad_body, needle in (
            ("<p>sem lista</p>", "esperava uma lista ol.topic-list"),
            ('<ol class="topic-list"></ol>', "lista visível ol.topic-list vazia"),
            (f"<template>{self.DEFAULT_BODY}</template>", "esperava uma lista ol.topic-list"),
            (self.DEFAULT_BODY.replace('class="post-list topic-list"', 'class="post-list topic-list" hidden'),
             "esperava uma lista ol.topic-list"),
        ):
            with self.subTest(bad_body=bad_body):
                self.topic(ld_script(g), body=bad_body)
                got = self.ld()
                self.assertTrue(any(needle in p for p in got), got)

    def test_ispartof_follows_home_website_id_and_blank_fails_once(self):
        self.home(self.HOME.replace("https://guifav.dev/#website", "   "))
        self.topic(ld_script(topic_graph("a")), slug="a")
        self.topic(ld_script(topic_graph("b")), slug="b")
        self.assertEqual(self.ld(), ["index.html: sem o WebSite com @id para o isPartOf dos temas (#105)"])

    def test_tema_template_builds_the_graph(self):
        src = (Path(__file__).resolve().parent.parent / "blog-src/src/pages/tema/[tema].astro").read_text(encoding="utf-8")
        self.assertIn("const ld = topicLdGraph({", src)
        self.assertIn("jsonLd={ld}", src)

    def test_blog_name_matches_consts(self):
        consts = (Path(__file__).resolve().parent.parent / "blog-src/src/consts.ts").read_text(encoding="utf-8")
        m = re.search(r"""^export const BLOG_NAME = (["'])(.+?)\1;""", consts, flags=re.M)
        self.assertIsNotNone(m, "BLOG_NAME não encontrado em blog-src/src/consts.ts")
        self.assertEqual(m.group(2), C.BLOG_NAME)

    def test_main_runs_the_rule(self):
        consts = Path(self.tmp.name) / "consts.ts"
        consts.write_text("export const PRE_CUTOVER_NOINDEX = false;\n", encoding="utf-8")
        posts = Path(self.tmp.name) / "posts"
        posts.mkdir()
        args = ["--site", str(self.site), "--posts", str(posts), "--consts", str(consts)]
        with isolate_main_rule("check_topic_ld"):
            for head, code in (("", 1), (ld_script(topic_graph()), 0)):
                with self.subTest(code=code):
                    self.topic(head)
                    out, err = io.StringIO(), io.StringIO()
                    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                        self.assertEqual(C.main(args), code)
                    self.assertEqual("JSON-LD do tema (#105)" in err.getvalue(), code == 1)


def framework_graph() -> dict:
    """O @graph DigitalDocument + Person da página do framework (#105, decisão 26/11/2026)."""
    url = C.FRAMEWORK_URL
    return {"@context": "https://schema.org", "@graph": [
        {
            "@type": "DigitalDocument",
            "@id": url,
            "url": url,
            "name": "Framework SEO + AEO/GEO",
            "headline": "Framework SEO + AEO/GEO: Como Ser Encontrado no Google e Citado por IAs",
            "description": "Guia completo para otimizar conteúdo para Google e IAs.",
            "inLanguage": "pt-BR",
            "isAccessibleForFree": True,
            "author": {"@id": "https://guifav.dev/#person"},
            "publisher": {"@id": "https://guifav.dev/#person"},
            "datePublished": "2025-12-01",
            "dateModified": "2025-12-15",
            "keywords": "Otimização SEO, AEO, GEO",
            "isPartOf": {"@id": "https://guifav.dev/#website"},
        },
        {"@type": "Person", "@id": "https://guifav.dev/#person", "name": "Guilherme Favaron", "url": "https://guifav.dev/"},
    ]}


class FrameworkLdTest(unittest.TestCase):
    """#105 (decisão 26/11/2026): o framework de SEO e AEO traz DigitalDocument gratuito e nenhum Product ou Offer."""

    HOME = PostLdTest.HOME
    FAQ = ld_script({
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [{"@type": "Question", "name": "O que é SEO?",
                        "acceptedAnswer": {"@type": "Answer", "text": "Otimização para busca."}}],
    })

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.site = Path(self.tmp.name) / "_site"
        self.site.mkdir()
        (self.site / "index.html").write_text(f"<html><head>{self.HOME}</head><body></body></html>", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def write_framework(self, head: str) -> None:
        p = self.site / C.FRAMEWORK_PAGE
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"<!DOCTYPE html><html><head>{head}</head><body></body></html>", encoding="utf-8")

    def _mutate(self, fn):
        g = framework_graph()
        fn(g)
        return g

    def test_valid_framework_with_faqpage_passes(self):
        self.write_framework(ld_script(framework_graph()) + self.FAQ)
        self.assertEqual(C.check_framework_ld(self.site), [])

    def test_missing_page_fails(self):
        got = C.check_framework_ld(self.site)
        self.assertEqual(len(got), 1, got)
        self.assertIn("página do framework ausente", got[0])

    def test_without_digital_document_fails(self):
        self.write_framework(self.FAQ)
        got = C.check_framework_ld(self.site)
        self.assertEqual(len(got), 1, got)
        self.assertIn("nenhum DigitalDocument", got[0])

    def test_product_or_nested_offer_fails(self):
        product_block = ld_script({
            "@context": "https://schema.org",
            "@type": "Product",
            "name": "Framework SEO + AEO/GEO",
            "offers": {"@type": "Offer", "price": "500", "priceCurrency": "BRL"},
        })
        cases = {
            "so Product": product_block,
            "DigitalDocument com Product extra": ld_script(framework_graph()) + product_block,
            "Offer aninhado em offers objeto": ld_script(self._mutate(
                lambda g: g["@graph"][0].update(offers={"@type": "Offer", "price": "0", "priceCurrency": "BRL"})
            )),
            "Offer aninhado em lista profunda": ld_script(self._mutate(
                lambda g: g["@graph"][0].update(mainEntity={"nested": [{"@type": "Offer", "price": "500"}]})
            )),
            "Product aninhado em mentions": ld_script(self._mutate(
                lambda g: g["@graph"][0].update(mentions={"@type": "Product", "name": "Outro"})
            )),
            "Offer em @type lista": ld_script(self._mutate(
                lambda g: g["@graph"][0].update(about={"@type": ["Thing", "Offer"]})
            )),
        }
        for name, head in cases.items():
            with self.subTest(case=name):
                self.write_framework(head)
                got = C.check_framework_ld(self.site)
                self.assertEqual(len(got), 1, got)
                self.assertIn("nó proibido no JSON-LD", got[0])

    def test_each_broken_digital_document_piece_fails(self):
        def doc(g):
            return g["@graph"][0]

        cases = {
            "isAccessibleForFree false": (lambda g: doc(g).update(isAccessibleForFree=False), "isAccessibleForFree"),
            "isAccessibleForFree string": (lambda g: doc(g).update(isAccessibleForFree="true"), "isAccessibleForFree"),
            "isAccessibleForFree int": (lambda g: doc(g).update(isAccessibleForFree=1), "isAccessibleForFree"),
            "isAccessibleForFree ausente": (lambda g: doc(g).pop("isAccessibleForFree"), "isAccessibleForFree"),
            "url errada": (lambda g: doc(g).update(url="https://guifav.dev/ferramentas/"), "url diferente"),
            "@id errado": (lambda g: doc(g).update({"@id": "https://guifav.dev/ferramentas/"}), "@id diferente"),
            "author ausente": (lambda g: doc(g).pop("author"), "author não aponta"),
            "author outro id": (lambda g: doc(g).update(author={"@id": "https://outro.dev/#p"}), "author não aponta"),
            "publisher errado": (lambda g: doc(g).update(publisher={"@type": "Person", "name": "G"}), "publisher não aponta"),
            "isPartOf errado": (lambda g: doc(g).update(isPartOf={"@id": "https://guifav.dev/#outro"}), "isPartOf não aponta"),
            "sem Person no graph": (lambda g: g["@graph"].pop(1), "0 nós Person"),
            "Person com nome errado": (lambda g: g["@graph"][1].update(name="Outro"), "a Person do @graph"),
            "campo brand proibido": (lambda g: doc(g).update(brand={"@type": "Brand", "name": "G"}), "campo proibido"),
            "campo productID proibido": (lambda g: doc(g).update(productID="v1"), "campo proibido"),
            "campo price proibido": (lambda g: doc(g).update(price="500"), "campo proibido"),
            "dois DigitalDocument": (lambda g: g["@graph"].append(dict(doc(g))), "2 DigitalDocument"),
        }
        for name, (fn, needle) in cases.items():
            with self.subTest(case=name):
                self.write_framework(ld_script(self._mutate(fn)))
                got = C.check_framework_ld(self.site)
                self.assertEqual(len(got), 1, got)
                self.assertIn(needle, got[0])

        flat = ld_script(dict(framework_graph()["@graph"][0], **{"@context": "https://schema.org"}))
        self.write_framework(flat)
        got = C.check_framework_ld(self.site)
        self.assertTrue(any("não está num @graph" in p for p in got), got)

    def test_repo_framework_page_passes(self):
        repo = Path(__file__).resolve().parent.parent
        html = (repo / C.FRAMEWORK_PAGE).read_text(encoding="utf-8")
        (self.site / "index.html").write_bytes((repo / "index.html").read_bytes())
        p = self.site / C.FRAMEWORK_PAGE
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(html, encoding="utf-8")
        self.assertEqual(C.check_framework_ld(self.site), [])
        self.assertEqual(C.check_paid_consulting(self.site), [])
        self.assertNotIn(C.OLD_PAID_SCHEDULE_ID, html)
        self.assertNotIn('"price"', html)
        nodes = C._ld_nodes(html)
        self.assertTrue(any(n.get("@type") == "FAQPage" for n in nodes))
        self.assertNotIn("/consultoria/", json.dumps(nodes, ensure_ascii=False))

    def test_main_runs_the_rule(self):
        consts = Path(self.tmp.name) / "consts.ts"
        consts.write_text("export const PRE_CUTOVER_NOINDEX = false;\n", encoding="utf-8")
        posts = Path(self.tmp.name) / "posts"
        posts.mkdir()
        args = ["--site", str(self.site), "--posts", str(posts), "--consts", str(consts)]
        with isolate_main_rule("check_framework_ld"):
            for head, code in ((self.FAQ, 1), (ld_script(framework_graph()) + self.FAQ, 0)):
                with self.subTest(code=code):
                    self.write_framework(head)
                    out, err = io.StringIO(), io.StringIO()
                    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                        self.assertEqual(C.main(args), code)
                    self.assertEqual("DigitalDocument (#105)" in err.getvalue(), code == 1)


class OgSiteNameTest(unittest.TestCase):
    """#105: a home e toda página HTML sob blog/ precisam de exatamente um og:site_name igual a SITE_NAME."""

    OK_META = f'<meta property="og:site_name" content="{C.SITE_NAME}">'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.site = Path(self.tmp.name) / "_site"
        self.site.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def w(self, rel: str, head: str) -> None:
        p = self.site / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"<!DOCTYPE html><html><head>{head}</head><body></body></html>", encoding="utf-8")

    def test_valid_home_and_blog_pages_pass(self):
        self.w("index.html", self.OK_META)
        self.w("blog/index.html", self.OK_META)
        self.w("blog/um-post/index.html", self.OK_META)
        self.w("blog/tema/agentes-de-ia/index.html", self.OK_META)
        # Página fora da home e de blog/ não entra nessa regra.
        self.w("portfolio/index.html", "")
        self.assertEqual(C.check_og_site_name(self.site), [])

    def test_missing_wrong_duplicate_or_inert_og_site_name_fails(self):
        for rel in ("index.html", "blog/um-post/index.html"):
            cases = {
                "ausente": ("", "sem meta og:site_name"),
                "valor errado": ('<meta property="og:site_name" content="guifav.dev">', "og:site_name com valor"),
                "valor vazio": ('<meta property="og:site_name" content="">', "og:site_name com valor"),
                "duplicado": (self.OK_META + self.OK_META, "esperado 1 meta og:site_name, achados 2"),
                "name em vez de property": (f'<meta name="og:site_name" content="{C.SITE_NAME}">', "sem meta og:site_name"),
                "comentado": (f"<!-- {self.OK_META} -->", "sem meta og:site_name"),
                "em template": (f"<template>{self.OK_META}</template>", "sem meta og:site_name"),
            }
            for name, (head, needle) in cases.items():
                with self.subTest(rel=rel, case=name):
                    got = C.check_page_og_site_name(rel, f"<html><head>{head}</head><body></body></html>")
                    self.assertEqual(len(got), 1, got)
                    self.assertIn(needle, got[0])

    def test_repo_sources_carry_og_site_name(self):
        repo = Path(__file__).resolve().parent.parent
        home_html = (repo / "index.html").read_text(encoding="utf-8")
        self.assertEqual(C.check_page_og_site_name("index.html", home_html), [])
        # #105 (T8): og:url da home com barra final, igual ao canonical.
        self.assertEqual(C._meta_contents(C._visible_html(home_html), "og:url", attrs=("property",)),
                         ["https://guifav.dev/"])
        base = (repo / "blog-src/src/layouts/Base.astro").read_text(encoding="utf-8")
        self.assertIn('<meta property="og:site_name" content={AUTHOR_NAME} />', base)

    def test_meta_contents_attrs_filter(self):
        html = '<meta name="og:site_name" content="Errado"><meta property="og:site_name" content="Certo">'
        self.assertEqual(C._meta_contents(html, "og:site_name"), ["Errado", "Certo"])
        self.assertEqual(C._meta_contents(html, "og:site_name", attrs=("property",)), ["Certo"])
        self.assertFalse(hasattr(C, "_og_property_contents"))

    def test_main_runs_the_rule(self):
        consts = Path(self.tmp.name) / "consts.ts"
        consts.write_text("export const PRE_CUTOVER_NOINDEX = false;\n", encoding="utf-8")
        posts = Path(self.tmp.name) / "posts"
        posts.mkdir()
        args = ["--site", str(self.site), "--posts", str(posts), "--consts", str(consts)]
        with isolate_main_rule("check_og_site_name"):
            for head, code in (("", 1), (self.OK_META, 0)):
                with self.subTest(code=code):
                    self.w("index.html", head)
                    out, err = io.StringIO(), io.StringIO()
                    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                        self.assertEqual(C.main(args), code)
                    self.assertEqual("og:site_name" in err.getvalue(), code == 1)


class PostResiduesTest(unittest.TestCase):
    """#105: posts indexáveis não podem conter resíduos legados na fonte (sem <!----> e sem diferenciar caixa)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.posts = Path(self.tmp.name) / "posts"
        self.posts.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def write_post(self, name: str, body: str, index: bool = True) -> None:
        fm = "---\ntitle: Teste\nslug: teste\n" + ("" if index else "index: false\n") + "---\n"
        (self.posts / name).write_text(fm + body, encoding="utf-8")

    def test_each_residue_term_fails_with_file_and_line_and_skips_noindex(self):
        terms = (
            "guilhermefavaron.com.br",
            "GuilhermeFavaron.COM.BR",
            "mindapps.ai",
            "VP de Tecnologia",
            "vp de tecnologia",
            "ia-aplicada.substack.com",
            "iaaplicada.substack.com",
            "guifav.github.io",
            "GUIFAV.GITHUB.IO",
        )
        for term in terms:
            with self.subTest(term=term):
                self.write_post("post.md", f"linha normal\nveja {term} aqui\n", index=True)
                got = C.check_post_residues(self.posts)
                self.assertEqual(len(got), 1, got)
                self.assertTrue(got[0].startswith("post.md:6: "), got)

                self.write_post("post.md", f"linha normal\nveja {term} aqui\n", index=False)
                self.assertEqual(C.check_post_residues(self.posts), [])

    def test_html_empty_comment_does_not_hide_residue(self):
        cases = (
            "https<!---->://www<!---->.guilhermefavaron.com.br/p/x",
            "mind<!---->apps.ai",
            "VP <!---->de Tecnologia",
            "ia-aplicada<!---->.substack.com",
            "iaaplicada<!---->.substack.com",
            "guifav<!---->.github.io",
        )
        for text in cases:
            with self.subTest(text=text):
                self.write_post("post.md", f"ok\n{text}\n", index=True)
                got = C.check_post_residues(self.posts)
                self.assertEqual(len(got), 1, got)
                self.assertTrue(got[0].startswith("post.md:6: "), got)

    def test_html_comments_with_content_do_not_hide_residue_and_keep_line_numbers(self):
        cases = (
            ("comentario com espaco", "ok\nguilhermefavaron<!-- -->.com.br\n", 6),
            ("comentario com texto", "ok\nguilhermefavaron<!-- nota -->.com.br\n", 6),
            ("varios comentarios na mesma linha",
             "ok\n<!-- a -->guilhermefavaron<!-- b -->.com.br<!-- c -->\n", 6),
            ("comentario na linha anterior mantem contagem",
             "<!-- linha cinco -->\nguilhermefavaron<!-- -->.com.br\n", 6),
            ("comentario multilinha nao atravessa linha",
             "<!-- inicio\nguilhermefavaron<!-- -->.com.br\nfim -->\n", 6),
        )
        for name, body, expected_line in cases:
            with self.subTest(case=name):
                self.write_post("post.md", body, index=True)
                got = C.check_post_residues(self.posts)
                self.assertEqual(len(got), 1, got)
                self.assertTrue(got[0].startswith(f"post.md:{expected_line}: "), got)
                self.assertIn("guilhermefavaron.com.br", got[0])

        self.write_post("post.md", "ok\nguilhermefavaron<!-- -->.com.br\n", index=False)
        self.assertEqual(C.check_post_residues(self.posts), [])

    def test_github_repo_link_exception(self):
        self.write_post(
            "post.md",
            "Código em [repositório](https://github.com/guifav/guifav.github.io/tree/main/estudos).\n"
            "Também github.com/guifav/guifav.github.io e https://GITHUB.COM/GUIFAV/GUIFAV.GITHUB.IO.\n",
            index=True,
        )
        self.assertEqual(C.check_post_residues(self.posts), [])

        for bad_line in (
            "https://github.com/guifav/guifav.github.io e https://guifav.github.io/\n",
            "https://evilgithub.com/guifav/guifav.github.io\n",
            "https://sub.github.com/guifav/guifav.github.io\n",
        ):
            with self.subTest(line=bad_line):
                self.write_post("post.md", bad_line, index=True)
                got = C.check_post_residues(self.posts)
                self.assertEqual(len(got), 1, got)
                self.assertIn("guifav.github.io", got[0])

    def test_repo_indexable_posts_have_no_residues(self):
        posts_dir = Path(__file__).resolve().parent.parent / "blog-src/src/content/posts"
        self.assertEqual(C.check_post_residues(posts_dir), [])

    def test_main_runs_the_rule(self):
        site = Path(self.tmp.name) / "_site"
        site.mkdir()
        consts = Path(self.tmp.name) / "consts.ts"
        consts.write_text("export const PRE_CUTOVER_NOINDEX = false;\n", encoding="utf-8")
        args = ["--site", str(site), "--posts", str(self.posts), "--consts", str(consts)]
        with isolate_main_rule("check_post_residues"):
            for body, code in (("site antigo mindapps.ai\n", 1), ("texto limpo\n", 0)):
                with self.subTest(code=code):
                    self.write_post("post.md", body, index=True)
                    out, err = io.StringIO(), io.StringIO()
                    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                        self.assertEqual(C.main(args), code)
                    self.assertEqual("resíduo legado" in err.getvalue(), code == 1)


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


class ContrastReviewR1Test(unittest.TestCase):
    """Achados da rodada 1 do #131 (agy P1/P2/P3, Claude P3)."""

    def pairs(self, css, theme):
        return {s: round(C.contrast_ratio(*p), 2) for s, p in C.css_cascade([css])[theme].items()}

    def test_intermediate_ratios_pin_the_formula(self):
        # agy P2: pesos, expoente e ramo linear da WCAG 2.
        self.assertAlmostEqual(C.contrast_ratio((23, 23, 23), (255, 190, 0)), 10.77, places=2)
        self.assertAlmostEqual(C.contrast_ratio((23, 23, 23), (230, 171, 0)), 8.70, places=2)
        self.assertAlmostEqual(C.contrast_ratio((138, 100, 0), (255, 190, 0)), 3.23, places=2)
        self.assertAlmostEqual(C.contrast_ratio((8, 8, 8), (255, 255, 255)), 20.03, places=2)  # canal <= 10: ramo linear
        self.assertAlmostEqual(C.contrast_ratio((255, 0, 0), (255, 255, 255)), 4.0, places=2)
        self.assertAlmostEqual(C.contrast_ratio((0, 0, 255), (255, 255, 255)), 8.59, places=2)

    def test_focus_visible_and_focus_within_inherit_the_base(self):
        # agy P1: :focus casava antes de :focus-visible e virava ".b-visible".
        css = TOKENS_CSS + ".b{background:#fff;color:var(--hl-text)}.b:focus-visible{background:var(--hl)}.b:focus-within{background:var(--hl)}"
        found = {s for s, t, *_ in C.contrast_problems([css]) if t == "escuro"}
        self.assertTrue({".b:focus-visible", ".b:focus-within"} <= found, found)

    def test_minified_and_body_dark_selectors(self):
        # agy P1: o LightningCSS do Astro grava [data-theme=dark] sem aspas no <style> do blog; o site.js põe o tema no body.
        for prefix in ('[data-theme=dark]', "[data-theme='dark']", 'body[data-theme="dark"]', 'body[data-theme=dark]',
                       'html[data-theme=dark]'):
            with self.subTest(prefix=prefix):
                css = TOKENS_CSS + f".x{{background:#fff;color:#000}}{prefix} .x{{background:#000;color:#fff}}"
                self.assertEqual(C.contrast_problems([css]), [])
                self.assertEqual(self.pairs(css, "claro")[".x"], 21.0)
                self.assertEqual(self.pairs(css, "escuro")[".x"], 21.0)

    def test_dark_tokens_on_body_and_unquoted(self):
        for sel in ("[data-theme=dark]", "body[data-theme=dark]", 'body[data-theme="dark"]'):
            with self.subTest(sel=sel):
                css = f":root{{--f:#000;--g:#fff}}{sel}{{--f:#fff;--g:#fff}}.x{{color:var(--f);background:var(--g)}}"
                self.assertEqual([(s, t) for s, t, *_ in C.contrast_problems([css])], [(".x", "escuro")])

    def test_last_background_declaration_wins(self):
        # agy P1 / Claude P3: background e background-color são o mesmo fundo; vale o último da cascata.
        self.assertTrue(C.contrast_problems(["a{background-color:#fff;color:#000}a{background:#000}"]))
        self.assertEqual(C.contrast_problems(["a{background-color:#fff;color:#000}a:hover{background:#000;color:#fff}"]), [])
        self.assertTrue(C.contrast_problems(["a{background:#fff;color:#000;background-color:#000}"]))  # r2 (agy P2)
        self.assertEqual(C.contrast_problems(["a{background-color:#000;background:#fff;color:#000}"]), [])
        # Fundo que não se resolve depois de um que se resolve: não é avaliado.
        self.assertEqual(C.contrast_problems(["a{background:#000;color:#000}a{background:url(x.png)}"]), [])
        self.assertEqual(C.contrast_problems(["a{background:#000;color:#000}a{background:transparent}"]), [])

    def test_charset_import_and_layer_statements_do_not_eat_the_next_rule(self):
        # agy P2: at-rule terminada em ";" engolia o :root seguinte.
        for head in ('@charset "UTF-8";', '@import url("x.css");', "@layer a, b;"):
            with self.subTest(head=head):
                css = head + ":root{--f:#000;--g:#000}.x{color:var(--f);background:var(--g)}"
                self.assertTrue(C.contrast_problems([css]))

    def test_selectors_normalized_across_sheets(self):
        # agy P2: o CSS do blog é minificado; a sobrescrita precisa casar com o seletor do site.css.
        site = ".a > .b  .c[aria-pressed='true']{background:#fff;color:#000}"
        blog = ".a>.b .c[aria-pressed=true]{color:#fff}"
        self.assertTrue(C.contrast_problems([site, blog]))

    def test_print_media_ignored(self):
        self.assertEqual(C.contrast_problems(["@media print{.x{background:#fff;color:#fff}}"]), [])
        self.assertTrue(C.contrast_problems(["@media screen and (max-width:600px){.x{background:#fff;color:#fff}}"]))

    def test_custom_property_names_keep_their_case(self):
        # agy P3: --bgCard e --bgcard são tokens diferentes.
        css = ":root{--bgCard:#000;--bgcard:#fff}.x{color:#fff;background:var(--bgCard)}"
        self.assertEqual(C.contrast_problems([css]), [])
        self.assertEqual(C.css_cascade([css])["claro"][".x"], ((255, 255, 255), (0, 0, 0)))

    def test_more_color_syntaxes(self):
        # Claude P3: hex de 4 e 8 dígitos com alfa total, hsl() e cores nomeadas comuns.
        toks = {}
        self.assertEqual(C.css_color("#ffbe00ff", toks), (255, 190, 0))
        self.assertEqual(C.css_color("#fb0f", toks), (255, 187, 0))
        self.assertIsNone(C.css_color("#ffbe0080", toks))
        self.assertEqual(C.css_color("hsl(0, 0%, 100%)", toks), (255, 255, 255))
        self.assertEqual(C.css_color("hsl(120deg 100% 25%)", toks), (0, 128, 0))
        self.assertIsNone(C.css_color("hsl(0 0% 100% / .5)", toks))
        self.assertEqual(C.css_color("Red", toks), (255, 0, 0))
        self.assertIsNone(C.css_color("transparent", toks))


class ContrastReviewR2Test(unittest.TestCase):
    """Achados da rodada 2 do #131 (Claude P2, agy P2/P3)."""

    def test_dark_rule_wins_over_a_later_unprefixed_rule(self):
        # Claude P2: [data-theme=dark] .x é mais específico que .x; no navegador vence mesmo vindo antes.
        css = (":root{--bg:#fff}[data-theme=dark]{--bg:#000}.x{background:var(--bg)}"
               "[data-theme=dark] .x{color:#fff}")
        self.assertEqual(C.contrast_problems([css, ".x{color:#000}"]), [])
        self.assertEqual(C.css_cascade([css, ".x{color:#000}"])["escuro"][".x"], ((255, 255, 255), (0, 0, 0)))

    def test_light_prefixed_rules_and_tokens_only_in_light(self):
        # agy P2: o site.js grava data-theme="light" no body.
        css = ':root{--f:#fff}[data-theme=light]{--f:#000}body[data-theme="light"] .x{color:var(--f);background:#fff}'
        self.assertEqual(C.contrast_problems([css]), [])
        self.assertNotIn(".x", C.css_cascade([css])["escuro"])

    def test_prefers_light_tokens_do_not_leak_into_dark(self):
        # agy P2: folha dark-first com ajuste para o claro.
        css = (":root{--f:#fff;--b:#000}@media (prefers-color-scheme: light){:root{--f:#000;--b:#fff}}"
               ".x{color:var(--f);background:var(--b)}")
        self.assertEqual(C.contrast_problems([css]), [])
        self.assertEqual(C.css_cascade([css])["escuro"][".x"], ((255, 255, 255), (0, 0, 0)))

    def test_background_image_is_not_judged(self):
        # agy P2: fundo com imagem, mesmo com cor de reserva, fica fora do contrato.
        for css in (".x{background:url(a.png);background-color:#fff;color:#fff}",
                    ".x{background-color:#fff;background-image:linear-gradient(red,blue);color:#fff}",
                    ".x{background:URL(a.png) #fff;color:#fff}",
                    ".x{background-image:url(a.png);background-color:#fff;color:#fff}"):
            with self.subTest(css=css):
                self.assertEqual(C.contrast_problems([css]), [])
        self.assertTrue(C.contrast_problems([".x{background:url(a.png)}.x{background:#fff;color:#fff}"]))
        self.assertTrue(C.contrast_problems([".x{background-image:url(a.png)}.x{background-image:none;background-color:#fff;color:#fff}"]))

    def test_compound_theme_selector_is_not_a_descendant(self):
        # Claude P4: [data-theme=dark].x é o próprio elemento com o tema, não um descendente .x.
        css = ".x{background:#fff;color:#000}[data-theme=dark].x{color:#fff}"
        self.assertEqual(C.contrast_problems([css]), [])

    def test_import_with_semicolon_inside_the_url(self):
        # agy P3: o CSS2 do Google Fonts tem ";" na URL.
        css = ('@import url("https://fonts.googleapis.com/css2?family=Inter:wght@400;700");'
               ":root{--f:#000;--b:#000}.x{color:var(--f);background:var(--b)}")
        self.assertTrue(C.contrast_problems([css]))

    def test_print_only_media_queries(self):
        # agy P3: "not print" e listas com outra consulta valem na tela.
        self.assertTrue(C._print_only("print"))
        self.assertTrue(C._print_only("only print"))
        for media in ("not print", "print, (max-width: 600px)", "screen", "all", "print and (min-width:1px), screen"):
            with self.subTest(media=media):
                self.assertFalse(C._print_only(media))

    def test_bad_numbers_and_uppercase_functions(self):
        # agy P3: "." ou "1.2.3" não derrubam o verificador; RGB() e HSL() valem.
        self.assertIsNone(C.css_color("rgba(0,0,0,.)", {}))
        self.assertIsNone(C.css_color("rgb(0,0,0 / 1.2.3)", {}))
        self.assertEqual(C.css_color("RGB(255, 0, 0)", {}), (255, 0, 0))
        self.assertEqual(C.css_color("HSL(0, 0%, 0%)", {}), (0, 0, 0))
        self.assertTrue(C.contrast_problems([".x{color:#fff !IMPORTANT;background:#fff}"]))


class ContrastReviewR3Test(unittest.TestCase):
    """Achados da rodada 3 do #131 (Claude P2/P3, agy P3/P4): cascata por especificidade, ordem e !important."""

    def test_dark_override_after_a_hover_with_equal_specificity(self):
        # Claude P2: .x:hover e [data-theme=dark] .x têm (0,2,0); vence a que vem depois.
        css = ".x{color:#000;background:#fff}.x:hover{color:#000}[data-theme=dark] .x{background:#000;color:#fff}"
        self.assertEqual(C.contrast_problems([css]), [])
        self.assertEqual(C.css_cascade([css])["escuro"][".x:hover"], ((255, 255, 255), (0, 0, 0)))
        self.assertEqual(C.css_cascade([css])["claro"][".x:hover"], ((0, 0, 0), (255, 255, 255)))

    def test_more_specific_rule_wins_regardless_of_order(self):
        # Mesma chave (".x" no escuro) com especificidades diferentes: body[data-theme=dark] .x (0,2,1) vence [data-theme=dark] .x (0,2,0).
        css = "body[data-theme=dark] .x{color:#fff;background:#000}[data-theme=dark] .x{color:#000}"
        self.assertEqual(C.contrast_problems([css]), [])
        self.assertEqual(C.css_cascade([css])["escuro"][".x"], ((255, 255, 255), (0, 0, 0)))
        css_bad = "body[data-theme=dark] .x{color:#fff;background:#fff}[data-theme=dark] .x{color:#000}"
        self.assertEqual([(s, t) for s, t, *_ in C.contrast_problems([css_bad])], [(".x", "escuro")])
        # o :hover (0,2,0) vence o base (0,1,0) mesmo vindo antes no texto
        css = ".x:hover{color:#000}.x{color:#fff;background:#000}"
        self.assertEqual(C.css_cascade([css])["claro"][".x:hover"], ((0, 0, 0), (0, 0, 0)))
        self.assertTrue(C.contrast_problems([".x{color:#000;background:#fff}.x{color:#fff}"]))

    def test_important_beats_a_later_normal_declaration(self):
        # Claude P3 e #138 (agy P2): !important vence empate e também seletor mais específico.
        self.assertEqual(C.contrast_problems([".x{color:#000!important;background:#fff}.x{color:#fff}"]), [])
        self.assertTrue(C.contrast_problems([".x{color:#000!important;background:#fff}.x{color:#fff!important}"]))
        self.assertEqual(C.contrast_problems([".x{color:#000 !IMPORTANT;background:#fff}.x{color:#fff}"]), [])
        # !important em seletor menos específico vencendo seletor mais específico (herança de estado e mesma chave).
        css = ".x{color:#000!important;background:#fff}.x:hover{color:#fff}"
        self.assertEqual(C.contrast_problems([css]), [])
        self.assertEqual(C.css_cascade([css])["claro"][".x:hover"], ((0, 0, 0), (255, 255, 255)))
        self.assertIn((".x:hover", "claro"),
                      {(s, t) for s, t, *_ in C.contrast_problems([".x{color:#fff!important;background:#fff}.x:hover{color:#000}"])})
        self.assertEqual(C.contrast_problems([
            "[data-theme=dark] .x{color:#fff!important;background:#000}body[data-theme=dark] .x{color:#000}"
        ]), [])
        self.assertEqual(
            [(s, t) for s, t, *_ in C.contrast_problems([
                "[data-theme=dark] .x{color:#000!important;background:#000}body[data-theme=dark] .x{color:#fff}"
            ])],
            [(".x", "escuro")],
        )

    def test_braces_and_semicolons_inside_strings(self):
        # Claude P3, agy P4: content:"{" não engole a regra seguinte; ";" numa string não parte a declaração.
        self.assertTrue(C.contrast_problems(['.a::before{content:"{"}.x{color:#fff;background:#fff}']))
        self.assertTrue(C.contrast_problems(['.a::before{content:"}"}.x{color:#fff;background:#fff}']))
        self.assertTrue(C.contrast_problems(['.a{content:"a;b";color:#fff;background:#fff}']))
        self.assertTrue(C.contrast_problems(['.x{color:#fff;background:#fff;content:"a;color:#000"}']))

    def test_selector_lists_inside_functional_pseudo_classes(self):
        # agy P4: a vírgula de :is(.a, .b) não separa seletores.
        cascade = C.css_cascade([":is(.a, .b) .x{color:#000;background:#fff}"])
        self.assertEqual(list(cascade["claro"]), [":is(.a, .b) .x"])

    def test_compound_theme_selector_is_only_in_its_theme(self):
        # agy P3: [data-theme=dark].x só existe no escuro, e não se mistura com .x.
        css = ":root{--t:#000}[data-theme=dark]{--t:#fff}[data-theme=dark].x{color:var(--t);background:#000}"
        self.assertEqual(C.contrast_problems([css]), [])
        cascade = C.css_cascade([css])
        self.assertNotIn("[data-theme=dark].x", cascade["claro"])
        self.assertIn("[data-theme=dark].x", cascade["escuro"])

    def test_colors_on_the_theme_element_itself(self):
        # agy P3: body[data-theme=dark] com cor fixa sobrescreve body no escuro, e um par ruim ali é acusado.
        css = "body{color:#000;background:#fff}body[data-theme=dark]{color:#fff;background:#000}"
        self.assertEqual(C.contrast_problems([css]), [])
        self.assertEqual(C.css_cascade([css])["escuro"]["body"], ((255, 255, 255), (0, 0, 0)))
        self.assertEqual(C.contrast_problems(["body{color:#000;background:#fff}[data-theme=dark]{background:#000}"])[0][:2],
                         ("body", "escuro"))

    def test_specificity(self):
        self.assertEqual([C._specificity(s) for s in ("*", "a", ".x", "#a", ".x:hover", "[data-theme=dark] .x",
                                                     "body[data-theme=dark] .x", "a::before", ":root", "#a .b c")],
                         [(0, 0, 0), (0, 0, 1), (0, 1, 0), (1, 0, 0), (0, 2, 0), (0, 2, 0), (0, 2, 1), (0, 0, 2),
                          (0, 1, 0), (1, 1, 1)])


class ContrastFollowup138Test(unittest.TestCase):
    """#138: 7 ajustes menores da rodada 4 do #131 (itens 2 a 7; item 1 está em ContrastReviewR3Test)."""

    def test_body_tokens_beat_root_and_html_tokens(self):
        # Item 2 (Claude P3): body é filho de html/:root, então os tokens de body vencem mesmo vindo antes.
        css = "body{--t:#fff}:root{--t:#000}.x{color:var(--t);background:#000}"
        self.assertEqual(C.contrast_problems([css]), [])
        for theme in C.THEMES:
            self.assertEqual(C.css_cascade([css])[theme][".x"], ((255, 255, 255), (0, 0, 0)))
        css_bad = "body{--t:#000}html{--t:#fff}.x{color:var(--t);background:#000}"
        self.assertEqual([(s, t) for s, t, *_ in C.contrast_problems([css_bad])], [(".x", "claro"), (".x", "escuro")])
        # Dentro da mesma camada, especificidade e tema continuam valendo.
        css_theme = "body[data-theme=dark]{--t:#fff}body{--t:#000}.x{color:var(--t);background:#000}"
        self.assertEqual([(s, t) for s, t, *_ in C.contrast_problems([css_theme])], [(".x", "claro")])
        css_root_vs_html = ":root{--t:#fff}html{--t:#000}.x{color:var(--t);background:#000}"
        self.assertEqual(C.contrast_problems([css_root_vs_html]), [])
        # html/:root não herda tokens definidos em body.
        css_no_leak = ":root{--t:#000;color:var(--t);background:#fff}body{--t:#fff;color:var(--t);background:#000}"
        self.assertEqual(C.contrast_problems([css_no_leak]), [])

    def test_root_and_html_theme_share_the_same_key(self):
        # Item 3 (agy P3): :root puro e html[data-theme]/:root[data-theme] caem na mesma chave "html".
        css_ok = ":root{--text:#000;color:var(--text);background:#fff}:root[data-theme=dark]{--text:#fff;background:#000}"
        self.assertEqual(C.contrast_problems([css_ok]), [])
        self.assertEqual(C.css_cascade([css_ok])["escuro"]["html"], ((255, 255, 255), (0, 0, 0)))
        css_bad = ":root{color:#000;background:#fff}html[data-theme=dark]{background:#000}"
        self.assertEqual([(s, t) for s, t, *_ in C.contrast_problems([css_bad])], [("html", "escuro")])
        # :root (0,1,0) vence html (0,0,1) na mesma chave "html".
        self.assertEqual(C.contrast_problems([":root{color:#fff;background:#000}html{color:#000}"]), [])

    def test_escaped_quotes_and_braces_inside_quoted_selectors(self):
        # Item 4 (agy P3): escapes com barra invertida em strings e "{" dentro de aspas no seletor ou @import.
        for css, want in (
            ('.a::before{content:"\\"}"}.x{color:#fff;background:#fff}', ".x"),
            (".a::before{content:'\\'}'}.x{color:#fff;background:#fff}", ".x"),
            ('.a::before{content:"\\\\\\"{" }.x{color:#fff;background:#fff}', ".x"),
            ('.a::before{content:"\\\\"}.x{color:#fff;background:#fff}', ".x"),
            ('.a{content:"a\\";color:#000";color:#fff;background:#fff}', ".a"),
            ('.a{content:"a\\\\";color:#fff;background:#fff}', ".a"),
            ('a[data-x="{"]{color:#fff;background:#fff}', 'a[data-x="{"]'),
            ('a[data-x="\\"{"].z{color:#fff;background:#fff}', 'a[data-x="\\"{"].z'),
            ('@import url("https://example.com/a{b;c\\";d");.x{color:#fff;background:#fff}', ".x"),
        ):
            with self.subTest(css=css):
                self.assertEqual({s for s, *_ in C.contrast_problems([css])}, {want}, css)
        # Vírgula escapada dentro de aspas no seletor não parte o seletor.
        probs = C.contrast_problems(['[data-x="a\\",b"],.y{color:#fff;background:#fff}'])
        self.assertEqual({s for s, *_ in probs}, {'[data-x="a\\",b"]', ".y"})

    def test_compound_states_inherit_intermediate_states(self):
        # Item 5 (agy P3): estados compostos (:hover:active) herdam dos estados intermediários pela cascata.
        css_ok = ".btn{color:#000;background:#fff}.btn:hover{color:#fff;background:#000}.btn:hover:active{background:#111}"
        self.assertEqual(C.contrast_problems([css_ok]), [])
        self.assertEqual(C.css_cascade([css_ok])["claro"][".btn:hover:active"], ((255, 255, 255), (17, 17, 17)))
        css_bad = ".btn{color:#000;background:#fff}.btn:hover{color:#fff;background:#000}.btn:hover:active{background:#fff}"
        self.assertEqual({s for s, *_ in C.contrast_problems([css_bad])}, {".btn:hover:active"})
        # Desempate por ordem entre intermediários de mesma especificidade.
        css_order_bad = ".btn{background:#fff}.btn:hover{color:#fff;background:#000}.btn:active{color:#000;background:#fff}.btn:hover:active{background:#000}"
        self.assertEqual({s for s, *_ in C.contrast_problems([css_order_bad])}, {".btn:hover:active"})
        css_order_ok = ".btn{background:#fff}.btn:active{color:#000;background:#fff}.btn:hover{color:#fff;background:#000}.btn:hover:active{background:#000}"
        self.assertEqual(C.contrast_problems([css_order_ok]), [])
        # !important no intermediário vence o estado composto.
        css_imp = ".btn{background:#fff}.btn:hover{color:#fff!important}.btn:hover:active{color:#000;background:#fff}"
        self.assertIn(".btn:hover:active", {s for s, *_ in C.contrast_problems([css_imp])})
        # Estados intermediários em ordem diferente (.btn:active:hover) e em partes diferentes do seletor.
        css_perm = ".btn{background:#fff}.btn:active:hover{color:#fff;background:#000}.btn:hover:active:focus{background:#111}"
        self.assertEqual(C.contrast_problems([css_perm]), [])
        css_split = ".card .btn{color:#000;background:#fff}.card:hover .btn{color:#fff;background:#000}.card:hover .btn:active{background:#111}"
        self.assertEqual(C.contrast_problems([css_split]), [])

    def test_var_followed_by_another_token(self):
        # Item 6 (agy P4): var(...) só vale como cor única quando o ")" correspondente encerra a string.
        toks = {"--a": "#fff", "--b": "#000"}
        self.assertIsNone(C.css_color("var(--a, #fff) var(--b)", toks))
        self.assertIsNone(C.css_color("var(--a, #000) rgb(0, 0, 0)", toks))
        self.assertIsNone(C.css_color("var(--a, #000) calc(1px)", toks))
        self.assertEqual(C.css_color("var(--a, var(--b, rgb(0, 0, 0)))", {}), (0, 0, 0))
        self.assertIsNone(C._css_background("var(--a, #fff) var(--b)", toks))
        self.assertEqual(C.contrast_problems([":root{--a:#fff;--b:#000}.x{color:#fff;background:var(--a, #fff) var(--b)}"]), [])
        self.assertEqual(C.contrast_problems([":root{--a:#fff}.x{color:var(--a, #fff) calc(1px);background:#fff}"]), [])

    def test_ancestor_before_data_theme(self):
        # Item 7 (Claude P4): ancestral html/:root antes de [data-theme] sobrescreve o seletor e os tokens do tema.
        for prefix in ("html body[data-theme=dark]", "html > body[data-theme=dark]",
                       ":root body[data-theme=dark]", ":root > body[data-theme=dark]",
                       "html [data-theme=dark]"):
            with self.subTest(prefix=prefix):
                css_ok = f".x{{color:#000;background:#fff}}{prefix} .x{{color:#fff;background:#000}}"
                self.assertEqual(C.contrast_problems([css_ok]), [])
                self.assertEqual(C.css_cascade([css_ok])["escuro"][".x"], ((255, 255, 255), (0, 0, 0)))
                css_bad = f".x{{color:#000;background:#fff}}{prefix} .x{{background:#000}}"
                self.assertEqual([(s, t) for s, t, *_ in C.contrast_problems([css_bad])], [(".x", "escuro")])
        # Tokens e cores no próprio body com ancestral html.
        css_tok = ":root{--f:#000;--b:#fff}html body[data-theme=dark]{--f:#fff;--b:#000}.x{color:var(--f);background:var(--b)}"
        self.assertEqual(C.contrast_problems([css_tok]), [])
        self.assertEqual(C.css_cascade([css_tok])["escuro"][".x"], ((255, 255, 255), (0, 0, 0)))
        css_body = "body{color:#000;background:#fff}html body[data-theme=dark]{background:#000}"
        self.assertEqual([(s, t) for s, t, *_ in C.contrast_problems([css_body])], [("body", "escuro")])
        # Ancestral arbitrário não-raiz não se mistura com .x global.
        css_scoped = ".x{color:#000;background:#fff}.card [data-theme=dark] .x{background:#000}"
        self.assertEqual(C.contrast_problems([css_scoped]), [])


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
        # Mockups do portfólio têm paleta própria: a regra é sobre o CSS do site.
        self.w("blog/um-post/index.html", page('<style>' + TOKENS_CSS + '.b{background:var(--hl);color:var(--hl)}</style>'))
        self.assertEqual(C.check_contrast(self.site), [])
        self.w("blog/um-post/index.html", page('<link rel="stylesheet" href="/assets/site.css">'
                                               '<style>.b{background:var(--hl);color:var(--hl)}</style>'))
        self.assertTrue(C.check_contrast(self.site))

    def test_page_parsing_rel_tokens_media_and_script(self):
        # agy P2: rel com vários tokens, alternate, media=print, <style> dentro de <script>, href sem aspas.
        bad = ".subscribe-row button{color:var(--hl-text)}"
        self.w("x.css", bad)
        cases = [
            ('<link rel="preload stylesheet" href="/assets/site.css"><link rel="stylesheet" href="/x.css">', True),
            ('<link rel=stylesheet href=/assets/site.css><link rel=stylesheet href=/x.css>', True),
            ('<link rel="stylesheet" href="/assets/site.css"><link rel="alternate stylesheet" href="/x.css">', False),
            ('<link rel="stylesheet" href="/assets/site.css"><link rel="stylesheet" media="print" href="/x.css">', False),
            ('<link rel="stylesheet" href="/assets/site.css"><style media="print">' + bad + '</style>', False),
            ('<link rel="stylesheet" href="/assets/site.css"><script>var s="<style>' + bad + '</style>";</script>', False),
            ('<link data-rel="stylesheet" rel="icon" href="/assets/site.css"><style>' + bad + '</style>', False),
        ]
        for head, judged in cases:
            with self.subTest(head=head):
                self.w("blog/um-post/index.html", page(head))
                self.assertEqual(bool(C.check_contrast(self.site)), judged)

    def test_main_runs_the_contrast_rule(self):
        consts = Path(self.tmp.name) / "consts.ts"
        consts.write_text("export const PRE_CUTOVER_NOINDEX = true;\n", encoding="utf-8")
        self.w("404.html", page('<a href="/">home</a><link rel="stylesheet" href="/assets/site.css">'
                                '<style>.subscribe-row button{color:var(--hl-text)}</style>',
                                robots='<meta name="robots" content="noindex">'))
        err = io.StringIO()
        with isolate_main_rule("check_contrast"), contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = C.main(["--site", str(self.site), "--posts", str(self.posts), "--consts", str(consts)])
        self.assertEqual(code, 1)
        self.assertIn("404.html: contraste 1.00:1", err.getvalue())


class LlmRankingTest(unittest.TestCase):
    """#113: a regra que falha se o /ferramentas/llm-ranking/ sair sem a tabela, a data dos dados, o JSON-LD ou a atribuição."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.site = Path(self.tmp.name)
        (self.site / "ferramentas/llm-ranking").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def page(self, rows: int = 100, cells: int = 7, meta: str = '<meta name="dados-publicados-em" content="2026-11-22">',
             modified: str = "2026-11-22", extra: str = "", attribution: bool = True, license_rel: str = "license",
             first_pos: str = "1", jsonld: str | None = None,
             versao: str = '<meta name="dados-versao" content="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa">') -> list[str]:
        trs = []
        for i in range(1, rows + 1):
            pos = first_pos if i == 1 else str(i)
            tds = [f"<td>{pos}</td>"] + [f"<td><span>c{j}</span></td>" for j in range(cells - 1)]
            trs.append("<tr>" + "".join(tds) + "</tr>")
        ld = jsonld if jsonld is not None else json.dumps({"@context": "https://schema.org", "@graph": [
            {"@type": "WebPage", "dateModified": modified}, {"@type": "BreadcrumbList"}]})
        att = (f'<a href="{C.LLM_DATASET_URL}">d</a> <a href="https://creativecommons.org/licenses/by/4.0/deed.pt-br" '
               f'rel="{license_rel}">CC BY 4.0</a>') if attribution else ""
        html = (f"<!DOCTYPE html><html><head>{meta}{versao}<script type=\"application/ld+json\">{ld}</script></head><body>"
                f'<table id="ranking"><caption>x</caption><thead><tr><th>Posição</th></tr></thead><tbody>{"".join(trs)}'
                f"</tbody></table><p>{att}</p>{extra}</body></html>")
        (self.site / "ferramentas/llm-ranking/index.html").write_text(html, encoding="utf-8")
        return C.check_llm_ranking(self.site)

    def test_good_page(self):
        self.assertEqual(self.page(), [])

    def test_content_version(self):
        # Revisão automática do #144 (P1): o snapshot da KVM compara esta versão (hash do snapshot) com a do repositório
        # de dados para saber se a página no ar está atrás, mesmo quando a fonte republica com a mesma data.
        self.assertTrue(any("esperava uma meta dados-versao, achou 0" in p for p in self.page(versao="")))
        dois = '<meta name="dados-versao" content="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa">' * 2
        self.assertTrue(any("esperava uma meta dados-versao, achou 2" in p for p in self.page(versao=dois)))
        for ruim in ("A" * 64, "a" * 63, "g" * 64, ""):
            with self.subTest(ruim=ruim):
                self.assertTrue(any("meta dados-versao inválida" in p
                                    for p in self.page(versao=f'<meta name="dados-versao" content="{ruim}">')))

    def test_missing_page(self):
        (self.site / "ferramentas/llm-ranking").rmdir()
        self.assertEqual(C.check_llm_ranking(self.site), ["ferramentas/llm-ranking/index.html: página não existe no _site"])

    def test_without_the_table(self):
        (self.site / "ferramentas/llm-ranking/index.html").write_text(
            '<html><head><meta name="dados-publicados-em" content="2026-11-22"></head><body></body></html>', encoding="utf-8")
        self.assertTrue(any('esperava uma <table id="ranking">, achou 0' in p for p in C.check_llm_ranking(self.site)))

    def test_rows_and_cells(self):
        self.assertTrue(any("99 linhas" in p for p in self.page(rows=99)))
        self.assertTrue(any("101 linhas" in p for p in self.page(rows=101)))
        self.assertTrue(any("linha 1 da tabela com 6 células" in p for p in self.page(cells=6)))
        self.assertTrue(any("com a posição '2'" in p for p in self.page(first_pos="2")))

    def test_empty_cell(self):
        probs = self.page(extra="")
        self.assertEqual(probs, [])
        html = (self.site / "ferramentas/llm-ranking/index.html").read_text(encoding="utf-8").replace("<td><span>c0</span></td>", "<td></td>", 1)
        (self.site / "ferramentas/llm-ranking/index.html").write_text(html, encoding="utf-8")
        self.assertTrue(any("célula vazia" in p for p in C.check_llm_ranking(self.site)))

    def test_nested_table_rows_do_not_count(self):
        html = (self.site / "ferramentas/llm-ranking/index.html")
        self.page()
        t = html.read_text(encoding="utf-8").replace("<td><span>c0</span></td>", "<td><table><tbody><tr><td>x</td></tr></tbody></table>c</td>", 1)
        html.write_text(t, encoding="utf-8")
        self.assertEqual(C.check_llm_ranking(self.site), [])

    def test_nested_empty_cell_does_not_close_the_outer_one(self):
        # r1 (agy P3): o </td> da tabela aninhada não fecha a célula de fora; a célula de fora tem o texto "c".
        html = (self.site / "ferramentas/llm-ranking/index.html")
        self.page()
        t = html.read_text(encoding="utf-8").replace("<td><span>c0</span></td>", "<td><table><tbody><tr><td></td></tr></tbody></table>c</td>", 1)
        html.write_text(t, encoding="utf-8")
        self.assertEqual(C.check_llm_ranking(self.site), [])

    def test_graph_that_is_not_a_list(self):
        self.assertTrue(any("esperava um WebPage" in p for p in self.page(jsonld='{"@graph": null}')))
        self.assertTrue(any("esperava um WebPage" in p for p in self.page(jsonld='{"@graph": 3}')))

    def test_license_rel_is_case_insensitive(self):
        self.assertEqual(self.page(license_rel="License"), [])

    def test_two_ranking_tables(self):
        self.assertTrue(any("achou 2" in p for p in self.page(extra='<table id="ranking"><tbody></tbody></table>')))

    def test_data_meta(self):
        self.assertTrue(any("achou 0" in p and "meta" in p for p in self.page(meta="")))
        two = '<meta name="dados-publicados-em" content="2026-11-22">' * 2
        self.assertTrue(any("achou 2" in p for p in self.page(meta=two)))
        self.assertTrue(any("inválida" in p for p in self.page(meta='<meta name="dados-publicados-em" content="2026-13-01">')))
        self.assertTrue(any("inválida" in p for p in self.page(meta='<meta name="dados-publicados-em" content="ontem">')))
        self.assertTrue(any("fora do formato" in p for p in self.page(meta='<meta name="dados-publicados-em" content="2026-1-2">')))

    def test_jsonld(self):
        self.assertTrue(any("diferente da data dos dados" in p for p in self.page(modified="2026-11-19")))
        self.assertTrue(any("esperava um WebPage" in p for p in self.page(jsonld='{"@type": "Dataset"}')))
        self.assertTrue(any("JSON-LD inválido" in p for p in self.page(jsonld="{")))
        self.assertEqual(self.page(jsonld='{"@type": "WebPage", "dateModified": "2026-11-22"}'), [])

    def test_attribution(self):
        self.assertTrue(any("link da fonte" in p for p in self.page(attribution=False)))
        self.assertTrue(any('rel="license"' in p for p in self.page(license_rel="nofollow")))

    def test_leftover_marker(self):
        self.assertTrue(any("marcador do template" in p for p in self.page(extra="<!--LLM:tabela-->")))

    def test_main_runs_the_rule(self):
        consts = Path(self.tmp.name) / "consts.ts"
        consts.write_text("export const PRE_CUTOVER_NOINDEX = false;\n", encoding="utf-8")
        err = io.StringIO()
        with isolate_main_rule("check_llm_ranking"), \
                contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = C.main(["--site", str(self.site), "--posts", str(self.site), "--consts", str(consts)])
        self.assertEqual(code, 1)
        self.assertIn("ERRO: ferramentas/llm-ranking/index.html: página não existe no _site", err.getvalue())
        self.page()
        with isolate_main_rule("check_llm_ranking"), \
                contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(C.main(["--site", str(self.site), "--posts", str(self.site), "--consts", str(consts)]), 0)


class NavSourcesTest(unittest.TestCase):
    """#113 r4 (achado P1 do agy): toda página da raiz com o menu do site, e o layout do blog, levam a /consultoria/ no
    menu e no rodapé, e o "Agende um horário" do menu vai para /consultoria/#como-contratar (#136). O merge de um PR que
    traz página nova com o menu antigo quebra aqui, em vez de publicar a página fora do padrão."""

    ROOT = Path(__file__).resolve().parent.parent

    def sources(self) -> list[Path]:
        entries = [l.strip() for l in (self.ROOT / "site-manifest.txt").read_text(encoding="utf-8").splitlines()
                   if l.strip() and not l.startswith("#")]
        files: list[Path] = []
        for e in entries:
            p = self.ROOT / e
            if e.endswith("/"):
                files += sorted(f for f in p.rglob("*.html") if "mockups" not in f.parts)
            elif e.endswith(".html"):
                files.append(p)
        return [f for f in files if '<div class="nav-links">' in f.read_text(encoding="utf-8")] + \
            [self.ROOT / "blog-src/src/layouts/Base.astro"]

    def test_every_menu_has_consulting_and_the_schedule_cta_goes_there(self):
        files = self.sources()
        rels = {str(f.relative_to(self.ROOT)) for f in files}
        for must in ("index.html", "404.html", "portfolio/index.html", "consultoria/index.html",
                     "ferramentas/index.html", "ferramentas/llm-ranking/index.html",
                     "ferramentas/mapa-decisao-ia/index.html", "ferramentas/planilha-custos-ia/index.html",
                     C.BOOK_PAGE, f"materiais/{C.BOOK_ID}/enviado/index.html",
                     "cursos/index.html", "privacidade/index.html", "termos/index.html", "prioridade/index.html",
                     "blog-src/src/layouts/Base.astro"):
            self.assertIn(must, rels)
        for f in files:
            rel = str(f.relative_to(self.ROOT))
            with self.subTest(rel=rel):
                h = f.read_text(encoding="utf-8")
                nav = re.search(r'<div class="nav-links">(.*?)</div>', h, re.S).group(1)
                self.assertRegex(nav, r'<a href="/consultoria/"[ >]')
                ctas = re.findall(r'<a\b[^>]*class="nav-cta"[^>]*>', nav)
                self.assertEqual(len(ctas), 1, ctas)
                self.assertIn('href="/consultoria/#como-contratar"', ctas[0])
                self.assertNotIn("target=", ctas[0])
                if rel != "consultoria/index.html":
                    foot = re.search(r"<footer>(.*?)</footer>", h, re.S).group(1)
                    self.assertIn('href="/consultoria/"', foot)

    def test_root_pages_load_one_version_of_site_js(self):
        # #113 r4 (P3 do agy e do Claude): o #142 mudou o site.js e subiu o ?v=; página que entra por merge com a versão
        # antiga cria outra chave de cache para o mesmo script. Revisão automática do #142 (P3): cada página carrega o
        # site.js exatamente uma vez, sempre com ?v= de 12 dígitos, como nos testes do consent.js e do track.js.
        versions = {}
        pages = self.sources()[:-1]
        self.assertIn("ferramentas/llm-ranking/index.html", {str(f.relative_to(self.ROOT)) for f in pages})
        for f in pages:
            rel = str(f.relative_to(self.ROOT))
            with self.subTest(rel=rel):
                h = f.read_text(encoding="utf-8")
                self.assertEqual(len(re.findall(r"/assets/site\.js\b", h)), 1, rel)
                m = re.findall(r'<script src="/assets/site\.js\?v=(\d{12})"></script>', h)
                self.assertEqual(len(m), 1, rel)
                versions.setdefault(m[0], []).append(rel)
        self.assertEqual(len(versions), 1, versions)


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


class ConsultingTest(unittest.TestCase):
    """#136: a /consultoria/ existe, com o título, o H1 e o JSON-LD Service + Offer ligado à Person."""
    TITLE = "<title>Consultoria em IA para empresas | Guilherme Favaron</title>"
    H1 = "<h1>Consultoria em inteligência artificial para empresas</h1>"
    PROVIDER = '{"@type":"Person","@id":"https://guifav.dev/#person","name":"G","url":"https://guifav.dev/"}'
    LD = ('<script type="application/ld+json">{"@context":"https://schema.org","@type":"Service",'
          '"provider":' + PROVIDER + ','
          '"offers":{"@type":"Offer","price":"0","priceCurrency":"BRL"}}</script>')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.site = Path(self.tmp.name) / "_site"
        (self.site / "consultoria").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def got(self, head=None, body=None) -> list[str]:
        head = self.TITLE + self.LD if head is None else head
        body = self.H1 if body is None else body
        (self.site / "consultoria" / "index.html").write_text(
            f"<html><head>{head}</head><body>{body}</body></html>", encoding="utf-8")
        return C.check_consulting(self.site)

    def test_complete_page_passes(self):
        self.assertEqual(self.got(), [])
        # @graph, offers em lista (com price string "0") e H1 com marcação dentro também valem
        graph = ('<script type="application/ld+json">[{"@context":"https://schema.org","@graph":[{"@type":"Service",'
                 '"provider":' + self.PROVIDER + ',"offers":[{"@type":"Offer","price":"0","priceCurrency":"BRL"}]}]}]'
                 '</script>')
        self.assertEqual(self.got(head=self.TITLE + graph,
                                  body="<h1>Consultoria em <span>inteligência artificial</span>\n para empresas</h1>"), [])

    def test_missing_page_fails(self):
        self.assertEqual(C.check_consulting(self.site), ["consultoria/index.html: a página de consultoria sumiu do "
                                                         "site montado (site-manifest.txt)"])

    def test_title_h1_and_json_ld_are_required(self):
        commented = "<!--" + self.LD + "-->"
        for name, head, body, needle in (
            ("sem título", self.LD, None, "o título precisa ser"),
            ("título trocado", "<title>Consultoria | Guilherme Favaron</title>" + self.LD, None, "o título precisa ser"),
            ("título em comentário", "<!--" + self.TITLE + "-->" + self.LD, None, "o título precisa ser"),
            ("sem H1", None, "<h2>Consultoria em inteligência artificial para empresas</h2>", "um H1"),
            ("dois H1", None, self.H1 + "<h1>Outro</h1>", "um H1"),
            ("H1 trocado", None, "<h1>Consultoria em IA</h1>", "um H1"),
            ("sem JSON-LD", self.TITLE, None, "sem o JSON-LD Service"),
            ("JSON-LD em comentário", self.TITLE + commented, None, "sem o JSON-LD Service"),
            ("sem Offer", self.TITLE + self.LD.replace('"Offer"', '"Thing"'), None, "sem o JSON-LD Service"),
            ("provider sem @id", self.TITLE + self.LD.replace('"@id":"https://guifav.dev/#person",', ''), None,
             "sem o JSON-LD Service"),
            ("provider sem nome", self.TITLE + self.LD.replace('"name":"G",', ''), None, "sem o JSON-LD Service"),
            ("provider sem tipo", self.TITLE + self.LD.replace('"@type":"Person",', ''), None, "sem o JSON-LD Service"),
            ("provider com url de outro site", self.TITLE + self.LD.replace('"url":"https://guifav.dev/"',
                                                                            '"url":"https://guifav.dev.evil/"'), None,
             "sem o JSON-LD Service"),
            ("Offer sem preço", self.TITLE + self.LD.replace('"price":"0",', ''), None, "sem o JSON-LD Service"),
            ("Offer com preço null", self.TITLE + self.LD.replace('"price":"0"', '"price":null'), None, "sem o JSON-LD Service"),
            ("Offer com preço booleano", self.TITLE + self.LD.replace('"price":"0"', '"price":false'), None,
             "sem o JSON-LD Service"),
            ("Offer com preço em texto solto", self.TITLE + self.LD.replace('"price":"0"', '"price":"grátis"'), None,
             "sem o JSON-LD Service"),
            ("Offer com preço negativo", self.TITLE + self.LD.replace('"price":"0"', '"price":-1'), None,
             "sem o JSON-LD Service"),
            ("Offer com preço NaN", self.TITLE + self.LD.replace('"price":"0"', '"price":NaN'), None,
             "sem o JSON-LD Service"),
            ("Offer com preço infinito", self.TITLE + self.LD.replace('"price":"0"', '"price":1e309'), None,
             "sem o JSON-LD Service"),
            ("Offer com preço texto negativo", self.TITLE + self.LD.replace('"price":"0"', '"price":"-1"'), None,
             "sem o JSON-LD Service"),
            ("Offer com preço 0 numérico", self.TITLE + self.LD.replace('"price":"0"', '"price":0'), None,
             "sem o JSON-LD Service"),
            ("Offer com preço 500", self.TITLE + self.LD.replace('"price":"0"', '"price":"500"'), None,
             "sem o JSON-LD Service"),
            ("lista com Offer 0 e Offer 500", self.TITLE + self.LD.replace(
                '"offers":{"@type":"Offer","price":"0","priceCurrency":"BRL"}',
                '"offers":[{"@type":"Offer","price":"0","priceCurrency":"BRL"},'
                '{"@type":"Offer","price":"500","priceCurrency":"BRL"}]'), None,
             "sem o JSON-LD Service"),
            ("provider com nome null", self.TITLE + self.LD.replace('"name":"G"', '"name":null'), None,
             "sem o JSON-LD Service"),
            ("Offer em outra moeda", self.TITLE + self.LD.replace('"BRL"', '"USD"'), None, "sem o JSON-LD Service"),
            ("Service com o @id da Person", self.TITLE + self.LD.replace('"@type":"Service",',
                                                                         '"@type":"Service","@id":"https://guifav.dev/#person",'),
             None, "sem o JSON-LD Service"),
            ("JSON-LD em data-type", self.TITLE + self.LD.replace(' type=', ' data-type='), None,
             "sem o JSON-LD Service"),
            ("JSON-LD em template", self.TITLE + "<template>" + self.LD + "</template>", None, "sem o JSON-LD Service"),
            ("contexto de outro vocabulário", self.TITLE + self.LD.replace("https://schema.org", "https://x.org"), None,
             "sem o JSON-LD Service"),
            ("FAQPage", self.TITLE + self.LD + '<script type="application/ld+json">{"@context":"https://schema.org",'
             '"@type":"FAQPage"}</script>', None, "FAQPage"),
            ("outro tipo", self.TITLE + self.LD.replace('"Service"', '"Product"'), None, "sem o JSON-LD Service"),
            ("JSON quebrado", self.TITLE + self.LD.replace("}}", "}"), None, "sem o JSON-LD Service"),
        ):
            with self.subTest(name):
                got = self.got(head=head, body=body)
                self.assertEqual(len(got), 1, got)
                self.assertIn(needle, got[0])

    def test_repo_page_passes(self):
        # A página do repo, como vai ao ar (o assemble_site a copia sem mudança).
        (self.site / "consultoria" / "index.html").write_bytes(
            (Path(__file__).resolve().parent.parent / C.CONSULTING_PAGE).read_bytes())
        self.assertEqual(C.check_consulting(self.site), [])

    def test_main_runs_the_consulting_rule(self):
        site = Path(self.tmp.name) / "vazio"
        site.mkdir()
        consts = Path(self.tmp.name) / "consts.ts"
        consts.write_text("export const PRE_CUTOVER_NOINDEX = false;\n", encoding="utf-8")
        err = io.StringIO()
        with isolate_main_rule("check_consulting"), contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = C.main(["--site", str(site), "--posts", str(self.tmp.name), "--consts", str(consts)])
        self.assertEqual(code, 1)
        self.assertIn("consultoria/index.html: a página de consultoria sumiu", err.getvalue())


class PaidConsultingTest(unittest.TestCase):
    """#105 (decisão 26/11/2026): sem o link antigo dos R$ 500 e sem Service com price diferente da string '0'."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.site = Path(self.tmp.name) / "_site"
        self.site.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def w(self, rel: str, html: str) -> None:
        p = self.site / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(html, encoding="utf-8")

    def test_old_schedule_id_fails_in_attribute_text_and_jsonld(self):
        url = f"https://calendar.app.google/{C.OLD_PAID_SCHEDULE_ID}"
        cases = {
            "atributo": ("index.html", f'<html><body><a href="{url}">Agendar</a></body></html>'),
            "texto": ("blog/post/index.html", f"<html><body><p>Agenda antiga: {C.OLD_PAID_SCHEDULE_ID}</p></body></html>"),
            "json-ld": ("ferramentas/index.html",
                        "<html><head>" + ld_script({"@context": "https://schema.org", "@type": "WebPage", "url": url})
                        + "</head><body></body></html>"),
        }
        for name, (rel, html) in cases.items():
            with self.subTest(case=name):
                got = C.check_page_paid_consulting(rel, html)
                self.assertEqual(len(got), 1, got)
                self.assertIn(C.OLD_PAID_SCHEDULE_ID, got[0])

    def test_service_offers_must_have_string_zero_price_anywhere_in_site(self):
        cases = {
            "price 500": {"@context": "https://schema.org", "@type": "Service",
                          "offers": {"@type": "Offer", "price": "500", "priceCurrency": "BRL"}},
            "price 0 numérico": {"@context": "https://schema.org", "@type": "Service",
                                 "offers": {"@type": "Offer", "price": 0, "priceCurrency": "BRL"}},
            "price 0.0 float": {"@context": "https://schema.org", "@type": "Service",
                                "offers": {"@type": "Offer", "price": 0.0, "priceCurrency": "BRL"}},
            "price 0.00 texto": {"@context": "https://schema.org", "@type": "Service",
                                 "offers": {"@type": "Offer", "price": "0.00", "priceCurrency": "BRL"}},
            "price ausente com priceCurrency": {"@context": "https://schema.org", "@type": "Service",
                                                "offers": {"@type": "Offer", "priceCurrency": "BRL"}},
            "lista com uma Offer 0 e outra 500": {
                "@context": "https://schema.org",
                "@type": "Service",
                "offers": [
                    {"@type": "Offer", "price": "0", "priceCurrency": "BRL"},
                    {"@type": "Offer", "price": "500", "priceCurrency": "BRL"},
                ],
            },
            "offers aninhado em hasOfferCatalog": {
                "@context": "https://schema.org",
                "@type": "Service",
                "hasOfferCatalog": {
                    "@type": "OfferCatalog",
                    "itemListElement": [
                        {"@type": "OfferCatalog", "offers": {"@type": "Offer", "price": "500", "priceCurrency": "BRL"}}
                    ],
                },
            },
            "Service aninhado em WebPage": {
                "@context": "https://schema.org",
                "@type": "WebPage",
                "mainEntity": {"@type": "Service", "offers": {"@type": "Offer", "price": "500", "priceCurrency": "BRL"}},
            },
            "Service em @graph": {
                "@context": "https://schema.org",
                "@graph": [{"@type": "Service", "offers": {"@type": "Offer", "price": "500", "priceCurrency": "BRL"}}],
            },
        }
        for name, data in cases.items():
            with self.subTest(case=name):
                html = f"<html><head>{ld_script(data)}</head><body></body></html>"
                got = C.check_page_paid_consulting("outra/index.html", html)
                self.assertEqual(len(got), 1, got)
                self.assertIn("Offer em Service", got[0])

    def test_offer_with_item_offered_service_fails(self):
        bad_cases = {
            "Offer com itemOffered Service": {
                "@context": "https://schema.org",
                "@type": "Offer",
                "price": "500",
                "priceCurrency": "BRL",
                "itemOffered": {"@type": "Service", "name": "Consultoria em IA"},
            },
            "Offer com itemOffered ProfessionalService": {
                "@context": "https://schema.org",
                "@type": "Offer",
                "price": "500",
                "priceCurrency": "BRL",
                "itemOffered": {"@type": "ProfessionalService", "name": "Consultoria em IA"},
            },
            "Offer com itemOffered em lista": {
                "@context": "https://schema.org",
                "@type": "Offer",
                "price": "500",
                "itemOffered": [{"@type": "Service", "name": "Consultoria em IA"}],
            },
            "Offer em @graph referenciando Service por @id": {
                "@context": "https://schema.org",
                "@graph": [
                    {"@type": "Service", "@id": "https://guifav.dev/consultoria/#servico", "name": "Consultoria"},
                    {"@type": "Offer", "price": "500", "priceCurrency": "BRL",
                     "itemOffered": {"@id": "https://guifav.dev/consultoria/#servico"}},
                ],
            },
        }
        for name, data in bad_cases.items():
            with self.subTest(case=name):
                html = f"<html><head>{ld_script(data)}</head><body></body></html>"
                got = C.check_page_paid_consulting("outra/index.html", html)
                self.assertEqual(len(got), 1, got)
                self.assertIn("Offer em Service", got[0])

        ok_cases = {
            "Offer gratuita com itemOffered Service": {
                "@context": "https://schema.org",
                "@type": "Offer",
                "price": "0",
                "priceCurrency": "BRL",
                "itemOffered": {"@type": "Service", "name": "Consultoria em IA"},
            },
            "Offer paga de item que nao e Service": {
                "@context": "https://schema.org",
                "@type": "Offer",
                "price": "500",
                "priceCurrency": "BRL",
                "itemOffered": {"@type": "Book", "name": "Livro"},
            },
        }
        for name, data in ok_cases.items():
            with self.subTest(case=name):
                html = f"<html><head>{ld_script(data)}</head><body></body></html>"
                self.assertEqual(C.check_page_paid_consulting("outra/index.html", html), [])

    def test_service_subtypes_with_paid_offer_fail(self):
        bad_cases = {
            "ProfessionalService": {
                "@context": "https://schema.org",
                "@type": "ProfessionalService",
                "offers": {"@type": "Offer", "price": 500, "priceCurrency": "BRL"},
            },
            "FinancialService": {
                "@context": "https://schema.org",
                "@type": "FinancialService",
                "offers": {"@type": "Offer", "price": "500", "priceCurrency": "BRL"},
            },
            "LegalService": {
                "@context": "https://schema.org",
                "@type": "LegalService",
                "offers": {"@type": "Offer", "price": 500, "priceCurrency": "BRL"},
            },
            "WebAPI (subtipo direto de Service)": {
                "@context": "https://schema.org",
                "@type": "WebAPI",
                "offers": {"@type": "Offer", "price": "500", "priceCurrency": "BRL"},
            },
            "ProfessionalService em @type lista": {
                "@context": "https://schema.org",
                "@type": ["LocalBusiness", "ProfessionalService"],
                "offers": {"@type": "Offer", "price": "500", "priceCurrency": "BRL"},
            },
        }
        for name, data in bad_cases.items():
            with self.subTest(case=name):
                html = f"<html><head>{ld_script(data)}</head><body></body></html>"
                got = C.check_page_paid_consulting("outra/index.html", html)
                self.assertEqual(len(got), 1, got)
                self.assertIn("Offer em Service", got[0])

        free_sub = {
            "@context": "https://schema.org",
            "@type": "ProfessionalService",
            "offers": {"@type": "Offer", "price": "0", "priceCurrency": "BRL"},
        }
        html = f"<html><head>{ld_script(free_sub)}</head><body></body></html>"
        self.assertEqual(C.check_page_paid_consulting("outra/index.html", html), [])

    def test_offer_with_zero_price_and_paid_price_specification_fails(self):
        bad_cases = {
            "priceSpecification objeto com price 500": {
                "@context": "https://schema.org",
                "@type": "Service",
                "offers": {
                    "@type": "Offer",
                    "price": "0",
                    "priceCurrency": "BRL",
                    "priceSpecification": {"@type": "PriceSpecification", "price": 500, "priceCurrency": "BRL"},
                },
            },
            "priceSpecification objeto com price texto 500": {
                "@context": "https://schema.org",
                "@type": "Service",
                "offers": {
                    "@type": "Offer",
                    "price": "0",
                    "priceCurrency": "BRL",
                    "priceSpecification": {"@type": "UnitPriceSpecification", "price": "500", "priceCurrency": "BRL"},
                },
            },
            "priceSpecification lista com 0 e 500": {
                "@context": "https://schema.org",
                "@type": "Service",
                "offers": {
                    "@type": "Offer",
                    "price": "0",
                    "priceCurrency": "BRL",
                    "priceSpecification": [
                        {"@type": "PriceSpecification", "price": "0", "priceCurrency": "BRL"},
                        {"@type": "PriceSpecification", "price": 500, "priceCurrency": "BRL"},
                    ],
                },
            },
            "Offer com itemOffered Service e priceSpecification paga": {
                "@context": "https://schema.org",
                "@type": "Offer",
                "price": "0",
                "priceCurrency": "BRL",
                "itemOffered": {"@type": "Service", "name": "Consultoria em IA"},
                "priceSpecification": {"@type": "PriceSpecification", "price": "500", "priceCurrency": "BRL"},
            },
        }
        for name, data in bad_cases.items():
            with self.subTest(case=name):
                html = f"<html><head>{ld_script(data)}</head><body></body></html>"
                got = C.check_page_paid_consulting("outra/index.html", html)
                self.assertEqual(len(got), 1, got)
                self.assertIn("priceSpecification", got[0])

        ok_spec = {
            "@context": "https://schema.org",
            "@type": "Service",
            "offers": {
                "@type": "Offer",
                "price": "0",
                "priceCurrency": "BRL",
                "priceSpecification": {"@type": "PriceSpecification", "price": "0", "priceCurrency": "BRL"},
            },
        }
        html = f"<html><head>{ld_script(ok_spec)}</head><body></body></html>"
        self.assertEqual(C.check_page_paid_consulting("outra/index.html", html), [])

    def test_real_consulting_page_passes(self):
        repo = Path(__file__).resolve().parent.parent
        self.w(C.CONSULTING_PAGE, (repo / C.CONSULTING_PAGE).read_text(encoding="utf-8"))
        self.assertEqual(C.check_paid_consulting(self.site), [])
        self.assertEqual(C.check_consulting(self.site), [])

    def test_main_runs_the_rule(self):
        consts = Path(self.tmp.name) / "consts.ts"
        consts.write_text("export const PRE_CUTOVER_NOINDEX = false;\n", encoding="utf-8")
        posts = Path(self.tmp.name) / "posts"
        posts.mkdir()
        args = ["--site", str(self.site), "--posts", str(posts), "--consts", str(consts)]
        bad_ld = ld_script({"@context": "https://schema.org", "@type": "Service",
                            "offers": {"@type": "Offer", "price": "500", "priceCurrency": "BRL"}})
        ok_ld = ld_script({"@context": "https://schema.org", "@type": "Service",
                           "offers": {"@type": "Offer", "price": "0", "priceCurrency": "BRL"}})
        with isolate_main_rule("check_paid_consulting"):
            for html, code, needle in (
                (f"<html><body><a href='https://calendar.app.google/{C.OLD_PAID_SCHEDULE_ID}'>x</a></body></html>", 1, C.OLD_PAID_SCHEDULE_ID),
                (f"<html><head>{bad_ld}</head><body></body></html>", 1, "Offer em Service"),
                (f"<html><head>{ok_ld}</head><body></body></html>", 0, ""),
            ):
                with self.subTest(code=code, needle=needle):
                    self.w("index.html", html)
                    out, err = io.StringIO(), io.StringIO()
                    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                        self.assertEqual(C.main(args), code)
                    if needle:
                        self.assertIn(needle, err.getvalue())


class HomeHubTest(unittest.TestCase):
    """#136 e #105: a home montada lista os últimos artigos, linka cada página de tema e traz a Person completa."""

    # 8 listados (mais que os 6 da home), um podcast indexável, um fora do índice e um "False" do YAML.
    POSTS = (("a", "2026-11-20", ""), ("b", "2026-11-22T10:00:00Z", ""),
             ("c", "2026-11-22T07:00:00-03:00", ""),  # 10h UTC: empata com b e vem depois pelo slug
             ("pod", "2026-11-23", "type: podcast\n"), ("fora", "2026-11-24", "index: false\n"),
             ("fora2", "2026-11-25", "index: False\n"), ("d", "2026-11-19", ""), ("e", "2026-11-18", ""),
             ("f", "2026-11-17", ""), ("g", "2026-11-16", ""), ("velho", "2025-01-01", ""))
    WANT6 = ("/blog/b/", "/blog/c/", "/blog/a/", "/blog/d/", "/blog/e/", "/blog/f/")

    @staticmethod
    def valid_person(**overrides) -> dict:
        data = {
            "@context": "https://schema.org",
            "@type": "Person",
            "@id": "https://guifav.dev/#person",
            "name": "Guilherme de Mauro Favaron",
            "alternateName": ["Guilherme Favaron", "guifav"],
            "url": "https://guifav.dev/",
            "description": "Builder e CTO. Transformo problemas de negócio em aplicações reais de dados e IA.",
            "disambiguatingDescription": "Executivo de tecnologia e CTO do GRI Institute, builder e arquiteto de dados e inteligência artificial.",
            "knowsAbout": ["Inteligência Artificial Aplicada", "Arquitetura de Dados"],
        }
        data.update(overrides)
        return data

    @classmethod
    def person_script(cls, data: dict | None = None) -> str:
        payload = cls.valid_person() if data is None else data
        return f'<script type="application/ld+json">{json.dumps(payload, ensure_ascii=False)}</script>'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        r = Path(self.tmp.name)
        self.site, self.posts = r / "_site", r / "posts"
        self.posts.mkdir()
        for slug, date, extra in self.POSTS:
            (self.posts / f"{slug}.md").write_text(f"---\ntitle: t\nslug: {slug}\ndate: {date}\n{extra}---\nx\n",
                                                   encoding="utf-8")
        for t in ("t1", "t2"):
            d = self.site / "blog" / "tema" / t
            d.mkdir(parents=True)
            (d / "index.html").write_text("<html></html>", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def home(self, latest=WANT6, topics=("/blog/tema/t1/", "/blog/tema/t2/"), extra="", n=6, head: str | None = None) -> list[str]:
        lis = "".join(f'<li><a href="{h}">x</a></li>' for h in latest)
        tl = "".join(f'<a href="{h}">t</a>' for h in topics)
        ld = self.person_script() if head is None else head
        (self.site / "index.html").write_text(
            f'<html><head>{ld}</head><body><!-- ultimos-artigos --><ul class="latest-list">{lis}</ul><!-- /ultimos-artigos -->'
            f'<section id="temas">{tl}</section>{extra}</body></html>', encoding="utf-8")
        return C.check_home(self.site, self.posts, n)

    def test_expected_list_order_and_selection(self):
        self.assertEqual(C.expected_home_latest(self.posts, 6), list(self.WANT6))
        self.assertEqual(C.expected_home_latest(self.posts, 2), ["/blog/b/", "/blog/c/"])
        self.assertEqual(len(C.expected_home_latest(self.posts, 100)), 8)

    def test_complete_home_passes(self):
        self.assertEqual(self.home(), [])

    def test_latest_list_wrong_fails(self):
        for name, latest in (("vazia", ()), ("ordem", ("/blog/c/", "/blog/b/") + self.WANT6[2:]),
                             ("com podcast indexável", ("/blog/pod/",) + self.WANT6[:5]),
                             ("com index False", ("/blog/fora2/",) + self.WANT6[:5]),
                             ("faltando um", self.WANT6[:5]), ("um a mais", self.WANT6 + ("/blog/g/",))):
            with self.subTest(name):
                got = self.home(latest=latest)
                self.assertEqual(len(got), 1, got)
                self.assertIn("os últimos artigos da home precisam ser", got[0])

    def test_count_comes_from_the_argument(self):
        self.assertEqual(self.home(latest=self.WANT6[:3], n=3), [])
        self.assertTrue(self.home(latest=self.WANT6, n=7))

    def test_read_home_latest_from_consts(self):
        consts = Path(self.tmp.name) / "consts.ts"
        consts.write_text("export const RSS_LIMIT = 50;\nexport const HOME_LATEST = 6;\n", encoding="utf-8")
        self.assertEqual(C.read_home_latest(consts), 6)
        consts.write_text("// export const HOME_LATEST = 6;\n", encoding="utf-8")
        self.assertIsNone(C.read_home_latest(consts))
        self.assertIn("sem export const HOME_LATEST", self.home(n=None)[0])
        repo = Path(__file__).resolve().parent.parent / "blog-src" / "src" / "consts.ts"
        self.assertEqual(C.read_home_latest(repo), 6)

    def test_unreadable_date_is_reported(self):
        (self.posts / "ruim.md").write_text("---\ntitle: t\nslug: ruim\ndate: ontem\n---\nx\n", encoding="utf-8")
        self.assertEqual(self.home(), ["posts: ruim.md com date ilegível no frontmatter"])

    def test_list_without_markers_or_commented_fails(self):
        (self.site / "index.html").write_text(
            f'<head>{self.person_script()}</head><section id="temas"><a href="/blog/tema/t1/">t</a>'
            '<a href="/blog/tema/t2/">t</a></section>', encoding="utf-8")
        self.assertIn("são None", C.check_home(self.site, self.posts, 6)[0])
        self.assertEqual(self.home(), [])
        html = (self.site / "index.html").read_text(encoding="utf-8")
        (self.site / "index.html").write_text(html.replace('<ul class="latest-list">', '<!--<ul class="latest-list">')
                                              .replace("</ul>", "</ul>-->"), encoding="utf-8")
        self.assertIn("são []", C.check_home(self.site, self.posts, 6)[0])

    def test_topics_each_published_page_once(self):
        for name, topics, n in (("faltando", ("/blog/tema/t1/",), 0), ("repetido", ("/blog/tema/t1/",) * 2 +
                                ("/blog/tema/t2/",), 2)):
            with self.subTest(name):
                got = self.home(topics=topics)
                self.assertEqual(len(got), 1, got)
                self.assertIn(f"linka {n}", got[0])
        # tema linkado fora da seção não conta
        got = self.home(topics=("/blog/tema/t1/",), extra='<a href="/blog/tema/t2/">t</a>')
        self.assertIn("/blog/tema/t2/ uma vez, linka 0", got[0])

    def test_no_published_topic_page_fails(self):
        import shutil
        shutil.rmtree(self.site / "blog" / "tema")
        self.assertEqual(self.home(topics=()), ["blog/tema/: nenhuma página de tema publicada para a home linkar"])

    def test_home_person_requires_disambiguation_fields(self):
        def without(field: str) -> str:
            d = self.valid_person()
            d.pop(field)
            return self.person_script(d)

        cases = {
            "sem Person": ("", "0 nós Person"),
            "duas Person": (self.person_script() * 2, "2 nós Person"),
            "Person comentada": (f"<!-- {self.person_script()} -->", "0 nós Person"),
            "Person em template": (f"<template>{self.person_script()}</template>", "0 nós Person"),
            "Person em noscript": (f"<noscript>{self.person_script()}</noscript>", "0 nós Person"),
            "sem alternateName": (without("alternateName"), "alternateName"),
            "alternateName vazio": (self.person_script(self.valid_person(alternateName=[])), "alternateName"),
            "alternateName sem Guilherme Favaron": (self.person_script(self.valid_person(alternateName=["guifav"])), "alternateName"),
            "alternateName substring": (self.person_script(self.valid_person(alternateName="Guilherme Favaron Jr")), "alternateName"),
            "alternateName com item vazio": (self.person_script(self.valid_person(alternateName=["Guilherme Favaron", "  "])), "alternateName"),
            "alternateName com item nao string": (self.person_script(self.valid_person(alternateName=["Guilherme Favaron", 1])), "alternateName"),
            "sem knowsAbout": (without("knowsAbout"), "knowsAbout"),
            "knowsAbout vazio": (self.person_script(self.valid_person(knowsAbout=[])), "knowsAbout"),
            "knowsAbout string solta": (self.person_script(self.valid_person(knowsAbout="Inteligência Artificial")), "knowsAbout"),
            "knowsAbout com string vazia": (self.person_script(self.valid_person(knowsAbout=["IA", "  "])), "knowsAbout"),
            "knowsAbout com item nao string": (self.person_script(self.valid_person(knowsAbout=["IA", 42])), "knowsAbout"),
            "sem disambiguatingDescription": (without("disambiguatingDescription"), "disambiguatingDescription"),
            "disambiguatingDescription vazio": (self.person_script(self.valid_person(disambiguatingDescription="")), "disambiguatingDescription"),
            "disambiguatingDescription espacos": (self.person_script(self.valid_person(disambiguatingDescription="   ")), "disambiguatingDescription"),
            "disambiguatingDescription lista": (self.person_script(self.valid_person(disambiguatingDescription=["texto"])), "disambiguatingDescription"),
        }
        for name, (head, needle) in cases.items():
            with self.subTest(case=name):
                got = self.home(head=head)
                self.assertEqual(len(got), 1, got)
                self.assertIn("Person da home", got[0])
                self.assertIn(needle, got[0])
        # String exata em alternateName e Person dentro de @graph passam.
        self.assertEqual(self.home(head=self.person_script(self.valid_person(alternateName="Guilherme Favaron"))), [])
        graph_person = {k: v for k, v in self.valid_person().items() if k != "@context"}
        graph_head = self.person_script({"@context": "https://schema.org", "@graph": [graph_person]})
        self.assertEqual(self.home(head=graph_head), [])

    def test_repo_home_person_passes_and_matches_contract(self):
        home = (Path(__file__).resolve().parent.parent / "index.html").read_text(encoding="utf-8")
        self.assertEqual(C.home_person_problems(home), [])
        persons = [n for n in C._ld_nodes(home) if C._has_ld_type(n, "Person") and n.get("@id") == C.PERSON_ID]
        self.assertEqual(len(persons), 1)
        p = persons[0]
        self.assertEqual(p["name"], "Guilherme de Mauro Favaron")
        self.assertEqual(p["alternateName"], ["Guilherme Favaron", "guifav"])
        self.assertTrue(6 <= len(p["knowsAbout"]) <= 8, p["knowsAbout"])
        disamb = p["disambiguatingDescription"]
        self.assertNotIn("\u2013", disamb)
        self.assertNotIn("\u2014", disamb)
        self.assertIsNone(re.search(r"\d", disamb))

    def test_missing_home_fails_and_main_runs_the_rule(self):
        self.assertEqual(C.check_home(self.site, self.posts, 6), ["index.html: home ausente no site montado"])
        consts = Path(self.tmp.name) / "consts.ts"
        consts.write_text("export const PRE_CUTOVER_NOINDEX = false;\nexport const HOME_LATEST = 6;\n", encoding="utf-8")
        err = io.StringIO()
        with isolate_main_rule("check_home"), contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = C.main(["--site", str(self.site), "--posts", str(self.posts), "--consts", str(consts)])
        self.assertEqual(code, 1)
        self.assertEqual(err.getvalue().splitlines(), ["ERRO: index.html: home ausente no site montado"])


class LegalTest(unittest.TestCase):
    """#146: /privacidade/ e /termos/ no build e no sitemap-site.xml, com título, H1, canonical, âncoras e rodapé em todo o site."""

    GA = TrackingTest.GA
    FOOT_GA = ('<footer><p><a href="/privacidade/">Privacidade</a> &middot; '
               '<a href="/termos/">Termos</a> &middot; '
               '<a href="/privacidade/#cookies" data-consent-open>Cookies</a></p></footer>')
    FOOT_NO_GA = ('<footer><p><a href="/privacidade/">Privacidade</a> &middot; '
                  '<a href="/termos/">Termos</a> &middot; '
                  '<a href="/privacidade/#cookies">Cookies</a></p></footer>')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.site = Path(self.tmp.name) / "_site"
        self.site.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def w(self, rel: str, content: str) -> None:
        p = self.site / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    def legal_doc(self, url: str, title: str, h1: str, body: str = "", head: str = "", foot: str | None = None, ga: bool = True) -> str:
        return (f'<!DOCTYPE html><html lang="pt-BR"><head><title>{title}</title>'
                f'<link rel="canonical" href="https://guifav.dev{url}">{self.GA if ga else ""}{head}</head>'
                f'<body><main><h1>{h1}</h1>{body}</main>{self.FOOT_GA if foot is None else foot}</body></html>')

    def build_valid_site(self) -> None:
        anchors = "".join(f'<h2 id="{a}">{a}</h2>' for a in C.PRIVACY_ANCHORS)
        self.w("privacidade/index.html", self.legal_doc("/privacidade/", "Política de privacidade | Guilherme Favaron",
                                                        "Política de privacidade", anchors))
        self.w("termos/index.html", self.legal_doc("/termos/", "Termos de uso | Guilherme Favaron", "Termos de uso"))
        self.w("blog/um-post/index.html", f"<!DOCTYPE html><html><head>{self.GA}</head><body>{self.FOOT_GA}</body></html>")
        self.w("404.html", f"<!DOCTYPE html><html><head></head><body>{self.FOOT_NO_GA}</body></html>")
        self.w("portfolio/mockups/crm.html", "<!DOCTYPE html><html><head></head><body>mockup sem rodapé</body></html>")
        self.w("sitemap-site.xml",
               '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
               '<url><loc>https://guifav.dev/privacidade/</loc><lastmod>2026-11-24T12:00:00-03:00</lastmod></url>'
               '<url><loc>https://guifav.dev/termos/</loc><lastmod>2026-11-24T12:00:00-03:00</lastmod></url>'
               "</urlset>")

    def test_complete_site_passes(self):
        self.build_valid_site()
        self.assertEqual(C.check_legal(self.site), [])

    def test_missing_legal_page_or_sitemap_entry_fails(self):
        for rel, url in (("privacidade/index.html", "/privacidade/"), ("termos/index.html", "/termos/")):
            with self.subTest(missing_page=rel):
                self.build_valid_site()
                (self.site / rel).unlink()
                got = C.check_legal(self.site)
                self.assertTrue(any(rel in p and "ausente" in p for p in got), got)
            with self.subTest(missing_sitemap=url):
                self.build_valid_site()
                sm = (self.site / "sitemap-site.xml").read_text(encoding="utf-8")
                (self.site / "sitemap-site.xml").write_text(sm.replace(f"https://guifav.dev{url}", "https://guifav.dev/x/"),
                                                            encoding="utf-8")
                got = C.check_legal(self.site)
                self.assertTrue(any(url in p and "sitemap-site.xml" in p for p in got), got)

    def test_noindex_bad_canonical_title_h1_or_missing_anchor_fails(self):
        self.build_valid_site()
        self.w("termos/index.html", self.legal_doc("/termos/", "Termos de uso | Guilherme Favaron", "Termos de uso",
                                                   head='<meta name="robots" content="noindex, follow">'))
        self.assertTrue(any("noindex" in p for p in C.check_legal(self.site)))

        self.build_valid_site()
        html = (self.site / "termos/index.html").read_text(encoding="utf-8").replace("https://guifav.dev/termos/", "https://guifav.dev/outro/")
        self.w("termos/index.html", html)
        self.assertTrue(any("canonical" in p for p in C.check_legal(self.site)))

        self.build_valid_site()
        html = (self.site / "termos/index.html").read_text(encoding="utf-8").replace('<link rel="canonical" href="https://guifav.dev/termos/">', "")
        self.w("termos/index.html", html)
        self.assertTrue(any("canonical" in p for p in C.check_legal(self.site)))

        self.build_valid_site()
        self.w("termos/index.html", self.legal_doc("/termos/", "Termos | Guilherme Favaron", "Termos de uso"))
        self.assertTrue(any("o título precisa ser" in p for p in C.check_legal(self.site)))

        self.build_valid_site()
        self.w("termos/index.html", self.legal_doc("/termos/", "Termos de uso | Guilherme Favaron", "Outro H1"))
        self.assertTrue(any("H1" in p for p in C.check_legal(self.site)))

        for anchor in C.PRIVACY_ANCHORS:
            for kind, rep in (("removida", ""), ("comentada", f'<!-- <h2 id="{anchor}">x</h2> -->'),
                              ("hidden", f'<h2 id="{anchor}" hidden>x</h2>'),
                              ("template", f'<template><h2 id="{anchor}">x</h2></template>')):
                with self.subTest(anchor=anchor, kind=kind):
                    self.build_valid_site()
                    html = (self.site / "privacidade/index.html").read_text(encoding="utf-8")
                    self.w("privacidade/index.html", html.replace(f'<h2 id="{anchor}">{anchor}</h2>', rep))
                    got = C.check_legal(self.site)
                    self.assertTrue(any(f"#{anchor}" in p for p in got), got)

    def test_privacy_requires_priority_and_courses_anchors(self):
        base_anchors = ("cookies", "materiais", "origem", "prioridade", "cursos", "quem-processa")
        for missing in ("prioridade", "cursos"):
            with self.subTest(missing=missing):
                self.build_valid_site()
                body = "".join(f'<h2 id="{a}">{a}</h2>' for a in base_anchors if a != missing)
                self.w("privacidade/index.html", self.legal_doc(
                    "/privacidade/", "Política de privacidade | Guilherme Favaron", "Política de privacidade", body,
                ))
                got = C.check_legal(self.site)
                self.assertTrue(any(f"#{missing}" in p for p in got), got)

    def test_footer_rules_with_and_without_ga(self):
        doc_ga = lambda body: f"<!DOCTYPE html><html><head>{self.GA}</head><body>{body}</body></html>"
        doc_noga = lambda body: f"<!DOCTYPE html><html><head></head><body>{body}</body></html>"
        self.assertEqual(C.check_legal_footer("index.html", doc_ga(self.FOOT_GA)), [])
        self.assertEqual(C.check_legal_footer("404.html", doc_noga(self.FOOT_NO_GA)), [])
        self.assertEqual(C.check_legal_footer("portfolio/mockups/crm.html", "<html><body>sem rodapé</body></html>"), [])

        bad_ga = {
            "sem footer": "<main></main>",
            "falta privacidade": '<footer><a href="/termos/">Termos</a><a href="/privacidade/#cookies" data-consent-open>Cookies</a></footer>',
            "falta termos": '<footer><a href="/privacidade/">Privacidade</a><a href="/privacidade/#cookies" data-consent-open>Cookies</a></footer>',
            "falta cookies": '<footer><a href="/privacidade/">Privacidade</a><a href="/termos/">Termos</a></footer>',
            "termos repetido": self.FOOT_GA.replace("</footer>", '<a href="/termos/">Termos</a></footer>'),
            "fora do footer": '<main><a href="/termos/">Termos</a></main><footer><a href="/privacidade/">Privacidade</a><a href="/privacidade/#cookies" data-consent-open>Cookies</a></footer>',
            "comentado": '<footer><!-- <a href="/termos/">Termos</a> --><a href="/privacidade/">Privacidade</a><a href="/privacidade/#cookies" data-consent-open>Cookies</a></footer>',
            "em template": '<footer><template><a href="/termos/">Termos</a></template><a href="/privacidade/">Privacidade</a><a href="/privacidade/#cookies" data-consent-open>Cookies</a></footer>',
            "em noscript": '<footer><noscript><a href="/termos/">Termos</a></noscript><a href="/privacidade/">Privacidade</a><a href="/privacidade/#cookies" data-consent-open>Cookies</a></footer>',
            "hidden no link": '<footer><a href="/termos/" hidden>Termos</a><a href="/privacidade/">Privacidade</a><a href="/privacidade/#cookies" data-consent-open>Cookies</a></footer>',
            "hidden no pai": '<footer><div hidden><a href="/termos/">Termos</a></div><a href="/privacidade/">Privacidade</a><a href="/privacidade/#cookies" data-consent-open>Cookies</a></footer>',
            "nofollow": self.FOOT_GA.replace('href="/termos/"', 'href="/termos/" rel="nofollow"'),
            "cookies sem data-consent-open": self.FOOT_NO_GA,
            "termos com data-consent-open": self.FOOT_GA.replace('href="/termos/"', 'href="/termos/" data-consent-open'),
        }
        for name, body in bad_ga.items():
            with self.subTest(ga=True, case=name):
                self.assertTrue(C.check_legal_footer("blog/um-post/index.html", doc_ga(body)), name)
        # Página sem GA4: Cookies sem data-consent-open passa; com data-consent-open falha.
        self.assertTrue(any("sem GA4" in p for p in C.check_legal_footer("404.html", doc_noga(self.FOOT_GA))))

    def test_main_runs_the_legal_rule(self):
        consts = Path(self.tmp.name) / "consts.ts"
        consts.write_text("export const PRE_CUTOVER_NOINDEX = false;\n", encoding="utf-8")
        err = io.StringIO()
        with isolate_main_rule("check_legal"), \
                contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = C.main(["--site", str(self.site), "--posts", str(self.tmp.name), "--consts", str(consts)])
        self.assertEqual(code, 1)
        self.assertIn("privacidade/index.html: página legal ausente", err.getvalue())
        self.build_valid_site()
        with isolate_main_rule("check_legal"), \
                contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(C.main(["--site", str(self.site), "--posts", str(self.tmp.name), "--consts", str(consts)]), 0)


class LegalSourcesTest(unittest.TestCase):
    """#146: fontes reais do site (páginas legais na raiz, rodapés, traduções, sem Termos nos formulários nem travessões)."""

    REPO = Path(__file__).resolve().parent.parent

    def root_html_files(self) -> list[tuple[str, Path]]:
        entries = [l.strip() for l in (self.REPO / "site-manifest.txt").read_text(encoding="utf-8").splitlines()
                   if l.strip() and not l.startswith("#")]
        files: list[Path] = []
        for e in entries:
            p = self.REPO / e
            if e.endswith("/"):
                files += sorted(f for f in p.rglob("*.html") if "mockups" not in f.parts)
            elif e.endswith(".html"):
                files.append(p)
        return [(f.relative_to(self.REPO).as_posix(), f) for f in files]

    def test_old_blog_privacy_page_is_removed_and_new_pages_are_in_manifest(self):
        self.assertFalse((self.REPO / "blog-src/src/pages/privacidade.astro").exists())
        manifest = [l.strip() for l in (self.REPO / "site-manifest.txt").read_text(encoding="utf-8").splitlines()
                    if l.strip() and not l.startswith("#")]
        self.assertIn("privacidade/", manifest)
        self.assertIn("termos/", manifest)

    def test_legal_pages_pass_all_page_checks_and_copy_rules(self):
        site_css = (self.REPO / "assets/site.css").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as d:
            site = Path(d)
            for rel in C.LEGAL_PAGES:
                html = (self.REPO / rel).read_text(encoding="utf-8")
                with self.subTest(rel=rel):
                    self.assertEqual(C.check_titles_and_groups(rel, html), [])
                    self.assertEqual(C.check_tracking(rel, html), [])
                    self.assertEqual(C.check_ga_host_guard(rel, html), [])
                    self.assertEqual(C.check_consent(rel, html), [])
                    self.assertEqual(C.check_legal_footer(rel, html), [])
                    styles = re.findall(r"<style[^>]*>(.*?)</style>", html, flags=re.S | re.I)
                    self.assertEqual(C.contrast_problems([site_css, *styles]), [])
                    self.assertNotIn("\u2014", html)  # sem travessão
                    self.assertNotIn("\u2013", html)  # sem meia-risca
                dst = site / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_text(html, encoding="utf-8")
            (site / "sitemap-site.xml").write_text(
                '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                '<url><loc>https://guifav.dev/privacidade/</loc><lastmod>2026-11-24T12:00:00-03:00</lastmod></url>'
                '<url><loc>https://guifav.dev/termos/</loc><lastmod>2026-11-24T12:00:00-03:00</lastmod></url>'
                "</urlset>", encoding="utf-8")
            self.assertEqual(C.check_legal(site), [])

    def test_terms_length_and_history_link(self):
        html = (self.REPO / "termos/index.html").read_text(encoding="utf-8")
        body = re.search(r'<div class="post-body">(.*?)</div>', html, re.S).group(1)
        words = re.findall(r"\b\w+\b", re.sub(r"<[^>]+>", " ", body))
        self.assertLessEqual(len(words), 400, f"Termos passou de 400 palavras ({len(words)})")
        self.assertIn('href="https://github.com/guifav/guifav.github.io/commits/main/termos/index.html"', html)
        self.assertIn("versões anteriores", html)
        self.assertIn("oferecido como está, respondo pelo que a lei exige", html)

    def test_every_root_page_and_blog_layout_has_legal_footer_and_no_old_privacy_links(self):
        for rel, f in self.root_html_files():
            html = f.read_text(encoding="utf-8")
            with self.subTest(rel=rel):
                self.assertEqual(C.check_legal_footer(rel, html), [])
                self.assertNotIn("/blog/privacidade", html)
                if 'id="lang-toggle"' in html:
                    self.assertIn('"ft_privacy": "Privacy"', html)
                    self.assertIn('"ft_terms": "Terms"', html)
                    self.assertIn('href="/privacidade/" hreflang="pt-BR" data-i18n="ft_privacy"', html)
                    self.assertIn('href="/termos/" hreflang="pt-BR" data-i18n="ft_terms"', html)
        base = (self.REPO / "blog-src/src/layouts/Base.astro").read_text(encoding="utf-8")
        foot = re.search(r"<footer>(.*?)</footer>", base, re.S).group(1)
        self.assertIn('<a href="/privacidade/">Privacidade</a>', foot)
        self.assertIn('<a href="/termos/">Termos</a>', foot)
        self.assertIn('<a href="/privacidade/#cookies" data-consent-open>Cookies</a>', foot)
        self.assertNotIn("/blog/privacidade", base)

    def test_terms_not_in_forms_or_cookie_banner(self):
        self.assertNotIn("/termos/", (self.REPO / "assets/consent.js").read_text(encoding="utf-8"))
        self.assertNotIn("/termos/", (self.REPO / "blog-src/src/components/SubscribeForm.astro").read_text(encoding="utf-8"))
        for rel in ("index.html", "ferramentas/planilha-custos-ia/index.html", "ferramentas/mapa-decisao-ia/index.html", C.BOOK_PAGE, C.PRIORITY_PAGE):
            html = (self.REPO / rel).read_text(encoding="utf-8")
            for form in re.findall(r"<form\b[^>]*>.*?</form>", html, flags=re.S | re.I):
                self.assertNotIn("/termos/", form, rel)


class CoursesTest(unittest.TestCase):
    """#166: /cursos/ existe com noindex, canonical próprio, título e link para /cursos/chatgpt-no-dia-a-dia, fora do sitemap;
    /cursos/ aparece em todos os rodapés (fora /cursos/ e mockups) e nunca no menu principal."""

    GA = TrackingTest.GA
    FOOT_COURSES = '<footer><p><a href="/cursos/">Cursos</a></p></footer>'
    NAV = '<nav aria-label="Principal"><a href="/portfolio/">Portfólio</a><a href="/consultoria/">Consultoria</a></nav>'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.site = Path(self.tmp.name) / "_site"
        self.site.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def w(self, rel: str, content: str) -> None:
        p = self.site / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    def courses_doc(self, title: str = "Cursos | Guilherme Favaron", robots: str = ROBOTS_NOINDEX,
                    canonical: str | None = "https://guifav.dev/cursos/",
                    body: str = '<a href="/cursos/chatgpt-no-dia-a-dia">Abrir</a>',
                    nav: str | None = None, foot: str = '<footer><p><a href="/">Início</a></p></footer>') -> str:
        c = "" if canonical is None else f'<link rel="canonical" href="{canonical}">'
        return (f'<!DOCTYPE html><html lang="pt-BR"><head><title>{title}</title>{robots}{c}{self.GA}</head>'
                f'<body>{self.NAV if nav is None else nav}<main>{body}</main>{foot}</body></html>')

    def build_valid_site(self) -> None:
        self.w("cursos/index.html", self.courses_doc())
        self.w("index.html", f"<!DOCTYPE html><html><head>{self.GA}</head><body>{self.NAV}{self.FOOT_COURSES}</body></html>")
        self.w("404.html", f"<!DOCTYPE html><html><head></head><body>{self.NAV}{self.FOOT_COURSES}</body></html>")
        self.w("portfolio/mockups/crm.html", "<!DOCTYPE html><html><head></head><body>mockup</body></html>")
        self.w("sitemap-site.xml",
               '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
               '<url><loc>https://guifav.dev/</loc><lastmod>2026-11-24T12:00:00-03:00</lastmod></url>'
               "</urlset>")

    def test_complete_site_passes(self):
        self.build_valid_site()
        self.assertEqual(C.check_courses(self.site), [])

    def test_missing_page_or_listed_in_sitemap_fails(self):
        self.build_valid_site()
        (self.site / "cursos/index.html").unlink()
        self.assertTrue(any("cursos/index.html" in p and "ausente" in p for p in C.check_courses(self.site)))

        self.build_valid_site()
        self.w("sitemap-site.xml",
               '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
               '<url><loc>https://guifav.dev/</loc><lastmod>2026-11-24T12:00:00-03:00</lastmod></url>'
               '<url><loc>https://guifav.dev/cursos/</loc><lastmod>2026-11-24T12:00:00-03:00</lastmod></url>'
               "</urlset>")
        self.assertTrue(any("sitemap-site.xml" in p and "/cursos/" in p for p in C.check_courses(self.site)))

    def test_page_requirements_noindex_canonical_title_and_course_link(self):
        for name, kw, needle in (
            ("sem noindex", {"robots": ROBOTS_INDEX}, "noindex"),
            ("sem meta robots", {"robots": ""}, "noindex"),
            ("sem canonical", {"canonical": None}, "canonical"),
            ("canonical errado", {"canonical": "https://guifav.dev/cursos"}, "canonical"),
            ("título errado", {"title": "Cursos de IA | Guilherme Favaron"}, "o título precisa ser"),
            ("sem link do curso", {"body": "<p>vazio</p>"}, "/cursos/chatgpt-no-dia-a-dia"),
            ("link antigo da academia", {"body": '<a href="/cursos/academia-ia">1</a>'}, "/cursos/chatgpt-no-dia-a-dia"),
            ("link do curso duplicado", {"body": '<a href="/cursos/chatgpt-no-dia-a-dia">1</a><a href="/cursos/chatgpt-no-dia-a-dia">2</a>'}, "/cursos/chatgpt-no-dia-a-dia"),
            ("link do curso comentado", {"body": '<!-- <a href="/cursos/chatgpt-no-dia-a-dia">1</a> -->'}, "/cursos/chatgpt-no-dia-a-dia"),
            ("link do curso hidden", {"body": '<a href="/cursos/chatgpt-no-dia-a-dia" hidden>1</a>'}, "/cursos/chatgpt-no-dia-a-dia"),
            ("link do curso nofollow", {"body": '<a href="/cursos/chatgpt-no-dia-a-dia" rel="nofollow">1</a>'}, "rel=nofollow"),
        ):
            with self.subTest(case=name):
                self.build_valid_site()
                self.w("cursos/index.html", self.courses_doc(**kw))
                got = C.check_courses(self.site)
                self.assertTrue(any(needle in p for p in got), got)

    def test_footer_and_nav_rules(self):
        doc = lambda body: f"<!DOCTYPE html><html><head></head><body>{body}</body></html>"
        self.assertEqual(C.check_courses_footer("index.html", doc(self.NAV + self.FOOT_COURSES)), [])
        # <nav class="footer-tools"> dentro de <footer> não conta como menu principal.
        in_footer_nav = '<footer><nav class="footer-tools"><a href="/cursos/">Cursos</a></nav></footer>'
        self.assertEqual(C.check_courses_footer("index.html", doc(self.NAV + in_footer_nav)), [])
        # <footer> dentro de <main> (como no framework-seo-aeo.html) funciona normalmente.
        nested_footer = f"<main>{self.FOOT_COURSES}</main>"
        self.assertEqual(C.check_courses_footer("ferramentas/seo-geo-aeo-framework/framework-seo-aeo.html", doc(nested_footer)), [])
        # Mockups ficam fora da regra; /cursos/ não precisa de auto-link no rodapé, mas também não pode no menu.
        self.assertEqual(C.check_courses_footer("portfolio/mockups/crm.html", doc("<p>x</p>")), [])
        self.assertEqual(C.check_courses_footer("cursos/index.html", doc(self.NAV + "<footer></footer>")), [])
        self.assertTrue(any("menu principal" in p for p in C.check_courses_footer(
            "cursos/index.html", doc('<nav><a href="/cursos/">Cursos</a></nav><footer></footer>'))))

        for name, body, needle in (
            ("sem footer", "<main></main>", "sem <footer>"),
            ("falta link", "<footer><a href='/'>Início</a></footer>", "uma vez, tem 0"),
            ("duplicado", '<footer><a href="/cursos/">1</a><a href="/cursos/">2</a></footer>', "uma vez, tem 2"),
            ("fora do footer", '<main><a href="/cursos/">Cursos</a></main><footer></footer>', "uma vez, tem 0"),
            ("comentado", '<footer><!-- <a href="/cursos/">Cursos</a> --></footer>', "uma vez, tem 0"),
            ("em template", '<footer><template><a href="/cursos/">Cursos</a></template></footer>', "uma vez, tem 0"),
            ("em noscript", '<footer><noscript><a href="/cursos/">Cursos</a></noscript></footer>', "uma vez, tem 0"),
            ("hidden no link", '<footer><a href="/cursos/" hidden>Cursos</a></footer>', "uma vez, tem 0"),
            ("hidden no pai", '<footer><div hidden><a href="/cursos/">Cursos</a></div></footer>', "uma vez, tem 0"),
            ("nofollow", '<footer><a href="/cursos/" rel="nofollow">Cursos</a></footer>', "rel=nofollow"),
            ("no menu principal", '<nav><a href="/cursos/">Cursos</a></nav>' + self.FOOT_COURSES, "menu principal"),
            ("no menu principal absoluto", '<nav><a href="https://guifav.dev/cursos/">Cursos</a></nav>' + self.FOOT_COURSES, "menu principal"),
        ):
            with self.subTest(case=name):
                got = C.check_courses_footer("portfolio/index.html", doc(body))
                self.assertTrue(any(needle in p for p in got), got)

    def test_worker_paths_without_node(self):
        # Garante a cobertura de WORKER_PATHS mesmo em ambientes sem Node (mock do resolvedor WHATWG).
        from urllib.parse import urljoin, urlsplit

        def fake_resolve(pairs):
            out = []
            for base, ref in pairs:
                u = urlsplit(urljoin(base, ref))
                out.append({"protocol": u.scheme + ":", "hostname": u.hostname or "", "pathname": u.path})
            return out

        self.assertEqual(C.WORKER_PATHS, frozenset({"/cursos/chatgpt-no-dia-a-dia"}))
        self.build_valid_site()
        posts = Path(self.tmp.name) / "posts"
        posts.mkdir()
        self.w("blog/index.html", page('<a href="/cursos/">Cursos</a>'))
        self.w("blog/rss.xml", RSS)
        self.w("blog/sitemap.xml", SITEMAP.format("<url><loc>https://guifav.dev/blog/</loc></url>"))
        self.w("robots.txt", "User-agent: *\nDisallow: /portfolio/mockups/\n\nSitemap: https://guifav.dev/sitemap.xml\n")
        self.w("sitemap.xml", SITEMAP_INDEX.format(
            "<sitemap><loc>https://guifav.dev/sitemap-site.xml</loc></sitemap>"
            "<sitemap><loc>https://guifav.dev/blog/sitemap.xml</loc></sitemap>"))
        self.w("index.html", page('<a href="/cursos/">Cursos</a>', robots="", title=HOME_FIXTURE_TITLE))
        self.w("cursos/index.html", page('<a href="/cursos/chatgpt-no-dia-a-dia">Curso</a>', robots=ROBOTS_NOINDEX))
        self.w("404.html", page('<a href="/">home</a>', robots='<meta name="robots" content="noindex">'))
        (self.site / "portfolio/mockups/crm.html").unlink()
        with mock.patch.object(C, "resolve_urls", fake_resolve):
            self.assertEqual(C.check(self.site, posts, pre_cutover=False), [])
            for bad in ("/cursos/chatgpt-no-dia-a-dia-errado", "/cursos/chatgpt-no-dia-a-dia/",
                        "/cursos/chatgpt-no-dia-a-dia/aula-1", "/cursos/academia-ia"):
                with self.subTest(bad=bad):
                    self.w("cursos/index.html", page(f'<a href="{bad}">Curso</a>', robots=ROBOTS_NOINDEX))
                    got = C.check(self.site, posts, pre_cutover=False)
                    self.assertTrue(any(f"link interno quebrado: {bad}" in p for p in got), got)

    def test_main_runs_the_courses_rule(self):
        consts = Path(self.tmp.name) / "consts.ts"
        consts.write_text("export const PRE_CUTOVER_NOINDEX = false;\n", encoding="utf-8")
        err = io.StringIO()
        with isolate_main_rule("check_courses"), \
                contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = C.main(["--site", str(self.site), "--posts", str(self.tmp.name), "--consts", str(consts)])
        self.assertEqual(code, 1)
        self.assertIn("cursos/index.html: página de cursos ausente", err.getvalue())
        self.build_valid_site()
        with isolate_main_rule("check_courses"), \
                contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(C.main(["--site", str(self.site), "--posts", str(self.tmp.name), "--consts", str(consts)]), 0)


class CoursesSourcesTest(unittest.TestCase):
    """#166: fontes reais da página /cursos/, manifesto, rodapés do site, traduções em inglês e ausência no menu."""

    REPO = Path(__file__).resolve().parent.parent

    def root_html_files(self) -> list[tuple[str, Path]]:
        entries = [l.strip() for l in (self.REPO / "site-manifest.txt").read_text(encoding="utf-8").splitlines()
                   if l.strip() and not l.startswith("#")]
        files: list[Path] = []
        for e in entries:
            p = self.REPO / e
            if e.endswith("/"):
                files += sorted(f for f in p.rglob("*.html") if "mockups" not in f.parts)
            elif e.endswith(".html"):
                files.append(p)
        return [(f.relative_to(self.REPO).as_posix(), f) for f in files]

    def test_courses_page_in_manifest_and_passes_all_checks(self):
        manifest = [l.strip() for l in (self.REPO / "site-manifest.txt").read_text(encoding="utf-8").splitlines()
                    if l.strip() and not l.startswith("#")]
        self.assertIn("cursos/", manifest)
        html = (self.REPO / C.COURSES_PAGE).read_text(encoding="utf-8")
        site_css = (self.REPO / "assets/site.css").read_text(encoding="utf-8")
        self.assertEqual(C.check_titles_and_groups(C.COURSES_PAGE, html), [])
        self.assertEqual(C.check_tracking(C.COURSES_PAGE, html), [])
        self.assertEqual(C.check_ga_host_guard(C.COURSES_PAGE, html), [])
        self.assertEqual(C.check_consent(C.COURSES_PAGE, html), [])
        self.assertEqual(C.check_legal_footer(C.COURSES_PAGE, html), [])
        self.assertEqual(C.check_courses_footer(C.COURSES_PAGE, html), [])
        styles = re.findall(r"<style[^>]*>(.*?)</style>", html, flags=re.S | re.I)
        self.assertEqual(C.contrast_problems([site_css, *styles]), [])
        self.assertEqual(C.contrast_problems([site_css, *styles, ".course-status{background:var(--bg-card)}"]), [])
        self.assertNotIn("\u2014", html)  # sem travessão
        self.assertNotIn("\u2013", html)  # sem meia-risca
        with tempfile.TemporaryDirectory() as d:
            site = Path(d)
            dst = site / C.COURSES_PAGE
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(html, encoding="utf-8")
            (site / "sitemap-site.xml").write_text(
                '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                '<url><loc>https://guifav.dev/</loc><lastmod>2026-11-24T12:00:00-03:00</lastmod></url>'
                "</urlset>", encoding="utf-8")
            self.assertEqual(C.check_courses(site), [])

    def test_every_root_page_and_blog_layout_has_courses_footer_and_english_label(self):
        for rel, f in self.root_html_files():
            html = f.read_text(encoding="utf-8")
            with self.subTest(rel=rel):
                self.assertEqual(C.check_courses_footer(rel, html), [])
                if rel != C.COURSES_PAGE and 'id="lang-toggle"' in html:
                    self.assertIn('"ft_courses": "Courses"', html)
                    self.assertIn('href="/cursos/" hreflang="pt-BR" data-i18n="ft_courses"', html)
                    en = json.loads(re.search(r'<script type="application/json" id="i18n-en">(.*?)</script>', html, re.S).group(1))
                    self.assertEqual(en["ft_courses"], "Courses")
        home = (self.REPO / "index.html").read_text(encoding="utf-8")
        en_home = json.loads(re.search(r'<script type="application/json" id="i18n-en">(.*?)</script>', home, re.S).group(1))
        self.assertEqual(en_home["tools_courses"], "Courses &#8594;")
        base = (self.REPO / "blog-src/src/layouts/Base.astro").read_text(encoding="utf-8")
        self.assertEqual(C.check_courses_footer("blog/index.html", base), [])


class YoutubeFooterTest(unittest.TestCase):
    """#166: todo link de YouTube dentro de <footer> (fora mockups) aponta para https://www.youtube.com/@ia-para-leigos."""

    OK_LINK = f'<a href="{C.YOUTUBE_FOOTER_URL}" target="_blank" rel="noopener">YouTube</a>'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.site = Path(self.tmp.name) / "_site"
        self.site.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def w(self, rel: str, content: str) -> None:
        p = self.site / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    def doc(self, body: str) -> str:
        return f"<!DOCTYPE html><html><head></head><body>{body}</body></html>"

    def test_valid_footer_and_exemptions_pass(self):
        self.assertEqual(C.check_youtube_footer("index.html", self.doc(f"<footer><p>{self.OK_LINK}</p></footer>")), [])
        # Rodapé sem link de YouTube (como framework-seo-aeo.html) e página sem footer passam.
        self.assertEqual(C.check_youtube_footer("ferramentas/seo-geo-aeo-framework/framework-seo-aeo.html",
                                                self.doc('<footer><a href="/cursos/">Cursos</a></footer>')), [])
        self.assertEqual(C.check_youtube_footer("404.html", self.doc("<main><p>x</p></main>")), [])
        # Links de YouTube fora de <footer> não entram na regra (card da home, JSON-LD, corpo dos materiais).
        outside = ('<main><a href="https://youtube.com/@ia-aplicada-guilhermefavaron">Assistir</a>'
                   '<a href="https://www.youtube.com/@guifav">Canal @guifav</a></main>'
                   f"<footer>{self.OK_LINK}</footer>")
        self.assertEqual(C.check_youtube_footer("index.html", self.doc(outside)), [])
        # Mockups ficam fora da regra.
        self.assertEqual(C.check_youtube_footer("portfolio/mockups/crm.html",
                                                self.doc('<footer><a href="https://www.youtube.com/@guifav">YT</a></footer>')), [])
        # Links internos ou externos que só têm "youtube" no caminho ou domínio de outro site não contam.
        non_yt = ('<footer><a href="/blog/curso-ia-negocios-gratuito-youtube/">Curso</a>'
                  '<a href="https://youtube.com.evil.example/@guifav">Outro</a>'
                  '<a href="http://[invalido">Quebrado</a></footer>')
        self.assertEqual(C.check_youtube_footer("index.html", self.doc(non_yt)), [])
        # Comentários, template, noscript e hidden são ignorados, como nas outras regras de rodapé.
        inert = ('<footer><!-- <a href="https://www.youtube.com/@guifav">YT</a> -->'
                 '<template><a href="https://www.youtube.com/@guifav">YT</a></template>'
                 '<noscript><a href="https://www.youtube.com/@guifav">YT</a></noscript>'
                 '<a href="https://www.youtube.com/@guifav" hidden>YT</a>'
                 '<div hidden><a href="https://www.youtube.com/@guifav">YT</a></div>'
                 f"{self.OK_LINK}</footer>")
        self.assertEqual(C.check_youtube_footer("index.html", self.doc(inert)), [])

    def test_wrong_youtube_links_in_footer_fail(self):
        bad_urls = (
            "https://youtube.com/@ia-aplicada-guilhermefavaron",
            "https://www.youtube.com/@guifav",
            "https://youtube.com/@ia-para-leigos",
            "http://www.youtube.com/@ia-para-leigos",
            "https://www.youtube.com/@ia-para-leigos/",
            "https://www.youtube.com/@ia-para-leigos?sub_confirmation=1",
            "https://m.youtube.com/@ia-para-leigos",
            "https://youtu.be/abc",
            "https://www.youtube-nocookie.com/embed/abc",
            "//www.youtube.com/@guifav",
            "HTTPS://WWW.YOUTUBE.COM/@ia-para-leigos",
            "https://www.youtube.com./@ia-para-leigos",
            "https://www.youtube.com:bad/@guifav",
        )
        for href in bad_urls:
            with self.subTest(href=href):
                got = C.check_youtube_footer("portfolio/index.html", self.doc(f'<footer><a href="{href}">YouTube</a></footer>'))
                self.assertEqual(len(got), 1, got)
                self.assertIn(C.YOUTUBE_FOOTER_URL, got[0])
                self.assertIn(href, got[0])
        # <footer> dentro de <main> e link dentro de <nav class="footer-tools"> no rodapé também são verificados.
        for name, body in (
            ("footer em main", '<main><footer><a href="https://www.youtube.com/@guifav">YT</a></footer></main>'),
            ("nav no footer", '<footer><nav class="footer-tools"><a href="https://www.youtube.com/@guifav">YT</a></nav></footer>'),
        ):
            with self.subTest(case=name):
                got = C.check_youtube_footer("index.html", self.doc(body))
                self.assertEqual(len(got), 1, got)

    def test_check_youtube_site_and_main_wiring(self):
        self.w("index.html", self.doc(f"<footer>{self.OK_LINK}</footer>"))
        self.w("ferramentas/index.html", self.doc('<footer><a href="https://www.youtube.com/@guifav">YouTube</a></footer>'))
        self.w("portfolio/mockups/crm.html", self.doc('<footer><a href="https://www.youtube.com/@guifav">YouTube</a></footer>'))
        got = C.check_youtube(self.site)
        self.assertEqual(len(got), 1, got)
        self.assertIn("ferramentas/index.html", got[0])

        consts = Path(self.tmp.name) / "consts.ts"
        consts.write_text("export const PRE_CUTOVER_NOINDEX = false;\n", encoding="utf-8")
        err = io.StringIO()
        with isolate_main_rule("check_youtube"), \
                contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = C.main(["--site", str(self.site), "--posts", str(self.tmp.name), "--consts", str(consts)])
        self.assertEqual(code, 1)
        self.assertIn("ferramentas/index.html: link de YouTube no rodapé", err.getvalue())

        self.w("ferramentas/index.html", self.doc(f"<footer>{self.OK_LINK}</footer>"))
        with isolate_main_rule("check_youtube"), \
                contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(C.main(["--site", str(self.site), "--posts", str(self.tmp.name), "--consts", str(consts)]), 0)


class YoutubeFooterSourcesTest(unittest.TestCase):
    """#166: fontes reais do site com o link de YouTube no rodapé apontando para @ia-para-leigos, mantendo os links fora do rodapé."""

    REPO = Path(__file__).resolve().parent.parent
    EXPECTED_WITH_YT = (
        "index.html",
        "404.html",
        "consultoria/index.html",
        "portfolio/index.html",
        "cursos/index.html",
        "privacidade/index.html",
        "termos/index.html",
        "prioridade/index.html",
        "ferramentas/index.html",
        "ferramentas/llm-ranking/index.html",
        "ferramentas/mapa-decisao-ia/index.html",
        "ferramentas/mapa-decisao-ia/enviado/index.html",
        "ferramentas/planilha-custos-ia/index.html",
        "ferramentas/planilha-custos-ia/enviado/index.html",
        C.BOOK_PAGE,
        f"materiais/{C.BOOK_ID}/enviado/index.html",
        "blog-src/src/layouts/Base.astro",
    )

    def root_html_files(self) -> list[tuple[str, Path]]:
        entries = [l.strip() for l in (self.REPO / "site-manifest.txt").read_text(encoding="utf-8").splitlines()
                   if l.strip() and not l.startswith("#")]
        files: list[Path] = []
        for e in entries:
            p = self.REPO / e
            if e.endswith("/"):
                files += sorted(f for f in p.rglob("*.html") if "mockups" not in f.parts)
            elif e.endswith(".html"):
                files.append(p)
        return [(f.relative_to(self.REPO).as_posix(), f) for f in files]

    def test_every_root_page_and_blog_layout_passes_youtube_footer_rule(self):
        for rel, f in self.root_html_files():
            with self.subTest(rel=rel):
                self.assertEqual(C.check_youtube_footer(rel, f.read_text(encoding="utf-8")), [])
        base = (self.REPO / "blog-src/src/layouts/Base.astro").read_text(encoding="utf-8")
        self.assertEqual(C.check_youtube_footer("blog/index.html", base), [])

    def test_expected_footers_point_to_ia_para_leigos(self):
        for rel in self.EXPECTED_WITH_YT:
            with self.subTest(rel=rel):
                p = C._LegalFooter()
                p.feed((self.REPO / rel).read_text(encoding="utf-8"))
                p.close()
                yt_links = [href for href, _, _ in p.links if C._is_youtube_url(href)]
                self.assertEqual(yt_links, [C.YOUTUBE_FOOTER_URL], rel)

    def test_youtube_links_outside_footer_were_not_changed(self):
        home = (self.REPO / "index.html").read_text(encoding="utf-8")
        self.assertIn('"https://www.youtube.com/@guifav","https://www.youtube.com/@ia-para-leigos"', home)
        self.assertIn(
            '<div class="card"><h3>YouTube: @guifav</h3><p data-i18n="content_yt_d">'
            'Vídeos para quem decide sobre IA nas empresas, com comparações e testes medidos em uso real.</p>'
            '<div class="card-links">'
            '<a href="https://www.youtube.com/@guifav" target="_blank" rel="noopener" data-i18n="content_yt_a">Assistir &#8599;</a>'
            '<a href="https://www.youtube.com/@ia-para-leigos" target="_blank" rel="noopener" data-i18n="content_yt_a2">Cursos e séries: IA para Leigos &#8599;</a>'
            '</div></div>',
            home,
        )
        en = json.loads(re.search(r'<script type="application/json" id="i18n-en">(.*?)</script>', home, re.S).group(1))
        self.assertEqual(en["content_yt_d"], "Videos for people who make AI decisions in companies, with comparisons and tests measured in real use.")
        self.assertEqual(en["content_yt_a"], "Watch &#8599;")
        self.assertEqual(en["content_yt_a2"], "Courses and series: IA para Leigos &#8599;")
        self.assertNotIn(C.OLD_YOUTUBE_HANDLE, home.lower())
        for rel in ("ferramentas/mapa-decisao-ia/index.html", "ferramentas/planilha-custos-ia/index.html"):
            with self.subTest(rel=rel):
                html = (self.REPO / rel).read_text(encoding="utf-8")
                self.assertIn('<a class="btn" href="https://www.youtube.com/@guifav" target="_blank" rel="noopener" data-i18n="yt">Canal @guifav no YouTube</a>', html)


class OldYoutubeHandleTest(unittest.TestCase):
    """#105: o handle antigo do YouTube (ia-aplicada-guilhermefavaron) não pode aparecer em nenhum HTML montado."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.site = Path(self.tmp.name) / "_site"
        self.site.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def w(self, rel: str, content: str) -> None:
        p = self.site / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    def doc(self, body: str) -> str:
        return f"<!DOCTYPE html><html><head></head><body>{body}</body></html>"

    def test_clean_site_passes(self):
        self.w("index.html", self.doc(
            '<a href="https://www.youtube.com/@guifav">Assistir</a>'
            '<a href="https://www.youtube.com/@ia-para-leigos">Cursos</a>'
            '<p>Canais @guifav e @ia-para-leigos</p>'
        ))
        self.w("blog/um-post/index.html", self.doc("<p>Texto sobre IA aplicada sem o handle antigo.</p>"))
        self.assertEqual(C.check_old_youtube_handle(self.site), [])

    def test_old_handle_fails_in_attribute_and_text(self):
        cases = {
            "em href": '<a href="https://youtube.com/@ia-aplicada-guilhermefavaron">Assistir</a>',
            "em atributo data": '<div data-channel="@ia-aplicada-guilhermefavaron">x</div>',
            "em texto": '<p>Veja @ia-aplicada-guilhermefavaron no YouTube.</p>',
            "caixa mista": '<p>IA-Aplicada-GuilhermeFavaron</p>',
            "entidade HTML": '<a href="https://youtube.com/@&#105;a-aplicada-guilhermefavaron">Assistir</a>',
            "percent-encoding": '<a href="https://youtube.com/@%69a-aplicada-guilhermefavaron">Assistir</a>',
        }
        for name, body in cases.items():
            with self.subTest(case=name):
                got = C.check_page_old_youtube_handle("index.html", self.doc(body))
                self.assertEqual(len(got), 1, got)
                self.assertIn(C.OLD_YOUTUBE_HANDLE, got[0])

    def test_site_scan_and_main_wiring(self):
        self.w("index.html", self.doc('<a href="https://www.youtube.com/@guifav">Assistir</a>'))
        self.w("blog/p/index.html", self.doc("<p>Antigo @ia-aplicada-guilhermefavaron</p>"))
        got = C.check_old_youtube_handle(self.site)
        self.assertEqual(len(got), 1, got)
        self.assertIn("blog/p/index.html", got[0])

        consts = Path(self.tmp.name) / "consts.ts"
        consts.write_text("export const PRE_CUTOVER_NOINDEX = false;\n", encoding="utf-8")
        err = io.StringIO()
        with isolate_main_rule("check_old_youtube_handle"), \
                contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = C.main(["--site", str(self.site), "--posts", str(self.tmp.name), "--consts", str(consts)])
        self.assertEqual(code, 1)
        self.assertIn(C.OLD_YOUTUBE_HANDLE, err.getvalue())

        self.w("blog/p/index.html", self.doc("<p>Atualizado para @guifav</p>"))
        with isolate_main_rule("check_old_youtube_handle"), \
                contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(C.main(["--site", str(self.site), "--posts", str(self.tmp.name), "--consts", str(consts)]), 0)

    def test_repo_home_has_no_old_youtube_handle(self):
        home = (Path(__file__).resolve().parent.parent / "index.html").read_text(encoding="utf-8")
        self.assertEqual(C.check_page_old_youtube_handle("index.html", home), [])


class SummaryTest(Base):
    """#108: bloco 'Em resumo' acima de .post-body quando há summary e ausente quando não há, com contraste AA."""

    SUMMARY_HTML = (
        '<section class="post-summary" aria-label="Em resumo">'
        '<p class="post-summary-label">Em resumo</p>'
        '<p>Resposta direta em duas frases. Sem repetir a description.</p>'
        '</section><div class="post-body"><figure><img src="/blog/img/um-post/a.webp" alt="x"></figure></div>'
    )

    def write_post(self, slug: str, summary: str | None, html_body: str) -> None:
        lines = ["---", "title: Teste", "description: Descricao propria do post", f"slug: {slug}"]
        if summary is not None:
            lines.append(f'summary: "{summary}"')
        lines += ["---", "corpo", ""]
        (self.posts / f"{slug}.md").write_text("\n".join(lines), encoding="utf-8")
        self.w(f"blog/{slug}/index.html", page(html_body))

    def test_valid_with_and_without_summary(self):
        self.write_post("com-resumo", "Resposta direta em duas frases.", self.SUMMARY_HTML)
        self.write_post("sem-resumo", None, '<div class="post-body"><p>texto</p></div>')
        self.assertEqual(C.check_summary(self.site, self.posts), [])

    def test_yaml_multiline_and_indented_summary_are_recognized(self):
        # Regressão da revisão do PR #158 (Roger P1 / Codex P2): YAML válido com valor indentado na linha seguinte
        # ou em bloco >/| é aceito pelo Astro e precisa ser reconhecido como post com summary.
        yaml_with = {
            "indentado": "summary:\n  Resposta direta na linha seguinte.\n  Segunda frase.",
            "indentado com comentario": "summary: # nota\n  # comentario interno\n  Resposta valida.",
            "bloco dobrado": "summary: >\n  Resposta dobrada em duas linhas.",
            "bloco literal": "summary: |-\n  Resposta literal.",
            "bloco indentado": "summary: >2-\n  Resposta com indicador numerico.",
            "aspas multilinha": 'summary:\n  "Resposta entre aspas\n  quebrada em duas linhas."',
            "espaco antes de dois-pontos": 'summary : "Resposta com espaco antes do separador."',
            "chave com aspas duplas": '"summary": "Resposta com chave entre aspas duplas."',
            "chave com aspas simples": "'summary' : 'Resposta com chave entre aspas simples.'",
            "aspas duplas escapadas": 'summary: "\\"Resposta\\" entre aspas escapadas."',
            "misto nUll e string no YAML 1.2": "summary: nUll",
            "indentado nUll e string no YAML 1.2": "summary:\n  nUll",
            "null entre aspas e string": 'summary: "null"',
        }
        for name, fm_line in yaml_with.items():
            with self.subTest(case=name):
                self.setUp()
                (self.posts / "ml.md").write_text(
                    f"---\ntitle: Teste\ndescription: Desc\nslug: ml\n{fm_line}\n---\ncorpo\n", encoding="utf-8"
                )
                self.w("blog/ml/index.html", page(self.SUMMARY_HTML))
                self.assertEqual(C.check_summary(self.site, self.posts), [], name)
                self.w("blog/ml/index.html", page('<div class="post-body"><p>sem bloco</p></div>'))
                self.assertTrue(C.check_summary(self.site, self.posts), name)

        yaml_without = {
            "chave vazia": "summary:",
            "so comentario": "summary: # nada\n  # ainda nada",
            "aspas vazias": 'summary: ""',
            "null": "summary: null",
            "Null": "summary: Null",
            "NULL": "summary: NULL",
            "til": "summary: ~",
            "bloco vazio": "summary: >\n   ",
            "indentado null": "summary:\n  null",
            "indentado Null": "summary:\n  Null",
            "indentado NULL": "summary:\n  NULL",
            "indentado til com comentario": "summary:\n  ~ # vazio",
        }
        for name, fm_line in yaml_without.items():
            with self.subTest(empty=name):
                self.setUp()
                (self.posts / "vazio.md").write_text(
                    f"---\ntitle: Teste\ndescription: Desc\nslug: vazio\n{fm_line}\n---\ncorpo\n", encoding="utf-8"
                )
                self.w("blog/vazio/index.html", page('<div class="post-body"><p>texto</p></div>'))
                self.assertEqual(C.check_summary(self.site, self.posts), [], name)

    def test_missing_or_misplaced_or_inert_summary_fails(self):
        cases = {
            "ausente": '<div class="post-body"><p>texto</p></div>',
            "depois do body": '<div class="post-body"><p>x</p></div>' + self.SUMMARY_HTML.split("<div")[0],
            "dentro do body": '<div class="post-body">' + self.SUMMARY_HTML.split("<div")[0] + "</div>",
            "sem rotulo": '<section class="post-summary"><p>So o texto.</p></section><div class="post-body"></div>',
            "sem texto": '<section class="post-summary"><p class="post-summary-label">Em resumo</p></section><div class="post-body"></div>',
            "texto so em template": '<section class="post-summary"><p class="post-summary-label">Em resumo</p><template><p>Oculto</p></template></section><div class="post-body"></div>',
            "em template": '<template>' + self.SUMMARY_HTML.split("<div")[0] + '</template><div class="post-body"></div>',
            "hidden": self.SUMMARY_HTML.replace('class="post-summary"', 'class="post-summary" hidden'),
            "duplicado": self.SUMMARY_HTML.split("<div")[0] * 2 + '<div class="post-body"></div>',
        }
        for name, body in cases.items():
            with self.subTest(case=name):
                self.setUp()
                self.write_post("com-resumo", "Resposta direta.", body)
                self.assertTrue(C.check_summary(self.site, self.posts), name)
        # .post-body dentro de <template> antes do summary é inerte e não conta como .post-body real.
        self.setUp()
        self.write_post("com-resumo", "Resposta direta.", '<template><div class="post-body"></div></template>' + self.SUMMARY_HTML)
        self.assertEqual(C.check_summary(self.site, self.posts), [])

    def test_main_runs_check_summary(self):
        consts = Path(self.tmp.name) / "consts.ts"
        consts.write_text("export const PRE_CUTOVER_NOINDEX = false;\n", encoding="utf-8")
        self.write_post("com-resumo", "Resposta direta.", '<div class="post-body"><p>sem bloco</p></div>')
        err = io.StringIO()
        with isolate_main_rule("check_summary"), \
                contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = C.main(["--site", str(self.site), "--posts", str(self.posts), "--consts", str(consts)])
        self.assertEqual(code, 1)
        self.assertIn("post-summary", err.getvalue())

    def test_post_without_summary_cannot_render_block(self):
        self.write_post("sem-resumo", None, self.SUMMARY_HTML)
        got = C.check_summary(self.site, self.posts)
        self.assertTrue(any("sem summary" in p for p in got), got)

    def test_blog_summary_contrast_in_light_and_dark(self):
        repo = Path(__file__).resolve().parent.parent
        site_css = (repo / "assets/site.css").read_text(encoding="utf-8")
        blog_css = (repo / "blog-src/src/styles/blog.css").read_text(encoding="utf-8")
        self.assertEqual(C.contrast_problems([site_css, blog_css]), [])
        # Confere também .post-summary-label (cor herdando fundo --tag-bg de .post-summary) nos dois temas.
        extra = ".post-summary-label{background:var(--tag-bg)}"
        self.assertEqual(C.contrast_problems([site_css, blog_css, extra]), [])


class LlmsTxtTest(Base):
    """#174: /llms.txt precisa existir no _site, seguir o formato e apontar só para páginas existentes e indexáveis."""

    VALID_LLMS = (
        "# Guilherme Favaron\n\n"
        "> Site oficial de Guilherme Favaron.\n\n"
        "Abertura curta sobre o site.\n\n"
        "## Consultoria\n\n"
        "- [Consultoria](https://guifav.dev/consultoria/): Consultoria em IA.\n\n"
        "## Ferramentas\n\n"
        "- [Ferramentas](https://guifav.dev/ferramentas/): Lista de ferramentas.\n\n"
        "## Temas\n\n"
        "- [Tema](https://guifav.dev/blog/tema/ia-sobre-dados-da-empresa/): Artigos sobre dados.\n"
        "  - [Um post](https://guifav.dev/blog/um-post/): Resumo do post.\n\n"
        "## Portfólio e Expert Insights\n\n"
        "- [Portfólio](https://guifav.dev/portfolio/): Projetos.\n"
        "- [Insights](https://guifav.dev/blog/): Artigos.\n"
    )

    def setUp(self):
        super().setUp()
        self.w("consultoria/index.html", page("c", robots=""))
        self.w("ferramentas/index.html", page("f", robots=""))
        self.w("ferramentas/enviado/index.html", page("e", robots=ROBOTS_NOINDEX))
        self.w("blog/index.html", page("b", robots=""))
        self.w("blog/um-post/index.html", page("p", robots=""))
        self.w("blog/tema/ia-sobre-dados-da-empresa/index.html", page("t", robots=""))
        self.w("blog/assinar/index.html", page("a", robots=ROBOTS_NOINDEX))
        self.w("llms.txt", self.VALID_LLMS)

    def test_valid_llms_txt_passes(self):
        self.assertEqual(C.check_llms_txt(self.site, self.posts), [])

    def test_missing_empty_or_symlink_llms_txt_fails(self):
        (self.site / "llms.txt").unlink()
        got = C.check_llms_txt(self.site, self.posts)
        self.assertTrue(any("llms.txt" in p and "ausente" in p for p in got), got)

        self.w("llms.txt", "   \n")
        got = C.check_llms_txt(self.site, self.posts)
        self.assertTrue(any("llms.txt" in p for p in got), got)

        target = Path(self.tmp.name) / "fora.txt"
        target.write_text(self.VALID_LLMS, encoding="utf-8")
        (self.site / "llms.txt").unlink()
        os.symlink(target, self.site / "llms.txt")
        got = C.check_llms_txt(self.site, self.posts)
        self.assertTrue(any("symlink" in p or "simbólico" in p for p in got), got)

    def test_missing_structure_or_required_section_fails(self):
        cases = {
            "sem H1": self.VALID_LLMS.replace("# Guilherme Favaron\n", ""),
            "sem citacao": self.VALID_LLMS.replace("> Site oficial de Guilherme Favaron.\n", ""),
            "sem secao Consultoria": self.VALID_LLMS.replace("## Consultoria\n\n- [Consultoria](https://guifav.dev/consultoria/): Consultoria em IA.\n\n", ""),
            "secao vazia": self.VALID_LLMS.replace("- [Consultoria](https://guifav.dev/consultoria/): Consultoria em IA.\n", ""),
        }
        for name, content in cases.items():
            with self.subTest(case=name):
                self.w("llms.txt", content)
                self.assertTrue(C.check_llms_txt(self.site, self.posts), name)

    def test_broken_page_or_dir_without_slash_fails(self):
        for bad_url in ("https://guifav.dev/nao-existe/", "https://guifav.dev/consultoria", "https://guifav.dev/assets/a.png"):
            with self.subTest(url=bad_url):
                self.w("llms.txt", self.VALID_LLMS + f"- [Ruim]({bad_url}): Quebrado.\n")
                got = C.check_llms_txt(self.site, self.posts)
                self.assertTrue(got, bad_url)

    def test_noindex_page_in_llms_txt_fails(self):
        # Página estática com noindex e página privada /blog/assinar/ com noindex.
        for bad_url in ("https://guifav.dev/ferramentas/enviado/", "https://guifav.dev/blog/assinar/"):
            with self.subTest(url=bad_url):
                self.w("llms.txt", self.VALID_LLMS + f"- [Noindex]({bad_url}): Não pode entrar.\n")
                got = C.check_llms_txt(self.site, self.posts)
                self.assertTrue(any("noindex" in p or "privada" in p for p in got), got)

    def test_index_false_or_podcast_post_in_llms_txt_fails(self):
        # Mesmo se o HTML perdeu a meta noindex, o frontmatter index: false ou type: podcast barra o link.
        (self.posts / "podado.md").write_text("---\ntitle: p\nslug: podado\nindex: false\n---\n", encoding="utf-8")
        self.w("blog/podado/index.html", page("podado", robots=""))
        (self.posts / "podcast.md").write_text("---\ntitle: ep\nslug: podcast\ntype: podcast\nindex: true\n---\n", encoding="utf-8")
        self.w("blog/podcast/index.html", page("podcast", robots=""))
        for slug in ("podado", "podcast"):
            with self.subTest(slug=slug):
                self.w("llms.txt", self.VALID_LLMS + f"- [Post](https://guifav.dev/blog/{slug}/): Não pode.\n")
                got = C.check_llms_txt(self.site, self.posts)
                self.assertTrue(got, slug)

    def test_robots_blocked_404_external_canonical_or_old_domain_fails(self):
        self.w("portfolio/mockups/a.html", page("m", robots=""))
        self.w("robots.txt", "User-agent: *\nDisallow: /portfolio/mockups/\nSitemap: https://guifav.dev/sitemap.xml\n")
        self.w("outra/index.html", '<!DOCTYPE html><html><head><link rel="canonical" href="https://guifav.dev/portfolio/"></head><body>x</body></html>')
        self.w("blog/pagina/2/index.html", page("pag2", robots=""))
        for bad_line in (
            "- [Mockup](https://guifav.dev/portfolio/mockups/a.html): Bloqueado.\n",
            "- [404](https://guifav.dev/404.html): Erro.\n",
            "- [Canonical](https://guifav.dev/outra/): Outro canonical.\n",
            "- [Paginacao](https://guifav.dev/blog/pagina/2/): Pagina 2.\n",
            "- [Antigo](https://guilhermefavaron.com.br/p/x): Dominio antigo.\n",
            "- [HTTP](http://guifav.dev/portfolio/): Sem HTTPS.\n",
            "- [Relativo](/portfolio/): URL relativa não permitida.\n",
            "- [Titulo ): falso](https://guifav.dev/portfolio/)\n",
        ):
            with self.subTest(line=bad_line):
                self.w("llms.txt", self.VALID_LLMS + bad_line)
                self.assertTrue(C.check_llms_txt(self.site, self.posts), bad_line)

    def test_main_runs_check_llms_txt(self):
        consts = Path(self.tmp.name) / "consts.ts"
        consts.write_text("export const PRE_CUTOVER_NOINDEX = false;\n", encoding="utf-8")
        (self.site / "llms.txt").unlink()
        err = io.StringIO()
        with isolate_main_rule("check_llms_txt"), \
                contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = C.main(["--site", str(self.site), "--posts", str(self.posts), "--consts", str(consts)])
        self.assertEqual(code, 1)
        self.assertIn("llms.txt", err.getvalue())


class SeriesTest(unittest.TestCase):
    """#106: pares consecutivos (A, B) de cada série precisam de links mútuos dentro de div.post-body."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.site = Path(self.tmp.name) / "_site"
        self.site.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def write_post(self, slug: str, body_html: str, related_html: str = "") -> None:
        p = self.site / "blog" / slug / "index.html"
        p.parent.mkdir(parents=True, exist_ok=True)
        html = (
            "<!DOCTYPE html><html><head><title>Post</title></head><body><main><article>"
            f'<div class="post-body">{body_html}</div>'
            f"{related_html}"
            "</article></main></body></html>"
        )
        p.write_text(html, encoding="utf-8")

    def test_series_with_all_links_passes(self):
        series = (("a", "b", "c"),)
        self.write_post("a", '<p><a href="/blog/b/">proxima</a></p>')
        self.write_post("b", '<div><a href="https://guifav.dev/blog/a/">anterior</a></div>'
                             '<p><a href="/blog/c/#secao">proxima</a></p>')
        self.write_post("c", '<p><a href="../b/">anterior</a></p>')
        self.assertEqual(C.check_series(self.site, series=series), [])

    def test_missing_next_link_fails(self):
        series = (("a", "b"),)
        self.write_post("a", "<p>sem link para a seguinte</p>")
        self.write_post("b", '<p><a href="/blog/a/">anterior</a></p>')
        got = C.check_series(self.site, series=series)
        self.assertEqual(len(got), 1, got)
        self.assertIn("blog/a/index.html", got[0])
        self.assertIn("/blog/b/", got[0])

    def test_next_link_only_in_related_aside_or_outside_post_body_fails(self):
        series = (("a", "b"),)
        related = '<aside class="related"><ul><li><a href="/blog/b/">B</a></li></ul></aside>'
        self.write_post("a", "<p>corpo sem link</p>", related_html=related)
        self.write_post("b", '<p><a href="/blog/a/">anterior</a></p>')
        got = C.check_series(self.site, series=series)
        self.assertEqual(len(got), 1, got)
        self.assertIn("blog/a/index.html", got[0])
        self.assertIn("/blog/b/", got[0])

        # Link depois que div.post-body fecha também não conta.
        self.write_post("a", "<p>corpo sem link</p>", related_html='<div><a href="/blog/b/">fora</a></div>')
        got_outside = C.check_series(self.site, series=series)
        self.assertEqual(len(got_outside), 1, got_outside)
        self.assertIn("blog/a/index.html", got_outside[0])

    def test_missing_prev_link_fails(self):
        series = (("a", "b"),)
        self.write_post("a", '<p><a href="/blog/b/">proxima</a></p>')
        related = '<aside class="related"><ul><li><a href="/blog/a/">A</a></li></ul></aside>'
        self.write_post("b", "<p>sem link para a anterior</p>", related_html=related)
        got = C.check_series(self.site, series=series)
        self.assertEqual(len(got), 1, got)
        self.assertIn("blog/b/index.html", got[0])
        self.assertIn("/blog/a/", got[0])

    def test_missing_series_page_fails(self):
        series = (("a", "b"),)
        self.write_post("a", '<p><a href="/blog/b/">proxima</a></p>')
        got = C.check_series(self.site, series=series)
        self.assertEqual(len(got), 1, got)
        self.assertIn("blog/b/index.html", got[0])
        self.assertIn("ausente", got[0])

    def test_series_constant_and_main_wiring(self):
        self.assertEqual(len(C.SERIES), 3)
        consts = Path(self.tmp.name) / "consts.ts"
        consts.write_text("export const PRE_CUTOVER_NOINDEX = false;\n", encoding="utf-8")
        err = io.StringIO()
        with isolate_main_rule("check_series"), \
                contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = C.main(["--site", str(self.site), "--posts", str(self.tmp.name), "--consts", str(consts)])
        self.assertEqual(code, 1)
        self.assertIn("blog/rag-llm-ai-o-que-e/index.html", err.getvalue())

        for slugs in C.SERIES:
            for i, slug in enumerate(slugs):
                links = []
                if i > 0:
                    links.append(f'<a href="/blog/{slugs[i - 1]}/">ant</a>')
                if i + 1 < len(slugs):
                    links.append(f'<a href="/blog/{slugs[i + 1]}/">prox</a>')
                self.write_post(slug, "<p>" + " ".join(links) + "</p>")
        with isolate_main_rule("check_series"), \
                contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(C.main(["--site", str(self.site), "--posts", str(self.tmp.name), "--consts", str(consts)]), 0)


class BookTest(Base):
    """#183: página do livro em /materiais/ com @graph (WebPage + Book gratuito, workExample PDF e Kindle, sem offers/FAQPage)."""

    REPO = Path(__file__).resolve().parent.parent

    def valid_graph(self) -> dict:
        book_url = C.SITE_ORIGIN + C.BOOK_URL
        book_id = book_url + "#book"
        return {
            "@context": "https://schema.org",
            "@graph": [
                {
                    "@type": "WebPage",
                    "@id": book_url + "#webpage",
                    "url": book_url,
                    "isPartOf": {"@id": C.SITE_ORIGIN + "/#website"},
                    "about": {"@id": book_id},
                    "mainEntity": {"@id": book_id},
                },
                {
                    "@type": "Book",
                    "@id": book_id,
                    "name": "Desbloqueando a Inteligência Artificial",
                    "author": {"@id": C.PERSON_ID},
                    "publisher": {"@id": C.PERSON_ID},
                    "isAccessibleForFree": True,
                    "workExample": [
                        {
                            "@type": "Book",
                            "bookFormat": "https://schema.org/EBook",
                            "encodingFormat": "application/pdf",
                            "url": book_url + "#baixar",
                            "isAccessibleForFree": True,
                        },
                        {
                            "@type": "Book",
                            "bookFormat": "https://schema.org/EBook",
                            "url": C.BOOK_AMAZON_URL,
                            "identifier": {"@type": "PropertyValue", "propertyID": "ASIN", "value": C.BOOK_ASIN},
                        },
                    ],
                },
            ],
        }

    def write_book(self, ld: dict | list | None, extra_head: str = "") -> None:
        script = "" if ld is None else f'<script type="application/ld+json">{json.dumps(ld)}</script>'
        self.w(C.BOOK_PAGE, f'<!DOCTYPE html><html><head>{script}{extra_head}</head><body>x</body></html>')

    def test_valid_book_graph_passes(self):
        self.write_book(self.valid_graph())
        self.assertEqual(C.check_book(self.site), [])

    def test_missing_page_or_book_or_graph_fails(self):
        self.assertTrue(any("sumiu" in p for p in C.check_book(self.site)))
        self.write_book(None)
        self.assertTrue(any("Book" in p for p in C.check_book(self.site)))
        # Book fora de @graph falha.
        solo = self.valid_graph()["@graph"][1]
        solo["@context"] = "https://schema.org"
        self.write_book(solo)
        self.assertTrue(any("@graph" in p for p in C.check_book(self.site)))
        # JSON-LD comentado ou dentro de <template> não conta.
        raw = json.dumps(self.valid_graph())
        for inert in (f'<!-- <script type="application/ld+json">{raw}</script> -->',
                      f'<template><script type="application/ld+json">{raw}</script></template>'):
            with self.subTest(inert=inert[:20]):
                self.w(C.BOOK_PAGE, f'<!DOCTYPE html><html><head>{inert}</head><body>x</body></html>')
                self.assertTrue(C.check_book(self.site))

    def test_invalid_book_fields_offers_or_faqpage_fail(self):
        def mutate(fn):
            g = self.valid_graph()
            fn(g["@graph"][0], g["@graph"][1], g["@graph"])
            return g

        cases = {
            "offers no Book": mutate(lambda wp, b, g: b.__setitem__("offers", {"@type": "Offer", "price": "0"})),
            "offers no workExample": mutate(lambda wp, b, g: b["workExample"][0].__setitem__("offers", {"@type": "Offer"})),
            "no Offer solto no graph": mutate(lambda wp, b, g: g.append({"@type": "Offer", "price": "0"})),
            "isAccessibleForFree false": mutate(lambda wp, b, g: b.__setitem__("isAccessibleForFree", False)),
            "isAccessibleForFree string": mutate(lambda wp, b, g: b.__setitem__("isAccessibleForFree", "true")),
            "sem isAccessibleForFree": mutate(lambda wp, b, g: b.pop("isAccessibleForFree")),
            "author errado": mutate(lambda wp, b, g: b.__setitem__("author", {"@type": "Person", "name": "Guilherme"})),
            "publisher errado": mutate(lambda wp, b, g: b.__setitem__("publisher", {"@id": "https://example.com/#p"})),
            "sem PDF": mutate(lambda wp, b, g: b.__setitem__("workExample", [b["workExample"][1]])),
            "PDF url sem ancora": mutate(lambda wp, b, g: b["workExample"][0].__setitem__("url", C.SITE_ORIGIN + C.BOOK_URL)),
            "PDF nao gratuito": mutate(lambda wp, b, g: b["workExample"][0].__setitem__("isAccessibleForFree", False)),
            "sem Kindle": mutate(lambda wp, b, g: b.__setitem__("workExample", [b["workExample"][0]])),
            "ASIN errado": mutate(lambda wp, b, g: b["workExample"][1]["identifier"].__setitem__("value", "B000000000")),
            "URL Amazon errada": mutate(lambda wp, b, g: b["workExample"][1].__setitem__("url", "https://amazon.com/dp/x")),
            "WebPage sem mainEntity": mutate(lambda wp, b, g: wp.pop("mainEntity")),
            "WebPage sem about": mutate(lambda wp, b, g: wp.pop("about")),
            "WebPage isPartOf errado": mutate(lambda wp, b, g: wp.__setitem__("isPartOf", {"@id": "https://guifav.dev/"})),
            "id do Book errado": mutate(lambda wp, b, g: b.__setitem__("@id", "https://guifav.dev/#book")),
            "FAQPage no graph": mutate(lambda wp, b, g: g.append({"@type": "FAQPage", "mainEntity": []})),
        }
        for name, doc in cases.items():
            with self.subTest(case=name):
                self.write_book(doc)
                self.assertTrue(C.check_book(self.site), name)

    def test_main_runs_check_book(self):
        consts = Path(self.tmp.name) / "consts.ts"
        consts.write_text("export const PRE_CUTOVER_NOINDEX = false;\n", encoding="utf-8")
        err = io.StringIO()
        with isolate_main_rule("check_book"), \
                contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = C.main(["--site", str(self.site), "--posts", str(self.posts), "--consts", str(consts)])
        self.assertEqual(code, 1)
        self.assertIn(C.BOOK_PAGE, err.getvalue())

    def test_repo_book_pages_pass_all_static_rules(self):
        book_html = (self.REPO / C.BOOK_PAGE).read_text(encoding="utf-8")
        sent_rel = f"materiais/{C.BOOK_ID}/enviado/index.html"
        sent_html = (self.REPO / sent_rel).read_text(encoding="utf-8")
        self.assertEqual(C.check_book(self.REPO), [])
        for rel, html in ((C.BOOK_PAGE, book_html), (sent_rel, sent_html)):
            with self.subTest(rel=rel):
                self.assertEqual(C.check_titles_and_groups(rel, html), [])
                self.assertEqual(C.check_tracking(rel, html), [])
                self.assertEqual(C.check_ga_host_guard(rel, html), [])
                self.assertEqual(C.check_consent(rel, html), [])
                self.assertEqual(C.check_legal_footer(rel, html), [])
                self.assertEqual(C.check_courses_footer(rel, html), [])
                self.assertEqual(C.check_youtube_footer(rel, html), [])
                self.assertNotIn("\u2014", html)
                self.assertNotIn("\u2013", html)


class PriorityTest(unittest.TestCase):
    """#197: /prioridade/ existe com noindex, canonical próprio, título, H1, formulário com Turnstile e os 5 fragmentos, fora do sitemap."""

    GA = TrackingTest.GA
    FOOT = ('<footer><p><a href="/cursos/">Cursos</a><a href="/privacidade/">Privacidade</a>'
            '<a href="/termos/">Termos</a><a href="/privacidade/#cookies" data-consent-open>Cookies</a></p></footer>')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.site = Path(self.tmp.name) / "_site"
        self.site.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def w(self, rel: str, content: str) -> None:
        p = self.site / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    DEFAULT_FOCUS_SCRIPT = (
        "<script>(function(){function f(){var id=location.hash.slice(1);if(!id)return;"
        "var el=document.getElementById(id);if(el&&el.classList.contains('form-msg'))el.focus();}"
        "f();window.addEventListener('hashchange',f);})();</script>"
    )

    def priority_doc(self, *, title: str = C.PRIORITY_TITLE, h1: str = C.PRIORITY_H1,
                     robots: str = ROBOTS_NOINDEX, canonical: str | None = "https://guifav.dev/prioridade/",
                     frags: tuple[str, ...] = C.PRIORITY_FRAGMENTS,
                     msg_tabindex: str | None = "-1",
                     privacy_link: str = '<a href="/privacidade/#prioridade">Política de privacidade</a>',
                     turnstile_script: str = f'<script async defer src="{C.TURNSTILE_SCRIPT_SRC}"></script>',
                     focus_script: str | None = None,
                     form: str | None = None) -> str:
        c = "" if canonical is None else f'<link rel="canonical" href="{canonical}">'
        ti = "" if msg_tabindex is None else f' tabindex="{msg_tabindex}"'
        msgs = "".join(f'<p id="{fid}" class="form-msg"{ti}>{fid}</p>' for fid in frags)
        default_form = (
            f'<form data-site-form data-clarity-mask="true" method="post" action="{C.PRIORITY_ACTION}">'
            '<input type="email" name="email" required>'
            '<button type="submit">Não quero ser pontuado</button>'
            f'<div class="cf-turnstile" data-sitekey="{C.TURNSTILE_SITE_KEY}"></div>'
            "</form>"
        )
        fs = self.DEFAULT_FOCUS_SCRIPT if focus_script is None else focus_script
        return (f'<!DOCTYPE html><html lang="pt-BR"><head><title>{title}</title>{robots}{c}{turnstile_script}{self.GA}</head>'
                f'<body><main><h1>{h1}</h1><p>{privacy_link}</p>{msgs}{default_form if form is None else form}</main>{self.FOOT}{fs}</body></html>')

    def build_valid_site(self) -> None:
        self.w(C.PRIORITY_PAGE, self.priority_doc())
        self.w("sitemap-site.xml",
               '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
               '<url><loc>https://guifav.dev/</loc><lastmod>2026-11-27T12:00:00-03:00</lastmod></url>'
               "</urlset>")

    def test_valid_priority_page_passes(self):
        self.build_valid_site()
        self.assertEqual(C.check_priority(self.site), [])

    def test_missing_page_or_listed_in_sitemap_fails(self):
        self.build_valid_site()
        (self.site / C.PRIORITY_PAGE).unlink()
        self.assertTrue(any(C.PRIORITY_PAGE in p and "ausente" in p for p in C.check_priority(self.site)))

        self.build_valid_site()
        self.w("sitemap-site.xml",
               '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
               '<url><loc>https://guifav.dev/prioridade/</loc><lastmod>2026-11-27T12:00:00-03:00</lastmod></url>'
               "</urlset>")
        self.assertTrue(any("sitemap-site.xml" in p and "/prioridade/" in p for p in C.check_priority(self.site)))

    def test_page_requirements(self):
        key = C.TURNSTILE_SITE_KEY
        for name, kw, needle in (
            ("sem noindex", {"robots": ROBOTS_INDEX}, "noindex"),
            ("sem canonical", {"canonical": None}, "canonical"),
            ("canonical errado", {"canonical": "https://guifav.dev/prioridade"}, "canonical"),
            ("título errado", {"title": "Prioridade | Guilherme Favaron"}, "o título precisa ser"),
            ("H1 errado", {"h1": "Outro"}, "H1"),
            ("sem link privacidade", {"privacy_link": ""}, "/privacidade/#prioridade"),
            ("falta #pronto", {"frags": ("erro-verificacao", "erro-email", "erro-limite", "erro")}, "#pronto"),
            ("falta #erro-limite", {"frags": ("pronto", "erro-verificacao", "erro-email", "erro")}, "#erro-limite"),
            ("mensagens sem tabindex=-1", {"msg_tabindex": None}, 'tabindex="-1"'),
            ("mensagens com tabindex=0", {"msg_tabindex": "0"}, 'tabindex="-1"'),
            ("sem script de foco", {"focus_script": ""}, "script de foco"),
            ("script de foco comentado em HTML", {"focus_script": f"<!-- {self.DEFAULT_FOCUS_SCRIPT} -->"}, "script de foco"),
            ("script de foco só em string", {"focus_script": '<script>var s = "location.hash .focus(";</script>'}, "script de foco"),
            ("script de foco só em string simples e template", {"focus_script": "<script>var a = 'location.hash', b = `.focus(`;</script>"}, "script de foco"),
            ("script de foco só em comentário //", {"focus_script": "<script>// location.hash .focus(</script>"}, "script de foco"),
            ("script de foco só em comentário /* */", {"focus_script": "<script>/* location.hash .focus( */</script>"}, "script de foco"),
            ("sem form", {"form": ""}, C.PRIORITY_ACTION),
            ("action fora da tag form", {"form": f'<form data-clarity-mask="true" method="post" action="https://outro.example/"><p action="{C.PRIORITY_ACTION}"></p><input type="email" name="email"><div class="cf-turnstile" data-sitekey="{key}"></div></form>'}, C.PRIORITY_ACTION),
            ("method get", {"form": f'<form data-clarity-mask="true" method="get" action="{C.PRIORITY_ACTION}"><input type="email" name="email"><div class="cf-turnstile" data-sitekey="{key}"></div></form>'}, 'method="post"'),
            ("sem method", {"form": f'<form data-clarity-mask="true" action="{C.PRIORITY_ACTION}"><input type="email" name="email"><div class="cf-turnstile" data-sitekey="{key}"></div></form>'}, 'method="post"'),
            ("sem clarity mask", {"form": f'<form method="post" action="{C.PRIORITY_ACTION}"><input type="email" name="email"><div class="cf-turnstile" data-sitekey="{key}"></div></form>'}, "data-clarity-mask"),
            ("clarity mask fora da tag form", {"form": f'<form method="post" action="{C.PRIORITY_ACTION}"><input type="email" name="email"><p data-clarity-mask="true">nota</p><div class="cf-turnstile" data-sitekey="{key}"></div></form>'}, "data-clarity-mask"),
            ("sem campo email", {"form": f'<form data-clarity-mask="true" method="post" action="{C.PRIORITY_ACTION}"><div class="cf-turnstile" data-sitekey="{key}"></div></form>'}, "campo de email"),
            ("input com name diferente", {"form": f'<form data-clarity-mask="true" method="post" action="{C.PRIORITY_ACTION}"><input type="email" name="endereco"><div class="cf-turnstile" data-sitekey="{key}"></div></form>'}, "campo de email"),
            ("input com type text", {"form": f'<form data-clarity-mask="true" method="post" action="{C.PRIORITY_ACTION}"><input type="text" name="email"><div class="cf-turnstile" data-sitekey="{key}"></div></form>'}, "campo de email"),
            ("input email fora do form", {"form": f'<input type="email" name="email"><form data-clarity-mask="true" method="post" action="{C.PRIORITY_ACTION}"><div class="cf-turnstile" data-sitekey="{key}"></div></form>'}, "campo de email"),
            ("sem turnstile", {"form": f'<form data-clarity-mask="true" method="post" action="{C.PRIORITY_ACTION}"><input type="email" name="email"></form>'}, "Turnstile"),
            ("turnstile sem data-sitekey", {"form": f'<form data-clarity-mask="true" method="post" action="{C.PRIORITY_ACTION}"><input type="email" name="email"><div class="cf-turnstile"></div></form>'}, "Turnstile"),
            ("turnstile sitekey vazio", {"form": f'<form data-clarity-mask="true" method="post" action="{C.PRIORITY_ACTION}"><input type="email" name="email"><div class="cf-turnstile" data-sitekey=""></div></form>'}, "Turnstile"),
            ("turnstile sitekey errado", {"form": f'<form data-clarity-mask="true" method="post" action="{C.PRIORITY_ACTION}"><input type="email" name="email"><div class="cf-turnstile" data-sitekey="0x4AAAAAAF00000000000000"></div></form>'}, "Turnstile"),
            ("sem script do turnstile", {"turnstile_script": ""}, C.TURNSTILE_SCRIPT_SRC),
            ("script do turnstile comentado", {"turnstile_script": f'<!-- <script src="{C.TURNSTILE_SCRIPT_SRC}"></script> -->'}, C.TURNSTILE_SCRIPT_SRC),
        ):
            with self.subTest(case=name):
                self.build_valid_site()
                self.w(C.PRIORITY_PAGE, self.priority_doc(**kw))
                got = C.check_priority(self.site)
                self.assertTrue(any(needle in p for p in got), got)

    def test_fields_after_closing_form_fail(self):
        # Ao fechar </form>, _PriorityPage zera active_form: campo de email e Turnstile depois do fechamento não contam.
        key = C.TURNSTILE_SITE_KEY
        email = '<input type="email" name="email" required>'
        turnstile = f'<div class="cf-turnstile" data-sitekey="{key}"></div>'
        head = f'<form data-clarity-mask="true" method="post" action="{C.PRIORITY_ACTION}">'
        for name, form_html, needles in (
            ("email depois de </form>", f"{head}{turnstile}</form>{email}", ("campo de email",)),
            ("turnstile depois de </form>", f"{head}{email}</form>{turnstile}", ("Turnstile",)),
            ("email e turnstile depois de </form>", f"{head}</form>{email}{turnstile}", ("campo de email", "Turnstile")),
        ):
            with self.subTest(case=name):
                self.build_valid_site()
                self.w(C.PRIORITY_PAGE, self.priority_doc(form=form_html))
                got = C.check_priority(self.site)
                for needle in needles:
                    self.assertTrue(any(needle in p for p in got), got)

    def test_inert_fields_and_duplicate_forms_fail(self):
        key = C.TURNSTILE_SITE_KEY
        email = '<input type="email" name="email" required>'
        turnstile = f'<div class="cf-turnstile" data-sitekey="{key}"></div>'
        head = f'<form data-clarity-mask="true" method="post" action="{C.PRIORITY_ACTION}">'
        valid_form = f"{head}{email}{turnstile}</form>"
        for name, form_html, needle in (
            ("email em template", f"{head}<template>{email}</template>{turnstile}</form>", "campo de email"),
            ("email em noscript", f"{head}<noscript>{email}</noscript>{turnstile}</form>", "campo de email"),
            ("email em elemento hidden", f"{head}<div hidden>{email}</div>{turnstile}</form>", "campo de email"),
            ("email com proprio hidden", f'{head}<input type="email" name="email" hidden>{turnstile}</form>', "campo de email"),
            ("turnstile em template", f"{head}{email}<template>{turnstile}</template></form>", "Turnstile"),
            ("turnstile em noscript", f"{head}{email}<noscript>{turnstile}</noscript></form>", "Turnstile"),
            ("turnstile em elemento hidden", f"{head}{email}<div hidden>{turnstile}</div></form>", "Turnstile"),
            ("turnstile com proprio hidden", f'{head}{email}<div class="cf-turnstile" data-sitekey="{key}" hidden></div></form>', "Turnstile"),
            ("dois formularios com mesmo action", f"{valid_form}{valid_form}", "achados 2"),
        ):
            with self.subTest(case=name):
                self.build_valid_site()
                self.w(C.PRIORITY_PAGE, self.priority_doc(form=form_html))
                got = C.check_priority(self.site)
                self.assertTrue(any(needle in p for p in got), got)

    def test_inert_fragment_messages_fail(self):
        # Mensagens de fragmento em blocos inertes (template, noscript ou hidden) não contam em _PriorityPage.id_attrs.
        pronto = '<p id="pronto" class="form-msg" tabindex="-1">pronto</p>'
        pronto_hidden = '<p id="pronto" class="form-msg" tabindex="-1" hidden>pronto</p>'
        base = self.priority_doc()
        self.assertIn(pronto, base)
        for name, replacement in (
            ("em template", f"<template>{pronto}</template>"),
            ("em noscript", f"<noscript>{pronto}</noscript>"),
            ("em elemento hidden", f"<div hidden>{pronto}</div>"),
            ("com proprio hidden", pronto_hidden),
        ):
            with self.subTest(case=name):
                self.build_valid_site()
                self.w(C.PRIORITY_PAGE, base.replace(pronto, replacement, 1))
                got = C.check_priority(self.site)
                self.assertTrue(any("falta a mensagem #pronto" in p for p in got), got)

    def test_main_runs_the_priority_rule(self):
        consts = Path(self.tmp.name) / "consts.ts"
        consts.write_text("export const PRE_CUTOVER_NOINDEX = false;\n", encoding="utf-8")
        err = io.StringIO()
        with isolate_main_rule("check_priority"), \
                contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = C.main(["--site", str(self.site), "--posts", str(self.tmp.name), "--consts", str(consts)])
        self.assertEqual(code, 1)
        self.assertIn(C.PRIORITY_PAGE, err.getvalue())
        self.build_valid_site()
        with isolate_main_rule("check_priority"), \
                contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(C.main(["--site", str(self.site), "--posts", str(self.tmp.name), "--consts", str(consts)]), 0)


class PrioritySourcesTest(unittest.TestCase):
    """#197: fontes reais da página /prioridade/, manifesto e novas seções da política de privacidade."""

    REPO = Path(__file__).resolve().parent.parent

    def test_priority_page_in_manifest_and_passes_all_checks(self):
        manifest = [l.strip() for l in (self.REPO / "site-manifest.txt").read_text(encoding="utf-8").splitlines()
                    if l.strip() and not l.startswith("#")]
        self.assertIn("prioridade/", manifest)
        html = (self.REPO / C.PRIORITY_PAGE).read_text(encoding="utf-8")
        site_css = (self.REPO / "assets/site.css").read_text(encoding="utf-8")
        self.assertEqual(C.check_titles_and_groups(C.PRIORITY_PAGE, html), [])
        self.assertEqual(C.check_tracking(C.PRIORITY_PAGE, html), [])
        self.assertEqual(C.check_ga_host_guard(C.PRIORITY_PAGE, html), [])
        self.assertEqual(C.check_consent(C.PRIORITY_PAGE, html), [])
        self.assertEqual(C.check_legal_footer(C.PRIORITY_PAGE, html), [])
        self.assertEqual(C.check_courses_footer(C.PRIORITY_PAGE, html), [])
        self.assertEqual(C.check_youtube_footer(C.PRIORITY_PAGE, html), [])
        self.assertEqual(C.check_page_date_placeholder(C.PRIORITY_PAGE, html), [])
        self.assertIn(
            '<p id="pronto" class="form-msg" role="status" tabindex="-1">Pronto. A partir de agora você não é pontuado. '
            'Os registros no site foram apagados, e a nota no meu CRM sai na atualização diária seguinte.</p>',
            html,
        )
        consts = (self.REPO / "blog-src/src/consts.ts").read_text(encoding="utf-8")
        m = re.search(r"export const TURNSTILE_SITE_KEY = '([^']+)';", consts)
        self.assertIsNotNone(m)
        self.assertEqual(C.TURNSTILE_SITE_KEY, m.group(1))
        styles = re.findall(r"<style[^>]*>(.*?)</style>", html, flags=re.S | re.I)
        self.assertEqual(C.contrast_problems([site_css, *styles]), [])
        self.assertNotIn("\u2014", html)
        self.assertNotIn("\u2013", html)
        with tempfile.TemporaryDirectory() as d:
            site = Path(d)
            dst = site / C.PRIORITY_PAGE
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(html, encoding="utf-8")
            (site / "sitemap-site.xml").write_text(
                '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                '<url><loc>https://guifav.dev/</loc><lastmod>2026-11-27T12:00:00-03:00</lastmod></url>'
                "</urlset>", encoding="utf-8")
            self.assertEqual(C.check_priority(site), [])

    def test_priority_focus_script_semantics_in_node(self):
        # O script inline em prioridade/index.html foca a mensagem correspondente ao fragmento (ao carregar e em hashchange),
        # ignora fragmentos que não existem ou não são .form-msg e nunca lança erro sem fragmento.
        node = shutil.which("node")
        if not node:
            self.skipTest("node ausente")
        html = (self.REPO / C.PRIORITY_PAGE).read_text(encoding="utf-8")
        focus_scripts = [b for b in C._exec_scripts(html) if "location.hash" in b and ".focus(" in b]
        self.assertEqual(len(focus_scripts), 1)
        script_body = focus_scripts[0]
        frags = list(C.PRIORITY_FRAGMENTS)
        js = (
            f"const script = {json.dumps(script_body)};\n"
            f"const frags = {json.dumps(frags)};\n"
            "function run(initialHash, afterHash) {\n"
            "  const focused = [];\n"
            "  const listeners = {};\n"
            "  const elems = {};\n"
            "  for (const id of frags) {\n"
            "    elems[id] = { classList: { contains: (c) => c === 'form-msg' }, focus: () => focused.push(id) };\n"
            "  }\n"
            "  elems['oposicao'] = { classList: { contains: (c) => c === 'priority-form' }, focus: () => focused.push('oposicao') };\n"
            "  const location = { hash: initialHash };\n"
            "  const document = { getElementById: (id) => elems[id] || null };\n"
            "  const window = { addEventListener: (ev, fn) => { listeners[ev] = fn; } };\n"
            "  new Function('location', 'document', 'window', script)(location, document, window);\n"
            "  if (afterHash !== null) {\n"
            "    location.hash = afterHash;\n"
            "    if (listeners.hashchange) listeners.hashchange();\n"
            "  }\n"
            "  return focused;\n"
            "}\n"
            "const out = {};\n"
            "for (const id of frags) out[id] = run('#' + id, null);\n"
            "out.vazio = run('', null);\n"
            "out.inexistente = run('#nao-existe', null);\n"
            "out.form = run('#oposicao', null);\n"
            "out.hashchange = run('', '#erro-limite');\n"
            "console.log(JSON.stringify(out));\n"
        )
        got = json.loads(subprocess.run([node, "-e", js], capture_output=True, text=True, check=True).stdout)
        for fid in C.PRIORITY_FRAGMENTS:
            self.assertEqual(got[fid], [fid], fid)
        self.assertEqual(got["vazio"], [])
        self.assertEqual(got["inexistente"], [])
        self.assertEqual(got["form"], [])
        self.assertEqual(got["hashchange"], ["erro-limite"])

    def test_privacy_has_priority_courses_and_cut2_login_sections(self):
        t = (self.REPO / "privacidade/index.html").read_text(encoding="utf-8")
        self.assertEqual(C.check_page_date_placeholder("privacidade/index.html", t), [])
        for must in (
            '<h2 id="prioridade">Prioridade de contato</h2>',
            '<h2 id="cursos">Cursos</h2>',
            'href="/prioridade/"',
            "Google Identity Platform e Firestore",
            "as inscrições nos cursos e as notas de prioridade e de engajamento",
            "guifav.dev/cursos/",
            "Aberturas de email, sozinhas, nunca deixam alguém na faixa quente.",
            "Cada curso tem uma nota de engajamento própria, separada da prioridade de contato do site",
            "a home, os materiais para baixar, os artigos do blog e a página Não quero ser pontuado",
            "o registro de engajamento da prioridade de contato e as marcas de oposição",
            "Guardo o seu email, o nome que vem da conta Google, a data de entrada e as aulas concluídas num banco de "
            "dados do Google (Firestore). A abertura e o clique nos emails do curso ficam no registro de engajamento, "
            'no Cloudflare, como descrito em <a href="#prioridade">Prioridade de contato</a>. A inscrição no curso, '
            "com a nota de engajamento, fica no meu CRM, ligada ao seu contato.",
            # 11a: restrição aos emails no parágrafo do registro.
            "Para isso, guardo um registro com o seu email, o tipo de sinal, a data e de onde ele veio. Dos emails, "
            "não guardo o link em que você clicou, o que você leu nem o seu endereço IP.",
            # 11b: visitas logadas incluídas na lista "Entram na conta" antes de "Cada sinal vale pontos".
            "Entram na conta: a confirmação da inscrição nos Expert Insights, o pedido e o download de materiais e a "
            "abertura e o clique nos emails dos Expert Insights e dos materiais. Se você entrar no site com a sua conta "
            "Google e ligar a sua atividade ao seu contato, entram também as suas visitas ao site, como descrito abaixo. "
            "Cada sinal vale pontos",
            # 11c: parágrafo do corte 2 com consentimento separado da base legal do legítimo interesse.
            "Se você entrar no site com a sua conta Google, aceitar os cookies e confirmar o aviso do login, as suas "
            "visitas ficam ligadas ao seu contato: páginas vistas, tempo de leitura, rolagem e cliques em Agende e "
            "Contato. Essas visitas entram na prioridade de contato. O Google Analytics recebe um código da sua conta, "
            "nunca o seu email, e essas visitas não são usadas para anúncios. A base legal é o seu consentimento "
            "(Lei 13.709/2018, art. 7º, I). Para desligar, saia da conta, desligue no menu da conta ou clique em "
            "Cookies no rodapé. Você pode pedir que eu apague o que já foi registrado, inclusive no Google Analytics.",
            # 11d: Firebase Auth, GIS, gstatic, accounts.google.com, apis.google.com no clique, carregamento condicional e cookie g_state.
            "<strong>Google Identity Platform e Firestore</strong>: o login com a conta Google e o banco de dados com "
            "os dados do curso (inscrição e progresso). O login do site usa o Firebase Authentication e, para o login "
            "de um toque, o Google Identity Services. Eles só são carregados (de www.gstatic.com e "
            "accounts.google.com; no clique em Entrar, também de apis.google.com) depois que você responde ao aviso "
            "de cookies, se você já entrou antes neste navegador ou se clicar em Entrar; ao carregar, o Google recebe "
            "o endereço IP e o navegador usado (user agent). O login de um toque grava o cookie g_state, do Google, "
            "para lembrar se você fechou o convite e não insistir.",
            "Eu paro de calcular na hora e apago os registros no site, inclusive os dos cursos; as notas no meu CRM "
            "saem na atualização diária seguinte. Você continua recebendo os Expert Insights e o curso normalmente.",
        ):
            self.assertIn(must, t)
        # 11c: parágrafo do corte 2 vem DEPOIS do parágrafo da base legal do legítimo interesse e ANTES da oposição.
        legit = "A base legal é o meu legítimo interesse (Lei 13.709/2018, art. 7º, IX), com o teste de balanceamento registrado."
        self.assertLess(t.index("Para que serve:"), t.index(legit))
        self.assertLess(t.index(legit), t.index("confirmar o aviso do login"))
        self.assertLess(t.index("confirmar o aviso do login"), t.index("Se você não quiser ser pontuado"))
        self.assertNotIn("(de www.gstatic.com e accounts.google.com)", t)
        self.assertNotIn("veio. Não guardo o link em que você clicou", t)
        self.assertNotIn("o botão e o aviso do Google", t)
        self.assertNotIn("inscrição, progresso e abertura e clique nos emails do curso", t)
        self.assertNotIn("apago as notas e os registros", t)


class AuthCheckTest(unittest.TestCase):
    """#197 corte 2: track.js carrega auth.js com versão ?v= de 12 dígitos e 404/privacidade/termos/prioridade ficam sem One Tap."""

    REPO = Path(__file__).resolve().parent.parent

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.site = Path(self.tmp.name) / "_site"
        self.site.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def w(self, rel: str, content: str) -> None:
        f = self.site / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content, encoding="utf-8")

    def valid_site(self) -> None:
        self.w("assets/track.js", "var AUTH_SRC = '/assets/auth.js?v=202611271230';")
        self.w(
            "assets/auth.js",
            "var NO_ONE_TAP_PREFIXES = ['/privacidade', '/termos', '/prioridade', '/404'];\n"
            "sendGtag('consent', 'update', { ad_storage: 'denied', ad_user_data: 'denied', ad_personalization: 'denied' });\n"
            "sendGtag('consent', 'update', { ad_storage: 'granted', ad_user_data: 'granted', ad_personalization: 'granted' });\n",
        )
        self.w("404.html", "<!DOCTYPE html><html><body><h1>404</h1><script src='/assets/site.js?v=202611241500'></script></body></html>")
        for rel in ("privacidade/index.html", "termos/index.html", "prioridade/index.html"):
            self.w(rel, "<!DOCTYPE html><html><body><script src='/assets/track.js?v=202611271230'></script></body></html>")

    def test_valid_passes_and_main_wiring(self):
        self.valid_site()
        self.assertEqual(C.check_auth(self.site), [])
        consts = Path(self.tmp.name) / "consts.ts"
        consts.write_text("export const PRE_CUTOVER_NOINDEX = false;\n", encoding="utf-8")
        with isolate_main_rule("check_auth"):
            self.assertEqual(C.main(["--site", str(self.site), "--posts", str(self.tmp.name), "--consts", str(consts)]), 0)
        (self.site / "assets/auth.js").unlink()
        err = io.StringIO()
        with isolate_main_rule("check_auth"), contextlib.redirect_stderr(err):
            code = C.main(["--site", str(self.site), "--posts", str(self.tmp.name), "--consts", str(consts)])
        self.assertEqual(code, 1)
        self.assertIn("assets/auth.js: arquivo ausente", err.getvalue())

    def test_rejects_missing_version_missing_exclusion_or_one_tap_on_excluded_pages(self):
        self.valid_site()
        self.w("assets/track.js", "var AUTH_SRC = '/assets/auth.js';")
        self.assertTrue(any("?v= de 12 dígitos" in p for p in C.check_auth(self.site)))

        self.valid_site()
        self.w("assets/auth.js", "var NO_ONE_TAP_PREFIXES = ['/privacidade', '/termos'];")
        got = C.check_auth(self.site)
        self.assertTrue(any("/prioridade" in p for p in got))
        self.assertTrue(any("/404" in p for p in got))

        self.valid_site()
        self.w("assets/auth.js", "var NO_ONE_TAP_PREFIXES = ['/privacidade', '/termos', '/prioridade', '/404'];")
        got_ad = C.check_auth(self.site)
        self.assertTrue(any("negar ad_storage" in p for p in got_ad), got_ad)
        self.assertTrue(any("devolver ad_storage" in p for p in got_ad), got_ad)

        self.valid_site()
        self.w(
            "assets/auth.js",
            "var NO_ONE_TAP_PREFIXES = ['/privacidade', '/termos', '/prioridade', '/404'];\n"
            "// sendGtag('consent', 'update', { ad_storage: 'denied', ad_user_data: 'denied', ad_personalization: 'denied' });\n"
            "var x = \"sendGtag('consent','update',{ad_storage:'granted',ad_user_data:'granted',ad_personalization:'granted'})\";\n",
        )
        got_ad_fake = C.check_auth(self.site)
        self.assertTrue(any("negar ad_storage" in p for p in got_ad_fake), got_ad_fake)
        self.assertTrue(any("devolver ad_storage" in p for p in got_ad_fake), got_ad_fake)

        self.valid_site()
        self.w("404.html", "<!DOCTYPE html><html><body><script src='/assets/track.js?v=202611271230'></script></body></html>")
        self.assertTrue(any("404.html: página 404 não pode carregar" in p for p in C.check_auth(self.site)))

        for rel in ("404.html", "privacidade/index.html", "termos/index.html", "prioridade/index.html"):
            with self.subTest(rel=rel):
                self.valid_site()
                self.w(rel, "<!DOCTYPE html><html><body><script src='https://accounts.google.com/gsi/client'></script></body></html>")
                self.assertTrue(any(rel in p and "One Tap" in p for p in C.check_auth(self.site)))
                self.valid_site()
                self.w(rel, "<!DOCTYPE html><html><body><script src='/assets/auth.js?v=202611271230'></script></body></html>")
                self.assertTrue(any(rel in p and "/assets/auth.js" in p for p in C.check_auth(self.site)))

    def test_ga_uid_snippet_rule_and_all_repo_pages(self):
        ga_tag = '<script async src="https://www.googletagmanager.com/gtag/js?id=G-K7Y87QNCX8"></script>'
        uid_block = (
            "if(window.guifavConsent==='granted'){"
            "gtag('consent','update',{ad_storage:'granted',ad_user_data:'granted',ad_personalization:'granted',analytics_storage:'granted'});"
            "try{var k=JSON.parse(localStorage.getItem('heating-link-v1')||'null'),n=Date.now();"
            "if(k&&k.v==='granted'&&typeof k.uid==='string'&&/^[0-9A-Za-z]{1,128}$/.test(k.uid)&&typeof k.t==='number'&&n-k.t>=0&&n-k.t<31536e6){"
            "gtag('consent','update',{ad_storage:'denied',ad_user_data:'denied',ad_personalization:'denied'});"
            "gtag('set','allow_google_signals',false);"
            "gtag('set','allow_ad_personalization_signals',false);"
            "var u=JSON.parse(localStorage.getItem('heating-uid-v1')||'null');"
            "if(u&&u.uid===k.uid&&typeof u.t==='number'&&n-u.t>=0&&n-u.t<18e5)gtag('set',{user_id:u.uid});"
            "}}catch(e){}}"
        )
        cfg = "gtag('js',new Date());gtag('config','G-K7Y87QNCX8',{content_group:'home'});"
        self.assertEqual(C.check_ga_uid_snippet("index.html", f"<html><head>{ga_tag}<script>{uid_block}{cfg}</script></head></html>"), [])
        self.assertEqual(C.check_ga_uid_snippet("404.html", "<html><head></head></html>"), [])

        for name, js in {
            "sem bloco": cfg,
            "depois do config": cfg + uid_block,
            "uid com validade de 365 dias": uid_block.replace("n-u.t<18e5", "n-u.t<31536e6") + cfg,
            "anuncio negado so com o uid": uid_block.replace("k.v==='granted'&&", "") + cfg,
            "user_id sem conferir o uid do vinculo": uid_block.replace("u.uid===k.uid&&", "") + cfg,
            "sem consent update de anuncio": uid_block.replace("gtag('consent','update',{ad_storage:'denied',ad_user_data:'denied',ad_personalization:'denied'});", "") + cfg,
            "sem sinais de anuncio": uid_block.replace("gtag('set','allow_google_signals',false);", "") + cfg,
            "sem user_id": uid_block.replace("gtag('set',{user_id:u.uid});", "") + cfg,
            "comentado": f"/* {uid_block} */" + cfg,
            "em string": f'var x="{uid_block}";' + cfg,
        }.items():
            with self.subTest(name=name):
                got = C.check_ga_uid_snippet("index.html", f"<html><head>{ga_tag}<script>{js}</script></head></html>")
                self.assertEqual(len(got), 1, (name, got))

        # Todas as 15 páginas HTML raiz e Base.astro do repositório precisam passar na verificação exata.
        root_pages = (
            "index.html", "portfolio/index.html", C.CONSULTING_PAGE,
            *sorted(C.MATERIAL_PAGES), *sorted(C.TOOLS_PAGES),
            C.COURSES_PAGE, *C.LEGAL_PAGES, C.PRIORITY_PAGE,
        )
        self.assertEqual(len(root_pages), 15)
        for rel in root_pages:
            with self.subTest(rel=rel):
                self.assertEqual(C.check_ga_uid_snippet(rel, (self.REPO / rel).read_text(encoding="utf-8")), [])
        base_astro = (self.REPO / "blog-src/src/layouts/Base.astro").read_text(encoding="utf-8")
        # Simula a saída do Astro (GA4_ID resolvido na tag script async).
        base_rendered = base_astro.replace("src={`https://www.googletagmanager.com/gtag/js?id=${GA4_ID}`}",
                                           'src="https://www.googletagmanager.com/gtag/js?id=G-K7Y87QNCX8"')
        self.assertEqual(C.check_ga_uid_snippet("blog/index.html", base_rendered), [])

    def test_repo_sources_pass_check_auth(self):
        with tempfile.TemporaryDirectory() as d:
            site = Path(d)
            for rel in (
                "assets/track.js", "assets/auth.js", "404.html",
                "index.html", "portfolio/index.html", C.CONSULTING_PAGE,
                *sorted(C.MATERIAL_PAGES), *sorted(C.TOOLS_PAGES),
                C.COURSES_PAGE, *C.LEGAL_PAGES, C.PRIORITY_PAGE,
            ):
                dst = site / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_text((self.REPO / rel).read_text(encoding="utf-8"), encoding="utf-8")
            self.assertEqual(C.check_auth(site), [])


class DatePlaceholderTest(unittest.TestCase):
    """#197: guarda de build que recusa o marcador '[DATA]' em qualquer página HTML publicada."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.site = Path(self.tmp.name) / "_site"
        self.site.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def w(self, rel: str, content: str) -> None:
        p = self.site / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    def doc(self, body: str) -> str:
        return f"<!DOCTYPE html><html><head></head><body>{body}</body></html>"

    def test_clean_site_passes(self):
        self.w("index.html", self.doc('<p data-theme="dark">Atualizada em 27 de novembro de 2026.</p>'))
        self.w("privacidade/index.html", self.doc("<p>Abertura e clique só contam a partir de 27 de novembro de 2026.</p>"))
        self.assertEqual(C.check_date_placeholder(self.site), [])

    def test_placeholder_fails_in_text_attribute_entity_and_script(self):
        cases = {
            "em texto": "<p>Atualizada em [DATA].</p>",
            "em atributo": '<meta name="description" content="Desde [DATA]">',
            "em entidade HTML": "<p>A partir de &#91;DATA&#93;.</p>",
            "em script": "<script>const d = '[DATA]';</script>",
        }
        for name, body in cases.items():
            with self.subTest(case=name):
                got = C.check_page_date_placeholder("privacidade/index.html", self.doc(body))
                self.assertEqual(len(got), 1, got)
                self.assertIn(C.DATE_PLACEHOLDER, got[0])

    def test_site_scan_and_main_wiring(self):
        self.w("index.html", self.doc("<p>Home limpa.</p>"))
        self.w("privacidade/index.html", self.doc("<p>Atualizada em [DATA].</p>"))
        got = C.check_date_placeholder(self.site)
        self.assertEqual(len(got), 1, got)
        self.assertIn("privacidade/index.html", got[0])

        consts = Path(self.tmp.name) / "consts.ts"
        consts.write_text("export const PRE_CUTOVER_NOINDEX = false;\n", encoding="utf-8")
        err = io.StringIO()
        with isolate_main_rule("check_date_placeholder"), \
                contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = C.main(["--site", str(self.site), "--posts", str(self.tmp.name), "--consts", str(consts)])
        self.assertEqual(code, 1)
        self.assertIn(C.DATE_PLACEHOLDER, err.getvalue())

        self.w("privacidade/index.html", self.doc("<p>Atualizada em 27 de novembro de 2026.</p>"))
        with isolate_main_rule("check_date_placeholder"), \
                contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(C.main(["--site", str(self.site), "--posts", str(self.tmp.name), "--consts", str(consts)]), 0)


# Regras chamadas por C.main() e o retorno neutro usado ao isolar uma delas (#147).
MAIN_RULES = {
    "check": lambda *a, **k: [],
    "check_related": lambda *a, **k: [],
    "check_summary": lambda *a, **k: [],
    "check_contrast": lambda *a, **k: [],
    "check_consulting": lambda *a, **k: [],
    "check_book": lambda *a, **k: [],
    "check_paid_consulting": lambda *a, **k: [],
    "check_home": lambda *a, **k: [],
    "check_post_ld": lambda *a, **k: [],
    "check_topic_ld": lambda *a, **k: [],
    "check_framework_ld": lambda *a, **k: [],
    "check_og_site_name": lambda *a, **k: [],
    "check_post_residues": lambda *a, **k: [],
    "check_llm_ranking": lambda *a, **k: [],
    "check_legal": lambda *a, **k: [],
    "check_priority": lambda *a, **k: [],
    "check_auth": lambda *a, **k: [],
    "check_date_placeholder": lambda *a, **k: [],
    "check_llms_txt": lambda *a, **k: [],
    "check_courses": lambda *a, **k: [],
    "check_youtube": lambda *a, **k: [],
    "check_old_youtube_handle": lambda *a, **k: [],
    "check_series": lambda *a, **k: [],
    "check_seo": lambda *a, **k: ([], [], []),
}


@contextlib.contextmanager
def isolate_main_rule(keep: str):
    """Faz mock de todas as regras de C.main(), exceto `keep`, para o teste depender só dela."""
    if keep not in MAIN_RULES:
        raise KeyError(f"regra fora de MAIN_RULES: {keep!r}")
    with contextlib.ExitStack() as stack:
        for name, stub in MAIN_RULES.items():
            if name != keep:
                stack.enter_context(mock.patch.object(C, name, stub))
        yield


def _main_rules_in_source(fn) -> set[str]:
    """Extrai via AST todas as chamadas a check / check_* dentro de C.main()."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and (node.func.id == "check" or node.func.id.startswith("check_")):
                out.add(node.func.id)
    return out


def _with_item_mocks(expr: ast.AST, rules: set[str]) -> set[str]:
    """Quais regras de `rules` um item de `with` isola (via isolate_main_rule ou mock.patch.object)."""
    if not isinstance(expr, ast.Call):
        return set()
    f = expr.func
    if isinstance(f, ast.Name) and f.id == "isolate_main_rule":
        arg = expr.args[0] if expr.args else next((kw.value for kw in expr.keywords if kw.arg == "keep"), None)
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value in rules:
            return set(MAIN_RULES) - {arg.value}
        return set()
    if (isinstance(f, ast.Attribute) and f.attr == "object"
            and isinstance(f.value, ast.Attribute) and f.value.attr == "patch"
            and len(expr.args) >= 2
            and isinstance(expr.args[0], ast.Name) and expr.args[0].id == "C"
            and isinstance(expr.args[1], ast.Constant) and isinstance(expr.args[1].value, str)):
        return {expr.args[1].value}
    return set()


def _unisolated_main_tests(source: str, rules: set[str]) -> dict[str, set[str]]:
    """Retorna {escopo: regras_nao_isoladas} para qualquer chamada a C.main() no arquivo sem isolar as outras regras.
    Cobre métodos test_*, métodos auxiliares em classes, funções de módulo e closures/lambdas (que podem rodar fora do with)."""
    tree = ast.parse(source)
    bad: dict[str, set[str]] = {}

    def walk(node: ast.AST, active: frozenset[str], label: str) -> None:
        if isinstance(node, ast.With):
            added: set[str] = set()
            for item in node.items:
                walk(item.context_expr, active, label)
                added |= _with_item_mocks(item.context_expr, rules)
            inner = active | added
            for stmt in node.body:
                walk(stmt, inner, label)
            return
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            # Função interna ou lambda pode ser executada depois do fim do bloco with: reinicia os mocks ativos.
            for child in ast.iter_child_nodes(node):
                walk(child, frozenset(), label)
            return
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "C" and f.attr == "main":
                unmocked = rules - active
                if len(unmocked) > 1:
                    bad.setdefault(label, set()).update(unmocked)
        for child in ast.iter_child_nodes(node):
            walk(child, active, label)

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for stmt in item.body:
                        walk(stmt, frozenset(), f"{node.name}.{item.name}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for stmt in node.body:
                walk(stmt, frozenset(), node.name)
        else:
            walk(node, frozenset(), "<module>")
    return bad


class MainRuleIsolationTest(unittest.TestCase):
    """#147: garante que MAIN_RULES acompanha C.main() e que todo teste de C.main() isola todas as outras regras."""

    def test_main_rules_registry_matches_c_main(self):
        self.assertEqual(set(MAIN_RULES), _main_rules_in_source(C.main))

    def test_every_main_test_isolates_all_other_rules(self):
        src = Path(__file__).read_text(encoding="utf-8")
        rules = _main_rules_in_source(C.main)
        self.assertEqual(_unisolated_main_tests(src, rules), {})

    def test_isolate_main_rule_patches_every_other_rule(self):
        originals = {name: getattr(C, name) for name in MAIN_RULES}
        for keep in MAIN_RULES:
            with self.subTest(keep=keep):
                with isolate_main_rule(keep):
                    self.assertIs(getattr(C, keep), originals[keep])
                    for other in MAIN_RULES:
                        if other == keep:
                            continue
                        patched = getattr(C, other)
                        self.assertIsNot(patched, originals[other], f"{other} não foi substituída ao isolar {keep}")
                        expected = ([], [], []) if other == "check_seo" else []
                        self.assertEqual(patched(Path("."), Path(".")), expected, other)
                for name, orig in originals.items():
                    self.assertIs(getattr(C, name), orig, f"{name} não foi restaurada após sair do with")
        with self.assertRaises(KeyError):
            with isolate_main_rule("check_inexistente"):
                pass

    def test_guard_catches_new_rule_and_missing_mock(self):
        def fake_main():
            check()
            check_nova()

        self.assertIn("check_nova", _main_rules_in_source(fake_main) - set(MAIN_RULES))

        sample = (
            "def top_helper():\n"
            "    return C.main([])\n"
            "class T:\n"
            "    def _helper(self):\n"
            "        return C.main([])\n"
            "    def test_incomplete(self):\n"
            "        with mock.patch.object(C, 'check', lambda *a, **k: []):\n"
            "            C.main([])\n"
            "    def test_outside_with(self):\n"
            "        with isolate_main_rule('check_home'):\n"
            "            pass\n"
            "        C.main([])\n"
            "    def test_deferred_lambda(self):\n"
            "        with isolate_main_rule('check_home'):\n"
            "            fn = lambda: C.main([])\n"
            "        return fn\n"
            "    def test_ok_helper(self):\n"
            "        with isolate_main_rule('check_home'):\n"
            "            C.main([])\n"
        )
        rules = set(MAIN_RULES)
        got = _unisolated_main_tests(sample, rules)
        self.assertEqual(
            set(got.keys()),
            {"top_helper", "T._helper", "T.test_incomplete", "T.test_outside_with", "T.test_deferred_lambda"},
        )
        self.assertEqual(got["top_helper"], rules)
        self.assertEqual(got["T._helper"], rules)
        self.assertEqual(got["T.test_incomplete"], rules - {"check"})
        self.assertEqual(got["T.test_outside_with"], rules)
        self.assertEqual(got["T.test_deferred_lambda"], rules)


if __name__ == "__main__":
    unittest.main(verbosity=2)

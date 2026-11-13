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


# Títulos dos testes que não tratam dessa regra: a home tem texto exato; as demais páginas usam o sufixo.
# TitleAndGroupTest testa a regra com títulos próprios.
FIXTURE_TITLE = "<title>Guilherme Favaron | Teste | Guilherme Favaron</title>"
HOME_FIXTURE_TITLE = "<title>Guilherme Favaron | Builder e CTO</title>"


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
    """Títulos "Página | Guilherme Favaron" (home com o nome primeiro) e content_group do GA4 por tipo de página."""

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
                 ("blog/um-post/index.html", "Um post | Guilherme Favaron", True),
                 ("blog/um-post/index.html", "Um post | Guilherme Favaron ", True),  # espaço em volta não conta
                 ("blog/um-post/index.html", "Guilherme Favaron | Um post", False),
                 ("404.html", "Página não encontrada | Guilherme Favaron", True)]
        for rel, title, ok in cases:
            with self.subTest(rel=rel, title=title):
                self.assertEqual(self.check(rel, self.doc(f"<title>{title}</title>")) == [], ok)

    def test_entities_are_decoded(self):
        self.assertEqual(self.check("blog/x/index.html", self.doc("<title>A &amp; B &#124; Guilherme Favaron</title>")), [])

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
                       ("llm-ranking/index.html", None), ("blog/pagina/x/index.html", None),
                       ("blog/a/b/index.html", None)):
            with self.subTest(rel=rel):
                self.assertEqual(E(rel), g)
        self.assertEqual(set(C.CONTENT_GROUPS), {"home", "portfolio", "insights-lista", "insights-artigo", "expert-insights"})

    def test_group_literal_and_define_vars(self):
        title = "<title>Guilherme Favaron | Builder e CTO</title>"
        self.assertEqual(self.check("index.html", self.doc(title, self.GA + self.cfg(",{content_group:'home'}"))), [])
        astro = ('<script>(function(){const GA4_ID = "G-K7Y87QNCX8";\nconst CONTENT_GROUP = "insights-artigo";\n'
                 "gtag('config', GA4_ID, { content_group: CONTENT_GROUP });})();</script>")
        self.assertEqual(self.check("blog/um-post/index.html", self.doc("<title>X | Guilherme Favaron</title>", self.GA + astro)), [])

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
            "código em string": 'const exemplo = "' + valid + '";',
        }
        for name, js in bad.items():
            with self.subTest(name=name):
                html = self.doc(title, self.GA + "<script>" + js + "</script>")
                self.assertTrue(any("content_group" in p for p in self.check("index.html", html)), name)
        with_event = valid + "gtag('event','x',{content_group:'portfolio'});"
        self.assertEqual(self.check("index.html", self.doc(title, self.GA + "<script>" + with_event + "</script>")), [])

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
            (site / "blog" / "um" / "index.html").write_text(self.doc("<title>Sem barra</title>"), encoding="utf-8")
            posts = Path(d) / "posts"; posts.mkdir()
            self.assertTrue(any("precisa terminar em" in p for p in C.check(site, posts, pre_cutover=False)))


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
        for rel in ("index.html", "portfolio/index.html"):
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


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Testes do gerador de sitemaps da raiz (scripts/build_sitemaps.py).

Rodar: python3 scripts/test_build_sitemaps.py
"""
import os
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

import build_sitemaps as B

NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
BLOG = ('<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        '<url><loc>https://guifav.dev/blog/</loc><lastmod>2026-09-02T14:29:14.967Z</lastmod></url>'
        '<url><loc>https://guifav.dev/blog/a/</loc><lastmod>2026-10-01T00:00:00.000Z</lastmod></url>'
        '<url><loc>https://guifav.dev/blog/b/</loc><lastmod>2025-01-01T00:00:00.000Z</lastmod></url></urlset>')


def html(head: str = "") -> str:
    return f"<!DOCTYPE html><html><head>{head}</head><body>x</body></html>"


class Repo(unittest.TestCase):
    """Repo git de verdade com datas de commit controladas; o _site é cópia dos arquivos."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.site = Path(self.tmp.name) / "_site"
        self.repo.mkdir()
        self.git("init", "-q")
        self.commit({"index.html": html('<link rel="canonical" href="https://guifav.dev">'),
                     "robots.txt": "User-agent: *\nDisallow: /portfolio/mockups/\nDisallow: /tools/\n"},
                    "2026-01-10T10:00:00-03:00")
        self.commit({"portfolio/index.html": html('<link rel="canonical" href="https://guifav.dev/portfolio/">'),
                     "portfolio/mockups/a.html": html(),
                     "404.html": html('<meta name="robots" content="noindex">')}, "2026-03-05T12:00:00-03:00")
        self.commit({"llm-ranking/index.html": html(), "fw/f.html": html('<link rel="canonical" href="https://guifav.dev/fw/f.html">'),
                     "rascunho/index.html": html('<meta name="robots" content="noindex, follow">'),
                     "outra/index.html": html('<link rel="canonical" href="https://guifav.github.io/outra">')},
                    "2026-06-20T08:30:00-03:00")
        self.copy_site()

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args, date: str | None = None):
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                   GIT_COMMITTER_EMAIL="t@t")
        if date:
            env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
        subprocess.run(["git", "-C", str(self.repo), *args], check=True, env=env, capture_output=True)

    def commit(self, files: dict[str, str], date: str):
        for rel, text in files.items():
            p = self.repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "c", date=date)

    def copy_site(self):
        for f in self.repo.rglob("*"):
            if f.is_file() and ".git" not in f.relative_to(self.repo).parts:
                d = self.site / f.relative_to(self.repo)
                d.parent.mkdir(parents=True, exist_ok=True)
                d.write_bytes(f.read_bytes())
        (self.site / "blog").mkdir(parents=True, exist_ok=True)
        (self.site / "blog" / "sitemap.xml").write_text(BLOG, encoding="utf-8")
        (self.site / "blog" / "x").mkdir(exist_ok=True)
        (self.site / "blog" / "x" / "index.html").write_text(html(), encoding="utf-8")

    def entries(self, rel: str, item: str) -> list[tuple[str | None, str | None]]:
        root = ET.parse(self.site / rel).getroot()
        return [(e.findtext(NS + "loc"), e.findtext(NS + "lastmod")) for e in root.iter(NS + item)]


class BuildTest(Repo):
    def test_site_sitemap_selects_indexable_pages_with_git_lastmod(self):
        n_site, n_blog = B.build(self.repo, self.site)
        self.assertEqual((n_site, n_blog), (4, 3))
        self.assertEqual(self.entries("sitemap-site.xml", "url"), [
            ("https://guifav.dev/", "2026-01-10T10:00:00-03:00"),
            ("https://guifav.dev/fw/f.html", "2026-06-20T08:30:00-03:00"),
            ("https://guifav.dev/llm-ranking/", "2026-06-20T08:30:00-03:00"),
            ("https://guifav.dev/portfolio/", "2026-03-05T12:00:00-03:00"),
        ])

    def test_lastmod_follows_the_last_commit_of_each_file(self):
        self.commit({"portfolio/index.html": html('<link rel="canonical" href="https://guifav.dev/portfolio/"><b>novo</b>')},
                    "2026-11-12T09:00:00-03:00")
        self.copy_site()
        B.build(self.repo, self.site)
        got = dict(self.entries("sitemap-site.xml", "url"))
        self.assertEqual(got["https://guifav.dev/portfolio/"], "2026-11-12T09:00:00-03:00")
        self.assertEqual(got["https://guifav.dev/"], "2026-01-10T10:00:00-03:00")

    def test_index_cites_both_sitemaps_without_lastmod(self):
        # lastmod no índice seria a data do arquivo do sitemap; o maior lastmod das páginas não serve (r1).
        B.build(self.repo, self.site)
        self.assertEqual(self.entries("sitemap.xml", "sitemap"), [
            ("https://guifav.dev/sitemap-site.xml", None),
            ("https://guifav.dev/blog/sitemap.xml", None),
        ])
        self.assertEqual(ET.parse(self.site / "sitemap.xml").getroot().tag, NS + "sitemapindex")
        self.assertNotIn("lastmod", (self.site / "sitemap.xml").read_text(encoding="utf-8"))

    def test_refuses_when_files_already_exist(self):
        for name in ("sitemap.xml", "sitemap-site.xml"):
            with self.subTest(name=name):
                (self.site / name).write_text("x", encoding="utf-8")
                with self.assertRaisesRegex(B.SitemapError, "já existe"):
                    B.build(self.repo, self.site)
                (self.site / name).unlink()

    def test_refuses_shallow_clone(self):
        clone = Path(self.tmp.name) / "raso"
        subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{self.repo}", str(clone)], check=True, capture_output=True)
        with self.assertRaisesRegex(B.SitemapError, "raso"):
            B.build(clone, self.site)

    def test_refuses_untracked_page(self):
        (self.site / "nova.html").write_text(html(), encoding="utf-8")
        with self.assertRaisesRegex(B.SitemapError, "nova.html: sem commit"):
            B.build(self.repo, self.site)

    def test_refuses_bad_blog_sitemap(self):
        for body in ("nao e xml", '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"/>'):
            with self.subTest(body=body):
                (self.site / "blog" / "sitemap.xml").write_text(body, encoding="utf-8")
                with self.assertRaises(B.SitemapError):
                    B.build(self.repo, self.site)
                self.assertFalse((self.site / "sitemap.xml").exists())

    def test_404_out_even_without_noindex(self):
        (self.site / "404.html").write_text(html(), encoding="utf-8")
        B.build(self.repo, self.site)
        self.assertNotIn("https://guifav.dev/404.html", [l for l, _ in self.entries("sitemap-site.xml", "url")])

    def test_refuses_outside_git(self):
        plain = Path(self.tmp.name) / "sem-git"
        plain.mkdir()
        with self.assertRaisesRegex(B.SitemapError, "fora do git"):
            B.build(plain, self.site)

    def test_lastmod_is_commit_date_not_author_date(self):
        # Squash merge: o autor escreveu antes, mas a página entrou na main na data do commit.
        (self.repo / "llm-ranking/index.html").write_text(html("<b>v2</b>"), encoding="utf-8")
        self.git("add", "-A")
        env_date = "2026-11-12T18:00:00-03:00"
        subprocess.run(["git", "-C", str(self.repo), "commit", "-q", "-m", "squash"], check=True, capture_output=True,
                       env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                                GIT_COMMITTER_EMAIL="t@t", GIT_AUTHOR_DATE="2026-11-01T09:00:00-03:00",
                                GIT_COMMITTER_DATE=env_date))
        self.copy_site()
        B.build(self.repo, self.site)
        self.assertEqual(dict(self.entries("sitemap-site.xml", "url"))["https://guifav.dev/llm-ranking/"], env_date)

    def test_refuses_site_without_indexable_pages(self):
        for f in list(self.site.rglob("*.html")):
            if not f.relative_to(self.site).as_posix().startswith("blog/"):
                f.write_text(html('<meta name="robots" content="noindex">'), encoding="utf-8")
        with self.assertRaisesRegex(B.SitemapError, "nenhuma página"):
            B.build(self.repo, self.site)

    def test_main_exit_codes(self):
        self.assertEqual(B.main(["--repo", str(self.repo), "--site", str(self.site)]), 0)
        self.assertEqual(B.main(["--repo", str(self.repo), "--site", str(self.site)]), 1)


class RulesTest(unittest.TestCase):
    def test_robots_disallow_only_star_group(self):
        txt = ("User-agent: Googlebot\nDisallow: /g/\n\nUser-agent: *\nDisallow: /a/\nDisallow:\nAllow: /a/b\n"
               "# comentario\nDisallow: /c/ # fim\n\nUser-agent: Bingbot\nDisallow: /b/\n")
        self.assertEqual(B.robots_disallow(txt), ["/a/", "/c/"])

    def test_robots_group_with_several_agents(self):
        self.assertEqual(B.robots_disallow("User-agent: x\nUser-agent: *\nDisallow: /p/\n"), ["/p/"])
        self.assertEqual(B.robots_disallow("user-agent: *\ndisallow: /p/\n"), ["/p/"])

    def test_canonical_is_self(self):
        self.assertTrue(B.canonical_is_self([], "/x/"))
        self.assertTrue(B.canonical_is_self(["https://guifav.dev"], "/"))
        self.assertTrue(B.canonical_is_self(["https://guifav.dev/x/", "https://guifav.dev/x/"], "/x/"))
        for c in (["https://guifav.dev/y/"], ["http://guifav.dev/x/"], ["https://guifav.github.io/x/"],
                  ["https://guifav.dev/x/?a=1"], ["https://guifav.dev/x/#t"], ["https://guifav.dev/x"],
                  ["https://guifav.dev/x/", "https://guifav.dev/y/"], ["/x/"]):
            with self.subTest(c=c):
                self.assertFalse(B.canonical_is_self(c, "/x/"))

    def test_noindex(self):
        self.assertTrue(B.noindex(["noindex, follow"]))
        self.assertTrue(B.noindex(["index", "none"]))
        self.assertFalse(B.noindex(["index, follow"]))
        self.assertFalse(B.noindex([]))

    def test_page_url(self):
        self.assertEqual(B.page_url("index.html"), "/")
        self.assertEqual(B.page_url("a/b/index.html"), "/a/b/")
        self.assertEqual(B.page_url("a/f.html"), "/a/f.html")

    def test_escape(self):
        self.assertIn("<loc>https://guifav.dev/a?b=1&amp;c=2</loc>", B.urlset([("https://guifav.dev/a?b=1&c=2", "d")]))


class RealSiteTest(unittest.TestCase):
    """O _site deste repo, quando montado: as 4 páginas que devem ir ao Google."""

    def test_repo_pages(self):
        repo = Path(__file__).resolve().parent.parent
        site = repo / "_site"
        if not (site / "index.html").is_file():
            self.skipTest("_site não montado")
        urls = [u for _, u in B.indexable_pages(site)]
        self.assertEqual(urls, ["/", "/llm-ranking/", "/portfolio/", "/seo-geo-aeo-framework/framework-seo-aeo.html"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

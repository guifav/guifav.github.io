"""Testes do gerador de sitemaps da raiz (scripts/build_sitemaps.py).

Rodar: python3 scripts/test_build_sitemaps.py
"""
import os
import re
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlsplit

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


class HomeLastmodTest(Repo):
    """#136: a home lista os últimos artigos; o lastmod dela acompanha o mais recente entre eles e o commit."""

    def latest(self, *paths: str) -> None:
        lis = "".join(f'<li><a href="{p}">t</a></li>' for p in paths)
        (self.site / "index.html").write_text(
            html('<link rel="canonical" href="https://guifav.dev">') +
            f'<!-- ultimos-artigos --><ul class="latest-list">{lis}</ul><!-- /ultimos-artigos -->', encoding="utf-8")

    def home(self) -> str:
        B.build(self.repo, self.site)
        return dict(self.entries("sitemap-site.xml", "url"))["https://guifav.dev/"]

    def test_newest_listed_post_wins_over_an_older_commit(self):
        self.latest("/blog/b/", "/blog/a/")
        self.assertEqual(self.home(), "2026-10-01T00:00:00.000Z")

    def test_newer_commit_wins_and_timezones_are_compared(self):
        self.commit({"index.html": html('<link rel="canonical" href="https://guifav.dev"><b>novo</b>')},
                    "2026-09-30T22:00:00-03:00")  # 01/10 01:00 UTC, depois do post a
        self.copy_site()
        self.latest("/blog/a/")
        self.assertEqual(self.home(), "2026-09-30T22:00:00-03:00")

    def test_without_the_list_the_commit_counts(self):
        self.assertEqual(self.home(), "2026-01-10T10:00:00-03:00")

    def test_other_pages_ignore_the_list(self):
        self.latest("/blog/a/")
        B.build(self.repo, self.site)
        self.assertEqual(dict(self.entries("sitemap-site.xml", "url"))["https://guifav.dev/portfolio/"],
                         "2026-03-05T12:00:00-03:00")

    def test_listed_post_without_lastmod_fails(self):
        for path in ("/blog/sumiu/", "https://guifav.dev/blog/a/"):
            with self.subTest(path=path):
                self.latest(path)
                with self.assertRaisesRegex(B.SitemapError, "não tem lastmod"):
                    B.build(self.repo, self.site)

    def test_bad_lastmod_in_the_blog_sitemap_fails(self):
        (self.site / "blog" / "sitemap.xml").write_text(BLOG.replace("2026-10-01T00:00:00.000Z", "ontem"),
                                                        encoding="utf-8")
        self.latest("/blog/a/")
        with self.assertRaisesRegex(B.SitemapError, "lastmod ilegível"):
            B.build(self.repo, self.site)

    def test_commented_list_is_not_a_list(self):
        (self.site / "index.html").write_text(html() + "<!-- ultimos-artigos  -->", encoding="utf-8")
        self.assertEqual(self.home(), "2026-01-10T10:00:00-03:00")


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


# Na CI, o passo depois do build_sitemaps.py roda esta classe com SITEMAP_REAL_SITE=required: sem _site montado ou sem o
# sitemap gerado, o teste falha em vez de se pular (#117). No passo dos testes, antes da montagem, ele se pula.
REQUIRED_ENV = "SITEMAP_REAL_SITE"
REPO = Path(__file__).resolve().parent.parent
_META_ROBOTS = re.compile(r"""<meta\b(?=[^>]*\bname\s*=\s*["']?robots["']?)[^>]*\bcontent\s*=\s*["']([^"']*)["']""", re.I)
_CANONICAL = re.compile(r"""<link\b(?=[^>]*\brel\s*=\s*["']?canonical["']?)[^>]*\bhref\s*=\s*["']([^"']*)["']""", re.I)


_HTML_SUFFIXES = (".html", ".htm", ".xhtml")


def _star_disallow(robots_txt: str) -> list[str]:
    """Prefixos Disallow só do grupo User-agent: * (um grupo de outro robô não conta), sem comentários."""
    out, in_star, seen_rule = [], False, False
    for raw in robots_txt.splitlines():
        line = raw.split("#", 1)[0].strip()
        key, _, val = line.partition(":")
        key, val = key.strip().lower(), val.strip()
        if key == "user-agent":
            if seen_rule:
                in_star, seen_rule = False, False
            in_star = in_star or val == "*"
        elif key in ("allow", "disallow"):
            seen_rule = True
            if in_star and key == "disallow" and val:
                out.append(val)
    return out


def _url(rel: str) -> str:
    """Caminho publicado de um arquivo: pasta/index.html vira /pasta/ (escrito aqui, sem o page_url sob teste)."""
    if rel == "index.html":
        return "/"
    return "/" + (rel[: -len("index.html")] if rel.endswith("/index.html") else rel)


def manifest_pages(repo: Path) -> list[str]:
    """Oráculo independente do build_sitemaps: as páginas HTML do site-manifest.txt (lidas dos fontes do repo) que
    devem ir ao /sitemap-site.xml. Sai o 404, o que o grupo User-agent: * do robots.txt do repo bloqueia, o que tem
    meta robots noindex/none e o que aponta o canonical para outra URL. Comentários HTML não contam."""
    disallow = _star_disallow((repo / "robots.txt").read_text(encoding="utf-8"))
    files: list[Path] = []
    for raw in (repo / "site-manifest.txt").read_text(encoding="utf-8").splitlines():
        entry = raw.split("#", 1)[0].strip()
        if not entry:
            continue
        src = repo / entry
        if not src.exists():
            raise AssertionError(f"site-manifest.txt cita {entry}, que não existe no repo")
        if entry.endswith("/"):
            files += sorted(f for f in src.rglob("*") if f.is_file() and f.suffix.lower() in _HTML_SUFFIXES)
        elif src.suffix.lower() in _HTML_SUFFIXES:
            files.append(src)
    out = []
    for f in files:
        rel = f.relative_to(repo).as_posix()
        if rel == "404.html":
            continue
        url = _url(rel)
        text = re.sub(r"<!--.*?-->", "", f.read_text(encoding="utf-8"), flags=re.S)
        if any(url.startswith(d) for d in disallow):
            continue
        if any({d.strip().lower() for d in c.split(",")} & {"noindex", "none"} for c in _META_ROBOTS.findall(text)):
            continue
        canon = {(s.scheme, s.netloc, s.path or "/", s.query, s.fragment) for s in map(urlsplit, _CANONICAL.findall(text))}
        if canon and canon != {("https", "guifav.dev", url, "", "")}:  # "https://guifav.dev" sem barra é a home
            continue
        out.append(url)
    return sorted(out)


class RealSiteTest(unittest.TestCase):
    """O _site deste repo, montado e com sitemaps gerados: o /sitemap-site.xml tem exatamente as páginas indexáveis do
    site-manifest.txt (#117), sem lista fixa."""

    def site_sitemap(self) -> Path:
        site = REPO / "_site"
        sm = site / B.SITE_SITEMAP
        if not sm.is_file():
            msg = "_site sem sitemap-site.xml (rode assemble_site.py e build_sitemaps.py antes)"
            if os.environ.get(REQUIRED_ENV) == "required":
                self.fail(msg)
            self.skipTest(msg)
        return sm

    def test_sitemap_has_every_indexable_manifest_page(self):
        sm = self.site_sitemap()
        got = sorted((e.text or "").strip() for e in ET.parse(sm).getroot().iter(f"{NS}loc"))
        want = manifest_pages(REPO)
        self.assertIn("/", want)
        self.assertEqual(got, ["https://guifav.dev" + u for u in want])

    def test_oracle_and_generator_agree_on_the_assembled_site(self):
        self.site_sitemap()
        self.assertEqual([u for _, u in B.indexable_pages(REPO / "_site")], manifest_pages(REPO))


class RealSiteGuardTest(unittest.TestCase):
    """O próprio RealSiteTest: com o modo obrigatório ele falha sem _site, e o oráculo enxerga uma página que some."""

    def run_real(self, env_value, files=None):
        with tempfile.TemporaryDirectory() as d:
            env = {k: v for k, v in os.environ.items() if k != REQUIRED_ENV}
            if env_value:
                env[REQUIRED_ENV] = env_value
            # Cópia do script num repo de mentira: o REPO do teste aponta para ela (sem _site, salvo o que vier em files).
            scripts = Path(d) / "scripts"
            scripts.mkdir()
            for name in ("test_build_sitemaps.py", "build_sitemaps.py"):
                (scripts / name).write_text((REPO / "scripts" / name).read_text(encoding="utf-8"), encoding="utf-8")
            for rel, text in (files or {}).items():
                (Path(d) / rel).parent.mkdir(parents=True, exist_ok=True)
                (Path(d) / rel).write_text(text, encoding="utf-8")
            return subprocess.run([sys.executable, str(scripts / "test_build_sitemaps.py"), "RealSiteTest"],
                                  capture_output=True, text=True, env=env)

    def test_required_mode_fails_without_site(self):
        r = self.run_real("required")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("_site sem sitemap-site.xml", r.stderr)
        self.assertNotIn("skipped", r.stderr)

    def test_default_mode_skips_without_site(self):
        r = self.run_real(None)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("skipped", r.stderr)

    def test_swapped_page_with_the_same_count_fails(self):
        # O sitemap troca /b.html por /c.html: mesmo número de URLs, mas a página do manifesto sumiu.
        pages = {"index.html": html(), "a.html": html(), "b.html": html()}
        loc = "<url><loc>https://guifav.dev{}</loc><lastmod>2026-11-24T00:00:00-03:00</lastmod></url>"
        files = {"robots.txt": "User-agent: *\nDisallow: /tools/\n", "site-manifest.txt": "index.html\na.html\nb.html\n",
                 **pages, **{f"_site/{k}": v for k, v in pages.items()},
                 "_site/sitemap-site.xml": f'<urlset xmlns="{B.NS}">' + "".join(loc.format(u) for u in ("/", "/a.html", "/c.html"))
                 + "</urlset>"}
        r = self.run_real("required", files)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("FAIL: test_sitemap_has_every_indexable_manifest_page", r.stderr)
        self.assertNotIn("FAIL: test_oracle_and_generator_agree", r.stderr)
        files["_site/sitemap-site.xml"] = files["_site/sitemap-site.xml"].replace("/c.html", "/b.html")
        self.assertEqual(self.run_real("required", files).returncode, 0)

    def fake_repo(self, sitemap_paths, site_pages=None):
        pages = {"index.html": html(), "a.html": html(), "b.html": html()}
        loc = "<url><loc>{}</loc><lastmod>2026-11-24T00:00:00-03:00</lastmod></url>"
        site = pages if site_pages is None else site_pages
        return {"robots.txt": "User-agent: *\nDisallow: /tools/\n", "site-manifest.txt": "index.html\na.html\nb.html\n",
                **pages, **{f"_site/{k}": v for k, v in site.items()},
                "_site/sitemap-site.xml": f'<urlset xmlns="{B.NS}">' + "".join(loc.format(u) for u in sitemap_paths)
                + "</urlset>"}

    def test_wrong_host_or_query_in_the_sitemap_fails(self):
        # r1 (agy P2): a comparação é da URL inteira, não só do caminho.
        ok = ["https://guifav.dev/", "https://guifav.dev/a.html", "https://guifav.dev/b.html"]
        self.assertEqual(self.run_real("required", self.fake_repo(ok)).returncode, 0)
        for bad in (["http://guifav.dev/"] + ok[1:], ["https://www.guifav.dev/"] + ok[1:], ok[:2] + [ok[2] + "?x=1"]):
            with self.subTest(bad=bad):
                r = self.run_real("required", self.fake_repo(bad))
                self.assertIn("FAIL: test_sitemap_has_every_indexable_manifest_page", r.stderr)

    def test_page_missing_from_the_assembled_site_fails(self):
        # r1 (agy P3): o caminho de falha do teste que compara o oráculo com o _site montado.
        ok = ["https://guifav.dev/", "https://guifav.dev/a.html", "https://guifav.dev/b.html"]
        r = self.run_real("required", self.fake_repo(ok, site_pages={"index.html": html(), "a.html": html()}))
        self.assertIn("FAIL: test_oracle_and_generator_agree_on_the_assembled_site", r.stderr)

    def test_workflow_runs_it_required_after_the_sitemaps(self):
        wf = (REPO / ".github/workflows/pages.yml").read_text(encoding="utf-8")
        step = re.search(r"- name: [^\n]*#117\)\n(?:[ ]+[^\n]*\n)+?[ ]+run: python3 scripts/test_build_sitemaps.py RealSiteTest\n", wf)
        self.assertTrue(step, "passo do RealSiteTest ausente no pages.yml")
        self.assertRegex(step.group(0), rf"\n[ ]+env:\n[ ]+{REQUIRED_ENV}: required\n")  # r1 (agy P3): não comentado
        pos = {k: wf.index(k) for k in ("python3 scripts/assemble_site.py", "python3 scripts/build_sitemaps.py",
                                        "python3 scripts/check_site.py")}
        self.assertLess(pos["python3 scripts/assemble_site.py"], pos["python3 scripts/build_sitemaps.py"])
        self.assertLess(pos["python3 scripts/build_sitemaps.py"], step.start())
        self.assertLess(step.start(), pos["python3 scripts/check_site.py"])

    def test_oracle_follows_manifest_robots_noindex_and_canonical(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            (repo / "robots.txt").write_text("User-agent: GPTBot\nDisallow: /\n\nUser-agent: *\n"
                                             "Disallow: /p/mockups/  # mockups\n", encoding="utf-8")
            (repo / "site-manifest.txt").write_text("# c\n#fora/index.html\nindex.html\n404.html\np/\nsolto.html  # comentário\n"
                                                    "img.png\n", encoding="utf-8")
            pages = {
                "index.html": html('<link rel="canonical" href="https://guifav.dev">'),
                "404.html": html(),
                "p/index.html": html('<link rel="canonical" href="https://guifav.dev/p/">'),
                "p/outra.html": html('<link rel="canonical" href="https://guifav.dev/p/">'),
                "p/fechada/index.html": html('<meta name="robots" content="noindex, follow">'),
                "p/mockups/a.html": html(),
                "solto.html": html('<meta content="index,follow" name="robots">'),
                "p/query.html": html('<link rel="canonical" href="https://guifav.dev/p/query.html?x=1">'),
                "fora/index.html": html(),
                "p/comentada.html": html('<!-- <meta name="robots" content="noindex"> -->'),
                "p/maiuscula.HTM": html(),
            }
            for rel, text in pages.items():
                (repo / rel).parent.mkdir(parents=True, exist_ok=True)
                (repo / rel).write_text(text, encoding="utf-8")
            (repo / "img.png").write_text("x", encoding="utf-8")
            self.assertEqual(manifest_pages(repo), ["/", "/p/", "/p/comentada.html", "/p/maiuscula.HTM", "/solto.html"])
            (repo / "site-manifest.txt").write_text("index.html\nsumiu/\n", encoding="utf-8")
            with self.assertRaisesRegex(AssertionError, "sumiu/"):
                manifest_pages(repo)

    def test_url_of_folder_index(self):
        # r1 (Claude P3): a conversão de caminho em URL é do próprio teste.
        self.assertEqual([_url(r) for r in ("index.html", "a/index.html", "a/b.html")], ["/", "/a/", "/a/b.html"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

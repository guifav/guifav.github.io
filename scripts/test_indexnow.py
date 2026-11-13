"""Testes do IndexNow (scripts/indexnow.py). Sem rede: GET e POST são simulados como a API real responde.

Rodar: python3 scripts/test_indexnow.py
"""
import http.server
import io
import json
import os
import socket
import subprocess
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import indexnow as I

REPO = Path(__file__).resolve().parent.parent
NSX = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'


def urlset(entries: dict[str, str]) -> bytes:
    body = "".join(f"<url><loc>{l}</loc>{f'<lastmod>{m}</lastmod>' if m else ''}</url>" for l, m in entries.items())
    return f'<?xml version="1.0" encoding="UTF-8"?><urlset {NSX}>{body}</urlset>'.encode()


def index(children: list[str]) -> bytes:
    body = "".join(f"<sitemap><loc>{c}</loc></sitemap>" for c in children)
    return f'<?xml version="1.0" encoding="UTF-8"?><sitemapindex {NSX}>{body}</sitemapindex>'.encode()


INDEX = index(["https://guifav.dev/sitemap-site.xml", "https://guifav.dev/blog/sitemap.xml"])
SITE_OLD = {"https://guifav.dev/": "", "https://guifav.dev/portfolio/": ""}
BLOG_OLD = {"https://guifav.dev/blog/": "2026-09-01T00:00:00.000Z", "https://guifav.dev/blog/pagina/2/": "",
            "https://guifav.dev/blog/a/": "2026-01-01T00:00:00.000Z", "https://guifav.dev/blog/sumiu/": "2025-01-01T00:00:00.000Z"}
SITE_NEW = {"https://guifav.dev/": "2026-11-12T10:00:00-03:00", "https://guifav.dev/portfolio/": "2026-11-12T10:00:00-03:00"}
BLOG_NEW = {"https://guifav.dev/blog/": "2026-11-12T00:00:00.000Z", "https://guifav.dev/blog/a/": "2026-01-01T00:00:00.000Z",
            "https://guifav.dev/blog/novo/": "2026-11-12T00:00:00.000Z"}
NOINDEX = b'<html><head><meta name="robots" content="noindex, follow"></head></html>'


class FakeWeb:
    """Responde GET como o Pages: 200 com corpo, 404 no resto; conta as chamadas."""

    def __init__(self, pages: dict[str, bytes]):
        self.pages = pages
        self.calls: list[str] = []

    def __call__(self, url: str):
        self.calls.append(url)
        base = url.split("?", 1)[0]
        return (200, self.pages[base]) if base in self.pages else (404, b"<html>404</html>")


class ErrorWeb(FakeWeb):
    """Responde 503 com o corpo da página (um CDN com erro que ainda manda o arquivo em cache)."""

    def __call__(self, url: str):
        status, body = super().__call__(url)
        return (503, body) if status == 200 else (status, body)


class FakePost:
    """Endpoint do IndexNow: 200/202 aceita; devolve o status configurado e guarda o corpo."""

    def __init__(self, statuses=(200,)):
        self.statuses = list(statuses)
        self.bodies: list[dict] = []

    def __call__(self, url: str, payload: dict):
        assert url == "https://api.indexnow.org/indexnow"
        self.bodies.append(payload)
        st = self.statuses.pop(0) if self.statuses else 200
        return st, b"" if st in (200, 202) else b'{"message":"erro"}'


class Site(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.site = Path(self.tmp.name)
        self.w("sitemap.xml", INDEX)
        self.w("sitemap-site.xml", urlset(SITE_NEW))
        self.w("blog/sitemap.xml", urlset(BLOG_NEW))
        for rel in ("index.html", "portfolio/index.html", "blog/index.html", "blog/a/index.html", "blog/novo/index.html",
                    "blog/pagina/2/index.html"):
            self.w(rel, b"<html></html>")

    def tearDown(self):
        self.tmp.cleanup()

    def w(self, rel: str, data: bytes):
        p = self.site / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def old_web(self) -> FakeWeb:
        # Layout publicado antes deste PR: /sitemap.xml do site e /blog/sitemap.xml, sem índice.
        return FakeWeb({"https://guifav.dev/sitemap.xml": urlset(SITE_OLD), "https://guifav.dev/blog/sitemap.xml": urlset(BLOG_OLD)})

    def built(self) -> dict[str, bytes]:
        """O site publicado igual ao build (o deploy já chegou)."""
        return {f"https://guifav.dev{p}": (self.site / p.lstrip("/")).read_bytes() for p in I.SITEMAPS}

    def shas(self) -> dict[str, str]:
        return {p: I.sha256((self.site / p.lstrip("/")).read_bytes()) for p in I.SITEMAPS}


class DiffTest(Site):
    def test_first_deploy_from_old_layout(self):
        p = I.plan(self.site, self.old_web())
        self.assertEqual(p["mode"], "diff")
        self.assertEqual(p["urls"], sorted([
            "https://guifav.dev/", "https://guifav.dev/portfolio/",  # ganharam lastmod
            "https://guifav.dev/blog/",  # lastmod mudou
            "https://guifav.dev/blog/novo/",  # nova
            "https://guifav.dev/blog/sumiu/",  # saiu e a página não existe mais
        ]))  # /blog/a/ igual e /blog/pagina/2/ só saiu do sitemap: ficam de fora
        self.assertEqual(p["sitemaps"], self.shas())

    def test_nothing_changed(self):
        self.assertEqual(I.plan(self.site, FakeWeb(self.built()))["urls"], [])

    def test_one_post_updated_from_index_layout(self):
        web = FakeWeb(dict(self.built(), **{"https://guifav.dev/blog/sitemap.xml": urlset(
            dict(BLOG_NEW, **{"https://guifav.dev/blog/a/": "2025-12-01T00:00:00.000Z"}))}))
        self.assertEqual(I.plan(self.site, web)["urls"], ["https://guifav.dev/blog/a/"])

    def capa_case(self, live_html: bytes | None, status: int = 200):
        """/blog/ sem lastmod nos dois lados; o HTML publicado é `live_html`."""
        blog = dict(BLOG_NEW, **{"https://guifav.dev/blog/": ""})
        self.w("blog/sitemap.xml", urlset(blog))
        self.w("blog/index.html", b"<html>capa nova</html>")
        pages = dict(self.built())
        web = FakeWeb(pages)
        if live_html is not None:
            pages["https://guifav.dev/blog/"] = live_html
        if status != 200:
            return I.changed(I.built_entries(self.site), I.built_entries(self.site), self.site, ErrorWeb(pages))
        return I.changed(I.built_entries(self.site), I.built_entries(self.site), self.site, web)

    def test_page_without_lastmod_announced_only_when_html_changes(self):
        self.assertEqual(self.capa_case(b"<html>capa nova</html>"), [])
        self.assertEqual(self.capa_case(b"<html>capa velha</html>"), ["https://guifav.dev/blog/"])
        self.assertEqual(self.capa_case(None), ["https://guifav.dev/blog/"])  # 404 publicado
        self.assertEqual(self.capa_case(b"<html>capa nova</html>", status=503), ["https://guifav.dev/blog/"])

    def test_page_without_lastmod_cannot_compare_without_get(self):
        self.w("blog/sitemap.xml", urlset(dict(BLOG_NEW, **{"https://guifav.dev/blog/": ""})))
        e = I.built_entries(self.site)
        self.assertEqual(I.changed(e, e, self.site), ["https://guifav.dev/blog/"])

    def test_page_without_lastmod_uses_cache_busting(self):
        self.w("blog/sitemap.xml", urlset(dict(BLOG_NEW, **{"https://guifav.dev/blog/": ""})))
        web = FakeWeb({"https://guifav.dev/blog/": (self.site / "blog/index.html").read_bytes()})
        e = I.built_entries(self.site)
        self.assertEqual(I.changed(e, e, self.site, web), [])
        self.assertTrue(web.calls[0].startswith("https://guifav.dev/blog/?indexnow="), web.calls)

    def test_removed_from_sitemap_because_of_noindex_is_announced(self):
        # Post que virou index: false: continua publicado, mas com noindex; o buscador precisa recrawlear.
        old = dict(BLOG_NEW, **{"https://guifav.dev/blog/virou-podcast/": "2026-01-01T00:00:00.000Z"})
        self.w("blog/virou-podcast/index.html", NOINDEX)
        web = FakeWeb(dict(self.built(), **{"https://guifav.dev/blog/sitemap.xml": urlset(old)}))
        self.assertEqual(I.plan(self.site, web)["urls"], ["https://guifav.dev/blog/virou-podcast/"])

    def test_removed_from_sitemap_but_still_indexable_is_not_announced(self):
        old = dict(BLOG_NEW, **{"https://guifav.dev/blog/pagina/2/": ""})
        web = FakeWeb(dict(self.built(), **{"https://guifav.dev/blog/sitemap.xml": urlset(old)}))
        self.assertEqual(I.plan(self.site, web)["urls"], [])

    def test_removed_non_html_that_still_exists_is_not_announced(self):
        # Arquivo que não é página: não se lê meta robots dele, mesmo que o texto tenha uma.
        self.w("exemplo.txt", NOINDEX)
        self.assertEqual(I.changed({"https://guifav.dev/exemplo.txt": ""}, {}, self.site), [])

    def test_live_unreadable_falls_back_to_all_urls(self):
        cases = {
            "fora do ar": FakeWeb({}),
            "não é xml": FakeWeb({"https://guifav.dev/sitemap.xml": b"<html>"}),
            "não é sitemap": FakeWeb({"https://guifav.dev/sitemap.xml": b"<rss/>"}),
            "filho fora do site": FakeWeb({"https://guifav.dev/sitemap.xml": index(["https://outro.dev/s.xml"]),
                                           "https://outro.dev/s.xml": urlset(BLOG_NEW)}),
            "status de erro com corpo de sitemap": ErrorWeb({"https://guifav.dev/sitemap.xml": urlset(SITE_NEW),
                                                            "https://guifav.dev/blog/sitemap.xml": urlset(BLOG_NEW)}),
            "índice dentro de índice": FakeWeb({"https://guifav.dev/sitemap.xml": index(["https://guifav.dev/s.xml"]),
                                                "https://guifav.dev/s.xml": index([])}),
            "blog antigo fora do ar": FakeWeb({"https://guifav.dev/sitemap.xml": urlset(SITE_OLD)}),
        }
        for name, web in cases.items():
            with self.subTest(name=name):
                out = io.StringIO()
                with redirect_stdout(out):
                    p = I.plan(self.site, web)
                self.assertEqual((p["mode"], p["urls"], p["incomplete"]), ("all", sorted({**SITE_NEW, **BLOG_NEW}), True))
                self.assertEqual(p["sitemaps"], self.shas())
                self.assertIn("::warning::", out.getvalue())

    def test_built_side_must_be_an_index_of_local_sitemaps(self):
        self.w("sitemap.xml", urlset(SITE_NEW))
        with self.assertRaisesRegex(ValueError, "índice"):
            I.built_entries(self.site)
        self.w("sitemap.xml", index(["https://guifav.dev/falta.xml"]))
        with self.assertRaisesRegex(ValueError, "não está no _site"):
            I.built_entries(self.site)

    def test_local_path_mapping(self):
        self.assertEqual(I._local(self.site, "https://guifav.dev/"), self.site / "index.html")
        self.assertEqual(I._local(self.site, "https://guifav.dev/a/b/"), self.site / "a/b/index.html")
        self.assertEqual(I._local(self.site, "https://guifav.dev/x.html"), self.site / "x.html")
        self.assertIsNone(I._local(self.site, "https://outro.dev/"))
        self.assertIsNone(I._local(self.site, "http://guifav.dev/"))

    def test_removed_url_of_another_host_is_ignored(self):
        self.assertEqual(I.changed({"https://outro.dev/x/": ""}, {}, self.site), [])

    def test_cli_diff_writes_plan_file(self):
        out = self.site / "plano" / "plan.json"
        with redirect_stdout(io.StringIO()):
            self.assertEqual(I.main(["diff", "--site", str(self.site), "--out", str(out)], get=self.old_web()), 0)
        p = json.loads(out.read_text())
        self.assertTrue(I.valid_plan(p))
        self.assertEqual(len(p["urls"]), 5)


class GitRemovalsTest(Site):
    """No fallback, as URLs que saíram do índice vêm do git diff contra o commit publicado antes."""

    def setUp(self):
        super().setUp()
        self.repo = Path(self.tmp.name) / "repo"
        posts = self.repo / "blog-src/src/content/posts"
        posts.mkdir(parents=True)
        for name in ("apagado", "virou-podcast", "editado"):
            (posts / f"{name}.md").write_text(f"---\nslug: {name}\n---\n", encoding="utf-8")
        (posts / "sub").mkdir()
        (posts / "sub" / "aninhado.md").write_text("x", encoding="utf-8")
        (self.repo / "velha.html").write_text("<html></html>", encoding="utf-8")
        (self.repo / "fica.html").write_text("<html></html>", encoding="utf-8")
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "antes")
        self.before = self.git("rev-parse", "HEAD").strip()
        (posts / "apagado.md").unlink()
        (posts / "sub" / "aninhado.md").unlink()
        (self.repo / "velha.html").unlink()
        (posts / "virou-podcast.md").write_text("---\nslug: virou-podcast\nindex: false\n---\n", encoding="utf-8")
        (posts / "editado.md").write_text("---\nslug: editado\n---\nnovo\n", encoding="utf-8")
        (self.repo / "fica.html").write_text("<html>v2</html>", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "depois")
        # _site do build novo: o podcast com noindex, o editado e a fica.html indexáveis; o resto sumiu.
        self.w("blog/virou-podcast/index.html", NOINDEX)
        self.w("blog/editado/index.html", b"<html></html>")
        self.w("fica.html", b"<html>v2</html>")

    def git(self, *args):
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True, env=env, capture_output=True, text=True).stdout

    def test_removals_from_git(self):
        self.assertEqual(I.git_removals(self.repo, self.before, self.site), [
            "https://guifav.dev/blog/apagado/", "https://guifav.dev/blog/virou-podcast/", "https://guifav.dev/velha.html"])

    def test_unknown_before(self):
        for before in (None, "", "0" * 40, "f" * 40):
            with self.subTest(before=before):
                self.assertIsNone(I.git_removals(self.repo, before, self.site))

    def test_fallback_plan_includes_removals(self):
        out = io.StringIO()
        with redirect_stdout(out):
            p = I.plan(self.site, FakeWeb({}), self.repo, self.before)
        self.assertEqual(p["mode"], "all")
        self.assertFalse(p["incomplete"])
        self.assertEqual(p["urls"], sorted({**SITE_NEW, **BLOG_NEW} | {
            "https://guifav.dev/blog/apagado/": "", "https://guifav.dev/blog/virou-podcast/": "", "https://guifav.dev/velha.html": ""}))
        self.assertIn("3 que saíram do índice", out.getvalue())

    def test_fallback_without_before_is_incomplete(self):
        with redirect_stdout(io.StringIO()):
            p = I.plan(self.site, FakeWeb({}), self.repo, None)
        self.assertTrue(p["incomplete"])

    def test_normal_diff_ignores_git(self):
        calls = []
        p = I.plan(self.site, FakeWeb(self.built()), self.repo, self.before, git=lambda *a: calls.append(a))
        self.assertEqual((p["mode"], p["incomplete"], calls), ("diff", False, []))

    def test_cli_reads_before_from_env(self):
        out = self.site / "plan.json"
        os.environ["INDEXNOW_BEFORE"] = self.before
        self.addCleanup(os.environ.pop, "INDEXNOW_BEFORE", None)
        with redirect_stdout(io.StringIO()):
            I.main(["diff", "--site", str(self.site), "--out", str(out), "--repo", str(self.repo)], get=FakeWeb({}))
        self.assertIn("https://guifav.dev/blog/apagado/", json.loads(out.read_text())["urls"])


class _Truncating(http.server.BaseHTTPRequestHandler):
    """Anuncia 1000 bytes e entrega 3 (reprodução do achado da r1)."""

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "1000")
        self.end_headers()
        self.wfile.write(b"abc")
        self.wfile.flush()
        self.close_connection = True

    def log_message(self, format, *args):
        pass


class TransportTest(unittest.TestCase):
    def test_truncated_response_becomes_status_0(self):
        srv = http.server.HTTPServer(("127.0.0.1", 0), _Truncating)
        t = threading.Thread(target=srv.handle_request, daemon=True)
        t.start()
        status, body = I.http_get(f"http://127.0.0.1:{srv.server_address[1]}/sitemap.xml")
        t.join(5)
        srv.server_close()
        self.assertEqual(status, 0)
        self.assertIn(b"IncompleteRead", body)

    def test_connection_refused_becomes_status_0(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        self.assertEqual(I.http_get(f"http://127.0.0.1:{port}/")[0], 0)
        self.assertEqual(I.http_post(f"http://127.0.0.1:{port}/", {"a": 1})[0], 0)

    def test_plan_with_truncating_live_site_falls_back(self):
        def get(url):
            return I._transport(lambda: (_ for _ in ()).throw(I.http.client.IncompleteRead(b"abc", 997)))
        with tempfile.TemporaryDirectory() as d:
            site = Path(d)
            (site / "blog").mkdir()
            (site / "sitemap.xml").write_bytes(INDEX)
            (site / "sitemap-site.xml").write_bytes(urlset(SITE_NEW))
            (site / "blog" / "sitemap.xml").write_bytes(urlset(BLOG_NEW))
            with redirect_stdout(io.StringIO()):
                self.assertEqual(I.plan(site, get)["mode"], "all")


class SubmitTest(Site):
    def live(self, **override: bytes) -> FakeWeb:
        pages = dict(self.built(), **{I.KEY_LOCATION: I.KEY.encode()})
        pages.update(override)
        return FakeWeb(pages)

    def plan_file(self, plan) -> str:
        f = self.site / "plan.json"
        f.write_text(plan if isinstance(plan, str) else json.dumps(plan), encoding="utf-8")
        return str(f)

    def run_submit(self, plan, web, post: FakePost, timeout: str = "60"):
        sleeps: list[float] = []
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = I.main(["submit", "--plan", self.plan_file(plan), "--timeout", timeout], get=web, post=post, sleep=sleeps.append)
        return rc, sleeps, out.getvalue() + err.getvalue()

    def sitemaps_503(self):
        """Sitemaps com o corpo certo mas status 503 (erro do CDN); a chave responde 200."""
        web = self.live()

        def get(url):
            status, body = web(url)
            return (503, body) if "sitemap" in url and status == 200 else (status, body)
        return get

    def plan(self, urls, mode="diff", incomplete=False):
        return {"mode": mode, "urls": urls, "sitemaps": self.shas(), "incomplete": incomplete}

    def test_submits_after_live_matches(self):
        post = FakePost([202])
        rc, sleeps, _ = self.run_submit(self.plan(["https://guifav.dev/blog/novo/"]), self.live(), post)
        self.assertEqual((rc, sleeps), (0, []))
        self.assertEqual(post.bodies, [{"host": "guifav.dev", "key": I.KEY, "keyLocation": I.KEY_LOCATION,
                                        "urlList": ["https://guifav.dev/blog/novo/"]}])

    def test_mode_all_also_submits(self):
        post = FakePost()
        urls = sorted({**SITE_NEW, **BLOG_NEW})
        rc, _, _ = self.run_submit(self.plan(urls, mode="all"), self.live(), post)
        self.assertEqual((rc, post.bodies[0]["urlList"]), (0, urls))

    def test_cache_busting_query_on_every_sitemap(self):
        web = self.live()
        self.run_submit(self.plan(["https://guifav.dev/"]), web, FakePost())
        for p in I.SITEMAPS:
            self.assertTrue(any(c.startswith(f"https://guifav.dev{p}?indexnow=") for c in web.calls), (p, web.calls))
        nonces = {c.split("=", 1)[1] for c in web.calls if "?indexnow=" in c}
        self.assertEqual(len(nonces), 1)  # uma rodada, mesmo nonce

    def test_waits_until_every_sitemap_is_live(self):
        # Índice igual ao antigo (sem lastmod, ele quase nunca muda), filho do blog ainda antigo (r1, Astra e Kimi).
        web = self.live(**{"https://guifav.dev/blog/sitemap.xml": urlset(BLOG_OLD)})
        post = FakePost()
        rounds = []

        def get(url):
            if "/sitemap.xml?" in url and url.startswith("https://guifav.dev/sitemap.xml"):
                rounds.append(url)
                if len(rounds) == 3:  # o filho novo chega na 3a rodada
                    web.pages["https://guifav.dev/blog/sitemap.xml"] = (self.site / "blog/sitemap.xml").read_bytes()
            return web(url)

        rc, sleeps, _ = self.run_submit(self.plan(["https://guifav.dev/"]), get, post)
        self.assertEqual(rc, 0)
        self.assertEqual(sleeps, [15, 15])
        self.assertEqual(len(post.bodies), 1)

    def test_gives_up_without_posting(self):
        old_child = {"https://guifav.dev/sitemap-site.xml": urlset(SITE_OLD)}
        for name, web in {"filho antigo com índice igual": self.live(**old_child),
                          "índice antigo": self.live(**{"https://guifav.dev/sitemap.xml": b"antigo"}),
                          "chave errada": self.live(**{I.KEY_LOCATION: b"outra"}),
                          "chave fora do ar": FakeWeb(self.built()),
                          "erro com corpo certo": ErrorWeb(dict(self.built(), **{I.KEY_LOCATION: I.KEY.encode()})),
                          "sitemaps com erro e chave ok": self.sitemaps_503()}.items():
            with self.subTest(name=name):
                post = FakePost()
                rc, sleeps, text = self.run_submit(self.plan(["https://guifav.dev/"]), web, post, timeout="30")
                self.assertEqual(rc, 1)
                self.assertEqual(post.bodies, [])
                self.assertEqual(sleeps, [15, 15])
                self.assertIn("não ficaram iguais", text)

    def test_incomplete_plan_sends_then_fails(self):
        post = FakePost()
        rc, _, text = self.run_submit(self.plan(["https://guifav.dev/"], mode="all", incomplete=True), self.live(), post)
        self.assertEqual((rc, len(post.bodies)), (1, 1))
        self.assertIn("indexnow.py urls", text)

    def test_empty_plan_does_nothing(self):
        web, post = self.live(), FakePost()
        rc, _, text = self.run_submit(self.plan([]), web, post)
        self.assertEqual((rc, web.calls, post.bodies), (0, [], []))
        self.assertIn("nada mudou", text)

    def test_invalid_plan(self):
        good = self.plan(["https://guifav.dev/"])
        bad = [
            "", "x", "{}", "[]",
            dict(good, mode="tudo"), dict(good, urls="a"), dict(good, urls=[1]),
            dict(good, sitemaps={"/sitemap.xml": good["sitemaps"]["/sitemap.xml"]}),
            dict(good, sitemaps=dict(good["sitemaps"], **{"/sitemap.xml": "curto"})),
            dict(good, sitemaps=dict(good["sitemaps"], **{"/outro.xml": "0" * 64})),
            {k: v for k, v in good.items() if k != "incomplete"}, dict(good, incomplete="nao"),
        ]
        for raw in bad:
            with self.subTest(raw=raw):
                post = FakePost()
                rc, _, text = self.run_submit(raw, self.live(), post)
                self.assertEqual((rc, post.bodies), (1, []))
                self.assertRegex(text, "plano (inválido|ilegível)")

    def test_missing_plan_file(self):
        out = io.StringIO()
        with redirect_stderr(out):
            self.assertEqual(I.main(["submit", "--plan", str(self.site / "nao-existe.json")], get=self.live(), post=FakePost()), 1)
        self.assertIn("plano ilegível", out.getvalue())

    def test_rejected_status_fails(self):
        for st in (400, 403, 422, 429, 0):
            with self.subTest(st=st):
                rc, _, text = self.run_submit(self.plan(["https://guifav.dev/"]), self.live(), FakePost([st]))
                self.assertEqual(rc, 1)
                self.assertIn(f"HTTP {st}", text)

    def test_foreign_url_never_posted(self):
        post = FakePost()
        rc, _, text = self.run_submit(self.plan(["https://guifav.dev/", "https://outro.dev/x"]), self.live(), post)
        self.assertEqual((rc, post.bodies), (1, []))
        self.assertIn("fora de guifav.dev", text)

    def test_batches_of_10000(self):
        urls = [f"https://guifav.dev/blog/p{i}/" for i in range(10_001)]
        post = FakePost([200, 200])
        with redirect_stdout(io.StringIO()):
            self.assertEqual(I.submit(urls, post), [])
        self.assertEqual([len(b["urlList"]) for b in post.bodies], [10_000, 1])

    def test_big_plan_goes_through_file(self):
        # O plano vai por arquivo (artefato), sem limite de variável de ambiente nem de output de job (r1, P3).
        urls = [f"https://guifav.dev/blog/{'x' * 100}{i}/" for i in range(10_001)]
        post = FakePost([200, 200])
        rc, _, _ = self.run_submit(self.plan(urls), self.live(), post)
        self.assertEqual((rc, [len(b["urlList"]) for b in post.bodies]), (0, [10_000, 1]))


class AllTest(Site):
    def test_all_submits_every_live_url(self):
        web = FakeWeb(dict(self.built(), **{I.KEY_LOCATION: I.KEY.encode() + b"\n"}))
        post = FakePost()
        with redirect_stdout(io.StringIO()):
            self.assertEqual(I.main(["all"], get=web, post=post), 0)
        self.assertEqual(post.bodies[0]["urlList"], sorted({**SITE_NEW, **BLOG_NEW}))

    def test_all_requires_live_key(self):
        sitemaps = {"https://guifav.dev/sitemap.xml": urlset(SITE_NEW), "https://guifav.dev/blog/sitemap.xml": urlset(BLOG_NEW)}
        for name, extra in {"sem chave": {}, "chave errada": {I.KEY_LOCATION: b"outra"}}.items():
            with self.subTest(name=name):
                post, err = FakePost(), io.StringIO()
                with redirect_stderr(err):
                    self.assertEqual(I.main(["all"], get=FakeWeb({**sitemaps, **extra}), post=post), 1)
                self.assertEqual(post.bodies, [])
                self.assertIn("chave fora do ar", err.getvalue())


class UrlsTest(Site):
    def test_urls_command(self):
        web = FakeWeb({I.KEY_LOCATION: I.KEY.encode()})
        post = FakePost()
        with redirect_stdout(io.StringIO()):
            rc = I.main(["urls", "https://guifav.dev/blog/b/", "https://guifav.dev/blog/a/", "https://guifav.dev/blog/a/"], get=web, post=post)
        self.assertEqual((rc, post.bodies[0]["urlList"]), (0, ["https://guifav.dev/blog/a/", "https://guifav.dev/blog/b/"]))

    def test_urls_command_guards(self):
        for name, web, urls in (("sem chave", FakeWeb({}), ["https://guifav.dev/x/"]),
                                ("outro host", FakeWeb({I.KEY_LOCATION: I.KEY.encode()}), ["https://outro.dev/x/"])):
            with self.subTest(name=name):
                post = FakePost()
                with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
                    self.assertEqual(I.main(["urls", *urls], get=web, post=post), 1)
                self.assertEqual(post.bodies, [])


class RepoTest(unittest.TestCase):
    """A chave publicada é a do script e vai ao ar pelo manifesto; o workflow passa o plano por artefato."""

    def test_key_file(self):
        self.assertRegex(I.KEY, r"^[a-zA-Z0-9-]{8,128}$")
        self.assertEqual((REPO / f"{I.KEY}.txt").read_text(encoding="utf-8").strip(), I.KEY)
        manifest = (REPO / "site-manifest.txt").read_text(encoding="utf-8").splitlines()
        self.assertIn(f"{I.KEY}.txt", manifest)
        self.assertNotIn("sitemap.xml", manifest)

    def test_workflow_wiring(self):
        y = (REPO / ".github/workflows/pages.yml").read_text(encoding="utf-8")
        if not y:
            self.skipTest("workflow ausente")
        jobs = y.split("\n  indexnow:\n")
        self.assertEqual(len(jobs), 2, "job indexnow")
        build, indexnow = jobs
        # O plano é opcional para o deploy: passo com continue-on-error e upload só se ele deu certo.
        self.assertRegex(build, r"id: indexnow\n\s+# [^\n]*\n\s+continue-on-error: true\n")
        self.assertIn("if: steps.indexnow.outcome == 'success'", build)
        upload = build.split("Guardar o plano do IndexNow")[1].split("\n      - ")[0]
        self.assertIn("continue-on-error: true", upload)  # falha do upload não pode barrar o deploy (r2)
        self.assertIn("if-no-files-found: error", upload)
        self.assertIn("INDEXNOW_BEFORE: ${{ github.event.before }}", build)
        self.assertIn("uses: actions/upload-artifact@", build)
        self.assertNotIn("GITHUB_OUTPUT", build)
        self.assertIn("fetch-depth: 0", build)
        self.assertIn("needs: [build, deploy]", indexnow)
        self.assertIn("uses: actions/download-artifact@", indexnow)
        self.assertIn("scripts/build_sitemaps.py", indexnow)  # import do indexnow.py no checkout esparso
        self.assertIn('submit --plan "$RUNNER_TEMP/indexnow/plan.json"', indexnow)
        self.assertNotIn("indexnow", build.split("\n  deploy:\n")[1].split("needs:")[1].split("\n")[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)

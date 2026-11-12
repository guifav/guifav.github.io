"""Testes do IndexNow (scripts/indexnow.py). Sem rede: GET e POST são simulados como a API real responde.

Rodar: python3 scripts/test_indexnow.py
"""
import io
import json
import os
import tempfile
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


SITE_OLD = {"https://guifav.dev/": "", "https://guifav.dev/portfolio/": ""}
BLOG_OLD = {"https://guifav.dev/blog/": "2026-09-01T00:00:00.000Z", "https://guifav.dev/blog/pagina/2/": "",
            "https://guifav.dev/blog/a/": "2026-01-01T00:00:00.000Z", "https://guifav.dev/blog/sumiu/": "2025-01-01T00:00:00.000Z"}
SITE_NEW = {"https://guifav.dev/": "2026-11-12T10:00:00-03:00", "https://guifav.dev/portfolio/": "2026-11-12T10:00:00-03:00"}
BLOG_NEW = {"https://guifav.dev/blog/": "2026-11-12T00:00:00.000Z", "https://guifav.dev/blog/a/": "2026-01-01T00:00:00.000Z",
            "https://guifav.dev/blog/novo/": "2026-11-12T00:00:00.000Z"}


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
        self.w("sitemap.xml", index(["https://guifav.dev/sitemap-site.xml", "https://guifav.dev/blog/sitemap.xml"]))
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


class DiffTest(Site):
    def test_first_deploy_from_old_layout(self):
        p = I.plan(self.site, self.old_web())
        self.assertEqual(p["urls"], sorted([
            "https://guifav.dev/", "https://guifav.dev/portfolio/",  # ganharam lastmod
            "https://guifav.dev/blog/",  # lastmod mudou
            "https://guifav.dev/blog/novo/",  # nova
            "https://guifav.dev/blog/sumiu/",  # saiu e a página não existe mais
        ]))  # /blog/a/ igual e /blog/pagina/2/ só saiu do sitemap: ficam de fora
        self.assertEqual(p["sitemap_sha256"], I.sha256((self.site / "sitemap.xml").read_bytes()))

    def test_nothing_changed(self):
        web = FakeWeb({"https://guifav.dev/sitemap.xml": (self.site / "sitemap.xml").read_bytes(),
                       "https://guifav.dev/sitemap-site.xml": urlset(SITE_NEW),
                       "https://guifav.dev/blog/sitemap.xml": urlset(BLOG_NEW)})
        self.assertEqual(I.plan(self.site, web)["urls"], [])

    def test_one_post_updated_from_index_layout(self):
        blog_live = dict(BLOG_NEW, **{"https://guifav.dev/blog/a/": "2025-12-01T00:00:00.000Z"})
        web = FakeWeb({"https://guifav.dev/sitemap.xml": index(["https://guifav.dev/sitemap-site.xml", "https://guifav.dev/blog/sitemap.xml"]),
                       "https://guifav.dev/sitemap-site.xml": urlset(SITE_NEW), "https://guifav.dev/blog/sitemap.xml": urlset(blog_live)})
        self.assertEqual(I.plan(self.site, web)["urls"], ["https://guifav.dev/blog/a/"])

    def test_live_unreadable_gives_empty_plan_with_warning(self):
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
                self.assertEqual(p["urls"], [])
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

    def test_cli_diff_writes_single_line_json(self):
        out = self.site / "plan.json"
        with redirect_stdout(io.StringIO()):
            self.assertEqual(I.main(["diff", "--site", str(self.site), "--out", str(out)], get=self.old_web()), 0)
        raw = out.read_text()
        self.assertNotIn("\n", raw)
        self.assertEqual(len(json.loads(raw)["urls"]), 5)


class SubmitTest(Site):
    def live(self, sitemap: bytes | None = None, key: bytes | None = None) -> FakeWeb:
        return FakeWeb({"https://guifav.dev/sitemap.xml": sitemap if sitemap is not None else (self.site / "sitemap.xml").read_bytes(),
                        I.KEY_LOCATION: key if key is not None else I.KEY.encode()})

    def run_submit(self, plan: dict | str, web, post: FakePost, timeout: str = "60"):
        os.environ["INDEXNOW_PLAN"] = plan if isinstance(plan, str) else json.dumps(plan)
        self.addCleanup(os.environ.pop, "INDEXNOW_PLAN", None)
        sleeps: list[float] = []
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = I.main(["submit", "--timeout", timeout], get=web, post=post, sleep=sleeps.append)
        return rc, sleeps, out.getvalue() + err.getvalue()

    def plan(self, urls):
        return {"urls": urls, "sitemap_sha256": I.sha256((self.site / "sitemap.xml").read_bytes())}

    def test_submits_after_live_matches(self):
        post = FakePost([202])
        rc, sleeps, _ = self.run_submit(self.plan(["https://guifav.dev/blog/novo/"]), self.live(), post)
        self.assertEqual((rc, sleeps), (0, []))
        self.assertEqual(post.bodies, [{"host": "guifav.dev", "key": I.KEY, "keyLocation": I.KEY_LOCATION,
                                        "urlList": ["https://guifav.dev/blog/novo/"]}])

    def test_cache_busting_query_on_sitemap(self):
        web = self.live()
        self.run_submit(self.plan(["https://guifav.dev/"]), web, FakePost())
        self.assertTrue(web.calls[0].startswith("https://guifav.dev/sitemap.xml?indexnow="), web.calls)

    def test_waits_until_deploy_is_live(self):
        web = self.live(sitemap=b"antigo")
        post = FakePost()
        polls = []

        def get(url):
            polls.append(url)
            if len(polls) >= 5:  # o deploy chega na 3a rodada (2 GETs por rodada)
                web.pages["https://guifav.dev/sitemap.xml"] = (self.site / "sitemap.xml").read_bytes()
            return web(url)

        rc, sleeps, _ = self.run_submit(self.plan(["https://guifav.dev/"]), get, post)
        self.assertEqual(rc, 0)
        self.assertEqual(sleeps, [15, 15])
        self.assertEqual(len(post.bodies), 1)

    def test_gives_up_without_posting(self):
        for name, web in {"sitemap antigo": self.live(sitemap=b"antigo"), "chave errada": self.live(key=b"outra"),
                          "chave fora do ar": FakeWeb({"https://guifav.dev/sitemap.xml": (self.site / "sitemap.xml").read_bytes()})}.items():
            with self.subTest(name=name):
                post = FakePost()
                rc, sleeps, text = self.run_submit(self.plan(["https://guifav.dev/"]), web, post, timeout="30")
                self.assertEqual(rc, 1)
                self.assertEqual(post.bodies, [])
                self.assertEqual(sleeps, [15, 15])
                self.assertIn("não ficou igual", text)

    def test_empty_plan_does_nothing(self):
        web, post = self.live(), FakePost()
        rc, _, text = self.run_submit(self.plan([]), web, post)
        self.assertEqual((rc, web.calls, post.bodies), (0, [], []))
        self.assertIn("nada mudou", text)

    def test_invalid_plan(self):
        for raw in ("", "x", "{}", '{"urls": "a", "sitemap_sha256": "s"}', '{"urls": [1], "sitemap_sha256": "s"}',
                    '{"urls": [], "sitemap_sha256": 3}'):
            with self.subTest(raw=raw):
                post = FakePost()
                rc, _, text = self.run_submit(raw, self.live(), post)
                self.assertEqual((rc, post.bodies), (1, []))
                self.assertIn("plano inválido", text)

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


class AllTest(Site):
    def test_all_submits_every_live_url(self):
        web = FakeWeb({"https://guifav.dev/sitemap.xml": index(["https://guifav.dev/sitemap-site.xml", "https://guifav.dev/blog/sitemap.xml"]),
                       "https://guifav.dev/sitemap-site.xml": urlset(SITE_NEW), "https://guifav.dev/blog/sitemap.xml": urlset(BLOG_NEW),
                       I.KEY_LOCATION: I.KEY.encode() + b"\n"})
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


class KeyFileTest(unittest.TestCase):
    """A chave publicada é a do script, vai ao ar pelo manifesto e segue o formato do protocolo."""

    def test_key_file(self):
        self.assertRegex(I.KEY, r"^[a-zA-Z0-9-]{8,128}$")
        self.assertEqual((REPO / f"{I.KEY}.txt").read_text(encoding="utf-8").strip(), I.KEY)
        manifest = (REPO / "site-manifest.txt").read_text(encoding="utf-8").splitlines()
        self.assertIn(f"{I.KEY}.txt", manifest)
        self.assertNotIn("sitemap.xml", manifest)


if __name__ == "__main__":
    unittest.main(verbosity=2)

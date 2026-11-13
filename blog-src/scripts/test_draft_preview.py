"""Testes do scripts/draft_preview.py (issue #62). Rodar: python3 blog-src/scripts/test_draft_preview.py

Sem rede e sem build: o build e o wrangler entram por um executor falso. O build real é conferido à mão no PR.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import draft_preview as D  # noqa: E402

SLUG = "sales-coach"
GOOD = (
    '<!DOCTYPE html><html><head><meta name="robots" content="noindex, nofollow">'
    '<meta property="og:image" content="https://guifav.dev/blog/img/sales-coach/capa.jpg"></head><body>'
    '<img src="/blog/img/sales-coach/a-1456.webp" srcset="/blog/img/sales-coach/a-728.webp 728w, /blog/img/sales-coach/a-1456.webp 1456w">'
    '<video src="/blog/video/sales-coach.mp4" poster="/blog/img/sales-coach/capa.jpg"></video>'
    '<a href="/blog/outro-post/">outro</a><a href="/blog/img/outro-post/x.webp">outra imagem</a></body></html>'
)


def dist_with(root: Path, html: str = GOOD, files=("img/sales-coach/a-1456.webp", "img/sales-coach/a-728.webp",
                                                   "img/sales-coach/capa.jpg", "video/sales-coach.mp4")) -> Path:
    d = root / "dist"
    (d / SLUG).mkdir(parents=True)
    (d / SLUG / "index.html").write_text(html, encoding="utf-8")
    for f in files:
        (d / f).parent.mkdir(parents=True, exist_ok=True)
        (d / f).write_bytes(f.encode())
    # Arquivo de outro post e vídeo de outro slug com o mesmo começo: nunca entram na prévia.
    (d / "img" / "outro-post").mkdir(parents=True, exist_ok=True)
    (d / "img" / "outro-post" / "x.webp").write_bytes(b"x")
    (d / "video").mkdir(exist_ok=True)
    (d / "video" / "sales-coach-2.mp4").write_bytes(b"x")
    return d


class Fake:
    """Executor falso: grava cada chamada e devolve o que o teste mandar."""

    def __init__(self, rc=0, stdout=b"", stderr=b""):
        self.calls = []
        self.rc, self.stdout, self.stderr = rc, stdout, stderr

    def __call__(self, args, **kw):
        self.calls.append((list(args), kw))
        return subprocess.CompletedProcess(args, self.rc, self.stdout, self.stderr)


class CollectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_rewrites_only_the_draft_files_and_collects_them(self):
        p = D.collect(dist_with(self.root), SLUG, "Sales Coach")
        html = p.html.decode()
        self.assertIn('src="/admin/rascunhos/sales-coach/img/sales-coach/a-1456.webp"', html)
        self.assertIn("/admin/rascunhos/sales-coach/img/sales-coach/a-728.webp 728w, "
                      "/admin/rascunhos/sales-coach/img/sales-coach/a-1456.webp 1456w", html)
        self.assertIn('<video src="/admin/rascunhos/sales-coach/video/sales-coach.mp4"', html)
        self.assertIn('content="https://guifav.dev/admin/rascunhos/sales-coach/img/sales-coach/capa.jpg"', html)
        # Links de outros posts e arquivos de outros posts ficam como estão.
        self.assertIn('<a href="/blog/outro-post/">', html)
        self.assertIn('<a href="/blog/img/outro-post/x.webp">', html)
        self.assertEqual(sorted(p.files), ["img/sales-coach/a-1456.webp", "img/sales-coach/a-728.webp",
                                           "img/sales-coach/capa.jpg", "video/sales-coach.mp4"])
        self.assertEqual(p.files["img/sales-coach/a-728.webp"], (b"img/sales-coach/a-728.webp", "image/webp"))
        self.assertEqual(p.files["img/sales-coach/capa.jpg"][1], "image/jpeg")
        self.assertEqual(p.files["video/sales-coach.mp4"][1], "video/mp4")
        self.assertNotIn("/blog/img/sales-coach/", html)

    def test_refuses_analytics_missing_noindex_dangling_file_or_missing_page(self):
        cases = {
            "gtag": GOOD.replace("</head>", '<script async src="https://www.googletagmanager.com/gtag/js?id=G-X"></script></head>'),
            "clarity": GOOD.replace("</head>", '<script>t.src="https://www.clarity.ms/tag/"+i</script></head>'),
            "track": GOOD.replace("</body>", '<script src="/assets/track.js?v=1"></script></body>'),
            "config": GOOD.replace("</head>", "<script>gtag('config','G-X')</script></head>"),
            "indexável": GOOD.replace('content="noindex, nofollow"', 'content="index, follow"'),
            "sem robots": GOOD.replace('<meta name="robots" content="noindex, nofollow">', ""),
            "arquivo ausente": GOOD.replace("a-728.webp 728w", "nao-existe.webp 728w"),
        }
        for name, html in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as t:
                with self.assertRaises(D.DraftError):
                    D.collect(dist_with(Path(t), html), SLUG, "x")
        with self.assertRaisesRegex(D.DraftError, "não gerou"):
            D.collect(self.root / "vazio", SLUG, "x")

    def test_refuses_unknown_file_types(self):
        with self.assertRaisesRegex(D.DraftError, "tipo de arquivo fora da lista"):
            D.collect(dist_with(self.root, files=("img/sales-coach/a-1456.webp", "img/sales-coach/a-728.webp",
                                                  "img/sales-coach/capa.jpg", "video/sales-coach.mp4", "img/sales-coach/x.exe")),
                      SLUG, "x")


class SourceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.md = self.root / f"{SLUG}.md"
        self.md.write_text('---\ntitle: "Sales Coach: o app"\ndescription: d\n---\ntexto\n', encoding="utf-8")
        self.imgs = self.root / "imgs"
        self.imgs.mkdir()
        (self.imgs / "a-728.webp").write_bytes(b"A")

    def tearDown(self):
        self.tmp.cleanup()

    def test_hash_changes_with_text_image_bytes_and_image_name(self):
        h0 = D.source_sha256(self.md, self.imgs)
        self.assertEqual(h0, D.source_sha256(self.md, self.imgs))
        self.assertRegex(h0, r"^[0-9a-f]{64}$")
        self.assertNotEqual(h0, D.source_sha256(self.md, None))
        (self.imgs / "a-728.webp").write_bytes(b"B")
        h1 = D.source_sha256(self.md, self.imgs)
        self.assertNotEqual(h0, h1)
        (self.imgs / "a-728.webp").rename(self.imgs / "b-728.webp")
        self.assertNotEqual(h1, D.source_sha256(self.md, self.imgs))
        self.md.write_text(self.md.read_text() + "mais\n", encoding="utf-8")
        self.assertNotEqual(h1, D.source_sha256(self.md, self.imgs))

    def test_image_names_and_types_are_checked(self):
        for bad in (".oculto.webp", "x.exe", "x y.webp"):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as t:
                d = Path(t)
                (d / bad).write_bytes(b"x")
                with self.assertRaises(D.DraftError):
                    D.image_files(d)
        with self.assertRaisesRegex(D.DraftError, "não existe"):
            D.image_files(self.root / "nao")

    def test_slug_and_title(self):
        self.assertEqual(D.slug_of(self.md), SLUG)
        for bad in ("Sales.md", "sales_coach.md", "sales-coach.txt", "-x.md"):
            with self.assertRaises(D.DraftError):
                D.slug_of(Path(bad))
        self.assertEqual(D.frontmatter_title(self.md.read_text()), "Sales Coach: o app")
        with self.assertRaises(D.DraftError):
            D.frontmatter_title("sem frontmatter")
        with self.assertRaises(D.DraftError):
            D.frontmatter_title("---\ndescription: x\n---\n")

    def test_build_refuses_existing_slug_and_sets_draft_flag(self):
        existing = next((D.BLOG_SRC / "src" / "content" / "posts").glob("*.md"))
        md = self.root / existing.name
        md.write_text("x", encoding="utf-8")
        with self.assertRaisesRegex(D.DraftError, "já existe um post publicado"):
            D.build(md, None, self.root / "w", run=Fake())
        fake = Fake()
        work = self.root / "w2"
        work.mkdir()
        out = D.build(self.md, self.imgs, work, run=fake, env={"PATH": "/usr/bin"})
        self.assertEqual(out, work / "blog-src" / "dist")
        args, kw = fake.calls[0]
        self.assertEqual(args, ["npm", "run", "build"])
        self.assertEqual(kw["env"]["DRAFT_PREVIEW"], "1")
        self.assertTrue((work / "blog-src" / "src" / "content" / "posts" / f"{SLUG}.md").is_file())
        self.assertEqual((work / "blog-src" / "public" / "img" / SLUG / "a-728.webp").read_bytes(), b"A")
        self.assertTrue((work / "blog-src" / "node_modules").is_symlink())
        # O rascunho foi para a cópia, nunca para o blog-src do repo.
        self.assertFalse((D.BLOG_SRC / "src" / "content" / "posts" / f"{SLUG}.md").exists())
        with self.assertRaisesRegex(D.DraftError, "build do rascunho falhou"):
            w3 = self.root / "w3"
            w3.mkdir()
            D.build(self.md, None, w3, run=Fake(rc=1, stdout="erro x"))

    def test_draft_inside_repo_is_refused(self):
        import contextlib
        import io
        inside = D.REPO / "rascunho-de-teste-nao-commitar.md"
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = D.main(["enviar", str(inside)])
        self.assertEqual(rc, 2)
        self.assertIn("fica fora do repo", err.getvalue())


class SourcesTest(unittest.TestCase):
    """O modo prévia só existe no build da KVM: o layout esconde os analytics por ele e o CI nunca o liga."""

    def read(self, rel):
        return (D.REPO / rel).read_text(encoding="utf-8")

    def test_layout_hides_analytics_and_forces_noindex_in_preview(self):
        base = self.read("blog-src/src/layouts/Base.astro")
        self.assertIn("const robots = DRAFT_PREVIEW ? 'noindex, nofollow' : robotsContent(index, PRE_CUTOVER_NOINDEX);", base)
        self.assertEqual(base.count("{!DRAFT_PREVIEW && <script data-site-script is:inline"), 3)
        self.assertIn("export const DRAFT_PREVIEW = process.env.DRAFT_PREVIEW === '1';", self.read("blog-src/src/consts.ts"))

    def test_ci_never_builds_the_preview(self):
        for wf in (D.REPO / ".github" / "workflows").glob("*.yml"):
            self.assertNotIn("DRAFT_PREVIEW", wf.read_text(encoding="utf-8"), wf.name)


class UploadTest(unittest.TestCase):
    def test_manifest_last_and_exact_keys_and_types(self):
        p = D.Preview(SLUG, "Sales Coach", b"<html>", {"img/sales-coach/a.webp": (b"A", "image/webp")})
        man = D.manifest(p, "a" * 64, now=datetime(2026, 11, 13, 19, 0, tzinfo=timezone.utc))
        self.assertEqual(man, {"slug": SLUG, "title": "Sales Coach", "uploaded_at": "2026-11-13T19:00:00.000Z",
                               "source_sha256": "a" * 64,
                               "files": {"index.html": "text/html; charset=utf-8", "img/sales-coach/a.webp": "image/webp"}})
        fake = Fake()
        with tempfile.TemporaryDirectory() as t:
            keys = D.upload(p, man, Path(t), run=fake)
            sent = Path(t) / "upload" / "manifest.json"
            self.assertEqual(json.loads(sent.read_text(encoding="utf-8")), man)
        self.assertEqual(keys, ["blog-rascunhos/sales-coach/img/sales-coach/a.webp", "blog-rascunhos/sales-coach/index.html",
                                "blog-rascunhos/sales-coach/manifest.json"])
        for (args, kw), ctype in zip(fake.calls, ["image/webp", "text/html; charset=utf-8", "application/json"]):
            self.assertEqual(args[:6], ["npx", "--yes", "wrangler@4", "r2", "object", "put"])
            self.assertEqual(args[args.index("--content-type") + 1], ctype)
            self.assertIn("--remote", args)
            self.assertEqual(kw["cwd"], D.ADMIN_WORKER)

    def test_wrangler_failure_stops(self):
        p = D.Preview(SLUG, "t", b"<html>", {})
        with tempfile.TemporaryDirectory() as t, self.assertRaisesRegex(D.DraftError, "wrangler r2 object put falhou"):
            D.upload(p, D.manifest(p, "a" * 64), Path(t), run=Fake(rc=1, stderr=b"sem permissao"))

    def test_approvals_query_and_slug_guard(self):
        fake = Fake(stdout=json.dumps([{"results": [{"html_sha256": "h", "source_sha256": "s"}]}]).encode())
        self.assertEqual(D.approvals(SLUG, run=fake), [{"html_sha256": "h", "source_sha256": "s"}])
        sql = fake.calls[0][0][-1]
        self.assertIn("WHERE slug = 'sales-coach'", sql)
        with self.assertRaises(D.DraftError):
            D.approvals("x'; DROP TABLE draft_approvals; --", run=Fake())


if __name__ == "__main__":
    unittest.main(verbosity=2)

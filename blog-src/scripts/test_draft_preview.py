"""Testes do scripts/draft_preview.py (issue #62). Rodar: python3 blog-src/scripts/test_draft_preview.py

Sem rede e sem build: o build e o wrangler entram por um executor falso. O build real é conferido à mão no PR.
"""
import contextlib
import hashlib
import io
import json
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
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


MD = b'---\ntitle: "Sales Coach"\n---\ntexto\n'


def fonte_de(slug=SLUG, md=MD, imgs=None):
    """Fonte (caminho -> bytes) e o source_sha256 dela, pelo mesmo caminho do script: os arquivos numa pasta no formato
    de fonte/ do R2 e o fonte_sha256 dessa pasta."""
    fonte = {f"{slug}.md": md, **{f"img/{k}": v for k, v in ({"a.webp": b"A"} if imgs is None else imgs).items()}}
    with tempfile.TemporaryDirectory() as t:
        for rel, b in fonte.items():
            (Path(t) / rel).parent.mkdir(parents=True, exist_ok=True)
            (Path(t) / rel).write_bytes(b)
        return fonte, D.fonte_sha256(Path(t), slug)


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
            "consent": GOOD.replace("</body>", '<script src="/assets/consent.js?v=1"></script></body>'),
            # #72: a prévia mora em /admin/...; nenhuma política pode mandar esse caminho para fora (só a origem).
            "referrer unsafe-url": GOOD.replace("</body>", '<iframe src="https://x.example/" referrerpolicy="unsafe-url"></iframe></body>'),
            "referrer downgrade": GOOD.replace("</body>", "<a href='https://x.example/' referrerPolicy='no-referrer-when-downgrade'>x</a></body>"),
            "referrer sem aspas": GOOD.replace("</body>", '<img src="https://x.example/a.png" referrerpolicy=unsafe-url></body>'),
            "meta referrer": GOOD.replace("</head>", '<meta name="referrer" content="unsafe-url"></head>'),
            "rel noreferrer não basta": GOOD.replace("</body>", '<a href="https://x.example/" rel=noopener referrerpolicy="UNSAFE-URL">x</a></body>'),
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

    def test_embeds_of_the_blog_keep_their_referrer_policy(self):
        # O embed do Spotify e do YouTube (strict-origin-when-cross-origin) manda só a origem: passa.
        for pol in ("strict-origin-when-cross-origin", "no-referrer", "Origin"):
            html = GOOD.replace("</body>", f'<iframe src="https://open.spotify.com/embed/x" referrerpolicy="{pol}"></iframe></body>')
            with self.subTest(pol=pol), tempfile.TemporaryDirectory() as t:
                D.collect(dist_with(Path(t), html), SLUG, "x")

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
        # r1 (Astra P3): cada entrada muda o hash isoladamente, a partir da MESMA base.
        h0 = D.source_sha256(self.md, self.imgs)
        self.assertEqual(h0, D.source_sha256(self.md, self.imgs))
        self.assertRegex(h0, r"^[0-9a-f]{64}$")
        self.assertNotEqual(h0, D.source_sha256(self.md, None))
        md0, img = self.md.read_bytes(), self.imgs / "a-728.webp"
        self.md.write_bytes(md0 + b"mais\n")
        self.assertNotEqual(h0, D.source_sha256(self.md, self.imgs), "texto")
        self.md.write_bytes(md0)
        self.assertEqual(h0, D.source_sha256(self.md, self.imgs))
        img.write_bytes(b"B")
        self.assertNotEqual(h0, D.source_sha256(self.md, self.imgs), "bytes da imagem")
        img.write_bytes(b"A")
        self.assertEqual(h0, D.source_sha256(self.md, self.imgs))
        img.rename(self.imgs / "b-728.webp")
        self.assertNotEqual(h0, D.source_sha256(self.md, self.imgs), "nome da imagem")

    def test_snapshot_has_the_r2_layout_and_the_same_source_hash(self):
        # #82: a cópia da fonte tem o formato de <slug>/v/<versão>/fonte/ (<slug>.md e img/<caminho>), e o
        # fonte_sha256 dela é o source_sha256 do original, com e sem imagens: a fatia 3 confere a fonte baixada assim.
        (self.imgs / "a-b").mkdir()
        (self.imgs / "a-b" / "x.png").write_bytes(b"X")
        (self.imgs / "a").mkdir()
        (self.imgs / "a" / "x.png").write_bytes(b"Y")
        pasta = D.snapshot(self.md, self.imgs, self.root / "copia")
        self.assertEqual(sorted(p.relative_to(pasta).as_posix() for p in pasta.rglob("*") if p.is_file()),
                         ["img/a-728.webp", "img/a-b/x.png", "img/a/x.png", f"{SLUG}.md"])
        self.assertEqual(D.fonte_sha256(pasta, SLUG), D.source_sha256(self.md, self.imgs))
        self.assertEqual(D.fonte_files(pasta, SLUG), {f"{SLUG}.md": self.md.read_bytes(), "img/a-728.webp": b"A",
                                                      "img/a-b/x.png": b"X", "img/a/x.png": b"Y"})
        sem = D.snapshot(self.md, None, self.root / "sem-imagens")
        self.assertFalse((sem / "img").exists())
        self.assertEqual(D.fonte_sha256(sem, SLUG), D.source_sha256(self.md, None))
        self.assertEqual(D.fonte_files(sem, SLUG), {f"{SLUG}.md": self.md.read_bytes()})
        # Pasta de imagens vazia dá o mesmo hash que sem imagens, dos dois lados.
        vazia = self.root / "vazia"
        vazia.mkdir()
        self.assertEqual(D.fonte_sha256(D.snapshot(self.md, vazia, self.root / "c3"), SLUG), D.source_sha256(self.md, vazia))
        # Imagem com nome inválido é recusada antes da cópia, pela regra de sempre.
        (self.imgs / "x y.webp").write_bytes(b"x")
        with self.assertRaises(D.DraftError):
            D.snapshot(self.md, self.imgs, self.root / "c4")

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

    def test_linkedin_and_artigo_go_together(self):
        with tempfile.TemporaryDirectory() as t:
            md = Path(t) / "post-linkedin.md"
            md.write_text("---\ntitle: x\n---\n", encoding="utf-8")
            for argv, msg in ((["enviar", str(md), "--linkedin"], "andam juntos"),
                              (["enviar", str(md), "--artigo", "post"], "andam juntos"),
                              (["enviar", str(md), "--artigo", ""], "andam juntos"),  # r1 (Kimi P1): "" também é presença
                              (["enviar", str(md), "--linkedin", "--artigo", ""], "inválido"),
                              (["enviar", str(md), "--linkedin", "--artigo", "post\n"], "inválido")):
                with self.subTest(argv=argv), mock.patch.object(D, "build", side_effect=AssertionError("não chega ao build")):
                    err = io.StringIO()
                    with contextlib.redirect_stderr(err):
                        self.assertEqual(D.main(argv), 2)
                    self.assertIn(msg, err.getvalue())

    def test_enviar_valid_cli_reaches_upload_with_kind_and_parent(self):
        # r2 (Kimi P2, Astra P3): a CLI válida precisa levar kind e parent até o manifesto enviado.
        with tempfile.TemporaryDirectory() as t:
            md = Path(t) / "post-linkedin.md"
            md.write_text("---\ntitle: x\n---\n", encoding="utf-8")
            for argv, kind, parent in ((["enviar", str(md), "--linkedin", "--artigo", "post"], "linkedin", "post"),
                                       (["enviar", str(md)], "blog", None)):
                with self.subTest(argv=argv), \
                        mock.patch.object(D, "build", return_value=Path(t)), \
                        mock.patch.object(D, "collect", return_value=D.Preview("post-linkedin", "x", b"<html>", {})), \
                        mock.patch.object(D, "upload", return_value=["a", "b"]) as up, \
                        contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(D.main(argv), 0)
                    man = up.call_args.args[1]
                    self.assertEqual((man["kind"], man.get("parent")), (kind, parent))
                    # #82: o derivado também leva a fonte, pela mesma regra do artigo.
                    self.assertEqual(up.call_args.args[2], {"post-linkedin.md": md.read_bytes()})
                    self.assertEqual(man["fonte"], {"post-linkedin.md": hashlib.sha256(md.read_bytes()).hexdigest()})

    def test_enviar_builds_hashes_and_uploads_the_same_snapshot_of_the_source(self):
        # #82: a fonte é lida uma vez, para uma cópia no formato de fonte/ do R2. O build sai dessa cópia, o
        # source_sha256 é o dela (igual ao do arquivo original, o que o status confere) e o envio leva os mesmos bytes.
        (self.imgs / "sub").mkdir()
        (self.imgs / "sub" / "b.png").write_bytes(b"PNG")
        seen = {}

        def fake_build(md, images, work, **kw):
            seen["md"], seen["images"] = md, images
            seen["md_bytes"] = md.read_bytes()
            seen["imgs"] = sorted(p.relative_to(images).as_posix() for p in images.rglob("*") if p.is_file())
            return work / "dist"
        with mock.patch.object(D, "build", side_effect=fake_build), \
                mock.patch.object(D, "collect", return_value=D.Preview(SLUG, "x", b"<html>", {})), \
                mock.patch.object(D, "upload", return_value=["a"]) as up, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(D.main(["enviar", str(self.md), "--imagens", str(self.imgs)]), 0)
        self.assertNotEqual(seen["md"], self.md.resolve(), "o build usa a cópia, não o arquivo original")
        self.assertEqual((seen["md"].name, seen["images"].name, seen["md"].parent), (f"{SLUG}.md", "img", seen["images"].parent))
        self.assertEqual(seen["md_bytes"], self.md.read_bytes())
        self.assertEqual(seen["imgs"], ["a-728.webp", "sub/b.png"])
        man, fonte = up.call_args.args[1], up.call_args.args[2]
        self.assertEqual(man["source_sha256"], D.source_sha256(self.md, self.imgs))
        self.assertEqual(fonte, {f"{SLUG}.md": self.md.read_bytes(), "img/a-728.webp": b"A", "img/sub/b.png": b"PNG"})
        self.assertEqual(man["fonte"], {k: hashlib.sha256(v).hexdigest() for k, v in fonte.items()})

    def test_dry_run_writes_the_source_next_to_the_preview(self):
        with tempfile.TemporaryDirectory() as out, \
                mock.patch.object(D, "build", return_value=self.root), \
                mock.patch.object(D, "collect", return_value=D.Preview(SLUG, "x", b"<html>", {})), \
                mock.patch.object(D, "upload", side_effect=AssertionError("simulação não envia")), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(D.main(["enviar", str(self.md), "--imagens", str(self.imgs), "--dry-run", "--out", out]), 0)
            o = Path(out)
            self.assertEqual((o / "fonte" / f"{SLUG}.md").read_bytes(), self.md.read_bytes())
            self.assertEqual((o / "fonte" / "img" / "a-728.webp").read_bytes(), b"A")
            self.assertEqual(D.fonte_sha256(o / "fonte", SLUG), D.source_sha256(self.md, self.imgs))
            self.assertIn("fonte", json.loads((o / "manifest.json").read_text(encoding="utf-8")))

    def test_dry_run_refuses_an_output_folder_that_already_has_files(self):
        # r1 (Astra P3): repetir a simulação no mesmo --out, depois de tirar uma imagem, deixava a imagem antiga em
        # fonte/, e a pasta deixava de reproduzir o source_sha256 do manifesto. Agora a segunda simulação é recusada
        # antes do build, sem tocar no que já está lá.
        argv = lambda out: ["enviar", str(self.md), "--imagens", str(self.imgs), "--dry-run", "--out", out]  # noqa: E731
        with tempfile.TemporaryDirectory() as t, \
                mock.patch.object(D, "collect", return_value=D.Preview(SLUG, "x", b"<html>", {})), \
                contextlib.redirect_stdout(io.StringIO()):
            out = str(Path(t) / "out")
            with mock.patch.object(D, "build", return_value=self.root):
                self.assertEqual(D.main(argv(out)), 0)
            (self.imgs / "a-728.webp").unlink()
            antes = sorted(p.relative_to(out).as_posix() for p in Path(out).rglob("*"))
            err = io.StringIO()
            with mock.patch.object(D, "build", side_effect=AssertionError("não chega ao build")), contextlib.redirect_stderr(err):
                self.assertEqual(D.main(argv(out)), 2)
            self.assertIn("precisa estar vazia", err.getvalue())
            self.assertEqual(sorted(p.relative_to(out).as_posix() for p in Path(out).rglob("*")), antes)
            # Um arquivo no lugar da pasta também é recusado; pasta vazia ou inexistente segue valendo.
            (Path(t) / "arquivo").write_text("x")
            vazia = Path(t) / "vazia"
            vazia.mkdir()
            with mock.patch.object(D, "build", return_value=self.root), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(D.main(argv(str(Path(t) / "arquivo"))), 2)
                self.assertEqual(D.main(argv(str(vazia))), 0)
                self.assertEqual(D.fonte_sha256(vazia / "fonte", SLUG), D.source_sha256(self.md, self.imgs))

    def test_derivado_cli(self):
        # #77: --derivado <rótulo> anda com --artigo, não anda com --linkedin, e o rótulo segue a regra do Worker.
        with tempfile.TemporaryDirectory() as t:
            md = Path(t) / "post-short.md"
            md.write_text("---\ntitle: x\n---\n", encoding="utf-8")
            for argv, msg in ((["enviar", str(md), "--derivado", "Roteiro do short"], "andam juntos"),
                              (["enviar", str(md), "--derivado", "", "--artigo", "post"], "rótulo inválido"),
                              (["enviar", str(md), "--derivado", " Roteiro", "--artigo", "post"], "rótulo inválido"),
                              (["enviar", str(md), "--derivado", "\ufeffRoteiro", "--artigo", "post"], "rótulo inválido"),
                              (["enviar", str(md), "--derivado", "Roteiro\u0085", "--artigo", "post"], "rótulo inválido"),
                              (["enviar", str(md), "--derivado", "x" * 41, "--artigo", "post"], "rótulo inválido"),
                              (["enviar", str(md), "--linkedin", "--derivado", "Roteiro", "--artigo", "post"], "não andam juntos")):
                with self.subTest(argv=argv), mock.patch.object(D, "build", side_effect=AssertionError("não chega ao build")):
                    err = io.StringIO()
                    with contextlib.redirect_stderr(err):
                        self.assertEqual(D.main(argv), 2)
                    self.assertIn(msg, err.getvalue())
            with mock.patch.object(D, "build", return_value=Path(t)), \
                    mock.patch.object(D, "collect", return_value=D.Preview("post-short", "x", b"<html>", {})), \
                    mock.patch.object(D, "upload", return_value=["a", "b"]) as up:
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    self.assertEqual(D.main(["enviar", str(md), "--derivado", "Roteiro do short", "--artigo", "post"]), 0)
                man = up.call_args.args[1]
                self.assertEqual((man["kind"], man["parent"], man["rotulo"]), ("derivado", "post", "Roteiro do short"))
                self.assertIn("(Roteiro do short, só leitura)", out.getvalue())

    def test_rotulo_valido_matches_worker_rule(self):
        # Mesma regra de ROTULO_RE em drafts.ts: 1 a 40 pontos de código, sem C0, DEL nem C1, sem espaço nas pontas,
        # com o conjunto de espaços do trim() do JavaScript (o strip() do Python não remove o BOM).
        for ok in ("Roteiro do short", "x", "x" * 40, "\U0001f3ac" * 40, "Vídeo (vertical) 9:16"):
            self.assertTrue(D.rotulo_valido(ok), ok)
        for bad in ("", "x" * 41, " Roteiro", "Roteiro ", "\u00a0Roteiro", "Roteiro\u3000", "\ufeffRoteiro",
                    "Roteiro\ndo", "Roteiro\u0085", "Rot\u007feiro", 7, None):
            self.assertFalse(D.rotulo_valido(bad), repr(bad))

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
        # GA4 (2), Clarity e track.js (#60).
        self.assertEqual(base.count("{!DRAFT_PREVIEW && <script data-site-script is:inline"), 5)  # + consent.js (#68)
        self.assertIn("export const DRAFT_PREVIEW = process.env.DRAFT_PREVIEW === '1';", self.read("blog-src/src/consts.ts"))

    def test_ci_never_builds_the_preview(self):
        for wf in (D.REPO / ".github" / "workflows").glob("*.yml"):
            self.assertNotIn("DRAFT_PREVIEW", wf.read_text(encoding="utf-8"), wf.name)


class UploadTest(unittest.TestCase):
    def test_manifest_last_and_exact_keys_and_types(self):
        p = D.Preview(SLUG, "Sales Coach", b"<html>", {"img/sales-coach/a.webp": (b"A", "image/webp")})
        fonte, source = fonte_de()
        man = D.manifest(p, source, fonte, now=datetime(2026, 11, 13, 19, 0, tzinfo=timezone.utc))
        html = hashlib.sha256(b"<html>").hexdigest()
        version = hashlib.sha256(f"html:{html}\nsource:{source}".encode()).hexdigest()
        self.assertEqual(man, {"slug": SLUG, "title": "Sales Coach", "uploaded_at": "2026-11-13T19:00:00.000Z",
                               "source_sha256": source, "html_sha256": html, "version": version, "kind": "blog",
                               "files": {"index.html": "text/html; charset=utf-8", "img/sales-coach/a.webp": "image/webp"},
                               # #82: a fonte fica fora de files (o Worker só serve files), com o sha256 de cada arquivo.
                               "fonte": {"sales-coach.md": hashlib.sha256(MD).hexdigest(), "img/a.webp": hashlib.sha256(b"A").hexdigest()}})
        # #73: a versão LinkedIn leva kind e parent (o slug do artigo); a versão (html + fonte) não muda.
        li = D.manifest(p, source, fonte, now=datetime(2026, 11, 13, 19, 0, tzinfo=timezone.utc), parent="artigo-pai")
        self.assertEqual((li["kind"], li["parent"], li["version"]), ("linkedin", "artigo-pai", version))
        for bad in ("Artigo", "artigo_pai", "", SLUG, "artigo\n", "artigo\n\n"):  # r1 (Kimi P1): newline
            with self.subTest(parent=bad), self.assertRaises(D.DraftError):
                D.manifest(p, source, fonte, parent=bad)
        # #77: com rótulo, o derivado leva kind "derivado" e o rótulo; rótulo sem parent ou inválido é recusado.
        rs = D.manifest(p, source, fonte, now=datetime(2026, 11, 13, 19, 0, tzinfo=timezone.utc), parent="artigo-pai", rotulo="Roteiro do short")
        self.assertEqual((rs["kind"], rs["parent"], rs["rotulo"], rs["version"]), ("derivado", "artigo-pai", "Roteiro do short", version))
        self.assertNotIn("rotulo", li)
        for parent, rot in ((None, "Roteiro"), ("artigo-pai", ""), ("artigo-pai", " Roteiro")):
            with self.subTest(parent=parent, rotulo=rot), self.assertRaises(D.DraftError):
                D.manifest(p, source, fonte, parent=parent, rotulo=rot)
        # A versão muda com o HTML e com a fonte, cada um isolado (r1: a aprovação vale para os dois).
        self.assertNotEqual(version, D.version_id(html, "b" * 64))
        self.assertNotEqual(version, D.version_id("c" * 64, source))
        fake = Fake()
        with tempfile.TemporaryDirectory() as t:
            keys = D.upload(p, man, fonte, Path(t), run=fake)
            sent = Path(t) / "upload" / SLUG / "manifest.json"
            self.assertEqual(json.loads(sent.read_text(encoding="utf-8")), man)
            # O que a fatia 3 vai fazer: baixar a fonte/ enviada para uma pasta e recalcular o hash. Bate byte a byte.
            baixada = Path(t) / "baixada"
            for args, _ in fake.calls:
                key = args[6]
                if "/fonte/" in key:
                    f = baixada / key.split("/fonte/", 1)[1]
                    f.parent.mkdir(parents=True, exist_ok=True)
                    f.write_bytes(Path(args[args.index("--file") + 1]).read_bytes())
            self.assertEqual(D.fonte_sha256(baixada, SLUG), man["source_sha256"])
        # Versão inteira na pasta dela, com a fonte em fonte/, e o manifesto (o ponteiro) por último (r1, Astra P2).
        v = f"blog-rascunhos/sales-coach/v/{version}/"
        self.assertEqual(keys, [v + "img/sales-coach/a.webp", v + "fonte/img/a.webp", v + "fonte/sales-coach.md", v + "index.html",
                                "blog-rascunhos/sales-coach/manifest.json"])
        for (args, kw), ctype in zip(fake.calls, ["image/webp", "image/webp", "text/markdown; charset=utf-8",
                                                  "text/html; charset=utf-8", "application/json"], strict=True):
            self.assertEqual(args[:6], ["npx", "--yes", "wrangler@4", "r2", "object", "put"])
            self.assertEqual(args[args.index("--content-type") + 1], ctype)
            self.assertIn("--remote", args)
            self.assertEqual(kw["cwd"], D.ADMIN_WORKER)

    def test_manifest_refuses_invalid_source_paths(self):
        # #82: caminhos da fonte com as regras de FILE_RE e do slug: <slug>.md e img/<caminho de imagem>, nada mais.
        p = D.Preview(SLUG, "t", b"<html>", {})
        fonte, source = fonte_de()
        for name, extra in (("..", {"img/../x.webp": b"x"}), ("absoluto", {"/img/x.webp": b"x"}), ("barra dupla", {"img//x.webp": b"x"}),
                            ("oculto", {"img/.x.webp": b"x"}), ("fora de img", {"x.webp": b"x"}), ("tipo", {"img/x.exe": b"x"}),
                            ("outro md", {"outro.md": b"x"}), ("newline", {"img/x.webp\n": b"x"}), ("img sem arquivo", {"img/": b"x"}),
                            ("img sozinho", {"img": b"x"}), ("não é bytes", {"img/x.webp": "x"})):
            with self.subTest(name=name), self.assertRaises(D.DraftError):
                D.manifest(p, source, {**fonte, **extra})
        for name, f in (("sem o md", {"img/a.webp": b"A"}), ("md de outro slug", {"outro-post.md": MD}), ("vazia", {}),
                        ("md com newline", {f"{SLUG}.md\n": MD})):
            with self.subTest(name=name), self.assertRaises(D.DraftError):
                D.manifest(p, source, f)
        # Arquivo da prévia sob fonte/ esconderia a fonte: recusado.
        q = D.Preview(SLUG, "t", b"<html>", {"fonte/sales-coach.md": (b"x", "image/webp")})
        with self.assertRaises(D.DraftError):
            D.manifest(q, source, fonte)

    def test_upload_refuses_manifest_that_does_not_match_the_html(self):
        p = D.Preview(SLUG, "t", b"<html>", {})
        fonte, source = fonte_de()
        man = D.manifest(p, source, fonte)
        for k, v in (("html_sha256", "c" * 64), ("version", "d" * 64), ("source_sha256", "e" * 64)):
            with self.subTest(k=k), tempfile.TemporaryDirectory() as t, self.assertRaisesRegex(D.DraftError, "não confere"):
                D.upload(p, {**man, k: v}, fonte, Path(t), run=Fake())

    def test_upload_refuses_source_that_does_not_match_the_manifest(self):
        # #82: a fonte enviada tem de ser exatamente a que gerou o source_sha256 da versão. Nada sobe se não for.
        p = D.Preview(SLUG, "t", b"<html>", {})
        fonte, source = fonte_de()
        outra, outra_source = fonte_de(md=MD + b"mais\n")
        cases = {
            # Bytes trocados com o manifesto antigo: o sha256 do arquivo não bate.
            "bytes": (D.manifest(p, source, fonte), {**fonte, f"{SLUG}.md": MD + b"x"}),
            # Arquivo a mais ou a menos que o manifesto.
            "a mais": (D.manifest(p, source, fonte), {**fonte, "img/b.webp": b"B"}),
            "a menos": (D.manifest(p, source, fonte), {f"{SLUG}.md": MD}),
            # Manifesto coerente com a fonte, mas com o source_sha256 de outra fonte (versão recalculada junto).
            "source de outra fonte": (D.manifest(p, outra_source, fonte), fonte),
            # Bytes que reproduzem o source_sha256, mas o campo fonte do manifesto mente: a publicação conferiria
            # cada arquivo contra ele e recusaria. Só a conferência arquivo a arquivo pega estes dois.
            "sha256 errado no manifesto": ({**(m := D.manifest(p, source, fonte)), "fonte": {**m["fonte"], f"{SLUG}.md": "0" * 64}}, fonte),
            "arquivo a mais no manifesto": ({**m, "fonte": {**m["fonte"], "img/b.webp": "0" * 64}}, fonte),
        }
        for name, (man, f) in cases.items():
            fake = Fake()
            with self.subTest(name=name), tempfile.TemporaryDirectory() as t:
                with self.assertRaisesRegex(D.DraftError, "fonte"):
                    D.upload(p, man, f, Path(t), run=fake)
                self.assertEqual(fake.calls, [], "nada sobe")
        self.assertNotEqual(source, outra_source)
        with tempfile.TemporaryDirectory() as t:
            D.upload(p, D.manifest(p, outra_source, outra), outra, Path(t), run=Fake())

    def remover(self, man, rc=0):
        calls = []

        def fake_wrangler(args, run=None, input_bytes=None):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0, json.dumps(man).encode(), b"")
        err = io.StringIO()
        with mock.patch.object(D, "wrangler", fake_wrangler), contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(D.main(["remover", SLUG]), rc, err.getvalue())
        return [a[3] for a in calls if a[:3] == ["r2", "object", "delete"]]

    def test_remover_deletes_manifest_first_then_the_current_version(self):
        # Manifesto antigo, sem fonte: continua valendo.
        man = {"version": "f" * 64, "files": {"index.html": "text/html; charset=utf-8", "img/sales-coach/a.webp": "image/webp"}}
        v = f"blog-rascunhos/sales-coach/v/{'f' * 64}/"
        self.assertEqual(self.remover(man), ["blog-rascunhos/sales-coach/manifest.json", v + "index.html", v + "img/sales-coach/a.webp"])
        # #82: com fonte, apaga também cada arquivo dela, depois da prévia.
        com = {**man, "fonte": {"sales-coach.md": "1" * 64, "img/a.webp": "2" * 64}}
        self.assertEqual(self.remover(com), ["blog-rascunhos/sales-coach/manifest.json", v + "index.html", v + "img/sales-coach/a.webp",
                                             v + "fonte/sales-coach.md", v + "fonte/img/a.webp"])

    def test_remover_checks_every_key_before_deleting_anything(self):
        # #82: chave inválida na fonte (ou em files) recusa a remoção antes do primeiro delete, manifesto incluído.
        man = {"version": "f" * 64, "files": {"index.html": "text/html; charset=utf-8"}}
        for name, extra in (("..", {"fonte": {"../../outro-post/manifest.json": "1" * 64}}), ("absoluto", {"fonte": {"/x.md": "1" * 64}}),
                            ("newline", {"fonte": {"sales-coach.md\n": "1" * 64}}), ("fonte lista", {"fonte": ["sales-coach.md"]}),
                            ("files com ..", {"files": {"index.html": "text/html; charset=utf-8", "img/../../x": "image/webp"}})):
            with self.subTest(name=name):
                self.assertEqual(self.remover({**man, **extra}, rc=2), [])
        with mock.patch.object(D, "wrangler", side_effect=AssertionError("não chega ao R2")), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(D.main(["remover", "sales-coach\n"]), 2)

    def test_worker_and_script_agree_on_version_and_frame_hosts(self):
        # O Worker calcula a versão igual (versionId) e a CSP da prévia libera os mesmos iframes que o check_site aceita.
        ts = (D.REPO / "workers" / "admin" / "src" / "drafts.ts").read_text(encoding="utf-8")
        self.assertIn("return sha256Hex(`html:${html}\\nsource:${source}`);", ts)
        sys.path.insert(0, str(D.REPO / "scripts"))
        import check_site
        frame = re.search(r"'frame-src ([^']+)'\s*\+\s*'([^']+)'", ts)
        hosts = {u.removeprefix("https://") for u in (frame.group(1) + frame.group(2)).split()}
        self.assertEqual(hosts, set(check_site.IFRAME_HOSTS))

    def test_wrangler_failure_stops(self):
        p = D.Preview(SLUG, "t", b"<html>", {})
        fonte, source = fonte_de()
        with tempfile.TemporaryDirectory() as t, self.assertRaisesRegex(D.DraftError, "wrangler r2 object put falhou"):
            D.upload(p, D.manifest(p, source, fonte), fonte, Path(t), run=Fake(rc=1, stderr=b"sem permissao"))

    def test_approvals_query_and_slug_guard(self):
        fake = Fake(stdout=json.dumps([{"results": [{"html_sha256": "h", "source_sha256": "s"}]}]).encode())
        self.assertEqual(D.approvals(SLUG, run=fake), [{"html_sha256": "h", "source_sha256": "s"}])
        sql = fake.calls[0][0][-1]
        self.assertIn("WHERE slug = 'sales-coach'", sql)
        with self.assertRaises(D.DraftError):
            D.approvals("x'; DROP TABLE draft_approvals; --", run=Fake())


class Nuvem:
    """D1 e R2 falsos para o publicar: aprovações e objetos do bucket, com o registro do que foi lido."""

    def __init__(self, rows, objetos):
        self.rows, self.objetos = rows, objetos
        self.consultas, self.baixados = [], []

    def aprovacoes(self, slug):
        self.consultas.append(slug)
        return self.rows

    def baixar(self, key, work):
        self.baixados.append(key)
        if key not in self.objetos:
            raise D.DraftError(f"não consegui baixar {key} do R2: NoSuchKey")
        return self.objetos[key]


IMGS = {"01-capa.webp": b"capa", "fig/02-grafico-728.png": b"grafico", "03-anim.gif": b"anim"}


class PublicarTest(unittest.TestCase):
    """#82, fatia 3: a versão aprovada vira os arquivos do post, e nada é gravado se uma conferência falhar."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.blog = root / "repo" / "blog-src"
        (self.blog / "src" / "content" / "posts").mkdir(parents=True)
        (self.blog / "src" / "content" / "posts" / "outro-post.md").write_bytes(b"outro")
        (self.blog / "public" / "img" / "outro-post").mkdir(parents=True)
        self.work = root / "work"
        self.fonte, self.source = fonte_de(imgs=IMGS)
        self.man = D.manifest(D.Preview(SLUG, "Sales Coach", b"<html>aprovado</html>", {}), self.source, self.fonte)
        self.v = self.man["version"]
        self.row = {"id": 7, "html_sha256": self.man["html_sha256"], "source_sha256": self.source, "approved_at": "2026-11-16T18:12:10.333Z"}

    def tearDown(self):
        self.tmp.cleanup()

    def objetos(self, man=None, fonte=None, versao=None):
        man = self.man if man is None else man
        fonte = self.fonte if fonte is None else fonte
        out = {f"{SLUG}/manifest.json": json.dumps(man).encode()}
        out.update({f"{SLUG}/v/{versao or man['version']}/fonte/{k}": b for k, b in fonte.items()})
        return out

    def arvore(self):
        return sorted(p.relative_to(self.blog).as_posix() for p in self.blog.rglob("*") if p.is_file())

    def recusa(self, nuvem, padrao, slug=SLUG, versao=None):
        antes = self.arvore()
        with self.assertRaisesRegex(D.DraftError, padrao):
            D.publicar(slug, self.v if versao is None else versao, nuvem, self.blog, self.work)
        self.assertEqual(self.arvore(), antes, "nada é gravado numa recusa")

    def test_caso_feliz_grava_o_markdown_e_as_imagens_nos_caminhos_do_blog(self):
        nuvem = Nuvem([{"id": 3, "html_sha256": "0" * 64, "source_sha256": "1" * 64, "approved_at": "x"}, self.row], self.objetos())
        p = D.publicar(SLUG, self.v, nuvem, self.blog, self.work)
        self.assertEqual(self.arvore(), sorted([
            "src/content/posts/outro-post.md", f"src/content/posts/{SLUG}.md", f"public/img/{SLUG}/01-capa.webp",
            f"public/img/{SLUG}/fig/02-grafico-728.png", f"public/img/{SLUG}/03-anim.gif"]))
        self.assertEqual((self.blog / "src" / "content" / "posts" / f"{SLUG}.md").read_bytes(), MD)
        for rel, data in IMGS.items():
            self.assertEqual((self.blog / "public" / "img" / SLUG / rel).read_bytes(), data)
        self.assertEqual((p.slug, p.versao, p.title, p.source_sha256, p.html_sha256), (SLUG, self.v, "Sales Coach", self.source, self.man["html_sha256"]))
        self.assertEqual(p.aprovacao, {"id": 7, "approved_at": "2026-11-16T18:12:10.333Z"})
        self.assertEqual(p.arquivos[0], (f"blog-src/src/content/posts/{SLUG}.md", hashlib.sha256(MD).hexdigest()))
        self.assertEqual(sorted(c for c, _ in p.arquivos[1:]), sorted(f"blog-src/public/img/{SLUG}/{k}" for k in IMGS))
        self.assertEqual(nuvem.consultas, [SLUG])
        self.assertEqual(sorted(nuvem.baixados), sorted(self.objetos()))
        corpo = D.corpo_pr(p)
        for trecho in (self.v, self.source, self.man["html_sha256"], "linha `7`", "2026-11-16T18:12:10.333Z",
                       f"| `blog-src/public/img/{SLUG}/fig/02-grafico-728.png` | `{hashlib.sha256(b'grafico').hexdigest()}` |"):
            self.assertIn(trecho, corpo)

    def test_corpo_do_pr_nao_menciona_ninguem_pelo_titulo(self):
        p = D.Publicacao(SLUG, self.v, "Com `@alguem`\nquebra", {"id": 1, "approved_at": "a"}, "h" * 64, "s" * 64, [])
        corpo = D.corpo_pr(p)
        self.assertIn("`Com '@alguem' quebra`", corpo)
        self.assertNotIn("\nquebra", corpo)

    def test_recusa_sem_aprovacao_da_versao(self):
        outra = {**self.row, "source_sha256": "2" * 64}
        for name, rows in (("nenhuma", []), ("outra versão do slug", [outra]), ("linha malformada", [{"id": 1}, "x", None])):
            nuvem = Nuvem(rows, self.objetos())
            with self.subTest(name=name):
                self.recusa(nuvem, "nenhuma aprovação")
                self.assertEqual(nuvem.baixados, [], "sem aprovação nada é baixado")

    def test_recusa_derivado(self):
        for name, extra in (("linkedin", {"kind": "linkedin", "parent": "artigo"}),
                            ("derivado", {"kind": "derivado", "parent": "artigo", "rotulo": "Roteiro"}),
                            ("parent sem kind", {"parent": "artigo"})):
            man = {**self.man, **extra}
            with self.subTest(name=name):
                self.recusa(Nuvem([self.row], self.objetos(man)), "derivado")

    def test_recusa_hash_de_arquivo_divergente(self):
        # O R2 devolve bytes que não são os do manifesto.
        trocado = {**self.fonte, "img/01-capa.webp": b"outra capa"}
        self.recusa(Nuvem([self.row], self.objetos(fonte=trocado, versao=self.v)), "sha256 de img/01-capa.webp")
        # Arquivo listado no manifesto que não está no bucket.
        sem = {k: b for k, b in self.fonte.items() if k != "img/03-anim.gif"}
        self.recusa(Nuvem([self.row], self.objetos(fonte=sem, versao=self.v)), "NoSuchKey")

    def test_recusa_source_sha256_divergente(self):
        # Manifesto e bucket coerentes entre si, com um Markdown trocado depois da aprovação: cada arquivo bate com o
        # manifesto, e só o source_sha256 recalculado mostra que não é a fonte aprovada.
        trocado = {**self.fonte, f"{SLUG}.md": MD + b"frase nova\n"}
        man = {**self.man, "fonte": {k: hashlib.sha256(b).hexdigest() for k, b in trocado.items()}}
        self.recusa(Nuvem([self.row], self.objetos(man, trocado)), "source_sha256")
        # Imagem a menos no manifesto e no bucket: também muda o hash da fonte.
        menos = {k: b for k, b in self.fonte.items() if k != "img/03-anim.gif"}
        man = {**self.man, "fonte": {k: hashlib.sha256(b).hexdigest() for k, b in menos.items()}}
        self.recusa(Nuvem([self.row], self.objetos(man, menos)), "source_sha256")

    def test_recusa_manifesto_de_outra_versao_ou_sem_fonte(self):
        # Versão nova enviada depois da aprovação: o manifesto atual não é o aprovado.
        novo, novo_source = fonte_de(md=MD + b"x", imgs=IMGS)
        man = D.manifest(D.Preview(SLUG, "Sales Coach", b"<html>novo</html>", {}), novo_source, novo)
        self.recusa(Nuvem([self.row], self.objetos(man, novo)), "versão atual do rascunho")
        # Manifesto que mente a versão, com HTML de outra.
        self.recusa(Nuvem([self.row], self.objetos({**self.man, "html_sha256": "3" * 64})), "HTML e a fonte da aprovação")
        # Manifesto de antes do #82.
        sem = {k: v for k, v in self.man.items() if k != "fonte"}
        self.recusa(Nuvem([self.row], self.objetos(sem)), "não tem a fonte")
        self.recusa(Nuvem([self.row], {f"{SLUG}/manifest.json": b"{nao e json"}), "ilegível")
        self.recusa(Nuvem([self.row], self.objetos({**self.man, "slug": "outro"})), "não é deste slug")

    def test_recusa_slug_ja_publicado_antes_de_consultar_a_nuvem(self):
        (self.blog / "src" / "content" / "posts" / f"{SLUG}.md").write_bytes(b"publicado")
        nuvem = Nuvem([self.row], self.objetos())
        self.recusa(nuvem, "já está publicado")
        self.assertEqual((nuvem.consultas, nuvem.baixados), ([], []))
        (self.blog / "src" / "content" / "posts" / f"{SLUG}.md").unlink()
        (self.blog / "public" / "img" / SLUG).mkdir()
        self.recusa(nuvem, "public/img/sales-coach")
        self.assertEqual((nuvem.consultas, nuvem.baixados), ([], []))

    def test_recusa_entradas_invalidas_antes_de_consultar_a_nuvem(self):
        nuvem = Nuvem([self.row], self.objetos())
        for slug, versao in (("Sales-Coach", self.v), ("sales-coach\n", self.v), ("$(touch x)", self.v), ("../x", self.v), ("", self.v),
                             (SLUG, self.v[:63]), (SLUG, self.v.upper()), (SLUG, self.v + "\n"), (SLUG, "$(id)" + self.v[5:])):
            with self.subTest(slug=slug, versao=versao):
                self.recusa(nuvem, "inválid", slug=slug, versao=versao)
        self.assertEqual((nuvem.consultas, nuvem.baixados), ([], []))

    def test_recusa_caminho_malicioso_na_fonte_antes_de_baixar_a_fonte(self):
        sha = "4" * 64
        for rel, padrao in (("../outro-post.md", "caminho inválido"), ("img/../../src/pages/index.astro", "caminho inválido"),
                            ("/etc/passwd", "caminho inválido"), ("img/a.webp\n", "caminho inválido"), ("outro.md", "caminho inválido"),
                            ("img/x.html", "caminho inválido"), ("fonte/sales-coach.md", "caminho inválido"),
                            ("img/a.svg", "barreira 2"), ("img/.oculto.webp", "caminho inválido")):
            man = {**self.man, "fonte": {**self.man["fonte"], rel: sha}}
            nuvem = Nuvem([self.row], self.objetos(man))
            with self.subTest(rel=rel):
                self.recusa(nuvem, padrao)
                self.assertEqual(nuvem.baixados, [f"{SLUG}/manifest.json"], "só o manifesto foi baixado")
        man = {**self.man, "fonte": {**self.man["fonte"], "img/a.webp": "nao-hex"}}
        self.recusa(Nuvem([self.row], self.objetos(man)), "sha256 inválido")

    def test_recusa_destino_fora_do_blog_src_por_link_simbolico(self):
        # Defesa em profundidade: public/img apontando para fora do repo.
        fora = Path(self.tmp.name) / "fora"
        fora.mkdir()
        import shutil
        shutil.rmtree(self.blog / "public" / "img")
        (self.blog / "public" / "img").symlink_to(fora)
        self.recusa(Nuvem([self.row], self.objetos()), "fora do blog-src")
        self.assertEqual(list(fora.iterdir()), [])

    def test_cli_publicar_grava_os_arquivos_do_pr(self):
        saida = Path(self.tmp.name) / "pr"
        nuvem = Nuvem([self.row], self.objetos())
        with mock.patch.object(D, "BLOG_SRC", self.blog), mock.patch.object(D, "Cloudflare", lambda env: nuvem), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(D.main(["publicar", SLUG, "--versao", self.v, "--saida", str(saida)]), 0)
        self.assertEqual((saida / "titulo.txt").read_text(encoding="utf-8"), "Sales Coach\n")
        self.assertIn(self.source, (saida / "corpo.md").read_text(encoding="utf-8"))
        self.assertEqual(sorted((saida / "arquivos.txt").read_text(encoding="utf-8").splitlines()),
                         sorted([f"blog-src/src/content/posts/{SLUG}.md", *[f"blog-src/public/img/{SLUG}/{k}" for k in IMGS]]))
        # Saída com arquivo de antes: recusa, sem tocar no blog.
        err = io.StringIO()
        with mock.patch.object(D, "BLOG_SRC", self.blog), mock.patch.object(D, "Cloudflare", side_effect=AssertionError("não chega")), \
                contextlib.redirect_stderr(err):
            self.assertEqual(D.main(["publicar", SLUG, "--versao", self.v, "--saida", str(saida)]), 2)
        self.assertIn("vazia", err.getvalue())


class CloudflareTest(unittest.TestCase):
    """O transporte real do publicar: o D1 pela API REST, com o slug como parâmetro, e o R2 pela API S3. Os segredos
    vão só em cabeçalho ou ambiente, nunca em argumento nem em mensagem de erro."""
    ENV = {"CLOUDFLARE_ACCOUNT_ID": "a" * 32, "CF_PUBLICAR_API_TOKEN": "tok-SEGREDO-d1", "R2_ACCESS_KEY_ID": "id-SEGREDO-r2",
           "R2_SECRET_ACCESS_KEY": "chave-SEGREDO-r2", "PATH": "/usr/bin", "AWS_PROFILE": "outro"}

    def test_faltando_segredo_diz_so_o_nome(self):
        with self.assertRaisesRegex(D.DraftError, "faltam no ambiente: CF_PUBLICAR_API_TOKEN, R2_SECRET_ACCESS_KEY$"):
            D.Cloudflare({**self.ENV, "CF_PUBLICAR_API_TOKEN": "", "R2_SECRET_ACCESS_KEY": ""})
        with self.assertRaisesRegex(D.DraftError, "CLOUDFLARE_ACCOUNT_ID"):
            D.Cloudflare({**self.ENV, "CLOUDFLARE_ACCOUNT_ID": "conta/../x"})

    def test_d1_pela_api_rest_com_parametro(self):
        pedidos = []

        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def http(req, timeout):
            pedidos.append(req)
            return Resp(json.dumps({"success": True, "result": [{"results": [{"id": 1}], "success": True}]}).encode())
        cf = D.Cloudflare(self.ENV, http=http)
        self.assertEqual(cf.aprovacoes("x'; DROP TABLE draft_approvals; --"), [{"id": 1}])
        req = pedidos[0]
        self.assertEqual(req.full_url, f"https://api.cloudflare.com/client/v4/accounts/{'a' * 32}/d1/database/{D.D1_ID}/query")
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.get_header("Authorization"), "Bearer tok-SEGREDO-d1")
        body = json.loads(req.data)
        self.assertEqual(body["params"], ["x'; DROP TABLE draft_approvals; --"])
        self.assertNotIn("DROP", body["sql"])
        self.assertIn("WHERE slug = ?", body["sql"])

    def test_d1_com_erro_nao_vaza_o_token(self):
        import urllib.error

        def recusa(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, io.BytesIO(b'{"errors":[{"message":"Authentication error"}]}'))

        def lixo(req, timeout):
            return contextlib.nullcontext(io.BytesIO(b'{"success": false}'))
        for http, padrao in ((recusa, "respondeu 403"), (lixo, "fora do formato")):
            with self.subTest(padrao=padrao), self.assertRaisesRegex(D.DraftError, padrao) as ctx:
                D.Cloudflare(self.ENV, http=http).aprovacoes(SLUG)
            self.assertNotIn("SEGREDO", str(ctx.exception))

    def test_r2_pela_api_s3_com_as_chaves_no_ambiente(self):
        chamadas = []

        def run(args, **kw):
            chamadas.append((args, kw))
            Path(args[-1]).write_bytes(b"conteudo")
            return subprocess.CompletedProcess(args, 0, b"{}", b"")
        with tempfile.TemporaryDirectory() as t:
            self.assertEqual(D.Cloudflare(self.ENV, run=run).baixar("sales-coach/manifest.json", Path(t)), b"conteudo")
            self.assertEqual(list(Path(t).iterdir()), [], "o arquivo temporário sai")
        args, kw = chamadas[0]
        self.assertEqual(args[:9], ["aws", "s3api", "get-object", "--bucket", "blog-rascunhos", "--key", "sales-coach/manifest.json",
                                    "--endpoint-url", f"https://{'a' * 32}.r2.cloudflarestorage.com"])
        self.assertNotIn("SEGREDO", " ".join(args))
        env = kw["env"]
        self.assertEqual((env["AWS_ACCESS_KEY_ID"], env["AWS_SECRET_ACCESS_KEY"]), ("id-SEGREDO-r2", "chave-SEGREDO-r2"))
        self.assertNotIn("CF_PUBLICAR_API_TOKEN", env)
        self.assertNotIn("AWS_PROFILE", env, "credencial de outro perfil não entra")
        falha = Fake(rc=254, stderr=b"An error occurred (NoSuchKey)")
        with tempfile.TemporaryDirectory() as t, self.assertRaisesRegex(D.DraftError, "NoSuchKey") as ctx:
            D.Cloudflare(self.ENV, run=falha).baixar("sales-coach/manifest.json", Path(t))
        self.assertNotIn("SEGREDO", str(ctx.exception))

    def test_d1_id_e_o_do_wrangler_toml(self):
        toml = (D.ADMIN_WORKER / "wrangler.toml").read_text(encoding="utf-8")
        self.assertIn(f'database_id = "{D.D1_ID}"', toml)


class PublicarWorkflowTest(unittest.TestCase):
    """O publicar.yml cumpre o contrato com o painel (fatia 4) e nunca põe uma entrada direto no shell."""

    def setUp(self):
        self.yml = (D.REPO / ".github" / "workflows" / "publicar.yml").read_text(encoding="utf-8")

    def test_entradas_so_por_variavel_de_ambiente(self):
        linhas = [ln.split(":")[0].strip() for ln in self.yml.splitlines() if re.search(r"\$\{\{\s*(?:inputs|github\.event)\b", ln)]
        self.assertEqual(sorted(linhas), ["SLUG", "VERSAO", "group", "run-name"], "inputs só no run-name, na concurrency e no env do job")

    def test_contrato_com_o_painel(self):
        y = self.yml
        self.assertRegex(y, r"(?m)^on:\n  workflow_dispatch:\n    inputs:\n      slug:\n(?:        .*\n)+      versao:\n")
        self.assertIn("run-name: Publica ${{ inputs.slug }} (${{ inputs.versao }})", y)
        self.assertIn('echo "BRANCH=publica/$SLUG-${VERSAO:0:8}" >> "$GITHUB_ENV"', y)
        for perm in ("contents: write", "pull-requests: write", "actions: write"):
            self.assertIn(f"\n  {perm}\n", y)
        self.assertIn("[[ \"$SLUG\" =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]]", y)
        self.assertIn("[[ \"$VERSAO\" =~ ^[0-9a-f]{64}$ ]]", y)
        self.assertIn("[ \"$GITHUB_REF\" = refs/heads/main ]", y)
        self.assertIn('gh workflow run pages.yml --ref "$BRANCH"', y)
        self.assertIn('draft_preview.py publicar "$SLUG" --versao "$VERSAO"', y)
        # Segredos só no passo que confere e grava.
        self.assertEqual(y.count("${{ secrets."), 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)

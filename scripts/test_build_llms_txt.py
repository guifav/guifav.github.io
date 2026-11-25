"""Testes do gerador de /llms.txt (scripts/build_llms_txt.py).

Rodar: python3 scripts/test_build_llms_txt.py
"""
from __future__ import annotations

import contextlib
import io
import os
import re
import tempfile
import unittest
from pathlib import Path

import build_llms_txt as L

REPO = Path(__file__).resolve().parent.parent


def page(title: str, desc: str = "", body: str = "", extra_head: str = "") -> str:
    meta_desc = f'<meta name="description" content="{desc}">' if desc else ""
    return (
        "<!DOCTYPE html><html lang=\"pt-BR\"><head>"
        f"<meta charset=\"utf-8\"><title>{title}</title>{meta_desc}{extra_head}"
        f"</head><body>{body}</body></html>"
    )


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.site = root / "_site"
        self.posts = root / "posts"
        self.posts.mkdir()
        self.w("robots.txt", "User-agent: *\nDisallow: /admin/\nDisallow: /portfolio/mockups/\n\nSitemap: https://guifav.dev/sitemap.xml\n")
        self.w("index.html", page("Guilherme Favaron | Builder e CTO", "Descrição da home."))
        self.w("404.html", page("Página não encontrada", "404", extra_head='<meta name="robots" content="noindex">'))
        self.w(
            "consultoria/index.html",
            page(
                "Consultoria em IA para empresas | Guilherme Favaron",
                "Consultoria de IA para CEOs, CFOs e CTOs: cinco formatos.",
                extra_head='<link rel="canonical" href="https://guifav.dev/consultoria/">',
            ),
        )
        self.w(
            "portfolio/index.html",
            page(
                "Portfólio | Guilherme Favaron",
                "Portfólio de Guilherme Favaron: 22 sistemas de dados e IA.",
                extra_head='<link rel="canonical" href="https://guifav.dev/portfolio/">',
            ),
        )
        self.w("portfolio/mockups/a.html", page("Mockup", "Não pode entrar"))
        self.w(
            "ferramentas/index.html",
            page(
                "Ferramentas de IA aplicada | Guilherme Favaron",
                "Ferramentas gratuitas para arquitetura e custos de IA.",
                body=(
                    '<main>'
                    '<a href="/ferramentas/mapa-decisao-ia/">Mapa</a>'
                    '<a href="/ferramentas/planilha-custos-ia/">Planilha</a>'
                    '<a href="/ferramentas/seo-geo-aeo-framework/framework-seo-aeo.html">Framework</a>'
                    '</main>'
                ),
                extra_head='<link rel="canonical" href="https://guifav.dev/ferramentas/">',
            ),
        )
        self.w(
            "ferramentas/mapa-decisao-ia/index.html",
            page(
                "RAG, Text-to-SQL ou contexto longo? | Guilherme Favaron",
                "Escolha entre RAG, Text-to-SQL e contexto longo.",
                extra_head='<link rel="canonical" href="https://guifav.dev/ferramentas/mapa-decisao-ia/">',
            ),
        )
        self.w(
            "ferramentas/mapa-decisao-ia/enviado/index.html",
            page("Links enviados | Guilherme Favaron", "Enviado", extra_head='<meta name="robots" content="noindex">'),
        )
        self.w(
            "ferramentas/planilha-custos-ia/index.html",
            page(
                "Planilha de custos de IA em produção | Guilherme Favaron",
                "Planilha aberta para calcular custos de IA.",
                extra_head='<link rel="canonical" href="https://guifav.dev/ferramentas/planilha-custos-ia/">',
            ),
        )
        self.w(
            "ferramentas/seo-geo-aeo-framework/framework-seo-aeo.html",
            page(
                "Framework SEO + AEO/GEO | Guilherme Favaron",
                "Framework prático de SEO e AEO.",
                extra_head='<link rel="canonical" href="https://guifav.dev/ferramentas/seo-geo-aeo-framework/framework-seo-aeo.html">',
            ),
        )
        self.w(
            "blog/index.html",
            page(
                "Insights | Guilherme Favaron",
                "Artigos de Guilherme Favaron sobre IA aplicada e dados.",
                body=(
                    '<nav class="topic-nav"><ul>'
                    '<li><a href="/blog/tema/custos-e-modelos-de-ia/">Custos</a></li>'
                    '<li><a href="/blog/tema/ia-sobre-dados-da-empresa/">Dados</a></li>'
                    '</ul></nav>'
                ),
                extra_head='<link rel="canonical" href="https://guifav.dev/blog/">',
            ),
        )
        self.w(
            "blog/assinar/index.html",
            page("Assine os Expert Insights | Guilherme Favaron", "Assinar.", extra_head='<meta name="robots" content="noindex">'),
        )
        self.w(
            "blog/tema/custos-e-modelos-de-ia/index.html",
            page(
                "Artigos sobre custos, ROI e escolha de modelo | Guilherme Favaron",
                "Artigos sobre quanto custa IA generativa em produção.",
                body=(
                    '<header><h1>Custos, ROI e escolha de modelo</h1></header>'
                    '<ol class="post-list topic-list">'
                    '<li><a href="/blog/post-com-summary/"><h2>Post com summary</h2></a></li>'
                    '<li><a href="/blog/post-sem-summary/"><h2>Post sem summary</h2></a></li>'
                    '</ol>'
                ),
                extra_head='<link rel="canonical" href="https://guifav.dev/blog/tema/custos-e-modelos-de-ia/">',
            ),
        )
        self.w(
            "blog/tema/ia-sobre-dados-da-empresa/index.html",
            page(
                "Artigos sobre IA e os dados da empresa | Guilherme Favaron",
                "Artigos sobre RAG, Text-to-SQL e MCP.",
                body=(
                    '<header><h1>IA sobre os dados da empresa</h1></header>'
                    '<ol class="post-list topic-list">'
                    '<li><a href="/blog/post-multiline/"><h2>Post multiline</h2></a></li>'
                    '</ol>'
                ),
                extra_head='<link rel="canonical" href="https://guifav.dev/blog/tema/ia-sobre-dados-da-empresa/">',
            ),
        )
        self.add_post(
            "post-com-summary",
            title="Quanto custa rodar IA [em produção]",
            description="Descrição curta do post.",
            summary="Resumo objetivo que responde quanto custa rodar IA em produção.",
        )
        self.add_post(
            "post-sem-summary",
            title="Post sem summary ainda",
            description="Descrição do frontmatter usada como fallback.",
        )
        self.add_post(
            "post-multiline",
            title="Post com summary em bloco YAML",
            description="Descrição padrão.",
            summary_block="Linha um do resumo.\nLinha dois do resumo.",
        )
        self.add_post("post-podado", title="Post da poda", description="Fora do foco.", index="false")
        self.add_post("ep-podcast", title="Episódio de podcast", description="Áudio.", type_="podcast", index="false")

    def tearDown(self):
        self.tmp.cleanup()

    def w(self, rel: str, text: str) -> Path:
        p = self.site / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def add_post(
        self,
        slug: str,
        *,
        title: str,
        description: str,
        summary: str | None = None,
        summary_block: str | None = None,
        index: str = "true",
        type_: str = "newsletter",
        html_noindex: bool = False,
    ) -> None:
        lines = [
            "---",
            f'title: "{title}"',
            f'description: "{description}"',
            f"slug: {slug}",
            f"type: {type_}",
            f"index: {index}",
        ]
        if summary is not None:
            lines.append(f'summary: "{summary}"')
        if summary_block is not None:
            lines.append("summary: >")
            for ln in summary_block.splitlines():
                lines.append(f"  {ln}")
        lines += ["---", "", "Corpo do post."]
        (self.posts / f"{slug}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        robots = '<meta name="robots" content="noindex">' if (html_noindex or index == "false") else ""
        self.w(
            f"blog/{slug}/index.html",
            page(title, description, extra_head=f'{robots}<link rel="canonical" href="https://guifav.dev/blog/{slug}/">'),
        )


class BuildLlmsTxtTest(Fixture):
    def test_generates_expected_format_sections_and_summaries(self):
        text, count = L.build(self.site, self.posts)
        out = self.site / "llms.txt"
        self.assertTrue(out.is_file())
        self.assertEqual(out.read_text(encoding="utf-8"), text)
        self.assertGreater(count, 0)

        lines = text.splitlines()
        self.assertTrue(lines[0].startswith("# "))
        self.assertTrue(any(ln.startswith("> ") for ln in lines[:5]))
        self.assertIn(L.SITE_INTRO, text)
        self.assertIn("mais de 20 sistemas construídos no portfólio", L.SITE_INTRO)
        self.assertNotIn("22 sistemas em operação no portfólio", text)
        for heading in ("## Consultoria", "## Ferramentas", "## Temas", "## Portfólio e Expert Insights"):
            self.assertIn(heading, lines)

        # Sufixo removido dos títulos das páginas estáticas e colchetes escapados no título do post.
        self.assertIn(
            "- [Consultoria em IA para empresas](https://guifav.dev/consultoria/): "
            "Consultoria de IA para CEOs, CFOs e CTOs: cinco formatos.",
            lines,
        )
        # Ferramentas na ordem da página /ferramentas/, começando pelo índice, sem /enviado/ (noindex).
        self.assertIn(
            "- [Ferramentas de IA aplicada](https://guifav.dev/ferramentas/): "
            "Ferramentas gratuitas para arquitetura e custos de IA.",
            lines,
        )
        i_ferr = text.index("https://guifav.dev/ferramentas/)")
        i_mapa = text.index("https://guifav.dev/ferramentas/mapa-decisao-ia/)")
        i_plan = text.index("https://guifav.dev/ferramentas/planilha-custos-ia/)")
        i_fw = text.index("https://guifav.dev/ferramentas/seo-geo-aeo-framework/framework-seo-aeo.html)")
        self.assertTrue(i_ferr < i_mapa < i_plan < i_fw)
        self.assertNotIn("/enviado/", text)

        # Temas na ordem do topic-nav do blog; posts usam summary (inclusive bloco YAML) com fallback para description.
        i_custos = text.index("https://guifav.dev/blog/tema/custos-e-modelos-de-ia/)")
        i_dados = text.index("https://guifav.dev/blog/tema/ia-sobre-dados-da-empresa/)")
        self.assertLess(i_custos, i_dados)
        self.assertIn(
            "  - [Quanto custa rodar IA \\[em produção\\]](https://guifav.dev/blog/post-com-summary/): "
            "Resumo objetivo que responde quanto custa rodar IA em produção.",
            lines,
        )
        self.assertIn(
            "  - [Post sem summary ainda](https://guifav.dev/blog/post-sem-summary/): "
            "Descrição do frontmatter usada como fallback.",
            lines,
        )
        self.assertIn(
            "  - [Post com summary em bloco YAML](https://guifav.dev/blog/post-multiline/): "
            "Linha um do resumo. Linha dois do resumo.",
            lines,
        )

        # Portfólio e Expert Insights (sem /blog/assinar/ que é noindex).
        self.assertIn(
            "- [Portfólio](https://guifav.dev/portfolio/): Portfólio de Guilherme Favaron: 22 sistemas de dados e IA.",
            lines,
        )
        self.assertIn(
            "- [Insights](https://guifav.dev/blog/): Artigos de Guilherme Favaron sobre IA aplicada e dados.",
            lines,
        )
        self.assertNotIn("/blog/assinar/", text)
        self.assertNotIn("/portfolio/mockups/", text)
        self.assertNotIn("post-podado", text)
        self.assertNotIn("ep-podcast", text)
        self.assertNotIn("404.html", text)

    def test_excludes_noindex_index_false_podcast_and_robots_blocked_posts_even_if_listed_in_topic(self):
        # Mesmo se uma página de tema listar um post podado, um podcast ou um post com HTML noindex ou bloqueado no robots,
        # o gerador nunca inclui páginas não indexáveis no llms.txt.
        self.add_post("post-html-noindex", title="HTML noindex", description="d", html_noindex=True)
        self.add_post("post-podcast-index-true", title="Podcast mal marcado", description="d", type_="podcast", index="true")
        self.add_post("post-bloqueado", title="Bloqueado no robots", description="d")
        self.w(
            "robots.txt",
            "User-agent: *\nDisallow: /admin/\nDisallow: /blog/post-bloqueado/\nSitemap: https://guifav.dev/sitemap.xml\n",
        )
        self.w(
            "blog/tema/custos-e-modelos-de-ia/index.html",
            page(
                "Artigos sobre custos | Guilherme Favaron",
                "Artigos sobre custos.",
                body=(
                    '<header><h1>Custos, ROI e escolha de modelo</h1></header>'
                    '<ol class="post-list topic-list">'
                    '<li><a href="/blog/post-com-summary/"><h2>Ok</h2></a></li>'
                    '<li><a href="/blog/post-podado/"><h2>Podado</h2></a></li>'
                    '<li><a href="/blog/ep-podcast/"><h2>Podcast</h2></a></li>'
                    '<li><a href="/blog/post-podcast-index-true/"><h2>Podcast 2</h2></a></li>'
                    '<li><a href="/blog/post-html-noindex/"><h2>Noindex</h2></a></li>'
                    '<li><a href="/blog/post-bloqueado/"><h2>Bloqueado</h2></a></li>'
                    '</ol>'
                ),
            ),
        )
        text, _ = L.build(self.site, self.posts)
        self.assertIn("https://guifav.dev/blog/post-com-summary/", text)
        for excluded in ("post-podado", "ep-podcast", "post-podcast-index-true", "post-html-noindex", "post-bloqueado"):
            self.assertNotIn(excluded, text)

    def test_excludes_tool_with_external_canonical_or_robots_disallow(self):
        self.w(
            "ferramentas/externa/index.html",
            page("Externa | Guilherme Favaron", "Desc", extra_head='<link rel="canonical" href="https://guifav.dev/ferramentas/">'),
        )
        self.w("ferramentas/privada/index.html", page("Privada | Guilherme Favaron", "Desc"))
        self.w(
            "robots.txt",
            "User-agent: *\nDisallow: /admin/\nDisallow: /ferramentas/privada/\nSitemap: https://guifav.dev/sitemap.xml\n",
        )
        text, _ = L.build(self.site, self.posts)
        self.assertNotIn("/ferramentas/externa/", text)
        self.assertNotIn("/ferramentas/privada/", text)

    def test_refuses_when_llms_txt_already_exists(self):
        self.w("llms.txt", "ja existe")
        with self.assertRaisesRegex(L.LlmsTxtError, "já existe"):
            L.build(self.site, self.posts)

    def test_refuses_missing_required_section_page(self):
        (self.site / "consultoria/index.html").unlink()
        with self.assertRaisesRegex(L.LlmsTxtError, "consultoria"):
            L.build(self.site, self.posts)

    def test_refuses_topic_link_pointing_to_missing_post(self):
        self.w(
            "blog/tema/custos-e-modelos-de-ia/index.html",
            page(
                "Artigos sobre custos | Guilherme Favaron",
                "Desc.",
                body='<h1>Custos</h1><ol class="post-list topic-list"><li><a href="/blog/nao-existe/">x</a></li></ol>',
            ),
        )
        with self.assertRaisesRegex(L.LlmsTxtError, "nao-existe"):
            L.build(self.site, self.posts)

    def test_ignores_html_comments_templates_and_scripts_when_parsing_meta(self):
        self.w(
            "consultoria/index.html",
            "<!DOCTYPE html><html><head>"
            "<!-- <title>Comentado</title><meta name=\"robots\" content=\"noindex\"> -->"
            "<script>var x = '<meta name=\"robots\" content=\"noindex\">';</script>"
            "<title>Consultoria real | Guilherme Favaron</title>"
            '<meta name="description" content="Descrição real da consultoria.">'
            '<link rel="canonical" href="https://guifav.dev/consultoria/">'
            "</head><body><template><a href=\"/ferramentas/fantasma/\">f</a></template></body></html>",
        )
        text, _ = L.build(self.site, self.posts)
        self.assertIn("- [Consultoria real](https://guifav.dev/consultoria/): Descrição real da consultoria.", text)

    def test_main_exit_codes(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = L.main(["--site", str(self.site), "--posts", str(self.posts)])
        self.assertEqual(code, 0, err.getvalue())
        self.assertIn("llms.txt:", out.getvalue())
        # Rodar de novo falha porque _site/llms.txt já existe.
        err2 = io.StringIO()
        with contextlib.redirect_stderr(err2):
            code2 = L.main(["--site", str(self.site), "--posts", str(self.posts)])
        self.assertEqual(code2, 1)
        self.assertIn("já existe", err2.getvalue())


class WorkflowWiringTest(unittest.TestCase):
    """Confere que o pages.yml testa e gera /llms.txt na ordem certa (depois de assemble_site e build_sitemaps, antes de check_site)."""

    def test_pages_workflow_runs_test_and_build_llms_txt_in_order(self):
        wf = (REPO / ".github/workflows/pages.yml").read_text(encoding="utf-8")
        self.assertIn("python3 scripts/test_build_llms_txt.py", wf)
        pos = {
            k: wf.index(k)
            for k in (
                "python3 scripts/assemble_site.py",
                "python3 scripts/build_sitemaps.py",
                "python3 scripts/build_llms_txt.py",
                "python3 scripts/check_site.py",
            )
        }
        self.assertLess(pos["python3 scripts/assemble_site.py"], pos["python3 scripts/build_sitemaps.py"])
        self.assertLess(pos["python3 scripts/build_sitemaps.py"], pos["python3 scripts/build_llms_txt.py"])
        self.assertLess(pos["python3 scripts/build_llms_txt.py"], pos["python3 scripts/check_site.py"])

    def test_site_manifest_documents_llms_txt_as_generated_and_does_not_list_it(self):
        manifest = (REPO / "site-manifest.txt").read_text(encoding="utf-8")
        self.assertIn("llms.txt", manifest)
        entries = [ln.strip() for ln in manifest.splitlines() if ln.strip() and not ln.strip().startswith("#")]
        self.assertNotIn("llms.txt", entries)


if __name__ == "__main__":
    unittest.main(verbosity=2)

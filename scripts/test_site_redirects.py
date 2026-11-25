"""Testes dos redirects de URLs antigas (#102). Rodar: python3 scripts/test_site_redirects.py"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cutover_redirects as C  # noqa: E402
import site_redirects as S  # noqa: E402

POSTS = Path(__file__).resolve().parent.parent / "blog-src/src/content/posts"
APEX, WWW = "guilhermefavaron.com.br", "www.guilhermefavaron.com.br"

# O plano postado na #102, escrito à parte (não usa OLD_POSTS): caminho antigo -> post.
PLAN = {
    "analise-preditiva-tomada-decisao-modelos-empresariais": "analise-preditiva-tomada-decisao-producao",
    "arquitetura-ia-empresarial-definitiva": "a-arquitetura-definitiva-de-ia-para",
    "construindo-times-ia-lideranca-tecnica-transformacao-empresarial": "construindo-times-de-ia-lideranca",
    "criancas-conectadas-pesquisa-sobre-como-as-familias-equilibram-tecnologia-e-bem-estar-parte-1":
        "criancas-conectadas-pesquisa-sobre-como-as-familias-equilibram-tecnologia-e-bem-estar-parte-1-0f6d9e9adf6a",
    "customer-analytics-metodologias-segmentacao-comportamental": "ab-testing-e-experimentacao-cientifica",
    "customer-analytics-segmentacao-personalizacao-dados": "customer-analytics-e-segmentacao",
    "data-pipelines-empresariais-silos-dados-business-intelligence": "data-pipelines-empresariais-como",
    "desmistificando-gpus-em-ia": "desmistificando-gpus-em-ia-79e168b03ccd",
    "fine-tuning-vs-prompt-engineering-decisoes-roi": "fine-tuning-vs-prompt-engineering",
    "ia-brasileira-sabia-2-amazonia-ia-maritaca-ai-inteligencia-artificial-nacional":
        "ia-brasileira-sabia-3-amazonia-ia-maritaca-ai-inteligencia-artificial-nacional",
    "ia-generativa-producao-custos-performance-escalabilidade": "ia-generativa-em-producao-custos",
    "langgraph-workflows-complexos-ia-orquestracao": "langgraph-orquestrando-workflows",
    "microservices-vs-monolith-decisoes-arquiteturais-competitividade": "microservices-vs-monolith-decisoes",
    "modernizacao-sistemas-legados-vantagem-competitiva": "modernizacao-de-sistemas-legados",
    "o-futuro-das-organizacoes-alem-da-tecnologia-e-processos": "o-futuro-das-organizacoes-alem-da",
    "o-surgimento-da-ia-geral-super-humana":
        "o-surgimento-da-ia-geral-super-humana-nao-sera-um-evento-o-avanco-sera-progressivo-dbf420240ecc",
    "streaming-data-em-escala-arquiteturas-para-analytics-sem-latencia-analitica":
        "streaming-data-em-escala-arquiteturas",
}
# A escolha registrada na #106, escrita à parte: duplicata absorvida -> versão original.
DUPS = {
    "a-era-dos-prototipos-acabou-como":
        "a-era-dos-prototipos-acabou-como-a-ia-esta-revolucionando-o-desenvolvimento-de-software",
    "sabia-2-o-rival-brasileiro-do-chatgpt40":
        "sabia-2-o-novo-modelo-de-linguagem-em-portugues-que-rivaliza-com-gpt-4-maritaca-ai",
}
# #151 e #166: as quatro ferramentas movidas da raiz para /tools/ e depois para /ferramentas/, sem cadeia.
PDF_OLD_PATH = "/seo-geo-aeo-framework/Framework-SEO-AEO_GEO_ GuilhermeFavaron(20251201).pdf"
PDF_TOOLS_PATH = "/tools/seo-geo-aeo-framework/Framework-SEO-AEO_GEO_ GuilhermeFavaron(20251201).pdf"
PDF_NEW_PATH = "/ferramentas/seo-geo-aeo-framework/Framework-SEO-AEO_GEO_ GuilhermeFavaron(20251201).pdf"
PDF_OLD_ENCODED = "/seo-geo-aeo-framework/Framework-SEO-AEO_GEO_%20GuilhermeFavaron(20251201).pdf"
PDF_TOOLS_ENCODED = "/tools/seo-geo-aeo-framework/Framework-SEO-AEO_GEO_%20GuilhermeFavaron(20251201).pdf"
PDF_NEW_ENCODED = "/ferramentas/seo-geo-aeo-framework/Framework-SEO-AEO_GEO_%20GuilhermeFavaron(20251201).pdf"
PDF_OLD_MAP = {PDF_OLD_PATH: PDF_OLD_ENCODED, PDF_TOOLS_PATH: PDF_TOOLS_ENCODED}
TOOLS = {
    "/mapa-decisao-ia/": "/ferramentas/mapa-decisao-ia/",
    "/mapa-decisao-ia/enviado/": "/ferramentas/mapa-decisao-ia/enviado/",
    "/planilha-custos-ia/": "/ferramentas/planilha-custos-ia/",
    "/planilha-custos-ia/enviado/": "/ferramentas/planilha-custos-ia/enviado/",
    "/llm-ranking/": "/ferramentas/llm-ranking/",
    "/seo-geo-aeo-framework/": "/ferramentas/seo-geo-aeo-framework/framework-seo-aeo.html",
    "/seo-geo-aeo-framework/framework-seo-aeo.html": "/ferramentas/seo-geo-aeo-framework/framework-seo-aeo.html",
    PDF_OLD_PATH: PDF_NEW_PATH,
    "/tools/": "/ferramentas/",
    "/tools/mapa-decisao-ia/": "/ferramentas/mapa-decisao-ia/",
    "/tools/mapa-decisao-ia/enviado/": "/ferramentas/mapa-decisao-ia/enviado/",
    "/tools/planilha-custos-ia/": "/ferramentas/planilha-custos-ia/",
    "/tools/planilha-custos-ia/enviado/": "/ferramentas/planilha-custos-ia/enviado/",
    "/tools/llm-ranking/": "/ferramentas/llm-ranking/",
    "/tools/seo-geo-aeo-framework/": "/ferramentas/seo-geo-aeo-framework/framework-seo-aeo.html",
    "/tools/seo-geo-aeo-framework/framework-seo-aeo.html": "/ferramentas/seo-geo-aeo-framework/framework-seo-aeo.html",
    PDF_TOOLS_PATH: PDF_NEW_PATH,
}
# Mudanças de endereço do próprio site (#146), escritas à parte: origem -> destino.
MOVES = {
    "/blog/privacidade/": "/privacidade/",
}
TOTAL = 17 * 2 + 2 * 2 + 1 * 2 + 13 * 2 + 4  # 70 itens na lista
# Grupo C da #120 (decisão do Gui): fica em 404, fora da lista.
GROUP_C_ENCODED = "como-a-intelig%c3%aancia-artificial-vai-transformar-a-lideran%c3%a7a-e-os-neg%c3%b3cios-no-futuro"
# Location que a zona antiga devolveu ao vivo em 24/11 para o caminho com acentos (curl -I).
ENCODED_LIVE = ("https://guifav.dev/blog/como-a-intelig%C3%AAncia-artificial-vai-transformar-a-lideran%C3%A7a-e-os-"
                "neg%C3%B3cios-no-futuro/")


def end_to_end(url: str) -> str | None:
    """Caminho antigo -> regra do cutover -> lista. Devolve o destino final ou None."""
    p = url.split("://", 1)[1]
    host, path = p.split("/", 1)
    mid = C.simulate(host, "/" + path)
    return None if mid is None else S.simulate(mid)


class PlanTest(unittest.TestCase):
    def test_table_is_exactly_the_posted_plan(self):
        self.assertEqual(dict(S.OLD_POSTS), PLAN)
        self.assertEqual(len(S.OLD_POSTS), 17)

    def test_duplicates_are_exactly_the_choice_posted_on_106(self):
        self.assertEqual(dict(S.DUPLICATES), DUPS)

    def test_moved_tools_table_and_redirect_simulation(self):
        self.assertEqual(dict(S.MOVED_TOOLS), TOOLS)
        repo = Path(__file__).resolve().parent.parent
        for old_path, new_path in TOOLS.items():
            enc_old = PDF_OLD_MAP.get(old_path, old_path)
            enc_new = PDF_NEW_ENCODED if new_path == PDF_NEW_PATH else new_path
            want = f"https://guifav.dev{enc_new}"
            forms = (enc_old, enc_old.rstrip("/")) if enc_old.endswith("/") else (enc_old,)
            for path in forms:
                for scheme in ("https", "http"):
                    url = f"{scheme}://guifav.dev{path}"
                    self.assertEqual(S.simulate(url), want, url)
            self.assertIsNone(S.simulate(want), want)  # o destino não é origem
            self.assertFalse(S._repo_page(repo, old_path).exists(), old_path)
            self.assertTrue(S._repo_page(repo, new_path).is_file(), new_path)
        for url in ("https://guifav.dev/tools/", "https://guifav.dev/tools",
                    "http://guifav.dev/tools/", "http://guifav.dev/tools"):
            self.assertEqual(S.simulate(url), "https://guifav.dev/ferramentas/", url)
        self.assertIsNone(S.simulate("https://guifav.dev/ferramentas/"))
        self.assertIsNone(S.simulate("https://guifav.dev/ferramentas"))

    def test_no_target_is_another_source_no_chain(self):
        # #166: nenhum destino da lista é outra origem (nem com nem sem barra); zero cadeias.
        reds = S.redirects()
        sources = {r.source for r in reds}
        for r in reds:
            target_no_scheme = r.target.removeprefix("https://")
            self.assertNotIn(target_no_scheme, sources, r)
            self.assertNotIn(target_no_scheme.rstrip("/"), sources, r)
            self.assertIsNone(S.simulate(r.target, reds), r)

    def test_framework_pdf_redirect_is_encoded_for_cloudflare_and_unique(self):
        repo = Path(__file__).resolve().parent.parent
        self.assertTrue((repo / PDF_NEW_PATH.lstrip("/")).is_file())
        self.assertFalse((repo / PDF_OLD_PATH.lstrip("/")).exists())
        self.assertFalse((repo / PDF_TOOLS_PATH.lstrip("/")).exists())
        expected_target = f"https://guifav.dev{PDF_NEW_ENCODED}"
        # Normalização RFC 3986 da Cloudflare (http.request.full_uri): espaço vira %20, () são sub-delims e ficam literais.
        self.assertEqual(C.normalize(PDF_OLD_PATH), PDF_OLD_ENCODED)
        self.assertEqual(C.normalize(PDF_TOOLS_PATH), PDF_TOOLS_ENCODED)
        self.assertEqual(C.normalize(PDF_NEW_PATH), PDF_NEW_ENCODED)
        reds = S.redirects()
        sources = [r.source for r in reds]
        self.assertEqual(len(set(sources)), len(sources))
        items = S.list_items(reds)
        by_src = {it["redirect"]["source_url"]: it["redirect"] for it in items}
        self.assertNotIn(" ", expected_target)
        self.assertNotIn("%28", expected_target)
        self.assertNotIn("%29", expected_target)
        for raw_old, enc_old in PDF_OLD_MAP.items():
            expected_source = f"guifav.dev{enc_old}"
            self.assertEqual(sources.count(expected_source), 1)
            self.assertNotIn(expected_source + "/", sources)
            self.assertIn(expected_source, by_src)
            self.assertEqual(by_src[expected_source], {
                "source_url": expected_source,
                "target_url": expected_target,
                "status_code": 301,
                "preserve_query_string": False,
                "include_subdomains": False,
                "subpath_matching": False,
            })
            self.assertNotIn(" ", expected_source)
            self.assertNotIn("%28", expected_source)
            self.assertNotIn("%29", expected_source)
            for url in (f"https://{expected_source}", f"http://{expected_source}",
                        f"https://GUIFAV.DEV{enc_old}", f"https://{expected_source}?utm_source=x"):
                self.assertEqual(S.simulate(url), expected_target, url)
            for non_match in (expected_target, f"https://{expected_source}/", f"https://guifav.dev{raw_old}"):
                self.assertIsNone(S.simulate(non_match), non_match)
        for it in items:
            self.assertNotIn(" ", it["redirect"]["source_url"])
            self.assertNotIn(" ", it["redirect"]["target_url"])

    def test_site_moves_are_separate_from_posts_and_carry_privacy(self):
        self.assertEqual(dict(S.SITE_MOVES), MOVES)
        self.assertNotIn("privacidade", dict(S.OLD_POSTS))
        self.assertNotIn("privacidade", dict(S.DUPLICATES))
        want = "https://guifav.dev/privacidade/"
        for url in ("https://guifav.dev/blog/privacidade/", "https://guifav.dev/blog/privacidade",
                    "http://guifav.dev/blog/privacidade/", "http://guifav.dev/blog/privacidade",
                    "https://guifav.dev/blog/privacidade/#cookies", "https://guifav.dev/blog/privacidade#materiais",
                    "https://guifav.dev/blog/privacidade/#origem", "https://guifav.dev/blog/privacidade/#quem-processa"):
            self.assertEqual(S.simulate(url), want, url)
        self.assertIsNone(S.simulate(want))
        # Domínio antigo -> cutover leva a /blog/privacidade/ -> lista leva a /privacidade/ (sem mexer no cutover).
        for host in (APEX, WWW):
            for path in ("/privacy", "/privacy/", "/tos", "/tos/",
                         "/p/politica-de-privacidade", "/p/termos-e-condicoes-de-uso"):
                self.assertEqual(end_to_end(f"https://{host}{path}"), want, (host, path))

    def test_absorbed_copy_stays_in_the_repo_without_index_and_the_winner_is_indexable(self):
        live = S.indexable_posts()
        for dup, win in DUPS.items():
            self.assertTrue((POSTS / f"{dup}.md").is_file(), dup)
            self.assertNotIn(dup, live)
            self.assertIn(win, live)

    def test_absorbed_url_old_domain_and_both_slash_forms_end_on_the_winner(self):
        for dup, win in DUPS.items():
            want = f"https://guifav.dev/blog/{win}/"
            for url in (f"https://guifav.dev/blog/{dup}/", f"http://guifav.dev/blog/{dup}"):
                self.assertEqual(S.simulate(url), want, url)
            for host in (APEX, WWW):
                for path in (f"/p/{dup}", f"/p/{dup}/", f"/post/{dup}"):
                    self.assertEqual(end_to_end(f"https://{host}{path}"), want, (host, path))
            self.assertIsNone(S.simulate(want))  # a vencedora não é origem

    def test_group_c_path_stays_out_of_the_list(self):
        self.assertNotIn(GROUP_C_ENCODED, dict(S.OLD_POSTS))
        self.assertIsNone(S.simulate(S.first_hop(GROUP_C_ENCODED)))

    def test_real_repo_has_no_problems(self):
        self.assertEqual(S.problems(), [])

    def test_every_target_is_a_published_post_file_outside_duplicates(self):
        # Posts podados na #107 continuam publicados com HTTP 200 e noindex (sem apagar a URL nem quebrar o 301).
        for new in PLAN.values():
            f = POSTS / f"{new}.md"
            self.assertTrue(f.is_file(), new)
            self.assertNotIn(new, DUPS)

    def test_no_old_path_is_a_post_today(self):
        for old in PLAN:
            self.assertFalse((POSTS / f"{old}.md").exists(), old)

    def test_every_old_path_ends_on_its_post_from_both_hosts_with_and_without_slash(self):
        for old, new in PLAN.items():
            for host in (APEX, WWW):
                for suffix in ("", "/"):
                    for scheme in ("https", "http"):
                        url = f"{scheme}://{host}/post/{old}{suffix}"
                        self.assertEqual(end_to_end(url), f"https://guifav.dev/blog/{new}/", url)

    def test_encoded_path_matches_the_location_the_zone_really_sends(self):
        self.assertEqual(S.first_hop(GROUP_C_ENCODED), ENCODED_LIVE)
        with mock.patch.object(S, "OLD_POSTS", ((GROUP_C_ENCODED, "novo"),)):
            self.assertIn(ENCODED_LIVE.removeprefix("https://"), {r.source for r in S.redirects()})

    def test_every_post_keeps_the_one_hop_cutover_and_only_duplicates_are_in_the_list(self):
        # Todos os posts, não uma amostra (P2 do agy no #123): só as duplicatas absorvidas ganham o segundo salto.
        slugs = sorted(p.stem for p in POSTS.glob("*.md"))
        self.assertGreater(len(slugs), 200)
        for slug in slugs:
            with self.subTest(slug=slug):
                mid = C.simulate(WWW, f"/post/{slug}")
                self.assertEqual(mid, f"https://guifav.dev/blog/{slug}/")
                want = f"https://guifav.dev/blog/{DUPS[slug]}/" if slug in DUPS else None
                self.assertEqual(S.simulate(mid), want)

    def test_list_has_both_slash_forms_and_nothing_else(self):
        reds = S.redirects()
        self.assertEqual(len(reds), TOTAL)
        for old, new in PLAN.items():
            src = C.simulate(WWW, f"/post/{old}").removeprefix("https://")
            got = {r.source: r.target for r in reds if r.source.rstrip("/") == src.rstrip("/")}
            self.assertEqual(got, {src: f"https://guifav.dev/blog/{new}/",
                                   src.rstrip("/"): f"https://guifav.dev/blog/{new}/"})
        for old_path, new_path in TOOLS.items():
            enc_old = PDF_OLD_MAP.get(old_path, old_path)
            enc_new = PDF_NEW_ENCODED if new_path == PDF_NEW_PATH else new_path
            src = f"guifav.dev{enc_old}"
            want = f"https://guifav.dev{enc_new}"
            expected = {src: want, src.rstrip("/"): want} if enc_old.endswith("/") else {src: want}
            got = {r.source: r.target for r in reds if r.source in expected}
            self.assertEqual(got, expected)
        for old, new in MOVES.items():
            src = S.HOST + old
            got = {r.source: r.target for r in reds if r.source.rstrip("/") == src.rstrip("/")}
            self.assertEqual(got, {src: f"https://{S.HOST}{new}",
                                   src.rstrip("/"): f"https://{S.HOST}{new}"})


class SimulateTest(unittest.TestCase):
    R = [S.Redirect("guifav.dev/blog/velho/", "https://guifav.dev/blog/novo/")]

    def test_scheme_query_and_host_case_do_not_matter(self):
        for url in ("https://guifav.dev/blog/velho/", "http://guifav.dev/blog/velho/",
                    "https://guifav.dev/blog/velho/?utm_source=x", "https://GUIFAV.DEV/blog/velho/"):
            self.assertEqual(S.simulate(url, self.R), "https://guifav.dev/blog/novo/", url)

    def test_path_is_exact_case_sensitive_and_without_subpaths(self):
        for url in ("https://guifav.dev/blog/velho", "https://guifav.dev/blog/Velho/",
                    "https://guifav.dev/blog/velho/x/", "https://www.guifav.dev/blog/velho/",
                    "https://guilhermefavaron.com.br/blog/velho/"):
            self.assertIsNone(S.simulate(url, self.R), url)


def _posts(tmp: Path, files: dict[str, str]) -> Path:
    for name, fm in files.items():
        (tmp / f"{name}.md").write_text(f"---\ntitle: x\n{fm}---\ncorpo\n", encoding="utf-8")
    return tmp


class ProblemsTest(unittest.TestCase):
    def run_with(self, table, posts, reds=None, dups=(), moves=(), tools=(), files=()):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(S, "OLD_POSTS", table), \
                mock.patch.object(S, "DUPLICATES", dups), mock.patch.object(S, "SITE_MOVES", moves), \
                mock.patch.object(S, "MOVED_TOOLS", tools):
            root = Path(d)
            posts_dir = root / "posts"
            posts_dir.mkdir()
            p = _posts(posts_dir, posts)
            for rel in files:
                f = root / rel
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text("<html></html>", encoding="utf-8")
            return S.problems(S.redirects() if reds is None else reds, p, repo=root)

    def test_site_moves_must_point_to_existing_page_and_remove_old_source(self):
        ok_files = ("privacidade/index.html",)
        self.assertEqual(self.run_with((("velho", "novo"),), {"novo": ""}, moves=(("/blog/privacidade/", "/privacidade/"),),
                                       files=ok_files), [])
        cases = (
            (("/blog/privacidade/", "/privacidade/"), (), "não existe no repo"),
            (("/blog/privacidade/", "/privacidade/"), (*ok_files, "blog-src/src/pages/privacidade.astro"), "privacidade.astro"),
            (("/antigo/", "/privacidade/"), (*ok_files, "antigo/index.html"), "esconderia a página"),
            (("/blog/novo/", "/privacidade/"), ok_files, "post indexável"),
            (("/privacidade/", "/privacidade/"), ok_files, "iguais"),
            (("/blog/privacidade", "/privacidade/"), ok_files, "fora do formato"),
            (("/blog/privacidade/", "/privacidade/#cookies"), ok_files, "fora do formato"),
            (("/blog/velho/", "/privacidade/"), ok_files, "OLD_POSTS"),
        )
        for move, files, word in cases:
            with self.subTest(move=move, files=files):
                got = self.run_with((("velho", "novo"),), {"novo": ""}, moves=(move,), files=files)
                self.assertTrue(any(word in x for x in got), got)
        dup_moves = (("/blog/privacidade/", "/privacidade/"), ("/blog/privacidade/", "/privacidade/"))
        got = self.run_with((("velho", "novo"),), {"novo": ""}, moves=dup_moves, files=ok_files)
        self.assertTrue(any("repetido em SITE_MOVES" in x for x in got), got)
        # Fragmento no target_url sobrescreveria a âncora do navegador (#cookies): recusado.
        bad_frag = [S.Redirect("guifav.dev/blog/privacidade/", "https://guifav.dev/privacidade/#cookies")]
        got = self.run_with((("velho", "novo"),), {"novo": ""}, reds=bad_frag)
        self.assertTrue(any("destino fora" in x for x in got), got)

    def test_duplicate_must_stay_without_index_and_point_to_an_indexable_post(self):
        ok = {"novo": "", "dup": "index: false\n", "orig": ""}
        self.assertEqual(self.run_with((("velho", "novo"),), ok, dups=(("dup", "orig"),)), [])
        cases = (
            ({"novo": "", "dup": "", "orig": ""}, (("dup", "orig"),), "index: false"),      # cópia ainda indexável
            ({"novo": "", "orig": ""}, (("dup", "orig"),), "index: false"),                  # cópia apagada do repo
            ({"novo": "", "dup": "index: false\n"}, (("dup", "orig"),), "não é post indexável"),
            (ok, (("dup", "dup"),), "não é post indexável"),                                  # aponta para si
            (ok, (("dup", "orig"), ("dup", "orig")), "repetida"),
            (ok, (("velho", "orig"),), "repetida"),                                          # também em OLD_POSTS
        )
        for posts, dups, word in cases:
            with self.subTest(dups=dups, posts=posts):
                got = self.run_with((("velho", "novo"),), posts, dups=dups)
                self.assertTrue(any(word in x for x in got), got)

    def test_target_must_be_a_published_post_outside_duplicates(self):
        self.assertEqual(self.run_with((("velho", "novo"),), {"novo": ""}), [])
        # Post com index: false (#107) continua publicado (200) e pode receber 301 de URL antiga sem virar 404.
        self.assertEqual(self.run_with((("velho", "novo"),), {"novo": "index: false\n"}), [])
        for posts in ({}, {"outro": ""}):
            with self.subTest(posts=posts):
                self.assertTrue(any("não é post publicado" in p for p in self.run_with((("velho", "novo"),), posts)))
        got = self.run_with((("velho", "novo"),), {"novo": "index: false\n", "orig": ""}, dups=(("novo", "orig"),))
        self.assertTrue(any("não é post publicado" in p for p in got), got)

    def test_noindex_detection_ignores_body_and_other_keys(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p / "a.md").write_text("---\ntitle: x\nnewsletter: false\n---\nindex: false\n", encoding="utf-8")
            (p / "b.md").write_text("---\ntitle: x\nindex: false\n---\n", encoding="utf-8")
            (p / "c.md").write_text("---\ntitle: x\nindex: true\n---\n", encoding="utf-8")
            self.assertEqual(S.indexable_posts(p), {"a", "c"})

    def test_old_path_that_is_a_post_is_refused(self):
        got = self.run_with((("velho", "novo"),), {"novo": "", "velho": "index: false\n"})
        self.assertTrue(any("já é um post" in p for p in got), got)

    def test_source_that_is_an_indexable_post_is_refused(self):
        reds = [S.Redirect("guifav.dev/blog/novo/", "https://guifav.dev/blog/outro/")]
        got = self.run_with((("velho", "outro"),), {"outro": "", "novo": ""}, reds)
        self.assertTrue(any("esconderia a página" in p for p in got), got)

    def test_chain_duplicate_and_foreign_host_are_refused(self):
        cases = {
            "cadeia": [S.Redirect("guifav.dev/blog/a/", "https://guifav.dev/blog/b/"),
                       S.Redirect("guifav.dev/blog/b/", "https://guifav.dev/blog/novo/")],
            "repetida": [S.Redirect("guifav.dev/blog/a/", "https://guifav.dev/blog/novo/")] * 2,
            "fora de": [S.Redirect("www.guifav.dev/blog/a/", "https://guifav.dev/blog/novo/")],
            "destino fora": [S.Redirect("guifav.dev/blog/a/", "https://guifav.dev/novo")],
        }
        for word, reds in cases.items():
            with self.subTest(word):
                got = self.run_with((("velho", "novo"),), {"novo": ""}, reds)
                self.assertTrue(any(word in p for p in got), got)

    def test_bad_old_path_and_repeated_old_path_are_refused(self):
        for table, word in (((("Velho", "novo"),), "fora do formato"), ((("a b", "novo"),), "fora do formato"),
                            ((("velho", "novo"), ("velho", "novo")), "repetido")):
            with self.subTest(table=table):
                with tempfile.TemporaryDirectory() as d, mock.patch.object(S, "OLD_POSTS", table), \
                        mock.patch.object(S, "DUPLICATES", ()), mock.patch.object(S, "SITE_MOVES", ()), \
                        mock.patch.object(S, "MOVED_TOOLS", ()):
                    got = S.problems([], _posts(Path(d), {"novo": ""}))
                self.assertTrue(any(word in p for p in got), got)

    def test_moved_tools_origin_must_be_gone_and_target_must_exist_in_repo(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            posts = _posts(root / "posts", {"novo": ""}) if (root / "posts").mkdir() is None else root / "posts"
            repo = root / "repo"
            (repo / "ferramentas/ferramenta").mkdir(parents=True)
            (repo / "ferramentas/ferramenta/index.html").write_text("<html></html>", encoding="utf-8")
            with mock.patch.object(S, "OLD_POSTS", (("velho", "novo"),)), \
                    mock.patch.object(S, "DUPLICATES", ()), \
                    mock.patch.object(S, "SITE_MOVES", ()), \
                    mock.patch.object(S, "MOVED_TOOLS", (("/ferramenta/", "/ferramentas/ferramenta/"),
                                                         ("/tools/ferramenta/", "/ferramentas/ferramenta/"))):
                self.assertEqual(S.problems(posts_dir=posts, repo=repo), [])
                # Origem ainda existe no repo -> recusado
                (repo / "ferramenta").mkdir()
                (repo / "ferramenta/index.html").write_text("<html></html>", encoding="utf-8")
                got = S.problems(posts_dir=posts, repo=repo)
                self.assertTrue(any("ainda existe no repo" in p for p in got), got)
                (repo / "ferramenta/index.html").unlink()
                (repo / "tools/ferramenta").mkdir(parents=True)
                (repo / "tools/ferramenta/index.html").write_text("<html></html>", encoding="utf-8")
                got = S.problems(posts_dir=posts, repo=repo)
                self.assertTrue(any("ainda existe no repo" in p for p in got), got)
                (repo / "tools/ferramenta/index.html").unlink()
                # Destino ausente no repo -> recusado
                (repo / "ferramentas/ferramenta/index.html").unlink()
                got = S.problems(posts_dir=posts, repo=repo)
                self.assertTrue(any("não existe no repo" in p for p in got), got)
            # Origem em /ferramentas/ ou repetida ou destino fora de /ferramentas/ -> recusados
            (repo / "ferramentas/ferramenta/index.html").write_text("<html></html>", encoding="utf-8")
            for tools, word in (
                ((("/ferramentas/x/", "/ferramentas/ferramenta/"),), "fora do formato"),
                ((("/x/", "/ferramenta/"),), "destino fora de /ferramentas/"),
                ((("/x/", "/tools/ferramenta/"),), "destino fora de /ferramentas/"),
                ((("/x/", "/ferramentas/ferramenta/"), ("/x/", "/ferramentas/ferramenta/")), "repetido"),
            ):
                with self.subTest(tools=tools):
                    with mock.patch.object(S, "OLD_POSTS", (("velho", "novo"),)), \
                            mock.patch.object(S, "DUPLICATES", ()), \
                            mock.patch.object(S, "SITE_MOVES", ()), \
                            mock.patch.object(S, "MOVED_TOOLS", tools):
                        got = S.problems([], posts_dir=posts, repo=repo)
                    self.assertTrue(any(word in p for p in got), got)
            # Cadeia (/ferramenta/ -> /tools/ferramenta/ -> /ferramentas/ferramenta/) -> recusada
            chain = [S.Redirect("guifav.dev/ferramenta/", "https://guifav.dev/tools/ferramenta/"),
                     S.Redirect("guifav.dev/tools/ferramenta/", "https://guifav.dev/ferramentas/ferramenta/")]
            with mock.patch.object(S, "OLD_POSTS", (("velho", "novo"),)), \
                    mock.patch.object(S, "DUPLICATES", ()), \
                    mock.patch.object(S, "SITE_MOVES", ()), \
                    mock.patch.object(S, "MOVED_TOOLS", ()):
                got = S.problems(chain, posts_dir=posts, repo=repo)
            self.assertTrue(any("cadeia de redirects" in p for p in got), got)
            # Origem duplicada entre MOVED_TOOLS e SITE_MOVES -> recusada
            (repo / "privacidade").mkdir(parents=True)
            (repo / "privacidade/index.html").write_text("<html></html>", encoding="utf-8")
            with mock.patch.object(S, "OLD_POSTS", (("velho", "novo"),)), \
                    mock.patch.object(S, "DUPLICATES", ()), \
                    mock.patch.object(S, "MOVED_TOOLS", (("/ferramenta/", "/ferramentas/ferramenta/"),)), \
                    mock.patch.object(S, "SITE_MOVES", (("/ferramenta/", "/privacidade/"),)):
                got = S.problems(posts_dir=posts, repo=repo)
            self.assertTrue(any("já está em MOVED_TOOLS" in p for p in got), got)
            self.assertTrue(any("origem repetida na lista" in p for p in got), got)

    def test_framework_pdf_exception_does_not_loosen_other_tool_paths(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "posts").mkdir()
            posts = _posts(root / "posts", {"novo": ""})
            repo = root / "repo"
            pdf = repo / PDF_NEW_PATH.lstrip("/")
            pdf.parent.mkdir(parents=True)
            pdf.write_bytes(b"%PDF-1.4\n")
            with mock.patch.object(S, "OLD_POSTS", (("velho", "novo"),)), \
                    mock.patch.object(S, "DUPLICATES", ()), \
                    mock.patch.object(S, "SITE_MOVES", ()), \
                    mock.patch.object(S, "MOVED_TOOLS", ((PDF_OLD_PATH, PDF_NEW_PATH), (PDF_TOOLS_PATH, PDF_NEW_PATH))):
                self.assertEqual(S.problems(posts_dir=posts, repo=repo), [])
                for raw_old in (PDF_OLD_PATH, PDF_TOOLS_PATH):
                    old_pdf = repo / raw_old.lstrip("/")
                    old_pdf.parent.mkdir(parents=True, exist_ok=True)
                    old_pdf.write_bytes(b"%PDF-1.4\n")
                    got = S.problems(posts_dir=posts, repo=repo)
                    self.assertTrue(any("ainda existe no repo" in p for p in got), got)
                    old_pdf.unlink()
                pdf.unlink()
                got = S.problems(posts_dir=posts, repo=repo)
                self.assertTrue(any("não existe no repo" in p for p in got), got)
                pdf.write_bytes(b"%PDF-1.4\n")
            # Outros caminhos com espaço, parênteses, maiúsculas ou .pdf continuam recusados.
            bad_pairs = (
                (("/ferramenta com espaco/", "/ferramentas/ferramenta com espaco/"), "fora do formato"),
                (("/ferramenta(1)/", "/ferramentas/ferramenta(1)/"), "fora do formato"),
                (("/seo-geo-aeo-framework/Arquivo.html", "/ferramentas/seo-geo-aeo-framework/Arquivo.html"), "fora do formato"),
                (("/seo-geo-aeo-framework/Outro Arquivo(20251201).pdf",
                  "/ferramentas/seo-geo-aeo-framework/Outro Arquivo(20251201).pdf"), "fora do formato"),
                ((PDF_NEW_PATH, PDF_NEW_PATH), "fora do formato"),
                (("/ferramenta/", "/ferramentas/outro/arquivo.pdf"), "destino fora de /ferramentas/"),
                ((PDF_OLD_PATH, "/ferramentas/seo-geo-aeo-framework/Outro(20251201).pdf"), "destino fora de /ferramentas/"),
            )
            for pair, word in bad_pairs:
                with self.subTest(pair=pair):
                    with mock.patch.object(S, "OLD_POSTS", (("velho", "novo"),)), \
                            mock.patch.object(S, "DUPLICATES", ()), \
                            mock.patch.object(S, "SITE_MOVES", ()), \
                            mock.patch.object(S, "MOVED_TOOLS", (pair,)):
                        got = S.problems([], posts_dir=posts, repo=repo)
                    self.assertTrue(any(word in p for p in got), got)
            # Lista final recusa espaço literal na origem ou destino, .pdf arbitrário e origem codificada que existe no repo.
            bad_reds = (
                ([S.Redirect(f"guifav.dev{PDF_OLD_PATH}", f"https://guifav.dev{PDF_NEW_ENCODED}")], "não codificado"),
                ([S.Redirect(f"guifav.dev{PDF_TOOLS_PATH}", f"https://guifav.dev{PDF_NEW_ENCODED}")], "não codificado"),
                ([S.Redirect(f"guifav.dev{PDF_OLD_ENCODED}", f"https://guifav.dev{PDF_NEW_PATH}")], "destino fora"),
                ([S.Redirect("guifav.dev/x.pdf", "https://guifav.dev/ferramentas/x.pdf")], "destino fora"),
                ([S.Redirect(f"guifav.dev{PDF_NEW_ENCODED}", "https://guifav.dev/ferramentas/ferramenta/")], "esconderia a página"),
            )
            for reds, word in bad_reds:
                with self.subTest(reds=reds):
                    with mock.patch.object(S, "OLD_POSTS", (("velho", "novo"),)), \
                            mock.patch.object(S, "DUPLICATES", ()), \
                            mock.patch.object(S, "SITE_MOVES", ()), \
                            mock.patch.object(S, "MOVED_TOOLS", ()):
                        got = S.problems(reds, posts_dir=posts, repo=repo)
                    self.assertTrue(any(word in p for p in got), got)

    def test_limit_of_the_free_plan_counts_both_lists_of_the_pair(self):
        def reds(n):
            return [S.Redirect(f"guifav.dev/blog/x{i}/", "https://guifav.dev/blog/novo/") for i in range(n)]
        half = S.MAX_ITEMS // len(S.LISTS)
        self.assertFalse(any("plano Free" in p for p in self.run_with((("velho", "novo"),), {"novo": ""}, reds(half))))
        got = self.run_with((("velho", "novo"),), {"novo": ""}, reds(half + 1))
        self.assertTrue(any("plano Free" in p for p in got), got)


class PayloadTest(unittest.TestCase):
    def test_items_are_exact_301_without_query_subdomains_or_subpaths(self):
        items = S.list_items()
        self.assertEqual(len(items), TOTAL)
        for it in items:
            r = it["redirect"]
            self.assertEqual(set(r), {"source_url", "target_url", "status_code", "preserve_query_string",
                                      "include_subdomains", "subpath_matching"})
            self.assertEqual((r["status_code"], r["preserve_query_string"], r["include_subdomains"],
                              r["subpath_matching"]), (301, False, False, False))
            self.assertFalse(r["source_url"].startswith("http"))
            self.assertNotIn("#", r["target_url"])  # sem fragmento no destino: o navegador preserva a âncora

    def test_rule_enables_exactly_the_given_list_of_the_pair(self):
        self.assertEqual(S.LISTS, ("guifav_redirects_a", "guifav_redirects_b"))
        for name in S.LISTS:
            r = S.rule(name)
            self.assertEqual(r["expression"], f"http.request.full_uri in ${name}")
            self.assertEqual(r["action_parameters"], {"from_list": {"name": name, "key": "http.request.full_uri"}})
            self.assertEqual((r["action"], r["enabled"], r["description"]), ("redirect", True, S.RULE_DESCRIPTION))
        with self.assertRaises(ValueError):
            S.rule("guifav_redirects")


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeCF:
    """Conta Cloudflare em memória, com o registro das chamadas."""

    def __init__(self, phase=None, lists=None, op_status=("pending", "completed"), echo_items=True,
                 echo_rule=True, page=20, items=None, decode_source=True, readback_map=None):
        self.phase = phase  # None = fase sem ruleset
        self.lists = lists if lists is not None else []
        self.items: dict[str, list[dict]] = dict(items or {})  # itens por id de lista
        self.op_status = list(op_status)
        self.echo_items, self.echo_rule, self.page = echo_items, echo_rule, page
        self.decode_source, self.readback_map = decode_source, readback_map
        self.calls: list[tuple[str, str]] = []
        self.raw_cursors: list[str] = []
        self.read_items: list[dict] = []

    def __call__(self, req, timeout=None):
        m, url = req.get_method(), req.full_url.removeprefix(S.API)
        body = json.loads(req.data) if req.data else None
        self.calls.append((m, url.split("?")[0]))
        ok = lambda res, **kw: _Resp(json.dumps({"success": True, "result": res, "errors": [], **kw}).encode())  # noqa
        if url.endswith(f"/phases/{S.PHASE}/entrypoint"):
            if m == "GET":
                if self.phase is None:
                    return _Resp(json.dumps({"success": False, "errors": [{"code": 10003}]}).encode())
                return ok({"rules": self.phase})
            self.phase = body["rules"] if self.echo_rule else [{"description": "x"}]
            return ok({"id": "rs1", "rules": self.phase})
        if url.split("?")[0].endswith("/rules/lists") and m == "GET":
            return ok(self.lists)
        if url.endswith("/rules/lists") and m == "POST":
            new = {"id": "L-" + body["name"][-1], **body}
            self.lists.append(new)
            return ok(new)
        if "/items" in url and m == "PUT":
            self.items[url.split("/lists/")[1].split("/")[0]] = body if self.echo_items else body[:-1]
            return ok({"operation_id": "op1"})
        if "/bulk_operations/" in url:
            st = self.op_status.pop(0) if len(self.op_status) > 1 else self.op_status[0]
            return ok({"status": st, "error": "boom" if st == "failed" else None})
        if "/items" in url and m == "GET":
            start = 0
            if "cursor=" in url:
                raw = url.split("cursor=")[1]
                self.raw_cursors.append(raw)
                start = int(urllib.parse.unquote(raw).rsplit("=", 1)[1])
            items = self.items.get(url.split("/lists/")[1].split("/")[0], [])
            chunk = []
            for i, it in enumerate(items[start:start + self.page], start):
                red = {**it["redirect"], "preserve_path_suffix": True}
                if self.decode_source and isinstance(red.get("source_url"), str):
                    red["source_url"] = red["source_url"].replace("%20", " ")
                if self.readback_map is not None:
                    red = self.readback_map(red)
                row = {"id": str(i), "created_on": "x", "redirect": red}
                chunk.append(row)
                self.read_items.append(row)
            after = start + self.page
            cursors = {"after": f"c+/x={after}"} if after < len(items) else {}  # opaco, como os da Cloudflare
            return ok(chunk, result_info={"cursors": cursors})
        raise AssertionError(f"chamada inesperada {m} {url}")

    def writes(self):
        return [c for c in self.calls if c[0] != "GET"]


class ApplyTest(unittest.TestCase):
    def apply(self, cf):
        with contextlib.redirect_stdout(io.StringIO()):
            return S.apply("tok", "acc", opener=cf, sleep=lambda s: None)

    A, B = S.LISTS
    OLD = [{"redirect": {"source_url": "guifav.dev/blog/velho/", "target_url": "https://guifav.dev/blog/x/",
                         "status_code": 301, "preserve_query_string": False, "include_subdomains": False,
                         "subpath_matching": False}}]

    def live(self, on=None):
        """Conta com a regra ativa na lista `on` (que tem OLD) e a outra lista do par já criada."""
        lists = [{"id": "LA", "name": self.A, "kind": "redirect", "description": S.LIST_DESCRIPTION},
                 {"id": "LB", "name": self.B, "kind": "redirect", "description": S.LIST_DESCRIPTION}]
        rule = S.rule(on)
        return FakeCF(phase=[{"id": "r", **rule}], lists=lists,
                      items={"LA" if on == self.A else "LB": list(self.OLD)})

    def test_first_apply_creates_list_a_fills_it_reads_it_back_then_enables_the_rule(self):
        cf = FakeCF()
        self.assertEqual(self.apply(cf), 0)
        self.assertEqual(cf.writes(), [("POST", "/accounts/acc/rules/lists"),
                                       ("PUT", "/accounts/acc/rules/lists/L-a/items"),
                                       ("PUT", f"/accounts/acc/rulesets/phases/{S.PHASE}/entrypoint")])
        put_items = cf.calls.index(("PUT", "/accounts/acc/rules/lists/L-a/items"))
        reads = [i for i, c in enumerate(cf.calls) if c == ("GET", "/accounts/acc/rules/lists/L-a/items")]
        self.assertGreaterEqual(len(reads), 3)  # 53 itens em páginas de 20: o cursor foi seguido
        self.assertTrue(put_items < reads[0] and reads[-1] < len(cf.calls) - 1)
        self.assertEqual(cf.phase, [S.rule(self.A)])
        self.assertEqual(len(cf.items["L-a"]), TOTAL)
        self.assertEqual(cf.lists[0]["description"], S.LIST_DESCRIPTION)
        self.assertTrue(cf.raw_cursors and all("+" not in c and "/" not in c and "=" not in c for c in cf.raw_cursors),
                        cf.raw_cursors)  # cursor opaco codificado na query

    def test_update_writes_only_the_inactive_list_and_then_switches_the_rule(self):
        for on, other, other_id, on_id in ((self.A, self.B, "LB", "LA"), (self.B, self.A, "LA", "LB")):
            with self.subTest(on=on):
                cf = self.live(on)
                self.assertEqual(self.apply(cf), 0)
                self.assertEqual(cf.writes(), [("PUT", f"/accounts/acc/rules/lists/{other_id}/items"),
                                               ("PUT", f"/accounts/acc/rulesets/phases/{S.PHASE}/entrypoint")])
                self.assertEqual(cf.phase, [S.rule(other)])
                self.assertEqual(cf.items[on_id], self.OLD)  # a lista ativa antes não foi tocada
                self.assertEqual(len(cf.items[other_id]), TOTAL)

    def test_failed_update_keeps_the_active_rule_on_the_untouched_list(self):
        # Achado P1 da revisão de beb73ac: com a regra ativa, a falha de validação não pode deixá-la sobre uma
        # lista alterada e não conferida.
        for kw in ({"echo_items": False}, {"op_status": ("failed",)}, {"op_status": ("pending",)}):
            with self.subTest(**kw):
                cf = self.live(self.A)
                cf.echo_items = kw.get("echo_items", True)
                cf.op_status = list(kw.get("op_status", ("pending", "completed")))
                with self.assertRaises(SystemExit) as e:
                    self.apply(cf)
                self.assertIn(self.A, str(e.exception))
                self.assertEqual(cf.phase, [{"id": "r", **S.rule(self.A)}])
                self.assertEqual(cf.items["LA"], self.OLD)
                self.assertNotIn(("PUT", f"/accounts/acc/rulesets/phases/{S.PHASE}/entrypoint"), cf.calls)
                self.assertNotIn(("PUT", "/accounts/acc/rules/lists/LA/items"), cf.calls)

    def test_own_rule_on_an_unknown_list_or_two_own_rules_are_refused(self):
        weird = {**S.rule(self.A), "action_parameters": {"from_list": {"name": "outra", "key": "http.request.full_uri"}}}
        for phase in ([weird], [S.rule(self.A), S.rule(self.B)]):
            with self.subTest(phase=phase):
                cf = FakeCF(phase=phase)
                with self.assertRaises(SystemExit):
                    self.apply(cf)
                self.assertEqual(cf.writes(), [])

    def test_foreign_rule_in_the_phase_stops_before_any_write(self):
        # Inclusive regra alheia que liga uma lista do par: o PUT da fase a apagaria.
        for foreign in ({"description": "outra lista", "id": "r"},
                        {**S.rule(self.A), "description": "regra feita no painel", "id": "r"}):
            with self.subTest(foreign=foreign["description"]):
                cf = FakeCF(phase=[foreign])
                with self.assertRaises(SystemExit) as e:
                    self.apply(cf)
                self.assertIn("não são deste script", str(e.exception))
                self.assertEqual(cf.writes(), [])

    def test_list_with_the_same_name_of_another_kind_is_refused(self):
        for name in S.LISTS:
            with self.subTest(name=name):
                cf = FakeCF(lists=[{"id": "L9", "name": name, "kind": "ip", "description": S.LIST_DESCRIPTION}])
                with self.assertRaises(SystemExit) as e:
                    self.apply(cf)
                self.assertIn("de tipo 'ip'", str(e.exception))
                self.assertEqual(cf.writes(), [])

    def test_list_of_the_pair_with_another_description_is_not_adopted(self):
        for desc in (None, "", "lista feita no painel", S.LIST_DESCRIPTION + " "):
            for name in S.LISTS:
                with self.subTest(desc=desc, name=name):
                    cf = FakeCF(lists=[{"id": "L9", "name": name, "kind": "redirect", "description": desc}])
                    with self.assertRaises(SystemExit) as e:
                        self.apply(cf)
                    self.assertIn("outra descrição", str(e.exception))
                    self.assertEqual(cf.writes(), [])

    def test_list_problems_stop_before_any_call(self):
        cf = FakeCF()
        with mock.patch.object(S, "problems", return_value=["x"]), self.assertRaises(SystemExit):
            self.apply(cf)
        self.assertEqual(cf.calls, [])

    def test_rule_is_not_written_when_the_list_does_not_read_back_the_same(self):
        for cf in (FakeCF(echo_items=False), FakeCF(op_status=("failed",)), FakeCF(op_status=("pending",))):
            with self.subTest(op=cf.op_status, echo=cf.echo_items):
                with self.assertRaises(SystemExit):
                    self.apply(cf)
                self.assertNotIn(("PUT", f"/accounts/acc/rulesets/phases/{S.PHASE}/entrypoint"), cf.calls)

    def test_rule_answer_differing_is_an_error(self):
        with self.assertRaises(SystemExit):
            self.apply(FakeCF(echo_rule=False))

    def test_apply_accepts_cloudflare_decoded_space_in_pdf_source_url_and_rejects_other_diffs(self):
        # Reproduz a leitura de volta da Cloudflare (#151, #166): o PUT envia %20 no source_url e no target_url dos PDFs,
        # mas o GET devolve source_url com espaço literal e target_url ainda com %20; os outros itens conferem.
        pdf_srcs_sent = {f"guifav.dev{enc}" for enc in PDF_OLD_MAP.values()}
        pdf_srcs_cf = {f"guifav.dev{raw}" for raw in PDF_OLD_MAP}
        pdf_src_cf = f"guifav.dev{PDF_OLD_PATH}"
        pdf_target = f"https://guifav.dev{PDF_NEW_ENCODED}"

        cf = self.live(self.A)
        self.assertEqual(self.apply(cf), 0)
        self.assertEqual(cf.phase, [S.rule(self.B)])
        self.assertEqual(cf.items["LA"], self.OLD)
        self.assertEqual(len(cf.items["LB"]), TOTAL)
        self.assertEqual(len(cf.read_items), TOTAL)
        sent_pdf = [it["redirect"] for it in cf.items["LB"] if it["redirect"]["source_url"] in pdf_srcs_sent]
        read_pdf = [it["redirect"] for it in cf.read_items if it["redirect"]["source_url"] in pdf_srcs_cf]
        self.assertEqual(len(sent_pdf), 2)
        self.assertTrue(all(it["target_url"] == pdf_target for it in sent_pdf))
        self.assertEqual(len(read_pdf), 2)
        self.assertTrue(all(it["target_url"] == pdf_target for it in read_pdf))
        self.assertEqual([it for it in cf.read_items if it["redirect"]["source_url"] in pdf_srcs_sent], [])
        sent_other = {it["redirect"]["source_url"] for it in cf.items["LB"] if it["redirect"]["source_url"] not in pdf_srcs_sent}
        read_other = {it["redirect"]["source_url"] for it in cf.read_items if it["redirect"]["source_url"] not in pdf_srcs_cf}
        self.assertEqual(len(sent_other), TOTAL - 2)
        self.assertEqual(read_other, sent_other)

        # Também aceita se a API ecoar %20 sem decodificar.
        cf_echo = self.live(self.A)
        cf_echo.decode_source = False
        self.assertEqual(self.apply(cf_echo), 0)
        self.assertEqual(cf_echo.phase, [S.rule(self.B)])

        # Qualquer outra divergência na leitura de volta continua reprovando, mantendo a regra ativa intacta.
        def _drop_source(r):
            out = dict(r)
            del out["source_url"]
            return out

        cases = (
            ("target_url decodificado", pdf_src_cf, lambda r: {**r, "target_url": f"https://guifav.dev{PDF_NEW_PATH}"}),
            ("target_url diferente", pdf_src_cf, lambda r: {**r, "target_url": "https://guifav.dev/ferramentas/outro/"}),
            ("status_code diferente", pdf_src_cf, lambda r: {**r, "status_code": 302}),
            ("preserve_query_string diferente", pdf_src_cf, lambda r: {**r, "preserve_query_string": True}),
            ("include_subdomains diferente", pdf_src_cf, lambda r: {**r, "include_subdomains": True}),
            ("subpath_matching diferente", pdf_src_cf, lambda r: {**r, "subpath_matching": True}),
            ("parenteses codificados no source_url", pdf_src_cf,
             lambda r: {**r, "source_url": r["source_url"].replace("(", "%28").replace(")", "%29")}),
            ("duplo escape %2520 no source_url", pdf_src_cf,
             lambda r: {**r, "source_url": r["source_url"].replace(" ", "%2520")}),
            ("sinal + no lugar do espaco", pdf_src_cf,
             lambda r: {**r, "source_url": r["source_url"].replace(" ", "+")}),
            ("barra codificada %2F em outro item", "guifav.dev/llm-ranking",
             lambda r: {**r, "source_url": r["source_url"].replace("/", "%2F")}),
            ("espaco extra em outro item", "guifav.dev/llm-ranking",
             lambda r: {**r, "source_url": r["source_url"].replace("-", " ")}),
            ("source_url ausente", pdf_src_cf, _drop_source),
        )
        for label, match_src, mutator in cases:
            with self.subTest(case=label):
                hits = 0

                def apply_mut(r, _match=match_src, _fn=mutator):
                    nonlocal hits
                    if r.get("source_url") == _match:
                        hits += 1
                        return _fn(r)
                    return r

                cf_bad = self.live(self.A)
                cf_bad.readback_map = apply_mut
                with self.assertRaises(SystemExit) as e:
                    self.apply(cf_bad)
                self.assertEqual(hits, 1, label)
                self.assertIn(self.A, str(e.exception))
                self.assertEqual(cf_bad.phase, [{"id": "r", **S.rule(self.A)}])
                self.assertEqual(cf_bad.items["LA"], self.OLD)
                self.assertNotIn(("PUT", f"/accounts/acc/rulesets/phases/{S.PHASE}/entrypoint"), cf_bad.calls)


class CfErrorsTest(unittest.TestCase):
    """Falha da API ou da rede vira SystemExit com método e caminho, sem traceback nem token."""

    def test_http_error_without_json_and_connection_errors(self):
        import urllib.error

        def http_html(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 502, "Bad Gateway", {}, io.BytesIO(b"<html>502</html>"))

        def no_net(req, timeout=None):
            raise urllib.error.URLError("sem rede")

        def not_json(req, timeout=None):
            return _Resp(b"<html>ok</html>")

        for opener, word in ((http_html, "HTTP 502"), (no_net, "URLError"), (not_json, "JSONDecodeError")):
            with self.subTest(word=word):
                with self.assertRaises(SystemExit) as e:
                    S._cf("GET", "/accounts/acc/rules/lists", "segredo", opener=opener)
                msg = str(e.exception)
                self.assertIn("GET /accounts/acc/rules/lists", msg)
                self.assertIn(word, msg)
                self.assertNotIn("segredo", msg)


class CfClosesTest(unittest.TestCase):
    def test_http_error_body_is_closed_after_reading(self):
        import urllib.error
        body = io.BytesIO(b'{"success": false, "errors": [{"code": 1}]}')

        errs = []  # mantém o erro vivo: sem isso o coletor de lixo fecharia o corpo e o teste não provaria nada

        def opener(req, timeout=None):
            errs.append(urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, body))
            raise errs[-1]

        with self.assertRaises(SystemExit):
            S._cf("GET", "/accounts/acc/rules/lists", "tok", opener=opener)
        self.assertTrue(errs and body.closed)


class RemoveTest(unittest.TestCase):
    def run_remove(self, cf):
        with contextlib.redirect_stdout(io.StringIO()):
            return S.remove("tok", "acc", opener=cf)

    def test_own_rule_is_removed_and_the_list_is_kept(self):
        cf = FakeCF(phase=[S.rule(S.LISTS[0])], lists=[{"id": "L1", "name": S.LISTS[0]}])
        self.assertEqual(self.run_remove(cf), 0)
        self.assertEqual(cf.writes(), [("PUT", f"/accounts/acc/rulesets/phases/{S.PHASE}/entrypoint")])
        self.assertEqual(cf.phase, [])

    def test_nothing_to_remove_writes_nothing(self):
        for phase in (None, []):
            cf = FakeCF(phase=phase)
            self.assertEqual(self.run_remove(cf), 0)
            self.assertEqual(cf.writes(), [])

    def test_foreign_rule_is_refused(self):
        cf = FakeCF(phase=[{"description": "outra"}])
        with self.assertRaises(SystemExit):
            self.run_remove(cf)
        self.assertEqual(cf.writes(), [])


class CliTest(unittest.TestCase):
    def test_writes_need_yes_token_and_account(self):
        for cmd in ("apply", "remove"):
            with self.subTest(cmd=cmd):
                with mock.patch.object(S, cmd) as fn:
                    with self.assertRaises(SystemExit) as e:
                        S.main([cmd])
                    self.assertIn("--yes", str(e.exception))
                    for env in ({}, {"CLOUDFLARE_API_TOKEN": "t"}, {"CLOUDFLARE_ACCOUNT_ID": "a"}):
                        with mock.patch.dict(os.environ, env, clear=True), self.assertRaises(SystemExit) as e:
                            S.main([cmd, "--yes"])
                        self.assertIn("CLOUDFLARE_ACCOUNT_ID", str(e.exception))
                    fn.assert_not_called()
                    with mock.patch.dict(os.environ, {"CLOUDFLARE_API_TOKEN": "t", "CLOUDFLARE_ACCOUNT_ID": "a"},
                                         clear=True):
                        S.main([cmd, "--yes"])
                    fn.assert_called_once_with("t", "a")

    def test_rules_prints_the_payload(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(S.main(["rules"]), 0)
        data = json.loads(out.getvalue())
        self.assertEqual((data["lists"], len(data["items"])), (list(S.LISTS), TOTAL))
        by_src = {it["redirect"]["source_url"]: it["redirect"] for it in data["items"]}
        for src in ("guifav.dev/blog/privacidade/", "guifav.dev/blog/privacidade"):
            self.assertEqual(by_src[src]["target_url"], "https://guifav.dev/privacidade/")
            self.assertEqual(by_src[src]["status_code"], 301)
            self.assertNotIn("#", by_src[src]["target_url"])
        for src in ("guifav.dev/tools/", "guifav.dev/tools"):
            self.assertEqual(by_src[src]["target_url"], "https://guifav.dev/ferramentas/")
            self.assertEqual(by_src[src]["status_code"], 301)
        for enc_old in PDF_OLD_MAP.values():
            pdf_src = f"guifav.dev{enc_old}"
            self.assertEqual(by_src[pdf_src]["target_url"], f"https://guifav.dev{PDF_NEW_ENCODED}")
            self.assertEqual(by_src[pdf_src]["status_code"], 301)


class VerifyTest(unittest.TestCase):
    def live(self, break_url=None, answer=None):
        def fetch(url, method="GET"):
            if url == break_url:
                return answer
            p = url.split("://", 1)[1]
            host, path = p.split("/", 1)
            if host in (APEX, WWW):
                return 301, C.simulate(host, "/" + path)
            t = S.simulate(url)
            return (301, t) if t else (200, None)
        return fetch

    def run_verify(self, fetch):
        out = io.StringIO()
        return S.verify(fetch, pause=0, out=out), out.getvalue()

    def test_all_good_passes_and_counts_every_check(self):
        rc, out = self.run_verify(self.live())
        self.assertEqual(rc, 0, out)
        self.assertIn(f"ok: {17 * 4 + 17 + 2 + 8 + 1 + TOTAL}; falhas: 0", out)

    def test_each_hop_is_checked(self):
        old, new = S.OLD_POSTS[0]
        mid = S.first_hop(old)
        final = f"https://guifav.dev/blog/{new}/"
        for url, answer in ((f"https://{WWW}/post/{old}", (301, "https://guifav.dev/blog/x/")),
                            (f"https://{APEX}/post/{old}/", (404, None)),
                            (mid, (404, None)), (mid.rstrip("/"), (302, final)),
                            (final, (404, None)),
                            ("https://guifav.dev/llm-ranking", (404, None)),
                            ("https://guifav.dev/tools/", (404, None)),
                            ("https://guifav.dev/tools", (404, None)),
                            ("https://guifav.dev/ferramentas/", (404, None)),
                            ("https://guifav.dev/tools/llm-ranking/", (404, None)),
                            ("https://guifav.dev/ferramentas/llm-ranking/", (404, None)),
                            (f"https://guifav.dev{PDF_OLD_ENCODED}", (404, None)),
                            (f"https://guifav.dev{PDF_TOOLS_ENCODED}", (404, None)),
                            (f"https://guifav.dev{PDF_NEW_ENCODED}", (404, None)),
                            ("https://guifav.dev/blog/privacidade/", (404, None)),
                            ("https://guifav.dev/blog/privacidade", (302, "https://guifav.dev/privacidade/")),
                            ("https://guifav.dev/privacidade/", (404, None))):
            with self.subTest(url=url):
                rc, out = self.run_verify(self.live(url, answer))
                self.assertEqual(rc, 1)
                self.assertIn("FALHA " + url, out)


if __name__ == "__main__":
    unittest.main()

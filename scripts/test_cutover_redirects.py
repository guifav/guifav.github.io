"""Testes dos redirects do cutover (#24). Rodar: python3 scripts/test_cutover_redirects.py"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import unittest
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cutover_redirects as R  # noqa: E402

# Destinos do plano da #24, escritos à parte (não usam as constantes do módulo).
T_PRIVACY = "https://guifav.dev/blog/privacidade/"
T_HOME = "https://guifav.dev/"
T_RSS = "https://guifav.dev/blog/rss.xml"
T_BLOG = "https://guifav.dev/blog/"
LEGAL = {"/p/politica-de-privacidade", "/p/termos-e-condicoes-de-uso"}
APEX, WWW = "guilhermefavaron.com.br", "www.guilhermefavaron.com.br"


def oracle(path: str) -> str:
    """O plano da #24 escrito à parte, sobre o caminho já normalizado pela zona."""
    low = path.lower()
    base = re.sub(r"/comments/?$", "", low).rstrip("/")
    if base in LEGAL or low in ("/privacy", "/privacy/", "/tos", "/tos/") or low.startswith("/action/"):
        return T_PRIVACY
    if low in ("/", "/about", "/about/"):
        return T_HOME
    if low in ("/feed", "/feed/"):
        return T_RSS
    m = (re.fullmatch(r"/p/([^/]+)/comments(?:/.*)?", path, re.I)
         or re.fullmatch(r"/(?:p|post)/(.+?)/?", path, re.I))
    if m:  # inclui caminho aninhado (/p/a/b): destino /blog/a/b/, sem página; o Substack não gera esse caminho
        return f"https://guifav.dev/blog/{m.group(1)}/"
    return T_BLOG


def corpus() -> list[str]:
    paths = ["/", "/about", "/about/", "/ABOUT", "/feed", "/feed/", "/archive", "/podcast", "/leaderboard",
             "/recommendations", "/chat", "/privacy", "/tos", "/privacy/", "/TOS", "/action/disable_email",
             "/action/disable_email/x", "/Action/x", "/p", "/p/", "/P/", "/post", "/post/", "/POST/", "/subscribe",
             "/x/y", "/pagina-inicial", "/pagina-inicial/hashtags/FF0000", "/livro-ia-guia-estrategico-empresarios-era-digital",
             "/p/a/b", "/post/a/b/"]
    for s in ("politica-de-privacidade", "termos-e-condicoes-de-uso", "Politica-De-Privacidade"):
        paths += [f"/p/{s}", f"/p/{s}/", f"/p/{s}/comments", f"/p/{s}/comments/", f"/P/{s}/Comments"]
    for s in R.slugs():
        paths += [f"/p/{s}", f"/p/{s}/", f"/p/{s}/comments", f"/p/{s}/comments/", f"/post/{s}", f"/post/{s}/"]
    return paths


class NormalizeTest(unittest.TestCase):
    def test_examples_from_the_cloudflare_docs(self):
        # https://developers.cloudflare.com/rules/normalization/examples/ (tipo Cloudflare, entrada normalizada)
        self.assertEqual(R.normalize("/%68ello//pa\\th"), "/hello/pa/th")
        self.assertEqual(R.normalize("/hello//../path"), "/path")
        self.assertEqual(R.normalize("/hello/\\../path"), "/path")
        self.assertEqual(R.normalize("/hello"), "/hello")

    def test_rfc3986_rules(self):
        self.assertEqual(R.normalize("/p/%61%2d%5F%7e%2E"), "/p/a-_~.")  # não reservados decodificados
        self.assertEqual(R.normalize("/p/a%2fb%3a"), "/p/a%2Fb%3A")  # reservados ficam, em caixa alta
        self.assertEqual(R.normalize("/p/você"), "/p/voc%C3%AA")  # bytes fora do conjunto são codificados
        self.assertEqual(R.normalize("/p/./a/../b"), "/p/b")
        self.assertEqual(R.normalize("//"), "/")
        # pontos codificados viram segmentos de ponto antes da remoção (decodificação vem primeiro)
        self.assertEqual(R.normalize("/p/%2e/abc"), "/p/abc")
        self.assertEqual(R.normalize("/p/x/%2E%2e/abc"), "/p/abc")
        self.assertEqual(R.normalize("/p/x/.%2e/abc"), "/p/abc")
        self.assertEqual(R.normalize("/p/%2e%2e%2e/abc"), "/p/.../abc")
        # decodificar antes de remover: com a ordem trocada, o ".." literal consumiria o "%2e%2e" e daria /a/b/c
        self.assertEqual(R.normalize("/a/b/%2e%2e/../c"), "/c")

    def test_simulation_normalizes_before_matching(self):
        self.assertEqual(R.simulate(WWW, "/p//"), T_BLOG)
        self.assertEqual(R.simulate(WWW, "/%70/abc"), "https://guifav.dev/blog/abc/")
        self.assertEqual(R.simulate(WWW, "/p/politica%2Dde-privacidade"), T_PRIVACY)
        self.assertEqual(R.simulate(WWW, "/p//abc//"), "https://guifav.dev/blog/abc/")


class RulesTest(unittest.TestCase):
    def test_corpus_is_real(self):
        self.assertEqual(len(R.slugs()), 233)
        self.assertNotIn("politica-de-privacidade", R.slugs())

    def test_simulation_matches_the_plan_for_every_post_and_page(self):
        bad = [(h, p, R.simulate(h, p), oracle(R.normalize(p))) for p in corpus() for h in R.HOSTS
               if R.simulate(h, p) != oracle(R.normalize(p))]
        self.assertEqual(bad[:5], [])

    def test_every_post_lands_on_its_own_page(self):
        for s in R.slugs():
            for p in (f"/p/{s}", f"/p/{s}/", f"/p/{s}/comments", f"/p/{s}/comments/", f"/post/{s}", f"/post/{s}/"):
                self.assertEqual(R.simulate(WWW, p), f"https://guifav.dev/blog/{s}/", p)

    def test_legal_pages_and_their_comments_go_to_privacy(self):
        for s in ("politica-de-privacidade", "termos-e-condicoes-de-uso"):
            for v in ("", "/", "/comments", "/comments/", "/COMMENTS"):
                self.assertEqual(R.simulate(APEX, f"/p/{s}{v}"), T_PRIVACY, v)

    def test_no_target_has_a_double_slash_or_query(self):
        for p in corpus() + ["/p//", "/post//", "/P//x", "/p//comments", "/p/a//comments/"]:
            t = R.simulate(APEX, p)
            self.assertNotIn("//", t.split("://", 1)[1], p)
            self.assertNotIn("?", t, p)

    def test_empty_slug_in_any_case_goes_to_the_blog(self):
        for p in ("/p/", "/P/", "/post/", "/POST/", "/Post/"):
            self.assertEqual(R.simulate(WWW, p), T_BLOG, p)

    def test_other_hosts_of_the_zone_are_untouched(self):
        for h in ("techie.guilhermefavaron.com.br", "newsletter.guilhermefavaron.com.br", "guifav.dev", "x.com"):
            self.assertIsNone(R.simulate(h, "/p/qualquer"))

    def test_host_is_case_insensitive_in_both_simulation_and_payload(self):
        self.assertEqual(R.simulate("WWW.GuilhermeFavaron.com.br", "/feed"), T_RSS)
        for r in R.cloudflare_rules(True):
            self.assertTrue(r["expression"].startswith("(lower(http.host) in {"), r["description"])

    def test_wildcard_is_case_insensitive_and_keeps_the_slug_case(self):
        self.assertEqual(R.simulate(WWW, "/P/Abc"), "https://guifav.dev/blog/Abc/")

    def test_wildcard_is_lazy_and_full_match(self):
        w = R._wild("/p/*/comments*")
        self.assertEqual(w.match("/p/a/comments/b/comments").group(1), "a")
        self.assertIsNone(w.match("/x/p/a/comments"))


# As expressões exatas que vão para a Cloudflare (revisadas uma a uma; qualquer mudança aparece aqui).
H = '(lower(http.host) in {"guilhermefavaron.com.br" "www.guilhermefavaron.com.br"})'
LEGAL_SET = ('"/p/politica-de-privacidade" "/p/politica-de-privacidade/" "/p/politica-de-privacidade/comments" '
             '"/p/politica-de-privacidade/comments/" "/p/termos-e-condicoes-de-uso" "/p/termos-e-condicoes-de-uso/" '
             '"/p/termos-e-condicoes-de-uso/comments" "/p/termos-e-condicoes-de-uso/comments/" "/privacy" "/privacy/" '
             '"/tos" "/tos/"')
GOLDEN = [
    (H + ' and (lower(http.request.uri.path) in {' + LEGAL_SET + '} or starts_with(lower(http.request.uri.path), "/action/"))',
     {"value": T_PRIVACY}),
    (H + ' and (lower(http.request.uri.path) in {"/" "/about" "/about/"})', {"value": T_HOME}),
    (H + ' and (http.request.uri.path wildcard r"/p/*/comments*")',
     {"expression": 'wildcard_replace(http.request.uri.path, r"/p/*/comments*", r"https://guifav.dev/blog/${1}/")'}),
    (H + ' and (http.request.uri.path wildcard r"/p/*/")',
     {"expression": 'wildcard_replace(http.request.uri.path, r"/p/*/", r"https://guifav.dev/blog/${1}/")'}),
    (H + ' and (http.request.uri.path wildcard r"/p/*") and not (lower(http.request.uri.path) in {"/p/"})',
     {"expression": 'wildcard_replace(http.request.uri.path, r"/p/*", r"https://guifav.dev/blog/${1}/")'}),
    (H + ' and (http.request.uri.path wildcard r"/post/*/")',
     {"expression": 'wildcard_replace(http.request.uri.path, r"/post/*/", r"https://guifav.dev/blog/${1}/")'}),
    (H + ' and (http.request.uri.path wildcard r"/post/*") and not (lower(http.request.uri.path) in {"/post/"})',
     {"expression": 'wildcard_replace(http.request.uri.path, r"/post/*", r"https://guifav.dev/blog/${1}/")'}),
    (H + ' and (lower(http.request.uri.path) in {"/feed" "/feed/"})', {"value": T_RSS}),
    (H, {"value": T_BLOG}),
]


class PayloadTest(unittest.TestCase):
    def test_expressions_and_targets_are_exactly_the_reviewed_ones(self):
        got = [(r["expression"], r["action_parameters"]["from_value"]["target_url"]) for r in R.cloudflare_rules(True)]
        self.assertEqual(got, GOLDEN)

    def test_shape_and_limits(self):
        rules = R.cloudflare_rules(False)
        self.assertLessEqual(len(rules), 10)
        for r in rules:
            self.assertEqual(set(r), {"description", "expression", "action", "action_parameters", "enabled"})
            fv = r["action_parameters"]["from_value"]
            self.assertEqual((r["action"], fv["status_code"], fv["preserve_query_string"], r["enabled"]),
                             ("redirect", 301, False, False))
            self.assertTrue(r["description"].startswith("cutover #24: "))
        self.assertTrue(all(r["enabled"] for r in R.cloudflare_rules(True)))

    def test_rehearsal_hosts_replace_only_the_host_filter(self):
        h = ("cutover-ensaio.guilhermefavaron.com.br",)
        for a, b in zip(R.cloudflare_rules(True, h), R.cloudflare_rules(True)):
            self.assertEqual(a["expression"].replace('{"cutover-ensaio.guilhermefavaron.com.br"}',
                                                     '{"guilhermefavaron.com.br" "www.guilhermefavaron.com.br"}'),
                             b["expression"])
        self.assertEqual(R.simulate(h[0], "/p/a", h), "https://guifav.dev/blog/a/")
        self.assertIsNone(R.simulate(WWW, "/p/a", h))


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return None


class ApplyTest(unittest.TestCase):
    def opener(self, current=None, missing=False, back=None, zones=None):
        calls = []

        def op(req, timeout=None):
            body = json.loads(req.data) if req.data else None
            calls.append((req.get_method(), req.full_url, body, req.headers.get("Authorization")))
            if req.full_url.endswith("?name=guilhermefavaron.com.br"):
                res = {"success": True, "result": zones if zones is not None
                       else [{"id": "Z1", "name": "guilhermefavaron.com.br"}]}
            elif req.get_method() == "GET":
                res = ({"success": False, "errors": [{"code": 10003, "message": "not found"}]} if missing
                       else {"success": True, "result": {"rules": current or []}})
            else:
                rules = [dict(r, id=f"r{i}", version="1", ref=f"r{i}") for i, r in enumerate(body["rules"])]
                res = {"success": True, "result": {"id": "RS", "rules": back(rules) if back else rules}}
            return _Resp(json.dumps(res).encode())
        return op, calls

    def test_reads_then_puts_all_rules_disabled(self):
        op, calls = self.opener(missing=True)
        self.assertEqual(R.apply("tok", False, opener=op), 0)
        self.assertEqual([c[0] for c in calls], ["GET", "GET", "PUT"])
        self.assertTrue(calls[1][1].endswith("/zones/Z1/rulesets/phases/http_request_dynamic_redirect/entrypoint"))
        self.assertEqual(calls[2][1], calls[1][1])
        self.assertEqual(calls[2][2], {"rules": R.cloudflare_rules(False)})
        self.assertFalse(any(r["enabled"] for r in calls[2][2]["rules"]))
        self.assertEqual(calls[2][3], "Bearer tok")

    def test_own_rules_already_there_are_replaced(self):
        op, calls = self.opener(current=[{"description": "cutover #24: x", "id": "a"}])
        self.assertEqual(R.apply("tok", True, opener=op), 0)
        self.assertTrue(all(r["enabled"] for r in calls[2][2]["rules"]))

    def test_refuses_when_the_phase_has_foreign_rules(self):
        for foreign in ({"description": "redirect do techie", "id": "a"}, {"id": "sem-descricao"}):
            with self.subTest(foreign=foreign):
                op, calls = self.opener(current=[{"description": "cutover #24: x"}, foreign])
                with self.assertRaises(SystemExit) as e:
                    R.apply("tok", False, opener=op)
                self.assertIn("não são do cutover", str(e.exception))
                self.assertNotIn("PUT", [c[0] for c in calls])

    def test_wrong_zone_answer_is_refused(self):
        for zones in ([], [{"id": "Z1", "name": "outra.com.br"}], [{"id": "a", "name": "guilhermefavaron.com.br"}] * 2):
            with self.subTest(zones=zones):
                op, calls = self.opener(zones=zones)
                with self.assertRaises(SystemExit):
                    R.apply("tok", False, opener=op)
                self.assertNotIn("PUT", [c[0] for c in calls])

    def test_any_field_differing_in_the_answer_is_an_error(self):
        def change(field, value):
            def back(rules):
                rules[2] = dict(rules[2], **{field: value})
                return rules
            return back
        def nested(**kw):
            def back(rules):
                fv = dict(rules[1]["action_parameters"]["from_value"], **kw)
                rules[1] = dict(rules[1], action_parameters={"from_value": fv})
                return rules
            return back
        cases = [change("expression", "(true)"), change("enabled", True), change("action", "block"),
                 change("description", "outra"),
                 nested(status_code=302), nested(preserve_query_string=True), nested(target_url={"value": T_BLOG}),
                 nested(extra=1),
                 lambda rules: rules[:-1], lambda rules: rules + rules[:1], lambda rules: list(reversed(rules))]
        for back in cases:
            with self.subTest(back=back):
                op, _ = self.opener(back=back)
                with self.assertRaises(SystemExit):
                    R.apply("tok", False, opener=op)


class CliTest(unittest.TestCase):
    """main() inteiro, com apply e verify como stubs e sem token no ambiente: nada chega à rede."""

    def setUp(self):
        self.old = os.environ.pop("CLOUDFLARE_API_TOKEN", None)
        self.orig = (R.apply, R.verify)
        self.calls = []
        R.apply = lambda token, enabled, hosts=R.HOSTS, **k: self.calls.append(("apply", token, enabled, hosts)) or 0
        R.verify = lambda todo, **k: self.calls.append(("verify", todo, k.get("hosts"))) or 0

    def tearDown(self):
        R.apply, R.verify = self.orig
        if self.old is not None:
            os.environ["CLOUDFLARE_API_TOKEN"] = self.old
        else:
            os.environ.pop("CLOUDFLARE_API_TOKEN", None)

    def test_apply_needs_yes_and_token_and_is_disabled_by_default(self):
        with self.assertRaises(SystemExit) as e:
            R.main(["apply"])
        self.assertIn("--yes", str(e.exception))
        with self.assertRaises(SystemExit) as e:
            R.main(["apply", "--yes"])
        self.assertIn("CLOUDFLARE_API_TOKEN", str(e.exception))
        self.assertEqual(self.calls, [])
        os.environ["CLOUDFLARE_API_TOKEN"] = "tok"
        R.main(["apply", "--yes"])
        R.main(["apply", "--yes", "--enable"])
        self.assertEqual(self.calls, [("apply", "tok", False, R.HOSTS), ("apply", "tok", True, R.HOSTS)])

    def test_hosts_outside_the_zone_are_refused(self):
        os.environ["CLOUDFLARE_API_TOKEN"] = "tok"
        for bad in ("guifav.dev", "x.guilhermefavaron.com.br.evil.com", ",", "evilguilhermefavaron.com.br"):
            for cmd in (["verify", "--hosts", bad], ["apply", "--yes", "--hosts", bad]):
                with self.subTest(cmd=cmd):
                    with self.assertRaises(SystemExit) as e:
                        R.main(cmd)
                    self.assertIn("--hosts", str(e.exception))
        self.assertEqual(self.calls, [])
        R.main(["verify", "--quick", "--hosts", "Cutover-Ensaio.guilhermefavaron.com.br"])
        self.assertEqual(self.calls[0][2], ("cutover-ensaio.guilhermefavaron.com.br",))
        self.assertTrue(all(urlsplit(j.url).hostname == "cutover-ensaio.guilhermefavaron.com.br" for j in self.calls[0][1]))

    def test_verify_reads_urls_and_allow_list(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            u, a = Path(d, "u.txt"), Path(d, "a.txt")
            u.write_text("https://www.guilhermefavaron.com.br/p/erro-x\nhttps://www.guilhermefavaron.com.br/p/certo\n\n")
            a.write_text("https://www.guilhermefavaron.com.br/p/erro-x\n")
            R.main(["verify", "--quick", "--urls", str(u), "--allow-404", str(a)])
        todo = self.calls[0][1]
        extra = [j for j in todo if j.url.endswith(("/p/erro-x", "/p/certo"))]
        self.assertEqual(len(extra), 4)  # cada URL nos dois hosts
        self.assertEqual({(urlsplit(j.url).path, j.allow_404) for j in extra}, {("/p/erro-x", True), ("/p/certo", False)})


class JobsTest(unittest.TestCase):
    # Especificação independente das páginas especiais exigidas (com e sem barra), escrita à parte de R.SPECIAL.
    SPECIAL = ["/", "/about", "/about/", "/feed", "/feed/", "/archive", "/archive/", "/podcast", "/podcast/",
               "/leaderboard", "/leaderboard/", "/privacy", "/privacy/", "/tos", "/tos/", "/action/disable_email",
               "/action/disable_email/"] + [f"/p/{s}{v}" for s in ("politica-de-privacidade", "termos-e-condicoes-de-uso")
                                            for v in ("", "/", "/comments", "/comments/")]

    def test_every_required_special_page_is_checked_on_both_hosts(self):
        urls = {(j.url, j.method) for j in R.jobs(quick=True)}
        for p in self.SPECIAL:
            for h in (APEX, WWW):
                self.assertIn((f"https://{h}{p}", "GET"), urls, p)

    def test_query_http_and_head_cover_every_form_on_both_hosts(self):
        s = R.slugs()[0]
        forms = [f"/p/{s}", f"/p/{s}/", f"/p/{s}/comments", f"/p/{s}/comments/", f"/post/{s}", f"/post/{s}/",
                 "/", "/feed", "/privacy", "/archive", "/action/x"]
        urls = {(j.url, j.method) for j in R.jobs()}
        for h in (APEX, WWW):
            for p in forms:
                self.assertIn((f"https://{h}{p}?utm_source=x&ref=y", "GET"), urls, p)
                self.assertIn((f"http://{h}{p}", "GET"), urls, p)
                self.assertIn((f"https://{h}{p}", "HEAD"), urls, p)

    def test_full_matrix_covers_the_acceptance_criteria(self):
        todo = R.jobs()
        urls = {(j.url, j.method) for j in todo}
        for s in R.slugs():
            for v in ("/p/{s}", "/p/{s}/", "/p/{s}/comments", "/p/{s}/comments/", "/post/{s}", "/post/{s}/"):
                for h in (APEX, WWW):
                    self.assertIn((f"https://{h}{v.format(s=s)}", "GET"), urls)
        for p in ("/", "/about", "/about/", "/feed", "/feed/", "/privacy", "/tos/", "/p/termos-e-condicoes-de-uso/comments/"):
            for h in (APEX, WWW):
                self.assertIn((f"https://{h}{p}", "GET"), urls)
        for h in (APEX, WWW):
            forms = [u for (u, m) in urls if u.startswith(f"https://{h}/") and "?utm_source=x&ref=y" in u]
            self.assertGreaterEqual(len(forms), 11)
            self.assertTrue(any(u.startswith(f"http://{h}/p/") for (u, m) in urls))
            self.assertTrue(any(m == "HEAD" and u.startswith(f"https://{h}/post/") for (u, m) in urls))
            self.assertTrue(any(u.startswith(f"https://{h.upper()}/") for (u, m) in urls))
        self.assertEqual(sum(1 for j in todo if j.method == "GET" and j.url.startswith("https://") and "?" not in j.url
                             and urlsplit(j.url).netloc in (APEX, WWW)), (233 * 6 + len(self.SPECIAL)) * 2)
        self.assertFalse(any(j.allow_404 for j in todo))

    def test_quick_is_a_sample_but_keeps_every_form(self):
        q = R.jobs(quick=True)
        self.assertLess(len(q), len(R.jobs()) / 5)
        self.assertTrue(any(j.method == "HEAD" for j in q) and any(j.url.startswith("http://") for j in q))

    def test_extra_urls_go_to_both_hosts_encoded(self):
        todo = R.jobs(quick=True, extra=["https://techie.guilhermefavaron.com.br/p/você"], allow_404={"/p/voc%C3%AA"})
        extra = [j for j in todo if "voc" in j.url]
        self.assertEqual({j.url for j in extra}, {f"https://{h}/p/voc%C3%AA" for h in (APEX, WWW)})
        self.assertTrue(all(j.allow_404 for j in extra))


class VerifyTest(unittest.TestCase):
    def fetcher(self, override=None, dest=None, seen=None):
        override, dest = override or {}, dest or {}

        def f(url, method="GET"):
            if seen is not None:
                seen.append((url, method))
            if (url, method) in override:
                return override[(url, method)]
            if url in override:
                return override[url]
            if url.startswith("https://guifav.dev"):
                return dest.get(url, (200, None))
            s = urlsplit(url)
            return 301, R.simulate(s.hostname, s.path)
        return f

    def run_verify(self, fetcher, todo=None):
        out = io.StringIO()
        todo = todo if todo is not None else [R.Job(f"https://{h}{p}") for p in ("/", "/p/a", "/feed") for h in (APEX, WWW)]
        rc = R.verify(todo, fetcher=fetcher, pause=0, out=out)
        return rc, out.getvalue()

    def test_all_good_passes(self):
        rc, out = self.run_verify(self.fetcher())
        self.assertEqual(rc, 0, out)
        self.assertIn("falhas: 0", out)

    def test_wrong_status_location_or_hops_fail(self):
        u = f"https://{WWW}/p/a"
        for bad in ((302, "https://guifav.dev/blog/a/"), (308, "https://guifav.dev/blog/a/"), (301, T_BLOG),
                    (200, None), (0, None), (301, "https://guifav.dev/blog/a"), (301, None)):
            with self.subTest(bad=bad):
                rc, out = self.run_verify(self.fetcher({u: bad}))
                self.assertEqual(rc, 1, out)
                self.assertIn(u, out)

    def test_head_and_http_are_sent_as_such(self):
        seen = []
        todo = [R.Job(f"https://{WWW}/p/a", "HEAD"), R.Job(f"http://{APEX}/p/a")]
        rc, _ = self.run_verify(self.fetcher(seen=seen), todo)
        self.assertEqual(rc, 0)
        self.assertIn((f"https://{WWW}/p/a", "HEAD"), seen)
        self.assertIn((f"http://{APEX}/p/a", "GET"), seen)
        rc, out = self.run_verify(self.fetcher({(f"https://{WWW}/p/a", "HEAD"): (405, None)}), todo)
        self.assertEqual(rc, 1, out)

    def test_destination_must_be_200_without_another_redirect(self):
        for d in ((301, "https://guifav.dev/blog/a/x"), (404, None), (500, None), (0, None)):
            with self.subTest(d=d):
                rc, out = self.run_verify(self.fetcher(dest={"https://guifav.dev/blog/a/": d}))
                self.assertEqual(rc, 1, out)

    def test_404_is_tolerated_only_when_listed(self):
        u = f"https://{WWW}/p/erro-de-digitacao"
        dest = {"https://guifav.dev/blog/erro-de-digitacao/": (404, None)}
        rc, out = self.run_verify(self.fetcher(dest=dest), [R.Job(u)])
        self.assertEqual(rc, 1, out)
        rc, out = self.run_verify(self.fetcher(dest=dest), [R.Job(u, allow_404=True)])
        self.assertEqual(rc, 0, out)
        self.assertIn("SEM PÁGINA " + u, out)
        rc, out = self.run_verify(self.fetcher(dest={"https://guifav.dev/blog/erro-de-digitacao/": (500, None)}),
                                  [R.Job(u, allow_404=True)])
        self.assertEqual(rc, 1, out)

    def test_url_outside_the_cutover_hosts_fails(self):
        rc, out = self.run_verify(self.fetcher(), [R.Job("https://techie.guilhermefavaron.com.br/p/a")])
        self.assertEqual(rc, 1, out)

    def test_expected_location_uses_the_normalized_path(self):
        rc, out = self.run_verify(self.fetcher({f"https://{WWW}/p//abc": (301, "https://guifav.dev/blog/abc/")}),
                                  [R.Job(f"https://{WWW}/p//abc")])
        self.assertEqual(rc, 0, out)

    def test_to_uri_keeps_existing_escapes_and_query(self):
        self.assertEqual(R.to_uri("https://x.com.br/a%20b/é?q=á&r=1#frag"), "https://x.com.br/a%20b/%C3%A9?q=%C3%A1&r=1")

    def test_fetch_never_raises_on_bad_urls(self):
        self.assertEqual(R.fetch("http://[::1"), (0, None))


if __name__ == "__main__":
    unittest.main()

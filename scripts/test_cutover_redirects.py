"""Testes dos redirects do cutover (#24). Rodar: python3 scripts/test_cutover_redirects.py"""
from __future__ import annotations

import io
import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cutover_redirects as R  # noqa: E402

PRIV = {"/p/politica-de-privacidade", "/p/termos-e-condicoes-de-uso", "/privacy", "/tos"}


def oracle(path: str) -> str:
    """O plano da #24 escrito à parte, sem as regras: o que cada caminho antigo deve virar."""
    if path.rstrip("/") in PRIV or path.startswith("/action/"):
        return R.PRIVACY
    if path in ("/", "/about", "/about/"):
        return R.HOME
    if path in ("/feed", "/feed/"):
        return R.RSS
    m = re.fullmatch(r"/p/([^/]+)/comments/?", path) or re.fullmatch(r"/(?:p|post)/([^/]+)/?", path)
    if m:
        return f"https://guifav.dev/blog/{m.group(1)}/"
    return R.BLOG


def corpus() -> list[str]:
    paths = ["/", "/about", "/about/", "/feed", "/feed/", "/archive", "/podcast", "/leaderboard", "/recommendations",
             "/chat", "/privacy", "/tos", "/privacy/", "/p/politica-de-privacidade", "/p/termos-e-condicoes-de-uso/",
             "/action/disable_email", "/action/disable_email/x", "/p", "/p/", "/post", "/post/", "/subscribe",
             "/x/y", "/pagina-inicial", "/pagina-inicial/hashtags/FF0000", "/livro-ia-guia-estrategico-empresarios-era-digital"]
    for s in R.slugs():
        paths += [f"/p/{s}", f"/p/{s}/", f"/p/{s}/comments", f"/p/{s}/comments/", f"/post/{s}", f"/post/{s}/"]
    return paths


class RulesTest(unittest.TestCase):
    def test_corpus_is_real(self):
        self.assertEqual(len(R.slugs()), 233)

    def test_simulation_matches_the_plan_for_every_post_and_page(self):
        bad = [(h, p, R.simulate(h, p), oracle(p)) for p in corpus() for h in R.HOSTS if R.simulate(h, p) != oracle(p)]
        self.assertEqual(bad[:5], [])

    def test_every_post_lands_on_its_own_page(self):
        for s in R.slugs():
            for p in (f"/p/{s}", f"/p/{s}/", f"/p/{s}/comments", f"/post/{s}"):
                self.assertEqual(R.simulate("www.guilhermefavaron.com.br", p), f"https://guifav.dev/blog/{s}/", p)

    def test_no_target_has_a_double_slash_or_query(self):
        for p in corpus():
            t = R.simulate(R.HOSTS[0], p)
            self.assertNotIn("//", t.split("://", 1)[1], p)
            self.assertNotIn("?", t, p)

    def test_other_hosts_of_the_zone_are_untouched(self):
        for h in ("techie.guilhermefavaron.com.br", "newsletter.guilhermefavaron.com.br", "guifav.dev", "x.com"):
            self.assertIsNone(R.simulate(h, "/p/qualquer"))
        for r in R.cloudflare_rules(True):
            self.assertTrue(r["expression"].startswith(
                '(http.host in {"guilhermefavaron.com.br" "www.guilhermefavaron.com.br"})'), r["description"])

    def test_host_is_case_insensitive_like_dns(self):
        self.assertEqual(R.simulate("WWW.GuilhermeFavaron.com.br", "/feed"), R.RSS)

    def test_wildcard_is_case_insensitive_and_keeps_the_slug_case(self):
        # Como a Cloudflare: o curinga não diferencia caixa, e ${1} leva o trecho como veio.
        self.assertEqual(R.simulate(R.HOSTS[1], "/P/Abc"), "https://guifav.dev/blog/Abc/")

    def test_wildcard_is_lazy_and_full_match(self):
        w = R._wild("/p/*/comments*")
        self.assertEqual(w.match("/p/a/comments/b/comments").group(1), "a")
        self.assertIsNone(w.match("/x/p/a/comments"))


class PayloadTest(unittest.TestCase):
    def test_shape_and_limits(self):
        rules = R.cloudflare_rules(False)
        self.assertLessEqual(len(rules), R.MAX_RULES)
        self.assertEqual(len(rules), len(R.RULES))
        for r in rules:
            fv = r["action_parameters"]["from_value"]
            self.assertEqual((r["action"], fv["status_code"], fv["preserve_query_string"], r["enabled"]),
                             ("redirect", 301, False, False))
            self.assertEqual(len(fv["target_url"]), 1)
            self.assertTrue(r["description"].startswith("cutover #24: "))
        self.assertTrue(all(r["enabled"] for r in R.cloudflare_rules(True)))

    def test_wildcard_target_uses_the_same_pattern_as_the_match(self):
        for rule, r in zip(R.RULES, R.cloudflare_rules(True)):
            tu = r["action_parameters"]["from_value"]["target_url"]
            if rule.wildcard:
                self.assertIn(f'http.request.uri.path wildcard r"{rule.wildcard}"', r["expression"])
                self.assertEqual(tu["expression"],
                                 f'wildcard_replace(http.request.uri.path, r"{rule.wildcard}", r"{rule.target}")')
            else:
                self.assertEqual(tu, {"value": rule.target})
                self.assertNotIn("${", rule.target)

    def test_expression_lists_every_path_prefix_and_exclusion(self):
        for rule, r in zip(R.RULES, R.cloudflare_rules(True)):
            for p in rule.paths:
                self.assertIn(f'"{p}"', r["expression"])
            for p in rule.prefixes:
                self.assertIn(f'starts_with(http.request.uri.path, "{p}")', r["expression"])
            if rule.exclude:
                self.assertIn("and not (http.request.uri.path in {", r["expression"])
                for p in rule.exclude:
                    self.assertIn(f'"{p}"', r["expression"].split("and not", 1)[1])

    def test_catch_all_is_last_and_only_one(self):
        catch = [i for i, r in enumerate(R.RULES) if not (r.paths or r.prefixes or r.wildcard)]
        self.assertEqual(catch, [len(R.RULES) - 1])
        self.assertEqual(R.cloudflare_rules(True)[-1]["expression"],
                         '(http.host in {"guilhermefavaron.com.br" "www.guilhermefavaron.com.br"})')


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class RehearsalHostsTest(unittest.TestCase):
    H = ("cutover-ensaio.guilhermefavaron.com.br",)

    def test_rules_filter_only_the_given_hosts(self):
        for r in R.cloudflare_rules(True, self.H):
            self.assertTrue(r["expression"].startswith('(http.host in {"cutover-ensaio.guilhermefavaron.com.br"})'))
            self.assertNotIn('"www.guilhermefavaron.com.br"', r["expression"])
        self.assertEqual(R.simulate(self.H[0], "/p/a", self.H), "https://guifav.dev/blog/a/")
        self.assertIsNone(R.simulate("www.guilhermefavaron.com.br", "/p/a", self.H))

    def test_main_rejects_hosts_outside_the_zone(self):
        # Hermético: verify e apply viram stubs; se a recusa falhar, o teste falha na hora, sem rede.
        calls = []
        orig = (R.verify, R.apply)
        R.verify = lambda *a, **k: calls.append("verify") or 0
        R.apply = lambda *a, **k: calls.append("apply") or 0
        try:
            for bad in ("guifav.dev", "x.guilhermefavaron.com.br.evil.com", ",", "evilguilhermefavaron.com.br"):
                for cmd in (["verify", "--hosts", bad], ["apply", "--yes", "--hosts", bad]):
                    with self.subTest(cmd=cmd):
                        with self.assertRaises(SystemExit) as e:
                            R.main(cmd)
                        self.assertIn("--hosts", str(e.exception))
            self.assertEqual(R.main(["verify", "--hosts", "cutover-ensaio.guilhermefavaron.com.br"]), 0)
        finally:
            R.verify, R.apply = orig
        self.assertEqual(calls, ["verify"])

    def test_verify_uses_the_given_hosts(self):
        seen = []

        def f(u):
            seen.append(u)
            from urllib.parse import urlsplit
            s = urlsplit(u)
            return (200, None) if s.hostname == "guifav.dev" else (301, R.simulate(s.hostname, s.path, self.H))
        rc = R.verify(["/p/a"], [], fetcher=f, pause=0, out=io.StringIO(), hosts=self.H)
        self.assertEqual(rc, 0)
        self.assertTrue(all("www.guilhermefavaron" not in u for u in seen))
        self.assertIn("https://cutover-ensaio.guilhermefavaron.com.br/p/a", seen)


class ApplyTest(unittest.TestCase):
    def opener(self, rules_back=None):
        calls = []

        def op(req, timeout=None):
            body = json.loads(req.data) if req.data else None
            calls.append((req.get_method(), req.full_url, body, req.headers.get("Authorization")))
            if req.get_method() == "GET":
                return _Resp(json.dumps({"success": True, "result": [{"id": "Z1"}]}).encode())
            got = body["rules"] if rules_back is None else rules_back
            return _Resp(json.dumps({"success": True, "result": {"id": "RS", "rules": got}}).encode())
        return op, calls

    def test_puts_all_rules_disabled_by_default(self):
        op, calls = self.opener()
        self.assertEqual(R.apply("tok", False, opener=op), 0)
        self.assertEqual([c[0] for c in calls], ["GET", "PUT"])
        self.assertTrue(calls[0][1].endswith("/zones?name=guilhermefavaron.com.br"))
        self.assertTrue(calls[1][1].endswith("/zones/Z1/rulesets/phases/http_request_dynamic_redirect/entrypoint"))
        self.assertEqual(calls[1][2], {"rules": R.cloudflare_rules(False)})
        op, calls = self.opener()
        R.apply("tok", True, ("cutover-ensaio.guilhermefavaron.com.br",), opener=op)
        self.assertEqual(calls[1][2], {"rules": R.cloudflare_rules(True, ("cutover-ensaio.guilhermefavaron.com.br",))})
        self.assertEqual(calls[1][3], "Bearer tok")

    def test_enable_turns_every_rule_on(self):
        op, calls = self.opener()
        R.apply("tok", True, opener=op)
        self.assertTrue(all(r["enabled"] for r in calls[1][2]["rules"]))

    def test_mismatching_ruleset_back_is_an_error(self):
        op, _ = self.opener(rules_back=[{"enabled": False}])
        with self.assertRaises(SystemExit):
            R.apply("tok", False, opener=op)
        op, _ = self.opener(rules_back=[{"enabled": True}] * len(R.RULES))
        with self.assertRaises(SystemExit):
            R.apply("tok", False, opener=op)

    def test_main_refuses_without_yes_or_token(self):
        # Hermético: sem token no ambiente e apply como stub; nada aqui chega à API real.
        import os
        old = os.environ.pop("CLOUDFLARE_API_TOKEN", None)
        orig = R.apply
        R.apply = lambda *a, **k: self.fail("apply chamado")
        try:
            with self.assertRaises(SystemExit) as e:
                R.main(["apply"])
            self.assertIn("--yes", str(e.exception))
            with self.assertRaises(SystemExit) as e:
                R.main(["apply", "--yes"])
            self.assertIn("CLOUDFLARE_API_TOKEN", str(e.exception))
        finally:
            R.apply = orig
            if old is not None:
                os.environ["CLOUDFLARE_API_TOKEN"] = old


class VerifyTest(unittest.TestCase):
    PATHS = ["/", "/p/a", "/feed"]

    def fetcher(self, override=None, dest=None):
        override, dest = override or {}, dest or {}

        def f(url):
            if url in override:
                return override[url]
            if url.startswith("https://guifav.dev"):
                return dest.get(url, (200, None))
            from urllib.parse import urlsplit
            s = urlsplit(url)
            return 301, R.simulate(s.hostname, s.path)
        return f

    def run_verify(self, fetcher, extra=()):
        out = io.StringIO()
        rc = R.verify(self.PATHS, list(extra), fetcher=fetcher, pause=0, out=out)
        return rc, out.getvalue()

    def test_all_good_passes(self):
        rc, out = self.run_verify(self.fetcher())
        self.assertEqual(rc, 0, out)
        self.assertIn("falhas: 0", out)

    def test_wrong_status_location_or_hops_fail(self):
        u = "https://www.guilhermefavaron.com.br/p/a"
        for bad in ((302, "https://guifav.dev/blog/a/"), (301, "https://guifav.dev/blog/"), (200, None), (0, None),
                    (301, "https://guifav.dev/blog/a")):
            with self.subTest(bad=bad):
                rc, out = self.run_verify(self.fetcher({u: bad}))
                self.assertEqual(rc, 1, out)
                self.assertIn("FALHA " + u, out)

    def test_destination_must_be_200_without_another_redirect(self):
        for d in ((301, "https://guifav.dev/blog/a/x"), (404, None), (500, None)):
            with self.subTest(d=d):
                rc, out = self.run_verify(self.fetcher(dest={"https://guifav.dev/blog/a/": d}))
                self.assertEqual(rc, 1, out)

    def test_extra_url_without_page_is_reported_not_failed(self):
        x = "https://www.guilhermefavaron.com.br/p/erro-de-digitacao"
        rc, out = self.run_verify(self.fetcher(dest={"https://guifav.dev/blog/erro-de-digitacao/": (404, None)}), [x])
        self.assertEqual(rc, 0, out)
        self.assertIn("SEM PÁGINA " + x, out)
        rc, out = self.run_verify(self.fetcher(dest={"https://guifav.dev/blog/erro-de-digitacao/": (500, None)}), [x])
        self.assertEqual(rc, 1, out)

    def test_extra_url_with_accents_is_sent_encoded(self):
        # Achado do ensaio: uma URL do Search Console com "ê" derrubava o verify (UnicodeEncodeError).
        seen = []
        f = self.fetcher()
        x = "https://www.guilhermefavaron.com.br/p/você-já-viu"
        rc = R.verify([], [x], fetcher=lambda u: (seen.append(u), f(u))[1], pause=0, out=io.StringIO())
        self.assertEqual(rc, 0)
        enc = "https://www.guilhermefavaron.com.br/p/voc%C3%AA-j%C3%A1-viu"
        self.assertIn(enc, seen)
        self.assertIn("https://guifav.dev/blog/voc%C3%AA-j%C3%A1-viu/", seen)
        self.assertTrue(all(u.isascii() for u in seen))

    def test_to_uri_keeps_existing_escapes_and_query(self):
        self.assertEqual(R.to_uri("https://x.com.br/a%20b/é?q=á&r=1#frag"), "https://x.com.br/a%20b/%C3%A9?q=%C3%A1&r=1")

    def test_fetch_never_raises_on_bad_urls(self):
        self.assertEqual(R.fetch("http://[::1"), (0, None))

    def test_extra_url_outside_the_cutover_hosts_fails(self):
        rc, out = self.run_verify(self.fetcher(), ["https://techie.guilhermefavaron.com.br/p/a"])
        self.assertEqual(rc, 1, out)

    def test_both_hosts_and_query_string_are_checked(self):
        seen = []
        f = self.fetcher()
        rc = R.verify(self.PATHS, [], fetcher=lambda u: (seen.append(u), f(u))[1], pause=0, out=io.StringIO())
        self.assertEqual(rc, 0)
        for h in R.HOSTS:
            self.assertIn(f"https://{h}/p/a", seen)
        self.assertTrue(any("?utm_source=x" in u for u in seen))


if __name__ == "__main__":
    unittest.main()

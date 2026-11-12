"""Testes da importação de assinantes para o Resend (issue #21, parte C).

Rodar em blog-src/: python3 scripts/test_import_subscribers.py
"""
import contextlib
import csv
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import import_subscribers as S  # noqa: E402

SEG = "seg-1"
TOPIC = "topic-1"
HEAD = ["email", "active_subscription", "expiry", "plan", "email_disabled", "created_at", "first_payment_at"]


def export(rows) -> str:
    f = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8", newline="")
    w = csv.writer(f)
    w.writerow(HEAD)
    for r in rows:
        w.writerow(r)
    f.close()
    return f.name


def row(email, created="2025-01-02T03:04:05", disabled="false", expiry="", plan="other", active="false"):
    return [email, active, expiry, plan, disabled, created, ""]


class FakeResend:
    """Resend em memória, com o contrato documentado: GET de contato devolve cada propriedade como
    {"value": ..., "type": "string"}; o email no caminho chega codificado; listas paginam por limit/after."""

    def __init__(self, contacts=None, props=(), extra_props=0, limit_429=0, retry_after="0", on_create=None,
                 fail=None, rate_path=None, hook=None, conflict_on_existing=False, topics=None):
        self.contacts = {}  # email -> dict
        for c in contacts or []:
            self.contacts[c["email"]] = {"id": "c-" + c["email"], "unsubscribed": False, "segments": set(),
                                         "properties": {}, "topic": "opt_in", **c}
        # Contrato real (sondado em 2026-11-12): POST de email existente é upsert (201, mesmo id). Com
        # conflict_on_existing, o fake responde 409, para exercitar o caminho de reserva do importador.
        self.conflict_on_existing = conflict_on_existing
        self.topics = topics  # topics(contact) -> (status, corpo) substitui a resposta de GET .../topics
        self.props = [f"outra_{i:03d}" for i in range(extra_props)] + list(props)
        self.calls = []
        self.limit_429 = limit_429
        self.retry_after = retry_after
        self.on_create = on_create  # corrida: chamado antes de criar (simula inscrição pelo site)
        self.fail = fail  # (method, prefixo do caminho, status, mensagem)
        self.rate_path = rate_path  # prefixo do caminho que responde sempre 429
        self.hook = hook  # hook(fake, method, path, data): simula o Worker agindo no meio da execução

    def _page(self, items, q):
        if "after" in q:
            items = [x for x in items if x["id"] > q["after"]]
        n = min(int(q.get("limit", 20)), 100)
        return {"object": "list", "has_more": len(items) > n, "data": items[:n]}

    def __call__(self, method, url, headers, body):
        raw = url[len(S.RESEND):]
        path = urllib.parse.unquote(raw.split("?", 1)[0])
        q = dict(urllib.parse.parse_qsl(raw.split("?", 1)[1])) if "?" in raw else {}
        data = json.loads(body) if body else None
        self.calls.append((method, raw, data))
        if self.hook:
            self.hook(self, method, path, data)
        if self.limit_429:
            self.limit_429 -= 1
            return 429, {"message": "rate"}, {"Retry-After": self.retry_after}
        if self.rate_path and path.startswith(self.rate_path):
            return 429, {"message": "rate"}, {"Retry-After": "0"}
        if self.fail and method == self.fail[0] and path.startswith(self.fail[1]):
            return self.fail[2], {"name": "validation_error", "message": self.fail[3]}, {}
        by_id = {c["id"]: c for c in self.contacts.values()}
        if method == "GET" and path == "/contact-properties":
            return 200, self._page([{"id": f"p-{k}", "key": k} for k in sorted(self.props)], q), {}
        if method == "POST" and path == "/contact-properties":
            self.props.append(data["key"])
            return 201, {"id": "p-" + data["key"]}, {}
        if method == "GET" and path == "/contacts":
            items = sorted(self.contacts.values(), key=lambda c: c["id"])
            if "segment_id" in q:
                items = [c for c in items if q["segment_id"] in c["segments"]]
            items = [{"id": c["id"], "email": c["email"], "unsubscribed": c["unsubscribed"]} for c in items]
            return 200, self._page(items, q), {}
        if method == "GET" and path.startswith("/contacts/") and path.endswith("/topics"):
            c = by_id[path.split("/")[2]]
            if self.topics:
                st, body = self.topics(c)
                return st, body, {}
            # Formato real: o tópico aparece mesmo sem estado explícito, com o padrão opt_in.
            return 200, {"object": "list", "has_more": False, "data": [
                {"id": TOPIC, "name": "IA Aplicada", "description": "x", "subscription": c["topic"],
                 "created_at": "2026-11-12 12:05:07+00"}]}, {}
        if method == "PATCH" and path.startswith("/contacts/") and path.endswith("/topics"):
            if not isinstance(data, list):  # contrato real: array puro
                return 422, {"name": "validation_error", "message": "x"}, {}
            c = by_id[path.split("/")[2]]
            for t in data:
                if t["id"] == TOPIC:
                    c["topic"] = t["subscription"]
            return 200, {"object": "contact_topics", "id": c["id"]}, {}
        if method == "GET" and path.startswith("/contacts/"):
            key = path[len("/contacts/"):]
            c = self.contacts.get(key) or by_id.get(key)
            if not c:
                return 404, {"message": "not found"}, {}
            return 200, {"object": "contact", "id": c["id"], "email": c["email"], "unsubscribed": c["unsubscribed"],
                         "properties": {k: {"value": v, "type": "string"} for k, v in c["properties"].items()}}, {}
        if method == "POST" and path == "/contacts":
            if self.on_create:
                self.on_create(self, data["email"])
            c = self.contacts.get(data["email"])
            if c and self.conflict_on_existing:
                return 409, {"message": "exists"}, {}
            if c:  # upsert real: status global volta a inscrito (mesmo sem o campo); tópico só muda com `topics`
                c["unsubscribed"] = bool(data.get("unsubscribed", False))
                c["segments"] |= {s["id"] for s in data.get("segments", [])}
                c["properties"].update(data.get("properties", {}))
                for t in data.get("topics", []):
                    if t["id"] == TOPIC:
                        c["topic"] = t["subscription"]
                return 201, {"object": "contact", "id": c["id"]}, {}
            self.contacts[data["email"]] = {"id": "c-" + data["email"], "email": data["email"],
                                            "unsubscribed": bool(data.get("unsubscribed", False)),
                                            "segments": {s["id"] for s in data.get("segments", [])},
                                            "properties": dict(data.get("properties", {})), "topic": "opt_in"}
            return 201, {"id": "c-" + data["email"]}, {}
        if method == "PATCH" and path.startswith("/contacts/"):
            c = by_id.get(path[len("/contacts/"):])
            if "unsubscribed" in data:
                c["unsubscribed"] = data["unsubscribed"]
            c["properties"].update(data.get("properties", {}))
            return 200, {"id": c["id"]}, {}
        if method == "POST" and path.startswith("/contacts/") and "/segments/" in path:
            _, _, cid, _, seg = path.split("/")
            by_id[cid]["segments"].add(seg)
            return 201, {"id": seg}, {}
        return 500, {"message": "rota inesperada " + method + " " + path}, {}

    def writes(self):
        return [(m, p) for m, p, _ in self.calls if m != "GET"]


def run(fake, path, sleeps=None, **kw):
    out = io.StringIO()
    api = S.Api("re_test", fake, sleep=(sleeps.append if sleeps is not None else (lambda s: None)))
    counts = S.run(api, path, SEG, TOPIC, out=out, **kw)
    return counts, out.getvalue()


def cli(fake, path, *flags):
    out, err = io.StringIO(), io.StringIO()
    env = {"RESEND_API_KEY": "re_test", "NEWSLETTER_SEGMENT_ID": SEG, "NEWSLETTER_TOPIC_ID": TOPIC}
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = S.main([x for x in (path, *flags) if x], transport=fake, sleep=lambda s: None)
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return rc, out.getvalue() + err.getvalue()


def out(c):
    """Fora da newsletter: saiu do tópico dela ou da conta inteira."""
    return c["unsubscribed"] or c["topic"] == "opt_out"


def substack(email, **kw):
    return {"email": email, "segments": {SEG}, "properties": {"source": "substack"}, **kw}


class ExportTest(unittest.TestCase):
    def test_reads_only_enabled_valid_unique_emails(self):
        p = export([row("A@x.com", created="2024-05-01T00:00:00"), row("a@x.com", created="2023-01-01T00:00:00"),
                    row("b@x.com", disabled="true"), row("sem-arroba"), row(" c@y.org ")])
        subs, skipped = S.load_export(p)
        self.assertEqual([s.email for s in subs], ["a@x.com", "c@y.org"])
        self.assertEqual(subs[0].joined_at, "2023-01-01T00:00:00")  # a entrada mais antiga vale
        self.assertEqual(skipped, {"desabilitado": 1, "invalido": 1, "repetido": 1})

    def test_only_explicit_false_counts_as_enabled(self):
        subs, skipped = S.load_export(export([row("a@x.com"), row("b@x.com", disabled=""), row("c@x.com", disabled="talvez"),
                                              row("d@x.com", disabled="FALSE")]))
        self.assertEqual(sorted(s.email for s in subs), ["a@x.com", "d@x.com"])
        self.assertEqual(skipped, {"desabilitado": 2})

    def test_rejects_malformed_addresses(self):
        bad = ["a@x..com", "a@x.com>", "x" * 65 + "@x.com", ".a@x.com", "a.@x.com", "a..b@x.com", "a@x",
               "a@-x.com", "a b@x.com", "a'b@x.com", "a@" + ".".join(["x" * 63] * 4) + ".com"]
        good = ["a.b+tag@sub.x.com.br", "a_b-c@x.io", "x" * 64 + "@x.com"]
        subs, skipped = S.load_export(export([row(e) for e in bad + good]))
        self.assertEqual(sorted(s.email for s in subs), sorted(good))
        self.assertEqual(skipped, {"invalido": len(bad)})

    def test_length_boundaries(self):
        def addr(total):  # parte local de 64 e rótulos de 63, completando até o tamanho pedido
            local, dom = "a" * 64, []
            rest = total - 65 - 4  # "@" e ".com"
            while rest > 0:
                n = min(63, rest - (1 if dom else 0))
                if dom:
                    rest -= 1
                dom.append("b" * n)
                rest -= n
            return f"{local}@{'.'.join(dom)}.com"
        ok, long = addr(254), addr(255)
        self.assertEqual((len(ok), len(long)), (254, 255))
        label63, label64 = "a@" + "c" * 63 + ".com", "a@" + "c" * 64 + ".com"
        subs, skipped = S.load_export(export([row(ok), row(long), row(label63), row(label64)]))
        self.assertEqual(sorted(x.email for x in subs), sorted([ok, label63]))
        self.assertEqual(skipped, {"invalido": 2})

    def test_duplicate_keeps_the_whole_oldest_record_in_any_order(self):
        old = row("d@x.com", created="2023-01-01T00:00:00", expiry="2026-01-01T00:00:00.000Z", active="true")
        new = row("d@x.com", created="2024-01-01T00:00:00", expiry="2027-01-01T00:00:00.000Z", active="true")
        for rows in ([old, new], [new, old]):
            (s,), _ = S.load_export(export(rows))
            self.assertEqual((s.joined_at, s.paid_until), ("2023-01-01T00:00:00", "2026-01-01T00:00:00.000Z"))

    def test_older_free_record_beats_newer_paid_record_in_any_order(self):
        old = row("d@x.com", created="2023-01-01T00:00:00")
        new = row("d@x.com", created="2024-01-01T00:00:00", expiry="2027-01-01T00:00:00.000Z", active="true")
        for rows in ([old, new], [new, old]):
            (s,), _ = S.load_export(export(rows))
            self.assertEqual((s.joined_at, s.paid_until), ("2023-01-01T00:00:00", ""))

    def test_paid_until_only_for_active_subscription(self):
        p = export([row("p@x.com", expiry="2026-12-16T22:53:57.000Z", plan="yearly", active="true"),
                    row("q@x.com", expiry="2020-01-01T00:00:00.000Z", active="false")])
        subs, _ = S.load_export(p)
        self.assertEqual({s.email: s.paid_until for s in subs}, {"p@x.com": "2026-12-16T22:53:57.000Z", "q@x.com": ""})

    def test_export_without_expected_columns_fails(self):
        f = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False)
        f.write("mail,created\nx@y.com,2020\n")
        f.close()
        with self.assertRaises(S.ImportError_):
            S.load_export(f.name)


class ImportTest(unittest.TestCase):
    def test_dry_run_makes_no_writes(self):
        for sync in (False, True):
            f = FakeResend(contacts=[substack("saiu@x.com"), {"email": "a@x.com"}])
            counts, out = run(f, export([row("a@x.com"), row("b@x.com")]), sync=sync)
            self.assertEqual(f.writes(), [], sync)
            self.assertEqual((counts["criar"], counts["adicionar_ao_segmento"], counts["completar_propriedades"]), (1, 1, 1))
            self.assertIn("simulação", out)

    def test_creates_contacts_in_segment_with_properties(self):
        f = FakeResend()
        p = export([row("a@x.com", created="2024-02-18T15:38:10"),
                    row("p@x.com", created="2023-03-01T00:00:00", expiry="2026-12-16T22:53:57.000Z", active="true")])
        counts, _ = run(f, p, apply=True)
        self.assertEqual(f.contacts["a@x.com"]["properties"], {"source": "substack", "substack_joined_at": "2024-02-18T15:38:10",
                                                               "substack_paid_until": ""})
        self.assertEqual(f.contacts["p@x.com"]["properties"]["substack_paid_until"], "2026-12-16T22:53:57.000Z")
        for c in f.contacts.values():
            self.assertEqual(c["segments"], {SEG})
            self.assertFalse(c["unsubscribed"])
        prop_bodies = [d for m, p_, d in f.calls if m == "POST" and p_ == "/contact-properties"]
        # Lista literal: site_confirmed_at precisa existir antes do deploy do Worker que a grava.
        self.assertEqual(prop_bodies, [{"key": k, "type": "string"} for k in
                                       ("source", "substack_joined_at", "substack_paid_until", "site_confirmed_at",
                                        "substack_sync_unsubscribed_at")])
        self.assertEqual(counts["criado"], 2)

    def test_property_listing_is_paginated(self):
        f = FakeResend(props=S.PROPERTIES, extra_props=150)
        run(f, export([row("a@x.com")]), apply=True)
        self.assertNotIn(("POST", "/contact-properties"), f.writes())

    def test_existing_contact_is_not_overwritten_nor_resubscribed(self):
        f = FakeResend(contacts=[{"email": "a@x.com", "unsubscribed": True, "properties": {"source": "site"}}],
                       props=S.PROPERTIES)
        counts, _ = run(f, export([row("a@x.com")]), apply=True)
        c = f.contacts["a@x.com"]
        self.assertTrue(c["unsubscribed"])
        self.assertEqual(c["properties"], {"source": "site"})
        self.assertEqual(c["segments"], {SEG})
        self.assertEqual(counts["existente_descadastrado"], 1)
        self.assertEqual([m for m, p in f.writes() if m == "PATCH"], [])

    def test_unsubscribed_contact_without_source_gets_properties_and_stays_unsubscribed(self):
        f = FakeResend(contacts=[{"email": "a@x.com", "unsubscribed": True}], props=S.PROPERTIES)
        run(f, export([row("a@x.com", created="2025-01-01T00:00:00")]), apply=True)
        c = f.contacts["a@x.com"]
        self.assertEqual(c["properties"]["source"], "substack")
        self.assertTrue(c["unsubscribed"])
        self.assertEqual([d for m, _, d in f.calls if m == "PATCH"], [{"properties": S._props(S.Subscriber(
            "a@x.com", "2025-01-01T00:00:00", ""))}])

    def test_contact_in_segment_without_source_came_from_site_and_is_untouched(self):
        # Só o Worker põe gente no segmento antes da importação; sem origem = opt-in pelo Worker antigo.
        f = FakeResend(contacts=[{"email": "a@x.com", "segments": {SEG}}, substack("saiu@x.com")], props=S.PROPERTIES)
        counts, _ = run(f, export([row("a@x.com")]), apply=True)
        self.assertEqual(f.contacts["a@x.com"]["properties"], {})
        self.assertEqual((counts["existente_do_site_intocado"], counts["adicionado_ao_segmento"]), (1, 0))
        counts, _ = run(f, export([row("fica@x.com")]), apply=True, sync=True)
        self.assertFalse(out(f.contacts["a@x.com"]))
        self.assertEqual((f.contacts["saiu@x.com"]["topic"], f.contacts["saiu@x.com"]["unsubscribed"]), ("opt_out", False))

    def test_documented_empty_source_counts_as_no_source(self):
        f = FakeResend(contacts=[{"email": "a@x.com", "properties": {"source": ""}}], props=S.PROPERTIES)
        run(f, export([row("a@x.com")]), apply=True)
        self.assertEqual(f.contacts["a@x.com"]["properties"]["source"], "substack")

    def test_fill_rereads_and_skips_a_contact_whose_source_was_written_meanwhile(self):
        def write_source(fake, method, path, data):
            if method == "GET" and path == "/contact-properties":
                fake.contacts["a@x.com"]["properties"]["source"] = "site"
        f = FakeResend(contacts=[{"email": "a@x.com"}], props=S.PROPERTIES, hook=write_source)
        counts, _ = run(f, export([row("a@x.com")]), apply=True)
        self.assertEqual(f.contacts["a@x.com"]["properties"], {"source": "site"})
        self.assertEqual(counts["propriedades_completadas"], 0)

    def test_fill_rereads_and_skips_a_contact_confirmed_meanwhile(self):
        def confirm(fake, method, path, data):
            if method == "GET" and path == "/contact-properties":  # depois do planejamento, antes das escritas
                c = fake.contacts["a@x.com"]
                c["properties"]["site_confirmed_at"] = "2026-11-12T00:00:00Z"
        f = FakeResend(contacts=[{"email": "a@x.com"}], props=S.PROPERTIES, hook=confirm)
        counts, _ = run(f, export([row("a@x.com")]), apply=True)
        self.assertNotIn("source", f.contacts["a@x.com"]["properties"])
        self.assertEqual(counts["propriedades_completadas"], 0)

    def test_site_confirmed_contact_does_not_get_substack_source(self):
        f = FakeResend(contacts=[{"email": "a@x.com", "properties": {"site_confirmed_at": "2026-11-11T00:00:00Z"}}],
                       props=S.PROPERTIES)
        run(f, export([row("a@x.com")]), apply=True)
        self.assertEqual(f.contacts["a@x.com"]["properties"], {"site_confirmed_at": "2026-11-11T00:00:00Z"})
        self.assertEqual(f.contacts["a@x.com"]["segments"], {SEG})

    def test_second_run_is_a_no_op(self):
        f = FakeResend()
        p = export([row("a@x.com"), row("b@x.com")])
        run(f, p, apply=True)
        before = len(f.writes())
        counts, _ = run(f, p, apply=True)
        self.assertEqual(len(f.writes()), before)
        self.assertEqual(counts["no_segmento"], 2)

    def test_contact_listing_is_paginated(self):
        many = [{"email": f"s{i:03d}@x.com", "segments": {SEG}, "properties": {"source": "substack"}} for i in range(250)]
        f = FakeResend(contacts=many, props=S.PROPERTIES)
        counts, _ = run(f, export([row(c["email"]) for c in many]), apply=True)
        self.assertEqual((counts["criar"], counts["no_segmento"]), (0, 250))
        self.assertEqual(f.writes(), [])

    def test_race_with_site_signup_keeps_the_contact_and_encodes_the_email(self):
        for email in ("a@x.com", "a/b@x.com", "a?b@x.com", "a#b@x.com"):
            def signup(fake, e, email=email):
                if e == email and e not in fake.contacts:
                    fake.contacts[e] = {"id": "c-site", "email": e, "unsubscribed": True, "segments": set(),
                                        "properties": {"site_confirmed_at": "2026-11-11T00:00:00Z"}}
            f = FakeResend(props=S.PROPERTIES, on_create=signup, conflict_on_existing=True)  # caminho de reserva
            counts, _ = run(f, export([row(email)]), apply=True)
            c = f.contacts[email]
            self.assertEqual((c["id"], c["segments"], c["unsubscribed"]), ("c-site", {SEG}, True), email)
            self.assertEqual(c["properties"], {"site_confirmed_at": "2026-11-11T00:00:00Z"}, email)
            self.assertEqual(counts["corrida_409"], 1, email)
            got = [p for m, p, _ in f.calls if m == "GET" and p.startswith("/contacts/")]
            self.assertEqual(got, ["/contacts/" + urllib.parse.quote(email, safe="@")], email)

    def test_race_409_with_a_contact_without_origin_gets_substack_properties(self):
        def other(fake, e):
            fake.contacts.setdefault(e, {"id": "c-x", "email": e, "unsubscribed": False, "segments": set(), "properties": {}})
        f = FakeResend(props=S.PROPERTIES, on_create=other, conflict_on_existing=True)
        run(f, export([row("a@x.com")]), apply=True)
        self.assertEqual(f.contacts["a@x.com"]["properties"]["source"], "substack")
        self.assertEqual(f.contacts["a@x.com"]["segments"], {SEG})

    def test_f2_upsert_race_keeps_site_confirmation_and_topic(self):
        # API real: o POST de quem se inscreveu pelo site entre a listagem e a criação é upsert (201), não 409.
        for topic in ("opt_in", "opt_out"):
            def signup(fake, e, topic=topic):
                fake.contacts.setdefault(e, {"id": "c-site", "email": e, "unsubscribed": False, "segments": {SEG},
                                             "properties": {"site_confirmed_at": "2026-11-11T00:00:00Z"}, "topic": topic})
            f = FakeResend(props=S.PROPERTIES, on_create=signup)
            run(f, export([row("a@x.com")]), apply=True)
            c = f.contacts["a@x.com"]
            self.assertEqual((c["id"], c["topic"], c["properties"]["site_confirmed_at"]), ("c-site", topic, "2026-11-11T00:00:00Z"))
            self.assertTrue(S._protected({"properties": {k: {"value": v} for k, v in c["properties"].items()}}))
            self.assertEqual([d for m, _, d in f.calls if m == "PATCH"], [], topic)  # nenhum script escreve no tópico

    def test_topic_is_required_before_any_call(self):
        f = FakeResend(contacts=[substack("saiu@x.com")], props=S.PROPERTIES)
        with self.assertRaisesRegex(S.ImportError_, "tópico"):
            S.run(S.Api("re_test", f, sleep=lambda s: None), export([row("outro@x.com")]), SEG, "", apply=True, sync=True)
        self.assertEqual(f.calls, [])
        rc, text = cli(f, export([row("outro@x.com")]), "--sync")
        self.assertEqual(rc, 0)
        os.environ.pop("NEWSLETTER_TOPIC_ID", None)
        env = {"RESEND_API_KEY": "re_test", "NEWSLETTER_SEGMENT_ID": SEG}
        old = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                rc = S.main([export([row("outro@x.com")]), "--sync"], transport=f, sleep=lambda s: None)
        finally:
            for k, v in old.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        self.assertEqual((rc, "NEWSLETTER_TOPIC_ID" in err.getvalue()), (2, True))

    def test_existing_out_of_topic_counts_as_unsubscribed(self):
        f = FakeResend(contacts=[substack("a@x.com", topic="opt_out"), substack("b@x.com", unsubscribed=True),
                                 substack("c@x.com")], props=S.PROPERTIES)
        counts, _ = run(f, export([row("a@x.com"), row("b@x.com"), row("c@x.com")]))
        self.assertEqual(counts["existente_descadastrado"], 2)
        self.assertEqual(f.writes(), [])

    def test_lists_ask_for_pages_of_100(self):
        f = FakeResend(contacts=[substack("b@x.com")])  # existente: lê também os tópicos dele
        run(f, export([row("a@x.com"), row("b@x.com")]), apply=True)
        self.assertTrue(any("/topics?" in p for m, p, _ in f.calls if m == "GET"))
        lists = [p for m, p, _ in f.calls if m == "GET" and "?" in p]
        self.assertTrue(lists)
        self.assertTrue(all("limit=100" in p for p in lists), lists)

    def test_retry_after_is_capped_at_60_seconds(self):
        sleeps = []
        f = FakeResend(limit_429=1, retry_after="120")
        run(f, export([row("a@x.com")]), sleeps=sleeps, apply=True)
        self.assertEqual([x for x in sleeps if x != S.INTERVAL], [60.0])

    def test_requests_are_spaced_and_rate_limit_waits_retry_after(self):
        sleeps = []
        f = FakeResend(limit_429=2, retry_after="3")
        counts, _ = run(f, export([row("a@x.com")]), sleeps=sleeps, apply=True)
        self.assertEqual(counts["criado"], 1)
        self.assertEqual(S.INTERVAL, 0.15)  # valor documentado: abaixo de 10 req/s com folga
        self.assertEqual(sleeps.count(S.INTERVAL), len(f.calls))
        self.assertEqual([s for s in sleeps if s != S.INTERVAL], [3.0, 3.0])

    def test_output_never_contains_emails_even_on_errors(self):
        cases = [
            (FakeResend(), ()),
            (FakeResend(), ("--apply",)),
            (FakeResend(fail=("POST", "/contacts", 422, "email segredo@x.com inválido")), ("--apply",)),
            (FakeResend(on_create=lambda f, e: f.contacts.setdefault(e, {"id": "c-1", "email": e, "unsubscribed": False,
                                                                         "segments": set(), "properties": {}}),
                        fail=("GET", "/contacts/segredo", 500, "erro com segredo@x.com"), conflict_on_existing=True), ("--apply",)),
        ]
        for fake, flags in cases:
            rc, text = cli(fake, export([row("segredo@x.com")]), *flags)
            self.assertIn("export_validos: 1" if rc == 0 else "ERRO", text)  # a saída foi mesmo capturada
            self.assertNotIn("segredo", text, (flags, text))
        self.assertEqual(rc, 1)

    def test_persistent_rate_limit_error_has_no_email(self):
        race = lambda f, e: f.contacts.setdefault(e, {"id": "c-1", "email": e, "unsubscribed": False,  # noqa: E731
                                                      "segments": set(), "properties": {}})
        for fake in (FakeResend(limit_429=10_000), FakeResend(on_create=race, rate_path="/contacts/segredo", conflict_on_existing=True)):
            rc, text = cli(fake, export([row("segredo@x.com")]), "--apply")
            self.assertEqual(rc, 1)
            self.assertIn("limite", text)
            self.assertNotIn("segredo", text)


class SyncTest(unittest.TestCase):
    def test_sync_unsubscribes_only_substack_contacts_missing_from_export(self):
        f = FakeResend(contacts=[
            substack("fica@x.com"),
            substack("saiu@x.com"),
            {"email": "site@x.com", "segments": {SEG}, "properties": {}},
            substack("confirmou@x.com", properties={"source": "substack", "site_confirmed_at": "2026-11-12T00:00:00Z"}),
            {"email": "fora@x.com", "properties": {"source": "substack"}},  # fora do segmento
        ], props=S.PROPERTIES)
        counts, _ = run(f, export([row("fica@x.com")]), apply=True, sync=True)
        state = {e: c["topic"] for e, c in f.contacts.items()}
        self.assertEqual(state, {"fica@x.com": "opt_in", "saiu@x.com": "opt_out", "site@x.com": "opt_in",
                                 "confirmou@x.com": "opt_in", "fora@x.com": "opt_in"})
        # Só a newsletter: o status global da conta continua inscrito para todos (os outros envios seguem).
        self.assertEqual({e for e, c in f.contacts.items() if c["unsubscribed"]}, set())
        self.assertEqual(counts["sincronizado_descadastrado"], 1)
        self.assertEqual(counts["sync_conflito_confirmacao"], 0)  # confirmado pelo site e inscrito não é conflito
        self.assertNotIn("DELETE", [m for m, _ in f.writes()])

    def test_sync_refuses_a_bad_export_before_any_write(self):
        many = [substack(f"s{i:02d}@x.com") for i in range(40)]
        for rows in ([row("s00@x.com")], [], [row("invalido")]):
            f = FakeResend(contacts=list(many))
            with self.assertRaises(S.ImportError_):
                run(f, export(rows), apply=True, sync=True)
            self.assertEqual(f.writes(), [], rows)

    def test_sync_refuses_an_empty_export_even_for_a_small_segment(self):
        f = FakeResend(contacts=[substack(f"s{i}@x.com") for i in range(5)], props=S.PROPERTIES)
        with self.assertRaisesRegex(S.ImportError_, "export sem nenhum"):
            run(f, export([row("desabilitado@x.com", disabled="true")]), apply=True, sync=True)
        self.assertEqual(f.writes(), [])

    def test_f1_race_ends_unsubscribed_with_both_stamps_and_the_next_run_flags_it(self):
        def confirm(fake, method, path, data):
            # O Worker confirma entre a releitura e as escritas do sync (o POST dele: reativa, tópico e marcador).
            if method == "PATCH" and path == "/contacts/c-saiu@x.com":
                c = fake.contacts["saiu@x.com"]
                c["unsubscribed"], c["topic"] = False, "opt_in"
                c["properties"]["site_confirmed_at"] = "2026-11-12T00:00:00Z"
        f = FakeResend(contacts=[substack("saiu@x.com")], props=S.PROPERTIES, hook=confirm)
        p = export([row("outro@x.com")])
        counts, _ = run(f, p, apply=True, sync=True)
        c = f.contacts["saiu@x.com"]
        self.assertEqual(c["topic"], "opt_out")  # desfecho aceito F1: fica do lado seguro (sem a newsletter)
        self.assertTrue(c["properties"]["site_confirmed_at"] and c["properties"][S.SYNC_AUDIT])
        self.assertEqual(counts["sincronizado_descadastrado"], 1)
        f.hook = None
        counts, _ = run(f, p, sync=True)  # S5/S6: a simulação seguinte acusa o conflito
        self.assertEqual((counts["sync_conflito_confirmacao"], counts["sync_descadastrar"]), (1, 0))
        rc, text = cli(f, p, "--sync")
        self.assertEqual(rc, S.EXIT_CONFLICT)
        self.assertIn("sync_conflito_confirmacao", text)
        self.assertNotIn("saiu@", text)

    def test_s1_no_script_path_ever_writes_unsubscribed_false(self):
        def confirm(fake, method, path, data):
            if method == "PATCH" and path == "/contacts/c-saiu@x.com":
                fake.contacts["saiu@x.com"]["properties"]["site_confirmed_at"] = "2026-11-12T00:00:00Z"
        scenarios = [
            FakeResend(contacts=[substack("saiu@x.com")], props=S.PROPERTIES, hook=confirm),
            FakeResend(contacts=[{"email": "a@x.com", "unsubscribed": True}, substack("saiu@x.com", unsubscribed=True)], props=S.PROPERTIES),
            FakeResend(contacts=[{"email": "a@x.com", "topic": "opt_out"}, substack("saiu@x.com", topic="opt_out")], props=S.PROPERTIES),
            FakeResend(props=S.PROPERTIES, on_create=lambda fk, e: fk.contacts.setdefault(e, {"id": "c-r", "email": e, "unsubscribed": True,
                                                                                            "segments": set(), "properties": {}})),
        ]
        for f in scenarios:
            run(f, export([row("a@x.com"), row("b@x.com")]), apply=True, sync=True)
            for m, path, d in f.calls:
                if m == "PATCH" and path.endswith("/topics"):
                    self.assertEqual(d, [{"id": TOPIC, "subscription": "opt_out"}], d)  # nunca opt_in
                elif m == "PATCH":
                    self.assertNotIn("unsubscribed", d, d)  # o sync não toca no status global
                if m == "POST" and path == "/contacts":
                    self.assertNotIn("topics", d, d)  # a criação não escreve no tópico (o padrão já é opt_in)

    def test_sync_patch_carries_the_audit_stamp(self):
        f = FakeResend(contacts=[substack("saiu@x.com")], props=S.PROPERTIES)
        run(f, export([row("outro@x.com")]), apply=True, sync=True)
        patches = [(path, d) for m, path, d in f.calls if m == "PATCH"]
        # S2: auditoria primeiro, depois o tópico; nada no status global.
        self.assertEqual([path for path, _ in patches], ["/contacts/c-saiu@x.com", "/contacts/c-saiu@x.com/topics"])
        audit, topic = patches[0][1], patches[1][1]
        self.assertEqual(list(audit), ["properties"])
        self.assertEqual(list(audit["properties"]), [S.SYNC_AUDIT])
        self.assertRegex(audit["properties"][S.SYNC_AUDIT], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.000Z$")
        self.assertEqual(topic, [{"id": TOPIC, "subscription": "opt_out"}])

    def test_topic_write_failure_stops_with_the_audit_written_and_the_next_run_retries(self):
        f = FakeResend(contacts=[substack("saiu@x.com")], props=S.PROPERTIES, fail=("PATCH", "/contacts/c-saiu@x.com/topics", 500, "x"))
        with self.assertRaises(S.ImportError_):
            run(f, export([row("outro@x.com")]), apply=True, sync=True)
        c = f.contacts["saiu@x.com"]
        self.assertEqual((c["topic"], bool(c["properties"].get(S.SYNC_AUDIT))), ("opt_in", True))
        f.fail = None
        counts, _ = run(f, export([row("outro@x.com")]), apply=True, sync=True)
        self.assertEqual((counts["sincronizado_descadastrado"], c["topic"]), (1, "opt_out"))

    def test_invalid_topics_response_stops_before_any_write(self):
        bad = [((200, {"object": "list", "data": {}}), "formato inválido"),
               ((200, {"object": "list"}), "formato inválido"),
               ((200, {"object": "list", "data": [{"id": "outro", "subscription": "opt_in"}]}), "ausente"),
               ((200, {"object": "list", "data": [{"id": TOPIC, "subscription": "talvez"}]}), "ausente"),
               ((500, {"name": "x", "message": "x"}), "respondeu 500")]
        for resp, why in bad:
            f = FakeResend(contacts=[substack("saiu@x.com")], props=S.PROPERTIES, topics=lambda c, r=resp: r)
            with self.assertRaisesRegex(S.ImportError_, why, msg=resp):
                run(f, export([row("outro@x.com")]), apply=True, sync=True)
            self.assertEqual(f.writes(), [], resp)

    def test_sync_preread_skips_a_voluntary_unsubscribe_after_planning(self):
        for field, value in (("unsubscribed", True), ("topic", "opt_out")):  # saiu da conta, ou só da newsletter
            def leave(fake, method, path, data, field=field, value=value):
                if method == "GET" and path == "/contact-properties":
                    fake.contacts["saiu@x.com"][field] = value
            f = FakeResend(contacts=[substack("saiu@x.com")], props=S.PROPERTIES, hook=leave)
            counts, _ = run(f, export([row("outro@x.com")]), apply=True, sync=True)
            self.assertEqual([d for m, _, d in f.calls if m == "PATCH"], [], field)
            self.assertEqual(counts["sync_pulado_na_releitura"], 1, field)

    def test_sync_preread_skips_a_contact_whose_origin_changed(self):
        def change(fake, method, path, data):
            if method == "GET" and path == "/contact-properties":
                fake.contacts["saiu@x.com"]["properties"]["source"] = "manual"
        f = FakeResend(contacts=[substack("saiu@x.com")], props=S.PROPERTIES, hook=change)
        counts, _ = run(f, export([row("outro@x.com")]), apply=True, sync=True)
        self.assertFalse(out(f.contacts["saiu@x.com"]))
        self.assertEqual(counts["sync_pulado_na_releitura"], 1)

    def test_sync_skips_a_contact_confirmed_after_planning(self):
        def confirm(fake, method, path, data):
            if method == "GET" and path == "/contact-properties":
                fake.contacts["saiu@x.com"]["properties"]["site_confirmed_at"] = "2026-11-12T00:00:00Z"
        f = FakeResend(contacts=[substack("saiu@x.com")], props=S.PROPERTIES, hook=confirm)
        counts, _ = run(f, export([row("outro@x.com")]), apply=True, sync=True)
        self.assertFalse(out(f.contacts["saiu@x.com"]))
        self.assertEqual(counts["sync_pulado_na_releitura"], 1)
        self.assertEqual([d for m, _, d in f.calls if m == "PATCH"], [])

    def test_sync_guard_boundaries(self):
        # Recusa só acima de 10 E acima de 10%: 50 contatos -> 10 passa, 11 não; 200 -> 20 passa, 21 não.
        for total, ok_n in ((50, 10), (200, 20)):
            for n, allowed in ((ok_n, True), (ok_n + 1, False)):
                many = [substack(f"s{i:03d}@x.com") for i in range(total)]
                f = FakeResend(contacts=many, props=S.PROPERTIES)
                p = export([row(c["email"]) for c in many[n:]])
                if allowed:
                    counts, _ = run(f, p, apply=True, sync=True)
                    self.assertEqual(counts["sincronizado_descadastrado"], n, (total, n))
                else:
                    with self.assertRaises(S.ImportError_, msg=(total, n)):
                        run(f, p, apply=True, sync=True)
                    self.assertEqual(f.writes(), [], (total, n))

    def test_second_sync_is_a_no_op(self):
        f = FakeResend(contacts=[substack("fica@x.com"), substack("saiu@x.com")], props=S.PROPERTIES)
        p = export([row("fica@x.com")])
        run(f, p, apply=True, sync=True)
        before = len(f.writes())
        counts, _ = run(f, p, apply=True, sync=True)
        self.assertEqual(len(f.writes()), before)
        self.assertEqual(counts["sincronizado_descadastrado"], 0)
        self.assertEqual(counts["sync_descadastrar"], 0)  # o plano (e a simulação) não conta quem já saiu

    def test_sync_guard_needs_both_limits(self):
        # 11 de 200 (5,5%) passa: acima de 10, mas abaixo de 10%.
        many = [substack(f"s{i:03d}@x.com") for i in range(200)]
        f = FakeResend(contacts=many, props=S.PROPERTIES)
        counts, _ = run(f, export([row(c["email"]) for c in many[11:]]), apply=True, sync=True)
        self.assertEqual(counts["sincronizado_descadastrado"], 11)


class ApiTest(unittest.TestCase):
    def test_refuses_routes_outside_the_import(self):
        f = FakeResend()
        api = S.Api("re_test", f, sleep=lambda s: None)
        for m, p in (("DELETE", "/contacts/x"), ("POST", "/emails"), ("POST", "/broadcasts"),
                     ("POST", "/broadcasts/b1/send"), ("DELETE", "/segments/seg-1"), ("POST", "/segments"),
                     ("GET", "/contacts/a@x.com/segments"), ("PATCH", "/contacts"), ("GET", "/api-keys"),
                     ("POST", "/api-keys"), ("DELETE", "/api-keys/k1"), ("DELETE", "/contact-properties/p1"),
                     ("PATCH", "/contact-properties/p1"), ("PATCH", "/contacts/x/segments"), ("PATCH", "/contacts/x/topics/t1"),
                     ("GET", "/topics"), ("POST", "/topics"), ("PATCH", "/topics/t1"), ("DELETE", "/topics/t1")):
            with self.assertRaisesRegex(S.ImportError_, "chamada recusada", msg=f"{m} {p}") as cm:
                api.request(m, p, op="teste")
            self.assertNotIn(p, str(cm.exception))
        self.assertEqual(f.calls, [])  # a recusa acontece antes de qualquer chamada ao Resend


class CliTest(unittest.TestCase):
    def test_schema_only_creates_properties(self):
        f = FakeResend(contacts=[{"email": "a@x.com"}])
        rc, text = cli(f, None, "--schema")
        self.assertEqual(rc, 0)
        self.assertEqual(f.props, list(S.PROPERTIES))
        self.assertEqual([p for m, p in f.writes()], ["/contact-properties"] * 5)

    def test_unexpected_transport_error_prints_a_fixed_message(self):
        def broken(method, url, headers, body):
            raise KeyError("resposta estranha com segredo@x.com")
        rc, text = cli(broken, export([row("segredo@x.com")]), "--apply")
        self.assertEqual(rc, 1)
        self.assertIn("falha inesperada (KeyError)", text)
        self.assertNotIn("segredo", text)

    def test_schema_cannot_be_combined_with_import_or_sync(self):
        # --schema só cria propriedades; combinado com --sync ele pularia a contagem S5 e sairia com zero.
        conflict = substack("c@x.com", unsubscribed=True, properties={"source": "substack", "site_confirmed_at": "2026-11-12T00:00:00Z",
                                                                       S.SYNC_AUDIT: "2026-11-12T00:00:01Z"})
        p = export([row("fica@x.com")])
        for flags in (("--schema", "--sync"), ("--schema", "--apply"), ("--schema", "--apply", "--sync")):
            f = FakeResend(contacts=[conflict], props=S.PROPERTIES)
            rc, text = cli(f, p, *flags)
            self.assertEqual(rc, 2, flags)
            self.assertIn("ERRO: argumentos inválidos", text, flags)
            self.assertEqual(f.calls, [], flags)
        for flags in (("--schema", "--sync"), ("--schema", "--apply")):  # também sem export
            f = FakeResend(contacts=[conflict], props=S.PROPERTIES)
            self.assertEqual(cli(f, None, *flags)[0], 2, flags)
            self.assertEqual(f.calls, [], flags)
        f = FakeResend(props=S.PROPERTIES)
        self.assertEqual(cli(f, None, "--schema")[0], 0)

    def test_help_alone_exits_zero_and_help_with_other_options_is_refused(self):
        import subprocess
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "import_subscribers.py")
        env = {**os.environ, "RESEND_API_KEY": "re_x", "NEWSLETTER_SEGMENT_ID": SEG}
        for h in ("-h", "--help"):
            r = subprocess.run([sys.executable, "-B", script, h], capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0, h)
            self.assertIn("--sync", r.stdout, h)
            for args in ([h, "--sync", "a.csv"], ["a.csv", "--sync", h], ["a.csv", "--apply", "--sync", h], [h, "--schema"],
                         [h, "--sync"], ["--sync", h]):
                r = subprocess.run([sys.executable, "-B", script, *args], capture_output=True, text=True, env=env)
                self.assertEqual(r.returncode, 2, args)
                self.assertIn("ERRO: argumentos inválidos", r.stderr, args)

    def test_argument_errors_do_not_echo_the_arguments(self):
        import subprocess
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "import_subscribers.py")
        for args in (["a.csv", "segredo@x.com.csv"], ["--nao-existe=segredo@x.com"]):
            r = subprocess.run([sys.executable, "-B", script, *args], capture_output=True, text=True,
                               env={**os.environ, "RESEND_API_KEY": "re_x", "NEWSLETTER_SEGMENT_ID": SEG})
            self.assertEqual(r.returncode, 2, args)
            self.assertNotIn("segredo", r.stdout + r.stderr, args)
            self.assertIn("ERRO: argumentos inválidos", r.stderr, args)

    def test_unexpected_errors_print_a_fixed_message_without_traceback(self):
        import subprocess
        d = tempfile.mkdtemp()
        missing = os.path.join(d, "segredo@x.com.csv")
        r = subprocess.run([sys.executable, "-B", os.path.join(os.path.dirname(os.path.abspath(__file__)), "import_subscribers.py"),
                            missing], capture_output=True, text=True,
                           env={**os.environ, "RESEND_API_KEY": "re_x", "NEWSLETTER_SEGMENT_ID": SEG, "NEWSLETTER_TOPIC_ID": TOPIC})
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("segredo", r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("ERRO:", r.stderr)


if __name__ == "__main__":
    unittest.main()

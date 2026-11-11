"""Testes da importação de assinantes para o Resend (issue #21, parte C).

Rodar em blog-src/: python3 scripts/test_import_subscribers.py
"""
import csv
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import import_subscribers as S  # noqa: E402

SEG = "seg-1"
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
    """Resend em memória: contatos, segmentos e propriedades, com as mesmas rotas que o script usa."""

    def __init__(self, contacts=None, props=None, limit_429=0):
        self.contacts = {}  # email -> dict
        for c in contacts or []:
            self.contacts[c["email"]] = {"id": "c-" + c["email"], "unsubscribed": False, "segments": set(),
                                         "properties": {}, **c}
        self.props = set(props or [])
        self.calls = []
        self.limit_429 = limit_429

    def __call__(self, method, url, headers, body):
        path = url[len(S.RESEND):]
        data = json.loads(body) if body else None
        self.calls.append((method, path, data))
        if self.limit_429:
            self.limit_429 -= 1
            return 429, {"message": "rate"}, {"retry-after": "0"}
        by_id = {c["id"]: c for c in self.contacts.values()}
        if method == "GET" and path.startswith("/contact-properties"):
            return 200, {"object": "list", "has_more": False, "data": [{"key": k} for k in sorted(self.props)]}, {}
        if method == "POST" and path == "/contact-properties":
            self.props.add(data["key"])
            return 201, {"id": "p-" + data["key"]}, {}
        if method == "GET" and path.startswith("/contacts?"):
            q = dict(x.split("=", 1) for x in path.split("?", 1)[1].split("&"))
            items = sorted(self.contacts.values(), key=lambda c: c["id"])
            if "segment_id" in q:
                items = [c for c in items if q["segment_id"] in c["segments"]]
            if "after" in q:
                items = [c for c in items if c["id"] > q["after"]]
            n = int(q.get("limit", 20))
            page = [{"id": c["id"], "email": c["email"], "unsubscribed": c["unsubscribed"]} for c in items[:n]]
            return 200, {"object": "list", "has_more": len(items) > n, "data": page}, {}
        if method == "GET" and path.startswith("/contacts/"):
            key = path.split("/")[2]
            c = self.contacts.get(key) or by_id.get(key)
            if not c:
                return 404, {"message": "not found"}, {}
            return 200, {"id": c["id"], "email": c["email"], "unsubscribed": c["unsubscribed"],
                         "properties": dict(c["properties"])}, {}
        if method == "POST" and path == "/contacts":
            if data["email"] in self.contacts:
                return 409, {"message": "exists"}, {}
            self.contacts[data["email"]] = {"id": "c-" + data["email"], "email": data["email"],
                                            "unsubscribed": data["unsubscribed"],
                                            "segments": {s["id"] for s in data.get("segments", [])},
                                            "properties": dict(data.get("properties", {}))}
            return 201, {"id": "c-" + data["email"]}, {}
        if method == "PATCH" and path.startswith("/contacts/"):
            c = self.contacts.get(path.split("/")[2]) or by_id.get(path.split("/")[2])
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


def run(fake, path, **kw):
    out = io.StringIO()
    api = S.Api("re_test", fake, sleep=lambda s: None)
    counts = S.run(api, path, SEG, out=out, **kw)
    return counts, out.getvalue()


class ExportTest(unittest.TestCase):
    def test_reads_only_enabled_valid_unique_emails(self):
        p = export([row("A@x.com", created="2024-05-01T00:00:00"), row("a@x.com", created="2023-01-01T00:00:00"),
                    row("b@x.com", disabled="true"), row("sem-arroba"), row(" c@y.org ")])
        subs, skipped = S.load_export(p)
        self.assertEqual([s.email for s in subs], ["a@x.com", "c@y.org"])
        self.assertEqual(subs[0].joined_at, "2023-01-01T00:00:00")  # a entrada mais antiga vale
        self.assertEqual(skipped, {"desabilitado": 1, "invalido": 1, "repetido": 1})

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
        f = FakeResend()
        counts, out = run(f, export([row("a@x.com"), row("b@x.com")]))
        self.assertEqual(f.writes(), [])
        self.assertEqual(counts["criar"], 2)
        self.assertIn("simulação", out)

    def test_creates_contacts_in_segment_with_properties(self):
        f = FakeResend()
        counts, _ = run(f, export([row("a@x.com", created="2024-02-18T15:38:10")]), apply=True)
        c = f.contacts["a@x.com"]
        self.assertEqual(c["segments"], {SEG})
        self.assertFalse(c["unsubscribed"])
        self.assertEqual(c["properties"], {"source": "substack", "substack_joined_at": "2024-02-18T15:38:10",
                                           "substack_paid_until": ""})
        self.assertEqual(f.props, set(S.PROPERTIES))
        self.assertEqual(counts["criado"], 1)

    def test_existing_contact_is_not_overwritten_nor_resubscribed(self):
        f = FakeResend(contacts=[{"email": "a@x.com", "unsubscribed": True, "properties": {"source": "site"}}],
                       props=S.PROPERTIES)
        counts, _ = run(f, export([row("a@x.com")]), apply=True)
        c = f.contacts["a@x.com"]
        self.assertTrue(c["unsubscribed"])
        self.assertEqual(c["properties"], {"source": "site"})
        self.assertEqual(c["segments"], {SEG})
        self.assertEqual(counts["existente_descadastrado"], 1)
        self.assertNotIn(("PATCH", "/contacts/c-a@x.com"), f.writes())

    def test_existing_contact_without_source_gets_substack_properties_only(self):
        f = FakeResend(contacts=[{"email": "a@x.com"}], props=S.PROPERTIES)
        run(f, export([row("a@x.com", created="2025-01-01T00:00:00")]), apply=True)
        self.assertEqual(f.contacts["a@x.com"]["properties"]["source"], "substack")
        self.assertFalse(f.contacts["a@x.com"]["unsubscribed"])

    def test_second_run_is_a_no_op(self):
        f = FakeResend()
        p = export([row("a@x.com"), row("b@x.com")])
        run(f, p, apply=True)
        before = len(f.writes())
        counts, _ = run(f, p, apply=True)
        self.assertEqual(len(f.writes()), before)
        self.assertEqual(counts["no_segmento"], 2)

    def test_rate_limit_is_retried(self):
        f = FakeResend(limit_429=2)
        counts, _ = run(f, export([row("a@x.com")]), apply=True)
        self.assertEqual(counts["criado"], 1)

    def test_output_never_contains_emails(self):
        f = FakeResend()
        _, out = run(f, export([row("segredo@x.com")]), apply=True)
        self.assertNotIn("segredo", out)


class SyncTest(unittest.TestCase):
    def test_sync_unsubscribes_only_substack_contacts_missing_from_export(self):
        f = FakeResend(contacts=[
            {"email": "fica@x.com", "segments": {SEG}, "properties": {"source": "substack"}},
            {"email": "saiu@x.com", "segments": {SEG}, "properties": {"source": "substack"}},
            {"email": "site@x.com", "segments": {SEG}, "properties": {}},
        ], props=S.PROPERTIES)
        counts, _ = run(f, export([row("fica@x.com")]), apply=True, sync=True)
        self.assertFalse(f.contacts["fica@x.com"]["unsubscribed"])
        self.assertTrue(f.contacts["saiu@x.com"]["unsubscribed"])
        self.assertFalse(f.contacts["site@x.com"]["unsubscribed"])
        self.assertEqual(counts["sincronizado_descadastrado"], 1)
        self.assertNotIn("DELETE", [m for m, _ in f.writes()])

    def test_sync_refuses_an_export_much_smaller_than_the_segment(self):
        many = [{"email": f"s{i}@x.com", "segments": {SEG}, "properties": {"source": "substack"}} for i in range(40)]
        f = FakeResend(contacts=many, props=S.PROPERTIES)
        with self.assertRaises(S.ImportError_):
            run(f, export([row("s0@x.com")]), apply=True, sync=True)
        self.assertEqual([w for w in f.writes() if w[0] == "PATCH"], [])


class ApiTest(unittest.TestCase):
    def test_refuses_routes_outside_the_import(self):
        f = FakeResend()
        api = S.Api("re_test", f, sleep=lambda s: None)
        for m, p in (("DELETE", "/contacts/x"), ("POST", "/emails"), ("POST", "/broadcasts"),
                     ("POST", "/broadcasts/b1/send"), ("DELETE", "/segments/seg-1")):
            with self.assertRaisesRegex(S.ImportError_, "chamada recusada", msg=f"{m} {p}"):
                api.request(m, p)
        self.assertEqual(f.calls, [])  # a recusa acontece antes de qualquer chamada ao Resend


if __name__ == "__main__":
    unittest.main()

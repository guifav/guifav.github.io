"""Testes do heating de contatos (épico #197, corte 1). Rodar em blog-src/: python3 scripts/test_heating_sync.py

O D1 falso é um SQLite em memória com as migrações reais do painel (0001 em diante, incluindo 0010, 0011 e 0012), com
suporte ao payload `batch` da API REST do Cloudflare D1 (incluindo simulação de falha no meio do batch sem rollback), e o
Twenty falso guarda estado como o servidor (lixeira, campos únicos `emails` e `inscricaoId`, objeto `courseEnrollment` e
relação `pessoa`).
"""
import contextlib
import copy
import fcntl
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
import urllib.parse
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import conversoes_sync as CS  # noqa: E402
import heating_sync as H  # noqa: E402
import twenty_sync as TS  # noqa: E402

FORGET_KEY = "chave-hmac-de-teste-com-32-bytes-ou-mais-0123456789"
ENV = {
    "CLOUDFLARE_API_TOKEN": "cf",
    "CLOUDFLARE_ACCOUNT_ID": "acc",
    "D1_DATABASE_ID": "db",
    "TWENTY_API_KEY": "tw",
    "TWENTY_LOCAL_URL": "http://127.0.0.1:3100",
    "FORGET_HMAC_KEY": FORGET_KEY,
}
MIGRATIONS = os.path.join(HERE, "..", "..", "workers", "admin", "migrations")
NOW = datetime(2026, 11, 27, 12, 0, 0, tzinfo=timezone.utc)
NOW_ISO = CS.stamp(NOW)
CUSTO = {"id": "CUSTO_IA", "rotulo": "Custo de IA"}
SLUG = "chatgpt-no-dia-a-dia"
CTX_CURSO = f"curso:{SLUG}"


def hexid(n: int) -> str:
    return f"{n:064x}"


def ago(days: float = 0, seconds: float = 0) -> str:
    return CS.stamp(NOW - timedelta(days=days, seconds=seconds))


class Fake:
    """D1 pela API REST (SQLite com migrações reais + batch transacional) e Twenty local com estado e lixeira."""

    def __init__(self, people=(), schema=False, fail=None, db_path=":memory:"):
        self.db = sqlite3.connect(db_path)
        self.db.row_factory = sqlite3.Row
        for name in sorted(n for n in os.listdir(MIGRATIONS) if n.endswith(".sql")):
            with open(os.path.join(MIGRATIONS, name), encoding="utf-8") as fh:
                self.db.executescript(fh.read())
        self.people = {p["id"]: copy.deepcopy(p) for p in people}
        self.person_fields = {
            n: {"id": f"pf-{n}", "name": n, "type": t, "options": None}
            for n, t in (("name", "FULL_NAME"), ("emails", "EMAILS"), ("company", "RELATION"))
        }
        self.enroll_obj = None
        self.enroll_fields: dict = {}
        self.enrolls: dict = {}
        self.conv_obj = None
        self.conv_fields: dict = {}
        self.convs: dict = {}
        self.calls = []
        self.fail = fail
        self.n = 0
        self.max_params = 0
        self._db_close = self.db.close
        if schema:
            c = TS.Clients(ENV, self, sleep=lambda s: None)
            meta = H.read_metadata(c)
            H.ensure_schema(c, meta, need_person=True, need_enroll=True)
            self.calls.clear()

    def __del__(self):
        self._db_close()

    # ---- Helpers de seed no D1 ----
    def add_conv(
        self,
        n,
        email,
        action="material_download",
        material="planilha-custos-ia",
        when=None,
        verified=None,
        file=None,
        interests=(CUSTO,),
    ):
        if verified is None:
            verified = 0 if action == "material_request" else 1
        if action == "material_download" and file is None:
            file = material
        if action == "newsletter_confirm":
            material = None
            file = None
        self.db.execute(
            "INSERT INTO conversions (id, email, action, material, interests, occurred_at, verified, file) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                hexid(n) if isinstance(n, int) else n,
                email,
                action,
                material,
                json.dumps(list(interests)),
                when or ago(days=1),
                verified,
                file,
            ),
        )
        self.db.commit()

    def add_event(
        self,
        ev_id: str,
        email: str,
        signal: str,
        *,
        context: str = "site",
        source: str = "resend",
        ref: str | None = "em-1",
        when: str | None = None,
    ):
        self.db.execute(
            "INSERT INTO engagement_events (id, context, email, signal, source, ref, occurred_at, recorded_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (ev_id, context, email, signal, source, ref, when or ago(days=1), NOW_ISO),
        )
        self.db.commit()

    def add_subscriber(self, sid: str, email: str, unsubscribed: int = 0):
        self.db.execute(
            "INSERT INTO subscribers (id, email, unsubscribed, origin, joined_at, paid_until, site_confirmed_at, "
            "admin_unsubscribed_at, created_at, run, rev) VALUES (?, ?, ?, 'site', '2026-01-01', '', '', '', '', 'r0', 0)",
            (sid, email, unsubscribed),
        )
        self.db.commit()

    def add_optout(self, email: str, opted_out_at: str = NOW_ISO):
        mac = H.heating_optout_hmac(email, FORGET_KEY)
        self.db.execute("INSERT OR REPLACE INTO heating_optout (email_hmac, opted_out_at) VALUES (?, ?)", (mac, opted_out_at))
        self.db.commit()

    def heating_rows(self) -> dict[tuple[str, str], dict]:
        return {
            (r["context"], r["email"]): dict(r)
            for r in self.db.execute("SELECT * FROM heating ORDER BY context, email")
        }

    def event_ids(self) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT id FROM engagement_events ORDER BY id")]

    # ---- Transporte ----
    def __call__(self, method, url, headers, body):
        data = json.loads(body) if body else None
        u = urllib.parse.urlparse(url)
        self.calls.append((method, u.path, data, u.query))
        if self.fail:
            hit = self.fail(method, u.path, data)
            if hit:
                return hit
        if u.netloc == "api.cloudflare.com":
            assert headers["Authorization"] == "Bearer cf" and u.path == "/client/v4/accounts/acc/d1/database/db/query"
            if "batch" in data:
                results = []
                try:
                    self.db.execute("BEGIN")
                    for item in data["batch"]:
                        assert "@" not in item["sql"], "email só por parâmetro"
                        params = item.get("params") or []
                        self.max_params = max(self.max_params, len(params))
                        assert len(params) <= 100, f"limite de 100 parâmetros excedido: {len(params)}"
                        cur = self.db.execute(item["sql"], params)
                        rows = [dict(r) for r in cur.fetchall()] if cur.description else []
                        results.append({"results": rows, "success": True})
                    self.db.commit()
                except Exception:
                    self.db.rollback()
                    raise
                return 200, {"success": True, "result": results}
            assert "@" not in data["sql"], "email só por parâmetro"
            params = data.get("params") or []
            self.max_params = max(self.max_params, len(params))
            assert len(params) <= 100, f"limite de 100 parâmetros excedido: {len(params)}"
            cur = self.db.execute(data["sql"], params)
            rows = [dict(r) for r in cur.fetchall()] if cur.description else []
            self.db.commit()
            return 200, {"success": True, "result": [{"results": rows, "success": True}]}
        assert headers["Authorization"] == "Bearer tw" and u.netloc == "127.0.0.1:3100"
        st, res = self._twenty(method, u, data)
        return st, json.loads(json.dumps(res))

    def _twenty(self, method, u, data):
        if u.path == "/metadata":
            q = data["query"]
            v = data.get("variables") or {}
            if "createOneObject" in q:
                o = v["input"]["object"]
                if o["nameSingular"] == "courseEnrollment":
                    assert self.enroll_obj is None
                    self.enroll_obj = {"id": "obj-enroll", **o}
                    self.enroll_fields = {"name": {"id": "ef-name", "name": "name", "type": "TEXT", "options": None}}
                    return 200, {"data": {"createOneObject": {"id": "obj-enroll"}}}
                assert self.conv_obj is None
                self.conv_obj = {"id": "obj-conv", **o}
                self.conv_fields = {"name": {"id": "cf-name", "name": "name", "type": "TEXT", "options": None}}
                return 200, {"data": {"createOneObject": {"id": "obj-conv"}}}
            if "createOneField" in q:
                f = v["input"]["field"]
                target = {"obj-person": self.person_fields, "obj-enroll": self.enroll_fields, "obj-conv": self.conv_fields}[f["objectMetadataId"]]
                assert f["name"] not in target, f"campo duplicado {f['name']}"
                opts = [{"id": f"opt-{f['name']}-{o['value']}", **o} for o in f.get("options") or []] or None
                node = {"id": f"fid-{f['name']}", "name": f["name"], "type": f["type"], "options": opts, "_spec": f}
                target[f["name"]] = node
                if f["type"] == "RELATION":
                    rp = f["relationCreationPayload"]
                    assert rp["targetObjectMetadataId"] == "obj-person" and rp["type"] == "MANY_TO_ONE"
                    inv = "inscricoesEmCursos" if f["objectMetadataId"] == "obj-enroll" else "conversoesDoSite"
                    self.person_fields[inv] = {"id": f"pf-{inv}", "name": inv, "type": "RELATION", "options": None}
                return 200, {"data": {"createOneField": {k: val for k, val in node.items() if k != "_spec"}}}
            if "updateOneField" in q:
                i = v["input"]
                for src in (self.person_fields, self.enroll_fields, self.conv_fields):
                    for node in src.values():
                        if node["id"] == i["id"]:
                            old = {o["id"] for o in node["options"] or []}
                            sent = i["update"]["options"]
                            assert old <= {o.get("id") for o in sent}, "opção antiga sumiu"
                            node["options"] = [o if o.get("id") else {"id": f"opt-{node['name']}-{o['value']}", **o} for o in sent]
                            return 200, {"data": {"updateOneField": {"id": node["id"], "options": node["options"]}}}
                return 200, {"errors": [{"message": "campo não encontrado"}]}

            def obj(oid, ns, np, fields):
                return {"node": {"id": oid, "nameSingular": ns, "namePlural": np,
                                 "fields": {"edges": [{"node": {k: val for k, val in f.items() if k != "_spec"}} for f in fields.values()]}}}
            edges = [obj("obj-person", "person", "people", self.person_fields)]
            if self.enroll_obj:
                edges.append(obj("obj-enroll", self.enroll_obj["nameSingular"], self.enroll_obj["namePlural"], self.enroll_fields))
            if self.conv_obj:
                edges.append(obj("obj-conv", self.conv_obj["nameSingular"], self.conv_obj["namePlural"], self.conv_fields))
            return 200, {"data": {"objects": {"edges": edges}}}

        q = dict(urllib.parse.parse_qsl(u.query))
        if u.path in ("/rest/people", "/rest/courseEnrollments", "/rest/siteConversions") and method == "GET":
            store, key = {
                "/rest/people": (self.people, "people"),
                "/rest/courseEnrollments": (self.enrolls, "courseEnrollments"),
                "/rest/siteConversions": (self.convs, "siteConversions"),
            }[u.path]
            items = sorted(store.values(), key=lambda x: x["id"])
            if q.get("filter") is None:
                items = [x for x in items if not x.get("deletedAt")]
            elif q["filter"] != CS.WITH_TRASH:
                return 400, {"messages": ["filtro inesperado"]}
            if "starting_after" in q:
                items = [x for x in items if x["id"] > q["starting_after"]]
            lim = int(q["limit"])
            page = items[:lim]
            return 200, {"data": {key: page}, "pageInfo": {"hasNextPage": len(items) > lim, "endCursor": page[-1]["id"] if page else None}}

        if u.path == "/rest/people" and method == "POST":
            if any((p.get("emails") or {}).get("primaryEmail") == data["emails"]["primaryEmail"] for p in self.people.values()):
                return 400, {"messages": ["A duplicate entry was detected"]}
            for k in data:
                assert k in ("emails", "name") or k in self.person_fields, f"campo inexistente {k}"
            self.n += 1
            pid = f"new-{self.n:04d}"
            self.people[pid] = {"id": pid, "createdAt": f"2026-11-27T12:00:{self.n:02d}.000Z", **data}
            return 201, {"data": {"createPerson": {"id": pid}}}

        if u.path.startswith("/rest/people/") and method == "PATCH":
            pid = urllib.parse.unquote(u.path.rsplit("/", 1)[1])
            for k in data:
                assert k in self.person_fields, f"campo inexistente {k}"
            self.people[pid].update(data)
            return 200, {"data": {}}

        if u.path == "/rest/courseEnrollments" and method == "POST":
            assert self.enroll_obj
            for k in data:
                assert k in self.enroll_fields or (k == "pessoaId" and "pessoa" in self.enroll_fields), f"campo inexistente {k}"
            if any(e.get("inscricaoId") == data["inscricaoId"] for e in self.enrolls.values()):
                return 400, {"messages": ["A duplicate entry was detected"]}
            self.n += 1
            eid = f"enr-{self.n:04d}"
            self.enrolls[eid] = {"id": eid, **data}
            return 201, {"data": {"createCourseEnrollment": {"id": eid}}}

        if u.path.startswith("/rest/courseEnrollments/") and method == "PATCH":
            eid = urllib.parse.unquote(u.path.rsplit("/", 1)[1])
            for k in data:
                assert k in self.enroll_fields or (k == "pessoaId" and "pessoa" in self.enroll_fields), f"campo inexistente {k}"
            if "pessoaId" in data and data["pessoaId"] == "":
                return 400, {"messages": ["invalid input syntax for type uuid: \"\""]}
            self.enrolls[eid].update(data)
            return 200, {"data": {}}

        if u.path.startswith("/rest/courseEnrollments/") and method == "DELETE":
            eid = urllib.parse.unquote(u.path.rsplit("/", 1)[1])
            del self.enrolls[eid]
            return 200, {"data": {"deleteCourseEnrollment": {"id": eid}}}

        if u.path.startswith("/rest/siteConversions/") and method == "DELETE":
            cid = urllib.parse.unquote(u.path.rsplit("/", 1)[1])
            del self.convs[cid]
            return 200, {"data": {"deleteSiteConversion": {"id": cid}}}

        return 500, {"errors": [{"message": f"rota inesperada {method} {u.path}"}]}

    def twenty_writes(self):
        return [
            (m, p) for m, p, d, _ in self.calls
            if not p.endswith("/query") and m != "GET"
            and not (p == "/metadata" and not any(x in d["query"] for x in ("createOne", "updateOne")))
        ]

    def d1_writes(self):
        out = []
        for _m, p, d, _ in self.calls:
            if not p.endswith("/query"):
                continue
            if "batch" in d:
                out.append("BATCH:" + ";".join(x["sql"].split()[0] for x in d["batch"]))
            elif not d["sql"].lstrip().upper().startswith("SELECT"):
                out.append(d["sql"])
        return out


def person(pid, email, extra=(), created="2026-01-01T00:00:00.000Z", **fields):
    return {
        "id": pid,
        "createdAt": created,
        "name": {"firstName": "Nome", "lastName": "Original"},
        "emails": {"primaryEmail": email, "additionalEmails": list(extra)},
        **fields,
    }


def run_job(fake, *, apply=False, now_dt=NOW, ler_curso=None, email_desde=None, exclude_emails=None, extra_env=None):
    out = io.StringIO()
    env = {**ENV, **(extra_env or {})}
    if email_desde is not None:
        env["HEATING_EMAIL_DESDE"] = email_desde
    reader = (lambda slug: []) if ler_curso is None else ler_curso
    counts = H.run(
        TS.Clients(env, fake, sleep=lambda s: None),
        apply=apply,
        out=out,
        now_dt=now_dt,
        ler_curso=reader,
        forget_key=FORGET_KEY,
        exclude_emails=exclude_emails,
    )
    return counts, out.getvalue()


# ------------------------------------------------------------------ Testes unitários da régua

class ReguaTest(unittest.TestCase):
    def _ev(self, ev_id, signal, *, days=0.0, seconds=0.0, context="site", ref="ref-1"):
        return {
            "id": ev_id,
            "context": context,
            "email": "u@corp.com",
            "signal": signal,
            "source": "resend",
            "ref": ref,
            "occurred_at": ago(days=days, seconds=seconds),
        }

    def test_meia_vida_180_dias_e_futuro(self):
        # 12 * 0.5**(30/30) = 6.0; 12 * 0.5**(0/30) = 12.0
        r0 = H.calcular_heating_contexto("site", [self._ev("e1", "material_baixado", days=0)], NOW)
        self.assertEqual((r0["score"], r0["band"]), (12, "frio"))
        r30 = H.calcular_heating_contexto("site", [self._ev("e1", "material_baixado", days=30)], NOW)
        self.assertEqual((r30["score"], r30["band"], r30["signals_list"][0]["pontos"]), (6, "frio", 6.0))
        # Exatamente 180 dias: entra (50 * 0.5**6 = 0.78125 -> round 1).
        r180 = H.calcular_heating_contexto("site", [self._ev("e1", "site_agende", days=180)], NOW)
        self.assertEqual((r180["score"], r180["signals_list"][0]["pontos"]), (1, 0.8))
        # > 180 dias: não entra.
        self.assertIsNone(H.calcular_heating_contexto("site", [self._ev("e1", "site_agende", days=180, seconds=1)], NOW))
        # Evento futuro (> T): não entra.
        self.assertIsNone(H.calcular_heating_contexto("site", [self._ev("e1", "site_agende", seconds=-1)], NOW))

    def test_tetos_por_dia_e_mais_recente(self):
        # newsletter_confirmada: só a mais recente conta (10 hoje, ignora a de 10 dias atrás).
        evs = [
            self._ev("n1", "newsletter_confirmada", days=10, ref=None),
            self._ev("n2", "newsletter_confirmada", days=0, ref=None),
        ]
        r = H.calcular_heating_contexto("site", evs, NOW)
        self.assertEqual(r["score"], 10)
        # email_aberto: teto 1/dia UTC; email_clicado: teto 2/dia UTC.
        desde = ago(days=30)
        evs2 = [
            self._ev("o1", "email_aberto", days=0, seconds=600, ref="m1"),
            self._ev("o2", "email_aberto", days=0, seconds=300, ref="m2"),
            self._ev("o3", "email_aberto", days=0, seconds=100, ref="m3"),
            # 3 cliques no mesmo dia (refs diferentes para não disparar rajada): só 2 contam -> 8 pts + 1 aberto = 9.
            self._ev("c1", "email_clicado", days=0, seconds=500, ref="m1"),
            self._ev("c2", "email_clicado", days=0, seconds=400, ref="m2"),
            self._ev("c3", "email_clicado", days=0, seconds=300, ref="m3"),
        ]
        r2 = H.calcular_heating_contexto("site", evs2, NOW, email_desde=desde)
        self.assertEqual(r2["score"], 9)
        # site_visita (2, 1/dia), site_engajada (3, 1/dia), site_agende (50, 1/dia), site_contato (50, 1/dia): teto 100.
        evs3 = [
            self._ev("v1", "site_visita", days=0, seconds=10),
            self._ev("v2", "site_visita", days=0, seconds=20),
            self._ev("g1", "site_engajada", days=0, seconds=10),
            self._ev("g2", "site_engajada", days=0, seconds=20),
            self._ev("a1", "site_agende", days=0, seconds=10),
            self._ev("a2", "site_agende", days=0, seconds=20),
            self._ev("k1", "site_contato", days=0, seconds=10),
            self._ev("k2", "site_contato", days=0, seconds=20),
        ]
        r3 = H.calcular_heating_contexto("site", evs3, NOW)
        self.assertEqual((r3["score"], r3["band"]), (100, "quente"))

    def test_teto_passivos_proporcional_site_e_curso(self):
        # Site: 10 dias de site_visita (2) + 10 dias de site_engajada (3) -> sem teto daria ~45; com teto para em 25
        # mantendo a proporção 2:3 (10,0 e 15,0).
        evs = []
        for d in range(10):
            evs.append(self._ev(f"v{d}", "site_visita", days=d))
            evs.append(self._ev(f"g{d}", "site_engajada", days=d))
        r = H.calcular_heating_contexto("site", evs, NOW)
        self.assertEqual((r["score"], r["band"]), (25, "morno"))
        pts = {s["sinal"]: s["pontos"] for s in r["signals_list"]}
        self.assertEqual(pts, {"site_engajada": 15.0, "site_visita": 10.0})

        # Curso: 40 dias seguidos de email_aberto (1/dia) -> soma sem teto > 26; com teto para em 20 (morno).
        evs_c = [self._ev(f"oc{d}", "email_aberto", days=d, context=CTX_CURSO, ref=f"em-{d}") for d in range(40)]
        rc = H.calcular_heating_contexto(CTX_CURSO, evs_c, NOW, email_desde=ago(days=100))
        self.assertEqual((rc["score"], rc["band"]), (20, "morno"))

    def test_material_maior_ponto_e_materiais_distintos(self):
        # Mesmo material: pedido (2) + download (12) -> só 12 conta; dois downloads -> só o mais recente conta.
        evs = [
            self._ev("p1", "material_pedido", days=0, ref="planilha"),
            self._ev("d1", "material_baixado", days=30, ref="planilha"),
            self._ev("d2", "material_baixado", days=0, ref="planilha"),
            # Outro material: soma!
            self._ev("p2", "material_pedido", days=0, ref="mapa"),
        ]
        r = H.calcular_heating_contexto("site", evs, NOW)
        self.assertEqual((r["score"], r["band"]), (14, "frio"))
        self.assertEqual(
            [(s["sinal"], s["pontos"]) for s in r["signals_list"]],
            [("material_baixado", 12.0), ("material_pedido", 2.0)],
        )

    def test_robo_por_entrega_e_por_rajada(self):
        desde = ago(days=30)
        # Entrega há 100 s; clique 60 s depois da entrega -> descartado; clique 61 s depois -> conta (4 pts).
        evs_entrega = [
            self._ev("ent", "email_entregue", seconds=100, ref="em-a"),
            self._ev("bot", "email_clicado", seconds=40, ref="em-a"),   # 60 s após entrega: descartado
            self._ev("hum", "email_clicado", seconds=39, ref="em-a"),   # 61 s após entrega: mas está a 1 s de `bot`!
        ]
        # Como `bot` (40 s atrás) e `hum` (39 s atrás) têm 1 s entre si no mesmo ref, ambos caem na rajada!
        self.assertIsNone(H.calcular_heating_contexto("site", evs_entrega, NOW, email_desde=desde))

        # Separando o humano (sem rajada): entrega há 200 s, bot 140 s atrás (60 s depois), humano 100 s atrás (100 s depois, 40 s do bot).
        evs_sep = [
            self._ev("ent", "email_entregue", seconds=200, ref="em-a"),
            self._ev("bot", "email_clicado", seconds=140, ref="em-a"),
            self._ev("hum", "email_clicado", seconds=100, ref="em-a"),
        ]
        r = H.calcular_heating_contexto("site", evs_sep, NOW, email_desde=desde)
        self.assertEqual(r["score"], 4)

        # Rajada pura (sem email_entregue): dois cliques a 5 s de distância no mesmo ref -> ambos descartados.
        evs_burst = [
            self._ev("b1", "email_clicado", seconds=100, ref="em-b"),
            self._ev("b2", "email_clicado", seconds=95, ref="em-b"),
        ]
        self.assertIsNone(H.calcular_heating_contexto("site", evs_burst, NOW, email_desde=desde))

        # Dois cliques a 6 s de distância no mesmo ref -> ambos contam (8 pts).
        evs_ok = [
            self._ev("b1", "email_clicado", seconds=100, ref="em-b"),
            self._ev("b2", "email_clicado", seconds=94, ref="em-b"),
        ]
        self.assertEqual(H.calcular_heating_contexto("site", evs_ok, NOW, email_desde=desde)["score"], 8)

    def test_heating_email_desde_e_descadastro(self):
        evs = [
            self._ev("o1", "email_aberto", days=10, ref="m1"),
            self._ev("c1", "email_clicado", days=2, ref="m2"),
        ]
        # Sem HEATING_EMAIL_DESDE: abertura e clique não contam.
        self.assertIsNone(H.calcular_heating_contexto("site", evs, NOW, email_desde=None))
        # Com HEATING_EMAIL_DESDE há 5 dias: só o clique (há 2 dias) conta -> 4 * 0.5**(2/30) = 3.82 -> 4.
        r = H.calcular_heating_contexto("site", evs, NOW, email_desde=ago(days=5))
        self.assertEqual((r["score"], [s["sinal"] for s in r["signals_list"]]), (4, ["email_clicado"]))
        # Exatamente igual a HEATING_EMAIL_DESDE entra; 1 ms antes fica de fora.
        exato = ago(days=5)
        antes_1ms = CS.stamp(NOW - timedelta(days=5, milliseconds=1))
        ev_eq = {**self._ev("eq", "email_clicado", days=5, ref="m-eq"), "occurred_at": exato}
        ev_lt = {**self._ev("lt", "email_clicado", days=5, ref="m-lt"), "occurred_at": antes_1ms}
        r_eq = H.calcular_heating_contexto("site", [ev_eq], NOW, email_desde=exato)
        self.assertIsNotNone(r_eq)
        self.assertEqual([s["sinal"] for s in r_eq["signals_list"]], ["email_clicado"])
        self.assertIsNone(H.calcular_heating_contexto("site", [ev_lt], NOW, email_desde=exato))
        # Descadastrado: perde sinais de email, mas mantém outros sinais.
        evs2 = evs + [self._ev("d1", "material_baixado", days=0, ref="planilha")]
        r_unsub = H.calcular_heating_contexto("site", evs2, NOW, email_desde=ago(days=30), email_bloqueado=True)
        self.assertEqual((r_unsub["score"], [s["sinal"] for s in r_unsub["signals_list"]]), (12, ["material_baixado"]))

    def test_calcular_heating_contexto_usa_datetime_e_dia_utc(self):
        # 1) email_desde sem milissegundos ('...Z') vs occurred_at com milissegundos ('...000Z'):
        #    como texto, '.' < 'Z' falharia; por datetime, são iguais e o evento conta.
        ev_ms = {**self._ev("c1", "email_clicado", ref="m1"), "occurred_at": "2026-11-27T12:00:00.000Z"}
        r = H.calcular_heating_contexto("site", [ev_ms], NOW, email_desde="2026-11-27T12:00:00Z")
        self.assertIsNotNone(r)
        self.assertEqual(r["score"], 4)

        # 2) Dia UTC por _dt (não por slice [:10]): 2026-11-26T22:00:00-03:00 == 2026-11-27T01:00:00Z,
        #    mesmo dia UTC que 2026-11-27T02:00:00.000Z -> teto de 1 email_aberto/dia UTC mantém só 1 (1 pt, não 2).
        evs_tz = [
            {**self._ev("o1", "email_aberto", ref="m1"), "occurred_at": "2026-11-26T22:00:00-03:00"},
            {**self._ev("o2", "email_aberto", ref="m2"), "occurred_at": "2026-11-27T02:00:00.000Z"},
        ]
        r_tz = H.calcular_heating_contexto("site", evs_tz, NOW, email_desde="2026-11-01T00:00:00Z")
        self.assertEqual(r_tz["score"], 1)

        # 3) Ordenação por _dt (não por texto): 2026-11-26T23:00:00-03:00 (2026-11-27T02:00Z) é MAIS recente
        #    que 2026-11-27T01:00:00Z; em newsletter_confirmada (só a mais recente conta), vence a de 02:00Z.
        now_t = datetime(2026, 11, 27, 2, 0, 0, tzinfo=timezone.utc)
        evs_ord = [
            {**self._ev("n-old", "newsletter_confirmada", ref=None), "occurred_at": "2026-11-01T01:00:00Z"},
            {**self._ev("n-new", "newsletter_confirmada", ref=None), "occurred_at": "2026-11-26T23:00:00-03:00"},
        ]
        r_ord = H.calcular_heating_contexto("site", evs_ord, now_t)
        self.assertEqual(r_ord["score"], 10)

    def test_spam_365_dias_faixas_e_top_3(self):
        # Spam de 300 dias e exatamente 365 dias atrás (> 180d, <= 365d) zera mesmo com Agende hoje.
        for sp_days in (300, 365):
            evs = [
                self._ev("sp", "email_spam", days=sp_days, ref="m0"),
                self._ev("ag", "site_agende", days=0, ref=None),
            ]
            r = H.calcular_heating_contexto("site", evs, NOW)
            self.assertEqual(r["score"], 0)
            self.assertEqual(r["band"], "frio")
            self.assertEqual(r["signals"], '[{"sinal":"email_spam","rotulo":"Reclamou de spam: não abordar","pontos":0}]')

        # Spam com mais de 365 dias (> 365d) NÃO zera a nota.
        evs_old_spam = [
            self._ev("sp-old", "email_spam", days=365, seconds=1, ref="m0"),
            self._ev("dl", "material_baixado", days=0, ref="m1"),
        ]
        r_old = H.calcular_heating_contexto("site", evs_old_spam, NOW)
        self.assertEqual((r_old["score"], r_old["band"]), (12, "frio"))

        # Fronteiras exatas das faixas: 14 frio, 15 morno, 39 morno, 40 quente.
        r14 = H.calcular_heating_contexto("site", [
            self._ev("m1", "material_baixado", days=0, ref="a"),  # 12
            self._ev("p1", "material_pedido", days=0, ref="b"),   # 2 -> 14
        ], NOW)
        self.assertEqual((r14["score"], r14["band"]), (14, "frio"))
        r15 = H.calcular_heating_contexto("site", [
            self._ev("m1", "material_baixado", days=0, ref="a"),  # 12
            self._ev("g1", "site_engajada", days=0, ref=None),    # 3 -> 15
        ], NOW)
        self.assertEqual((r15["score"], r15["band"]), (15, "morno"))
        r39 = H.calcular_heating_contexto("site", [
            self._ev("m1", "material_baixado", days=0, ref="a"),  # 12
            self._ev("m2", "material_baixado", days=0, ref="b"),  # 12
            self._ev("m3", "material_baixado", days=0, ref="c"),  # 12
            self._ev("g1", "site_engajada", days=0, ref=None),    # 3 -> 39
        ], NOW)
        self.assertEqual((r39["score"], r39["band"]), (39, "morno"))
        r40 = H.calcular_heating_contexto("site", [
            self._ev("m1", "material_baixado", days=0, ref="a"),  # 12
            self._ev("m2", "material_baixado", days=0, ref="b"),  # 12
            self._ev("m3", "material_baixado", days=0, ref="c"),  # 12
            self._ev("p1", "material_pedido", days=0, ref="d"),   # 2
            self._ev("p2", "material_pedido", days=0, ref="e"),   # 2 -> 40
        ], NOW)
        self.assertEqual((r40["score"], r40["band"]), (40, "quente"))

        # Top 3 sinais em JSON compacto.
        evs_top = [
            self._ev("a1", "site_agende", days=0),       # 50
            self._ev("m1", "material_baixado", days=0, ref="mat1"),  # 12
            self._ev("n1", "newsletter_confirmada", days=0),  # 10
            self._ev("v1", "site_visita", days=0),       # 2 (4º lugar: fica fora do top 3)
        ]
        rt = H.calcular_heating_contexto("site", evs_top, NOW)
        self.assertEqual((rt["score"], rt["band"]), (74, "quente"))
        self.assertEqual(
            [s["sinal"] for s in rt["signals_list"]],
            ["site_agende", "material_baixado", "newsletter_confirmada"],
        )
        self.assertNotIn(", ", rt["signals"])  # JSON compacto

        # Curso: cadastro (10) + 1 aula hoje (5) = 15 -> morno; mais 1 aula ontem + 1 clique (3) + 1 aberto (1) -> 24.
        evs_c = [
            self._ev("cad", "curso_cadastro", days=0, context=CTX_CURSO, ref=None),
            self._ev("au1", "curso_aula", days=0, context=CTX_CURSO, ref="d1"),
            self._ev("au2", "curso_aula", days=0, seconds=60, context=CTX_CURSO, ref="d2"),  # mesmo dia: teto 1/dia
        ]
        rc = H.calcular_heating_contexto(CTX_CURSO, evs_c, NOW)
        self.assertEqual((rc["score"], rc["band"]), (15, "morno"))


# ------------------------------------------------------------------ Testes integrados do job, Twenty e CLI

class HeatingSyncTest(unittest.TestCase):
    def test_separacao_site_e_curso_criacao_de_pessoa_e_inscricao_e_idempotencia(self):
        # Pessoa existente no CRM + nova pessoa vinda do curso (com nome Google) + bounced (não cria Pessoa)
        # + email só do site sem Pessoa (`sem_pessoa`).
        f = Fake(people=[person("p1", "ana@corp.com")])
        f.add_conv(1, "ana@corp.com", action="material_download", material="planilha-custos-ia", when=ago(days=0))
        f.add_conv(2, "sempessoa@corp.com", action="newsletter_confirm", when=ago(days=0))
        f.add_event("ev-ag", "ana@corp.com", "site_agende", source="ga4", ref=None, when=ago(days=0))

        curso_docs = [
            {
                "email": "Bia@Corp.com",
                "status": "active",
                "source": "google",
                "googleUid": "uid_bia_1",
                "name": "Bia Maria Souza",
                "createdAt": ago(days=0),
                "aulas": [
                    {"dayId": "day-01", "completed": True, "updatedAt": ago(days=0)},
                    {"dayId": "day-02", "completed": False, "updatedAt": ago(days=0)},  # não concluída
                ],
            },
            {
                "email": "bounce@corp.com",
                "status": "bounced",
                "source": "form",
                "googleUid": None,
                "name": "Bounce User",
                "createdAt": ago(days=0),
                "aulas": [],
            },
            {
                # Ana também faz o curso, mas isso NÃO pode mudar a nota dela no contexto `site` (62 = 50 + 12).
                "email": "ana@corp.com",
                "status": "completed",
                "source": "google",
                "googleUid": "uid_ana_1",
                "name": "Outro Nome",
                "createdAt": ago(days=0),
                "aulas": [
                    {"dayId": "day-01", "completed": True, "updatedAt": ago(days=2)},
                    {"dayId": "day-02", "completed": True, "updatedAt": ago(days=1)},
                    {"dayId": "day-03", "completed": True, "updatedAt": ago(days=0)},
                ],
            },
        ]

        # 1) Simulação não escreve nada.
        counts_dry, out_dry = run_job(f, apply=False, ler_curso=lambda slug: curso_docs)
        self.assertEqual((f.twenty_writes(), f.d1_writes()), ([], []))
        self.assertIn("simulação", out_dry)
        self.assertEqual(counts_dry["pessoas_criadas"], 1)  # só Bia (bounce não cria)
        self.assertEqual(counts_dry["sem_pessoa"], 1)       # sempessoa@corp.com
        self.assertEqual(counts_dry["inscricoes_criadas"], 2)  # Ana e Bia

        # 2) --apply grava tudo.
        counts, out = run_job(f, apply=True, ler_curso=lambda slug: curso_docs)
        self.assertIn("gravado", out)
        self.assertEqual(counts["eventos_conversao_novos"], 2)
        self.assertEqual(counts["eventos_curso_novos"], 7)  # 3 cadastros + 4 aulas concluídas
        self.assertEqual(counts["notas_site"], 2)           # ana, sempessoa
        self.assertEqual(counts["notas_curso"], 3)          # ana, bia, bounce
        self.assertEqual(counts["quentes_site"], 1)         # ana (62)
        self.assertEqual(counts["pessoas_atualizadas"], 1)  # ana
        self.assertEqual(counts["pessoas_criadas"], 1)      # bia
        self.assertEqual(counts["inscricoes_criadas"], 2)   # ana, bia
        self.assertEqual(counts["sem_pessoa"], 1)           # sempessoa
        self.assertLessEqual(f.max_params, 100)

        # Ana: nota site = 62 (não contaminada pelo curso!), nome original preservado; sinais no Twenty sem pontos.
        p_ana = f.people["p1"]
        self.assertEqual(p_ana["name"], {"firstName": "Nome", "lastName": "Original"})
        self.assertEqual((p_ana["heatingNota"], p_ana["heatingFaixa"]), (62, "QUENTE"))
        self.assertEqual(p_ana["heatingSinais"], "Clique em Agende; Download de material")
        self.assertIn('"pontos":12.0', f.heating_rows()[("site", "ana@corp.com")]["signals"])

        # Bia: criada com firstName/lastName divididos, sem nota site na Person.
        p_bia = next(p for p in f.people.values() if p["emails"]["primaryEmail"] == "bia@corp.com")
        self.assertEqual(p_bia["name"], {"firstName": "Bia", "lastName": "Maria Souza"})
        self.assertNotIn("heatingNota", p_bia)

        # Bounce: não criou Pessoa nem Inscrição no Twenty, mas tem nota de curso no D1 com twenty_person_id nulo.
        self.assertFalse(any(p["emails"]["primaryEmail"] == "bounce@corp.com" for p in f.people.values()))
        hrows = f.heating_rows()
        self.assertEqual(hrows[("site", "ana@corp.com")]["score"], 62)
        self.assertEqual(hrows[("site", "ana@corp.com")]["twenty_person_id"], "p1")
        self.assertEqual(hrows[("site", "sempessoa@corp.com")]["twenty_person_id"], None)
        self.assertEqual(hrows[(CTX_CURSO, "bia@corp.com")]["score"], 15)
        self.assertEqual(hrows[(CTX_CURSO, "bia@corp.com")]["twenty_person_id"], p_bia["id"])
        self.assertEqual(hrows[(CTX_CURSO, "bounce@corp.com")]["twenty_person_id"], None)

        # Inscrição da Bia no Twenty (sinais sem pontos).
        enr_bia = next(e for e in f.enrolls.values() if e["pessoaId"] == p_bia["id"])
        self.assertEqual(
            (enr_bia["curso"], enr_bia["status"], enr_bia["aulasConcluidas"], enr_bia["nota"], enr_bia["faixa"], enr_bia["sinais"]),
            ("CHATGPT_NO_DIA_A_DIA", "ATIVO", 1, 15, "MORNO", "Cadastro no curso; Aula concluída no curso"),
        )

        # 3) Idempotência: segunda rodada sem mudança (mesmo T) não grava nada nem no D1 nem no Twenty!
        n_tw, n_d1 = len(f.twenty_writes()), len(f.d1_writes())
        counts2, _ = run_job(f, apply=True, ler_curso=lambda slug: curso_docs)
        self.assertEqual(len(f.twenty_writes()), n_tw)
        self.assertEqual(len(f.d1_writes()), n_d1)
        self.assertEqual(
            (counts2["eventos_conversao_novos"], counts2["eventos_curso_novos"],
             counts2["pessoas_atualizadas"], counts2["pessoas_criadas"],
             counts2["inscricoes_criadas"], counts2["inscricoes_atualizadas"]),
            (0, 0, 0, 0, 0, 0),
        )

    def test_oposicao_exclusao_e_retencao(self):
        f = Fake(
            people=[
                person(
                    "p-opt", "opt@corp.com",
                    heatingNota=50, heatingFaixa="QUENTE", heatingSinais="x", heatingCalculadoEm=NOW_ISO,
                ),
                person("p-ex", "gui@corp.com"),
            ],
            schema=True,
        )
        f.enrolls["e-opt"] = {
            "id": "e-opt",
            "inscricaoId": H.inscricao_id(SLUG, "opt@corp.com"),
            "curso": "CHATGPT_NO_DIA_A_DIA",
            "status": "ATIVO",
            "entrada": ago(days=10),
            "aulasConcluidas": 2,
            "ultimaAula": ago(days=5),
            "nota": 20,
            "faixa": "MORNO",
            "sinais": "Cadastro no curso (10,0)",
            "calculadoEm": NOW_ISO,
            "pessoaId": "p-opt",
        }
        # Oposição registrada no D1.
        f.add_optout("opt@corp.com")
        f.add_event("ev-opt-site", "opt@corp.com", "material_baixado", context="site", source="conversoes", ref="m1", when=ago(days=1))
        f.add_event("ev-opt-cur", "opt@corp.com", "curso_cadastro", context=CTX_CURSO, source="curso", ref=None, when=ago(days=1))
        # Excluídos: HEATING_EXCLUDE + domínios de teste (eventos ficam, mas não recebem nota).
        for i, em in enumerate(
            ("gui@corp.com", "a@example.com", "b@example.org", "c@example.net", "d@exemplo.com",
             "e@foo.test", "f@foo.invalid", "g@sub.example"),
            start=1,
        ):
            f.add_event(f"ev-ex-{i}", em, "site_agende", context="site", source="ga4", ref=None, when=ago(days=1))
        # Retenção: evento com 366 dias é apagado; evento com 364 dias fica.
        f.add_event("ev-velho", "ok@corp.com", "site_visita", context="site", source="ga4", ref=None, when=ago(days=366))
        f.add_event("ev-recente", "ok@corp.com", "material_baixado", context="site", source="conversoes", ref="m1", when=ago(days=1))

        curso_docs = [{
            "email": "opt@corp.com",
            "status": "active",
            "googleUid": "uid_opt",
            "name": "Opt User",
            "createdAt": ago(days=10),
            "aulas": [{"dayId": "d1", "completed": True, "updatedAt": ago(days=5)}],
        }]
        counts, _ = run_job(f, apply=True, ler_curso=lambda s: curso_docs, exclude_emails={"gui@corp.com"})
        self.assertEqual(counts["oposicao_aplicada"], 1)
        self.assertEqual(counts["excluidos"], 8)
        self.assertEqual(counts["retencao_apagados"], 1)
        self.assertEqual(counts["pessoas_limpas"], 1)
        self.assertEqual(counts["notas_site"], 1)  # só ok@corp.com

        # Eventos de optout e de retenção sumiram; eventos de excluídos ficaram.
        ev_ids = set(f.event_ids())
        self.assertNotIn("ev-opt-site", ev_ids)
        self.assertNotIn("ev-opt-cur", ev_ids)
        self.assertNotIn("ev-velho", ev_ids)
        self.assertIn("ev-ex-1", ev_ids)
        self.assertIn("ev-recente", ev_ids)

        # Person do optout teve os 4 campos heating* limpos; Inscrição teve nota/faixa/sinais limpos mantendo o resto.
        for k in H.PERSON_HEATING_FIELDS:
            self.assertIsNone(f.people["p-opt"][k])
        eopt = f.enrolls["e-opt"]
        self.assertEqual((eopt["nota"], eopt["faixa"], eopt["sinais"], eopt["calculadoEm"]), (None, None, None, None))
        self.assertEqual((eopt["status"], eopt["aulasConcluidas"]), ("ATIVO", 1))

    def test_curso_indisponivel_mantem_eventos_e_nao_mexe_nas_inscricoes(self):
        f = Fake(people=[person("p1", "ana@corp.com")], schema=True)
        f.add_event("cu-prev", "ana@corp.com", "curso_cadastro", context=CTX_CURSO, source="curso", ref=None, when=ago(days=0))
        f.enrolls["e1"] = {
            "id": "e1",
            "inscricaoId": H.inscricao_id(SLUG, "ana@corp.com"),
            "curso": "CHATGPT_NO_DIA_A_DIA",
            "status": "ATIVO",
            "entrada": ago(days=10),
            "aulasConcluidas": 3,
            "ultimaAula": ago(days=5),
            "nota": 99,
            "faixa": "QUENTE",
            "sinais": "antigo",
            "calculadoEm": ago(days=5),
            "pessoaId": "p1",
        }
        before_enroll = copy.deepcopy(f.enrolls["e1"])

        def fail_curso(_slug):
            raise H.CursoIndisponivel("sem acesso ao Firestore")

        counts, out = run_job(f, apply=True, ler_curso=fail_curso)
        self.assertEqual(counts["curso_indisponivel"], 1)
        self.assertIn("curso_indisponivel: 1", out)
        # Evento já gravado ainda gera nota de curso no D1, mas a Inscrição no Twenty não é tocada.
        self.assertEqual(f.heating_rows()[(CTX_CURSO, "ana@corp.com")]["score"], 10)
        self.assertEqual(f.enrolls["e1"], before_enroll)
        self.assertEqual([c for c in f.twenty_writes() if "courseEnrollments" in c[1]], [])

        # Sem ler_curso injetado e sem HEATING_GCP_SA_FILE: também conta curso_indisponivel sem quebrar.
        out2 = io.StringIO()
        counts2 = H.run(TS.Clients(ENV, f, sleep=lambda s: None), apply=False, out=out2, now_dt=NOW, forget_key=FORGET_KEY)
        self.assertEqual(counts2["curso_indisponivel"], 1)

        # Erros que não são de leitura do curso (ex.: RuntimeError de programação) não são engolidos.
        with self.assertRaises(RuntimeError):
            run_job(f, apply=False, ler_curso=lambda _s: (_ for _ in ()).throw(RuntimeError("bug")))

    def test_lixeira_nao_recria_pessoa_nem_inscricao(self):
        f = Fake(
            people=[
                person("p-lixo", "lixo@corp.com", deletedAt="2026-11-20T00:00:00.000Z"),
                person("p-ok", "ok@corp.com"),
            ],
            schema=True,
        )
        f.enrolls["e-lixo"] = {
            "id": "e-lixo",
            "inscricaoId": H.inscricao_id(SLUG, "ok@corp.com"),
            "curso": "CHATGPT_NO_DIA_A_DIA",
            "status": "ATIVO",
            "deletedAt": "2026-11-20T00:00:00.000Z",
            "pessoaId": "p-ok",
        }
        f.add_event("ev-lixo", "lixo@corp.com", "material_baixado", context="site", source="conversoes", ref="m1", when=ago(days=0))
        docs = [
            {"email": "lixo@corp.com", "status": "active", "googleUid": "u1", "name": "Lixo", "createdAt": ago(days=0), "aulas": []},
            {"email": "ok@corp.com", "status": "active", "googleUid": "u2", "name": "Ok", "createdAt": ago(days=0), "aulas": []},
        ]
        counts, _ = run_job(f, apply=True, ler_curso=lambda _s: docs)
        self.assertEqual(counts["pessoa_na_lixeira"], 1)
        self.assertEqual(counts["pessoas_criadas"], 0)
        self.assertEqual(counts["inscricoes_criadas"], 0)
        self.assertEqual(counts["inscricoes_atualizadas"], 0)
        self.assertEqual([c for c in f.twenty_writes() if c[1] == "/rest/courseEnrollments/e-lixo"], [])
        self.assertEqual(len(f.people), 2)
        self.assertEqual(len(f.enrolls), 1)
        self.assertIsNone(f.heating_rows()[("site", "lixo@corp.com")]["twenty_person_id"])

    def test_descadastro_site_e_curso_no_job(self):
        f = Fake(people=[person("p1", "u@corp.com")], schema=True)
        f.add_subscriber("s1", "u@corp.com", unsubscribed=1)
        f.add_event("e-site-clk", "u@corp.com", "email_clicado", context="site", ref="m1", when=ago(days=0))
        f.add_event("e-cur-clk", "u@corp.com", "email_clicado", context=CTX_CURSO, ref="m2", when=ago(days=0))
        docs = [{"email": "u@corp.com", "status": "unsubscribed", "googleUid": "u1", "name": "U", "createdAt": ago(days=0), "aulas": []}]
        run_job(f, apply=True, ler_curso=lambda _s: docs, email_desde=ago(days=30))
        hrows = f.heating_rows()
        # No site: só tinha clique, e está descadastrado dos Expert Insights -> sem nota no site.
        self.assertNotIn(("site", "u@corp.com"), hrows)
        # No curso: perdeu o clique (3 pts), ficou só com curso_cadastro (10 pts).
        self.assertEqual(hrows[(CTX_CURSO, "u@corp.com")]["score"], 10)

    def test_optout_cli_e_simulacao(self):
        f = Fake(
            people=[person(
                "p1", "segredo@corp.com",
                heatingNota=45, heatingFaixa="QUENTE", heatingSinais="x", heatingCalculadoEm=NOW_ISO,
            )],
            schema=True,
        )
        f.enrolls["e1"] = {
            "id": "e1",
            "inscricaoId": H.inscricao_id(SLUG, "segredo@corp.com"),
            "curso": "CHATGPT_NO_DIA_A_DIA",
            "status": "ATIVO",
            "entrada": ago(days=5),
            "aulasConcluidas": 2,
            "ultimaAula": ago(days=1),
            "nota": 20,
            "faixa": "MORNO",
            "sinais": "Cadastro no curso (10,0)",
            "calculadoEm": NOW_ISO,
            "pessoaId": "p1",
        }
        f.add_event("e-s", "segredo@corp.com", "site_agende", context="site", source="ga4", ref=None, when=ago(days=1))
        f.add_event("e-c", "segredo@corp.com", "curso_cadastro", context=CTX_CURSO, source="curso", ref=None, when=ago(days=1))
        f.db.execute(
            "INSERT INTO heating (context, email, score, band, signals, computed_at, twenty_person_id) VALUES "
            "('site', 'segredo@corp.com', 45, 'quente', '[]', ?, 'p1'),"
            "(?, 'segredo@corp.com', 20, 'morno', '[]', ?, 'p1')",
            (NOW_ISO, CTX_CURSO, NOW_ISO),
        )
        f.db.commit()

        # Simulação não grava nada.
        out_dry = io.StringIO()
        c_dry = H.optout(TS.Clients(ENV, f, sleep=lambda s: None), "Segredo@Corp.com\n", apply=False, out=out_dry, forget_key=FORGET_KEY)
        self.assertEqual((c_dry["eventos_apagados"], c_dry["notas_apagadas"], c_dry["pessoas_limpas"], c_dry["inscricoes_limpas"]), (2, 2, 1, 1))
        self.assertNotIn("segredo", out_dry.getvalue().lower())
        self.assertEqual(f.db.execute("SELECT count(*) FROM heating_optout").fetchone()[0], 0)

        # --apply grava marca, apaga todos os contextos no D1 e limpa Person + nota/faixa/sinais da Inscrição.
        out = io.StringIO()
        H.optout(TS.Clients(ENV, f, sleep=lambda s: None), "Segredo@Corp.com", apply=True, out=out, forget_key=FORGET_KEY)
        self.assertIn("oposição registrada", out.getvalue())
        self.assertNotIn("segredo", out.getvalue().lower())
        self.assertEqual(f.db.execute("SELECT count(*) FROM heating_optout").fetchone()[0], 1)
        self.assertEqual(f.event_ids(), [])
        self.assertEqual(f.heating_rows(), {})
        for k in H.PERSON_HEATING_FIELDS:
            self.assertIsNone(f.people["p1"][k])
        self.assertEqual((f.enrolls["e1"]["nota"], f.enrolls["e1"]["faixa"], f.enrolls["e1"]["sinais"], f.enrolls["e1"]["calculadoEm"]),
                         (None, None, None, None))
        self.assertEqual(f.enrolls["e1"]["aulasConcluidas"], 2)  # resto da Inscrição mantido

    def test_optout_limpa_pessoa_com_email_alterado_via_heating_e_conversions(self):
        # P2 / Claude P4: --optout resolve Pessoas ativas também pelos twenty_person_id salvos em `heating`
        # (todos os contextos) e em `conversions` antes de apagar o D1, ignorando Pessoas na lixeira e ids nulos.
        f = Fake(
            people=[
                person("p-site", "novo-site@corp.com", heatingNota=45, heatingFaixa="QUENTE", heatingSinais="x", heatingCalculadoEm=NOW_ISO),
                person("p-curso", "novo-curso@corp.com", heatingNota=30, heatingFaixa="MORNO", heatingSinais="y", heatingCalculadoEm=NOW_ISO),
                person("p-conv", "novo-conv@corp.com", heatingNota=12, heatingFaixa="FRIO", heatingSinais="z", heatingCalculadoEm=NOW_ISO),
                person(
                    "p-lixo", "novo-lixo@corp.com",
                    deletedAt="2026-11-20T00:00:00.000Z",
                    heatingNota=50, heatingFaixa="QUENTE", heatingSinais="w", heatingCalculadoEm=NOW_ISO,
                ),
            ],
            schema=True,
        )
        f.db.execute(
            "INSERT INTO heating (context, email, score, band, signals, computed_at, twenty_person_id) VALUES "
            "('site', 'antigo@corp.com', 45, 'quente', '[]', ?, 'p-site'),"
            "(?, 'antigo@corp.com', 20, 'morno', '[]', ?, 'p-curso')",
            (NOW_ISO, CTX_CURSO, NOW_ISO),
        )
        f.add_conv(1, "antigo@corp.com", action="material_download", material="planilha-custos-ia", when=ago(days=1))
        f.add_conv(2, "antigo@corp.com", action="newsletter_confirm", when=ago(days=1))
        f.add_conv(3, "antigo@corp.com", action="material_request", material="planilha-custos-ia", when=ago(days=1))
        f.db.execute("UPDATE conversions SET twenty_person_id = 'p-conv' WHERE id = ?", (hexid(1),))
        f.db.execute("UPDATE conversions SET twenty_person_id = 'p-lixo' WHERE id = ?", (hexid(2),))
        f.db.execute("UPDATE conversions SET twenty_person_id = NULL WHERE id = ?", (hexid(3),))
        f.db.commit()

        # Simulação conta as 3 Pessoas ativas (site, curso e conversions) e ignora a da lixeira, sem gravar nada.
        out_dry = io.StringIO()
        c_dry = H.optout(TS.Clients(ENV, f, sleep=lambda s: None), "antigo@corp.com", apply=False, out=out_dry, forget_key=FORGET_KEY)
        self.assertEqual((c_dry["notas_apagadas"], c_dry["pessoas_limpas"]), (2, 3))
        self.assertEqual(f.twenty_writes(), [])
        self.assertNotIn("antigo", out_dry.getvalue().lower())

        # --apply limpa as 3 Pessoas ativas (mesmo com email alterado no Twenty), não toca na lixeira e apaga D1.
        out = io.StringIO()
        c_app = H.optout(TS.Clients(ENV, f, sleep=lambda s: None), "antigo@corp.com", apply=True, out=out, forget_key=FORGET_KEY)
        self.assertEqual((c_app["notas_apagadas"], c_app["pessoas_limpas"]), (2, 3))
        self.assertNotIn("antigo", out.getvalue().lower())
        for pid in ("p-site", "p-curso", "p-conv"):
            for k in H.PERSON_HEATING_FIELDS:
                self.assertIsNone(f.people[pid][k], (pid, k))
        self.assertEqual(f.people["p-lixo"]["heatingNota"], 50)
        patched = {p for m, p, _, _ in f.calls if m == "PATCH"}
        self.assertEqual(patched, {"/rest/people/p-site", "/rest/people/p-curso", "/rest/people/p-conv"})
        self.assertEqual(f.heating_rows(), {})
        self.assertEqual(f.db.execute("SELECT count(*) FROM heating_optout").fetchone()[0], 1)

    def test_forget_curso_impede_rematerializacao_e_preserva_site(self):
        f = Fake(people=[person("p1", "aluna@corp.com")], schema=True)
        f.add_conv(1, "aluna@corp.com", action="material_download", material="planilha-custos-ia", when=ago(days=0))
        # Evento de site vindo do webhook (não regenerado a partir de conversions) e evento de outro curso: devem sobreviver.
        f.add_event("wh-site-aluna", "aluna@corp.com", "email_clicado", context="site", source="resend", ref="em-1", when=ago(days=0))
        f.add_event("cu-outro-aluna", "aluna@corp.com", "curso_cadastro", context="curso:outro-curso", source="curso", ref=None, when=ago(days=0))
        docs = [{
            "email": "aluna@corp.com",
            "status": "active",
            "googleUid": "uid_aluna",
            "name": "Aluna",
            "createdAt": ago(days=0),
            "aulas": [{"dayId": "d1", "completed": True, "updatedAt": ago(days=0)}],
        }]
        run_job(f, apply=True, ler_curso=lambda _s: docs)
        self.assertEqual(len(f.enrolls), 1)
        self.assertIn(("site", "aluna@corp.com"), f.heating_rows())
        self.assertIn((CTX_CURSO, "aluna@corp.com"), f.heating_rows())

        # Coloca a Inscrição na lixeira para garantir que --forget-curso também apaga da lixeira de vez.
        only_enr = next(iter(f.enrolls.values()))
        only_enr["deletedAt"] = NOW_ISO

        # Simulação não apaga.
        out_dry = io.StringIO()
        c_dry = H.forget_curso(TS.Clients(ENV, f, sleep=lambda s: None), SLUG, "aluna@corp.com", apply=False, out=out_dry, forget_key=FORGET_KEY)
        self.assertEqual((c_dry["inscricoes_no_crm"], c_dry["notas_apagadas"]), (1, 1))
        self.assertIn("Firestore e no Identity Platform", out_dry.getvalue())
        self.assertNotIn("aluna", out_dry.getvalue().lower())

        # --apply grava curso_esquecido, apaga Inscrição de vez, apaga eventos/nota do curso, preserva site e Pessoa.
        out = io.StringIO()
        H.forget_curso(TS.Clients(ENV, f, sleep=lambda s: None), SLUG, "aluna@corp.com", apply=True, out=out, forget_key=FORGET_KEY)
        self.assertIn("curso removido", out.getvalue())
        self.assertIn("Firestore e no Identity Platform", out.getvalue())
        self.assertNotIn("aluna", out.getvalue().lower())
        self.assertEqual(f.enrolls, {})
        self.assertIn("p1", f.people)  # Pessoa preservada
        self.assertIn(("site", "aluna@corp.com"), f.heating_rows())
        self.assertNotIn((CTX_CURSO, "aluna@corp.com"), f.heating_rows())
        self.assertEqual(f.db.execute("SELECT count(*) FROM curso_esquecido").fetchone()[0], 1)
        # Eventos de outros contextos (site do webhook e outro curso) continuam em engagement_events; os do curso saíram.
        rem_events = {r["id"]: r["context"] for r in f.db.execute("SELECT id, context FROM engagement_events")}
        self.assertIn("wh-site-aluna", rem_events)
        self.assertIn("cu-outro-aluna", rem_events)
        self.assertNotIn(CTX_CURSO, rem_events.values())

        # Próxima rodada do job com os mesmos dados ainda no Firestore NÃO rematerializa o curso esquecido
        # (a única nota de curso que permanece é a de `curso:outro-curso`).
        counts_next, _ = run_job(f, apply=True, ler_curso=lambda _s: docs)
        self.assertEqual((counts_next["eventos_curso_novos"], counts_next["inscricoes_criadas"], counts_next["notas_curso"]), (0, 0, 1))
        self.assertEqual(f.enrolls, {})
        self.assertNotIn((CTX_CURSO, "aluna@corp.com"), f.heating_rows())
        self.assertIn(("curso:outro-curso", "aluna@corp.com"), f.heating_rows())
        self.assertIn(("site", "aluna@corp.com"), f.heating_rows())

    def test_forget_curso_com_sobra_no_twenty_mantem_d1_intacto(self):
        f = Fake(people=[person("p1", "aluna@corp.com")], schema=True)
        f.enrolls["e1"] = {"id": "e1", "inscricaoId": H.inscricao_id(SLUG, "aluna@corp.com"), "pessoaId": "p1"}
        f.add_event("cu-1", "aluna@corp.com", "curso_cadastro", context=CTX_CURSO, source="curso", ref=None, when=ago(days=1))
        real = f._twenty
        f._twenty = lambda m, u, d: (200, {"data": {}}) if m == "DELETE" else real(m, u, d)
        with self.assertRaisesRegex(H.SyncError, "o D1 ficou intacto"):
            H.forget_curso(TS.Clients(ENV, f, sleep=lambda s: None), SLUG, "aluna@corp.com", apply=True, forget_key=FORGET_KEY)
        self.assertEqual(f.event_ids(), ["cu-1"])
        self.assertEqual(f.db.execute("SELECT count(*) FROM curso_esquecido").fetchone()[0], 1)

    def test_oposicao_concorrente_durante_a_rodada_condicional_e_releitura(self):
        # Codex P1: (A) INSERT condicional em engagement_events e heating não grava quem recebeu marca no meio;
        # (B) releitura de heating_optout no fim da rodada limpa D1 e Twenty (Pessoa e Inscrições, existentes e criadas).
        class ConcurrentOptoutFake(Fake):
            def __init__(self, *args, **kwargs):
                self.injected_ev = False
                self.injected_ht = False
                self.injected_late = False
                self.ev_after_insert = None
                self.ht_after_batch = None
                super().__init__(*args, **kwargs)

            def __call__(self, method, url, headers, body):
                data = json.loads(body) if body else None
                u = urllib.parse.urlparse(url)
                if u.netloc == "api.cloudflare.com" and isinstance(data, dict):
                    sql = data.get("sql", "")
                    if not self.injected_ev and sql.startswith("INSERT INTO engagement_events"):
                        self.injected_ev = True
                        # Simula POST /prioridade gravando heating_optout logo antes do INSERT de eventos.
                        self.add_optout("corr-ev@corp.com")
                        res = super().__call__(method, url, headers, body)
                        self.ev_after_insert = [
                            r[0] for r in self.db.execute("SELECT email FROM engagement_events WHERE email = 'corr-ev@corp.com'")
                        ]
                        return res
                    if not self.injected_ht and "batch" in data and data["batch"][0]["sql"] == "DELETE FROM heating":
                        self.injected_ht = True
                        # Simula POST /prioridade gravando heating_optout logo antes do batch de heating.
                        self.add_optout("corr-ht@corp.com")
                        res = super().__call__(method, url, headers, body)
                        self.ht_after_batch = [
                            r[0] for r in self.db.execute("SELECT email FROM heating WHERE email = 'corr-ht@corp.com'")
                        ]
                        # E simula outro email (com Pessoa + Inscrição criadas nesta rodada) fazendo oposição
                        # imediatamente após a troca de heating, antes da releitura final.
                        self.add_optout("late-new@corp.com")
                        self.injected_late = True
                        return res
                return super().__call__(method, url, headers, body)

        f = ConcurrentOptoutFake(
            people=[
                person("p-ev", "corr-ev@corp.com"),
                person("p-ht", "corr-ht@corp.com"),
                person("p-ok", "ok@corp.com"),
            ],
            schema=True,
        )
        f.enrolls["e-ev"] = {
            "id": "e-ev",
            "inscricaoId": H.inscricao_id(SLUG, "corr-ev@corp.com"),
            "curso": "CHATGPT_NO_DIA_A_DIA",
            "status": "ATIVO",
            "entrada": ago(days=2),
            "aulasConcluidas": 0,
            "ultimaAula": None,
            "nota": None,
            "faixa": None,
            "sinais": None,
            "calculadoEm": None,
            "pessoaId": "p-ev",
        }
        f.add_conv(1, "corr-ev@corp.com", action="material_download", material="planilha-custos-ia", when=ago(days=0))
        f.add_conv(2, "corr-ht@corp.com", action="material_download", material="planilha-custos-ia", when=ago(days=0))
        f.add_conv(3, "late-new@corp.com", action="material_download", material="planilha-custos-ia", when=ago(days=0))
        f.add_conv(4, "ok@corp.com", action="material_download", material="planilha-custos-ia", when=ago(days=0))
        docs = [
            {"email": "corr-ev@corp.com", "status": "active", "googleUid": "u1", "name": "Ev", "createdAt": ago(days=2), "aulas": []},
            {"email": "corr-ht@corp.com", "status": "active", "googleUid": "u2", "name": "Ht", "createdAt": ago(days=2), "aulas": []},
            {"email": "late-new@corp.com", "status": "active", "googleUid": "u3", "name": "Late New", "createdAt": ago(days=0), "aulas": []},
        ]

        counts, _ = run_job(f, apply=True, ler_curso=lambda _s: docs)
        self.assertTrue(f.injected_ev and f.injected_ht and f.injected_late)
        # (A) O próprio SQL condicional impediu a inserção em engagement_events e heating antes mesmo da releitura!
        self.assertEqual(f.ev_after_insert, [])
        self.assertEqual(f.ht_after_batch, [])
        # (B) Ao final da rodada, D1 e Twenty (Pessoa e Inscrições) dos 3 emails estão limpos; ok@corp.com intacto.
        self.assertEqual(counts["oposicao_aplicada"], 3)
        for em in ("corr-ev@corp.com", "corr-ht@corp.com", "late-new@corp.com"):
            self.assertEqual(f.db.execute("SELECT count(*) FROM engagement_events WHERE email = ?", (em,)).fetchone()[0], 0, em)
            self.assertEqual(f.db.execute("SELECT count(*) FROM heating WHERE email = ?", (em,)).fetchone()[0], 0, em)
        for pid in ("p-ev", "p-ht"):
            for k in H.PERSON_HEATING_FIELDS:
                self.assertIsNone(f.people[pid][k], (pid, k))
        p_late = next(p for p in f.people.values() if p["emails"]["primaryEmail"] == "late-new@corp.com")
        for k in H.PERSON_HEATING_FIELDS:
            self.assertIsNone(p_late[k], k)
        for enr in f.enrolls.values():
            if enr["pessoaId"] in ("p-ev", "p-ht", p_late["id"]):
                for k in ("nota", "faixa", "sinais", "calculadoEm"):
                    self.assertIsNone(enr[k], (enr["id"], k))
        # Contato sem oposição preservado.
        self.assertEqual(f.people["p-ok"]["heatingNota"], 12)
        self.assertEqual(f.heating_rows()[("site", "ok@corp.com")]["score"], 12)

    def test_d1_batch_valida_resposta_e_erros_por_item(self):
        # agy P1: d1_batch rejeita data não-dict, success falso no envelope, result não-lista,
        # tamanho divergente das instruções, item não-dict, item com success=False e results inválido.
        stmts = [("SELECT 1", None), ("SELECT 2", None), ("SELECT 3", None)]
        casos = [
            (200, []),
            (200, None),
            (200, {"success": False, "result": [], "errors": [{"message": "segredo@corp.com"}]}),
            (200, {"success": True, "result": "nao-lista"}),
            (200, {"success": True, "result": [{"results": [], "success": True}, {"results": [], "success": True}]}),
            (200, {"success": True, "result": [{"results": [], "success": True}, {"results": [], "success": False, "error": "segredo@corp.com"}, {"results": [], "success": True}]}),
            (200, {"success": True, "result": [{"results": [], "success": True}, "ruim", {"results": [], "success": True}]}),
            (200, {"success": True, "result": [{"results": [], "success": True}, {"results": "ruim", "success": True}, {"results": [], "success": True}]}),
        ]
        for st, payload in casos:
            c = TS.Clients(ENV, lambda _m, _u, _h, _b, _s=st, _p=payload: (_s, _p), sleep=lambda s: None)
            with self.assertRaises(H.SyncError) as ctx:
                H.d1_batch(c, stmts)
            self.assertNotIn("segredo", str(ctx.exception).lower())

        # Caso válido: 3 itens com success=True.
        ok_payload = {"success": True, "result": [{"results": [{"a": 1}], "success": True}, {"results": None, "success": True}, {"results": [], "success": True}]}
        c_ok = TS.Clients(ENV, lambda _m, _u, _h, _b: (200, ok_payload), sleep=lambda s: None)
        self.assertEqual(H.d1_batch(c_ok, stmts), [[{"a": 1}], [], []])

    def test_inscricao_orfa_e_email_trocado_religa_a_pessoa_criada(self):
        # agy P1: Inscrição existente sem Pessoa ativa (pessoaId=None ou de Pessoa cujo email mudou) quando a Pessoa
        # é criada nesta rodada liga a Inscrição ao novo id retornado e nunca envia pessoaId="".
        f = Fake(people=[person("p-trocada", "outro@corp.com")], schema=True)
        f.enrolls["e-orphan"] = {
            "id": "e-orphan",
            "inscricaoId": H.inscricao_id(SLUG, "orfa@corp.com"),
            "curso": "CHATGPT_NO_DIA_A_DIA",
            "status": "ATIVO",
            "entrada": ago(days=5),
            "aulasConcluidas": 0,
            "ultimaAula": None,
            "nota": 9,
            "faixa": "FRIO",
            "sinais": "Cadastro no curso",
            "calculadoEm": ago(days=5),
            "pessoaId": None,
        }
        f.enrolls["e-moved"] = {
            "id": "e-moved",
            "inscricaoId": H.inscricao_id(SLUG, "mudou@corp.com"),
            "curso": "CHATGPT_NO_DIA_A_DIA",
            "status": "ATIVO",
            "entrada": ago(days=5),
            "aulasConcluidas": 0,
            "ultimaAula": None,
            "nota": 9,
            "faixa": "FRIO",
            "sinais": "Cadastro no curso",
            "calculadoEm": ago(days=5),
            "pessoaId": "p-trocada",
        }
        docs = [
            {"email": "orfa@corp.com", "status": "active", "googleUid": "u-o", "name": "Orfa", "createdAt": ago(days=5), "aulas": []},
            {"email": "mudou@corp.com", "status": "active", "googleUid": "u-m", "name": "Mudou", "createdAt": ago(days=5), "aulas": []},
        ]
        # Simulação conta 2 criações de Pessoa e 2 atualizações de Inscrição sem escrever nada.
        c_dry, _ = run_job(f, apply=False, ler_curso=lambda _s: docs)
        self.assertEqual((c_dry["pessoas_criadas"], c_dry["inscricoes_atualizadas"]), (2, 2))
        self.assertEqual(f.twenty_writes(), [])

        # --apply cria as Pessoas e religa as duas Inscrições aos novos ids (nunca pessoaId="").
        c_app, _ = run_job(f, apply=True, ler_curso=lambda _s: docs)
        self.assertEqual((c_app["pessoas_criadas"], c_app["inscricoes_atualizadas"]), (2, 2))
        p_orfa = next(p for p in f.people.values() if p["emails"]["primaryEmail"] == "orfa@corp.com")
        p_mudou = next(p for p in f.people.values() if p["emails"]["primaryEmail"] == "mudou@corp.com")
        self.assertEqual(f.enrolls["e-orphan"]["pessoaId"], p_orfa["id"])
        self.assertEqual(f.enrolls["e-moved"]["pessoaId"], p_mudou["id"])
        for m, path, data, _ in f.calls:
            if m == "PATCH" and path.startswith("/rest/courseEnrollments/"):
                self.assertNotEqual(data.get("pessoaId"), "")

    def test_patch_diario_sinais_sem_pontos_e_sem_patch_no_dia_seguinte(self):
        # Claude P2: mesmo evento em dois dias seguidos com mesma nota -> nenhum PATCH no Twenty no 2º dia,
        # heatingSinais/sinais têm só rótulos sem pontos (pontos ficam no D1), e calculadoEm só muda quando
        # nota/faixa/rótulos mudam.
        day1 = datetime(2026, 11, 27, 12, 0, 0, tzinfo=timezone.utc)
        day2 = datetime(2026, 11, 28, 12, 0, 0, tzinfo=timezone.utc)
        ev_when = "2026-11-01T12:00:00.000Z"
        f = Fake(people=[person("p1", "ana@corp.com")], schema=True)
        f.add_conv(1, "ana@corp.com", action="material_download", material="planilha-custos-ia", when=ev_when)
        f.add_conv(2, "ana@corp.com", action="newsletter_confirm", when=ev_when)
        docs = [{
            "email": "ana@corp.com",
            "status": "active",
            "googleUid": "uid_ana",
            "name": "Ana",
            "createdAt": ev_when,
            "aulas": [{"dayId": "d1", "completed": True, "updatedAt": ev_when}],
        }]

        # Dia 1: nota site = 12 (frio), nota curso = 8 (frio).
        c1, _ = run_job(f, apply=True, now_dt=day1, ler_curso=lambda _s: docs)
        self.assertEqual((c1["pessoas_atualizadas"], c1["inscricoes_criadas"]), (1, 1))
        self.assertEqual(f.people["p1"]["heatingNota"], 12)
        self.assertEqual(f.people["p1"]["heatingSinais"], "Download de material; Inscrição na newsletter")
        self.assertEqual(f.people["p1"]["heatingCalculadoEm"], CS.stamp(day1))
        enr = next(iter(f.enrolls.values()))
        self.assertEqual((enr["nota"], enr["sinais"], enr["calculadoEm"]), (8, "Cadastro no curso; Aula concluída no curso", CS.stamp(day1)))
        d1_sig_day1 = f.heating_rows()[("site", "ana@corp.com")]["signals"]

        # Dia 2: pontos decaídos mudam no D1, mas nota (12 e 8), faixa e rótulos são iguais -> ZERO PATCH no Twenty!
        n_tw_before = len(f.twenty_writes())
        c2, _ = run_job(f, apply=True, now_dt=day2, ler_curso=lambda _s: docs)
        self.assertEqual((c2["pessoas_atualizadas"], c2["inscricoes_atualizadas"]), (0, 0))
        self.assertEqual(len(f.twenty_writes()), n_tw_before)
        self.assertEqual(f.people["p1"]["heatingCalculadoEm"], CS.stamp(day1))
        self.assertEqual(enr["calculadoEm"], CS.stamp(day1))
        # No D1, heating.computed_at e os pontos decaídos em heating.signals foram atualizados.
        self.assertEqual(f.heating_rows()[("site", "ana@corp.com")]["computed_at"], CS.stamp(day2))
        self.assertNotEqual(f.heating_rows()[("site", "ana@corp.com")]["signals"], d1_sig_day1)

        # Dia 2 com mudança só de status no curso (active -> completed), mesma nota/faixa/sinais:
        # PATCH altera só status e NÃO mexe em calculadoEm ("Nota mudou em").
        docs_comp = [{**docs[0], "status": "completed"}]
        c3, _ = run_job(f, apply=True, now_dt=day2, ler_curso=lambda _s: docs_comp)
        self.assertEqual(c3["inscricoes_atualizadas"], 1)
        last_patch = [d for m, p, d, _ in f.calls if m == "PATCH" and p.startswith("/rest/courseEnrollments/")][-1]
        self.assertEqual(last_patch, {"status": "CONCLUIDO"})
        self.assertEqual(enr["calculadoEm"], CS.stamp(day1))

        # Mutante P3: se heatingCalculadoEm estiver ausente (None ou "") com nota, faixa e sinais iguais,
        # _person_heating_patch e o job preenchem heatingCalculadoEm.
        for missing_calc in (None, ""):
            p_sem_calc = person(
                "p-calc", "ana@corp.com",
                heatingNota=12,
                heatingFaixa="FRIO",
                heatingSinais="Download de material; Inscrição na newsletter",
                heatingCalculadoEm=missing_calc,
            )
            want_ana = H.calcular_heating_contexto(
                "site",
                [
                    {"id": "1", "context": "site", "email": "ana@corp.com", "signal": "material_baixado", "ref": "planilha-custos-ia", "occurred_at": ev_when},
                    {"id": "2", "context": "site", "email": "ana@corp.com", "signal": "newsletter_confirmada", "ref": None, "occurred_at": ev_when},
                ],
                day1,
            )
            patch_calc = H._person_heating_patch(p_sem_calc, want_ana, CS.stamp(day1))
            self.assertEqual(patch_calc["heatingCalculadoEm"], CS.stamp(day1))

        f.people["p1"]["heatingCalculadoEm"] = None
        c4, _ = run_job(f, apply=True, now_dt=day2, ler_curso=lambda _s: docs_comp)
        self.assertEqual(c4["pessoas_atualizadas"], 1)
        self.assertEqual(f.people["p1"]["heatingCalculadoEm"], CS.stamp(day2))
        c5, _ = run_job(f, apply=True, now_dt=day2, ler_curso=lambda _s: docs_comp)
        self.assertEqual(c5["pessoas_atualizadas"], 0)

    def test_curso_status_desconhecido_bloqueia_emails_conservadoramente(self):
        # Claude P3 + Codex P2: descadastrado com cadastro (10) + clique (3) tem nota 10 com Firestore disponível,
        # e continua com nota 10 (NÃO vira 13) quando o Firestore está indisponível ou quando o inscrito sumiu.
        f = Fake(people=[person("p1", "u@corp.com"), person("p2", "ativo@corp.com")], schema=True)
        f.add_event("e-clk-u", "u@corp.com", "email_clicado", context=CTX_CURSO, ref="m1", when=ago(days=0))
        f.add_event("e-clk-a", "ativo@corp.com", "email_clicado", context=CTX_CURSO, ref="m2", when=ago(days=0))
        docs = [
            {"email": "u@corp.com", "status": "unsubscribed", "googleUid": "u1", "name": "U", "createdAt": ago(days=0), "aulas": []},
            {"email": "ativo@corp.com", "status": "active", "googleUid": "u2", "name": "A", "createdAt": ago(days=0), "aulas": []},
        ]
        # 1) Firestore disponível: u@ fica em 10 (bloqueado); ativo@ fica em 13 (10 + 3).
        run_job(f, apply=True, ler_curso=lambda _s: docs, email_desde=ago(days=30))
        self.assertEqual(f.heating_rows()[(CTX_CURSO, "u@corp.com")]["score"], 10)
        self.assertEqual(f.heating_rows()[(CTX_CURSO, "ativo@corp.com")]["score"], 13)

        # 2) Firestore indisponível: u@ continua 10 (não vira 13!), e ativo@ cai conservadoramente para 10.
        def fail_curso(_slug):
            raise H.CursoIndisponivel("falha Firestore")
        run_job(f, apply=True, ler_curso=fail_curso, email_desde=ago(days=30))
        self.assertEqual(f.heating_rows()[(CTX_CURSO, "u@corp.com")]["score"], 10)
        self.assertEqual(f.heating_rows()[(CTX_CURSO, "ativo@corp.com")]["score"], 10)

        # 3) Inscrito sumiu do Firestore (lista vazia): u@ continua 10 (não vira 13!).
        run_job(f, apply=True, ler_curso=lambda _s: [], email_desde=ago(days=30))
        self.assertEqual(f.heating_rows()[(CTX_CURSO, "u@corp.com")]["score"], 10)
        self.assertEqual(f.heating_rows()[(CTX_CURSO, "ativo@corp.com")]["score"], 10)

    def test_inscricao_fora_do_curso_limpa_nota_congelada_e_oposicao_limpa_com_curso_indisponivel(self):
        # agy P2: (1) Inscrição ativa cujo email sumiu do Firestore e não tem nota hoje tem nota/faixa/sinais/calculadoEm limpos;
        # (2) Oposição limpa a nota da Inscrição mesmo quando o Firestore do curso está indisponível.
        f = Fake(people=[person("p1", "saiu@corp.com")], schema=True)
        f.enrolls["e-saiu"] = {
            "id": "e-saiu",
            "inscricaoId": H.inscricao_id(SLUG, "saiu@corp.com"),
            "curso": "CHATGPT_NO_DIA_A_DIA",
            "status": "ATIVO",
            "entrada": ago(days=200),
            "aulasConcluidas": 2,
            "ultimaAula": ago(days=190),
            "nota": 25,
            "faixa": "MORNO",
            "sinais": "Cadastro no curso",
            "calculadoEm": ago(days=190),
            "pessoaId": "p1",
        }
        c1, _ = run_job(f, apply=True, ler_curso=lambda _s: [])
        self.assertEqual(c1["inscricoes_atualizadas"], 1)
        self.assertEqual(
            (f.enrolls["e-saiu"]["nota"], f.enrolls["e-saiu"]["faixa"], f.enrolls["e-saiu"]["sinais"], f.enrolls["e-saiu"]["calculadoEm"]),
            (None, None, None, None),
        )
        self.assertEqual((f.enrolls["e-saiu"]["status"], f.enrolls["e-saiu"]["aulasConcluidas"]), ("ATIVO", 2))

        # Curso indisponível: oposição ainda limpa a nota da Inscrição; quem não fez oposição fica intocado.
        g = Fake(people=[person("p-opt", "opt@corp.com"), person("p-ok", "ok@corp.com")], schema=True)
        g.add_optout("opt@corp.com")
        g.enrolls["e-opt"] = {
            "id": "e-opt",
            "inscricaoId": H.inscricao_id(SLUG, "opt@corp.com"),
            "curso": "CHATGPT_NO_DIA_A_DIA",
            "status": "ATIVO",
            "entrada": ago(days=5),
            "aulasConcluidas": 1,
            "ultimaAula": ago(days=5),
            "nota": 15,
            "faixa": "MORNO",
            "sinais": "Cadastro no curso",
            "calculadoEm": ago(days=5),
            "pessoaId": "p-opt",
        }
        g.enrolls["e-ok"] = {
            "id": "e-ok",
            "inscricaoId": H.inscricao_id(SLUG, "ok@corp.com"),
            "curso": "CHATGPT_NO_DIA_A_DIA",
            "status": "ATIVO",
            "entrada": ago(days=5),
            "aulasConcluidas": 1,
            "ultimaAula": ago(days=5),
            "nota": 30,
            "faixa": "MORNO",
            "sinais": "Cadastro no curso",
            "calculadoEm": ago(days=5),
            "pessoaId": "p-ok",
        }
        c2, _ = run_job(g, apply=True, ler_curso=lambda _s: (_ for _ in ()).throw(H.CursoIndisponivel("down")))
        self.assertEqual((c2["curso_indisponivel"], c2["oposicao_aplicada"], c2["inscricoes_atualizadas"]), (1, 1, 1))
        self.assertEqual(
            (g.enrolls["e-opt"]["nota"], g.enrolls["e-opt"]["faixa"], g.enrolls["e-opt"]["sinais"], g.enrolls["e-opt"]["calculadoEm"]),
            (None, None, None, None),
        )
        self.assertEqual(g.enrolls["e-ok"]["nota"], 30)

    def test_inscricao_ausente_do_curso_com_nota_no_d1_atualiza_apenas_campos_de_nota(self):
        # P2: Inscrição ativa no Twenty cujo email não veio na leitura atual do curso (curso disponível), mas que ainda
        # tem nota no D1 para o contexto (email_spam ou eventos dentro da janela de 180d), atualiza só nota, faixa,
        # sinais e calculadoEm (e só quando mudam ou falta calculadoEm), sem mexer em status/aulasConcluidas/pessoaId.
        f = Fake(
            people=[
                person("p-spam", "spam@corp.com"),
                person("p-decai", "decai@corp.com"),
                person("p-semcalc", "semcalc@corp.com"),
                person("p-vazia", "vazia-spam@corp.com"),
            ],
            schema=True,
        )
        # Caso do log: tinha 25/MORNO no Twenty, sumiu do Firestore e recebeu email_spam (janela de 365d).
        f.enrolls["e-spam"] = {
            "id": "e-spam",
            "inscricaoId": H.inscricao_id(SLUG, "spam@corp.com"),
            "curso": "CHATGPT_NO_DIA_A_DIA",
            "status": "ATIVO",
            "entrada": ago(days=40),
            "aulasConcluidas": 2,
            "ultimaAula": ago(days=20),
            "nota": 25,
            "faixa": "MORNO",
            "sinais": "Cadastro no curso",
            "calculadoEm": ago(days=10),
            "pessoaId": "p-spam",
        }
        f.add_event("ev-spam-cur", "spam@corp.com", "email_spam", context=CTX_CURSO, source="resend", ref="em-1", when=ago(days=200))

        # Decaimento: evento de cadastro há 30d -> nota 5 / FRIO / "Cadastro no curso"; sinais já era igual.
        f.enrolls["e-decai"] = {
            "id": "e-decai",
            "inscricaoId": H.inscricao_id(SLUG, "decai@corp.com"),
            "curso": "CHATGPT_NO_DIA_A_DIA",
            "status": "ATIVO",
            "entrada": ago(days=30),
            "aulasConcluidas": 1,
            "ultimaAula": ago(days=30),
            "nota": 10,
            "faixa": "FRIO",
            "sinais": "Cadastro no curso",
            "calculadoEm": ago(days=30),
            "pessoaId": "p-decai",
        }
        f.add_event("ev-decai-cur", "decai@corp.com", "curso_cadastro", context=CTX_CURSO, source="curso", ref=None, when=ago(days=30))

        # Mesma nota/faixa/sinais, mas sem calculadoEm -> PATCH preenche só calculadoEm.
        f.enrolls["e-semcalc"] = {
            "id": "e-semcalc",
            "inscricaoId": H.inscricao_id(SLUG, "semcalc@corp.com"),
            "curso": "CHATGPT_NO_DIA_A_DIA",
            "status": "ATIVO",
            "entrada": ago(days=30),
            "aulasConcluidas": 0,
            "ultimaAula": None,
            "nota": 5,
            "faixa": "FRIO",
            "sinais": "Cadastro no curso",
            "calculadoEm": None,
            "pessoaId": "p-semcalc",
        }
        f.add_event("ev-semcalc-cur", "semcalc@corp.com", "curso_cadastro", context=CTX_CURSO, source="curso", ref=None, when=ago(days=30))

        # Inscrição que estava com os 4 campos vazios no Twenty e recebeu email_spam fora do Firestore.
        f.enrolls["e-vazia"] = {
            "id": "e-vazia",
            "inscricaoId": H.inscricao_id(SLUG, "vazia-spam@corp.com"),
            "curso": "CHATGPT_NO_DIA_A_DIA",
            "status": "CONCLUIDO",
            "entrada": ago(days=200),
            "aulasConcluidas": 5,
            "ultimaAula": ago(days=190),
            "nota": None,
            "faixa": None,
            "sinais": None,
            "calculadoEm": None,
            "pessoaId": "p-vazia",
        }
        f.add_event("ev-vazia-spam", "vazia-spam@corp.com", "email_spam", context=CTX_CURSO, source="resend", ref="em-2", when=ago(days=1))

        c1, _ = run_job(f, apply=True, ler_curso=lambda _s: [])
        self.assertEqual(c1["inscricoes_atualizadas"], 4)
        patches = {
            p.rsplit("/", 1)[1]: d
            for m, p, d, _ in f.calls
            if m == "PATCH" and p.startswith("/rest/courseEnrollments/")
        }
        self.assertEqual(patches["e-spam"], {
            "nota": 0,
            "faixa": "FRIO",
            "sinais": "Reclamou de spam: não abordar",
            "calculadoEm": NOW_ISO,
        })
        self.assertEqual((f.enrolls["e-spam"]["status"], f.enrolls["e-spam"]["aulasConcluidas"], f.enrolls["e-spam"]["pessoaId"]),
                         ("ATIVO", 2, "p-spam"))
        self.assertEqual(patches["e-decai"], {"nota": 5, "calculadoEm": NOW_ISO})
        self.assertEqual((f.enrolls["e-decai"]["status"], f.enrolls["e-decai"]["aulasConcluidas"]), ("ATIVO", 1))
        self.assertEqual(patches["e-semcalc"], {"calculadoEm": NOW_ISO})
        self.assertEqual(patches["e-vazia"], {
            "nota": 0,
            "faixa": "FRIO",
            "sinais": "Reclamou de spam: não abordar",
            "calculadoEm": NOW_ISO,
        })
        self.assertEqual((f.enrolls["e-vazia"]["status"], f.enrolls["e-vazia"]["aulasConcluidas"]), ("CONCLUIDO", 5))

        # D1 e Twenty alinhados (0/frio no spam); segunda rodada sem mudança não faz nenhum PATCH!
        self.assertEqual((f.heating_rows()[(CTX_CURSO, "spam@corp.com")]["score"], f.heating_rows()[(CTX_CURSO, "spam@corp.com")]["band"]),
                         (0, "frio"))
        n_tw = len(f.twenty_writes())
        c2, _ = run_job(f, apply=True, ler_curso=lambda _s: [])
        self.assertEqual(c2["inscricoes_atualizadas"], 0)
        self.assertEqual(len(f.twenty_writes()), n_tw)

    def test_string_vazia_nao_gera_patch_eterno_e_zero_e_preenchido(self):
        # agy P2: "" é tratado como vazio (sem PATCH eterno), e 0 (nota de spam) é tratado como preenchido.
        f = Fake(
            people=[
                person("p-vazio", "vazio@corp.com", heatingNota=None, heatingFaixa="", heatingSinais="", heatingCalculadoEm=None),
                person("p-zero", "zero@corp.com", heatingNota=0, heatingFaixa="FRIO", heatingSinais="Reclamou de spam: não abordar", heatingCalculadoEm=NOW_ISO),
            ],
            schema=True,
        )
        f.enrolls["e-vazio"] = {
            "id": "e-vazio",
            "inscricaoId": H.inscricao_id(SLUG, "vazio@corp.com"),
            "curso": "CHATGPT_NO_DIA_A_DIA",
            "status": "ATIVO",
            "entrada": ago(days=200),
            "aulasConcluidas": 0,
            "ultimaAula": None,
            "nota": None,
            "faixa": "",
            "sinais": "",
            "calculadoEm": None,
            "pessoaId": "p-vazio",
        }
        f.enrolls["e-fora-vazio"] = {
            "id": "e-fora-vazio",
            "inscricaoId": H.inscricao_id(SLUG, "fora@corp.com"),
            "curso": "CHATGPT_NO_DIA_A_DIA",
            "status": "ATIVO",
            "entrada": ago(days=200),
            "aulasConcluidas": 0,
            "ultimaAula": None,
            "nota": None,
            "faixa": None,
            "sinais": "",
            "calculadoEm": None,
            "pessoaId": "p-vazio",
        }
        f.enrolls["e-zero"] = {
            "id": "e-zero",
            "inscricaoId": H.inscricao_id(SLUG, "zero@corp.com"),
            "curso": "CHATGPT_NO_DIA_A_DIA",
            "status": "ATIVO",
            "entrada": ago(days=200),
            "aulasConcluidas": 0,
            "ultimaAula": None,
            "nota": 0,
            "faixa": "FRIO",
            "sinais": "Reclamou de spam: não abordar",
            "calculadoEm": NOW_ISO,
            "pessoaId": "p-zero",
        }
        docs = [
            {"email": "vazio@corp.com", "status": "active", "googleUid": "uv", "name": "V", "createdAt": ago(days=200), "aulas": []},
            {"email": "zero@corp.com", "status": "active", "googleUid": "uz", "name": "Z", "createdAt": ago(days=200), "aulas": []},
        ]
        counts, _ = run_job(f, apply=True, ler_curso=lambda _s: docs)
        # p-vazio, e-vazio e e-fora-vazio NÃO recebem PATCH; p-zero e e-zero (nota=0) SÃO limpos.
        self.assertEqual((counts["pessoas_limpas"], counts["inscricoes_atualizadas"]), (1, 1))
        patched_paths = [p for m, p, _, _ in f.calls if m == "PATCH"]
        self.assertEqual(patched_paths, ["/rest/people/p-zero", "/rest/courseEnrollments/e-zero"])
        self.assertIsNone(f.people["p-zero"]["heatingNota"])
        self.assertIsNone(f.enrolls["e-zero"]["nota"])

        # optout() também trata "" como vazio.
        out_opt = io.StringIO()
        c_opt = H.optout(TS.Clients(ENV, f, sleep=lambda s: None), "vazio@corp.com", apply=False, out=out_opt, forget_key=FORGET_KEY)
        self.assertEqual((c_opt["pessoas_limpas"], c_opt["inscricoes_limpas"]), (0, 0))

    def test_excluidos_e_dominios_de_teste_nao_criam_pessoa_nem_inscricao(self):
        # Claude P3 + mutante P3: HEATING_EXCLUDE (mesmo com Pessoa já existente no Twenty) e domínios de teste
        # não ganham Pessoa nova nem Inscrição no Twenty.
        f = Fake(people=[person("p-gui", "gui@corp.com")], schema=True)
        docs = [
            {"email": "gui@corp.com", "status": "active", "googleUid": "u1", "name": "Gui", "createdAt": ago(days=0), "aulas": []},
            {"email": "ex2@corp.com", "status": "active", "googleUid": "u4", "name": "Ex2", "createdAt": ago(days=0), "aulas": []},
            {"email": "a@example.com", "status": "active", "googleUid": "u2", "name": "Ex", "createdAt": ago(days=0), "aulas": []},
            {"email": "b@foo.test", "status": "active", "googleUid": "u3", "name": "T", "createdAt": ago(days=0), "aulas": []},
        ]
        counts, _ = run_job(f, apply=True, ler_curso=lambda _s: docs, exclude_emails={"gui@corp.com", "ex2@corp.com"})
        self.assertEqual((counts["pessoas_criadas"], counts["inscricoes_criadas"], counts["excluidos"]), (0, 0, 4))
        self.assertEqual(set(f.people), {"p-gui"})
        self.assertEqual(f.enrolls, {})

    def test_id_evento_aula_inclui_sha16_do_email_e_evita_colisao_mesmo_uid(self):
        # Claude P4: dois documentos com o mesmo googleUid e emails diferentes geram ids distintos
        # `cu:<slug>:aula:<googleUid>:<sha16>:<dayId>` e ambos contabilizam a aula no D1 e no Twenty.
        f = Fake(schema=True)
        docs = [
            {
                "email": "Um@Corp.com",
                "status": "active",
                "googleUid": "uid_shared_1",
                "name": "Um",
                "createdAt": ago(days=0),
                "aulas": [{"dayId": "day-01", "completed": True, "updatedAt": ago(days=0)}],
            },
            {
                "email": "dois@corp.com",
                "status": "active",
                "googleUid": "uid_shared_1",
                "name": "Dois",
                "createdAt": ago(days=0),
                "aulas": [{"dayId": "day-01", "completed": True, "updatedAt": ago(days=0)}],
            },
        ]
        counts, _ = run_job(f, apply=True, ler_curso=lambda _s: docs)
        self.assertEqual(counts["eventos_curso_novos"], 4)  # 2 cadastros + 2 aulas sem colisão
        id_um = f"cu:{SLUG}:aula:uid_shared_1:{H.email_sha256_32('um@corp.com')[:16]}:day-01"
        id_dois = f"cu:{SLUG}:aula:uid_shared_1:{H.email_sha256_32('dois@corp.com')[:16]}:day-01"
        self.assertNotEqual(id_um, id_dois)
        ev_ids = set(f.event_ids())
        self.assertIn(id_um, ev_ids)
        self.assertIn(id_dois, ev_ids)
        hrows = f.heating_rows()
        self.assertEqual((hrows[(CTX_CURSO, "um@corp.com")]["score"], hrows[(CTX_CURSO, "um@corp.com")]["band"]), (15, "morno"))
        self.assertEqual((hrows[(CTX_CURSO, "dois@corp.com")]["score"], hrows[(CTX_CURSO, "dois@corp.com")]["band"]), (15, "morno"))

    def test_ci_pages_yml_roda_test_heating_sync_apos_conversoes(self):
        # P1: .github/workflows/pages.yml precisa rodar test_heating_sync.py no passo "Testes dos scripts de montagem",
        # logo depois de test_conversoes_sync.py.
        pages_yml = os.path.join(HERE, "..", "..", ".github", "workflows", "pages.yml")
        with open(pages_yml, encoding="utf-8") as fh:
            lines = [ln.strip() for ln in fh]
        step_idx = lines.index("- name: Testes dos scripts de montagem")
        conv_idx = lines.index("python3 blog-src/scripts/test_conversoes_sync.py", step_idx)
        self.assertEqual(lines[conv_idx + 1], "python3 blog-src/scripts/test_heating_sync.py")

    def test_mutacoes_sobreviventes_esquecido_optout_pessoa_nova_desempate_e_firestore(self):
        # 1) Evento pré-existente no D1 de curso_esquecido é apagado no passo 3 (preservando site).
        f1 = Fake(people=[person("p1", "esc@corp.com")], schema=True)
        mac_esc = H.curso_esquecido_hmac(CTX_CURSO, "esc@corp.com", FORGET_KEY)
        f1.db.execute("INSERT INTO curso_esquecido (context, email_hmac, forgotten_at) VALUES (?, ?, ?)", (CTX_CURSO, mac_esc, NOW_ISO))
        f1.db.commit()
        f1.add_event("ev-esc-cur", "esc@corp.com", "email_clicado", context=CTX_CURSO, source="resend", ref="m1", when=ago(days=1))
        f1.add_event("ev-esc-site", "esc@corp.com", "material_baixado", context="site", source="conversoes", ref="m1", when=ago(days=1))
        run_job(f1, apply=True)
        self.assertEqual(f1.event_ids(), ["ev-esc-site"])

        # 2) Oposição barra novos eventos de conversão e de curso no D1 depois do run.
        f2 = Fake(schema=True)
        f2.add_optout("opt@corp.com")
        f2.add_conv(1, "opt@corp.com", action="material_download", material="planilha-custos-ia", when=ago(days=0))
        docs2 = [{"email": "opt@corp.com", "status": "active", "googleUid": "u1", "name": "Opt", "createdAt": ago(days=0), "aulas": []}]
        c2, _ = run_job(f2, apply=True, ler_curso=lambda _s: docs2)
        self.assertEqual((c2["eventos_conversao_novos"], c2["eventos_curso_novos"], f2.event_ids(), f2.heating_rows()), (0, 0, [], {}))

        # 3) Pessoa criada pelo curso (inclusive source="form" sem googleUid) já nasce com campos heating* do site.
        f3 = Fake(schema=True)
        f3.add_conv(1, "form@corp.com", action="material_download", material="planilha-custos-ia", when=ago(days=0))
        docs3 = [{"email": "form@corp.com", "status": "active", "source": "form", "googleUid": None, "name": "Form User", "createdAt": ago(days=0), "aulas": []}]
        c3, _ = run_job(f3, apply=True, ler_curso=lambda _s: docs3)
        self.assertEqual((c3["pessoas_criadas"], c3["inscricoes_criadas"]), (1, 1))
        post_people = [d for m, p, d, _ in f3.calls if m == "POST" and p == "/rest/people"]
        self.assertEqual(len(post_people), 1)
        self.assertEqual(
            (post_people[0]["heatingNota"], post_people[0]["heatingFaixa"], post_people[0]["heatingSinais"], post_people[0]["heatingCalculadoEm"]),
            (12, "FRIO", "Download de material", NOW_ISO),
        )

        # 4) Desempate de Pessoa com dois emails: vence a maior nota em qualquer ordem; em empate, desempata por email.
        for em_high, em_low in (("a@corp.com", "b@corp.com"), ("b@corp.com", "a@corp.com")):
            f4 = Fake(people=[person("p1", "a@corp.com", extra=["b@corp.com"])], schema=True)
            f4.add_event("ev-hi", em_high, "site_agende", context="site", source="ga4", ref=None, when=ago(days=0))
            f4.add_event("ev-lo", em_low, "material_baixado", context="site", source="conversoes", ref="m1", when=ago(days=0))
            run_job(f4, apply=True)
            self.assertEqual((f4.people["p1"]["heatingNota"], f4.people["p1"]["heatingFaixa"]), (50, "QUENTE"))
        f4_tie = Fake(people=[person("p1", "a@corp.com", extra=["b@corp.com"])], schema=True)
        f4_tie.add_event("ev-a", "a@corp.com", "material_baixado", context="site", source="conversoes", ref="m1", when=ago(days=0))  # 12
        f4_tie.add_event("ev-b1", "b@corp.com", "newsletter_confirmada", context="site", source="conversoes", ref=None, when=ago(days=0))  # 10
        f4_tie.add_event("ev-b2", "b@corp.com", "site_visita", context="site", source="ga4", ref=None, when=ago(days=0))  # 2 -> 12
        run_job(f4_tie, apply=True)
        self.assertEqual(f4_tie.people["p1"]["heatingSinais"], "Inscrição na newsletter; Visita ao site")

        # 5) Mapeamento de exceções em ler_curso_firestore.
        import types

        with self.assertRaisesRegex(H.SyncError, "curso desconhecido"):
            H.ler_curso_firestore("inexistente", sa_file="/tmp/fake.json")
        with self.assertRaises(H.CursoIndisponivel):
            H.ler_curso_firestore(SLUG, sa_file="")

        class GoogleApiErr(Exception):
            __module__ = "google.api_core.exceptions"

        class GrpcErr(Exception):
            __module__ = "grpc._channel"

        for exc in (OSError("sem arquivo"), ValueError("json ruim"), GoogleApiErr("quota"), GrpcErr("rede"), RuntimeError("bug")):
            fake_google = types.ModuleType("google")
            fake_cloud = types.ModuleType("google.cloud")
            fake_fs = types.ModuleType("google.cloud.firestore")
            fake_oauth = types.ModuleType("google.oauth2")
            fake_sa = types.ModuleType("google.oauth2.service_account")

            class _Creds:
                @staticmethod
                def from_service_account_file(_path, _e=exc):
                    raise _e

            fake_sa.Credentials = _Creds
            fake_fs.Client = object
            fake_cloud.firestore = fake_fs
            fake_oauth.service_account = fake_sa
            fake_google.cloud = fake_cloud
            fake_google.oauth2 = fake_oauth
            saved = {k: sys.modules.get(k) for k in ("google", "google.cloud", "google.cloud.firestore", "google.oauth2", "google.oauth2.service_account")}
            sys.modules.update({
                "google": fake_google,
                "google.cloud": fake_cloud,
                "google.cloud.firestore": fake_fs,
                "google.oauth2": fake_oauth,
                "google.oauth2.service_account": fake_sa,
            })
            try:
                if isinstance(exc, RuntimeError):
                    with self.assertRaises(RuntimeError):
                        H.ler_curso_firestore(SLUG, sa_file="/tmp/fake.json")
                else:
                    with self.assertRaises(H.CursoIndisponivel):
                        H.ler_curso_firestore(SLUG, sa_file="/tmp/fake.json")
            finally:
                for k, v in saved.items():
                    if v is None:
                        sys.modules.pop(k, None)
                    else:
                        sys.modules[k] = v


class CliAndOutputTest(unittest.TestCase):
    def setUp(self):
        fd, self.lock = tempfile.mkstemp(prefix="heating-lock-")
        os.close(fd)
        self.addCleanup(os.unlink, self.lock)

    def _cli(self, fake, *args, stdin=None, ler_curso=None, lock_wait_s=0.05, lock_poll_s=0.01):
        out, err = io.StringIO(), io.StringIO()
        keys = [*ENV, "CONVERSOES_SYNC_LOCK", "HEATING_EXCLUDE", "HEATING_GCP_SA_FILE", "HEATING_EMAIL_DESDE"]
        old = {k: os.environ.get(k) for k in keys}
        os.environ.update(ENV)
        os.environ["CONVERSOES_SYNC_LOCK"] = self.lock
        for opt_k in ("HEATING_EXCLUDE", "HEATING_GCP_SA_FILE", "HEATING_EMAIL_DESDE"):
            os.environ.pop(opt_k, None)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = H.main(
                    list(args),
                    transport=fake,
                    sleep=lambda s: None,
                    stdin=io.StringIO(stdin) if stdin is not None else None,
                    now_dt=NOW,
                    ler_curso=(lambda s: []) if ler_curso is None else ler_curso,
                    lock_wait_s=lock_wait_s,
                    lock_poll_s=lock_poll_s,
                )
        finally:
            for k, v in old.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        return rc, out.getvalue() + err.getvalue()

    def test_saida_tem_todas_as_18_chaves_e_nunca_imprime_email(self):
        f = Fake(people=[person("p1", "segredo@corp.com")])
        f.add_conv(1, "segredo@corp.com", when=ago(days=0))
        rc, text = self._cli(f, "--apply")
        self.assertEqual(rc, 0, text)
        self.assertNotIn("segredo", text.lower())
        for k in H.RUN_COUNT_KEYS:
            self.assertRegex(text, rf"(?m)^{k}: \d+$", k)

        # Erro no Twenty ou exceção inesperada nunca ecoam email.
        g = Fake(fail=lambda m, p, d: (500, {"errors": [{"message": "erro com segredo@corp.com"}]}) if p == "/metadata" else None)
        rc2, text2 = self._cli(g, "--apply")
        self.assertEqual((rc2, "segredo" in text2.lower()), (1, False))

        def broken(*_a):
            raise KeyError("segredo@corp.com")
        rc3, text3 = self._cli(broken)
        self.assertEqual((rc3, "segredo" in text3.lower()), (1, False))

    def test_lock_ocupado_espera_e_falha_com_mensagem_fixa(self):
        f = Fake()
        clock = [0.0]
        sleeps = []

        def fake_sleep(s):
            sleeps.append(s)
            clock[0] += s

        with open(self.lock, "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            out, err = io.StringIO(), io.StringIO()
            old = {k: os.environ.get(k) for k in [*ENV, "CONVERSOES_SYNC_LOCK"]}
            os.environ.update(ENV)
            os.environ["CONVERSOES_SYNC_LOCK"] = self.lock
            try:
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    rc = H.main(
                        ["--apply"],
                        transport=f,
                        sleep=fake_sleep,
                        now_dt=NOW,
                        ler_curso=lambda _s: [],
                        lock_wait_s=600.0,
                        lock_poll_s=100.0,
                        monotonic=lambda: clock[0],
                    )
            finally:
                for k, v in old.items():
                    os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        self.assertEqual(rc, 1)
        self.assertIn("lock ocupado por mais de 10 minutos", err.getvalue())
        self.assertEqual(sum(sleeps), 600.0)
        self.assertEqual(f.calls, [])

    def test_cli_optout_e_forget_curso_exigem_exatamente_um_email(self):
        f = Fake(schema=True)
        for flag in (("--optout",), ("--forget-curso", SLUG)):
            rc0, txt0 = self._cli(f, *flag, stdin="")
            self.assertEqual(rc0, 1)
            self.assertIn("exatamente um email", txt0)
            rc2, txt2 = self._cli(f, *flag, stdin="a@corp.com\nb@corp.com\n")
            self.assertEqual(rc2, 1)
            self.assertIn("exatamente um email", txt2)
        rc_ok, txt_ok = self._cli(f, "--optout", "--apply", stdin="ok@corp.com\n")
        self.assertEqual(rc_ok, 0, txt_ok)
        self.assertNotIn("ok@corp.com", txt_ok)
        rc_fc, txt_fc = self._cli(f, "--forget-curso", SLUG, "--apply", stdin="ok@corp.com\n")
        self.assertEqual(rc_fc, 0, txt_fc)
        self.assertNotIn("ok@corp.com", txt_fc)

    def test_lotes_heating_d1_batch_conversao_e_curso_invalidos_e_check_0012(self):
        # 20 emails válidos -> 2 lotes de INSERT (14 + 6) dentro de uma única transação `batch` com <= 100 params.
        f = Fake(schema=True)
        for i in range(1, 21):
            f.add_conv(i, f"u{i:02d}@corp.com", action="newsletter_confirm", when=ago(days=0))
        # Uma conversão malformada (`conversao_invalida`) e dois docs inválidos no curso (`curso_invalido`).
        f.add_conv("z" * 64, "bad@corp.com", action="newsletter_confirm", when=ago(days=0))
        bad_curso = [
            {"email": "invalido", "status": "active", "createdAt": ago(days=0), "aulas": []},
            {"email": "ok@corp.com", "status": "status_desconhecido", "createdAt": ago(days=0), "aulas": []},
            {
                "email": "ok2@corp.com",
                "status": "active",
                "googleUid": "uid_ok2",
                "createdAt": ago(days=0),
                "aulas": [{"dayId": "day/invalido", "completed": True, "updatedAt": ago(days=0)}],
            },
        ]
        counts, _ = run_job(f, apply=True, ler_curso=lambda _s: bad_curso)
        self.assertEqual(counts["notas_site"], 20)
        self.assertEqual(counts["conversao_invalida"], 1)
        self.assertEqual(counts["curso_invalido"], 3)
        self.assertLessEqual(f.max_params, 100)
        batches = [w for w in f.d1_writes() if w.startswith("BATCH:")]
        self.assertEqual(batches, ["BATCH:DELETE;INSERT;INSERT"])

        # Migração 0012 recusa contexto `site` e `email_hmac` fora do padrão hex 64.
        with self.assertRaises(sqlite3.IntegrityError):
            f.db.execute("INSERT INTO curso_esquecido (context, email_hmac, forgotten_at) VALUES ('site', ?, ?)", ("a" * 64, NOW_ISO))
        with self.assertRaises(sqlite3.IntegrityError):
            f.db.execute("INSERT INTO curso_esquecido (context, email_hmac, forgotten_at) VALUES (?, 'curto', ?)", (CTX_CURSO, NOW_ISO))

        # Vetor cruzado de heating_optout_hmac com o Worker (mesmo prefixo heating-optout-v1\n).
        import hashlib
        import hmac as _hmac
        esperado = _hmac.new(FORGET_KEY.encode("utf-8"), b"heating-optout-v1\nleitor@exemplo.com", hashlib.sha256).hexdigest()
        self.assertEqual(H.heating_optout_hmac("  Leitor@Exemplo.COM ", FORGET_KEY), esperado)

    def test_metadados_nota_mudou_em_e_nao_renomeia_campo_existente(self):
        # 1) Criação do esquema do zero: rótulos e descrições explicam que só muda quando a nota muda
        #    e que a data do último cálculo fica em heating.computed_at no D1 (painel).
        f = Fake(people=[person("p1", "ana@corp.com")], schema=False)
        f.add_conv(1, "ana@corp.com", action="material_download", material="planilha-custos-ia", when=ago(days=0))
        docs = [{
            "email": "ana@corp.com",
            "status": "active",
            "googleUid": "uid_ana",
            "name": "Ana",
            "createdAt": ago(days=0),
            "aulas": [],
        }]
        run_job(f, apply=True, ler_curso=lambda _s: docs)

        spec_person = f.person_fields["heatingCalculadoEm"]["_spec"]
        self.assertEqual(spec_person["label"], "Heating: nota mudou em")
        self.assertIn("heating.computed_at", spec_person["description"])
        self.assertIn("D1", spec_person["description"])

        spec_enroll = f.enroll_fields["calculadoEm"]["_spec"]
        self.assertEqual(spec_enroll["label"], "Nota mudou em")
        self.assertIn("heating.computed_at", spec_enroll["description"])
        self.assertIn("D1", spec_enroll["description"])

        # 2) Se os campos já existirem no Twenty com outro label, o script NÃO renomeia nem chama updateOneField.
        g = Fake(people=[person("p1", "ana@corp.com")], schema=True)
        g.person_fields["heatingCalculadoEm"]["_spec"]["label"] = "Heating: calculado em"
        g.enroll_fields["calculadoEm"]["_spec"]["label"] = "Calculado em"
        g.add_conv(1, "ana@corp.com", action="material_download", material="planilha-custos-ia", when=ago(days=0))
        run_job(g, apply=True, ler_curso=lambda _s: docs)

        meta_muts = [
            d["query"] for m, p, d, _ in g.calls
            if p == "/metadata" and any(x in d["query"] for x in ("createOne", "updateOne"))
        ]
        self.assertEqual(meta_muts, [])
        self.assertEqual(g.person_fields["heatingCalculadoEm"]["_spec"]["label"], "Heating: calculado em")
        self.assertEqual(g.enroll_fields["calculadoEm"]["_spec"]["label"], "Calculado em")

    def test_falha_no_meio_do_batch_sem_rollback_recupera_na_rodada_seguinte(self):
        # D1 falso que executa parte do `batch` de `heating` (DELETE + 1º INSERT de 14 linhas) fazendo commit
        # SEM rollback e falha antes do 2º INSERT (6 linhas). A rodada seguinte precisa refazer `heating` inteira.
        class PartialBatchFake(Fake):
            def __init__(self, *args, **kwargs):
                self.fail_mid_batch = True
                self.mid_batch_triggered = False
                super().__init__(*args, **kwargs)

            def __call__(self, method, url, headers, body):
                data = json.loads(body) if body else None
                u = urllib.parse.urlparse(url)
                if (
                    self.fail_mid_batch
                    and u.netloc == "api.cloudflare.com"
                    and isinstance(data, dict)
                    and "batch" in data
                    and len(data["batch"]) >= 3
                    and data["batch"][0]["sql"] == "DELETE FROM heating"
                ):
                    self.calls.append((method, u.path, data, u.query))
                    self.fail_mid_batch = False
                    self.mid_batch_triggered = True
                    # Executa e commita só o DELETE e o primeiro INSERT, sem rollback, e devolve 500 no meio.
                    for item in data["batch"][:2]:
                        self.db.execute(item["sql"], item.get("params") or [])
                    self.db.commit()
                    return 500, {"success": False, "errors": [{"message": "falha no meio do batch"}]}
                return super().__call__(method, url, headers, body)

        f = PartialBatchFake(schema=True)
        # Linha antiga em `heating` que deve sumir na troca.
        f.db.execute(
            "INSERT INTO heating (context, email, score, band, signals, computed_at, twenty_person_id) "
            "VALUES ('site', 'antigo@corp.com', 99, 'quente', '[]', ?, NULL)",
            (ago(days=1),),
        )
        f.db.commit()
        for i in range(1, 21):
            f.add_conv(i, f"u{i:02d}@corp.com", action="newsletter_confirm", when=ago(days=0))

        # 1ª rodada: falha no meio do batch sem rollback -> SyncError e tabela `heating` parcial (14 de 20).
        with self.assertRaisesRegex(H.SyncError, "D1 respondeu 500"):
            run_job(f, apply=True)
        self.assertTrue(f.mid_batch_triggered)
        parcial = f.heating_rows()
        self.assertEqual(len(parcial), H.HEATING_CHUNK)  # 14 linhas gravadas sem rollback
        self.assertNotIn(("site", "antigo@corp.com"), parcial)

        # 2ª rodada: sem falha, refaz `heating` inteira com as 20 linhas certas.
        counts2, _ = run_job(f, apply=True)
        self.assertEqual(counts2["notas_site"], 20)
        final = f.heating_rows()
        self.assertEqual(len(final), 20)
        self.assertNotIn(("site", "antigo@corp.com"), final)
        for i in range(1, 21):
            row = final[("site", f"u{i:02d}@corp.com")]
            self.assertEqual((row["score"], row["band"], row["computed_at"]), (10, "frio", NOW_ISO))


if __name__ == "__main__":
    unittest.main()

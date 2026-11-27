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
                        results.append({"results": rows})
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
            return 200, {"success": True, "result": [{"results": rows}]}
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
        # Descadastrado: perde sinais de email, mas mantém outros sinais.
        evs2 = evs + [self._ev("d1", "material_baixado", days=0, ref="planilha")]
        r_unsub = H.calcular_heating_contexto("site", evs2, NOW, email_desde=ago(days=30), email_bloqueado=True)
        self.assertEqual((r_unsub["score"], [s["sinal"] for s in r_unsub["signals_list"]]), (12, ["material_baixado"]))

    def test_spam_365_dias_faixas_e_top_3(self):
        # Spam de 300 dias atrás (> 180d, <= 365d) zera mesmo com Agende hoje.
        evs = [
            self._ev("sp", "email_spam", days=300, ref="m0"),
            self._ev("ag", "site_agende", days=0, ref=None),
        ]
        r = H.calcular_heating_contexto("site", evs, NOW)
        self.assertEqual(r["score"], 0)
        self.assertEqual(r["band"], "frio")
        self.assertEqual(r["signals"], '[{"sinal":"email_spam","rotulo":"Reclamou de spam: não abordar","pontos":0}]')

        # Faixas: 14 frio, 15 morno, 39 morno, 40 quente; top 3 sinais em JSON compacto.
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

        # Ana: nota site = 62 (não contaminada pelo curso!), nome original preservado.
        p_ana = f.people["p1"]
        self.assertEqual(p_ana["name"], {"firstName": "Nome", "lastName": "Original"})
        self.assertEqual((p_ana["heatingNota"], p_ana["heatingFaixa"]), (62, "QUENTE"))
        self.assertIn("Download de material (12,0)", p_ana["heatingSinais"])

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

        # Inscrição da Bia no Twenty.
        enr_bia = next(e for e in f.enrolls.values() if e["pessoaId"] == p_bia["id"])
        self.assertEqual(
            (enr_bia["curso"], enr_bia["status"], enr_bia["aulasConcluidas"], enr_bia["nota"], enr_bia["faixa"]),
            ("CHATGPT_NO_DIA_A_DIA", "ATIVO", 1, 15, "MORNO"),
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

    def test_forget_curso_impede_rematerializacao_e_preserva_site(self):
        f = Fake(people=[person("p1", "aluna@corp.com")], schema=True)
        f.add_conv(1, "aluna@corp.com", action="material_download", material="planilha-custos-ia", when=ago(days=0))
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

        # Próxima rodada do job com os mesmos dados ainda no Firestore NÃO rematerializa o curso!
        counts_next, _ = run_job(f, apply=True, ler_curso=lambda _s: docs)
        self.assertEqual((counts_next["eventos_curso_novos"], counts_next["inscricoes_criadas"], counts_next["notas_curso"]), (0, 0, 0))
        self.assertEqual(f.enrolls, {})
        self.assertNotIn((CTX_CURSO, "aluna@corp.com"), f.heating_rows())
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

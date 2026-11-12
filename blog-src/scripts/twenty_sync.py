"""Leva os assinantes da newsletter para o Twenty (crm.guifav.dev) como Pessoas (issue #40, parte E).

Fonte: a cópia do segmento no D1 `blog-admin` (gravada de hora em hora pelo Worker do painel a partir do Resend).
Simulação por padrão: lê o D1 e o Twenty e mostra só contagens. Grava apenas com --apply.

- Campos no objeto Person, criados se faltarem: iaAplicadaStatus (INSCRITO, DESCADASTRADO), iaAplicadaOrigem
  (SUBSTACK, SITE, OUTRA) e iaAplicadaEntrada (data de entrada).
- Correspondência por email, no principal e nos adicionais, sem diferença de caixa. Quem já está no CRM só tem os três
  campos atualizados; nada mais da Pessoa muda. Mais de uma Pessoa com o mesmo email: todas recebem os campos.
- Um estado por Pessoa: se emails da mesma Pessoa têm estados diferentes na cópia, a Pessoa fica como está (conflito
  contado). Email repetido na cópia com estados diferentes: nada é gravado para ele. Cada email cria no máximo uma Pessoa.
- Lê e valida tudo (cópia, metadados e Pessoas) antes de qualquer escrita.
- Ritmo de 650 ms entre chamadas ao Twenty (limite documentado: 100 por minuto); 429 espera 60 s, até 5 tentativas.
- Quem não está no CRM vira Pessoa nova só com o email e os campos (sem nome inventado).
- Idempotente: só grava o que mudou. Nunca apaga Pessoa; quem saiu do segmento continua com o último status.
- A saída tem só contagens, nunca emails, e erros saem com mensagem fixa.

Rodar na KVM (todas as credenciais vêm do ambiente):
    CLOUDFLARE_API_TOKEN=... CLOUDFLARE_ACCOUNT_ID=... D1_DATABASE_ID=... TWENTY_API_KEY=... \\
    TWENTY_LOCAL_URL=http://127.0.0.1:3100 python3 scripts/twenty_sync.py [--apply]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone

STATUS = {0: "INSCRITO", 1: "DESCADASTRADO"}
ORIGIN = {"substack": "SUBSTACK", "site": "SITE", "outra": "OUTRA"}
FIELDS = {
    "iaAplicadaStatus": {"type": "SELECT", "label": "IA Aplicada: status", "options": [
        {"value": "INSCRITO", "label": "Inscrito", "color": "green", "position": 0},
        {"value": "DESCADASTRADO", "label": "Descadastrado", "color": "gray", "position": 1}]},
    "iaAplicadaOrigem": {"type": "SELECT", "label": "IA Aplicada: origem", "options": [
        {"value": "SUBSTACK", "label": "Substack", "color": "orange", "position": 0},
        {"value": "SITE", "label": "Site", "color": "blue", "position": 1},
        {"value": "OUTRA", "label": "Outra", "color": "gray", "position": 2}]},
    "iaAplicadaEntrada": {"type": "DATE_TIME", "label": "IA Aplicada: entrada"},
}
PAGE = 200
MAX_PAGES = 500
# Limite documentado do Twenty: 100 requisições por minuto (docs.twenty.com, importação via API, recomenda >= 600 ms).
GAP_S = 0.65
RETRY_429_S = 60  # janela do limite: depois de um 429, espera a janela inteira
MAX_ATTEMPTS = 5


class SyncError(Exception):
    pass


def _transport(method, url, headers, body):
    req = urllib.request.Request(url, data=body.encode("utf-8") if body else None, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


def iso(value: str) -> str:
    """Data do Resend ou do Substack em ISO UTC ('2024-02-18T15:38:10', '2026-11-11 10:00:00+00', ...); '' se inválida."""
    v = (value or "").strip().replace(" ", "T", 1)
    if not v:
        return ""
    v = re.sub(r"([+-]\d\d)$", r"\1:00", v.replace("Z", "+00:00"))
    try:
        d = datetime.fromisoformat(v)
    except ValueError:
        return ""
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class Clients:
    def __init__(self, env: dict, transport=_transport, sleep=time.sleep, now=time.monotonic):
        self.env, self.t, self.sleep, self.now = env, transport, sleep, now
        self._last = None

    def d1(self, sql: str) -> list[dict]:
        e = self.env
        url = (f"https://api.cloudflare.com/client/v4/accounts/{e['CLOUDFLARE_ACCOUNT_ID']}/d1/database/"
               f"{e['D1_DATABASE_ID']}/query")
        st, data = self.t("POST", url, {"Authorization": f"Bearer {e['CLOUDFLARE_API_TOKEN']}", "Content-Type": "application/json",
                                        "User-Agent": "guifav-twenty-sync"}, json.dumps({"sql": sql}))
        if st != 200 or not data.get("success"):
            raise SyncError(f"D1 respondeu {st}")
        res = data.get("result") or []
        return (res[0] or {}).get("results") or [] if res else []

    def twenty(self, method: str, path: str, body=None, op: str = "") -> dict:
        url = self.env["TWENTY_LOCAL_URL"].rstrip("/") + path
        headers = {"Authorization": f"Bearer {self.env['TWENTY_API_KEY']}", "Content-Type": "application/json",
                   "User-Agent": "guifav-twenty-sync"}
        for _ in range(MAX_ATTEMPTS):
            if self._last is not None:  # ritmo: toda chamada ao Twenty respeita o intervalo mínimo
                wait = GAP_S - (self.now() - self._last)
                if wait > 0:
                    self.sleep(wait)
            st, data = self.t(method, url, headers, json.dumps(body) if body is not None else None)
            self._last = self.now()
            if st == 429:
                self.sleep(RETRY_429_S)
                continue
            if st not in (200, 201) or (isinstance(data, dict) and data.get("errors")):
                raise SyncError(f"Twenty respondeu {st} em {method} ({op})")
            return data if isinstance(data, dict) else {}
        raise SyncError(f"Twenty 429 persistente em {method} ({op})")


def missing_fields(c: Clients) -> tuple[str, list[str]]:
    """Só leitura: id do objeto Person e os campos "IA Aplicada" que faltam."""
    q = "{ objects(paging:{first:200}){ edges{ node{ id nameSingular fields(paging:{first:500}){ edges{ node{ name } } } } } } }"
    data = c.twenty("POST", "/metadata", {"query": q}, op="ler metadados")
    person = next((e["node"] for e in data["data"]["objects"]["edges"] if e["node"]["nameSingular"] == "person"), None)
    if not person:
        raise SyncError("objeto Person não encontrado")
    have = {f["node"]["name"] for f in person["fields"]["edges"]}
    return person["id"], [k for k in FIELDS if k not in have]


def create_fields(c: Clients, object_id: str, missing: list[str]) -> None:
    m = "mutation($input: CreateOneFieldMetadataInput!){ createOneField(input:$input){ id } }"
    for k in missing:
        f = {"objectMetadataId": object_id, "name": k, "isNullable": True, **FIELDS[k]}
        c.twenty("POST", "/metadata", {"query": m, "variables": {"input": {"field": f}}}, op="criar campo")


def list_people(c: Clients) -> list[dict]:
    out, cursor = [], ""
    for _ in range(MAX_PAGES):
        q = f"?limit={PAGE}" + (f"&starting_after={urllib.parse.quote(cursor)}" if cursor else "")
        data = c.twenty("GET", f"/rest/people{q}", op="listar pessoas")
        # Só um término explícito vale: listagem incompleta tratada como fim faria criar de novo quem já está no CRM.
        page = (data.get("data") or {}).get("people")
        info = data.get("pageInfo")
        if not isinstance(page, list) or not isinstance(info, dict) or not isinstance(info.get("hasNextPage"), bool):
            raise SyncError("listagem de pessoas inválida")
        out += page
        if not info["hasNextPage"]:
            return out
        nxt = info.get("endCursor") or ""
        if not nxt or nxt == cursor:
            raise SyncError("paginação de pessoas sem progresso")
        cursor = nxt
    raise SyncError("paginação sem fim em pessoas")


def _emails(p: dict) -> list[str]:
    e = p.get("emails") or {}
    found = [e.get("primaryEmail") or ""] + list(e.get("additionalEmails") or [])
    return sorted({x.strip().lower() for x in found if isinstance(x, str) and x.strip()})


def wanted(row: dict) -> dict:
    return {"iaAplicadaStatus": STATUS[int(row["unsubscribed"])], "iaAplicadaOrigem": ORIGIN.get(row["origin"], "OUTRA"),
            "iaAplicadaEntrada": iso(row["joined_at"]) or None}


def _same(person: dict, want: dict) -> bool:
    return all((person.get(k) or None) == v for k, v in want.items())


def run(c: Clients, *, apply: bool = False, out=None) -> Counter:
    out = out or sys.stdout
    counts: Counter = Counter()
    # 1) Só leituras e validações; nenhuma escrita antes de a fonte e o CRM estarem lidos por inteiro.
    rows = c.d1("SELECT email, unsubscribed, origin, joined_at FROM subscribers")
    if not rows:
        raise SyncError("cópia do D1 vazia; nada foi gravado")
    meta = {r["k"]: r["v"] for r in c.d1("SELECT k, v FROM meta")}
    if meta.get("last_sync_error"):
        counts["aviso_ultima_copia_com_erro"] = 1
    object_id, missing = missing_fields(c)
    counts["campos_a_criar"] = len(missing)
    people = list_people(c)

    # 2) Plano. Um estado por email (email repetido na cópia com estados diferentes: não grava esse email).
    by_sub: dict[str, dict] = {}
    conflicted: set[str] = set()
    for r in rows:
        e, want = r["email"].strip().lower(), wanted(r)
        if e in by_sub and by_sub[e] != want:
            conflicted.add(e)
        by_sub.setdefault(e, want)
    for e in conflicted:
        del by_sub[e]
    counts["conflito_email_repetido_na_copia"] = len(conflicted)
    counts["assinantes"] = len(by_sub) + len(conflicted)
    # Um estado por Pessoa: emails da mesma Pessoa com estados diferentes deixam a Pessoa como está.
    matched: set[str] = set()
    updates: list[tuple[str, dict]] = []
    for p in people:
        source = [e for e in _emails(p) if e in by_sub or e in conflicted]
        matched.update(source)  # inclui os emails em conflito: nunca viram Pessoa nova
        if not source:
            continue
        wants = [by_sub[e] for e in source if e in by_sub]
        # Qualquer email da Pessoa em conflito na cópia, ou estados diferentes entre os emails dela: fica como está.
        if any(e in conflicted for e in source) or any(w != wants[0] for w in wants):
            counts["conflito_pessoa_com_emails_divergentes"] += 1
            continue
        if _same(p, wants[0]):
            counts["sem_mudanca"] += 1
        else:
            updates.append((p["id"], wants[0]))
    counts["conflito_pessoa_com_emails_divergentes"] += 0  # sempre visível na saída
    creates = [(e, w) for e, w in by_sub.items() if e not in matched]
    counts["atualizar"], counts["criar"] = len(updates), len(creates)
    source_emails = set(by_sub) | conflicted
    counts["email_em_mais_de_uma_pessoa"] = sum(1 for e in source_emails if sum(e in _emails(p) for p in people) > 1)

    # 3) Escritas, só com --apply: campos, Pessoas existentes (só os três campos) e Pessoas novas (só email e campos).
    if apply:
        create_fields(c, object_id, missing)
        for pid, want in updates:
            c.twenty("PATCH", f"/rest/people/{urllib.parse.quote(pid)}", want, op="atualizar pessoa")
        for e, want in creates:
            c.twenty("POST", "/rest/people", {"emails": {"primaryEmail": e, "additionalEmails": []}, **want}, op="criar pessoa")
    _report(counts, out, "gravado" if apply else "simulação: nada foi gravado (use --apply)")
    return counts


def _report(counts: Counter, out, tail: str) -> None:
    for k in sorted(counts):
        print(f"{k}: {counts[k]}", file=out)
    print(tail, file=out)


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # noqa: ARG002
        print("ERRO: argumentos inválidos (veja --help)", file=sys.stderr)
        raise SystemExit(2)


REQUIRED_ENV = ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "D1_DATABASE_ID", "TWENTY_API_KEY", "TWENTY_LOCAL_URL")


def main(argv=None, transport=_transport, sleep=time.sleep) -> int:
    ap = _Parser(description="Leva os assinantes da newsletter para o Twenty (simulação por padrão).")
    ap.add_argument("--apply", action="store_true", help="grava no Twenty")
    a = ap.parse_args(argv)
    env = {k: os.environ.get(k, "") for k in REQUIRED_ENV}
    if not all(env.values()):
        print("ERRO: faltam variáveis de ambiente: " + ", ".join(k for k, v in env.items() if not v), file=sys.stderr)
        return 2
    try:
        run(Clients(env, transport, sleep), apply=a.apply)
    except SyncError as e:
        print(f"ERRO: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"ERRO: falha inesperada ({type(e).__name__})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

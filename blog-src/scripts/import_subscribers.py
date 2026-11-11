"""Importa os assinantes do export do Substack para o segmento da newsletter no Resend (issue #21, parte C).

Simulação por padrão: lê o export e o Resend e mostra só contagens. Grava apenas com --apply.

- Entram os emails habilitados, válidos e sem repetição (a entrada mais antiga vale).
- Contato novo: criado no segmento, inscrito, com as propriedades source=substack, substack_joined_at e
  substack_paid_until (vencimento da assinatura paga ativa; vazio para os demais).
- Contato que já existe: nunca é reinscrito nem sobrescrito. Só entra no segmento e, se não tiver origem
  registrada, recebe as propriedades do Substack.
- --sync (para o export novo, antes do primeiro envio): quem veio do Substack, está no segmento e não está mais no
  export é descadastrado (unsubscribed=true). Ninguém é apagado. Recusa rodar se fosse descadastrar mais de 10
  pessoas e mais de 20% de quem veio do Substack (export errado ou incompleto).
- Nada de envio: o cliente recusa qualquer rota fora das de contatos e propriedades.
- A saída tem só contagens, nunca emails.

Rodar na KVM (a chave fica só no ambiente):
    RESEND_API_KEY=... NEWSLETTER_SEGMENT_ID=... python3 scripts/import_subscribers.py <export.csv> [--apply] [--sync]
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass

RESEND = "https://api.resend.com"
PROPERTIES = ("source", "substack_joined_at", "substack_paid_until")
EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
REQUIRED = {"email", "email_disabled", "created_at", "expiry", "active_subscription"}
PAGE = 100
MAX_PAGES = 1000
SYNC_MAX_ABS, SYNC_MAX_RATIO = 10, 0.2  # teto de descadastros num sync: acima dos dois, o export está errado
INTERVAL = 0.15  # abaixo do limite de 10 requisições por segundo do Resend
MAX_RETRIES = 8


class ImportError_(Exception):
    pass


@dataclass
class Subscriber:
    email: str
    joined_at: str
    paid_until: str


def load_export(path: str) -> tuple[list[Subscriber], dict]:
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        missing = REQUIRED - set(reader.fieldnames or [])
        if missing:
            raise ImportError_(f"export sem as colunas {sorted(missing)}")
        rows = list(reader)
    skipped: Counter = Counter()
    best: dict[str, Subscriber] = {}
    for r in rows:
        email = (r["email"] or "").strip().lower()
        if (r["email_disabled"] or "").strip().lower() != "false":
            skipped["desabilitado"] += 1
            continue
        if not EMAIL_RE.fullmatch(email):
            skipped["invalido"] += 1
            continue
        paid = r["expiry"].strip() if (r["active_subscription"] or "").strip().lower() == "true" else ""
        s = Subscriber(email, (r["created_at"] or "").strip(), paid)
        if email in best:
            skipped["repetido"] += 1
            old = best[email]
            if s.joined_at and (not old.joined_at or s.joined_at < old.joined_at):
                best[email] = Subscriber(email, s.joined_at, old.paid_until or s.paid_until)
            continue
        best[email] = s
    return [best[e] for e in sorted(best)], dict(skipped)


def _urllib_transport(method, url, headers, body):
    req = urllib.request.Request(url, data=body.encode("utf-8") if body else None, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"{}"), dict(r.headers)
    except urllib.error.HTTPError as e:
        try:
            data = json.loads(e.read() or b"{}")
        except ValueError:
            data = {}
        return e.code, data, dict(e.headers or {})


class Api:
    """Só contatos e propriedades. Envio, broadcasts, exclusão e segmentos (fora a inclusão) são recusados."""

    ALLOWED = (
        ("GET", r"/contact-properties(\?[^/]*)?"), ("POST", r"/contact-properties"),
        ("GET", r"/contacts\?[^/]*"), ("GET", r"/contacts/[^/?]+"), ("POST", r"/contacts"),
        ("PATCH", r"/contacts/[^/?]+"), ("POST", r"/contacts/[^/?]+/segments/[^/?]+"),
    )

    def __init__(self, key: str, transport=_urllib_transport, sleep=time.sleep):
        self.key = key
        self.transport = transport
        self.sleep = sleep

    def request(self, method: str, path: str, body: dict | None = None, ok=(200, 201)) -> tuple[int, dict]:
        if not any(m == method and re.fullmatch(p, path) for m, p in self.ALLOWED):
            raise ImportError_(f"chamada recusada: {method} {path.split('?')[0]}")
        headers = {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json",
                   "User-Agent": "guifav-blog-import-subscribers"}
        for _ in range(MAX_RETRIES):
            self.sleep(INTERVAL)
            status, data, h = self.transport(method, RESEND + path, headers, json.dumps(body) if body is not None else None)
            if status == 429:
                retry = {k.lower(): v for k, v in h.items()}.get("retry-after", "1")
                self.sleep(float(retry) if re.fullmatch(r"\d+(\.\d+)?", str(retry)) else 1.0)
                continue
            if status not in ok:
                raise ImportError_(f"Resend respondeu {status} em {method} {path.split('/')[1].split('?')[0]}: "
                                   f"{data.get('name') or data.get('message') or ''}")
            return status, data
        raise ImportError_(f"limite de requisições do Resend persistiu em {method} {path.split('?')[0]}")


def list_contacts(api: Api, segment_id: str | None = None) -> list[dict]:
    out, after = [], ""
    for _ in range(MAX_PAGES):
        q = f"limit={PAGE}" + (f"&segment_id={segment_id}" if segment_id else "") + (f"&after={after}" if after else "")
        _, data = api.request("GET", f"/contacts?{q}")
        page = data.get("data") or []
        out += page
        last = page[-1]["id"] if page else ""
        if not data.get("has_more") or not last or last == after:
            return out
        after = last
    raise ImportError_("paginação de contatos sem fim")


def ensure_properties(api: Api) -> int:
    _, data = api.request("GET", f"/contact-properties?limit={PAGE}")
    have = {p.get("key") for p in data.get("data") or []}
    created = 0
    for k in PROPERTIES:
        if k not in have:
            api.request("POST", "/contact-properties", {"key": k, "type": "string"})
            created += 1
    return created


def _props(s: Subscriber) -> dict:
    return {"source": "substack", "substack_joined_at": s.joined_at, "substack_paid_until": s.paid_until}


def run(api: Api, export_path: str, segment_id: str, *, out=sys.stdout, apply: bool = False, sync: bool = False) -> Counter:
    subs, skipped = load_export(export_path)
    counts: Counter = Counter({f"ignorado_{k}": v for k, v in skipped.items()})
    counts["export_validos"] = len(subs)
    everyone = {c["email"].lower(): c for c in list_contacts(api)}
    in_seg = {c["email"].lower(): c for c in list_contacts(api, segment_id)}
    create = [s for s in subs if s.email not in everyone]
    attach = [s for s in subs if s.email in everyone and s.email not in in_seg]
    counts["criar"] = len(create)
    counts["adicionar_ao_segmento"] = len(attach)
    counts["no_segmento"] = sum(1 for s in subs if s.email in in_seg)

    to_unsub = []
    if sync:
        wanted = {s.email for s in subs}
        substack_in_seg = 0
        for email, c in in_seg.items():
            _, full = api.request("GET", f"/contacts/{c['id']}")
            if (full.get("properties") or {}).get("source") != "substack":
                continue
            substack_in_seg += 1
            if email not in wanted and not full.get("unsubscribed"):
                to_unsub.append(c["id"])
        counts["sync_substack_no_segmento"] = substack_in_seg
        counts["sync_descadastrar"] = len(to_unsub)
        if len(to_unsub) > max(SYNC_MAX_ABS, SYNC_MAX_RATIO * substack_in_seg):
            raise ImportError_(f"o sync descadastraria {len(to_unsub)} de {substack_in_seg} vindos do Substack: acima de "
                               f"{SYNC_MAX_ABS} e de {SYNC_MAX_RATIO:.0%}; export provavelmente errado; nada foi gravado")

    if not apply:
        _report(counts, out, "simulação: nada foi gravado (use --apply)")
        return counts

    counts["propriedades_criadas"] = ensure_properties(api)
    for s in create:
        status, _ = api.request("POST", "/contacts", {"email": s.email, "unsubscribed": False,
                                                       "segments": [{"id": segment_id}], "properties": _props(s)},
                                ok=(200, 201, 409))
        if status == 409:  # criado entre a leitura e a escrita (inscrição pelo site): trata como existente
            attach.append(s)
            everyone[s.email] = {"id": api.request("GET", f"/contacts/{s.email}")[1]["id"]}
            continue
        counts["criado"] += 1
    for s in attach:
        cid = everyone[s.email]["id"]
        _, full = api.request("GET", f"/contacts/{cid}")
        if not (full.get("properties") or {}).get("source"):
            api.request("PATCH", f"/contacts/{cid}", {"properties": _props(s)})
        api.request("POST", f"/contacts/{cid}/segments/{segment_id}")
        counts["adicionado_ao_segmento"] += 1
        if full.get("unsubscribed"):
            counts["existente_descadastrado"] += 1
    for cid in to_unsub:
        api.request("PATCH", f"/contacts/{cid}", {"unsubscribed": True})
        counts["sincronizado_descadastrado"] += 1
    _report(counts, out, "gravado")
    return counts


def _report(counts: Counter, out, tail: str) -> None:
    for k in sorted(counts):
        print(f"{k}: {counts[k]}", file=out)
    print(tail, file=out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Importa assinantes do export do Substack para o Resend (simulação por padrão).")
    ap.add_argument("export", help="email_list.*.csv do export do Substack (fora do repo)")
    ap.add_argument("--apply", action="store_true", help="grava no Resend")
    ap.add_argument("--sync", action="store_true", help="descadastra quem veio do Substack e saiu do export")
    a = ap.parse_args(argv)
    key, seg = os.environ.get("RESEND_API_KEY", ""), os.environ.get("NEWSLETTER_SEGMENT_ID", "")
    if not key or not seg:
        print("ERRO: faltam RESEND_API_KEY e NEWSLETTER_SEGMENT_ID no ambiente", file=sys.stderr)
        return 2
    try:
        run(Api(key), a.export, seg, apply=a.apply, sync=a.sync)
    except ImportError_ as e:
        print(f"ERRO: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

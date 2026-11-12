"""Importa os assinantes do export do Substack para o segmento da newsletter no Resend (issue #21, parte C).

Simulação por padrão: lê o export e o Resend e mostra só contagens. Grava apenas com --apply (a exceção é --schema,
que só cria as propriedades).

- Entram os emails habilitados, válidos e sem repetição. Na repetição, vale o registro inteiro mais antigo.
- Contato novo: criado no segmento, inscrito, com as propriedades source=substack, substack_joined_at e
  substack_paid_until (vencimento da assinatura paga ativa; vazio para os demais).
- Contato que já existe: nunca é reinscrito nem sobrescrito. Entra no segmento, se ainda não estiver, e só recebe as
  propriedades do Substack se não tiver origem registrada nem confirmação pelo site. Quem já estava no segmento antes
  da importação sem origem entrou pelo site (Worker anterior ao site_confirmed_at) e fica intocado: o sync o ignora.
- --schema: só cria as propriedades. Rodar antes do deploy do Worker que grava site_confirmed_at; a importação vem
  depois do deploy, para que nenhuma confirmação pelo site fique sem o marcador.
- --sync (export novo, antes do primeiro envio): quem veio do Substack, está no segmento, não confirmou pelo site
  (site_confirmed_at, gravado pelo Worker) e não está mais no export é descadastrado (unsubscribed=true). Ninguém é
  apagado nem tirado do segmento. Recusa antes de gravar se o export estiver vazio ou se fosse descadastrar mais de 10
  pessoas e mais de 10% dos vindos do Substack (export errado ou incompleto).
  Contrato de falhas (adjudicado pelo consultor na rodada 3 do #39; texto completo na descrição do PR):
  S1 nenhum script grava unsubscribed=false: só a confirmação do próprio leitor reinscreve.
  S2 a única escrita do sync é um PATCH com unsubscribed=true e substack_sync_unsubscribed_at juntos, depois de uma
     releitura que mostra source=substack, sem site_confirmed_at e inscrito.
  S5 toda execução com --sync (inclusive a simulação) conta sync_conflito_confirmacao (site_confirmed_at,
     substack_sync_unsubscribed_at e unsubscribed=true) e, se houver algum, sai com código 3.
  F1 confirmação pelo site entre a releitura e o PATCH: o contato fica descadastrado, aparece em S5 e é reinscrito à
     mão antes do primeiro envio.
- Nada de envio: o cliente recusa qualquer rota fora das de contatos e propriedades.
- A saída, inclusive a de erro, tem só contagens, operações e códigos HTTP: nunca emails nem texto da API.

Rodar na KVM (a chave fica só no ambiente):
    RESEND_API_KEY=... NEWSLETTER_SEGMENT_ID=... python3 scripts/import_subscribers.py --schema
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
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone

RESEND = "https://api.resend.com"
# site_confirmed_at é gravada pelo Worker na confirmação; o importador só garante que a propriedade exista.
PROPERTIES = ("source", "substack_joined_at", "substack_paid_until", "site_confirmed_at", "substack_sync_unsubscribed_at")
SYNC_AUDIT = "substack_sync_unsubscribed_at"
EXIT_CONFLICT = 3
REQUIRED = {"email", "email_disabled", "created_at", "expiry", "active_subscription"}
PAGE = 100
MAX_PAGES = 1000
SYNC_MAX_ABS, SYNC_MAX_RATIO = 10, 0.1  # teto de descadastros num sync: acima dos dois, o export está errado
INTERVAL = 0.15  # abaixo do limite de 10 requisições por segundo do Resend
MAX_RETRIES = 8

# Mesma regra do Worker (workers/newsletter/src/index.ts, normalizeEmail), mais rígida na parte local:
# sem ponto no começo, no fim ou repetido.
_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_ATOM = r"[a-z0-9!#$%&*+/=?^_`{|}~-]+"
EMAIL_RE = re.compile(rf"{_ATOM}(?:\.{_ATOM})*@{_LABEL}(?:\.{_LABEL})+")


class ImportError_(Exception):
    pass


@dataclass
class Subscriber:
    email: str
    joined_at: str
    paid_until: str


def valid_email(email: str) -> bool:
    if len(email) > 254 or len(email.split("@", 1)[0]) > 64:
        return False
    return EMAIL_RE.fullmatch(email) is not None


def _rank(s: Subscriber) -> tuple:
    # O registro mais antigo vence inteiro. Sem data vai para o fim. Empate: o que tem pagamento ativo, depois o
    # vencimento mais distante. A ordem das linhas no arquivo não muda o resultado.
    return (s.joined_at == "", s.joined_at, s.paid_until == "", tuple(-ord(c) for c in s.paid_until))


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
        if not valid_email(email):
            skipped["invalido"] += 1
            continue
        paid = (r["expiry"] or "").strip() if (r["active_subscription"] or "").strip().lower() == "true" else ""
        s = Subscriber(email, (r["created_at"] or "").strip(), paid)
        if email in best:
            skipped["repetido"] += 1
            if _rank(s) < _rank(best[email]):
                best[email] = s
            continue
        best[email] = s
    return [best[e] for e in sorted(best)], dict(skipped)


def prop(contact: dict, key: str) -> str:
    """Valor de uma propriedade do contato. O Resend devolve {"value": ..., "type": ...} por chave."""
    v = (contact.get("properties") or {}).get(key)
    if isinstance(v, dict):
        v = v.get("value")
    return "" if v is None else str(v)


def contact_path(email: str) -> str:
    # O email vai codificado, com o @ literal, como no Worker: "/", "?" e "#" não podem virar parte da URL.
    return "/contacts/" + urllib.parse.quote(email, safe="@")


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
    """Só contatos e propriedades. Envio, broadcasts, exclusão e segmentos (fora a inclusão) são recusados.

    Mensagens de erro levam só o método, o nome fixo da operação e o código HTTP: nunca o caminho (pode ter email)
    nem o texto da resposta (o Resend repete o email nas mensagens de validação).
    """

    ALLOWED = (
        ("GET", r"/contact-properties\?[^/#]*"), ("POST", r"/contact-properties"),
        ("GET", r"/contacts\?[^/#]*"), ("GET", r"/contacts/[^/?#]+"), ("POST", r"/contacts"),
        ("PATCH", r"/contacts/[^/?#]+"), ("POST", r"/contacts/[^/?#]+/segments/[^/?#]+"),
    )

    def __init__(self, key: str, transport=_urllib_transport, sleep=time.sleep):
        self.key = key
        self.transport = transport
        self.sleep = sleep

    def request(self, method: str, path: str, body: dict | None = None, *, op: str, ok=(200, 201)) -> tuple[int, dict]:
        if not any(m == method and re.fullmatch(p, path) for m, p in self.ALLOWED):
            raise ImportError_(f"chamada recusada: {method} ({op})")
        headers = {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json",
                   "User-Agent": "guifav-blog-import-subscribers"}
        for _ in range(MAX_RETRIES):
            self.sleep(INTERVAL)
            status, data, h = self.transport(method, RESEND + path, headers, json.dumps(body) if body is not None else None)
            if status == 429:
                retry = str({k.lower(): v for k, v in (h or {}).items()}.get("retry-after", "1")).strip()
                self.sleep(min(float(retry), 60.0) if re.fullmatch(r"\d+(\.\d+)?", retry) else 1.0)
                continue
            if status not in ok:
                raise ImportError_(f"Resend respondeu {status} em {method} ({op})")
            return status, data if isinstance(data, dict) else {}
        raise ImportError_(f"limite de requisições do Resend persistiu em {method} ({op})")


def _paged(api: Api, route: str, extra: str = "") -> list[dict]:
    out, after = [], ""
    for _ in range(MAX_PAGES):
        q = f"limit={PAGE}" + (f"&{extra}" if extra else "") + (f"&after={after}" if after else "")
        _, data = api.request("GET", f"/{route}?{q}", op=f"listar {route}")
        page = data.get("data") or []
        out += page
        last = page[-1].get("id", "") if page else ""
        if not data.get("has_more") or not last or last == after:
            return out
        after = last
    raise ImportError_(f"paginação sem fim em {route}")


def list_contacts(api: Api, segment_id: str | None = None) -> list[dict]:
    return _paged(api, "contacts", f"segment_id={urllib.parse.quote(segment_id)}" if segment_id else "")


def ensure_properties(api: Api) -> int:
    have = {p.get("key") for p in _paged(api, "contact-properties")}
    created = 0
    for k in PROPERTIES:
        if k not in have:
            api.request("POST", "/contact-properties", {"key": k, "type": "string"}, op="criar propriedade")
            created += 1
    return created


def _props(s: Subscriber) -> dict:
    return {"source": "substack", "substack_joined_at": s.joined_at, "substack_paid_until": s.paid_until}


def _protected(full: dict) -> bool:
    """O sync só pode descadastrar quem veio do Substack e não confirmou pelo site."""
    return prop(full, "source") != "substack" or bool(prop(full, "site_confirmed_at"))


def _needs_props(full: dict) -> bool:
    return not prop(full, "source") and not prop(full, "site_confirmed_at")


def run(api: Api, export_path: str, segment_id: str, *, out=None, apply: bool = False, sync: bool = False) -> Counter:
    out = out or sys.stdout  # resolvido na chamada, para o redirecionamento da saída valer
    subs, skipped = load_export(export_path)
    counts: Counter = Counter({f"ignorado_{k}": v for k, v in skipped.items()})
    counts["export_validos"] = len(subs)
    everyone = {c["email"].lower(): c for c in list_contacts(api)}
    in_seg = {c["email"].lower(): c for c in list_contacts(api, segment_id)}
    full: dict[str, dict] = {}

    def read(email: str, cid: str) -> dict:
        if email not in full:
            full[email] = api.request("GET", f"/contacts/{cid}", op="ler contato")[1]
        return full[email]

    create = [s for s in subs if s.email not in everyone]
    existing = [s for s in subs if s.email in everyone]
    attach = [s for s in existing if s.email not in in_seg]
    # Sem origem e já no segmento = opt-in pelo Worker antigo: não recebe source=substack, e o sync o ignora.
    fill = [s for s in existing if _needs_props(read(s.email, everyone[s.email]["id"])) and s.email not in in_seg]
    counts["existente_do_site_intocado"] = sum(1 for s in existing if s.email in in_seg and _needs_props(full[s.email]))
    counts["criar"] = len(create)
    counts["adicionar_ao_segmento"] = len(attach)
    counts["completar_propriedades"] = len(fill)
    counts["no_segmento"] = sum(1 for s in subs if s.email in in_seg)
    counts["existente_descadastrado"] = sum(1 for s in existing if full[s.email].get("unsubscribed"))

    to_unsub = []
    if sync:
        if not subs:
            raise ImportError_("export sem nenhum assinante válido; nada foi gravado")
        wanted = {s.email for s in subs}
        substack_in_seg = 0
        for email, c in in_seg.items():
            f = read(email, c["id"])
            if _protected(f):
                continue
            substack_in_seg += 1
            if email not in wanted and not f.get("unsubscribed"):
                to_unsub.append(c["id"])
        counts["sync_substack_no_segmento"] = substack_in_seg
        counts["sync_descadastrar"] = len(to_unsub)
        # S5: conflito lido do estado dos contatos, não só de quem esta execução tocou.
        counts["sync_conflito_confirmacao"] = sum(
            1 for email, c in in_seg.items()
            if prop(full[email], "site_confirmed_at") and prop(full[email], SYNC_AUDIT) and full[email].get("unsubscribed"))
        if len(to_unsub) > max(SYNC_MAX_ABS, SYNC_MAX_RATIO * substack_in_seg):
            raise ImportError_(f"o sync descadastraria {len(to_unsub)} de {substack_in_seg} vindos do Substack: acima de "
                               f"{SYNC_MAX_ABS} e de {SYNC_MAX_RATIO:.0%}; export provavelmente errado; nada foi gravado")

    if not apply:
        _report(counts, out, "simulação: nada foi gravado (use --apply)")
        return counts

    counts["propriedades_criadas"] = ensure_properties(api)
    ids = {s.email: everyone[s.email]["id"] for s in existing}
    for s in create:
        status, _ = api.request("POST", "/contacts", {"email": s.email, "unsubscribed": False,
                                                       "segments": [{"id": segment_id}], "properties": _props(s)},
                                op="criar contato", ok=(200, 201, 409))
        if status == 409:
            # Criado entre a leitura e a escrita (inscrição pelo site): trata como existente, sem reinscrever.
            _, c = api.request("GET", contact_path(s.email), op="ler contato por email")
            ids[s.email] = c["id"]
            attach.append(s)
            if _needs_props(c):
                fill.append(s)
            counts["corrida_409"] += 1
            continue
        counts["criado"] += 1
    for s in fill:
        fresh = api.request("GET", f"/contacts/{ids[s.email]}", op="reler contato")[1]
        if not _needs_props(fresh):  # ganhou origem ou confirmação pelo site desde a leitura
            continue
        api.request("PATCH", f"/contacts/{ids[s.email]}", {"properties": _props(s)}, op="completar propriedades")
        counts["propriedades_completadas"] += 1
    for s in attach:
        api.request("POST", f"/contacts/{ids[s.email]}/segments/{segment_id}", op="incluir no segmento")
        counts["adicionado_ao_segmento"] += 1
    for cid in to_unsub:
        # S2: releitura imediatamente anterior; sem compensação depois (S1). A corrida residual é o desfecho F1.
        before = api.request("GET", f"/contacts/{cid}", op="reler contato")[1]
        if _protected(before) or before.get("unsubscribed"):
            counts["sync_pulado_na_releitura"] += 1
            continue
        at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        api.request("PATCH", f"/contacts/{cid}", {"unsubscribed": True, "properties": {SYNC_AUDIT: at}}, op="descadastrar")
        counts["sincronizado_descadastrado"] += 1
    _report(counts, out, "gravado")
    return counts


def _report(counts: Counter, out, tail: str) -> None:
    for k in sorted(counts):
        print(f"{k}: {counts[k]}", file=out)
    print(tail, file=out)


class _Parser(argparse.ArgumentParser):
    """Erro de argumento com mensagem fixa: o argparse padrão ecoa os argumentos, e um caminho pode ter email."""

    def error(self, message):  # noqa: ARG002
        print("ERRO: argumentos inválidos (veja --help)", file=sys.stderr)
        raise SystemExit(2)


def main(argv=None, transport=_urllib_transport, sleep=time.sleep) -> int:
    # Ajuda sem saída automática do argparse: combinada com outra opção, recusa (senão --sync --help sairia com zero
    # sem a contagem S5).
    ap = _Parser(description="Importa assinantes do export do Substack para o Resend (simulação por padrão).", add_help=False)
    ap.add_argument("-h", "--help", action="store_true", help="mostra esta ajuda e sai")
    ap.add_argument("export", nargs="?", help="email_list.*.csv do export do Substack (fora do repo)")
    ap.add_argument("--schema", action="store_true", help="só cria as propriedades (antes do deploy do Worker)")
    ap.add_argument("--apply", action="store_true", help="grava no Resend")
    ap.add_argument("--sync", action="store_true", help="descadastra quem veio do Substack e saiu do export")
    a = ap.parse_args(argv)
    if a.help:
        if a.export or a.apply or a.sync or a.schema:
            print("ERRO: argumentos inválidos (--help não se combina com outras opções)", file=sys.stderr)
            return 2
        ap.print_help()
        return 0
    key, seg = os.environ.get("RESEND_API_KEY", ""), os.environ.get("NEWSLETTER_SEGMENT_ID", "")
    if not key or not seg:
        print("ERRO: faltam RESEND_API_KEY e NEWSLETTER_SEGMENT_ID no ambiente", file=sys.stderr)
        return 2
    if a.schema and (a.export or a.apply or a.sync):
        # --schema só cria propriedades: combinado com importação ou sync pularia a contagem S5 e sairia com zero.
        print("ERRO: argumentos inválidos (--schema não se combina com export, --apply ou --sync)", file=sys.stderr)
        return 2
    if not a.schema and not a.export:
        print("ERRO: informe o export ou use --schema", file=sys.stderr)
        return 2
    # Mensagens fixas: nenhuma exceção original é impressa, porque caminho, URL e texto podem conter email.
    try:
        api = Api(key, transport, sleep)
        if a.schema:
            print(f"propriedades_criadas: {ensure_properties(api)}")
        else:
            counts = run(api, a.export, seg, apply=a.apply, sync=a.sync)
            if counts.get("sync_conflito_confirmacao"):
                print("ERRO: há contatos confirmados pelo site e descadastrados pelo sync (sync_conflito_confirmacao); "
                      "reinscrever à mão antes do primeiro envio", file=sys.stderr)
                return EXIT_CONFLICT
    except ImportError_ as e:
        print(f"ERRO: {e}", file=sys.stderr)
        return 1
    except (OSError, UnicodeError, csv.Error) as e:
        print(f"ERRO: não foi possível ler o export ou falha de rede ({type(e).__name__})", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"ERRO: falha inesperada ({type(e).__name__}); nada mais foi gravado", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

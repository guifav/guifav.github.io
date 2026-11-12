"""Redirects do cutover (#24): guilhermefavaron.com.br para guifav.dev.

Uma fonte só para as regras de Single Redirects da Cloudflare, com três usos:

    python3 scripts/cutover_redirects.py rules
        Imprime o JSON das regras, na ordem em que a Cloudflare as avalia.

    python3 scripts/cutover_redirects.py apply --yes [--enable]
        Grava as regras no ruleset de redirects da zona. Antes, lê o ruleset atual e recusa se houver regra que não
        seja deste cutover (o PUT substituiria a fase inteira). Depois, confere campo a campo o que a API devolveu.
        Sem --enable, todas ficam desligadas: serve para preparar antes da janela e para o rollback (antes do
        cutover a fase não tinha nenhuma regra). Ligar só na janela do cutover, com o ok do Gui.
        A leitura e o PUT não são atômicos: rodar com um escritor só na fase (ninguém editando redirects da zona
        pelo painel ao mesmo tempo).
        Lê CLOUDFLARE_API_TOKEN do ambiente. Use um token restrito a esta zona, com permissão só de edição de
        Single Redirects (Zone > Single Redirect > Edit).

    python3 scripts/cutover_redirects.py verify [--urls ARQUIVO] [--allow-404 ARQUIVO] [--quick]
        Confere no ar: 301 de um salto, Location exata e destino 200 sem novo redirect. A matriz cobre, nos dois
        hosts, os 233 posts em /p/ e /post/ (com e sem barra, /comments e /comments/), as páginas especiais (com e
        sem barra) e, para uma amostra de cada forma de caminho, query string, http:// e HEAD, além do host em caixa
        alta. --urls acrescenta URLs antigas (por exemplo, as páginas do Search Console), conferidas nos dois hosts.
        Destino 404 reprova, salvo para as URLs listadas em --allow-404 (erros de digitação conhecidos, guardados
        fora do repo).

    --hosts h1,h2 (em apply e verify) troca os hosts filtrados pelas regras. Serve para o ensaio: as mesmas regras
    ligadas só num host descartável da zona, sem tocar no apex nem no www.

Semântica da Cloudflare reproduzida em simulate():
  - a normalização de URL da zona (tipo "cloudflare", nas requisições de entrada) roda antes das regras:
    \\ vira /, barras repetidas se fundem, %XX de caracteres não reservados é decodificado, os demais escapes vão
    para caixa alta, bytes fora do conjunto permitido são codificados e os segmentos . e .. saem;
  - http.host e os caminhos literais são comparados em caixa baixa (lower() nas expressões);
  - o curinga casa o valor inteiro, sem diferenciar caixa, e cada * pega o menor trecho possível;
  - a primeira regra que casa vence (ação terminal de redirect);
  - a query string não entra no caminho e não é preservada.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import string
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

REPO = Path(__file__).resolve().parent.parent
POSTS = REPO / "blog-src/src/content/posts"

ZONE = "guilhermefavaron.com.br"
HOSTS = (ZONE, "www." + ZONE)
NEW = "https://guifav.dev"
HOME = NEW + "/"
BLOG = NEW + "/blog/"
PRIVACY = NEW + "/blog/privacidade/"
RSS = NEW + "/blog/rss.xml"
PHASE = "http_request_dynamic_redirect"
PREFIX = "cutover #24: "
MAX_RULES = 10  # plano Free
LEGAL = ("politica-de-privacidade", "termos-e-condicoes-de-uso")  # páginas do Substack, não importadas como post

PATH = "http.request.uri.path"
LPATH = f"lower({PATH})"


def _set(items) -> str:
    return "{" + " ".join(f'"{x}"' for x in sorted(items)) + "}"


@dataclass(frozen=True)
class Rule:
    description: str
    target: str  # URL fixa, ou padrão com ${1} quando wildcard
    paths: frozenset[str] = field(default_factory=frozenset)  # caminhos exatos, em caixa baixa
    prefixes: tuple[str, ...] = ()  # starts_with, em caixa baixa
    wildcard: str | None = None  # padrão do caminho (curinga sem caixa)
    exclude: frozenset[str] = field(default_factory=frozenset)  # caminhos que a regra não pega, em caixa baixa

    def expression(self, hosts: tuple[str, ...] = HOSTS) -> str:
        conds = []
        if self.paths:
            conds.append(f"{LPATH} in {_set(self.paths)}")
        conds += [f'starts_with({LPATH}, "{p}")' for p in self.prefixes]
        if self.wildcard:
            conds.append(f'{PATH} wildcard r"{self.wildcard}"')
        expr = f"(lower(http.host) in {_set(hosts)})"
        if conds:
            expr += " and (" + " or ".join(conds) + ")"
        if self.exclude:
            expr += f" and not ({LPATH} in {_set(self.exclude)})"
        return expr

    def target_url(self) -> dict:
        if self.wildcard:
            return {"expression": f'wildcard_replace({PATH}, r"{self.wildcard}", r"{self.target}")'}
        return {"value": self.target}

    def matches(self, path: str) -> bool:
        low = path.lower()
        if low in self.exclude:
            return False
        if not (self.paths or self.prefixes or self.wildcard):
            return True
        return (low in self.paths or any(low.startswith(p) for p in self.prefixes)
                or (self.wildcard is not None and _wild(self.wildcard).match(path) is not None))

    def resolve(self, path: str) -> str:
        if not self.wildcard:
            return self.target
        m = _wild(self.wildcard).match(path)
        assert m is not None
        return re.sub(r"\$\{(\d)\}", lambda g: m.group(int(g.group(1))), self.target)


def _wild(pattern: str) -> re.Pattern[str]:
    """Curinga da Cloudflare: casa o valor inteiro, sem caixa, cada * preguiçoso."""
    return re.compile("^" + "".join("(.*?)" if c == "*" else re.escape(c) for c in pattern) + "$", re.I | re.S)


def _post(prefix: str) -> list[Rule]:
    # Com barra final antes da sem barra: senão o * levaria a barra e o destino teria //.
    # O caminho vazio (/p/, /post/) vai para a regra final, não para /blog//.
    return [
        Rule(f"post {prefix}<slug>/", NEW + "/blog/${1}/", wildcard=prefix + "*/"),
        Rule(f"post {prefix}<slug>", NEW + "/blog/${1}/", wildcard=prefix + "*", exclude=frozenset({prefix})),
    ]


# A ordem importa: a primeira regra que casa vence.
RULES: tuple[Rule, ...] = (
    Rule("privacidade, termos e ações dos emails antigos", PRIVACY,
         paths=frozenset({f"/p/{s}{v}" for s in LEGAL for v in ("", "/", "/comments", "/comments/")}
                         | {"/privacy", "/privacy/", "/tos", "/tos/"}),
         prefixes=("/action/",)),
    Rule("home do site", HOME, paths=frozenset({"/", "/about", "/about/"})),
    Rule("comentários de post", NEW + "/blog/${1}/", wildcard="/p/*/comments*"),
    *_post("/p/"),
    *_post("/post/"),
    Rule("feed", RSS, paths=frozenset({"/feed", "/feed/"})),
    Rule("qualquer outro caminho", BLOG),
)

# ---------------------------------------------------------------- normalização de URL da zona

_UNRESERVED = frozenset(string.ascii_letters + string.digits + "-._~")
_KEEP = frozenset(":/?#[]@!$&'()*+,;=")


def _remove_dot_segments(path: str) -> str:
    """RFC 3986, seção 5.2.4."""
    inp, out = path, []
    while inp:
        if inp.startswith("../"):
            inp = inp[3:]
        elif inp.startswith("./"):
            inp = inp[2:]
        elif inp.startswith("/./"):
            inp = inp[2:]
        elif inp == "/.":
            inp = "/"
        elif inp.startswith("/../"):
            inp = inp[3:]
            if out:
                out.pop()
        elif inp == "/..":
            inp = "/"
            if out:
                out.pop()
        elif inp in (".", ".."):
            inp = ""
        else:
            i = inp.find("/", 1 if inp.startswith("/") else 0)
            seg, inp = (inp, "") if i == -1 else (inp[:i], inp[i:])
            out.append(seg)
    return "".join(out)


def normalize(path: str) -> str:
    """Normalização "cloudflare" da zona, na ordem da documentação: \\ -> /, funde //, depois RFC 3986."""
    p = re.sub(r"/{2,}", "/", path.replace("\\", "/"))
    out, i = [], 0
    while i < len(p):
        c = p[i]
        if c == "%" and re.fullmatch(r"[0-9A-Fa-f]{2}", p[i + 1:i + 3]):
            ch = chr(int(p[i + 1:i + 3], 16))
            out.append(ch if ch in _UNRESERVED else "%" + p[i + 1:i + 3].upper())
            i += 3
            continue
        out.append(c if c in _UNRESERVED or c in _KEEP else "".join(f"%{b:02X}" for b in c.encode("utf-8")))
        i += 1
    return _remove_dot_segments("".join(out)) or "/"


def simulate(host: str, path: str, hosts: tuple[str, ...] = HOSTS) -> str | None:
    """Location que as regras produzem para host+caminho (caminho como chega); None se o host não é do cutover."""
    if host.lower() not in hosts:
        return None
    path = normalize(path)
    for r in RULES:
        if r.matches(path):
            return r.resolve(path)
    return None


def cloudflare_rules(enabled: bool, hosts: tuple[str, ...] = HOSTS) -> list[dict]:
    return [{
        "description": PREFIX + r.description,
        "expression": r.expression(hosts),
        "action": "redirect",
        "action_parameters": {"from_value": {"status_code": 301, "preserve_query_string": False,
                                             "target_url": r.target_url()}},
        "enabled": enabled,
    } for r in RULES]


# ---------------------------------------------------------------- Cloudflare

API = "https://api.cloudflare.com/client/v4"
_KEYS = ("description", "expression", "action", "action_parameters", "enabled")


def _cf(method: str, path: str, token: str, body: dict | None = None, opener=urllib.request.urlopen,
        missing_ok: bool = False) -> dict:
    req = urllib.request.Request(API + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    try:
        with opener(req, timeout=30) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        data = json.load(e)
    if missing_ok and any(e.get("code") == 10003 for e in data.get("errors") or []):
        return {"success": True, "result": {"rules": []}}  # a fase ainda não tem ruleset
    if not data.get("success"):
        raise SystemExit(f"Cloudflare {method} {path}: {data.get('errors')}")
    return data


def apply(token: str, enabled: bool, hosts: tuple[str, ...] = HOSTS, opener=urllib.request.urlopen) -> int:
    zones = _cf("GET", f"/zones?name={ZONE}", token, opener=opener)["result"]
    if len(zones) != 1 or zones[0].get("name") != ZONE:
        raise SystemExit(f"zona {ZONE}: resultado inesperado ({len(zones)})")
    entry = f"/zones/{zones[0]['id']}/rulesets/phases/{PHASE}/entrypoint"
    current = (_cf("GET", entry, token, opener=opener, missing_ok=True)["result"] or {}).get("rules") or []
    foreign = [r.get("description") or r.get("id") for r in current
               if not str(r.get("description") or "").startswith(PREFIX)]
    if foreign:
        raise SystemExit(f"o ruleset de redirects tem regras que não são do cutover: {foreign}. O PUT as apagaria.")
    sent = cloudflare_rules(enabled, hosts)
    res = _cf("PUT", entry, token, {"rules": sent}, opener=opener)["result"]
    got = res.get("rules") or []
    if [{k: r.get(k) for k in _KEYS} for r in got] != sent:
        raise SystemExit(f"ruleset gravado não confere com o enviado ({len(got)} regras): rodar apply de novo")
    print(f"{len(got)} regras gravadas na zona {ZONE}, {'LIGADAS' if enabled else 'desligadas'}, "
          f"hosts {', '.join(hosts)} (ruleset {res.get('id')})")
    return 0


# ---------------------------------------------------------------- verificação no ar

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def fetch(url: str, method: str = "GET") -> tuple[int, str | None]:
    """(status, Location) sem seguir redirect; status 0 se a conexão falhar."""
    try:
        req = urllib.request.Request(url, method=method, headers={"User-Agent": "guifav-cutover-check/1"})
        with _opener.open(req, timeout=20) as r:
            return r.status, r.headers.get("Location")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Location")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, UnicodeError):
        return 0, None


def to_uri(url: str) -> str:
    """Como o navegador envia: caminho e query com percent-encoding (acentos viram %XX; %XX já pronto fica)."""
    s = urlsplit(url.strip())
    return urlunsplit((s.scheme, s.netloc, quote(s.path, safe="/%:@!$&'()*+,;=-._~"),
                       quote(s.query, safe="=&%/:@!$'()*+,;?-._~"), ""))


def slugs() -> list[str]:
    return sorted(f.name.rsplit(".", 1)[0] for f in POSTS.glob("*.md*"))


SPECIAL = ["/", "/about", "/about/", "/feed", "/feed/", "/archive", "/archive/", "/podcast", "/podcast/", "/leaderboard",
           "/leaderboard/", "/privacy", "/privacy/", "/tos", "/tos/", "/action/disable_email",
           "/action/disable_email/"] + [f"/p/{s}{v}" for s in LEGAL for v in ("", "/", "/comments",
                                                                                             "/comments/")]
VARIANTS = ("/p/{s}", "/p/{s}/", "/p/{s}/comments", "/p/{s}/comments/", "/post/{s}", "/post/{s}/")


@dataclass(frozen=True)
class Job:
    url: str
    method: str = "GET"
    allow_404: bool = False


def jobs(hosts: tuple[str, ...] = HOSTS, quick: bool = False, extra: list[str] = (),
         allow_404: set[str] = frozenset()) -> list[Job]:
    """A matriz da verificação. allow_404: caminhos (normalizados) que podem cair num destino sem página."""
    ss = slugs()
    if quick:
        ss = ss[:: max(1, len(ss) // 12)]
    paths = SPECIAL + [v.format(s=s) for s in ss for v in VARIANTS]
    out = [Job(f"https://{h}{p}") for p in paths for h in hosts]
    sample = [v.format(s=ss[0]) for v in VARIANTS] + ["/", "/feed", "/privacy", "/archive", "/action/x"] if ss else []
    for h in hosts:
        out += [Job(f"https://{h}{p}?utm_source=x&ref=y") for p in sample]
        out += [Job(f"http://{h}{p}") for p in sample]
        out += [Job(f"https://{h}{p}", "HEAD") for p in sample]
        out += [Job(f"https://{h.upper()}{p}") for p in sample[:2]]
    for u in extra:
        if u.strip():
            p = urlsplit(to_uri(u)).path or "/"
            out += [Job(f"https://{h}{p}", allow_404=normalize(p) in allow_404) for h in hosts]
    return out


def verify(todo: list[Job], fetcher=fetch, pause: float = 0.12, out=sys.stdout, hosts: tuple[str, ...] = HOSTS) -> int:
    """Confere cada URL antiga. Falha: status, Location, conexão ou destino diferente de 200 direto, salvo
    destino 404 em URL marcada como allow_404 (contada à parte)."""
    dest_cache: dict[str, tuple[int, str | None]] = {}
    fails: list[str] = []
    no_page: list[str] = []
    ok = 0
    for job in todo:
        parts = urlsplit(job.url)
        want = simulate(parts.hostname or "", parts.path or "/", hosts)
        if want is None:
            fails.append(f"{job.url}: fora dos hosts do cutover")
            continue
        status, loc = fetcher(job.url, job.method)
        time.sleep(pause)
        if status != 301 or loc != want:
            fails.append(f"{job.method} {job.url}: esperado 301 -> {want}, veio {status} -> {loc}")
            continue
        if want not in dest_cache:
            dest_cache[want] = fetcher(want, "GET")
            time.sleep(pause)
        dstatus, dloc = dest_cache[want]
        if dstatus == 200:
            ok += 1
        elif dstatus == 404 and job.allow_404:
            no_page.append(job.url)
        else:
            fails.append(f"{job.url}: destino {want} respondeu {dstatus}" + (f" -> {dloc}" if dloc else ""))
    print(f"verificadas: {len(todo)}; ok: {ok}; sem página (permitidas): {len(no_page)}; falhas: {len(fails)}",
          file=out)
    for f in fails[:50]:
        print("FALHA " + f, file=out)
    for u in no_page[:50]:
        print("SEM PÁGINA " + u, file=out)
    return 1 if fails else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("rules")
    a = sub.add_parser("apply")
    a.add_argument("--enable", action="store_true", help="liga as regras (só na janela, com ok do Gui)")
    a.add_argument("--yes", action="store_true", help="confirma a gravação na zona")
    a.add_argument("--hosts", help="hosts filtrados, separados por vírgula (ensaio)")
    v = sub.add_parser("verify")
    v.add_argument("--urls", type=Path, help="arquivo com uma URL antiga por linha")
    v.add_argument("--allow-404", type=Path, help="arquivo com URLs antigas cujo destino pode não ter página")
    v.add_argument("--quick", action="store_true", help="amostra de ~12 posts")
    v.add_argument("--hosts", help="hosts conferidos, separados por vírgula (ensaio)")
    args = ap.parse_args(argv)
    hosts = HOSTS
    if getattr(args, "hosts", None):
        hosts = tuple(h.strip().lower() for h in args.hosts.split(",") if h.strip())
        if not hosts or any(not h.endswith("." + ZONE) and h != ZONE for h in hosts):
            raise SystemExit(f"--hosts: só hosts da zona {ZONE}")
    if len(RULES) > MAX_RULES:
        raise SystemExit(f"{len(RULES)} regras: o plano Free aceita {MAX_RULES}")
    if args.cmd == "rules":
        print(json.dumps(cloudflare_rules(enabled=True), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "apply":
        if not args.yes:
            raise SystemExit("apply grava na zona: repita com --yes")
        token = os.environ.get("CLOUDFLARE_API_TOKEN")
        if not token:
            raise SystemExit("defina CLOUDFLARE_API_TOKEN (token restrito à zona)")
        return apply(token, args.enable, hosts)
    extra = args.urls.read_text(encoding="utf-8").splitlines() if args.urls else []
    allow = set()
    if args.allow_404:
        allow = {normalize(urlsplit(to_uri(u)).path or "/") for u in args.allow_404.read_text(encoding="utf-8").splitlines()
                 if u.strip()}
    return verify(jobs(hosts, args.quick, extra, allow), hosts=hosts)


if __name__ == "__main__":
    sys.exit(main())

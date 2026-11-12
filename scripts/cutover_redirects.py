"""Redirects do cutover (#24): guilhermefavaron.com.br para guifav.dev.

Uma fonte só para as regras de Single Redirects da Cloudflare, com três usos:

    python3 scripts/cutover_redirects.py rules
        Imprime o JSON das regras, na ordem em que a Cloudflare as avalia.

    python3 scripts/cutover_redirects.py apply --yes [--enable]
        Grava as regras na zona (substitui o ruleset de redirects). Sem --enable, todas ficam desligadas:
        serve para preparar antes da janela e para o rollback. Ligar só na janela do cutover, com o ok do Gui.
        Lê CLOUDFLARE_API_TOKEN do ambiente (token da conta pessoal; a zona é procurada pelo nome).

    python3 scripts/cutover_redirects.py verify [--urls ARQUIVO] [--quick]
        Confere no ar, nos dois hosts: 301 de um salto, Location exata e destino 200 sem novo redirect.
        Corpus: os 233 posts do blog (em /p/ e /post/, com e sem barra final e com /comments), as páginas
        especiais e, se dado, um arquivo com uma URL antiga por linha (por exemplo, as páginas do Search Console).

    --hosts h1,h2 (em apply e verify) troca os hosts filtrados pelas regras. Serve para o ensaio: as mesmas regras
    ligadas só num host descartável da zona, sem tocar no apex nem no www.

Semântica da Cloudflare reproduzida em simulate():
  - a primeira regra que casa vence (ação terminal de redirect);
  - o padrão curinga casa o valor inteiro, sem diferenciar caixa, e cada * pega o menor trecho possível;
  - o padrão é aplicado ao caminho (http.request.uri.path), sem a query string, que não é preservada.
"""
from __future__ import annotations

import argparse
import json
import os
import re
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
MAX_RULES = 10  # plano Free


@dataclass(frozen=True)
class Rule:
    description: str
    target: str  # URL fixa, ou padrão com ${1} quando wildcard
    paths: frozenset[str] = field(default_factory=frozenset)  # caminhos exatos
    prefixes: tuple[str, ...] = ()  # starts_with
    wildcard: str | None = None  # padrão do caminho
    exclude: frozenset[str] = field(default_factory=frozenset)  # caminhos que a regra não pega

    def expression(self, hosts: tuple[str, ...] = HOSTS) -> str:
        host = "http.host in {" + " ".join(f'"{h}"' for h in hosts) + "}"
        conds = []
        if self.paths:
            conds.append("http.request.uri.path in {" + " ".join(f'"{p}"' for p in sorted(self.paths)) + "}")
        conds += [f'starts_with(http.request.uri.path, "{p}")' for p in self.prefixes]
        if self.wildcard:
            conds.append(f'http.request.uri.path wildcard r"{self.wildcard}"')
        expr = f"({host})"
        if conds:
            expr += " and (" + " or ".join(conds) + ")"
        if self.exclude:
            expr += " and not (http.request.uri.path in {" + " ".join(f'"{p}"' for p in sorted(self.exclude)) + "})"
        return expr

    def target_url(self) -> dict:
        if self.wildcard:
            return {"expression": f'wildcard_replace(http.request.uri.path, r"{self.wildcard}", r"{self.target}")'}
        return {"value": self.target}

    def matches(self, path: str) -> bool:
        if path in self.exclude:
            return False
        if not (self.paths or self.prefixes or self.wildcard):
            return True
        return (path in self.paths or any(path.startswith(p) for p in self.prefixes)
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
    return [
        Rule(f"post {prefix}<slug>/", NEW + "/blog/${1}/", wildcard=prefix + "*/"),
        Rule(f"post {prefix}<slug>", NEW + "/blog/${1}/", wildcard=prefix + "*", exclude=frozenset({prefix})),
    ]


# A ordem importa: a primeira regra que casa vence.
RULES: tuple[Rule, ...] = (
    Rule("privacidade, termos e ações dos emails antigos", PRIVACY,
         paths=frozenset({"/p/politica-de-privacidade", "/p/politica-de-privacidade/",
                          "/p/termos-e-condicoes-de-uso", "/p/termos-e-condicoes-de-uso/",
                          "/privacy", "/privacy/", "/tos", "/tos/"}),
         prefixes=("/action/",)),
    Rule("home do site", HOME, paths=frozenset({"/", "/about", "/about/"})),
    Rule("comentários de post", NEW + "/blog/${1}/", wildcard="/p/*/comments*"),
    *_post("/p/"),
    *_post("/post/"),
    Rule("feed", RSS, paths=frozenset({"/feed", "/feed/"})),
    Rule("qualquer outro caminho", BLOG),
)


def simulate(host: str, path: str, hosts: tuple[str, ...] = HOSTS) -> str | None:
    """Location que as regras produzem para host+caminho; None se nenhuma casa (outro host)."""
    if host.lower() not in hosts:
        return None
    for r in RULES:
        if r.matches(path):
            return r.resolve(path)
    return None


def cloudflare_rules(enabled: bool, hosts: tuple[str, ...] = HOSTS) -> list[dict]:
    return [{
        "description": f"cutover #24: {r.description}",
        "expression": r.expression(hosts),
        "action": "redirect",
        "action_parameters": {"from_value": {"status_code": 301, "preserve_query_string": False,
                                             "target_url": r.target_url()}},
        "enabled": enabled,
    } for r in RULES]


# ---------------------------------------------------------------- Cloudflare

API = "https://api.cloudflare.com/client/v4"


def _cf(method: str, path: str, token: str, body: dict | None = None, opener=urllib.request.urlopen) -> dict:
    req = urllib.request.Request(API + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    try:
        with opener(req, timeout=30) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        data = json.load(e)
    if not data.get("success"):
        raise SystemExit(f"Cloudflare {method} {path}: {data.get('errors')}")
    return data


def apply(token: str, enabled: bool, hosts: tuple[str, ...] = HOSTS, opener=urllib.request.urlopen) -> int:
    zones = _cf("GET", f"/zones?name={ZONE}", token, opener=opener)["result"]
    if len(zones) != 1:
        raise SystemExit(f"zona {ZONE}: {len(zones)} resultados")
    zid = zones[0]["id"]
    res = _cf("PUT", f"/zones/{zid}/rulesets/phases/{PHASE}/entrypoint", token,
              {"rules": cloudflare_rules(enabled, hosts)}, opener=opener)["result"]
    got = res.get("rules") or []
    if len(got) != len(RULES) or any(bool(r.get("enabled")) != enabled for r in got):
        raise SystemExit(f"ruleset gravado não confere: {len(got)} regras")
    print(f"{len(got)} regras gravadas na zona {ZONE}, {'LIGADAS' if enabled else 'desligadas'}, "
          f"hosts {', '.join(hosts)} (ruleset {res.get('id')})")
    return 0


# ---------------------------------------------------------------- verificação no ar

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def fetch(url: str) -> tuple[int, str | None]:
    """(status, Location) sem seguir redirect; status 0 se a conexão falhar."""
    try:
        req = urllib.request.Request(url, method="GET", headers={"User-Agent": "guifav-cutover-check/1"})
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


def corpus(quick: bool) -> list[str]:
    paths = ["/", "/about", "/feed", "/archive", "/podcast", "/leaderboard", "/privacy", "/tos",
             "/p/politica-de-privacidade", "/p/termos-e-condicoes-de-uso", "/action/disable_email"]
    ss = slugs()
    if quick:
        ss = ss[:: max(1, len(ss) // 12)]
    for s in ss:
        paths += [f"/p/{s}", f"/p/{s}/", f"/p/{s}/comments", f"/post/{s}"]
    return paths


def verify(paths: list[str], extra: list[str], fetcher=fetch, pause: float = 0.12, out=sys.stdout,
           hosts: tuple[str, ...] = HOSTS) -> int:
    """Confere cada URL antiga. Falha: status, Location ou destino. URLs de --urls com destino sem página
    (erro de digitação em link externo) são contadas à parte e não reprovam."""
    jobs = [(f"https://{h}{p}", True) for p in paths for h in hosts]
    jobs += [(to_uri(u), False) for u in extra if u.strip()]
    jobs += [(f"https://{hosts[-1]}/p/{slugs()[0]}?utm_source=x", True)] if slugs() else []
    dest_cache: dict[str, tuple[int, str | None]] = {}
    fails: list[str] = []
    no_page: list[str] = []
    ok = 0
    for url, strict in jobs:
        parts = urlsplit(url)
        want = simulate(parts.hostname or "", parts.path or "/", hosts)
        status, loc = fetcher(url)
        time.sleep(pause)
        if want is None:
            fails.append(f"{url}: fora dos hosts do cutover")
            continue
        if status != 301 or loc != want:
            fails.append(f"{url}: esperado 301 -> {want}, veio {status} -> {loc}")
            continue
        if want not in dest_cache:
            dest_cache[want] = fetcher(want)
            time.sleep(pause)
        dstatus, dloc = dest_cache[want]
        if dstatus == 200:
            ok += 1
        elif strict or dstatus != 404:
            fails.append(f"{url}: destino {want} respondeu {dstatus}" + (f" -> {dloc}" if dloc else ""))
        else:
            no_page.append(url)
    print(f"verificadas: {len(jobs)}; ok: {ok}; destino sem página (só --urls): {len(no_page)}; falhas: {len(fails)}",
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
            raise SystemExit("defina CLOUDFLARE_API_TOKEN (conta pessoal)")
        return apply(token, args.enable, hosts)
    extra = args.urls.read_text(encoding="utf-8").splitlines() if args.urls else []
    return verify(corpus(args.quick), extra, hosts=hosts)


if __name__ == "__main__":
    sys.exit(main())

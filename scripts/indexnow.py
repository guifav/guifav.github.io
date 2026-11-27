"""IndexNow do guifav.dev: avisa Bing, Yandex e os demais buscadores do protocolo sobre URLs novas, alteradas
ou removidas, logo depois do deploy.

Fluxo no workflow do Pages:
1. build: restaura o registro de URLs já avisadas do cache do GitHub Actions (`indexnow-registry-*`) e roda
   `diff --registry`, que compara os sitemaps do _site/ com os publicados (ainda o deploy anterior) e inclui
   também as URLs do build que nunca foram avisadas com o lastmod atual. Grava o plano (modo, URLs, sha256 de
   cada sitemap novo, base do registro e lastmods) num arquivo, que vai como artefato ao job seguinte.
   URL sem lastmod no sitemap (a capa do blog e as páginas de tema, `/blog/tema/...`) entra quando o HTML
   publicado difere do build ou quando ainda não está no registro.
   Se não conseguir ler o site publicado, o plano vira modo "all": todas as URLs do build mais as que saíram do
   índice desde o commit anterior (git diff contra github.event.before). Sem esse commit, o plano é marcado
   incompleto e o job termina vermelho depois do envio. O deploy nunca depende do IndexNow.
2. depois do deploy: `submit --registry-out` espera os três sitemaps publicados ficarem iguais aos do build
   (e a chave estar no ar), manda as URLs e grava o registro atualizado (salvo no cache do Actions mesmo quando
   o envio falha, para que os lotes já aceitos fiquem registrados).

Registro (cache do Actions):
- Fica no cache do GitHub Actions (`indexnow-registry-*`), fora do repositório.
- Ausência ou invalidez do cache faz avisar todas as URLs do build uma vez e recria o registro.
- O cache do Actions some depois de 7 dias sem acesso, o que também só causa um reenvio completo.
- Re-executar o job `indexnow` de um deploy antigo depois de um mais novo pode salvar um registro mais velho
  como o mais recente; o efeito é só reenviar URLs, nunca deixar de avisar.

À mão: `all` avisa tudo o que está publicado; `urls URL...` avisa URLs escolhidas (remoções, por exemplo).
Os comandos manuais (`all` e `urls`) não leem nem gravam o registro.
    python3 scripts/indexnow.py all

A chave fica pública em https://guifav.dev/<KEY>.txt, como o protocolo exige.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_sitemaps import _Head, noindex, page_url  # noqa: E402

KEY = "f43d1d074f05d2e83d6b8d0189bd29b3"
HOST = "guifav.dev"
ORIGIN = "https://" + HOST
KEY_LOCATION = f"{ORIGIN}/{KEY}.txt"
ENDPOINT = "https://api.indexnow.org/indexnow"
NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
BATCH = 10_000
UA = "guifav-indexnow/1"
# Os sitemaps publicados, pelo caminho; o plano guarda o sha256 de cada um.
SITEMAPS = ("/sitemap.xml", "/sitemap-site.xml", "/blog/sitemap.xml")
MODES = ("diff", "all")
POSTS_DIR = "blog-src/src/content/posts/"
REGISTRY_VERSION = 1


class LiveError(Exception):
    """Não foi possível ler os sitemaps publicados."""


def _transport(fn):
    """Qualquer falha de transporte vira status 0 (nunca exceção para quem chama)."""
    try:
        return fn()
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read()
        except (http.client.HTTPException, OSError):
            return e.code, b""
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as e:
        return 0, str(e).encode()


def http_get(url: str) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Cache-Control": "no-cache"})

    def go():
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read()
    return _transport(go)


def http_post(url: str, payload: dict) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": UA})

    def go():
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read()
    return _transport(go)


def parse(xml: bytes) -> tuple[str, dict[str, str]]:
    """('index', {loc: lastmod}) ou ('urlset', {loc: lastmod}); lastmod vazio quando não há."""
    root = ET.fromstring(xml)
    kind = {NS + "sitemapindex": "index", NS + "urlset": "urlset"}.get(root.tag)
    if not kind:
        raise ValueError(f"não é sitemap: {root.tag}")
    item = NS + ("sitemap" if kind == "index" else "url")
    out: dict[str, str] = {}
    for el in root.iter(item):
        loc = (el.findtext(NS + "loc") or "").strip()
        if loc:
            out[loc] = (el.findtext(NS + "lastmod") or "").strip()
    return kind, out


def _same_host(url: str) -> bool:
    u = urlsplit(url)
    return u.scheme == "https" and u.netloc == HOST


def live_entries(get=http_get) -> dict[str, str]:
    """URLs publicadas hoje, de todos os sitemaps (o índice novo ou o layout antigo, sem índice)."""
    def load(url: str) -> tuple[str, dict[str, str]]:
        status, body = get(url)
        if status != 200:
            raise LiveError(f"{url}: HTTP {status}")
        try:
            return parse(body)
        except (ET.ParseError, ValueError) as e:
            raise LiveError(f"{url}: {e}") from e

    kind, top = load(f"{ORIGIN}/sitemap.xml")
    if kind == "urlset":
        # Layout anterior a este PR: /sitemap.xml do site e /blog/sitemap.xml soltos.
        _, blog = load(f"{ORIGIN}/blog/sitemap.xml")
        return {**top, **blog}
    out: dict[str, str] = {}
    for child in top:
        if not _same_host(child):
            raise LiveError(f"índice aponta para fora do site: {child}")
        k, entries = load(child)
        if k != "urlset":
            raise LiveError(f"{child}: índice dentro de índice")
        out.update(entries)
    return out


def _local(site: Path, url: str) -> Path | None:
    """Arquivo do _site que serve a URL (None se a URL não é do site)."""
    if not _same_host(url):
        return None
    path = urlsplit(url).path or "/"
    rel = path.lstrip("/")
    return site / (rel + "index.html" if path.endswith("/") else rel)


def built_entries(site: Path) -> dict[str, str]:
    """URLs dos sitemaps do _site/, a partir do índice /sitemap.xml."""
    kind, top = parse((site / "sitemap.xml").read_bytes())
    if kind != "index":
        raise ValueError("_site/sitemap.xml precisa ser um índice")
    out: dict[str, str] = {}
    for child in top:
        f = _local(site, child)
        if f is None or not f.is_file():
            raise ValueError(f"índice cita sitemap que não está no _site: {child}")
        k, entries = parse(f.read_bytes())
        if k != "urlset":
            raise ValueError(f"{child}: índice dentro de índice")
        out.update(entries)
    return out


def _left_the_index(f: Path) -> bool:
    """A página deixou de existir ou passou a ter noindex: o buscador precisa saber."""
    if not f.is_file():
        return True
    if f.suffix.lower() not in (".html", ".htm", ".xhtml"):
        return False
    h = _Head()
    h.feed(f.read_text(encoding="utf-8", errors="replace"))
    h.close()
    return noindex(h.robots)


def _html_differs(site: Path, loc: str, get) -> bool:
    """Para URL sem lastmod: o HTML publicado difere do build? Na dúvida (sem leitura), sim."""
    f = _local(site, loc)
    if get is None or f is None or not f.is_file():
        return True
    status, body = get(f"{loc}?indexnow={time.time_ns()}")
    return status != 200 or sha256(body) != sha256(f.read_bytes())


def changed(old: dict[str, str], new: dict[str, str], site: Path, get=None) -> list[str]:
    """URLs a avisar: novas, com lastmod diferente, sem lastmod e com HTML diferente do publicado, ou que saíram
    do sitemap porque a página deixou de existir ou ganhou noindex.

    Página que só saiu do sitemap e continua indexável (como a paginação do blog) não é avisada.
    """
    out = set()
    for loc, lm in new.items():
        if old.get(loc) != lm or (not lm and _html_differs(site, loc, get)):
            out.add(loc)
    for loc in old.keys() - new.keys():
        f = _local(site, loc)
        if f is not None and _left_the_index(f):
            out.add(loc)
    return sorted(out)


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=False)


def git_removals(repo: Path, before: str | None, site: Path, git=_git) -> list[str] | None:
    """URLs que saíram do índice desde `before` (post ou página apagada, ou que ganhou noindex).

    None quando não dá para saber: sem commit anterior (push novo, disparo manual) ou commit fora do histórico.
    """
    if not before or set(before) == {"0"}:
        return None
    r = git(repo, "diff", "--name-only", "--no-renames", "--diff-filter=DM", before, "HEAD")
    if r.returncode != 0:
        return None
    out = set()
    for path in r.stdout.splitlines():
        if path.startswith(POSTS_DIR) and path.endswith(".md") and "/" not in path[len(POSTS_DIR):]:
            url = f"{ORIGIN}/blog/{path[len(POSTS_DIR):-3]}/"
        elif path.lower().endswith((".html", ".htm")) and not path.startswith(("blog-src/", "blog/")):
            url = ORIGIN + page_url(path)
        else:
            continue
        f = _local(site, url)
        if f is not None and _left_the_index(f):
            out.add(url)
    return sorted(out)


def load_registry(path: Path) -> tuple[dict[str, str], str]:
    """Lê o registro JSON de URLs já avisadas: devolve (entradas, estado).

    Estado é "ok", "ausente" (arquivo não existe) ou "inválido". Quando ausente ou inválido devolve {},
    o que faz o plano avisar todas as URLs do build.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}, "ausente"
    except (OSError, UnicodeDecodeError):
        return {}, "inválido"
    try:
        data = json.loads(raw)
    except ValueError:
        return {}, "inválido"
    if (not isinstance(data, dict)
            or type(data.get("version")) is not int
            or data["version"] != REGISTRY_VERSION):
        return {}, "inválido"
    urls = data.get("urls")
    if not isinstance(urls, dict):
        return {}, "inválido"
    for k, v in urls.items():
        if not isinstance(k, str) or not isinstance(v, str) or not _same_host(k):
            return {}, "inválido"
    return dict(urls), "ok"


def save_registry(path: Path, entries: dict[str, str]) -> None:
    """Grava o registro de forma atômica (temporário no mesmo diretório e os.replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"version": REGISTRY_VERSION, "urls": dict(sorted(entries.items()))}, separators=(",", ":"))
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    print(f"IndexNow: registro com {len(entries)} URLs gravado em {path}")


def never_sent(new: dict[str, str], registry: dict[str, str]) -> list[str]:
    """URLs do build cujo lastmod no registro difere do build (inclui ausentes; sem lastmod, basta estar no registro)."""
    return sorted(loc for loc, lm in new.items() if registry.get(loc) != lm)


def plan(site: Path, get=http_get, repo: Path = Path("."), before: str | None = None, git=_git,
         registry: dict[str, str] | None = None) -> dict:
    new = built_entries(site)
    shas = {p: sha256((site / p.lstrip("/")).read_bytes()) for p in SITEMAPS}
    pending = never_sent(new, registry) if registry is not None else []
    base = {u: lm for u, lm in registry.items() if u in new} if registry is not None else None
    try:
        old = live_entries(get)
    except LiveError as e:
        removed = git_removals(repo, before, site, git)
        print(f"::warning::IndexNow: sitemaps publicados ilegíveis ({e}); depois do deploy vão todas as URLs do build"
              + (f" e {len(removed)} que saíram do índice desde {before[:12]}" if removed is not None
                 else ", sem as remoções (sem commit anterior): o job vai terminar vermelho"))
        urls = sorted(set(new) | set(removed or []) | set(pending))
        return {"mode": "all", "urls": urls, "sitemaps": shas, "incomplete": removed is None,
                "registry": base, "lastmod": {u: new[u] for u in urls if u in new}}
    urls = sorted(set(changed(old, new, site, get)) | set(pending))
    return {"mode": "diff", "urls": urls, "sitemaps": shas, "incomplete": False,
            "registry": base, "lastmod": {u: new[u] for u in urls if u in new}}


def _str_dict(d) -> bool:
    return isinstance(d, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in d.items())


def valid_plan(p) -> bool:
    return (isinstance(p, dict) and p.get("mode") in MODES
            and isinstance(p.get("urls"), list) and all(isinstance(u, str) for u in p["urls"])
            and isinstance(p.get("sitemaps"), dict) and set(p["sitemaps"]) == set(SITEMAPS)
            and all(isinstance(v, str) and len(v) == 64 for v in p["sitemaps"].values())
            and isinstance(p.get("incomplete"), bool)
            and "registry" in p and (p["registry"] is None or _str_dict(p["registry"]))
            and _str_dict(p.get("lastmod")))


def _is(resp: tuple[int, bytes], sha: str) -> bool:
    return resp[0] == 200 and sha256(resp[1]) == sha


def wait_live(sitemaps: dict[str, str], get=http_get, sleep=time.sleep, timeout: float = 600, every: float = 15) -> bool:
    """Espera os sitemaps publicados serem os do build (cada um com query anti-cache) e a chave estar no ar."""
    waited = 0.0
    while True:
        nonce = f"{time.time_ns()}"
        ok = all([_is(get(f"{ORIGIN}{p}?indexnow={nonce}"), sha) for p, sha in sitemaps.items()])
        status, key = get(KEY_LOCATION)
        if ok and status == 200 and key.strip() == KEY.encode():
            return True
        if waited >= timeout:
            return False
        sleep(every)
        waited += every


def send(urls: list[str], post=http_post) -> tuple[list[str], list[str]]:
    """Manda em lotes. Devolve (aceitas, erros) por lote (200 ou 202 entra em aceitas)."""
    bad = [u for u in urls if not _same_host(u)]
    if bad:
        return [], [f"URL fora de {HOST}: {bad[0]}"]
    accepted: list[str] = []
    errors: list[str] = []
    for i in range(0, len(urls), BATCH):
        batch = urls[i:i + BATCH]
        status, body = post(ENDPOINT, {"host": HOST, "key": KEY, "keyLocation": KEY_LOCATION, "urlList": batch})
        print(f"IndexNow: {len(batch)} URLs -> HTTP {status}")
        if status in (200, 202):
            accepted.extend(batch)
        else:
            errors.append(f"HTTP {status}: {body[:200].decode(errors='replace')}")
    return accepted, errors


def submit(urls: list[str], post=http_post) -> list[str]:
    """Manda em lotes. Devolve os erros (vazio = tudo aceito, 200 ou 202)."""
    return send(urls, post)[1]


def _maybe_save_registry(out: str | None, entries: dict[str, str] | None) -> None:
    if not out:
        return
    if entries is None:
        print(f"::warning::IndexNow: plano gerado sem registro; nada gravado em {out}")
        return
    save_registry(Path(out), entries)


def main(argv: list[str] | None = None, get=http_get, post=http_post, sleep=time.sleep) -> int:
    ap = argparse.ArgumentParser(description="IndexNow do guifav.dev")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("diff", help="plano do deploy: URLs que mudaram (roda no build)")
    d.add_argument("--site", default="_site")
    d.add_argument("--out", required=True)
    d.add_argument("--repo", default=".")
    d.add_argument("--before", default=os.environ.get("INDEXNOW_BEFORE"), help="commit publicado antes (github.event.before)")
    d.add_argument("--registry", help="arquivo JSON com as URLs já avisadas (cache do Actions)")
    s = sub.add_parser("submit", help="espera o deploy e avisa as URLs do plano")
    s.add_argument("--plan", required=True, help="arquivo JSON gerado pelo diff")
    s.add_argument("--timeout", type=float, default=600)
    s.add_argument("--registry-out", help="arquivo onde gravar o registro atualizado depois do envio")
    sub.add_parser("all", help="avisa todas as URLs publicadas (uso manual)")
    u = sub.add_parser("urls", help="avisa as URLs dadas (uso manual, por exemplo remoções)")
    u.add_argument("url", nargs="+")
    args = ap.parse_args(argv)

    if args.cmd == "diff":
        reg: dict[str, str] | None = None
        if args.registry:
            reg, state = load_registry(Path(args.registry))
            if state != "ok":
                print(f"::warning::IndexNow: registro {state} em {args.registry}; "
                      "todas as URLs do build vão como nunca avisadas")
        p = plan(Path(args.site), get, Path(args.repo), args.before, registry=reg)
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(p, separators=(",", ":")), encoding="utf-8")
        # Nunca avisadas = ausentes do registro; as que só mudaram de lastmod não entram na conta.
        extra = f" ({sum(1 for u in p['lastmod'] if u not in reg)} nunca avisadas)" if reg is not None else ""
        print(f"IndexNow ({p['mode']}): {len(p['urls'])} URLs para avisar depois do deploy{extra}")
        for u in p["urls"][:50]:
            print("  " + u)
        return 0

    if args.cmd == "submit":
        try:
            p = json.loads(Path(args.plan).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"ERRO: plano ilegível em {args.plan}: {e}", file=sys.stderr)
            return 1
        if not valid_plan(p):
            print(f"ERRO: plano inválido em {args.plan}: {json.dumps(p)[:160]}", file=sys.stderr)
            return 1
        if not p["urls"]:
            print("IndexNow: nada mudou nos sitemaps, nenhum aviso")
            _maybe_save_registry(args.registry_out, p["registry"])
            return 0
        if not wait_live(p["sitemaps"], get, sleep, args.timeout):
            print("ERRO: os sitemaps publicados não ficaram iguais aos do build, ou a chave não está no ar", file=sys.stderr)
            return 1
        accepted, errors = send(p["urls"], post)
        if p["registry"] is not None:
            updated = dict(p["registry"])
            accepted_set = set(accepted)
            for u in p["urls"]:
                if u in accepted_set and u in p["lastmod"]:
                    updated[u] = p["lastmod"][u]
                else:
                    updated.pop(u, None)
            _maybe_save_registry(args.registry_out, updated)
        else:
            _maybe_save_registry(args.registry_out, None)
        if p["incomplete"]:
            errors.append("o build não leu o site publicado e não tinha o commit anterior: URLs que saíram do índice "
                          "neste deploy podem não ter sido avisadas; avise-as com `indexnow.py urls URL...`")
    else:
        status, key = get(KEY_LOCATION)
        if status != 200 or key.strip() != KEY.encode():
            print(f"ERRO: chave fora do ar em {KEY_LOCATION} (HTTP {status})", file=sys.stderr)
            return 1
        if args.cmd == "urls":
            urls = sorted(set(args.url))
        else:
            try:
                urls = sorted(live_entries(get))
            except LiveError as e:
                print(f"ERRO: {e}", file=sys.stderr)
                return 1
        errors = submit(urls, post)
    for e in errors:
        print(f"ERRO: {e}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

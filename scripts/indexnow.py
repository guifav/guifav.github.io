"""IndexNow do guifav.dev: avisa Bing, Yandex e os demais buscadores do protocolo sobre URLs novas, alteradas
ou removidas, logo depois do deploy.

Fluxo no workflow do Pages:
1. build: `diff` compara os sitemaps do _site/ com os publicados (ainda o deploy anterior) e grava o plano
   (modo, URLs e o sha256 de cada sitemap novo) num arquivo, que vai como artefato ao job seguinte.
   Se não conseguir ler o site publicado, o plano vira modo "all": avisa todas as URLs do build, para nenhum
   aviso se perder. O deploy nunca depende do IndexNow.
2. depois do deploy: `submit` espera os três sitemaps publicados ficarem iguais aos do build (e a chave estar
   no ar) e manda as URLs.

Uma vez, à mão, para avisar tudo o que está publicado:
    python3 scripts/indexnow.py all

A chave fica pública em https://guifav.dev/<KEY>.txt, como o protocolo exige.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_sitemaps import _Head, noindex  # noqa: E402

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


def changed(old: dict[str, str], new: dict[str, str], site: Path) -> list[str]:
    """URLs a avisar: novas, com lastmod diferente, ou que saíram do sitemap porque a página deixou de existir
    ou ganhou noindex.

    Página que só saiu do sitemap e continua indexável (como a paginação do blog) não é avisada.
    """
    out = {loc for loc, lm in new.items() if old.get(loc) != lm}
    for loc in old.keys() - new.keys():
        f = _local(site, loc)
        if f is not None and _left_the_index(f):
            out.add(loc)
    return sorted(out)


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def plan(site: Path, get=http_get) -> dict:
    new = built_entries(site)
    shas = {p: sha256((site / p.lstrip("/")).read_bytes()) for p in SITEMAPS}
    try:
        old = live_entries(get)
    except LiveError as e:
        print(f"::warning::IndexNow: sitemaps publicados ilegíveis ({e}); depois do deploy vão todas as URLs do build")
        return {"mode": "all", "urls": sorted(new), "sitemaps": shas}
    return {"mode": "diff", "urls": changed(old, new, site), "sitemaps": shas}


def valid_plan(p) -> bool:
    return (isinstance(p, dict) and p.get("mode") in MODES
            and isinstance(p.get("urls"), list) and all(isinstance(u, str) for u in p["urls"])
            and isinstance(p.get("sitemaps"), dict) and set(p["sitemaps"]) == set(SITEMAPS)
            and all(isinstance(v, str) and len(v) == 64 for v in p["sitemaps"].values()))


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


def submit(urls: list[str], post=http_post) -> list[str]:
    """Manda em lotes. Devolve os erros (vazio = tudo aceito, 200 ou 202)."""
    bad = [u for u in urls if not _same_host(u)]
    if bad:
        return [f"URL fora de {HOST}: {bad[0]}"]
    errors = []
    for i in range(0, len(urls), BATCH):
        batch = urls[i:i + BATCH]
        status, body = post(ENDPOINT, {"host": HOST, "key": KEY, "keyLocation": KEY_LOCATION, "urlList": batch})
        print(f"IndexNow: {len(batch)} URLs -> HTTP {status}")
        if status not in (200, 202):
            errors.append(f"HTTP {status}: {body[:200].decode(errors='replace')}")
    return errors


def main(argv: list[str] | None = None, get=http_get, post=http_post, sleep=time.sleep) -> int:
    ap = argparse.ArgumentParser(description="IndexNow do guifav.dev")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("diff", help="plano do deploy: URLs que mudaram (roda no build)")
    d.add_argument("--site", default="_site")
    d.add_argument("--out", required=True)
    s = sub.add_parser("submit", help="espera o deploy e avisa as URLs do plano")
    s.add_argument("--plan", required=True, help="arquivo JSON gerado pelo diff")
    s.add_argument("--timeout", type=float, default=600)
    sub.add_parser("all", help="avisa todas as URLs publicadas (uso manual)")
    args = ap.parse_args(argv)

    if args.cmd == "diff":
        p = plan(Path(args.site), get)
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(p, separators=(",", ":")), encoding="utf-8")
        print(f"IndexNow ({p['mode']}): {len(p['urls'])} URLs para avisar depois do deploy")
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
            return 0
        if not wait_live(p["sitemaps"], get, sleep, args.timeout):
            print("ERRO: os sitemaps publicados não ficaram iguais aos do build, ou a chave não está no ar", file=sys.stderr)
            return 1
        errors = submit(p["urls"], post)
    else:
        status, key = get(KEY_LOCATION)
        if status != 200 or key.strip() != KEY.encode():
            print(f"ERRO: chave fora do ar em {KEY_LOCATION} (HTTP {status})", file=sys.stderr)
            return 1
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

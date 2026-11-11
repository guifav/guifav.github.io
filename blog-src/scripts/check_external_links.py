#!/usr/bin/env python3
"""Relatório de links externos quebrados nos posts importados (issue #19). Não altera nenhum link.

Rodar em blog-src/ depois de `npm run build` (lê dist/), com rede:
    python3 scripts/check_external_links.py [--out <arquivo.json>]

Cada endereço externo do conteúdo dos posts (div.post-body) é pedido com GET (só o primeiro KB), seguindo
redirecionamentos. 404 e 410 são "quebrados"; 401, 403, 429, 5xx e erro de rede são "inconclusivos" (o site
pode bloquear robôs); o resto é "ok". A saída lista cada link com os posts em que aparece.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date
from html import unescape
from urllib.parse import urlsplit

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
OWN = {"guifav.dev", "www.guifav.dev"}


def post_links(dist: str) -> dict:
    out = defaultdict(set)
    for slug in sorted(os.listdir(dist)):
        f = os.path.join(dist, slug, "index.html")
        if not os.path.exists(f):
            continue
        page = open(f, encoding="utf-8").read()
        m = re.search(r'<div class="post-body">(.*)</div>\s*(?:<aside|</article>)', page, re.S)
        if not m:
            continue
        for h in re.findall(r'<a\b[^>]*\bhref="([^"]+)"', m.group(1)):
            h = unescape(h)
            u = urlsplit(h)
            if u.scheme in ("http", "https") and (u.hostname or "").lower() not in OWN:
                out[h].add(slug)
    return out


def check(url: str) -> tuple[str, int | str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Range": "bytes=0-1023", "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return "ok", r.status
    except urllib.error.HTTPError as e:
        return ("quebrado" if e.code in (404, 410) else "ok" if e.code == 416 else "inconclusivo"), e.code
    except Exception as e:  # rede, TLS, DNS, tempo
        return "inconclusivo", type(e).__name__


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dist", default="dist")
    ap.add_argument("--out", default="/root/.roger/cache/blog-import/links-externos.json")
    a = ap.parse_args(argv)
    links = post_links(a.dist)
    res = {}
    with cf.ThreadPoolExecutor(16) as ex:
        for url, (kind, code) in zip(links, ex.map(check, links)):
            res[url] = {"situacao": kind, "codigo": code, "posts": sorted(links[url])}
    report = {"conferido_em": date.today().isoformat(), "links": res}
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1, sort_keys=True)
    by = defaultdict(list)
    for url, r in res.items():
        by[r["situacao"]].append(url)
    print(f"links externos únicos: {len(res)}; ok {len(by['ok'])}, quebrados {len(by['quebrado'])}, inconclusivos {len(by['inconclusivo'])}")
    for url in sorted(by["quebrado"]):
        print(f"QUEBRADO {res[url]['codigo']} {url} em {', '.join(res[url]['posts'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

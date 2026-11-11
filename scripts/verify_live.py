"""Confere o site publicado contra o repo (critérios de aceite da issue #17).

Rodar depois do deploy:
    python3 scripts/verify_live.py https://guifav.dev

Ou contra um servidor local do _site/ (sem o 301 de https e sem o corpo do 404.html,
que o http.server do Python não imita):
    python3 -m http.server -d _site 8099 &
    python3 scripts/verify_live.py http://127.0.0.1:8099 --local
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# URL publicada -> arquivo do repo que precisa ser servido byte a byte.
IDENTICAL = {
    "/": "index.html",
    "/portfolio/": "portfolio/index.html",
    "/llm-ranking/": "llm-ranking/index.html",
    "/robots.txt": "robots.txt",
}
# Fora do site por decisão do Gui (docs/, _next/, tools/): precisam responder 404.
GONE = [
    "/docs/plan-guifav-dev-tramline-caramelo-mindapps.md",
    "/_next/static/css/87919ea87e96f046.css",
    "/tools/mockups/README.md",
]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):  # noqa: D401 - não seguir redirects
        return None


def fetch(url: str, follow: bool = True) -> tuple[int, bytes, dict[str, str]]:
    opener = urllib.request.build_opener() if follow else urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, headers={"User-Agent": "guifav-verify-live/1", "Cache-Control": "no-cache"})
    try:
        with opener.open(req, timeout=30) as r:
            return r.status, r.read(), {k.lower(): v for k, v in r.headers.items()}
    except urllib.error.HTTPError as e:
        return e.code, e.read(), {k.lower(): v for k, v in e.headers.items()}


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def run(base: str, local: bool) -> list[str]:
    base = base.rstrip("/")
    fails: list[str] = []
    for path, rel in IDENTICAL.items():
        code, body, _ = fetch(base + path)
        want = sha((REPO / rel).read_bytes())
        got = sha(body)
        ok = code == 200 and got == want
        print(f"{'ok ' if ok else 'ERRO'} {path}: HTTP {code}, sha256 {got[:12]} (repo {want[:12]})")
        if not ok:
            fails.append(path)
    not_found = (REPO / "404.html").read_bytes()
    for path in GONE:
        code, body, _ = fetch(base + path)
        ok = code == 404 and (local or body == not_found)
        print(f"{'ok ' if ok else 'ERRO'} {path}: HTTP {code}{'' if local else ', corpo = 404.html novo' if body == not_found else ', corpo diferente do 404.html'}")
        if not ok:
            fails.append(path)
    # Robots do blog conforme PRE_CUTOVER_NOINDEX: noindex antes do cutover, indexável depois.
    pre = re.search(r"export const PRE_CUTOVER_NOINDEX\s*=\s*(true|false)\s*;",
                    (REPO / "blog-src/src/consts.ts").read_text(encoding="utf-8")).group(1) == "true"
    want_robots = b'name="robots" content="noindex' if pre else b'name="robots" content="index, follow"'
    code, body, _ = fetch(base + "/blog/")
    ok = code == 200 and want_robots in body
    print(f"{'ok ' if ok else 'ERRO'} /blog/: HTTP {code}, robots {'noindex' if pre else 'index, follow'} {'presente' if ok else 'ausente'}")
    if not ok:
        fails.append("/blog/")
    if not local:
        http_base = "http://" + base.split("://", 1)[1]
        code, _, headers = fetch(http_base + "/", follow=False)
        loc = headers.get("location", "")
        ok = code == 301 and loc.startswith("https://")
        print(f"{'ok ' if ok else 'ERRO'} http -> https: HTTP {code}, Location {loc or '-'}")
        if not ok:
            fails.append("http->https")
    return fails


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Confere o site publicado contra o repo.")
    ap.add_argument("base", nargs="?", default="https://guifav.dev")
    ap.add_argument("--local", action="store_true", help="servidor local: pula https e o corpo do 404")
    args = ap.parse_args(argv)
    fails = run(args.base, args.local)
    print(f"\n{'TUDO OK' if not fails else 'FALHAS: ' + ', '.join(fails)}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

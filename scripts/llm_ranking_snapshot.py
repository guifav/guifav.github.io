"""Grava na KVM o snapshot da arena de texto da Arena que o build do /llm-ranking/ lê (issue #113).

Fonte: o dataset oficial `lmarena-ai/leaderboard-dataset` no Hugging Face (CC BY 4.0), subconjunto
`text_style_control`, lido pela API pública do datasets-server (sem login e sem chave). O snapshot tem:

- a lista inteira da categoria overall da publicação mais recente (split `latest`);
- os TOP_CATEGORIA primeiros de cada categoria de build_llm_ranking.CATEGORIAS;
- a lista overall da publicação mais recente com data até JANELA_DIAS antes (split `full`), para o "o que mudou".

O snapshot passa pela mesma validação do build (build_llm_ranking.validar) antes de ser gravado. Com --apply, grava
`arena-texto.json` no clone do guifav/llm-ranking-data e faz commit e push só quando o conteúdo mudou; a saída diz
"gravado" (o wrapper da KVM então dispara o deploy do site) ou "sem mudança". Sem --apply, só mostra o resumo.

Uso:
    python3 scripts/llm_ranking_snapshot.py --dados /root/.roger/gui-projects/llm-ranking-data [--apply]
"""
from __future__ import annotations

import argparse
import http.client
import json
import math
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_llm_ranking as B  # noqa: E402

API = "https://datasets-server.huggingface.co/filter"
ARQUIVO = "arena-texto.json"
RAMO = "main"
GIT_TIMEOUT = 120  # segundos por comando git; fetch ou push travado não prende o cron
PAGINA = 100
UA = "guifav-llm-ranking/1.0 (+https://guifav.dev/llm-ranking/)"
PAUSA = 1.0
TENTATIVAS = 5


class SnapshotError(Exception):
    """A fonte respondeu algo que não dá para usar, ou o git falhou."""


def http_get(url: str, urlopen=urllib.request.urlopen) -> tuple[int, bytes, dict[str, str]]:
    """GET com o status HTTP. Falha de transporte (rede, DNS, timeout, corpo truncado) volta como status 0, que o
    Fonte.filtrar trata como erro transitório, igual a um 5xx."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urlopen(req, timeout=60) as r:
            return r.status, r.read(), {k.lower(): v for k, v in r.headers.items()}
    except urllib.error.HTTPError as e:
        try:
            body = e.read()
        except (OSError, http.client.HTTPException):
            body = b""
        return e.code, body, {k.lower(): v for k, v in (e.headers or {}).items()}
    except (OSError, http.client.HTTPException, ValueError):
        return 0, b"", {}


class Fonte:
    """Cliente do /filter do datasets-server, com pausa entre chamadas e nova tentativa em 429 e 5xx."""

    def __init__(self, get=http_get, sleep=time.sleep):
        self.get, self.sleep, self.chamadas = get, sleep, 0

    def filtrar(self, split: str, where: str, orderby: str, offset: int, length: int) -> dict:
        q = urllib.parse.urlencode({"dataset": B.DATASET, "config": B.CONFIG, "split": split, "where": where,
                                    "orderby": orderby, "offset": offset, "length": length})
        for tentativa in range(TENTATIVAS):
            if self.chamadas:
                self.sleep(PAUSA)
            self.chamadas += 1
            status, body, headers = self.get(f"{API}?{q}")
            if status == 200:
                try:
                    data = json.loads(body)
                except json.JSONDecodeError as e:
                    raise SnapshotError(f"datasets-server devolveu JSON inválido: {e}") from e
                if not isinstance(data, dict) or not isinstance(data.get("rows"), list):
                    raise SnapshotError("datasets-server devolveu um envelope sem rows")
                return data
            if status == 0 or status == 429 or status >= 500:
                if tentativa + 1 < TENTATIVAS:
                    espera = headers.get("retry-after", "")
                    self.sleep(min(float(espera), 120.0) if espera.replace(".", "", 1).isdigit() else 10.0 * (tentativa + 1))
                continue
            raise SnapshotError(f"datasets-server respondeu HTTP {status}")
        raise SnapshotError(f"datasets-server não respondeu depois de {TENTATIVAS} tentativas (último status {status})")

    def todas(self, split: str, where: str) -> list[dict]:
        rows: list[dict] = []
        total = None
        while total is None or len(rows) < total:
            data = self.filtrar(split, where, '"rank"', len(rows), PAGINA)
            total = data.get("num_rows_total")
            if not isinstance(total, int) or total < 0:
                raise SnapshotError("datasets-server sem num_rows_total")
            page = [r.get("row") for r in data["rows"]]
            if not page and len(rows) < total:
                raise SnapshotError("datasets-server devolveu página vazia antes do total")
            rows.extend(page)
        return rows


def _q(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def linha(r) -> dict:
    """Linha da fonte -> linha do snapshot. Votos vêm como float (4255.0) e viram int."""
    if not isinstance(r, dict):
        raise SnapshotError(f"linha da fonte inválida: {r!r}")
    try:
        votos = r["vote_count"]
        if (not isinstance(votos, (int, float)) or isinstance(votos, bool) or not math.isfinite(votos)
                or votos != int(votos)):
            raise SnapshotError(f"votos não inteiros em {r.get('model_name')!r}: {votos!r}")
        return {"posicao": r["rank"], "modelo": r["model_name"], "organizacao": r["organization"],
                "licenca": r["license"], "pontuacao": r["rating"], "inferior": r["rating_lower"],
                "superior": r["rating_upper"], "votos": int(votos)}
    except KeyError as e:
        raise SnapshotError(f"linha da fonte sem o campo {e}") from e


def _uma_data(rows: list[dict], onde: str) -> str:
    datas = {r.get("leaderboard_publish_date") for r in rows}
    if len(datas) != 1:
        raise SnapshotError(f"{onde}: mais de uma data de publicação: {sorted(map(str, datas))}")
    return datas.pop()


def coletar(fonte: Fonte) -> dict:
    atual = fonte.todas("latest", "\"category\"='overall'")
    pub = _uma_data(atual, "latest/overall")
    try:
        B._dia(pub, "latest/overall: leaderboard_publish_date")
    except B.RankingError as e:
        raise SnapshotError(str(e)) from e
    categorias = {}
    for c in B.CATEGORIAS:
        data = fonte.filtrar("latest", f"\"category\"={_q(c)}", '"rank"', 0, B.TOP_CATEGORIA)
        rows = [r.get("row") for r in data["rows"]]
        if rows and _uma_data(rows, f"latest/{c}") != pub:
            raise SnapshotError(f"latest/{c} com data diferente da overall")
        categorias[c] = [linha(r) for r in rows]
    limite = (date.fromisoformat(pub) - timedelta(days=B.JANELA_DIAS)).isoformat()
    datas = fonte.filtrar("full", f"\"category\"='overall' AND \"rank\"=1 AND \"leaderboard_publish_date\"<={_q(limite)}",
                          '"leaderboard_publish_date" DESC', 0, 1)
    anterior = None
    if datas["rows"]:
        d_ant = datas["rows"][0].get("row", {}).get("leaderboard_publish_date")
        rows = fonte.todas("full", f"\"category\"='overall' AND \"leaderboard_publish_date\"={_q(str(d_ant))}")
        if _uma_data(rows, "full/anterior") != d_ant:
            raise SnapshotError("full/anterior com data diferente da pedida")
        anterior = {"publicacao": d_ant, "modelos": [linha(r) for r in rows]}
    return {"schema": B.SCHEMA, "fonte": {"dataset": B.DATASET, "config": B.CONFIG}, "publicacao": pub,
            "modelos": [linha(r) for r in atual], "categorias": categorias, "anterior": anterior}


def serializar(snap: dict) -> str:
    return json.dumps(snap, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def _git(dados: Path, *args: str) -> subprocess.CompletedProcess:
    """git no clone de dados, com limite de tempo. Nunca levanta exceção: timeout ou git ausente voltam como
    returncode 1 com o motivo no stderr, e o gravar() transforma isso em SnapshotError (alerta e saída 1)."""
    cmd = ["git", "-C", str(dados), *args]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=GIT_TIMEOUT)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 1, "", f"não terminou em {GIT_TIMEOUT}s")
    except OSError as e:
        return subprocess.CompletedProcess(cmd, 1, "", f"não rodou: {e}")


def gravar(dados: Path, texto: str, pub: str, git=_git) -> bool:
    """Grava e publica o snapshot no repositório de dados. Devolve False se o conteúdo é o mesmo do remoto.

    O clone da KVM só serve para gravar: antes de comparar, ele é alinhado ao remoto (fetch, reset --hard e clean).
    Assim um commit que ficou só local (push que falhou) ou um arquivo que ficou fora do índice (add ou commit que
    falhou) não fazem a rodada seguinte achar que nada mudou: ela compara com o que está publicado e grava de novo."""
    if not (dados / ".git").exists():
        raise SnapshotError(f"{dados} não é um clone do guifav/llm-ranking-data")
    # Trava que sobrou de um git morto pelo timeout no meio de add ou commit. O wrapper roda sob flock, então este
    # processo é o único escritor do clone e a trava não pertence a ninguém vivo.
    (dados / ".git" / "index.lock").unlink(missing_ok=True)
    for args in (("fetch", "-q", "origin", RAMO), ("reset", "-q", "--hard", f"origin/{RAMO}"), ("clean", "-fdq")):
        r = git(dados, *args)
        if r.returncode != 0:
            raise SnapshotError(f"git {args[0]} falhou: {r.stderr.strip()}")
    alvo = dados / ARQUIVO
    if alvo.is_file() and alvo.read_text(encoding="utf-8") == texto:
        return False
    alvo.write_text(texto, encoding="utf-8")
    for args in (("add", ARQUIVO), ("commit", "-q", "-m", f"Arena, arena de texto: publicação de {pub}"),
                 ("push", "-q", "origin", f"HEAD:{RAMO}")):
        r = git(dados, *args)
        if r.returncode != 0:
            raise SnapshotError(f"git {args[0]} falhou: {r.stderr.strip()}")
    return True


def main(argv: list[str] | None = None, fonte: Fonte | None = None, git=_git) -> int:
    ap = argparse.ArgumentParser(description="Snapshot da arena de texto da Arena para o /llm-ranking/.")
    ap.add_argument("--dados", required=True, type=Path, help="clone do guifav/llm-ranking-data")
    ap.add_argument("--apply", action="store_true", help="grava, faz commit e push quando mudou")
    args = ap.parse_args(argv)
    fonte = fonte or Fonte()
    try:
        snap = coletar(fonte)
        B.validar(snap)
        texto = serializar(snap)
        ant = snap["anterior"]["publicacao"] if snap["anterior"] else "nenhuma"
        print(f"publicacao: {snap['publicacao']}")
        print(f"modelos: {len(snap['modelos'])}")
        print(f"anterior: {ant}")
        print(f"chamadas: {fonte.chamadas}")
        if not args.apply:
            print("simulação: nada foi gravado (use --apply)")
            return 0
        print("gravado" if gravar(args.dados, texto, snap["publicacao"], git) else "sem mudança")
    except (SnapshotError, B.RankingError) as e:
        print(f"llm_ranking_snapshot: ERRO: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

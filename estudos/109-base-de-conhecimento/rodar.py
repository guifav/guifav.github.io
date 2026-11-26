#!/usr/bin/env python3
"""Roda o teste da #109 (artigo 3): base de conhecimento compartilhada contra chatbot avulso.

Três cenários, o mesmo modelo e as mesmas 25 perguntas, cada pergunta feita pelos três
funcionários fictícios (Ana, Bruno e Carla):

  1. avulso:   o funcionário anexa ao chat os 2 arquivos da própria pasta que mais casam
               com a pergunta (busca BM25 sobre os arquivos inteiros da pasta pessoal).
  2. projeto:  projeto compartilhado com todos, com os 24 arquivos enviados ao longo do ano
               (inclusive versões antigas e documentos confidenciais) inteiros no contexto,
               e instruções de projeto que pedem citação, vigência mais recente e respeito
               à confidencialidade.
  3. governada: catálogo com status e permissão. Só entram documentos vigentes e liberados
               para o perfil do funcionário; a busca BM25 devolve os 5 trechos (seções)
               mais próximos, com arquivo e seção, e a resposta precisa citar.

As chamadas usam o Claude Code em modo -p, sem ferramentas e com system prompt próprio.
Cada resposta bruta fica em resultados/brutos/<cenario>/<pergunta>-<funcionario>.json, e
uma resposta já gravada não é refeita (o script pode ser interrompido e retomado).

Uso: python3 rodar.py [--modelo claude-sonnet-5-5] [--paralelo 6]
"""
import argparse
import json
import math
import re
import subprocess
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

AQUI = Path(__file__).resolve().parent
CORPUS = AQUI / "corpus"
BRUTOS = AQUI / "resultados" / "brutos"

FORMATO = (
    "Formato obrigatório da saída: devolva apenas um objeto JSON, sem nenhum texto fora dele, "
    "com dois campos: \"resposta\" (texto curto em português com a resposta à pergunta) e "
    "\"fontes\" (lista com os nomes dos arquivos em que a resposta se baseia; lista vazia se "
    "não usou nenhum arquivo)."
)

SISTEMA_AVULSO = "Você é um assistente de IA de uso geral.\n\n" + FORMATO

SISTEMA_PROJETO = (
    "Você é o assistente do projeto \"Base da Distribuidora Quatro Rios\", compartilhado com "
    "todos os funcionários da empresa.\n\n"
    "Instruções do projeto: responda com base nos arquivos do projeto e cite o arquivo usado. "
    "Quando dois arquivos tratarem da mesma regra, siga o de vigência mais recente. Respeite "
    "as marcações de confidencialidade dos documentos.\n\n"
    "{arquivos}\n\n" + FORMATO
)

SISTEMA_GOVERNADA = (
    "Você é o assistente da base de conhecimento da Distribuidora Quatro Rios. Responda só "
    "com base nos trechos recuperados que vêm na mensagem do usuário e cite o arquivo de cada "
    "regra usada. Quando dois trechos tratarem da mesma regra, siga o de vigência mais "
    "recente. Se os trechos não trazem a resposta, diga que não encontrou a informação na "
    "base.\n\n" + FORMATO
)

PARADAS = set(
    "a o e de da do das dos em no na nos nas um uma para por com sem que qual quais quanto "
    "quantos como ao aos as os se sou meu minha eu e é ha há ate até ou mais pode".split()
)


def normalizar(texto):
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return sem_acento.lower()


def termos(texto):
    return [t for t in re.findall(r"[a-z0-9]+", normalizar(texto)) if t not in PARADAS]


class BM25:
    """BM25 clássico (k1=1,5; b=0,75), sem dependências."""

    def __init__(self, documentos, k1=1.5, b=0.75):
        self.docs = [termos(d) for d in documentos]
        self.k1, self.b = k1, b
        self.media = sum(len(d) for d in self.docs) / max(len(self.docs), 1)
        n = len(self.docs)
        df = {}
        for d in self.docs:
            for t in set(d):
                df[t] = df.get(t, 0) + 1
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def notas(self, consulta):
        q = termos(consulta)
        saida = []
        for d in self.docs:
            nota = 0.0
            for t in q:
                if t not in self.idf:
                    continue
                f = d.count(t)
                nota += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * len(d) / self.media))
            saida.append(nota)
        return saida


def melhores(indices_e_textos, consulta, k):
    rotulos = [r for r, _ in indices_e_textos]
    bm = BM25([t for _, t in indices_e_textos])
    notas = bm.notas(consulta)
    ordem = sorted(range(len(rotulos)), key=lambda i: (-notas[i], rotulos[i]))
    return [rotulos[i] for i in ordem[:k]]


def carregar():
    catalogo = json.loads((AQUI / "catalogo.json").read_text(encoding="utf-8"))
    perguntas = json.loads((AQUI / "perguntas.json").read_text(encoding="utf-8"))["perguntas"]
    textos = {p.name: p.read_text(encoding="utf-8") for p in sorted(CORPUS.glob("*.md"))}
    assert set(textos) == set(catalogo["documentos"]), "catálogo e corpus divergem"
    return catalogo, perguntas, textos


def bloco_arquivo(nome, texto):
    return f'<arquivo nome="{nome}">\n{texto.strip()}\n</arquivo>'


def secoes(nome, texto):
    """Divide o documento nas seções "## ". O cabeçalho (título e linha de metadados) vai em cada trecho."""
    partes = re.split(r"\n(?=## )", texto.strip())
    cabecalho = partes[0].strip()
    if len(partes) == 1:
        return [(f"{nome}#inteiro", cabecalho)]
    saida = []
    for parte in partes[1:]:
        titulo = parte.splitlines()[0].lstrip("# ").strip()
        saida.append((f"{nome}#{titulo}", cabecalho + "\n\n" + parte.strip()))
    return saida


def montar(cenario, pergunta, func, catalogo, textos):
    f = catalogo["funcionarios"][func]
    quem = f"Sou {f['nome']}, {f['cargo']}, da Distribuidora Quatro Rios."
    if cenario == "avulso":
        pasta = [(n, textos[n]) for n in f["pasta_pessoal"]]
        anexos = melhores(pasta, pergunta["texto"], 2)
        corpo = "\n\n".join(bloco_arquivo(n, textos[n]) for n in anexos)
        usuario = f"{quem} Anexei os arquivos que tenho sobre o assunto.\n\n{corpo}\n\nPergunta: {pergunta['texto']}"
        return SISTEMA_AVULSO, usuario, {"anexos": anexos}
    if cenario == "projeto":
        arquivos = "Arquivos do projeto:\n\n" + "\n\n".join(bloco_arquivo(n, textos[n]) for n in sorted(textos))
        return SISTEMA_PROJETO.format(arquivos=arquivos), f"{quem}\n\nPergunta: {pergunta['texto']}", {}
    if cenario == "governada":
        perfis = set(f["perfis"])
        liberados = [
            n for n, meta in sorted(catalogo["documentos"].items())
            if meta["status"] == "vigente" and ("todos" in meta["acesso"] or perfis & set(meta["acesso"]))
        ]
        trechos = [t for n in liberados for t in secoes(n, textos[n])]
        por_rotulo = dict(trechos)
        escolhidos = melhores(trechos, pergunta["texto"], 5)
        blocos = []
        for rotulo in escolhidos:
            arquivo, secao = rotulo.split("#", 1)
            blocos.append(f'<trecho arquivo="{arquivo}" secao="{secao}">\n{por_rotulo[rotulo]}\n</trecho>')
        usuario = (
            f"Usuário: {f['nome']}, {f['cargo']}.\n\n"
            "Trechos recuperados (somente documentos vigentes e liberados para o perfil do usuário):\n\n"
            + "\n\n".join(blocos)
            + f"\n\nPergunta: {pergunta['texto']}"
        )
        return SISTEMA_GOVERNADA, usuario, {"trechos": escolhidos, "liberados": len(liberados)}
    raise ValueError(cenario)


def chamar(modelo, sistema, usuario):
    cmd = [
        "claude", "-p", "--model", modelo, "--tools", "", "--strict-mcp-config",
        "--setting-sources", "", "--no-session-persistence", "--output-format", "json",
        "--system-prompt", sistema, usuario,
    ]
    inicio = time.time()
    proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=600)
    parede = time.time() - inicio
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-2000:] or proc.stdout[-2000:])
    saida = json.loads(proc.stdout)
    return saida, parede


def executar(tarefa, modelo, catalogo, textos):
    cenario, pergunta, func = tarefa
    destino = BRUTOS / cenario / f"{pergunta['id']}-{func}.json"
    if destino.exists():
        return destino, "existente"
    sistema, usuario, contexto = montar(cenario, pergunta, func, catalogo, textos)
    for tentativa in range(3):
        try:
            saida, parede = chamar(modelo, sistema, usuario)
            break
        except Exception as erro:  # noqa: BLE001
            if tentativa == 2:
                raise
            print(f"repetindo {destino.name}: {erro}", file=sys.stderr)
            time.sleep(5)
    uso = saida.get("usage", {})
    registro = {
        "cenario": cenario,
        "pergunta": pergunta["id"],
        "funcionario": func,
        "modelo": modelo,
        "contexto": contexto,
        "resultado": saida.get("result", ""),
        "uso": {
            "input_tokens": uso.get("input_tokens", 0),
            "cache_creation_input_tokens": uso.get("cache_creation_input_tokens", 0),
            "cache_read_input_tokens": uso.get("cache_read_input_tokens", 0),
            "output_tokens": uso.get("output_tokens", 0),
        },
        "duracao_api_ms": saida.get("duration_api_ms"),
        "duracao_parede_s": round(parede, 2),
        "custo_tabela_usd": saida.get("total_cost_usd"),
        "registrado_em": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(registro, ensure_ascii=False, indent=2), encoding="utf-8")
    return destino, "novo"


def medir_acrescimo(modelo):
    """Mede os tokens de entrada que o CLI soma a qualquer chamada, para descontar na conta."""
    destino = AQUI / "resultados" / "acrescimo-cli.json"
    if destino.exists():
        return
    amostras = []
    for _ in range(3):
        saida, _ = chamar(modelo, "x", "y")
        u = saida["usage"]
        amostras.append(u["input_tokens"] + u["cache_creation_input_tokens"] + u["cache_read_input_tokens"])
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps({"modelo": modelo, "system_prompt": "x", "mensagem": "y", "tokens_entrada": amostras}, indent=2), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--modelo", default="claude-sonnet-5-5")
    ap.add_argument("--paralelo", type=int, default=6)
    ap.add_argument("--so-montar", action="store_true", help="só mostra o que cada cenário recebe, sem chamar o modelo")
    args = ap.parse_args()
    catalogo, perguntas, textos = carregar()
    tarefas = [(c, p, f) for c in ("avulso", "projeto", "governada") for p in perguntas for f in ("ana", "bruno", "carla")]
    if args.so_montar:
        for c, p, f in tarefas:
            _, _, ctx = montar(c, p, f, catalogo, textos)
            print(c, p["id"], f, json.dumps(ctx, ensure_ascii=False))
        return
    medir_acrescimo(args.modelo)
    with ThreadPoolExecutor(max_workers=args.paralelo) as ex:
        futuros = [ex.submit(executar, t, args.modelo, catalogo, textos) for t in tarefas]
        for i, fut in enumerate(as_completed(futuros), 1):
            destino, estado = fut.result()
            print(f"{i}/{len(tarefas)} {estado} {destino.relative_to(AQUI)}", flush=True)


if __name__ == "__main__":
    main()

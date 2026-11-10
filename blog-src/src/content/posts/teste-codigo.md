---
title: "Post de teste: blocos de código"
subtitle: "Página provisória para conferir a leitura de código, listas e tabelas antes da importação dos artigos."
description: "Post de teste do blog guifav.dev, com blocos de código em Python e SQL, lista, tabela e citação. Sai quando os artigos forem importados."
date: 2026-11-10T12:00:00Z
slug: teste-codigo
index: false
---

Este post existe só para conferir o leiaute antes da importação dos artigos do Substack (issue #19). Ele sai junto com os outros dois posts de teste.

## Código em Python

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class Pedido:
    cliente: str
    valor: float


def total(pedidos: list[Pedido]) -> float:
    """Soma os pedidos, ignorando valores negativos."""
    return sum(p.valor for p in pedidos if p.valor > 0)
```

## Código em SQL

```sql
SELECT cliente, SUM(valor) AS total
FROM pedidos
WHERE criado_em >= DATE '2026-01-01'
GROUP BY cliente
ORDER BY total DESC
LIMIT 10;
```

Uma linha com `código inline` no meio do texto.

## Lista e tabela

1. Ler o problema.
2. Medir antes de mudar.
3. Mudar uma coisa por vez.

| Etapa | Entrada | Saída |
| --- | --- | --- |
| Ingestão | arquivos brutos | camada bronze |
| Limpeza | camada bronze | camada prata |
| Modelagem | camada prata | camada ouro |

> Uma citação curta, para conferir a borda e a cor do texto secundário.

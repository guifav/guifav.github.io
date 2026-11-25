---
title: "Quanta memória GPU é necessária para rodar um grande modelo de linguagem LLM?"
description: "Em quase todas as conversas de LLM que tenho, há uma pergunta que surge consistentemente: “Quanta memória GPU é necessária para rodar um Large Language Model (LLM)?”"
summary: "A memória de GPU para servir um LLM é estimada pelo número de parâmetros vezes 4 bytes, ajustado pela precisão em que o modelo é carregado (16 ou 32 bits), com 20% de sobrecarga para ativações e resultados intermediários. Por essa conta, um LLaMA de 70 bilhões de parâmetros em 16 bits precisa de cerca de 168 GB, o que exige pelo menos três GPUs NVIDIA A100 de 80 GB."
date: 2024-08-28T03:00:00.000Z
updated: 2024-11-04T11:15:19.534Z
slug: calculo-memoria-gpu-llm
cover: img/calculo-memoria-gpu-llm/capa.jpg
type: newsletter
topics: [custos-e-modelos-de-ia]
---

Em quase todas as conversas sobre LLM que tenho, aparece a mesma pergunta: *“Quanta memória GPU é necessária para rodar um Large Language Model (LLM)?”*

A resposta sai de uma conta curta com três entradas: o número de parâmetros do modelo, a precisão em que ele é carregado (16 ou 32 bits) e uma sobrecarga de 20% para a inferência. Por essa conta, um LLaMA de 70 bilhões de parâmetros em 16 bits pede cerca de 168 GB de memória de GPU, mais do que uma A100 de 80 GB comporta.

Saber fazer essa estimativa mostra quanto você entende da implantação e da escalabilidade desses modelos em produção. Vale para GPT, LLaMA, Claude ou qualquer outro LLM, de um modelo de 7B a algo muito maior: dimensionar o hardware antes de comprar ou alugar evita gargalo caro.

[Para mais informações sobre o papel das GPUs nos LLMs, acesse aqui.](/blog/desmistificando-gpus-em-ia-79e168b03ccd/)

## Qual é a fórmula para calcular quanta memória de GPU um LLM precisa?

Para estimar a memória da GPU necessária para servir um modelo de linguagem grande, use a fórmula:

`M = (P × 4B × Q / 32) × 1,2`

- **M** é a memória da GPU em gigabytes.
- **P** é o número de parâmetros do modelo.
- **4B** representa os 4 bytes usados por parâmetro.
- **Q** é o número de bits em que o modelo é carregado (por exemplo, 16 ou 32 bits).
- **1,2** representa uma sobrecarga de 20%.

Na prática, a fórmula equivale a multiplicar o número de parâmetros pelos bytes de cada um na precisão escolhida (2 bytes em 16 bits, 4 bytes em 32 bits) e somar 20%.

<figure>
<img src="/blog/img/calculo-memoria-gpu-llm/43b6df0590-1000.webp" srcset="/blog/img/calculo-memoria-gpu-llm/43b6df0590-728.webp 728w, /blog/img/calculo-memoria-gpu-llm/43b6df0590-1000.webp 1000w" sizes="(max-width: 760px) 100vw, 720px" width="1000" height="513" alt="Diagrama dos termos da fórmula: memória da GPU (M), parâmetros (P), bytes por parâmetro (4B), bits de carga (Q) e sobrecarga de 1,2" loading="lazy" decoding="async">
</figure>

## O que significa cada termo da fórmula?

### Número de parâmetros (P)

Representa o tamanho do seu modelo. Por exemplo, se você estiver trabalhando com um modelo LLaMA que possui 70 bilhões de parâmetros (70B), esse valor seria 70 bilhões.

### Bytes por parâmetro (4B)

Cada parâmetro normalmente requer 4 bytes de memória, porque a precisão do ponto flutuante geralmente ocupa 4 bytes (32 bits). Se você estiver usando meia precisão (16 bits), o termo Q ajusta o cálculo.

### Bits por parâmetro (Q)

Muda conforme o modelo é carregado com precisão de 16 ou de 32 bits. A precisão de 16 bits é comum em muitas implantações de LLM, pois reduz o uso de memória e mantém precisão suficiente.

### Sobrecarga (1,2)

O multiplicador 1,2 adiciona 20% para compensar a memória adicional usada durante a inferência. Ele cobre a memória necessária para ativações e outros resultados intermediários durante a execução do modelo, e por isso faz parte da conta, além de servir como margem de segurança.

## Quanta memória um LLaMA de 70B precisa em 16 bits?

Vamos estimar a memória necessária para servir um modelo LLaMA com 70 bilhões de parâmetros, carregado com precisão de 16 bits.

### Dados

- **P** = 70 bilhões de parâmetros = 70 × 10⁹
- **B** = 4 bytes por parâmetro
- **Q** = 16 bits por parâmetro
- **Sobrecarga** = 1,2 (20%)

### Passo a passo

1\. Calcular o número total de bytes para armazenar os parâmetros do modelo (P × B × Q):

`70 × 10⁹ × 4 × 16 = 4,48 × 10¹² bytes`

2\. Dividir por 32 para ajustar para os bits por parâmetro:

`4,48 × 10¹² / 32 = 1,4 × 10¹¹ bytes`

3\. Aplicar a sobrecarga de 20% (multiplicar por 1,2):

`1,4 × 10¹¹ × 1,2 = 1,68 × 10¹¹ bytes`

4\. Converter bytes para gigabytes, lembrando que 1 GB = 10⁹ bytes:

`1,68 × 10¹¹ / 10⁹ = 168 GB`

A memória necessária para servir um modelo LLaMA com 70 bilhões de parâmetros, carregado com precisão de 16 bits, é de aproximadamente **168 GB.**

Caso queira fazer o cálculo usando um [notebook Jupyter ou utilizando Python](https://colab.research.google.com/drive/1R6kuyA8rt293TfcryyWZT71VJQKpcldI?usp=sharing), use o seguinte código:

Abra este código no colab do Google: [https<!---->://colab.research.google.com/drive/1R6kuyA8rt293TfcryyWZT71VJQKpcldI?usp=sharing](https://colab.research.google.com/drive/1R6kuyA8rt293TfcryyWZT71VJQKpcldI?usp=sharing)

## Quanta memória cada tamanho de modelo precisa em 32, 16, 8 e 4 bits?

A mesma fórmula vale para outros tamanhos de modelo e para precisões menores. Com Q = 8 ou Q = 4 (modelos quantizados), a conta fica assim:

| Tamanho do modelo | 32 bits | 16 bits | 8 bits | 4 bits |
|---|---|---|---|---|
| 8B | 38,4 GB | 19,2 GB | 9,6 GB | 4,8 GB |
| 14B | 67,2 GB | 33,6 GB | 16,8 GB | 8,4 GB |
| 32B | 153,6 GB | 76,8 GB | 38,4 GB | 19,2 GB |
| 70B | 336 GB | 168 GB | 84 GB | 42 GB |

Os valores já incluem os 20% de sobrecarga. Contextos muito longos e muitas requisições simultâneas consomem memória além dessa margem, então trate a tabela como piso.

Cruzando esses valores com a memória das placas mais comuns:

| Memória da GPU | Maior modelo da tabela que cabe em 4 bits | Exemplos de GPU |
|---|---|---|
| 8 GB | 8B (4,8 GB) | RTX 4060, RTX 3070 |
| 12 a 16 GB | 14B (8,4 GB) | RTX 4070, RTX 4080, T4 |
| 24 GB | 32B (19,2 GB) | RTX 3090, RTX 4090, L4 |
| 48 GB | 70B (42 GB) | L40S, RTX 6000 Ada, RTX A6000 |
| 80 GB | 70B com folga para contexto | A100 80 GB, H100 |

Ao dominar esse cálculo, você estará preparado para responder a essa pergunta e, mais importante, evitar gargalos de hardware dispendiosos em suas implantações e inovações.

## Quando usar precisão de 16 bits e quando usar 32 bits?

A gestão eficiente da memória GPU tornou-se um desafio crucial para desenvolvedores e pesquisadores. Um aspecto fundamental dessa otimização envolve a escolha da precisão numérica utilizada nos cálculos do modelo.

| Critério | 16 bits | 32 bits |
|---|---|---|
| Bytes por parâmetro | 2 | 4 |
| Memória de um LLaMA de 70B pela fórmula | 168 GB | 336 GB |
| Precisão | Ligeira perda, aceitável na maioria das aplicações | Referência de precisão máxima |
| Fase em que costuma ser usada | Inferência | Treinamento |

A escolha entre precisão de 16 bits e 32 bits depende de vários fatores:

1. **Requisitos de Precisão**: Avalie se a ligeira perda de precisão com 16 bits é aceitável para sua aplicação.
2. **Recursos de Hardware**: Considere a memória GPU disponível e a arquitetura das GPUs utilizadas.
3. **Escala do Modelo**: Modelos maiores podem se beneficiar mais da redução de memória oferecida pelos 16 bits.
4. **Fase do Projeto**: A precisão de 16 bits pode ser mais adequada para inferência, enquanto 32 bits pode ser preferível durante o treinamento.

## O que esse cálculo muda na escolha de hardware e no custo?

O cálculo tem implicações no mundo real. Uma única GPU NVIDIA A100 com 80 GB de memória não comporta o LLaMA de 70B em 16 bits. Duas A100 somam 160 GB, abaixo dos 168 GB da conta, então você precisaria de pelo menos três GPUs A100 de 80 GB para lidar com a carga de memória.

E em que isso implica?

- **Escalabilidade do Modelo**: Este exemplo mostra como os modelos de linguagem de última geração estão ultrapassando as capacidades de hardware individual, mesmo considerando GPUs de alto desempenho como a A100.
- **Necessidade de Paralelismo**: Para modelos extremamente grandes, como o LLaMA de 70B, o uso de múltiplas GPUs é obrigatório.
- **Importância da Precisão Reduzida**: O uso de precisão de 16 bits é crucial para tornar viável a execução desses modelos gigantes. Sem essa redução, os requisitos de memória seriam ainda maiores.
- **Custo e Infraestrutura**: A necessidade de múltiplas GPUs de alta performance implica em consideráveis investimentos em hardware e infraestrutura de computação.
- **Desafios de Engenharia**: Distribuir eficientemente um modelo tão grande entre múltiplas GPUs apresenta desafios significativos de engenharia de software e otimização de performance.

Se a dúvida for entre manter GPU própria ou pagar por uso de API, a [planilha de custos de IA](/ferramentas/planilha-custos-ia/) ajuda a comparar os cenários. O resto é tema de outro post.

## Perguntas frequentes sobre memória de GPU para rodar LLMs

### Quanta memória de GPU preciso para rodar um modelo de 8B ou de 70B?

Pela fórmula deste post, um modelo de 8B pede cerca de 19,2 GB em 16 bits e 4,8 GB em 4 bits. Um de 70B pede cerca de 168 GB em 16 bits e 42 GB em 4 bits. Os valores incluem 20% de sobrecarga e crescem com contextos longos e muitas requisições simultâneas.

### A fórmula vale para modelos quantizados em 8 ou 4 bits?

Sim, basta usar Q = 8 ou Q = 4 no lugar de 16. Cada vez que o número de bits cai pela metade, a memória dos pesos cai pela metade também. A perda de qualidade da quantização varia com o modelo e com a tarefa, então teste o modelo quantizado nas suas próprias perguntas antes de levar para produção.

### Posso usar a memória RAM do computador se faltar memória na GPU?

Alguns runtimes de inferência conseguem deixar parte das camadas na RAM e executar o restante na GPU. O modelo roda, mas cada token passa a depender da transferência entre RAM e GPU, e a velocidade cai bastante. Serve para testar um modelo, e fica lento para atender usuários em produção.

### Para uma empresa, sai mais barato alugar GPU ou pagar por token em API?

Com volume baixo ou irregular, a API costuma sair mais barata, porque cobra só pelos tokens usados, enquanto a GPU própria ou alugada custa por hora, ocupada ou não. Com volume alto e constante, a GPU dedicada pode compensar. A [planilha de custos de IA](/ferramentas/planilha-custos-ia/) faz essa comparação com os seus números.

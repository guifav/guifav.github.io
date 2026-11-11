---
title: "22.580: do GPT-2 ao Kimi K3, explicado"
subtitle: "Como a arquitetura de atenção evoluiu de um modelo de 124 milhões para um sistema de 2,8 trilhões de parâmetros"
description: "Como a arquitetura de atenção evoluiu de um modelo de 124 milhões para um sistema de 2,8 trilhões de parâmetros"
date: 2026-08-09T18:25:17.849Z
updated: 2026-08-09T18:26:44.950Z
slug: 22580-do-gpt-2-ao-kimi-k3-explicado
cover: img/22580-do-gpt-2-ao-kimi-k3-explicado/capa.jpg
type: newsletter
---

Este artigo não foi escrito por mim, mas eu gostaria muito de tê-lo escrito. Por isso, segue tradução adaptada do artigo na íntegra.

<figure>
<img src="/blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/551307bd2b-1200.webp" srcset="/blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/551307bd2b-728.webp 728w, /blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/551307bd2b-1200.webp 1200w" sizes="(max-width: 760px) 100vw, 720px" width="1200" height="814" alt="Imagem" loading="lazy" decoding="async">
<figcaption>Linha do tempo das principais mudanças arquiteturais entre a atenção softmax e o Kimi k3.</figcaption>
</figure>

Vinte e dois mil quinhentos e oitenta. É essa a quantidade de modelos GPT-2 (2019) que cabe dentro do Kimi K3 (2026). Em sete anos, aumentamos a escala por um fator de 22.580. Mas será que foi apenas... escala?

Neste diário de trabalho, vou mostrar como chegamos até aqui e quanto — ou quão pouco — realmente mudou desde então. Acompanharemos os principais avanços arquiteturais que levaram ao Kimi K3.

Nota editorial: os rótulos incorporados às imagens foram preservados no idioma original; títulos, prosa, listas, comentários de código e legendas estão em português brasileiro.

<figure>
<img src="/blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/b8cb724cea-1200.webp" srcset="/blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/b8cb724cea-728.webp 728w, /blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/b8cb724cea-1200.webp 1200w" sizes="(max-width: 760px) 100vw, 720px" width="1200" height="705" alt="Imagem" loading="lazy" decoding="async">
<figcaption><em>Visão comparativa das arquiteturas discutidas ao longo do artigo.</em></figcaption>
</figure>

## GPT-2

O GPT-2 usa uma arquitetura somente decodificadora:

> ```python
> tok_emb = self.transformer.wte(idx) # embeddings de tokens com forma (b, t, n_embd)
> pos_emb = self.transformer.wpe(pos) # embeddings de posição com forma (t, n_embd)
> x = self.transformer.drop(tok_emb + pos_emb)
> for block in self.transformer.h:
>  x = block(x)
> x = self.transformer.ln_f(x)
> logits = self.lm_head(x)
> return logits
> ```

A entrada recebe embeddings de tokens e de posição:

<figure>
<img src="/blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/e23712e1e0-1134.webp" srcset="/blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/e23712e1e0-728.webp 728w, /blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/e23712e1e0-1134.webp 1134w" sizes="(max-width: 760px) 100vw, 720px" width="1134" height="1200" alt="Imagem" loading="lazy" decoding="async">
<figcaption><em>Combinação dos embeddings de tokens e de posição na entrada d</em>Ao ampliar um bloco Transformer, vemos esta estrutura:</figcaption>
</figure>

> ```python
> class Block(nn.Module):
>  def __init__(self, config):
>  super().__init__()
>  self.ln_1 = LayerNorm(config.n_embd, bias=config.bias)
>  self.attn = CausalSelfAttention(config)
>  self.ln_2 = LayerNorm(config.n_embd, bias=config.bias)
>  self.mlp = MLP(config)
>
>  def forward(self, x):
>  x = x + self.attn(self.ln_1(x))
>  x = x + self.mlp(self.ln_2(x))
>  return x
> ```

<figure>
<img src="/blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/7251b68cc7-1200.webp" srcset="/blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/7251b68cc7-728.webp 728w, /blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/7251b68cc7-1200.webp 1200w" sizes="(max-width: 760px) 100vw, 720px" width="1200" height="1120" alt="Imagem" loading="lazy" decoding="async">
<figcaption><em>Estrutura de um bloco Transformer do GPT-2 e sua atenção multi-head.</em></figcaption>
</figure>

O processo de atenção:

> ```python
> B, T, C = x.size() # tamanho do lote, comprimento da sequência e dimensão do embedding (n_embd)
>
> # calcula consultas, chaves e valores para todas as cabeças e move a dimensão de cabeças para a frente
>  q, k, v = self.c_attn(x).split(self.n_embd, dim=2)
>  k = k.view(B, T, self.n_head, C // self.n_head).transpose(1, 2) # (B, nh, T, hs)
>  q = q.view(B, T, self.n_head, C // self.n_head).transpose(1, 2) # (B, nh, T, hs)
>  v = v.view(B, T, self.n_head, C // self.n_head).transpose(1, 2) # (B, nh, T, hs)
>
> # implementação manual da atenção
>  att = (q @ k.transpose(-2, -1)) * (1.0 / math.sqrt(k.size(-1)))
>  att = att.masked_fill(self.bias[:,:,:T,:T] == 0, float(’-inf’))
>  att = F.softmax(att, dim=-1)
>  att = self.attn_dropout(att)
>  y = att @ v # (B, nh, T, T) x (B, nh, T, hs) -> (B, nh, T, hs)
>  y = y.transpose(1, 2).contiguous().view(B, T, C) # reagrupa lado a lado as saídas de todas as cabeças
>
> # projeção de saída
>  y = self.resid_dropout(self.c_proj(y))
>  return y
> ```

Depois de produzir a matriz final de estados ocultos, a cabeça do modelo de linguagem a converte em logits sobre o vocabulário. Durante a decodificação autorregressiva, apenas os logits da última posição são necessários para escolher o próximo token.

Essa é uma ineficiência da geração somente decodificadora: o modelo calcula representações para todas as posições da entrada, embora cada etapa de decodificação consuma apenas os logits da posição final. Sem cache, grande parte desse trabalho seria repetida para o token seguinte.

<figure>
<img src="/blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/b1e9b45a0a-1200.webp" srcset="/blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/b1e9b45a0a-728.webp 728w, /blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/b1e9b45a0a-1200.webp 1200w" sizes="(max-width: 760px) 100vw, 720px" width="1200" height="362" alt="Imagem" loading="lazy" decoding="async">
<figcaption><em>Cálculo dos estados ocultos e uso do resultado na geração autorregressiva.</em></figcaption>
</figure>

O cache de KV nasce de uma observação simples: depois que o token gerado é anexado à entrada, o modelo teria de recalcular as projeções de todos os tokens anteriores. Armazenar seus vetores de chave e valor evita esse trabalho redundante.

Esse armazenamento é o cache de KV. Ele retém vetores dos N−1 tokens anteriores e pode crescer a ponto de se tornar um gargalo de largura de banda de memória.

No total, com cerca de 50 mil tokens possíveis, 12 blocos, 12 cabeças e uma dimensão de embedding de 768, nosso modelo de referência tem aproximadamente 124 milhões de parâmetros.

> ```python
> vocab_size: int = 50304 # vocab_size do GPT-2 (50257), preenchido até o múltiplo de 64 mais próximo para maior eficiência
> n_layer: int = 12
> n_head: int = 12
> n_embd: int = 768
> ```

Com 2,8 trilhões de parâmetros, um único Kimi K3 contém aproximadamente tantos parâmetros quanto 22.580 modelos GPT-2.

## Atenção linear

A atenção softmax aplica sua não linearidade depois do produto q·k, acoplando cada consulta a todas as chaves. A atenção linear, por sua vez, aplica separadamente a q e k um mapa de características, como ELU+1. Assim, o produto pode ser reassociado e o conjunto crescente de vetores K e V pode ser condensado em um estado fixo D×D.

A formulação O(N²) do artigo me confundiu. Não é verdade que “o custo por passo de tempo dos Transformers cresce com o quadrado do comprimento atual da sequência”. É justamente isso que o FlashAttention resolve... até eu perceber que o trabalho citado foi publicado em 2020.

Na época, o treinamento normalmente materializava a matriz de atenção N×N inteira, o FlashAttention ainda não existia e implementações autorregressivas de referência frequentemente recalculavam todo o histórico de tokens sem usar um cache de KV.

> ```python
> def forward(self, x, mask=None, past_kv=None):
> # x tem forma b,t,d
>  b,t,d=x.shape
>  d_head=d//self.num_heads
>  h=self.num_heads
>  qkv=self.qkv_proj(x)
>
>  q=qkv[:, :, :d].view(b,t,h,d_head).transpose(1,2)
>  k=qkv[:, :, d:2*d].view(b,t,h,d_head).transpose(1,2)
>  v=qkv[:, :, 2*d:].view(b,t,h,d_head).transpose(1,2)
>
> # no prefill, q,k,v têm forma b,h,t,d
> # na decodificação, a forma é b,h,1,d
> # a concatenação ocorre na dimensão t, dim(2)
>
>  if past_kv is not None:
>  k_past=past_kv[0]
>  v_past=past_kv[1]
>  k=torch.cat((k_past, k), dim=2)
>  v=torch.cat((v_past, v), dim=2)
>
>  scores=(q@k.transpose(-1,-2))/math.sqrt(d_head)
>  if past_kv is None: # estamos no prefill e precisamos aplicar a máscara
>  causal_mask=torch.ones(t,t,dtype=bool, device=q.device)
>  causal_mask=torch.triu(causal_mask, diagonal=1)
>  scores=scores.masked_fill(causal_mask, float(’-inf’))
>
>  if mask is not None:
>  scores=scores.masked_fill(~mask, float(’-inf’))
>
> # calcula a atenção (bhtt x bhtd)
>  attn=scores.softmax(-1) # bhtt
>  o=attn@v # bhtd
>  o=o.transpose(1,2).contiguous().view(b,t,d) # b,t,d
>
> # usa x para obter qkv
>  o_proj=self.o_proj(o)
>  past_kv=(k, v)
>  return o_proj, past_kv
> ```

O mesmo processo fica mais claro visualmente. Cada etapa de decodificação realiza duas leituras ND e duas gravações 1D na HBM, enquanto o cache de KV cresce linearmente, em O(N), com o comprimento da sequência.

<figure>
<img src="/blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/771cb09123-1208.webp" srcset="/blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/771cb09123-728.webp 728w, /blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/771cb09123-1208.webp 1208w" sizes="(max-width: 760px) 100vw, 720px" width="1208" height="1744" alt="Imagem" loading="lazy" decoding="async">
<figcaption><em>Leituras, gravações e crescimento do cache de KV durante a decodificação.</em></figcaption>
</figure>

Observe o excesso de leituras e gravações, que o artigo substitui por:

> ```python
> def forward(self, x, mask=None, cache=None):
> # x tem forma b,t,d
>  b,t,d=x.shape
>  d_head=d//self.num_heads
>  h=self.num_heads
>  qkv=self.qkv_proj(x)
>
>  q=qkv[:, :, :d].view(b,t,h,d_head).transpose(1,2)
>  k=qkv[:, :, d:2*d].view(b,t,h,d_head).transpose(1,2)
>  v=qkv[:, :, 2*d:].view(b,t,h,d_head).transpose(1,2)
>  
>  k=F.elu(k)+1 
>  k=k.transpose(-1,-2) 
>  q=F.elu(q)+1
>  
>  S,z=cache if cache is not None else (0.0, 0.0)
>  S=S+k@v
>  z=z+k
>  
>  o=q@S # bhtd
>  denom=q@z
>  o_scaled=o/denom
>  o_scaled=o_scaled.transpose(1,2).contiguous().view(b,t,d)
>  o_proj=self.o_proj(o_scaled)
>  cache=(S,z)
>  
>  return o_proj, cache
> ```

Há uma contrapartida.

Aqui, substituímos a exponenciação do softmax por ELU+1, aplicada separadamente a q e k antes de eles interagirem. As duas abordagens normalizam os escores resultantes, mas o mapa de características da atenção linear é uma aproximação menos expressiva do kernel softmax. Essa aproximação pode reduzir a fidelidade, embora a perda prática de acurácia dependa da arquitetura e da carga de trabalho.

Ainda dividimos pela soma de qk, embora isso tenha sido omitido do diagrama para simplificar. Em alto nível, a atenção é composta por três etapas:

1\. Tornar não negativos os escores de qk. A atenção linear usa ELU+1, enquanto o softmax usa exponenciação.

2\. Dividir pela soma.

3\. Calcular a média ponderada dos valores.

Isso preserva o contrato básico da atenção, mas usa um mapa de características menos expressivo para tornar não negativos os escores de QK.

## DeltaNet (programadores de pesos rápidos)

Um cache finito precisa sobrescrever ou combinar informações já armazenadas. O estado do token i−1 não recebe um espaço próprio; ele é somado à mesma matriz D×D. Por isso, novas consultas já não conseguem recuperar uma representação perfeitamente isolada de cada token anterior.

Essa soma também é a origem do ganho de eficiência. Atualizar o cache por adição, em vez de concatenação, impede que ele cresça em O(N), mas a mesma operação faz as informações interferirem entre si. A DeltaNet tenta recuperar essa capacidade de acesso.

<figure>
<img src="/blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/d8af82e252-1200.webp" srcset="/blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/d8af82e252-728.webp 728w, /blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/d8af82e252-1200.webp 1200w" sizes="(max-width: 760px) 100vw, 720px" width="1200" height="365" alt="Imagem" loading="lazy" decoding="async">
<figcaption><em>Interferência causada por atualizações puramente aditivas em uma memória de tamanho fixo.</em></figcaption>
</figure>

Como resume com precisão o artigo de Schlag, Fast Weight Programmers: “quando o comprimento da sequência ultrapassa a capacidade de armazenamento, o modelo pode entrar em um regime de sobrecapacidade. Para operar adequadamente nesse regime, ele deve aprender a interagir dinamicamente com o conteúdo da memória e decidir, de forma seletiva, quais associações chave–valor manter e quais excluir. A instrução puramente aditiva pode ser inadequada para essa finalidade \[...\]. Somar indefinidamente novas associações a uma memória de tamanho finito, como na Eq. 17, inevitavelmente atingirá um limite”.

O regime que torna a atenção linear atraente — quando N é muito maior que D — também expõe sua principal limitação. Quando o estado ultrapassa sua capacidade efetiva, as associações começam a interferir, pois a atualização é aditiva e nada sai do cache.

> ```python
> def forward(self, x, mask=None, cache=None):
> # x tem forma b,t,d
>  b,t,d=x.shape
>  d_head=d//self.num_heads
>  h=self.num_heads
>  qkv=self.qkv_proj(x)
>
>  q=qkv[:, :, :d].view(b,t,h,d_head).transpose(1,2)
>  k=qkv[:, :, d:2*d].view(b,t,h,d_head).transpose(1,2)
>  v=qkv[:, :, 2*d:].view(b,t,h,d_head).transpose(1,2)
>
>  q = F.normalize(F.silu(q), dim=-1) 
>  k = F.normalize(F.silu(k), dim=-1) 
>  beta = torch.sigmoid(self.w_beta(x)).view(b, 1, t, 1) 
> # novo: intensidade de gravação por token
>
>  S = cache if cache is not None else 0.0 
>
>  v_old = k @ S # lê a memória nesta chave
>  u = beta * (v - v_old) # o delta: apenas o que é realmente novo
>  S = S + k.transpose(-1, -2) @ u # a mesma gravação por produto externo usada antes
>
>  o = q @ S # leitura sem denominador
>  o = o.transpose(1, 2).contiguous().view(b, t, d)
>  return self.o_proj(o), S
> ```

Um exemplo visual ajuda a acompanhar o processo.

<figure>
<img src="/blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/a85e4aa990-1200.webp" srcset="/blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/a85e4aa990-728.webp 728w, /blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/a85e4aa990-1200.webp 1200w" sizes="(max-width: 760px) 100vw, 720px" width="1200" height="358" alt="Imagem" loading="lazy" decoding="async">
<figcaption><em>Leitura e substituição de uma associação chave–valor pela regra delta.</em></figcaption>
</figure>

Considere uma única associação gravada como S = k.T \@ v. Se a lermos de volta com a mesma chave, obtemos k \@ (k.T \@ v), isto é, (k \@ k.T) v: a norma de k ao quadrado multiplicada por v. A leitura, portanto, devolve v escalado pela norma quadrática da chave. Se normalizarmos k para ter norma unitária — ou simplesmente dividirmos o resultado pela norma — recuperamos v exatamente.

Q também funciona como um ponteiro aprendido. Wq e Wk leem o mesmo fluxo residual, e a consulta associada a um fato aponta para a direção da chave na qual esse fato foi gravado. Primeiro, a atualização pergunta qual informação a chave atual recupera do cache. Em seguida, subtrai essa informação existente do valor que queremos armazenar, multiplica a diferença pela chave e soma o resultado de volta. A informação antiga é removida e a nova é gravada em seu lugar.

## DeltaNet: paralelizando transformadores lineares com a regra delta

Esta é a seção mais difícil do texto. Levei cerca de sete horas para chegar a uma compreensão funcional, por isso construirei a explicação a partir da implementação. Em resumo, a DeltaNet implementa uma recorrência linear de primeira ordem com matrizes de transição de Householder generalizadas. Isso permite passagens diretas paralelas por chunks, adequadas ao hardware, durante um treinamento de tempo linear. As entradas e saídas são divididas em chunks de tamanho C, e a saída de cada chunk é calculada com base no estado final do chunk anterior e nos blocos de consulta, chave e valor do chunk atual.

O problema prático está no prefill. Uma implementação direta da regra delta sobre uma sequência de T tokens seria assim:

> ```python
> S = torch.zeros(b, h, dh, dh) if cache is None else cache
> outs = []
> for i in range(t):
>  k_i = k[:, :, i:i+1] 
>  v_i = v[:, :, i:i+1]
>  b_i = beta[:, :, i:i+1]
>  v_old = k_i @ S 
>  u_i = b_i * (v_i - v_old)
>  S = S + k_i.transpose(-1, -2) @ u_i # gravação
>  outs.append(q[:, :, i:i+1] @ S) 
> o = torch.cat(outs, dim=2)
> ```

Ao contrário da atenção padrão, essa formulação exige uma correção em cada vetor de chave, por isso o caminho até uma multiplicação matricial paralela não é imediatamente óbvio. Mesmo sem a regra delta, um prefill direto de atenção linear continua sequencial:

> ```python
> S = torch.zeros(b, h, dh, dh) if cache is None else cache
> outs = []
> for i in range(t):
>  q = q[:, :, i:i+1] 
>  k = k[:, :, i:i+1] 
>  v = v[:, :, i:i+1]
>
>  S=S_old+k@v
>  o=q@S # bhtd
>  o=self.norm(o)
>  o=o.transpose(1, 2).contiguous().view(b, t, d)
>
>  out=self.o_proj(o)
>  cache=S
>  outs.append(out)
>
> o = torch.cat(outs, dim=2)
> ```

Uma formulação em chunks oferece uma alternativa mais eficiente. A mecânica fica mais fácil de entender com um exemplo:

<figure>
<img src="/blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/0a7620dd97-1200.webp" srcset="/blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/0a7620dd97-728.webp 728w, /blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/0a7620dd97-1200.webp 1200w" sizes="(max-width: 760px) 100vw, 720px" width="1200" height="713" alt="Imagem" loading="lazy" decoding="async">
<figcaption><em>Divisão da sequência em chunks para combinar atenção local e estado recorrente.</em></figcaption>
</figure>

Definir C=N recupera a atenção O(N²) padrão, enquanto C=1 produz a atenção linear convencional. Valores intermediários equilibram trabalho adicional dentro de cada chunk com melhor utilização do hardware. Na prática, C costuma ser 64 ou 128 porque as instruções dos tensor cores operam eficientemente nessa granularidade; UMMA é um exemplo.

Os blocos intermediários são incorporados a S como parte da atualização de estado:

<figure>
<img src="/blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/35e4b60a30-1200.webp" srcset="/blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/35e4b60a30-728.webp 728w, /blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/35e4b60a30-1200.webp 1200w" sizes="(max-width: 760px) 100vw, 720px" width="1200" height="722" alt="Imagem" loading="lazy" decoding="async">
<figcaption><em>Atualização do estado recorrente entre chunks.</em></figcaption>
</figure>

> ```python
> S = torch.zeros(b, h, dh, dh) if cache is None else cache
> outs = []
> for i in range(t//C):
>  q_c = q[:, :, i*C:(i+1)*C] 
>  k_c = k[:, :, i*C:(i+1)*C] 
>  v_c = v[:, :, i*C:(i+1)*C]
>
>  o_prev=q_c@S # tudo o que veio antes deste bloco
>  
>  attn=(q_c@k_c.transpose(-1,-2)).tril() # atenção mascarada
>  o_curr=attn@v_c
>  
>  o=o_prev+o_curr
>  
>  S_new=k_c.transpose(-1,-2)@v_c # atenção recorrente
>  S=S+S_new
>  outs.append(o)
>
> o = torch.cat(outs, dim=2)
> ```

Dentro de um bloco, calculamos q(kᵀv): primeiro os escores, na ordem usual da atenção, com mascaramento. Entre blocos, seguimos (kᵀv)q: primeiro o estado, na ordem recorrente. A atenção cresce em O(N²), mas este método não. Dentro de um bloco, aplico atenção real — QKᵀ mascarado, multiplicado por V —; entre blocos, incorporo tudo ao estado e leio de volta com uma única multiplicação matricial. O custo se divide em duas partes. Há uma parcela fixa, 2Ld², correspondente ao trabalho de estado e independente de C, e uma parcela crescente, 2LCd, correspondente às matrizes de escores na diagonal. A atenção completa é o caso em que C=L; então, o segundo termo se torna 2L²d, quadrático. Portanto, quanto menor C, menor o número de FLOPs.

C=1 é a opção mais barata em número puro de FLOPs, mas não necessariamente em tempo de execução. Uma GPU pode concluir mais operações aritméticas em menos tempo quando o trabalho se adapta bem ao hardware de multiplicação matricial.

O próximo passo é estender essa mesma abordagem à DeltaNet.

<figure>
<img src="/blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/f69a5b1ed0-1200.webp" srcset="/blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/f69a5b1ed0-728.webp 728w, /blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/f69a5b1ed0-1200.webp 1200w" sizes="(max-width: 760px) 100vw, 720px" width="1200" height="716" alt="Imagem" loading="lazy" decoding="async">
<figcaption><em>Reparametrização necessária para paralelizar as atualizações delta.</em></figcaption>
</figure>

O problema subjacente é simples: o método de divisão em chunks usado na atenção puramente aditiva não se aplica diretamente às atualizações delta:

> ```python
> v_old = k_i @ S 
> u_i = b_i * (v_i - v_old)
> ```

Precisamos de cada estado intermediário, na ordem correta, para calcular a informação que deve ser subtraída. Não é possível paralelizar do mesmo modo sem alguma reparametrização matemática. Por isso, os autores reescrevem as atualizações delta de:

> ```python
> u=v_new-v_old
> S_t= S_(t-1)+K.T@u
> o=q@S_T
> ```

Aqui, um laço sequencial calcula um delta por iteração. A forma reparametrizada é:

> ```python
> S_t = S_{t-1}(I − β_t k_t k_tᵀ) + β_t v_t k_tᵀ
> o_t = S_t q_t
> ```

Essa formulação permite que o código em chunks calcule os C deltas de uma só vez:

> ```python
> def chunk_delta_rule_forward(Q, K, V, beta, C):
> # L: comprimento da sequência; d: dimensão da cabeça
>  L, d = Q.shape
> # divisão em chunks
>  Q, K, V = map(lambda x: x.reshape(-1,C,d), [Q, K, V])
>  beta = beta.reshape(-1, C)
>  K_beta = K * beta.unsqueeze(-1)
>  V_beta = V * beta.unsqueeze(-1)
>  
> # calcula a Eq. 10 com substituição direta vetorizada para obter a inversa com eficiência
>  T = -(K_beta @ K.t()).tril(-1)
>  for i in range(1, C):
>  T[i, :i] = T[i, :i] + (T[i, :, None] * T[:, :i]).sum(-2)
>  
>  T += torch.eye(C)
>  W = T @ K_beta
>  U = T @ V_beta
>
> # paralelismo por chunks, Eqs. 8–9
>  S = torch.zeros(d, d)
>  O = torch.empty_like(V)
>  
>  for i in range(L//C):
>  q_i, k_i, w_i = Q[i], K[i], W[i]
>  u_i = U[i] - w_i @ S # todas as correções de um chunk
>  o_inter = q_i @ S
>  A_i = (q_i @ k_i.t()).tril() # qk.t
>  o_intra = A_i @ u_i # atenção @ v, usando u por conter as correções
>  S += k_i.t() @ u_i # atualiza o estado por adição
>  O[i] = o_intra + o_inter # atualiza a saída com os termos local e recorrente
>  return O.reshape(L, d)
> ```

Chegamos, assim, ao primeiro ponto de comparação: Transformers com MHA versus Transformers com DeltaNet.

<figure>
<img src="/blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/40b5b90506-1200.webp" srcset="/blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/40b5b90506-728.webp 728w, /blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/40b5b90506-1200.webp 1200w" sizes="(max-width: 760px) 100vw, 720px" width="1200" height="504" alt="Imagem" loading="lazy" decoding="async">
<figcaption><em>Comparação entre Transformers com atenção multi-head e com DeltaNet.</em></figcaption>
</figure>

## Gated DeltaNet

Agora temos um método para fazer alterações precisas no cache. A cada novo fato — isto é, a cada novo vetor de chave —, podemos consultar exatamente a informação antiga armazenada naquele ponto e substituí-la pelo novo conteúdo ao qual queremos dar atenção.

Esse mecanismo, porém, só consegue esquecer uma associação quando há uma substituição específica para ela. Ele não consegue limpar várias associações de forma eficiente durante uma mudança de contexto, nem fazer a memória decair de modo geral para liberar capacidade.

Se estivéssemos usando atenção linear puramente aditiva:

Adicionar a capacidade de esquecer seria simples. Bastaria um parâmetro para controlar o estado a ser retido:

> ```python
> S_old=cache
> S_new=k@v
> # cache=S_old+S_new
> cache=alpha * S_old + S_new
> ```

<figure>
<img src="/blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/84c38b513a-1200.webp" srcset="/blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/84c38b513a-728.webp 728w, /blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/84c38b513a-1200.webp 1200w" sizes="(max-width: 760px) 100vw, 720px" width="1200" height="258" alt="Imagem" loading="lazy" decoding="async">
<figcaption><em>Decaimento do estado anterior antes da inclusão de novas informações.</em></figcaption>
</figure>

Essa é a contribuição do Mamba-2. Aplicamos decaimento ao cache anterior e, depois, somamos o novo cache com força total, impedindo que o estado cresça sem limite.

Aplicar, em cada passo de tempo, o mesmo decaimento dinâmico a todas as associações chave–valor é uma abordagem funcional — e é o que o Mamba faz. Mas ela não considera que associações diferentes têm importâncias diferentes.

Em outras palavras, se o modelo precisa esquecer uma associação específica, todas as associações são esquecidas por igual. A regra delta, por outro lado, consegue atualizar um único fato, mas não oferece uma forma de fazer o restante decair.

A regra Gated Delta combina, portanto, a atualização com gate do Mamba com a regra delta. Ela acrescenta um parâmetro, alpha, que reproduz a regra delta pura quando vale um e limpa a memória quando vale zero. O desafio é implementar isso com o mesmo método de chunks paralelos.

A implementação usa a mesma reparametrização da DeltaNet descrita na seção anterior. A matemática é praticamente idêntica, com um acréscimo: um escalar dependente dos dados, entre zero e um, que controla o decaimento do estado anterior. Assim, o modelo combina aprendizado eficaz de associações chave–valor com gerenciamento adaptativo da memória.

As alterações correspondentes no código aparecem abaixo:

<figure>
<img src="/blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/00d1eabc74-1200.webp" srcset="/blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/00d1eabc74-728.webp 728w, /blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/00d1eabc74-1200.webp 1200w" sizes="(max-width: 760px) 100vw, 720px" width="1200" height="686" alt="Imagem" loading="lazy" decoding="async">
<figcaption><em>Alterações introduzidas pela regra Gated Delta.</em></figcaption>
</figure>

O termo γʳ/γⁱ representa o decaimento acumulado. Um token gravado no passo x e lido em x+t foi multiplicado por αₓαₓ₊₁αₓ₊₂…αₓ₊ₜ. É o análogo multiplicativo de um cálculo de soma prefixada.

A arquitetura resultante tem esta aparência:

<figure>
<img src="/blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/c4286c43d9-1200.webp" srcset="/blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/c4286c43d9-728.webp 728w, /blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/c4286c43d9-1200.webp 1200w" sizes="(max-width: 760px) 100vw, 720px" width="1200" height="952" alt="Imagem" loading="lazy" decoding="async">
<figcaption><em>Arquitetura do Transformer com Gated DeltaNet.</em></figcaption>
</figure>

## KDA / Kimi Linear

A essa altura, pesquisadores começaram a experimentar modelos híbridos que combinam várias formas de atenção em uma única arquitetura, como Gated DeltaNet e Mamba.

O Kimi Linear chamou atenção por uma afirmação central: em comparações controladas, superou a atenção completa. Os autores o apresentaram como uma substituição arquitetural direta, com melhor qualidade e throughput de decodificação até seis vezes maior.

O Kimi Linear avança sobre a Gated DeltaNet ao introduzir gating de granularidade fina. Em vez de um único escalar de decaimento, aprende um valor de decaimento separado para cada canal.

<figure>
<img src="/blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/3efb9ca9dc-1014.webp" srcset="/blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/3efb9ca9dc-728.webp 728w, /blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/3efb9ca9dc-1014.webp 1014w" sizes="(max-width: 760px) 100vw, 720px" width="1014" height="1234" alt="Imagem" loading="lazy" decoding="async">
<figcaption><em>Controle de decaimento por canal na Kimi Delta Attention.</em></figcaption>
</figure>

A regra de atualização da KDA permanece semelhante, mas o código passa a se parecer mais com isto:

<figure>
<img src="/blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/5a3ec6f8c8-1200.webp" srcset="/blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/5a3ec6f8c8-728.webp 728w, /blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/5a3ec6f8c8-1200.webp 1200w" sizes="(max-width: 760px) 100vw, 720px" width="1200" height="462" alt="Imagem" loading="lazy" decoding="async">
<figcaption><em>Forma vetorizada da atualização de KDA.</em></figcaption>
</figure>

Aqui, alpha.reshape(nb, C, d) representa a contribuição mais importante do artigo: controle de granularidade fina sobre o decaimento da memória.

Colocada ao lado do Transformer com DeltaNet, a arquitetura Kimi Linear apresenta três mudanças principais:

1\. Usa um sistema híbrido que intercala camadas de Multi-head Latent Attention (MLA).

2\. Substitui a MLP por uma camada Mixture-of-Experts (MoE).

3\. Amplia a capacidade da DeltaNet por meio da projeção alpha.

<figure>
<img src="/blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/23fa184fdf-1200.webp" srcset="/blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/23fa184fdf-728.webp 728w, /blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/23fa184fdf-1200.webp 1200w" sizes="(max-width: 760px) 100vw, 720px" width="1200" height="722" alt="Imagem" loading="lazy" decoding="async">
<figcaption><em>Mudanças arquiteturais da DeltaNet para o Kimi Linear.</em></figcaption>
</figure>

As seções seguintes explicam MLA e MoE com mais detalhes. Por ora, o ponto central é que não se trata de escalar às cegas. A capacidade adicional tem um propósito matemático específico: a escala por canal oferece ao modelo controle mais preciso sobre o decaimento da memória.

As leis de escala continuam relevantes, mas a capacidade precisa ser adicionada no lugar certo e em uma forma que o sistema consiga usar. Cada arquitetura dessa progressão acrescenta capacidade para resolver uma limitação concreta do sistema anterior.

## Kimi K3

Em última análise, a espinha dorsal de linguagem do Kimi K3 se parece com o modelo Kimi Linear acima. Ela contém 23 macrociclos de quatro camadas. Em cada macrociclo, três camadas usam Kimi Delta Attention e a quarta usa Multi-head Latent Attention. A primeira camada emprega uma rede feed-forward densa; todas as demais usam uma Mixture-of-Experts latente.

À primeira vista, as mudanças em relação ao Kimi Linear parecem modestas:

1. Um aumento substancial de escala
2. AttnRes em blocos a cada 12 camadas
3. LoRA de consulta na MLA e gating de saída
4. MoE em espaço latente
5. Ativações SiTU
6. MLA com gating

A KDA fornece memória recorrente de estado constante, enquanto camadas periódicas de MLA preservam a recuperação softmax completa sobre o contexto. A visualização simplificada a seguir serve como referência para as mudanças discutidas abaixo.

<figure>
<img src="/blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/e25d95493f-1200.webp" srcset="/blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/e25d95493f-728.webp 728w, /blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/e25d95493f-1200.webp 1200w" sizes="(max-width: 760px) 100vw, 720px" width="1200" height="765" alt="Imagem" loading="lazy" decoding="async">
<figcaption><em>Arquitetura simplificada da espinha dorsal do Kimi K3.</em></figcaption>
</figure>

Começaremos pelas mudanças mais diretas: MLA com gating, MoE em espaço latente e ativações SiTU.

A MLA com gating determina quanto de cada característica recuperada passa da MLA para o fluxo residual. Isso é feito por multiplicação elemento a elemento com um gate projetado a partir da entrada.

Em uma MoE convencional, um roteador aprendido usa similaridade por produto escalar para enviar cada token a um subconjunto de redes especialistas. O Kimi K3 tem 898 especialistas no total. Dois são compartilhados e processam todos os tokens; entre os outros 896, o roteador seleciona 16 para cada token.

O Kimi K3 também altera a ativação dos especialistas. Em vez de aplicar SiLU à projeção ascendente, multiplicá-la elemento a elemento pelo gate e, em seguida, aplicar a projeção descendente, o modelo usa SiTU:

> ```python
> d = x.shape[-1] // 2
> gate = x[..., :d].to(torch.float32)
> up = x[..., d:].to(torch.float32)
> situ_a = self.beta * torch.tanh(gate / self.beta) * torch.sigmoid(gate)
> if self.linear_beta is not None:
>  up = self.linear_beta * torch.tanh(up / self.linear_beta)
> return (situ_a * up).to(x.dtype)
> ```

O modelo também reduz a dimensionalidade das entradas destinadas aos especialistas compartilhados e volta a expandir a soma final:

<figure>
<img src="/blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/df28b78614-1200.webp" srcset="/blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/df28b78614-728.webp 728w, /blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/df28b78614-1200.webp 1200w" sizes="(max-width: 760px) 100vw, 720px" width="1200" height="555" alt="Imagem" loading="lazy" decoding="async">
<figcaption><em>Especialistas compartilhados e roteados em espaço latente.</em></figcaption>
</figure>

Isso ilustra um desafio recorrente na inferência de modelos. Sem um kernel fundido, a nova ativação é quase três vezes mais lenta que o caminho original. Uma otimização compensatória é que os especialistas operam em um espaço latente comprimido, o que torna sua passagem direta muito mais rápida e quase reduz os FLOPs pela metade.

As mudanças restantes são LoRA de consulta na MLA, gating de saída e Attention Residuals em blocos a cada 12 camadas. A AttnRes acrescenta aproximadamente 2% de latência à inferência, mas oferece dois benefícios importantes:

1\. Recuperação seletiva de representações anteriores, o que reduz a diluição residual e o crescimento dos estados ocultos

2\. Uma vantagem computacional de 1,25x

AttnRes e MLA atacam a mesma limitação subjacente por caminhos diferentes. As camadas de KDA trabalham com um estado de tamanho constante e, inevitavelmente, precisam descartar informação. A MLA recupera conteúdo do contexto de tokens, enquanto a AttnRes recupera representações anteriores ao longo da profundidade da rede.

## AttnRes

Agradeço a [\@chloey3k](https://x.com/@chloey3k) pela ajuda nesta seção. Em cada passagem direta, a entrada percorre uma pilha de camadas. Aqui, cada camada contém um bloco de atenção — KDA ou MLA — e um bloco MLP ou MoE. Normalmente, a entrada de cada camada é a soma do embedding original com a saída de todas as camadas anteriores, todas com o mesmo peso.

<figure>
<img src="/blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/cdf60ffa41-336.webp" width="336" height="158" alt="" loading="lazy" decoding="async">
</figure>

Aqui, h\_i é a entrada da camada i, h\_1 é o embedding do token atual — o último token da sequência até aquele momento — e f\_i(h\_i) é a saída da camada i, isto é, um bloco de atenção ou MLP.

O problema é a falta de acesso seletivo. Tipos diferentes de camada recebem o mesmo estado agregado, embora possam se beneficiar de ponderações distintas. Como a recorrência é puramente aditiva, camadas posteriores também precisam aprender saídas cada vez maiores para influenciar o residual acumulado, o que pode desestabilizar o treinamento. Em vez de tratar todas as camadas por igual, a AttnRes multiplica cada termo dessa soma por um peso especializado, permitindo que o modelo valorize as camadas mais úteis em cada contexto.

<figure>
<img src="/blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/bce6f27c7e-436.webp" width="436" height="170" alt="" loading="lazy" decoding="async">
</figure>

Cada peso alpha\_i é calculado por um produto escalar entre consulta e chave. A consulta é aprendida para cada camada, enquanto chaves e valores vêm de estados anteriores do fluxo residual. Os escores são normalizados para somar um e, depois, usados para formar uma combinação ponderada desses estados.

<figure>
<img src="/blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/6ada2590a9-1199.webp" srcset="/blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/6ada2590a9-728.webp 728w, /blog/img/22580-do-gpt-2-ao-kimi-k3-explicado/6ada2590a9-1199.webp 1199w" sizes="(max-width: 760px) 100vw, 720px" width="1199" height="918" alt="Imagem" loading="lazy" decoding="async">
<figcaption><em>A AttnRes aprende pesos para recuperar seletivamente estados residuais anteriores.</em></figcaption>
</figure>

Assim, o modelo não precisa se condicionar apenas ao predecessor imediato. A AttnRes oferece a cada camada acesso seletivo às saídas de camadas anteriores, permitindo que sua consulta aprendida recupere as representações mais úteis para o cálculo atual.

O pseudocódigo abaixo aplica a mesma ideia na granularidade de blocos. Um bloco é a soma elemento a elemento das saídas de atenção e MLP acumuladas ao longo de 12 camadas do decodificador, armazenada como uma única representação de profundidade para a mistura posterior da AttnRes.

Aplicar atenção residual em todas as camadas acrescentaria custo demais ao treinamento e à inferência. Usá-la apenas em limites fixos de bloco captura a maior parte do benefício com menor custo. No Kimi K3, cada limite ocorre depois de 12 camadas do decodificador. Ao longo de 23 macrociclos de quatro camadas, isso produz oito blocos AttnRes e melhora a velocidade de inferência.

Esta é, possivelmente, a parte mais importante da função block\_attn\_res:

> ```python
> V = torch.stack(blocks + [partial_block]) # [N+1, B, T, D]
> K = norm(V)
> logits = torch.einsum(’d, n b t d -> n b t’, proj.weight.squeeze(), K)
> h = torch.einsum(’n b t, n b t d -> b t d’, logits.softmax(0), V)
> return h
> ```

Isso conclui a progressão do GPT-2 ao Kimi K3.

A mudança central não está apenas na escala. Cada etapa arquitetural altera o que o modelo armazena, como atualiza esse estado ou como recupera informações que um estado de tamanho fixo não consegue preservar.

O Kimi K3 combina memória recorrente de estado constante, recuperação softmax periódica, capacidade esparsa de especialistas e acesso residual seletivo ao longo da profundidade. O resultado é um sistema que investe capacidade adicional onde ela cumpre uma função específica.

Em essência, uma memória associativa de capacidade fixa — com dimensões fixas — precisa de uma política de descarte, pois uma operação linear puramente aditiva acaba gerando interferência ao atingir o limite. Por isso, torna-se necessária alguma forma de seleção aprendida, como gating, roteamento ou decaimento; e a atenção é o mecanismo de leitura seletiva mais eficaz.

**Artigo original traduzido:** [publicação de \@waterloo\_intern no X](https://x.com/waterloo_intern/status/2081762065392541951)

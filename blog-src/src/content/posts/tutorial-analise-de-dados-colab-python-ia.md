---
title: "Tutorial: analise de dados avançada com IA para gestores (não programadores)"
description: "Acesse as ferramentas e arquivos utilizados neste tutorial"
date: 2024-08-21T03:00:00.000Z
updated: 2024-10-02T23:49:08.731Z
slug: tutorial-analise-de-dados-colab-python-ia
cover: img/tutorial-analise-de-dados-colab-python-ia/capa.jpg
type: newsletter
---

Acesse as ferramentas e arquivos utilizados neste tutorial

<figure>
<a href="https://www.youtube.com/watch?v=VcxN30itGrM"><img src="/blog/img/tutorial-analise-de-dados-colab-python-ia/581fb7898d-1280.webp" srcset="/blog/img/tutorial-analise-de-dados-colab-python-ia/581fb7898d-728.webp 728w, /blog/img/tutorial-analise-de-dados-colab-python-ia/581fb7898d-1280.webp 1280w" sizes="(max-width: 760px) 100vw, 720px" width="1280" height="720" alt="" loading="lazy" decoding="async"></a>
</figure>

## **A Analise de Dados parece complicada, mas não precisa ser**

Hoje vou mostrar como gestores de negócios podem aproveitar o poder do Python para analise de dados, mesmo sem saber programar. Vamos usar a inteligência artificial para nos ajudar nesse processo.

A ideia aqui é te mostrar como uma empresa pode analisar a performance de suas vendas, por meio de técnicas relativamente avançadas, em menos de 10 minutos. Vou te guiar em um passo a passo bem simples e detalhado. 

Minha intenção de desmistificar questões complexas, colocando-as em linguagem simples, e te estimulando por meio de casos práticos, reais e com ferramentas excelentes e gratuitas.

Acesse os arquivos utilizados neste tutorial

## **Passo 1: Preparação dos Dados**

Para mergulharmos em qualquer analise de dados, é crucial ter nossos dados bem organizados. Eu tenho aqui uma planilha com dados de vendas e vamos seguir algumas práticas recomendadas.

1. Escolha do Software:

   - Você pode usar o Microsoft Excel ou o Google Sheets. Hoje, usarei o Google Sheets pela sua acessibilidade e facilidade de colaboração.

2. Estrutura da Planilha:

   - Começaremos com uma linha de cabeçalho clara. Por exemplo: Data, Produto, Região, Vendas, Custos.
   - Evitaremos espaços nos nomes das colunas, usando "PrecoUnitario" em vez de "Preço Unitário".

3. Tipos de Dados:

   - Manteremos a consistência em cada coluna. Datas serão no formato AAAA-MM-DD, e números usarão ponto como separador decimal.

4. Dados Ausentes:

   - Se houver dados faltantes, deixaremos as células vazias em vez de usar marcadores como "N/A".

5. Formatação:

   - Manteremos a formatação simples, sem cores ou estilos elaborados.

6. Salvando como CSV:

   - Após preencher os dados, salve a planilha como CSV. No Google Sheets, vá em **Arquivo** \> **Baixar** \> **Valores separados por vírgula** (.csv).

7. Verificação:

   - Abra o arquivo CSV em um editor de texto para garantir que está formatado corretamente.

8. Nomenclatura de Arquivos:

   - Nomeie o arquivo de forma descritiva: "vendas\_farmaceuticas.csv".

9. Backup:

   - Antes de converter para CSV, faça uma cópia da planilha original.

10. Considerações de Privacidade:

    - Lembrem-se de remover ou anonimizar dados sensíveis antes de compartilhar.

11. Tamanho do Arquivo:

    - Para este exemplo, usaremos um conjunto de dados relativamente pequeno. Para análises mais complexas, podem ser necessárias ferramentas mais avançadas.

12. Consistência:

    - Se você atualizar esses dados regularmente, mantenha a mesma estrutura para facilitar comparações ao longo do tempo.

<figure>
<img src="/blog/img/tutorial-analise-de-dados-colab-python-ia/3f7d86d52f-1000.webp" srcset="/blog/img/tutorial-analise-de-dados-colab-python-ia/3f7d86d52f-728.webp 728w, /blog/img/tutorial-analise-de-dados-colab-python-ia/3f7d86d52f-1000.webp 1000w" sizes="(max-width: 760px) 100vw, 720px" width="1000" height="656" alt="Salvar a planilha como CSV" loading="lazy" decoding="async">
</figure>

## **Passo 2: Utilizando IA para Criar o Código Python**

Agora que temos nossos dados preparados, vamos usar um modelo de IA para nos ajudar a criar o código Python para analise dos dados. Usarei o Claude, mas você pode usar qualquer assistente de IA de sua preferência, como o ChatGPT.

Aqui está o prompt que usaremos:

*Crie um script Python para analisar um arquivo CSV chamado 'vendas\_farmaceuticas.csv' com as colunas: Data, Produto, Região, Vendas e Custos. O script deve:*

1. *Carregar os dados*
2. *Calcular o total de vendas e custos*
3. *Identificar o produto mais vendido*
4. *Mostrar as vendas por região*
5. *Calcular o lucro (vendas - custos) ao longo do tempo*
6. *Criar visualizações para esses dados*

*Use pandas para manipulação de dados e matplotlib ou seaborn para visualizações.*

<figure>
<img src="/blog/img/tutorial-analise-de-dados-colab-python-ia/e871bdcd6d-1000.webp" srcset="/blog/img/tutorial-analise-de-dados-colab-python-ia/e871bdcd6d-728.webp 728w, /blog/img/tutorial-analise-de-dados-colab-python-ia/e871bdcd6d-1000.webp 1000w" sizes="(max-width: 760px) 100vw, 720px" width="1000" height="471" alt="Utilizando IA para Criar o Código Python" loading="lazy" decoding="async">
</figure>

## **Passo 3: Configurando o Google Colab**

Com nosso código gerado pela IA, vamos usar o Google Colab para executá-lo:

1. Abra o navegador e vá para [colab.research.google.com](http://colab.research.google.com)
2. Crie um novo notebook
3. Cole o código gerado pela IA no primeiro bloco de código

<figure>
<img src="/blog/img/tutorial-analise-de-dados-colab-python-ia/70c8749f02-1000.webp" srcset="/blog/img/tutorial-analise-de-dados-colab-python-ia/70c8749f02-728.webp 728w, /blog/img/tutorial-analise-de-dados-colab-python-ia/70c8749f02-1000.webp 1000w" sizes="(max-width: 760px) 100vw, 720px" width="1000" height="485" alt="Cole o código gerado pela IA no primeiro bloco de código" loading="lazy" decoding="async">
</figure>

## **Passo 4: Fazendo Upload do Arquivo CSV**

Antes de executar o código, faremos o upload do nosso arquivo CSV:

1. Clique no ícone de pasta no painel esquerdo
2. Clique em "Fazer upload para o armazenamento da sessão"
3. Selecione o arquivo "vendas\_farmaceuticas.csv"

<figure>
<img src="/blog/img/tutorial-analise-de-dados-colab-python-ia/c29084b59b-1000.webp" srcset="/blog/img/tutorial-analise-de-dados-colab-python-ia/c29084b59b-728.webp 728w, /blog/img/tutorial-analise-de-dados-colab-python-ia/c29084b59b-1000.webp 1000w" sizes="(max-width: 760px) 100vw, 720px" width="1000" height="500" alt="Fazendo Upload do Arquivo CSV no Colab" loading="lazy" decoding="async">
</figure>

## **Passo 5: Executando a Análise**

Agora estamos prontos para executar nossa análise:

1. Verifique se o caminho do arquivo no código corresponde ao nome do arquivo que você fez upload
2. Clique no botão de play ao lado do bloco de código ou use o atalho Shift+Enter

**Código (gerado e/ou corrigido por IA)**

Para rodar o código:

<figure>
<img src="/blog/img/tutorial-analise-de-dados-colab-python-ia/5de82b54b7-1000.webp" srcset="/blog/img/tutorial-analise-de-dados-colab-python-ia/5de82b54b7-728.webp 728w, /blog/img/tutorial-analise-de-dados-colab-python-ia/5de82b54b7-1000.webp 1000w" sizes="(max-width: 760px) 100vw, 720px" width="1000" height="499" alt="Clique no botão de play ao lado do bloco de código ou use o atalho Shift+Enter" loading="lazy" decoding="async">
</figure>

Em aproximadamente 3 segundos você verá os resultados esperados, logo abaixo do código:

<figure>
<img src="/blog/img/tutorial-analise-de-dados-colab-python-ia/833337f01e-1000.webp" srcset="/blog/img/tutorial-analise-de-dados-colab-python-ia/833337f01e-728.webp 728w, /blog/img/tutorial-analise-de-dados-colab-python-ia/833337f01e-1000.webp 1000w" sizes="(max-width: 760px) 100vw, 720px" width="1000" height="488" alt="Resultados esperados, logo abaixo do código" loading="lazy" decoding="async">
</figure>

## **Conclusão**

E pronto! Acabamos de realizar uma análise de dados complexa usando Python, sem precisar escrever uma única linha de código. Vimos como a preparação adequada dos dados e o uso de IA podem nos ajudar a gerar insights valiosos.

Esta abordagem permite que executivos e profissionais de negócios aproveitem o poder da análise de dados avançada, mesmo sem conhecimentos profundos de programação. Lembre-se, porém, que é importante entender os princípios básicos por trás dessas análises para interpretá-las corretamente e tomar decisões informadas.

Espero que este tutorial tenha sido útil! Se você tiver alguma dúvida, deixe nos comentários.

Até a próxima!

**Materiais utilizados neste tutorial:**

- [Análise dos dados - consolidado (Colab)](https://colab.research.google.com/drive/18cxI8LcYbN2LI4EXzFiQ7LIzE4xB1eTj?usp=sharing)
- [Análise de dados - passo a passo (Colab)](https://colab.research.google.com/drive/16HwEPQZucYTB6m1UnhAAJAIQRjgcJpkW?usp=sharing)
- [Planilha utilizada no exemplo (Google Sheets)](https://docs.google.com/spreadsheets/d/1sMXrwK0txfhUWHtHD6mv9g-TRnBl8qC-0-ApMH7UkiY/edit?usp=sharing)

**Ferramentas utilizadas:**

- [Colab (Google)](https://colab.research.google.com/)
- [Claude (Anthropic)](https://claude.ai/)

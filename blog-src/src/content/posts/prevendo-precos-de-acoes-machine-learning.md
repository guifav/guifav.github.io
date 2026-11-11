---
title: "Prevendo Preços de Ações com Machine Learning - Do Zero ao Deploy com ferramentas Gratuitas"
subtitle: "Passo a passo desde a captura de dados históricos de ações até a disponibilização do modelo via dashboard, com retreino automático semanal"
description: "Passo a passo desde a captura de dados históricos de ações até a disponibilização do modelo via dashboard, com retreino automático semanal"
date: 2026-02-18T13:09:03.469Z
updated: 2026-02-18T13:11:03.382Z
slug: prevendo-precos-de-acoes-machine-learning
cover: img/prevendo-precos-de-acoes-machine-learning/capa.jpg
type: newsletter
---

Existe uma pergunta que todo mundo que começa a estudar Machine Learning faz em algum momento: “dá pra prever o preço de uma ação?”. A resposta curta é: não com precisão suficiente pra ficar rico. Mas a resposta longa é muito mais interessante - e é isso que eu quero contar aqui.

Neste artigo, vou mostrar como construí um projeto completo de predição de preços usando redes neurais LSTM em PyTorch. O modelo recebe os últimos 60 dias de preços de fechamento de uma ação e tenta prever o preço do dia seguinte. Escolhi a PETR4 (Petrobras) como caso de estudo, mas a mesma abordagem funciona para qualquer ticker.

<figure>
<img src="/blog/img/prevendo-precos-de-acoes-machine-learning/d461ab65e0-1456.webp" srcset="/blog/img/prevendo-precos-de-acoes-machine-learning/d461ab65e0-728.webp 728w, /blog/img/prevendo-precos-de-acoes-machine-learning/d461ab65e0-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="813" alt="Pipeline completo: do zero ao deploy utilizando apenas ferramentas gratuitas e via navegador" loading="lazy" decoding="async">
<figcaption>Pipeline completo: do zero ao deploy utilizando apenas ferramentas gratuitas e via navegador</figcaption>
</figure>

O mais importante: tudo roda no navegador, de graça. Google Colab para treinar, HuggingFace para hospedar, GitHub para versionar. Você não precisa de uma GPU própria nem de nenhuma infra local.

***

## Por que LSTM?

Preços de ações são séries temporais. O valor de hoje carrega informação sobre o que aconteceu ontem, na semana passada, no mês anterior. Redes neurais tradicionais (feedforward) não capturam bem essa dependência temporal -- elas tratam cada entrada como se fosse independente.

A LSTM (Long Short-Term Memory) foi projetada exatamente para esse tipo de problema. Ela é um tipo de rede neural recorrente que possui uma “memória interna” controlada por três portões (forget, input e output). Isso permite que a rede decida o que lembrar e o que esquecer, aprendendo padrões de longo prazo nos dados.

Na prática, a LSTM recebe uma sequência de preços passados e aprende a reconhecer padrões que se repetem: tendências de alta e baixa, volatilidade cíclica, reversões. Ela não “entende” o mercado -- ela detecta regularidades estatísticas no histórico.

<figure>
<img src="/blog/img/prevendo-precos-de-acoes-machine-learning/a94b52b919-1456.webp" srcset="/blog/img/prevendo-precos-de-acoes-machine-learning/a94b52b919-728.webp 728w, /blog/img/prevendo-precos-de-acoes-machine-learning/a94b52b919-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="813" alt="Porque usar LSTMs" loading="lazy" decoding="async">
<figcaption>Porque usar LSTMs</figcaption>
</figure>

***

## A pipeline completa

O projeto segue um fluxo que vai desde a coleta de dados até o deploy em produção. Vou passar por cada etapa.

### Coleta de dados

Usei a biblioteca `yfinance` para baixar o histórico da PETR4 desde janeiro de 2018. São mais de 2000 dias de negociação, cada um com preço de abertura, máxima, mínima, fechamento e volume. Para a LSTM, usei apenas o preço de fechamento.

python

```
SYMBOL = 'PETR4.SA'
START_DATE = '2018-01-01'

df = yf.download(SYMBOL, start=START_DATE, end=datetime.now().strftime('%Y-%m-%d'))
```

A análise exploratória revelou o que quem acompanha a Petrobras já sabe: ciclos marcantes, picos de volatilidade em períodos de crise e uma tendência geral de valorização nos últimos anos. Gráficos com Plotly mostrando médias móveis de 50 e 200 dias ajudaram a visualizar esses padrões.

<figure>
<img src="/blog/img/prevendo-precos-de-acoes-machine-learning/e363df0a2c-1456.webp" srcset="/blog/img/prevendo-precos-de-acoes-machine-learning/e363df0a2c-728.webp 728w, /blog/img/prevendo-precos-de-acoes-machine-learning/e363df0a2c-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="567" alt="Análise exploratória do preço de fechamento da ação PERT4.SA" loading="lazy" decoding="async">
<figcaption>Análise exploratória do preço de fechamento da ação PERT4.SA</figcaption>
</figure>

### Pré-processamento: onde muita gente erra

Três decisões de pré-processamento fazem toda a diferença entre um modelo que funciona e um que só parece funcionar.

**Normalização.** A LSTM funciona muito melhor com dados entre 0 e 1. Usei um MinMaxScaler para transformar os preços. Sem esse passo, o modelo tem dificuldade para convergir -- e é algo que muita gente esquece na primeira tentativa.

**Janelas deslizantes.** O modelo recebe os 60 dias anteriores como entrada e prevê o próximo dia como saída. Na prática, a gente “desliza” uma janela de 60 posições sobre a série temporal, gerando milhares de pares (entrada, saída) para treino.

**Split temporal.** Esse é talvez o ponto mais crítico. Dividi os dados em 70% treino, 15% validação e 15% teste, respeitando a ordem cronológica. Sem embaralhar. Em séries temporais, se você embaralha os dados, está vazando informação do futuro para o passado. O modelo vai parecer brilhante nos números mas não vai generalizar para dados novos. Isso é um erro clássico.

python

```
# Split SEM embaralhar -- ordem cronológica preservada
X_train, y_train = X[:n_train], y[:n_train]
X_val, y_val = X[n_train:n_train+n_val], y[n_train:n_train+n_val]
X_test, y_test = X[n_train+n_val:], y[n_train+n_val:]
```

### Arquitetura do modelo

O modelo é uma LSTM com duas camadas empilhadas de 128 neurônios cada, seguida de um bloco fully connected (128 -\> 64 -\> 32 -\> 1) com ReLU e dropout de 0.2 entre as camadas. O dropout é importante para evitar overfitting - ele “desliga” aleatoriamente 20% dos neurônios durante o treino, forçando o modelo a não depender de caminhos específicos.

python

```
class LSTMModel(nn.Module):
    def __init__(self, input_size=1, hidden_size=128, num_layers=2, dropout=0.2):
        super().__init__()
        self.lstm = nn.LSTM(input_size, hidden_size, num_layers,
                            batch_first=True, dropout=dropout)
        self.fc = nn.Sequential(
            nn.Linear(hidden_size, 64), nn.ReLU(), nn.Dropout(dropout),
            nn.Linear(64, 32), nn.ReLU(),
            nn.Linear(32, 1)
        )

    def forward(self, x):
        lstm_out, _ = self.lstm(x)
        return self.fc(lstm_out[:, -1, :])
```

Essa arquitetura tem cerca de 210 mil parâmetros treináveis. É um modelo relativamente compacto, o que é intencional: para uma única feature (preço de fechamento) com janela de 60 dias, não faz sentido usar algo mais pesado.

<figure>
<img src="/blog/img/prevendo-precos-de-acoes-machine-learning/68253e271f-1456.webp" srcset="/blog/img/prevendo-precos-de-acoes-machine-learning/68253e271f-728.webp 728w, /blog/img/prevendo-precos-de-acoes-machine-learning/68253e271f-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="813" alt="Arquitetura do modelo treinado LSTM" loading="lazy" decoding="async">
<figcaption>Arquitetura do modelo treinado</figcaption>
</figure>

### Treinamento

Para o treinamento, usei Adam como otimizador com learning rate de 0.001, MSE como função de perda e dois mecanismos de controle: um ReduceLROnPlateau (que reduz o learning rate pela metade quando o modelo para de melhorar) e early stopping com paciência de 15 épocas.

O treinamento no Colab com GPU T4 leva poucos minutos. A curva de aprendizado mostra as losses de treino e validação caindo juntas -- quando as duas acompanham, é um bom sinal. Significa que o modelo está aprendendo padrões reais nos dados, não memorizando.

***

## Avaliação: o que os números dizem

Usei três métricas no conjunto de teste (os 15% finais, dados que o modelo nunca viu):

- **MAE (Mean Absolute Error):** o erro médio em reais. Se o MAE é 1.50, o modelo erra em média R$1,50.
- **RMSE (Root Mean Squared Error):** similar ao MAE, mas penaliza mais os erros grandes.
- **MAPE (Mean Absolute Percentage Error):** o erro em percentual, que facilita comparar entre ações de preços diferentes.

O gráfico mais revelador é o de valores reais versus previsões no conjunto de teste. O modelo acompanha a tendência geral, mas com um atraso característico - algo esperado em modelos que usam apenas histórico de preços.

Aqui vai uma reflexão honesta: o erro foi considerável. Para melhorar, o primeiro passo seria reduzir o período de treinamento para 3 anos em vez de 7. A Petrobras de 2018 é muito diferente da Petrobras de 2025 em termos de faixa de preço e comportamento. Dados mais recentes seriam mais representativos do regime atual.

<figure>
<img src="/blog/img/prevendo-precos-de-acoes-machine-learning/0c2949fd27-1456.webp" srcset="/blog/img/prevendo-precos-de-acoes-machine-learning/0c2949fd27-728.webp 728w, /blog/img/prevendo-precos-de-acoes-machine-learning/0c2949fd27-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="790" alt="Resultado do treinamento do modelo" loading="lazy" decoding="async">
<figcaption>Resultado do treinamento do modelo</figcaption>
</figure>

Mas optei por não otimizar o modelo até ele ficar “bonito”. O objetivo aqui é educacional, não lançar um produto de recomendação. Para um modelo real, eu faria muito mais testes com diferentes arquiteturas, hiperparâmetros e períodos de treinamento.

***

## Do modelo treinado ao deploy

Com o modelo treinado e avaliado, o próximo passo é colocar tudo em produção. Exportei três artefatos: os pesos do modelo (lstm\_model.pth), o normalizador (scaler.joblib) e a configuração com hiperparâmetros e métricas (config.json). Antes do upload, rodo uma verificação: carrego o modelo salvo do zero e comparo a previsão com o modelo original. Se não forem idênticas, algo deu errado no salvamento.

<figure>
<img src="/blog/img/prevendo-precos-de-acoes-machine-learning/8cf98ba199-1456.webp" srcset="/blog/img/prevendo-precos-de-acoes-machine-learning/8cf98ba199-728.webp 728w, /blog/img/prevendo-precos-de-acoes-machine-learning/8cf98ba199-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="975" alt="3 artefatos gerados com o treinamento" loading="lazy" decoding="async">
<figcaption>3 artefatos gerados com o treinamento</figcaption>
</figure>

### HuggingFace Hub

O modelo e os artefatos vão para o HuggingFace Hub, que funciona como um repositório de modelos. Qualquer pessoa pode baixar, carregar em PyTorch e usar. Junto vai um model card documentando a arquitetura, métricas e instruções de uso.

<figure>
<img src="/blog/img/prevendo-precos-de-acoes-machine-learning/f9ee405a08-1456.webp" srcset="/blog/img/prevendo-precos-de-acoes-machine-learning/f9ee405a08-728.webp 728w, /blog/img/prevendo-precos-de-acoes-machine-learning/f9ee405a08-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="1122" alt="Model Card para o modelo disponibilizado no Hugging Face Hub" loading="lazy" decoding="async">
<figcaption>Model Card para o modelo disponibilizado no Hugging Face Hub</figcaption>
</figure>

### HuggingFace Spaces

O dashboard e a API rodam num HuggingFace Space com Docker. A aplicação combina FastAPI (para os endpoints REST) com Gradio (para o dashboard interativo) no mesmo servidor.

<figure>
<img src="/blog/img/prevendo-precos-de-acoes-machine-learning/2839a377e0-1456.webp" srcset="/blog/img/prevendo-precos-de-acoes-machine-learning/2839a377e0-728.webp 728w, /blog/img/prevendo-precos-de-acoes-machine-learning/2839a377e0-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="911" alt="Space no Hugging Face para o projeto" loading="lazy" decoding="async">
<figcaption>Space no Hugging Face para o projeto</figcaption>
</figure>

O dashboard tem três abas: uma para gerar previsões em tempo real (digita o ticker, clica e vê o resultado), uma com informações detalhadas sobre o modelo, e uma com documentação da API incluindo exemplos de curl.

A API expõe cinco endpoints:

bash

```
# Verificar se está no ar
curl https://guifav-lstm-petr4-stock-prediction.hf.space/health

# Previsão automática (baixa dados recentes e roda o modelo)
curl https://guifav-lstm-petr4-stock-prediction.hf.space/predict/PETR4.SA

# Métricas do modelo e operacionais
curl https://guifav-lstm-petr4-stock-prediction.hf.space/metrics

# Detalhes da arquitetura e treinamento
curl https://guifav-lstm-petr4-stock-prediction.hf.space/model/info
```

Tudo roda no tier gratuito do HuggingFace. Funciona surpreendentemente bem para um projeto educacional.

### Retreino automático

Um detalhe que faz diferença: configurei um GitHub Action que toda segunda-feira, antes da abertura do mercado, retreina o modelo automaticamente. O workflow baixa os dados mais recentes via yfinance, retreina a LSTM, publica os novos artefatos no Hub e atualiza o Space. Tudo sem intervenção manual. O GitHub oferece 2000 minutos gratuitos por mês para Actions, e cada retreino leva menos de 15 minutos em CPU.

<figure>
<img src="/blog/img/prevendo-precos-de-acoes-machine-learning/7c05d3d276-1456.webp" srcset="/blog/img/prevendo-precos-de-acoes-machine-learning/7c05d3d276-728.webp 728w, /blog/img/prevendo-precos-de-acoes-machine-learning/7c05d3d276-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="813" alt="Predição de Ações da B3 usando LSTM" loading="lazy" decoding="async">
<figcaption>Predição de Ações da B3 usando LSTM</figcaption>
</figure>

***

## Quer reproduzir com outra ação?

O projeto foi desenhado para ser reutilizável. O passo a passo:

<figure>
<img src="/blog/img/prevendo-precos-de-acoes-machine-learning/e5e0d42d3f-1456.webp" srcset="/blog/img/prevendo-precos-de-acoes-machine-learning/e5e0d42d3f-728.webp 728w, /blog/img/prevendo-precos-de-acoes-machine-learning/e5e0d42d3f-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="813" alt="Passo a passo para replicar o projeto" loading="lazy" decoding="async">
<figcaption>Passo a passo para replicar o projeto utilizando outra ação</figcaption>
</figure>

1. Faça um fork do repositório no GitHub
2. Abra o notebook no Google Colab pelo badge “Open in Colab”
3. Ative a GPU gratuita (Runtime \> Change runtime type \> T4 GPU)
4. Troque o ticker de `PETR4.SA` para a ação que quiser (VALE3.SA, ITUB4.SA, BBDC4.SA, qualquer uma listada no Yahoo Finance)
5. Execute todas as células -- o notebook é autocontido e explica cada etapa
6. Crie um token no HuggingFace com permissão Write
7. Rode as células de upload para publicar no Hub e criar o Space
8. Teste sua API via curl ou acesse o dashboard pelo navegador

Não precisa instalar nada. Tudo roda no navegador, de graça.

***

## Limitações (uma parte muito importante)

Preciso ser direto aqui: esse modelo é educacional. Não use para tomar decisões financeiras reais.

Preços de ações são influenciados por política, macroeconomia, sentimento de mercado, notícias, decisões regulatórias -- fatores que um modelo treinado apenas com histórico de preços não captura. A LSTM aprende correlações estatísticas no passado, mas o mercado financeiro é um sistema complexo onde o passado não garante o futuro.

Além disso, a previsão é limitada a um dia à frente, usa apenas uma feature (preço de fechamento) e a performance pode degradar em cenários atípicos (crises, mudanças regulatórias, eventos inesperados).

O valor desse projeto está no aprendizado: entender como funciona uma LSTM, como preparar dados de séries temporais, como avaliar um modelo de regressão, como deployar com Docker e APIs, como usar o ecossistema HuggingFace. São habilidades que se transferem para problemas onde modelos preditivos realmente fazem sentido.

***

## Próximos passos

Para quem quiser evoluir o projeto, algumas ideias:

- Adicionar mais features: volume, indicadores técnicos como RSI e MACD, médias móveis
- Testar arquiteturas alternativas: Transformer, CNN-LSTM híbrido, modelos de atenção
- Implementar previsão multi-step (vários dias à frente em vez de apenas um)
- Adicionar backtesting para simular performance em cenários reais
- Reduzir o período de treinamento para capturar o regime mais recente da ação

***

**Links do projeto:**

- Dashboard: [https<!---->://huggingface.co/spaces/guifav/lstm-petr4-stock-prediction](https://huggingface.co/spaces/guifav/lstm-petr4-stock-prediction)

- Modelo: [https<!---->://huggingface.co/guifav/lstm-petr4-stock-prediction](https://huggingface.co/guifav/lstm-petr4-stock-prediction)

- Repositório: [https<!---->://github.com/guifav/previsao\_preco\_acoes](https://github.com/guifav/previsao_preco_acoes)

- Vídeo explicando o projeto:

  <div class="video"><iframe src="https://www.youtube-nocookie.com/embed/pBZtLsDH-cU?rel=0" title="Vídeo do YouTube" loading="lazy" allow="fullscreen; picture-in-picture" allowfullscreen referrerpolicy="strict-origin-when-cross-origin"></iframe></div>

***

*Guilherme Favaron é CTO do GRI Institute, uma organização global de inteligência imobiliária e de infraestrutura. Escreve sobre inteligência artificial aplicada em ia-aplicada.substack.com e é autor do livro “Desbloqueando a Inteligência Artificial”.*

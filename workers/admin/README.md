# Worker `blog-admin`

Painel de assinantes da newsletter em `guifav.dev/admin` (issue #40). Só o Gui tem acesso.

| Rota | O que faz |
| --- | --- |
| `GET /admin` | Lista os assinantes do segmento "IA Aplicada", com busca por email, filtros (status, origem, pago) e contadores, em páginas de 100. |
| `GET /admin/export.csv` | Os mesmos filtros, em CSV, sem paginação. Células que começam com `=`, `+`, `-` ou `@` ganham `'` na frente, para não virarem fórmula. |
| `POST /admin/unsubscribe` | Descadastra só da newsletter: grava a auditoria `admin_unsubscribed_at` no contato e o tira do tópico "Expert Insights, de Guilherme Favaron" (`TOPIC_ID`), sem tocar no status global da conta, então a pessoa continua recebendo os outros envios. Só mostra sucesso se a releitura no Resend mostrar o mesmo contato fora do tópico e com esta auditoria, e só então atualiza a cópia. Se a cópia falhar depois disso, a página diz que o Resend já descadastrou e a próxima sincronização corrige. Exige a caixa "confirmo" e `Origin` igual ao do site. |
| cron `17 * * * *` | Copia o segmento do Resend para o D1 `blog-admin`, com dois GET por contato, o contato e os tópicos dele (a listagem do Resend não traz propriedades nem tópicos); descadastrado é quem saiu do tópico da newsletter ou da conta inteira, a 4 req/s para toda chamada, contados de início a início (a latência da API não se soma à espera). A duração de cada execução fica em `last_sync_ms` (`meta`); a primeira execução com tópicos, com espera depois de cada resposta, levou cerca de 10 min para 647 contatos. Respeita o `Retry-After` inteiro; se ele passar de 60 s, desiste e deixa para a hora seguinte. Para antes de 12 min (a chamada em andamento é cancelada pelo tempo restante, e nada é gravado se o prazo passar) ou 9.000 chamadas (o cron tem 15 min e 10.000 subrequests no plano pago): com a taxa de 4 req/s, isso comporta cerca de 1.400 contatos (duas chamadas por contato, cerca de 5,5 min para 646). A listagem só vale com término explícito (`has_more=false`); resposta sem lista ou cursor que não avança é erro. As linhas antigas só saem depois que todas as novas foram gravadas, na mesma transação dos metadados: uma falha no meio não apaga nada e fica registrada no painel, sem email. Um descadastro gravado pelo painel durante a leitura não é desfeito pela cópia: o painel incrementa `rev` no D1, o cron tira as revisões antes de qualquer GET, e o status local vence só se a revisão mudou (ordem por contador, sem comparar relógios). Fora disso vale sempre o Resend. Execuções sobrepostas: a que começou por último é a dona (`sync_owner` em `meta`), e toda escrita, a limpeza e o registro de erro ficam condicionados a isso. Uma execução antiga que termine depois não grava nada e falha como "substituída". O prazo é conferido também antes de cada lote e da publicação. |

| `GET /admin/rascunhos` | Rascunhos do blog (issue #62): título, slug, data da prévia e estado (aguardando aprovação, aprovado em ..., ou versão mudou depois da aprovação). Nos artigos aprovados, o estado da publicação, lido do GitHub na hora (#82, ver "Publicação pelo GitHub" abaixo). Os derivados de um artigo aparecem logo abaixo dele, marcados com a etiqueta e "só leitura": a versão LinkedIn (#73) e qualquer outro artefato do mesmo artigo enviado com rótulo (#77), como "Roteiro do short". Depois que o artigo é publicado e o rascunho dele sai do R2, os derivados continuam agrupados abaixo de uma linha "publicado no site", com o título e o link do post (#80): o painel consulta `/blog/<slug>/` no site, uma vez por artigo pai, e lê o título do `<title>`. Sem rascunho e sem post no ar (404, redirecionamento, erro ou tempo esgotado), o derivado aparece sozinho com "artigo não encontrado". |
| `GET /admin/rascunhos/<slug>/` | Prévia do post como vai sair, com uma barra no topo e o botão "Aprovar publicação". Vem do bucket R2 privado `blog-rascunhos` (sem domínio público), com `Cache-Control: private, no-store`, `noindex` e CSP própria. |
| `GET /admin/rascunhos/<slug>/<arquivo>` | Imagens e vídeo do rascunho: só os listados no manifesto, lidos da versão para a qual ele aponta. |
| `POST /admin/rascunhos/<slug>/aprovar` | Derivado (LinkedIn ou com rótulo): 400, nada é gravado (só leitura). Artigo: grava em `draft_approvals` (D1) a aprovação da versão exata que foi vista: o HTML (`html_sha256`) e a fonte (`source_sha256`, Markdown e imagens) juntos, quem e quando. Trocar só uma imagem, com o mesmo HTML, já é versão nova. Se a versão mudou depois que a página abriu, responde 409 e não grava nada. Depois dispara a publicação no GitHub (#82) e avisa o Roger por email do resultado; o resultado do disparo (`dispatched_at` ou `dispatch_error`) e o do aviso (`notify_error`) ficam na própria linha, e a aprovação continua valendo mesmo se os dois falharem. Exige "confirmo" e `Origin` do site. |
| `POST /admin/rascunhos/<slug>/disparar` | "Disparar de novo" (#82): repete o disparo da publicação da versão atual, só se ela está aprovada, e grava o novo resultado na linha da aprovação. Não manda email. Derivado: 400. Versão mudada ou não aprovada: 409. Nada é disparado nesses casos. Exige `Origin` do site, como as outras ações. |

Qualquer outro caminho que a rota `guifav.dev/admin*` pegue (ex.: `/administrar`) segue para o GitHub Pages.

### Rascunhos (issue #62)

O rascunho nunca entra no repo antes da aprovação. Na KVM:

1. `python3 blog-src/scripts/draft_preview.py enviar <slug>.md --imagens <pasta>`: faz o build do blog numa cópia temporária com o rascunho e `DRAFT_PREVIEW=1` (sem GA4, sem Clarity, `noindex`), troca os caminhos dos arquivos do post para `/admin/rascunhos/<slug>/` e envia ao R2 a versão inteira em `<slug>/v/<versão>/` e, por último, o manifesto `<slug>/manifest.json`, que aponta para ela. A versão é o sha256 do HTML junto com o da fonte. Envio interrompido deixa o manifesto na versão anterior, inteira.
   A versão para a newsletter do LinkedIn vai com `--linkedin --artigo <slug-do-artigo>` (#73): o manifesto ganha `kind: "linkedin"` e `parent`, a prévia sai com uma barra "só leitura" e link para o artigo, sem formulário de aprovação.
   Qualquer outro artefato do mesmo artigo (roteiro de short, vídeo, carrossel) vai com `--derivado "<rótulo>" --artigo <slug-do-artigo>` (#77): o manifesto ganha `kind: "derivado"`, `parent` e `rotulo`, e o rótulo aparece na etiqueta da lista e na barra da prévia. Artefato novo não exige mudança de código. O rótulo tem de 1 a 40 caracteres, sem caractere de controle nem espaço nas pontas (a mesma regra no script e no Worker).
   A fonte do rascunho (o Markdown e as imagens) vai junto, em `<slug>/v/<versão>/fonte/` (`<slug>.md` e `img/...`), e o manifesto ganha o campo `fonte`, com o caminho e o sha256 de cada arquivo (#82). O conteúdo de `fonte/` é exatamente o que gerou o `source_sha256` da versão, e o script confere isso antes de enviar. O Worker só serve o que está em `files`, então a fonte nunca sai pelo painel. Derivados levam a fonte pela mesma regra; manifestos antigos, sem `fonte`, continuam válidos.
2. O Gui abre `guifav.dev/admin/rascunhos`, lê e aprova.
3. A aprovação dispara o workflow `publicar.yml` (#82): ele baixa a fonte da versão aprovada do R2 (a pasta `fonte/` acima), confere o hash contra a aprovação no D1 e abre o PR de publicação, com o mesmo Markdown e as mesmas imagens. O Roger recebe o email de aviso e coordena a revisão do PR, como antes. `python3 blog-src/scripts/draft_preview.py status <slug>.md --imagens <pasta>` continua confirmando no D1 que a fonte local é a aprovada.
4. Depois do merge, `python3 blog-src/scripts/draft_preview.py remover <slug>` tira do R2 o manifesto, a prévia e a fonte da versão atual.

### Publicação pelo GitHub (issue #82)

Contrato com o `.github/workflows/publicar.yml`:

- gatilho `workflow_dispatch` na `main`, com os inputs `slug` e `versao` (a versão inteira, 64 hex);
- o `run-name` é `Publica <slug> (<versão>)`, com a versão inteira (64 hex), porque as expressões do Actions não cortam texto. O painel lê o `run-name` em palavras (letras, números e `-`) e exige uma palavra igual ao slug e outra igual à versão inteira. Assim `mcp` não casa com `mcp-aberto`, um slug terminado em hex de outra versão não passa pelo slug mais curto, e a versão tem fronteira de não hex dos dois lados;
- o PR sai do branch `publica/<slug>-<versão8>`, aberto pelo `github-actions[bot]`;
- a workflow é idempotente: se o branch ou o PR da versão já existe, termina sem erro. Por isso o "Disparar de novo" é seguro.

Na aprovação, o Worker chama `POST /repos/guifav/guifav.github.io/actions/workflows/publicar.yml/dispatches` com o segredo `GITHUB_DISPATCH_TOKEN`, com prazo de 5 s. Resposta 2xx grava `dispatched_at`. Qualquer outra coisa grava o motivo em `dispatch_error` ("GitHub respondeu 422: ...", "falha de rede", "tempo esgotado" ou "disparo sem configuração", quando falta o token) e mostra o botão "Disparar de novo" na lista. O resultado do disparo é gravado antes do email. O email ao Roger diz "publicação disparada" ou "disparo falhou", com o motivo, tem prazo de 5 s e não é mais o gatilho. Se o D1 falhar ao gravar o resultado do disparo, na aprovação ou no "Disparar de novo", a tela avisa que a linha pode mostrar o estado anterior.

A lista calcula o estado de cada artigo aprovado na hora de abrir, do D1 e do GitHub, com o mesmo token. São no máximo duas leituras do GitHub por artigo aprovado, feitas em paralelo, cada uma com prazo de 5 s e corpo limitado a 4 MiB (a página de 100 execuções da API passa de 1,2 MB). Rascunho não aprovado, ou com a versão mudada depois da aprovação, não consulta o GitHub.

| Estado na lista | De onde vem | Na tela |
| --- | --- | --- |
| aguardando aprovação | D1 | link para a prévia, que tem o botão de aprovar |
| disparo falhou (motivo) | `dispatch_error`, sem PR da versão | botão "Disparar de novo" |
| publicação não disparada | aprovação sem `dispatched_at` nem motivo (anterior ao #82, ou registro que falhou) | botão "Disparar de novo" |
| preparando o PR (disparado há N min) | sem PR; execução na fila ou rodando (o tempo conta da execução), ou `dispatched_at` recente sem execução visível | link para a execução, quando ela já aparece |
| parado: nenhuma execução encontrada | `dispatched_at` há mais de 15 min, sem PR nem execução | botão "Disparar de novo" |
| publicação recusada | execução terminou com falha (hash que não bate, slug já publicado; o motivo fica no resumo da execução) | link para a execução |
| execução terminou sem abrir o PR, ou interrompida | execução com sucesso sem PR, ou cancelada | link para a execução e botão "Disparar de novo" |
| PR aberto #N, CI verde, vermelha, rodando ou pendente | PR do branch da versão e as execuções de Actions do head dele | link para o PR |
| PR #N mergeado, aguardando o deploy do site | PR mergeado, `/blog/<slug>/` ainda não responde 200 | link para o PR |
| PR #N fechado sem merge | PR fechado | link para o PR |
| publicado em (data) | PR mergeado e `/blog/<slug>/` respondendo 200 | link para o post |
| estado indisponível | GitHub fora do ar, prazo esgotado ou resposta inválida | nada; o resto da lista continua |

Leituras: `GET /pulls?head=guifav:publica/<slug>-<versão8>&state=all`, sempre; depois, só uma destas: `GET /actions/runs?head_sha=<head do PR>&per_page=100` (PR aberto), `GET /actions/workflows/publicar.yml/runs?event=workflow_dispatch&per_page=100&created=>=<aprovação menos 5 min>` (sem PR; um disparo manual com `gh workflow run`, que não passa pelo D1, aparece também, inclusive depois de um disparo falho pelo painel, junto com o motivo e o botão) ou a consulta ao post no site (PR mergeado, a mesma do #80). A execução certa é a que casa pela regra do contrato acima e tem a tentativa mais recente (`run_started_at`, porque uma reexecução mantém o id), e o "há N min" conta dessa tentativa. A janela começa na aprovação, porque toda execução desta versão vem depois dela: nem um disparo manual nem uma execução antiga reexecutada depois de um novo disparo ficam de fora. Se o `total_count` diz que a janela não coube na página, o estado é "estado indisponível", ache-se ou não uma execução, porque a vigente pode estar na página seguinte. A cor da CI considera só a execução mais recente de cada workflow no head do PR, pela última tentativa (`run_started_at`, porque reexecutar mantém o id): uma falha substituída por um novo disparo ou por uma reexecução não deixa a CI vermelha, e uma reexecução que roda ou falha depois de um verde aparece. Com uma página incompleta de execuções da CI, ela aparece como "CI indisponível". Com um motivo de disparo gravado e o GitHub fora do ar (ou sem execução, ou numa página incompleta), a lista mostra o motivo e o botão, não "estado indisponível". Com um motivo gravado e o PR da versão existente (o GitHub aceitou o disparo depois do prazo), vale o PR.

O token é fino, da conta `guifav`, só neste repositório, com `Actions: write` (disparar e ler execuções). PRs e execuções deste repositório público são lidos com a leitura de repositórios públicos que todo token fino tem. Ele expira (até um ano): a falha aparece na lista, como "disparo falhou (GitHub respondeu 401 ...)", no primeiro clique depois da expiração.

## Acesso

1. **Cloudflare Access:** aplicação "Painel de assinantes (guifav.dev/admin)", com allow só para o email do Gui, login por PIN no email e sessão de 24 h.
2. **No Worker:** confere de novo o JWT do Access (`Cf-Access-Jwt-Assertion`):
   - assinatura RS256 com as chaves de `guifav.cloudflareaccess.com/cdn-cgi/access/certs`;
   - emissor, `aud` da aplicação (`ACCESS_AUD`) e validade;
   - email em `ALLOWED_EMAILS`.

   Sem isso, toda rota do painel responde 403, mesmo se a aplicação do Access for apagada ou mal configurada.

## Testes

```
npm test
```

Node 22.18 ou mais novo; a CI usa a versão do `blog-src/.nvmrc`. O D1 dos testes é SQLite de verdade (`node:sqlite`), com as migrações do repo. O Resend falso devolve as propriedades no formato documentado (`{value, type}`). O GitHub falso cobre cada estado da tabela acima, o disparo aceito, recusado, sem rede e sem resposta até o prazo. Os JWTs são assinados com RSA.

## Deploy

O deploy é manual, depois do merge. O proxy da Cloudflare em `guifav.dev` já está ligado (2026-11-11), e a aplicação do Access e o D1 já existem.

1. **Chave do Resend dedicada:** `POST /api-keys` com `full_access`, nome `blog-admin`, gravada direto num arquivo 0600 em `/root/.hermes/credentials/`.
2. **Tabelas:** `npx wrangler@4 d1 migrations apply blog-admin --remote` (inclui a `0003_draft_dispatch.sql` do #82, com as colunas `dispatched_at` e `dispatch_error`).
   **Bucket dos rascunhos (issue #62):** `npx wrangler@4 r2 bucket create blog-rascunhos`, sem domínio público nem acesso `r2.dev`.
3. **Publicação:** `npx wrangler@4 deploy`.
4. **Segredos:** `RESEND_API_KEY`, `ALLOWED_EMAILS`, `DRAFT_NOTIFY_FROM` (remetente num domínio verificado no Resend), `DRAFT_NOTIFY_TO` (o Roger) e `GITHUB_DISPATCH_TOKEN` (#82, token fino descrito acima), de uma vez (`npx wrangler@4 secret bulk`, lendo JSON pela entrada padrão). O disparo só funciona depois que o `publicar.yml` existe na `main`; antes disso, a aprovação grava "GitHub respondeu 404" e a lista mostra o botão "Disparar de novo".
5. **Primeira cópia:** rodar o cron uma vez pelo painel da Cloudflare, ou esperar o minuto 17, e conferir `last_sync_count` no D1.
6. **Conferência:**
   - sem login, `/admin` vai para o Access;
   - com JWT inválido direto no Worker, responde 403;
   - o resto do site continua idêntico (comparação byte a byte);
   - com login do Gui, o painel mostra os contadores do Resend.

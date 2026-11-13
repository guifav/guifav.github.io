# Worker `blog-admin`

Painel de assinantes da newsletter em `guifav.dev/admin` (issue #40). Só o Gui tem acesso.

| Rota | O que faz |
| --- | --- |
| `GET /admin` | Lista os assinantes do segmento "IA Aplicada", com busca por email, filtros (status, origem, pago) e contadores, em páginas de 100. |
| `GET /admin/export.csv` | Os mesmos filtros, em CSV, sem paginação. Células que começam com `=`, `+`, `-` ou `@` ganham `'` na frente, para não virarem fórmula. |
| `POST /admin/unsubscribe` | Descadastra só da newsletter: grava a auditoria `admin_unsubscribed_at` no contato e o tira do tópico "Expert Insights, de Guilherme Favaron" (`TOPIC_ID`), sem tocar no status global da conta, então a pessoa continua recebendo os outros envios. Só mostra sucesso se a releitura no Resend mostrar o mesmo contato fora do tópico e com esta auditoria, e só então atualiza a cópia. Se a cópia falhar depois disso, a página diz que o Resend já descadastrou e a próxima sincronização corrige. Exige a caixa "confirmo" e `Origin` igual ao do site. |
| cron `17 * * * *` | Copia o segmento do Resend para o D1 `blog-admin`, com dois GET por contato, o contato e os tópicos dele (a listagem do Resend não traz propriedades nem tópicos); descadastrado é quem saiu do tópico da newsletter ou da conta inteira, a 4 req/s para toda chamada, contados de início a início (a latência da API não se soma à espera). A duração de cada execução fica em `last_sync_ms` (`meta`); a primeira execução com tópicos, com espera depois de cada resposta, levou cerca de 10 min para 647 contatos. Respeita o `Retry-After` inteiro; se ele passar de 60 s, desiste e deixa para a hora seguinte. Para antes de 12 min (a chamada em andamento é cancelada pelo tempo restante, e nada é gravado se o prazo passar) ou 9.000 chamadas (o cron tem 15 min e 10.000 subrequests no plano pago): com a taxa de 4 req/s, isso comporta cerca de 1.400 contatos (duas chamadas por contato, cerca de 5,5 min para 646). A listagem só vale com término explícito (`has_more=false`); resposta sem lista ou cursor que não avança é erro. As linhas antigas só saem depois que todas as novas foram gravadas, na mesma transação dos metadados: uma falha no meio não apaga nada e fica registrada no painel, sem email. Um descadastro gravado pelo painel durante a leitura não é desfeito pela cópia: o painel incrementa `rev` no D1, o cron tira as revisões antes de qualquer GET, e o status local vence só se a revisão mudou (ordem por contador, sem comparar relógios). Fora disso vale sempre o Resend. Execuções sobrepostas: a que começou por último é a dona (`sync_owner` em `meta`), e toda escrita, a limpeza e o registro de erro ficam condicionados a isso. Uma execução antiga que termine depois não grava nada e falha como "substituída". O prazo é conferido também antes de cada lote e da publicação. |

| `GET /admin/rascunhos` | Rascunhos do blog (issue #62): título, slug, data da prévia e estado (aguardando aprovação, aprovado em ..., ou versão mudou depois da aprovação). |
| `GET /admin/rascunhos/<slug>/` | Prévia do post como vai sair, com uma barra no topo e o botão "Aprovar publicação". Vem do bucket R2 privado `blog-rascunhos` (sem domínio público), com `Cache-Control: private, no-store`, `noindex` e CSP própria. |
| `GET /admin/rascunhos/<slug>/<arquivo>` | Imagens e vídeo do rascunho, só os listados no manifesto. |
| `POST /admin/rascunhos/<slug>/aprovar` | Grava em `draft_approvals` (D1) a aprovação do HTML exato que foi visto (`html_sha256`), com a fonte (`source_sha256`), quem e quando. Se a prévia mudou depois que a página abriu, responde 409 e não grava nada. Depois avisa o Roger por email; falha no aviso fica registrada na própria linha (`notify_error`) e a aprovação continua valendo. Exige "confirmo" e `Origin` do site. |

Qualquer outro caminho que a rota `guifav.dev/admin*` pegue (ex.: `/administrar`) segue para o GitHub Pages.

### Rascunhos (issue #62)

O rascunho nunca entra no repo antes da aprovação. Na KVM:

1. `python3 blog-src/scripts/draft_preview.py enviar <slug>.md --imagens <pasta>`: faz o build do blog numa cópia temporária com o rascunho e `DRAFT_PREVIEW=1` (sem GA4, sem Clarity, `noindex`), troca os caminhos dos arquivos do post para `/admin/rascunhos/<slug>/` e envia ao R2, com o manifesto por último.
2. O Gui abre `guifav.dev/admin/rascunhos`, lê e aprova.
3. `python3 blog-src/scripts/draft_preview.py status <slug>.md --imagens <pasta>` confirma no D1 que a fonte atual é a aprovada. Só então o Roger abre o PR de publicação, com o mesmo Markdown e as mesmas imagens.
4. Depois do merge, `python3 blog-src/scripts/draft_preview.py remover <slug>` tira a prévia do R2.

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

Node 22.18 ou mais novo. O D1 dos testes é SQLite de verdade (`node:sqlite`), com a migração do repo. O Resend falso devolve as propriedades no formato documentado (`{value, type}`). Os JWTs são assinados com RSA.

## Deploy

O deploy é manual, depois do merge. O proxy da Cloudflare em `guifav.dev` já está ligado (2026-11-11), e a aplicação do Access e o D1 já existem.

1. **Chave do Resend dedicada:** `POST /api-keys` com `full_access`, nome `blog-admin`, gravada direto num arquivo 0600 em `/root/.hermes/credentials/`.
2. **Tabelas:** `npx wrangler@4 d1 migrations apply blog-admin --remote`.
   **Bucket dos rascunhos (issue #62):** `npx wrangler@4 r2 bucket create blog-rascunhos`, sem domínio público nem acesso `r2.dev`.
3. **Publicação:** `npx wrangler@4 deploy`.
4. **Segredos:** `RESEND_API_KEY`, `ALLOWED_EMAILS`, `DRAFT_NOTIFY_FROM` (remetente num domínio verificado no Resend) e `DRAFT_NOTIFY_TO` (o Roger), de uma vez (`npx wrangler@4 secret bulk`, lendo JSON pela entrada padrão).
5. **Primeira cópia:** rodar o cron uma vez pelo painel da Cloudflare, ou esperar o minuto 17, e conferir `last_sync_count` no D1.
6. **Conferência:**
   - sem login, `/admin` vai para o Access;
   - com JWT inválido direto no Worker, responde 403;
   - o resto do site continua idêntico (comparação byte a byte);
   - com login do Gui, o painel mostra os contadores do Resend.

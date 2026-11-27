"""Leva as conversões do site (tabela `conversions` do D1 `blog-admin`) para o Twenty (crm.guifav.dev). Issue #97, fase 2.

Fonte: as linhas de `conversions` com `synced_at` nulo, gravadas pelo Worker `blog-newsletter` (fase 1, PRs #98 e #100).
Simulação por padrão: lê o D1 e o Twenty e mostra só contagens. Grava apenas com --apply.

No Twenty, criados se faltarem (metadata API):
- Objeto `siteConversion` ("Conversão do site"): acao (SELECT), comprovado (BOOLEAN), material e arquivo (TEXT), interesses
  (MULTI_SELECT), ocorreuEm (DATE_TIME), eventoId (TEXT único, o `id` do D1), pedidoId e gaClientId (TEXT), a origem da
  visita (issue #114: origemCanal e origemFonte, SELECT; utmSource, utmMedium, utmCampaign e paginaEntrada, TEXT) e a
  relação N:1 `pessoa` com Person.
- Na Person: siteInteresses (MULTI_SELECT, união dos interesses das conversões), siteUltimaConversao (DATE_TIME),
  siteEmailComprovado (BOOLEAN: alguma conversão com o email comprovado por link) e siteGaClientId (TEXT, o client_id do
  GA4 da conversão mais recente que trouxe um).

Regras:
- Correspondência por email, no principal e nos adicionais, sem diferença de caixa (a mesma do twenty_sync.py). Mais de
  uma Pessoa com o email: a conversão vai para a mais antiga (createdAt, depois id), e a contagem mostra o caso.
- Datas com milissegundos do começo ao fim (o Worker grava `toISOString()`): ordenar, comparar e gravar no segundo cheio
  trocaria a Pessoa mais antiga e o client_id mais recente quando dois eventos caem no mesmo segundo.
- Pessoa nova só nasce de uma ação com o email comprovado (download ou confirmação da newsletter), só com o email.
- Pedido sem clique (`material_request`, `verified = 0`) só vira Conversão se o email já estiver numa Pessoa (decisão do
  Gui na #97, 2026-11-23). Sem Pessoa, a linha fica pendente no D1: se o email chegar ao CRM depois (pelo download, pela
  newsletter ou à mão), o pedido vai junto na rodada seguinte.
- Idempotente: a Conversão é identificada pelo eventoId (campo único no Twenty); a que já existe não é criada de novo, e
  a linha é marcada com `synced_at` depois que a Pessoa e as Conversões do email foram gravadas.
- siteInteresses só ganha valores, nunca perde: uma tag tirada à mão volta se a pessoa tiver uma conversão nova com o tema.
  Um interesse novo no catálogo vira opção nova das duas multi-seleções, com o rótulo do catálogo.
- Linha malformada (ação, email, datas, interesses ou ids fora do formato que o Worker grava; o email com a mesma regra
  do `normalizeEmail` do Worker) não é gravada nem marcada, e é contada em `linha_invalida` (o wrapper alerta).
- Lê e valida tudo (D1, metadados, Pessoas e Conversões) antes de qualquer escrita. A saída tem só contagens, nunca emails.
  Um campo SELECT que já existe no Twenty sem todas as opções que o sync grava é recusado antes de qualquer escrita.
- Origem (issue #114): vai na Conversão, nunca na Pessoa, com as listas e os padrões do Worker
  (workers/newsletter/src/origem.ts) e do CHECK da migração 0006. Linha anterior à #114 tem a origem toda nula e vira
  Conversão com os campos de origem vazios. Origem fora do formato (canal ou fonte fora da lista, UTM ou página de
  entrada que não casam, fonte sem canal) é linha inválida. O --forget apaga a Conversão inteira, e a origem sai junto.
- Id da Pessoa no D1 (issue #193, migração 0008): ao marcar `synced_at`, o sync grava em `twenty_person_id` o id da
  Pessoa no Twenty a que cada Conversão está ligada (para o link do painel /admin/contatos): para Conversões criadas na
  rodada, o id da Pessoa criada ou encontrada pelo email; para linhas pendentes cuja Conversão já existia no Twenty
  (por exemplo após queda antes da marcação), o `pessoaId` da própria Conversão existente, mesmo que o email hoje
  resolva para outra Pessoa. Se o id (da Pessoa ou da Conversão existente) estiver ausente ou fora do padrão do CHECK
  (letras ASCII, números e hífen, de 1 a 64), a linha é marcada com `twenty_person_id` nulo e conta em
  `pessoa_id_invalido`. Linhas já sincronizadas antes da migração
  (`synced_at IS NOT NULL AND twenty_person_id IS NULL`) são preenchidas a partir da relação `pessoa` (`pessoaId`) das
  Conversões já lidas do Twenty (inclusive as da lixeira), sem escrever nada no Twenty e mesmo quando não há linhas
  pendentes; contam em `ids_a_preencher` e só são gravadas com --apply. Ficam com `twenty_person_id` nulo as linhas
  ainda pendentes (como o pedido sem Pessoa e a pessoa na lixeira) e as já sincronizadas cuja Conversão não está no
  Twenty ou não tem um `pessoaId` válido (contadas em `sem_conversao_no_crm`, sem erro). Enquanto houver linha
  sincronizada sem id (inclusive as que ficam nulas de vez, como a de uma Conversão apagada de vez no CRM), cada rodada
  lê os metadados e as Conversões do Twenty (sem listar Pessoas quando não há pendentes válidas), e uma falha do Twenty
  nessa leitura faz o sync falhar como em qualquer outra leitura. O sync consulta `twenty_person_id` logo no início e
  falha sem gravar nada enquanto a migração 0008 não estiver aplicada. O --forget apaga a linha inteira de
  `conversions`, e a coluna sai junto.
- Lixeira do Twenty: a listagem padrão da REST esconde registros apagados pela interface, mas os campos únicos (eventoId
  e o email principal da Pessoa) continuam valendo para eles. Por isso Pessoas e Conversões são lidas com a lixeira.
  Conversão na lixeira conta como já existente (não é recriada; a linha é marcada). Email cuja única Pessoa está na
  lixeira não ganha Pessoa nova nem Conversão: a linha fica pendente e conta em `pessoa_na_lixeira` (o wrapper alerta),
  porque restaurar ou apagar de vez essa Pessoa é decisão humana.

Remoção (direito do titular), --forget (issue #134 e épico #197, corte 2): lê UM email da entrada padrão (fora do
histórico do shell e da lista de processos). Exige FORGET_HMAC_KEY (32 caracteres ou mais, igual à do Worker) antes de
qualquer leitura ou escrita; o sync normal não precisa dela. Logo depois de validar a chave e o email, antes de qualquer
leitura do D1 ou do Twenty, --forget --apply grava:
- o marcador mínimo na tabela `forgotten` do D1 (upsert, fica a data mais recente): `email_hmac` (HMAC-SHA256 em hex
  minúsculo de `forget-v1\\n` + email normalizado, nunca o email em claro) e `forgotten_at` (pelo relógio do próprio D1,
  em ISO UTC com milissegundos). Com esse marcador, o Worker recusa links assinados emitidos antes (ou no instante) da
  remoção;
- a oposição permanente ao heating na tabela `heating_optout` do D1 (decisão do Gui, 2026-11-27; mesmo upsert do Worker,
  com `email_hmac` = HMAC-SHA256 em hex minúsculo de `heating-optout-v1\\n` + email normalizado com `FORGET_HMAC_KEY` e
  `opted_out_at` pelo relógio do próprio D1), antes de apagar o resto. Assim, quem pede para apagar tudo não volta a ser
  pontuado pelo job do heating nem volta a ser ligado pelo login (`/identidade` do PR #203 recusa quem tem a marca).
As linhas do email no D1 continuam sendo o diário da remoção:
apaga no Twenty (de vez, inclusive as que estão na lixeira) as Conversões cujos eventoId são os ids dessas linhas, relê
o Twenty com a lixeira para provar que nenhuma sobrou, limpa os quatro campos `heating*` (heatingNota, heatingFaixa,
heatingSinais e heatingCalculadoEm) da Pessoa ativa — tanto as achadas pelo email quanto as referenciadas pelos
`twenty_person_id` salvos nas linhas de `conversions` e de `heating` (contexto `site`) do email antes de apagá-las
(a nota não fica no resumo que sobra na Pessoa mesmo se o email mudou no CRM; épico #197) — e só então apaga as linhas
no D1 em `conversions` (levando junto `twenty_person_id` e a origem), `engagement_events` e `heating` no contexto `site`
(não toca nos contextos de curso `curso:<slug>`). Em seguida apaga as linhas do email em `site_identities` (migração
0013): o trigger `AFTER DELETE ON site_identities` da migração 0014 enfileira automaticamente cada `uid` removido na
fila única `ga4_purge_queue` (`ga4_pendente = 1`, `bq_tentativas = 0`, `sessoes = '[]'`). Quando há credencial GCP
(`HEATING_GCP_SA_FILE`), processa imediatamente os `uid`s enfileirados: envia a remoção no GA4 (Admin API
`properties.submitUserDeletion`), apaga no BigQuery tabela por tabela (`events_YYYYMMDD`, `events_intraday_YYYYMMDD`,
`users_YYYYMMDD` existentes e linhas exclusivas de `pseudonymous_users_YYYYMMDD`, nunca no wildcard e nunca tocando em
linhas com `user_id` de outra pessoa) e persiste os pares `(user_pseudo_id, ga_session_id)` descobertos em `sessoes`
para as repetições D+1, D+3 e eventuais adiamentos por streaming buffer no job diário (da 5ª tentativa em diante,
diárias enquanto houver tabela adiada, contando `bq_remocao_travada`). Sem `HEATING_GCP_SA_FILE` (por exemplo no Mac),
apenas apaga `site_identities` (o trigger enfileira para o job diário na KVM), informa `ga4_fila_pendente_total` e sai
com código 2. Com credencial, se o GA4 responder 403/erro ou se o BigQuery falhar, o item
permanece pendente em `ga4_purge_queue` para o job diário retomar, com mensagem fixa (sem `uid`) e saída com código 2,
sem impedir as demais remoções. Uma queda no meio (mesmo na leitura do Twenty ou na limpeza dos campos heating da
Pessoa) deixa os marcadores (`forgotten` e `heating_optout`) gravados e o D1 intacto, e repetir o comando conclui, mesmo
que o email da Pessoa tenha mudado no CRM. A Pessoa e os campos site* dela ficam como estão (decisão do Gui, 2026-11-23).
Simulação sem --apply.

Papéis mínimos da conta de serviço (`HEATING_GCP_SA_FILE`, projeto `mindapps`):
- BigQuery Job User (`roles/bigquery.jobUser`) no projeto `mindapps`;
- WRITER no dataset `analytics_341040810` (para descobrir tabelas e executar DELETE por tabela);
- Editor na propriedade GA4 `341040810` (para `properties.submitUserDeletion`).

Limpeza dos marcadores (issue #134): os marcadores em `forgotten` ficam por 8 dias (o link mais longo vale 7 dias, mais
1 dia de folga de relógio). No sync e no --forget com --apply, os marcadores com `forgotten_at` mais velho que 8 dias
(pelo relógio do próprio D1) são apagados logo depois da contagem, antes de ler o Twenty; sem --apply, só conta em
`marcadores_vencidos`.

Sync e --forget nunca rodam juntos: os dois pegam o mesmo lock (arquivo em CONVERSOES_SYNC_LOCK, padrão
/tmp/guifav-conversoes-sync.lock). Sem ele, o sync poderia criar no Twenty uma Conversão de uma linha pendente entre a
limpeza do Twenty e a do D1, e essa Conversão ficaria sem linha no D1 que a ligasse ao email.

Rodar na KVM (todas as credenciais vêm do ambiente):
    CLOUDFLARE_API_TOKEN=... CLOUDFLARE_ACCOUNT_ID=... D1_DATABASE_ID=... TWENTY_API_KEY=... \\
    TWENTY_LOCAL_URL=http://127.0.0.1:3100 python3 scripts/conversoes_sync.py [--apply]
    printf '%s\\n' "$EMAIL" | FORGET_HMAC_KEY=... [HEATING_GCP_SA_FILE=...] ... python3 scripts/conversoes_sync.py --forget [--apply]
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import hmac
import inspect
import json
import os
import re
import sys
import time
import urllib.parse
from collections import Counter, OrderedDict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import twenty_sync as TS  # noqa: E402
from twenty_sync import SyncError  # noqa: E402

OBJ = {"nameSingular": "siteConversion", "namePlural": "siteConversions", "labelSingular": "Conversão do site",
       "labelPlural": "Conversões do site", "icon": "IconTargetArrow", "isLabelSyncedWithName": False,
       "description": "Ações do site guifav.dev com email (pedido e download de material, confirmação da newsletter). "
                      "Gravadas pelo sync da issue #97 a partir do D1 blog-admin; não edite à mão."}
PLURAL = OBJ["namePlural"]
ACOES = {
    "material_request": ("MATERIAL_REQUEST", "Pedido de material"),
    "material_download": ("MATERIAL_DOWNLOAD", "Download de material"),
    "newsletter_confirm": ("NEWSLETTER_CONFIRM", "Inscrição confirmada"),
}
# Origem da visita (issue #114): valor no D1 -> (opção no Twenty, rótulo). As mesmas listas do Worker e da migração 0006.
CANAIS = OrderedDict([
    ("busca", ("BUSCA", "Busca")), ("assistente", ("ASSISTENTE", "Assistente de IA")), ("social", ("SOCIAL", "Rede social")),
    ("email", ("EMAIL", "Email")), ("pago", ("PAGO", "Pago")), ("outro_site", ("OUTRO_SITE", "Outro site")),
    ("direto", ("DIRETO", "Direto")), ("desconhecida", ("DESCONHECIDA", "Desconhecida")),
])
FONTES = OrderedDict([
    ("google", ("GOOGLE", "Google")), ("bing", ("BING", "Bing")), ("chatgpt", ("CHATGPT", "ChatGPT")),
    ("perplexity", ("PERPLEXITY", "Perplexity")), ("claude", ("CLAUDE", "Claude")), ("copilot", ("COPILOT", "Copilot")),
    ("gemini", ("GEMINI", "Gemini")), ("linkedin", ("LINKEDIN", "LinkedIn")), ("youtube", ("YOUTUBE", "YouTube")),
    ("github", ("GITHUB", "GitHub")), ("outro", ("OUTRO", "Outro")),
])
SEM_FONTE = {"direto", "desconhecida"}
COLORS = ("blue", "purple", "green", "orange", "turquoise", "pink", "yellow", "red", "sky", "gray")
CONV_FIELDS = OrderedDict([
    ("acao", {"type": "SELECT", "label": "Ação", "icon": "IconClick", "options": [
        {"value": v, "label": lbl, "color": COLORS[i], "position": i} for i, (v, lbl) in enumerate(ACOES.values())]}),
    ("comprovado", {"type": "BOOLEAN", "label": "Email comprovado", "icon": "IconMailCheck"}),
    ("material", {"type": "TEXT", "label": "Material", "icon": "IconFileDownload"}),
    ("arquivo", {"type": "TEXT", "label": "Arquivo baixado", "icon": "IconFile"}),
    ("interesses", {"type": "MULTI_SELECT", "label": "Interesses", "icon": "IconTags"}),
    ("ocorreuEm", {"type": "DATE_TIME", "label": "Ocorreu em", "icon": "IconCalendarEvent"}),
    ("eventoId", {"type": "TEXT", "label": "Id do evento (D1)", "icon": "IconHash", "isUnique": True}),
    ("pedidoId", {"type": "TEXT", "label": "Id do pedido (D1)", "icon": "IconLink"}),
    ("gaClientId", {"type": "TEXT", "label": "client_id do GA4", "icon": "IconChartLine"}),
    ("origemCanal", {"type": "SELECT", "label": "Origem: canal", "icon": "IconRoute", "options": [
        {"value": v, "label": lbl, "color": COLORS[i % 10], "position": i} for i, (v, lbl) in enumerate(CANAIS.values())]}),
    ("origemFonte", {"type": "SELECT", "label": "Origem: fonte", "icon": "IconWorld", "options": [
        {"value": v, "label": lbl, "color": COLORS[i % 10], "position": i} for i, (v, lbl) in enumerate(FONTES.values())]}),
    ("utmSource", {"type": "TEXT", "label": "utm_source", "icon": "IconTag"}),
    ("utmMedium", {"type": "TEXT", "label": "utm_medium", "icon": "IconTag"}),
    ("utmCampaign", {"type": "TEXT", "label": "utm_campaign", "icon": "IconTag"}),
    ("paginaEntrada", {"type": "TEXT", "label": "Página de entrada", "icon": "IconDoorEnter"}),
])
ORIGEM_COLS = ("origin_channel", "origin_source", "utm_source", "utm_medium", "utm_campaign", "landing_path")
RELATION = {"name": "pessoa", "type": "RELATION", "label": "Pessoa", "icon": "IconUser",
            "inverse_label": "Conversões do site", "inverse_icon": "IconTargetArrow"}
PERSON_FIELDS = OrderedDict([
    ("siteInteresses", {"type": "MULTI_SELECT", "label": "Interesses (site)", "icon": "IconTags"}),
    ("siteUltimaConversao", {"type": "DATE_TIME", "label": "Última conversão no site", "icon": "IconCalendarEvent"}),
    ("siteEmailComprovado", {"type": "BOOLEAN", "label": "Email comprovado no site", "icon": "IconMailCheck"}),
    ("siteGaClientId", {"type": "TEXT", "label": "client_id do GA4 (site)", "icon": "IconChartLine"}),
])
HEATING_PERSON_FIELDS = ("heatingNota", "heatingFaixa", "heatingSinais", "heatingCalculadoEm")
MULTI = {"interesses", "siteInteresses"}

HEX64 = re.compile(r"[0-9a-f]{64}")
# A mesma regra do normalizeEmail de workers/newsletter/src/index.ts (minúsculas, local até 64, rótulos de domínio sem
# hífen nas pontas, total até 254). Classes só ASCII: o \d e o \w do Python aceitam dígitos e letras Unicode.
_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
EMAIL = re.compile(r"[a-z0-9.!#$%&*+/=?^_`{|}~-]{1,64}@" + _LABEL + r"(?:\." + _LABEL + r")+")
MAX_EMAIL = 254
SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
INTEREST = re.compile(r"[A-Z][A-Z0-9_]{0,62}")
GA_CID = re.compile(r"[0-9]{1,20}\.[0-9]{1,20}")  # o parseGaClientId de workers/newsletter/src/ga4.ts
UTM = re.compile(r"[a-z0-9._-]{1,64}")  # o UTM de workers/newsletter/src/origem.ts
ENTRADA = re.compile(r"/(?:[A-Za-z0-9._-]+/)*[A-Za-z0-9._-]*")  # o ENTRADA de origem.ts: segmentos não vazios
MAX_ENTRADA = 200
TWENTY_PERSON_ID = re.compile(r"[0-9A-Za-z-]{1,64}")  # o CHECK de twenty_person_id na migração 0008 (issue #193)
MAX_LABEL = 120
MARK_CHUNK = 50  # ids por UPDATE no D1 (até 52 parâmetros com synced_at e twenty_person_id; limite de 100 do D1)
FORGET_MARK = "conversões apagadas"
# Marcador mínimo de remoção (issue #134) e oposição ao heating (épico #197): mesmos prefixos e piso de chave do Worker
# (workers/newsletter/src/forget.ts).
FORGET_PREFIX = "forget-v1\n"
HEATING_OPTOUT_PREFIX = "heating-optout-v1\n"
MIN_FORGET_KEY = 32
# Retenção dos marcadores em dias (issue #134): precisa cobrir o maior TTL dos links assinados (`TTL_MS` e
# `MATERIAL_TTL_MS` em `workers/newsletter/src/token.ts`) mais 1 dia de folga. Ao alterar um TTL, atualize junto este
# valor, `privacidade/index.html` e `workers/newsletter/README.md`.
FORGET_TTL_DAYS = 8
_D1_NOW_SQL = "strftime('%Y-%m-%dT%H:%M:%fZ','now')"
_D1_CUTOFF_SQL = f"strftime('%Y-%m-%dT%H:%M:%fZ','now','-{FORGET_TTL_DAYS} days')"
# Filtro da REST do Twenty que devolve ativos e lixeira juntos (conferido ao vivo na 2.32 com uma sonda descartável).
WITH_TRASH = "or(deletedAt[is]:NULL,deletedAt[is]:NOT_NULL)"
LOCK_DEFAULT = "/tmp/guifav-conversoes-sync.lock"

# GA4 e BigQuery (épico #197, corte 2).
GCP_PROJECT = "mindapps"
GA4_PROPERTY_ID = "341040810"
GA4_STREAM_ID = "13120304692"
BQ_DATASET = f"analytics_{GA4_PROPERTY_ID}"
BQ_LOCATION = "southamerica-east1"
GA4_DELETE_URL = f"https://analyticsadmin.googleapis.com/v1alpha/properties/{GA4_PROPERTY_ID}:submitUserDeletion"
GA4_EDIT_SCOPE = "https://www.googleapis.com/auth/analytics.edit"
GA4_PURGE_PENDENTE_MSG = (
    "remoção no GA4 pendente na fila para o job diário: confira o papel de Editor da conta de serviço na propriedade GA4"
)
BQ_PURGE_PENDENTE_MSG = "remoção no BigQuery pendente na fila para o job diário"
GA4_FILA_SEM_CRED_MSG = "remoção no GA4 e no BigQuery enfileirada para o job diário (sem credencial GCP neste ambiente)"
SITE_UID_RE = re.compile(r"[0-9A-Za-z]{1,128}")  # CHECK da migração 0013 (site_identities) e 0014 (ga4_purge_queue)
BQ_TABLE_SUFFIX_RE = re.compile(r"(?:intraday_)?\d{8}")
BQ_DAY_SUFFIX_RE = re.compile(r"\d{8}")
BQ_USERS_TABLE_RE = re.compile(r"users_(\d{8})")
BQ_PSEUDO_TABLE_RE = re.compile(r"pseudonymous_users_(\d{8})")
# Teto do JSON de sessões guardado na fila (CHECK da 0014): cerca de 25 mil sessões por pessoa. Nunca se corta a lista:
# acima disso a remoção no BigQuery não começa (o item fica na fila e conta em `bq_sessoes_excedidas`, o wrapper alerta).
GA4_PURGE_SESSOES_MAX = 1_000_000
GA4_PURGE_MAX_TENTATIVAS = 5
# Teto de bytes por consulta do BigQuery (o export do site tem poucos MB por dia; o teto só barra consulta fora do normal).
BQ_MAX_BYTES = 10 * 1024 ** 3
GA4_FILA_PENDENTE_MSG = (
    "há remoções no GA4 pendentes na fila (o job diário repete); confira o papel de Editor da conta de serviço"
)


class GoogleIndisponivel(Exception):
    """Falha de autenticação, rede, permissão ou ausência de biblioteca/credencial no Google."""


class SessoesExcedidas(GoogleIndisponivel):
    """As sessões descobertas não cabem no teto da fila: a remoção no BigQuery não começa, para nada se perder."""


def _full(rx: re.Pattern, v) -> bool:
    return isinstance(v, str) and rx.fullmatch(v) is not None


def _filled(v) -> bool:
    """Trata None e '' como vazios, preservando 0 (ex.: nota 0 de email_spam) e False."""
    return v is not None and v != ""


def valid_email(v) -> bool:
    return _full(EMAIL, v) and len(v) <= MAX_EMAIL


def forget_hmac(email: str, key: str) -> str:
    """HMAC-SHA256 em hex minúsculo de `forget-v1\\n` + email normalizado (issue #134), igual ao Worker."""
    if not isinstance(key, str) or len(key) < MIN_FORGET_KEY:
        raise SyncError("FORGET_HMAC_KEY ausente ou curta demais")
    norm = email.strip().lower() if isinstance(email, str) else ""
    if not valid_email(norm):
        raise SyncError("email inválido na entrada")
    return hmac.new(key.encode("utf-8"), (FORGET_PREFIX + norm).encode("utf-8"), hashlib.sha256).hexdigest()


def heating_optout_hmac(email: str, key: str) -> str:
    """HMAC-SHA256 em hex minúsculo de `heating-optout-v1\\n` + email normalizado (épico #197), igual ao Worker."""
    if not isinstance(key, str) or len(key) < MIN_FORGET_KEY:
        raise SyncError("FORGET_HMAC_KEY ausente ou curta demais")
    norm = email.strip().lower() if isinstance(email, str) else ""
    if not valid_email(norm):
        raise SyncError("email inválido na entrada")
    return hmac.new(key.encode("utf-8"), (HEATING_OPTOUT_PREFIX + norm).encode("utf-8"), hashlib.sha256).hexdigest()


def _upsert_forget_mark(c: TS.Clients, email_hmac: str) -> None:
    # Relógio do próprio D1 (issue #134): evita diferença entre a KVM e o Cloudflare; o upsert mantém a data mais recente.
    c.d1(
        f"INSERT INTO forgotten (email_hmac, forgotten_at) VALUES (?, {_D1_NOW_SQL}) "
        f"ON CONFLICT (email_hmac) DO UPDATE SET forgotten_at = max(forgotten.forgotten_at, {_D1_NOW_SQL})",
        [email_hmac],
    )


def _upsert_heating_optout(c: TS.Clients, email_hmac: str) -> None:
    # Mesmo upsert do Worker (com relógio do D1): grava a oposição permanente ao heating (épico #197).
    c.d1(
        f"INSERT INTO heating_optout (email_hmac, opted_out_at) VALUES (?, {_D1_NOW_SQL}) "
        f"ON CONFLICT(email_hmac) DO UPDATE SET opted_out_at = excluded.opted_out_at",
        [email_hmac],
    )


def _count_expired_marks(c: TS.Clients, exclude_hmac: str | None = None) -> int:
    if exclude_hmac is None:
        return int(c.d1(f"SELECT count(*) AS n FROM forgotten WHERE forgotten_at < {_D1_CUTOFF_SQL}")[0]["n"])
    return int(
        c.d1(
            f"SELECT count(*) AS n FROM forgotten WHERE forgotten_at < {_D1_CUTOFF_SQL} AND email_hmac != ?",
            [exclude_hmac],
        )[0]["n"]
    )


def _prune_expired_marks(c: TS.Clients) -> None:
    c.d1(f"DELETE FROM forgotten WHERE forgotten_at < {_D1_CUTOFF_SQL}")


def iso_ms(value: str) -> str:
    """Data em ISO UTC com milissegundos ('2026-11-24T10:00:00.900Z'); '' se inválida. Mesmas entradas do TS.iso."""
    v = (value or "").strip().replace(" ", "T", 1)
    if not v:
        return ""
    v = re.sub(r"([+-]\d\d)$", r"\1:00", v.replace("Z", "+00:00"))
    try:
        d = datetime.fromisoformat(v)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        d = d.astimezone(timezone.utc)  # pode estourar o ano 1 ou 9999 ao trocar de fuso (OverflowError)
    except (ValueError, OverflowError):
        return ""
    return stamp(d)


def stamp(d: datetime) -> str:
    # Campos explícitos: o %Y do strftime no Linux não completa anos abaixo de 1000 com zeros ("999-..."), o que quebra
    # o formato ISO e a comparação como texto.
    return (f"{d.year:04d}-{d.month:02d}-{d.day:02d}T{d.hour:02d}:{d.minute:02d}:{d.second:02d}."
            f"{d.microsecond // 1000:03d}Z")


def _now() -> datetime:  # relógio do synced_at (substituível nos testes)
    return datetime.now(timezone.utc)


def parse_origem(r: dict) -> dict | None:
    """Origem da linha (issue #114) validada: dict com os seis campos (todos None numa linha anterior à #114) ou None se
    algo estiver fora do formato que o Worker grava e o CHECK do D1 aceita."""
    o = {k: r.get(k) for k in ORIGEM_COLS}
    if o["origin_channel"] is None:
        return o if all(v is None for v in o.values()) else None
    if o["origin_channel"] not in CANAIS:
        return None
    src = o["origin_source"]
    if src is not None and (src not in FONTES or o["origin_channel"] in SEM_FONTE):
        return None
    for k in ("utm_source", "utm_medium", "utm_campaign"):
        if o[k] is not None and not _full(UTM, o[k]):
            return None
    path = o["landing_path"]
    if path is not None and (not _full(ENTRADA, path) or len(path) > MAX_ENTRADA):
        return None
    return o


def parse_row(r: dict) -> dict | None:
    """Linha do D1 validada e normalizada, ou None se algo estiver fora do formato que o Worker grava."""
    try:
        action, verified = r.get("action"), r.get("verified")
        if action not in ACOES or verified not in (0, 1) or isinstance(verified, bool):
            return None
        if (action == "material_request") != (verified == 0):
            return None  # só o pedido é não comprovado
        if not _full(HEX64, r.get("id")) or not valid_email(r.get("email")):
            return None
        material = r.get("material")
        if action == "newsletter_confirm":
            if material is not None:
                return None
        elif not _full(SLUG, material):
            return None
        for k, rx in (("file", SLUG), ("request_id", HEX64), ("ga_client_id", GA_CID)):
            if r.get(k) is not None and not _full(rx, r.get(k)):
                return None
        if r.get("file") is not None and action != "material_download":
            return None
        if r.get("request_id") is not None and action != "material_download":
            return None
        when = iso_ms(r.get("occurred_at") or "")
        if not when:
            return None
        origem = parse_origem(r)
        if origem is None:
            return None
        raw = json.loads(r.get("interests") or "")
        if not isinstance(raw, list):
            return None
        interests: OrderedDict[str, str] = OrderedDict()
        for it in raw:
            if not isinstance(it, dict) or not _full(INTEREST, it.get("id")):
                return None
            label = it.get("rotulo")
            if not isinstance(label, str) or not label.strip() or len(label) > MAX_LABEL:
                return None
            interests.setdefault(it["id"], label.strip())
        return {"id": r["id"], "email": r["email"], "action": action, "verified": verified == 1, "material": material,
                "file": r.get("file"), "request_id": r.get("request_id"), "ga": r.get("ga_client_id"), "when": when,
                "interests": interests, "origem": origem}
    except (ValueError, TypeError, AttributeError, OverflowError):
        return None


def read_metadata(c: TS.Clients) -> dict:
    q = ("{ objects(paging:{first:200}){ edges{ node{ id nameSingular namePlural "
         "fields(paging:{first:500}){ edges{ node{ id name type options } } } } } } }")
    data = c.twenty("POST", "/metadata", {"query": q}, op="ler metadados")
    try:
        nodes = {e["node"]["nameSingular"]: e["node"] for e in data["data"]["objects"]["edges"]}
        person = nodes["person"]
        conv = nodes.get(OBJ["nameSingular"])

        def fields(o):
            return {f["node"]["name"]: f["node"] for f in o["fields"]["edges"]} if o else {}
        meta = {"person_id": person["id"], "person": fields(person), "conv_id": conv["id"] if conv else None, "conv": fields(conv)}
    except (KeyError, TypeError) as e:
        raise SyncError("metadados do Twenty em formato inesperado") from e
    if conv and conv.get("namePlural") != PLURAL:
        raise SyncError("objeto siteConversion com plural inesperado")
    # Campo com o nome certo e tipo errado: recusa em vez de gravar no lugar errado.
    for spec, have in ((PERSON_FIELDS, meta["person"]), (CONV_FIELDS, meta["conv"])):
        for k, f in spec.items():
            if k in have and have[k]["type"] != f["type"]:
                raise SyncError(f"campo {k} existe com outro tipo")
            # SELECT que já existe precisa ter todas as opções que o sync grava: senão o Twenty recusaria a Conversão no
            # meio da rodada, depois de outras escritas.
            if k in have and f["type"] == "SELECT":
                missing = {o["value"] for o in f["options"]} - set(_option_values(have[k]))
                if missing:
                    raise SyncError(f"campo {k} existe sem as opções {', '.join(sorted(missing))}")
    if RELATION["name"] in meta["conv"] and meta["conv"][RELATION["name"]]["type"] != "RELATION":
        raise SyncError("campo pessoa existe com outro tipo")
    return meta


def list_records(c: TS.Clients, plural: str) -> list[dict]:
    """Listagem REST completa, com a lixeira (mesmo contrato de paginação do twenty_sync.list_people)."""
    out, cursor = [], ""
    for _ in range(TS.MAX_PAGES):
        q = (f"?limit={TS.PAGE}&filter={urllib.parse.quote(WITH_TRASH)}"
             + (f"&starting_after={urllib.parse.quote(cursor)}" if cursor else ""))
        data = c.twenty("GET", f"/rest/{plural}{q}", op=f"listar {plural}")
        page = (data.get("data") or {}).get(plural)
        info = data.get("pageInfo")
        if not isinstance(page, list) or not isinstance(info, dict) or not isinstance(info.get("hasNextPage"), bool):
            raise SyncError(f"listagem de {plural} inválida")
        out += page
        if not info["hasNextPage"]:
            return out
        nxt = info.get("endCursor") or ""
        if not nxt or nxt == cursor:
            raise SyncError(f"paginação de {plural} sem progresso")
        cursor = nxt
    raise SyncError(f"paginação sem fim em {plural}")


def people_by_email(people: list[dict]) -> dict[str, list[dict]]:
    """Pessoas ativas por email, da mais antiga para a mais nova. As da lixeira ficam de fora (veja trashed_emails)."""
    idx: dict[str, list[dict]] = {}
    for p in sorted(people, key=lambda p: (iso_ms(p.get("createdAt") or "") or "9999", p["id"])):
        if p.get("deletedAt"):
            continue
        for e in TS._emails(p):
            idx.setdefault(e, []).append(p)
    return idx


def trashed_emails(people: list[dict]) -> set[str]:
    return {e for p in people if p.get("deletedAt") for e in TS._emails(p)}


def _option_values(field: dict | None) -> list[str]:
    return [o["value"] for o in (field or {}).get("options") or []]


def person_patch(p: dict | None, rows: list[dict]) -> dict:
    """Campos site* desejados para a Pessoa (p None: Pessoa nova), só os que mudam."""
    cur_int = list((p or {}).get("siteInteresses") or [])
    merged = cur_int + [i for r in rows for i in r["interests"] if i not in cur_int]
    merged = list(OrderedDict.fromkeys(merged))
    cur_last = iso_ms((p or {}).get("siteUltimaConversao") or "")
    last = max([cur_last] + [r["when"] for r in rows])
    proven = bool((p or {}).get("siteEmailComprovado")) or any(r["verified"] for r in rows)
    want = {"siteInteresses": merged, "siteUltimaConversao": last, "siteEmailComprovado": proven}
    with_ga = [r for r in rows if r["ga"]]
    cur_ga = (p or {}).get("siteGaClientId") or ""
    if with_ga:
        newest = max(with_ga, key=lambda r: (r["when"], r["id"]))
        # Só troca um client_id já gravado por outro de uma conversão tão ou mais recente que a última conhecida.
        if not cur_ga or newest["when"] >= cur_last:
            want["siteGaClientId"] = newest["ga"]
    if p is None:
        return want
    out = {}
    for k, v in want.items():
        have = p.get(k)
        if k == "siteUltimaConversao":
            have = iso_ms(have or "") or None
        if k == "siteEmailComprovado":
            have = bool(have)
        if have != v:
            out[k] = v
    return out


def conversion_body(r: dict, person_id: str) -> dict:
    code, label = ACOES[r["action"]]
    o = r["origem"]
    return {"name": f"{label}: {r['material']}" if r["material"] else label, "acao": code, "comprovado": r["verified"],
            "material": r["material"], "arquivo": r["file"], "interesses": list(r["interests"]), "ocorreuEm": r["when"],
            "eventoId": r["id"], "pedidoId": r["request_id"], "gaClientId": r["ga"],
            "origemCanal": CANAIS[o["origin_channel"]][0] if o["origin_channel"] else None,
            "origemFonte": FONTES[o["origin_source"]][0] if o["origin_source"] else None,
            "utmSource": o["utm_source"], "utmMedium": o["utm_medium"], "utmCampaign": o["utm_campaign"],
            "paginaEntrada": o["landing_path"], "pessoaId": person_id}


def ensure_schema(c: TS.Clients, meta: dict, labels: dict[str, str]) -> None:
    """Cria o que falta: objeto, campos, relação e opções novas de interesse. Só roda com --apply, depois das leituras."""
    fm = "mutation($input: CreateOneFieldMetadataInput!){ createOneField(input:$input){ id name type options } }"

    def create(object_id: str, name: str, spec: dict, opts: list | None = None) -> dict:
        f = {"objectMetadataId": object_id, "name": name, "isNullable": True,
             **{k: v for k, v in spec.items() if k != "options"}}
        if spec["type"] == "SELECT":
            f["options"] = spec["options"]
        if spec["type"] == "MULTI_SELECT":
            f["options"] = opts or []
        data = c.twenty("POST", "/metadata", {"query": fm, "variables": {"input": {"field": f}}}, op="criar campo")
        return data["data"]["createOneField"]

    if not meta["conv_id"]:
        m = "mutation($input: CreateOneObjectInput!){ createOneObject(input:$input){ id } }"
        data = c.twenty("POST", "/metadata", {"query": m, "variables": {"input": {"object": OBJ}}}, op="criar objeto")
        meta["conv_id"] = data["data"]["createOneObject"]["id"]
    new_opts = lambda field: [  # noqa: E731
        {"value": v, "label": labels[v], "color": COLORS[(len(_option_values(field)) + i) % len(COLORS)],
         "position": len(_option_values(field)) + i}
        for i, v in enumerate(sorted(v for v in labels if v not in _option_values(field)))]
    for obj_key, obj_id, spec in (("person", meta["person_id"], PERSON_FIELDS), ("conv", meta["conv_id"], CONV_FIELDS)):
        for name, f in spec.items():
            if name not in meta[obj_key]:
                meta[obj_key][name] = create(obj_id, name, f, new_opts(None) if name in MULTI else None)
    if RELATION["name"] not in meta["conv"]:
        f = {"objectMetadataId": meta["conv_id"], "name": RELATION["name"], "type": "RELATION", "label": RELATION["label"],
             "icon": RELATION["icon"], "isNullable": True,
             "relationCreationPayload": {"targetObjectMetadataId": meta["person_id"], "targetFieldLabel": RELATION["inverse_label"],
                                         "targetFieldIcon": RELATION["inverse_icon"], "type": "MANY_TO_ONE"}}
        data = c.twenty("POST", "/metadata", {"query": fm, "variables": {"input": {"field": f}}}, op="criar relação")
        meta["conv"][RELATION["name"]] = data["data"]["createOneField"]
    um = "mutation($input: UpdateOneFieldMetadataInput!){ updateOneField(input:$input){ id options } }"
    for obj_key, name in (("person", "siteInteresses"), ("conv", "interesses")):
        field = meta[obj_key][name]
        add = new_opts(field)
        if add:  # as opções antigas vão como estão (com id), para o Twenty não recriar nem apagar nenhuma
            options = list(field.get("options") or []) + add
            data = c.twenty("POST", "/metadata", {"query": um, "variables": {"input": {"id": field["id"], "update": {"options": options}}}},
                            op="criar opção")
            field["options"] = data["data"]["updateOneField"]["options"]


def run(c: TS.Clients, *, apply: bool = False, out=None) -> Counter:
    out = out or sys.stdout
    counts: Counter = Counter()
    # 1) Leituras e validações. Nenhuma escrita de conversões antes de a fonte e o CRM estarem lidos por inteiro.
    raw = c.d1("SELECT id, email, action, material, interests, occurred_at, verified, ga_client_id, request_id, file, "
               + ", ".join(ORIGEM_COLS) + " FROM conversions WHERE synced_at IS NULL ORDER BY occurred_at, id")
    to_fill = [r["id"] for r in c.d1("SELECT id FROM conversions WHERE synced_at IS NOT NULL AND twenty_person_id IS NULL ORDER BY id")]
    rows = []
    for r in raw:
        p = parse_row(r)
        if p is None:
            counts["linha_invalida"] += 1
        else:
            rows.append(p)
    counts["pendentes_lidas"] = len(raw)
    expired_marks = _count_expired_marks(c)
    counts["marcadores_vencidos"] = expired_marks
    # Limpeza dos marcadores vencidos (#134): não depende do Twenty, então roda logo após a contagem com --apply.
    if apply and expired_marks:
        _prune_expired_marks(c)
    for k in ("linha_invalida", "pedido_sem_pessoa", "pessoa_na_lixeira", "email_em_mais_de_uma_pessoa", "pessoas_a_criar",
              "pessoas_a_atualizar", "conversoes_a_criar", "conversoes_ja_no_crm", "conversoes_na_lixeira", "linhas_a_marcar",
              "ids_a_preencher", "sem_conversao_no_crm", "pessoa_id_invalido"):
        counts[k] += 0  # sempre visíveis na saída
    if not rows and not to_fill:
        if apply and expired_marks:
            tail = "nada pendente; marcadores vencidos apagados" if not raw else "só linhas inválidas; marcadores vencidos apagados"
        else:
            tail = "nada pendente" if not raw else "só linhas inválidas; nada foi gravado"
        _report(counts, out, tail)
        return counts
    meta = read_metadata(c)
    people = list_records(c, "people") if rows else []
    existing = list_records(c, PLURAL) if meta["conv_id"] else []
    known = {x.get("eventoId") for x in existing if x.get("eventoId")}
    trashed_conv = {x.get("eventoId") for x in existing if x.get("eventoId") and x.get("deletedAt")}
    conv_person = {x["eventoId"]: x.get("pessoaId") for x in existing if x.get("eventoId")}
    idx = people_by_email(people)
    trashed = trashed_emails(people)

    # 2) Plano. Agrupa por Pessoa (dois emails da mesma Pessoa viram uma gravação só, calculada sobre todas as linhas
    # dela) e, para quem ainda não está no CRM, por email.
    by_email: OrderedDict[str, list[dict]] = OrderedDict()
    for r in rows:
        by_email.setdefault(r["email"], []).append(r)
    groups: OrderedDict[tuple, dict] = OrderedDict()
    for email, rs in by_email.items():
        persons = idx.get(email) or []
        if not persons and email in trashed:
            counts["pessoa_na_lixeira"] += len(rs)  # fica pendente: restaurar ou apagar de vez é decisão humana
            continue
        if not persons and not any(r["verified"] for r in rs):
            counts["pedido_sem_pessoa"] += len(rs)  # fica pendente: só vira Conversão quando o email estiver no CRM
            continue
        if len(persons) > 1:
            counts["email_em_mais_de_uma_pessoa"] += 1
        key = ("p", persons[0]["id"]) if persons else ("e", email)
        g = groups.setdefault(key, {"email": email, "target": persons[0] if persons else None, "rows": []})
        g["rows"] += rs
    plan = []
    labels: dict[str, str] = {}
    for g in groups.values():
        rs = sorted(g["rows"], key=lambda r: (r["when"], r["id"]))
        target = g["target"]
        patch = person_patch(target, rs)
        new = [r for r in rs if r["id"] not in known]
        counts["pessoas_a_criar" if target is None else "pessoas_a_atualizar"] += 1 if (target is None or patch) else 0
        counts["conversoes_a_criar"] += len(new)
        counts["conversoes_ja_no_crm"] += len(rs) - len(new)
        counts["conversoes_na_lixeira"] += sum(r["id"] in trashed_conv for r in rs)
        counts["linhas_a_marcar"] += len(rs)
        for r in rs:
            if r["id"] in conv_person and not _full(TWENTY_PERSON_ID, conv_person[r["id"]]):
                counts["pessoa_id_invalido"] += 1
        if target is not None and new and not _full(TWENTY_PERSON_ID, target.get("id")):
            counts["pessoa_id_invalido"] += len(new)
        for r in rs:
            for i, lbl in r["interests"].items():
                labels[i] = lbl  # rótulo do catálogo mais recente (linhas em ordem de data)
        plan.append((g["email"], target, patch, new, rs))
    fill_plan: OrderedDict[str, list[str]] = OrderedDict()
    for row_id in to_fill:
        pid = conv_person.get(row_id)
        if _full(TWENTY_PERSON_ID, pid):
            fill_plan.setdefault(pid, []).append(row_id)
            counts["ids_a_preencher"] += 1
        else:
            counts["sem_conversao_no_crm"] += 1
    missing_obj = 0 if meta["conv_id"] else 1
    counts["objeto_a_criar"] = missing_obj
    counts["campos_a_criar"] = (sum(k not in meta["person"] for k in PERSON_FIELDS) + sum(k not in meta["conv"] for k in CONV_FIELDS)
                                + (RELATION["name"] not in meta["conv"]))
    counts["opcoes_a_criar"] = sum(len([v for v in labels if v not in _option_values(meta[o].get(n))])
                                   for o, n in (("person", "siteInteresses"), ("conv", "interesses")))

    # 3) Escritas no Twenty e marcação em `conversions`, só com --apply: esquema, depois cada email (Pessoa, Conversões,
    # marca no D1) e por fim o preenchimento de `twenty_person_id` nas linhas antigas (#193).
    if apply and plan:
        ensure_schema(c, meta, labels)
        now = stamp(_now())
        for email, target, patch, new, rs in plan:
            if target is None:
                data = c.twenty("POST", "/rest/people", {"emails": {"primaryEmail": email, "additionalEmails": []}, **patch},
                                op="criar pessoa")
                pid = data["data"]["createPerson"]["id"]
            else:
                pid = target["id"]
                if patch:
                    c.twenty("PATCH", f"/rest/people/{urllib.parse.quote(pid)}", patch, op="atualizar pessoa")
            for r in new:
                c.twenty("POST", f"/rest/{PLURAL}", conversion_body(r, pid), op="criar conversão")
            valid_pid = pid if _full(TWENTY_PERSON_ID, pid) else None
            if valid_pid is None and target is None and new:
                counts["pessoa_id_invalido"] += len(new)
            by_pid: OrderedDict[str | None, list[str]] = OrderedDict()
            for r in rs:
                if r["id"] in conv_person:
                    raw_pid = conv_person[r["id"]]
                    row_pid = raw_pid if _full(TWENTY_PERSON_ID, raw_pid) else None
                else:
                    row_pid = valid_pid
                by_pid.setdefault(row_pid, []).append(r["id"])
            for row_pid, ids in by_pid.items():
                for i in range(0, len(ids), MARK_CHUNK):
                    part = ids[i:i + MARK_CHUNK]
                    c.d1(f"UPDATE conversions SET synced_at = ?, twenty_person_id = ? WHERE synced_at IS NULL AND id IN ({','.join('?' * len(part))})",
                         [now, row_pid, *part])
            counts["linhas_marcadas"] += len(rs)
    if apply and fill_plan:
        for pid, ids in fill_plan.items():
            for i in range(0, len(ids), MARK_CHUNK):
                part = ids[i:i + MARK_CHUNK]
                c.d1(f"UPDATE conversions SET twenty_person_id = ? WHERE twenty_person_id IS NULL AND id IN ({','.join('?' * len(part))})",
                     [pid, *part])
    _report(counts, out, "gravado" if apply else "simulação: nada foi gravado (use --apply)")
    return counts


# ------------------------------------------------------------------ Remoção no Google (GA4 e BigQuery; épico #197, corte 2)

def _is_google_err(exc: Exception) -> bool:
    mod = type(exc).__module__ or ""
    return isinstance(exc, (OSError, ValueError)) or mod.startswith(("google.", "grpc", "requests.", "urllib3."))


def _is_not_found(exc: Exception) -> bool:
    return getattr(exc, "code", None) == 404 or type(exc).__name__ == "NotFound"


def _is_streaming_buffer_err(exc: Exception) -> bool:
    if getattr(exc, "code", None) == 400 or type(exc).__name__ == "BadRequest":
        return "streaming buffer" in str(exc).lower()
    return False


def _ga4_submit_user_deletion(uid: str, sa_file: str) -> bool:
    """Envia pedido de remoção de usuário no GA4 via Admin API v1alpha (`submitUserDeletion`)."""
    if not _full(SITE_UID_RE, uid) or not sa_file:
        return False
    try:
        from google.auth.transport.requests import AuthorizedSession  # type: ignore[import-not-found]
        from google.oauth2 import service_account  # type: ignore[import-not-found]
    except ImportError:
        return False
    try:
        creds = service_account.Credentials.from_service_account_file(sa_file, scopes=[GA4_EDIT_SCOPE])
        session = AuthorizedSession(creds)
        resp = session.post(GA4_DELETE_URL, json={"userId": uid}, timeout=30)
        return 200 <= int(getattr(resp, "status_code", 0)) < 300
    except Exception as e:  # noqa: BLE001
        if _is_google_err(e):
            return False
        raise


def _normalize_sessoes(raw) -> list[tuple[str, int]]:
    seen: set[tuple[str, int]] = set()
    if not raw:
        return []
    for item in raw:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            psid, sid = item
        elif isinstance(item, dict):
            psid, sid = item.get("user_pseudo_id"), item.get("ga_session_id")
        else:
            continue
        if isinstance(psid, str) and psid and isinstance(sid, int) and not isinstance(sid, bool):
            seen.add((psid, sid))
    return sorted(seen)


def _parse_sessoes_json(raw: str | None) -> list[tuple[str, int]]:
    if not isinstance(raw, str) or not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    return _normalize_sessoes(data) if isinstance(data, list) else []


def _dump_sessoes_json(sessoes) -> str:
    """JSON compacto das sessões; nunca corta: acima do teto levanta `SessoesExcedidas`."""
    pairs = _normalize_sessoes(sessoes)
    encoded = json.dumps([[p, s] for p, s in pairs], separators=(",", ":"), ensure_ascii=False)
    if len(encoded) > GA4_PURGE_SESSOES_MAX:
        raise SessoesExcedidas("sessões acima do teto da fila")
    return encoded


def _bq_cfg(bigquery, query_parameters=None):
    """Config de consulta do BigQuery com o teto de bytes cobrados (BQ_MAX_BYTES)."""
    cfg = bigquery.QueryJobConfig()
    cfg.query_parameters = list(query_parameters or [])
    cfg.maximum_bytes_billed = BQ_MAX_BYTES
    return cfg


def _bq_user_tables_info_sql() -> str:
    return (
        f"SELECT table_name\n"
        f"FROM `{GCP_PROJECT}.{BQ_DATASET}.INFORMATION_SCHEMA.TABLES`\n"
        f"WHERE table_name LIKE 'users_%'\n"
        f"   OR table_name LIKE 'pseudonymous_users_%'"
    )


def _bq_discover_events_sql(has_known_sessoes: bool = False) -> str:
    extra_union = (
        "\n  UNION DISTINCT\n"
        "  SELECT s.user_pseudo_id, s.ga_session_id\n"
        "  FROM UNNEST(@sessoes_conhecidas) AS s"
        if has_known_sessoes
        else ""
    )
    return (
        f"WITH eventos AS (\n"
        f"  SELECT\n"
        f"    _TABLE_SUFFIX AS table_suffix,\n"
        f"    user_id,\n"
        f"    user_pseudo_id,\n"
        f"    (SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'ga_session_id') AS ga_session_id\n"
        f"  FROM `{GCP_PROJECT}.{BQ_DATASET}.events_*`\n"
        f"),\n"
        f"sessoes AS (\n"
        f"  SELECT DISTINCT user_pseudo_id, ga_session_id\n"
        f"  FROM eventos\n"
        f"  WHERE user_id = @uid\n"
        f"    AND user_pseudo_id IS NOT NULL\n"
        f"    AND ga_session_id IS NOT NULL"
        f"{extra_union}\n"
        f")\n"
        f"SELECT DISTINCT\n"
        f"  e.table_suffix,\n"
        f"  e.user_pseudo_id,\n"
        f"  e.ga_session_id\n"
        f"FROM eventos AS e\n"
        f"LEFT JOIN sessoes AS s\n"
        f"  ON e.user_pseudo_id = s.user_pseudo_id\n"
        f" AND e.ga_session_id = s.ga_session_id\n"
        f"WHERE e.user_id = @uid\n"
        f"   OR ((e.user_id IS NULL OR e.user_id = '') AND s.user_pseudo_id IS NOT NULL)"
    )


def _bq_delete_events_sql(table_ref: str, has_sessoes: bool) -> str:
    if not has_sessoes:
        return f"DELETE FROM {table_ref}\nWHERE user_id = @uid"
    return (
        f"DELETE FROM {table_ref}\n"
        f"WHERE user_id = @uid\n"
        f"   OR (\n"
        f"     (user_id IS NULL OR user_id = '')\n"
        f"     AND STRUCT(\n"
        f"       user_pseudo_id,\n"
        f"       (SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'ga_session_id')\n"
        f"     ) IN (\n"
        f"       SELECT AS STRUCT s.user_pseudo_id, s.ga_session_id\n"
        f"       FROM UNNEST(@sessoes) AS s\n"
        f"     )\n"
        f"   )"
    )


def _bq_shared_pseudo_ids_sql() -> str:
    return (
        f"SELECT DISTINCT user_pseudo_id\n"
        f"FROM `{GCP_PROJECT}.{BQ_DATASET}.events_*`\n"
        f"WHERE user_pseudo_id IN UNNEST(@pseudo_ids)\n"
        f"  AND user_id IS NOT NULL\n"
        f"  AND user_id != ''\n"
        f"  AND user_id != @uid"
    )


def _bq_sessoes_param(bigquery, name: str, sessoes: list[tuple[str, int]]):
    return bigquery.ArrayQueryParameter(
        name,
        "STRUCT",
        [
            bigquery.StructQueryParameter(
                None,
                bigquery.ScalarQueryParameter("user_pseudo_id", "STRING", psid),
                bigquery.ScalarQueryParameter("ga_session_id", "INT64", sid),
            )
            for psid, sid in sessoes
        ],
    )


def _bq_apagar_uid(uid: str, sa_file: str, sessoes_conhecidas=(), persistir=None) -> dict:
    """Remove os dados do `uid` no export do GA4 no BigQuery tabela por tabela (nunca DELETE no wildcard):
    - `events_YYYYMMDD` e `events_intraday_YYYYMMDD`: linhas com `user_id = @uid` e linhas anônimas
      (`user_id IS NULL OR user_id = ''`) dos pares `(user_pseudo_id, ga_session_id)` descobertos ou já salvos
      (nunca apaga linhas com `user_id` de outra pessoa);
    - `users_YYYYMMDD` existentes: `WHERE user_id = @uid`;
    - `pseudonymous_users_YYYYMMDD` existentes: `WHERE pseudo_user_id IN UNNEST(@pseudo_ids)` apenas para os
      `user_pseudo_id` descobertos que nunca apareceram com outro `user_id` em `events_*`.
    Dataset ou tabela não encontrados na descoberta levantam `GoogleIndisponivel` (erro, não sucesso).
    Tabelas com recusa de DML por streaming buffer contam em `adiadas`.
    `persistir(sessoes)` é chamado com a união das sessões logo depois da descoberta e ANTES do primeiro DELETE: uma
    falha no meio não perde as sessões (as linhas com o uid já apagadas não deixariam redescobri-las)."""
    if not _full(SITE_UID_RE, uid):
        raise SyncError("uid inválido na remoção do BigQuery")
    if not sa_file:
        raise GoogleIndisponivel("HEATING_GCP_SA_FILE ausente")
    try:
        from google.cloud import bigquery  # type: ignore[import-not-found]
        from google.oauth2 import service_account  # type: ignore[import-not-found]
    except ImportError as e:
        raise GoogleIndisponivel("biblioteca do BigQuery ausente") from e

    known_sessoes = _normalize_sessoes(sessoes_conhecidas)
    discover_sql = _bq_discover_events_sql(bool(known_sessoes))

    try:
        creds = service_account.Credentials.from_service_account_file(sa_file)
        client = bigquery.Client(project=GCP_PROJECT, credentials=creds)

        # 1) Descobre tabelas de usuário existentes (e valida existência do dataset; NotFound é erro).
        info_rows = list(client.query(_bq_user_tables_info_sql(), job_config=_bq_cfg(bigquery), location=BQ_LOCATION).result())
        users_suffixes: set[str] = set()
        pseudo_suffixes: set[str] = set()
        for r in info_rows:
            tname = r["table_name"]
            if not isinstance(tname, str):
                continue
            m_u = BQ_USERS_TABLE_RE.fullmatch(tname)
            if m_u:
                users_suffixes.add(m_u.group(1))
                continue
            m_p = BQ_PSEUDO_TABLE_RE.fullmatch(tname)
            if m_p:
                pseudo_suffixes.add(m_p.group(1))

        # 2) Descobre tabelas `events_*` e pares `(user_pseudo_id, ga_session_id)` (NotFound é erro).
        disc_params: list = [bigquery.ScalarQueryParameter("uid", "STRING", uid)]
        if known_sessoes:
            disc_params.append(_bq_sessoes_param(bigquery, "sessoes_conhecidas", known_sessoes))
        rows = list(
            client.query(
                discover_sql,
                job_config=_bq_cfg(bigquery, query_parameters=disc_params),
                location=BQ_LOCATION,
            ).result()
        )

        tabelas: dict[str, set[tuple[str, int]]] = {}
        all_sessoes: set[tuple[str, int]] = set(known_sessoes)
        candidate_pseudo_ids: set[str] = {psid for psid, _ in all_sessoes}
        for row in rows:
            suffix = row["table_suffix"]
            if not _full(BQ_TABLE_SUFFIX_RE, suffix):
                continue
            sess = tabelas.setdefault(suffix, set())
            psid = row["user_pseudo_id"]
            sid = row["ga_session_id"]
            if isinstance(psid, str) and psid:
                candidate_pseudo_ids.add(psid)
                if isinstance(sid, int) and not isinstance(sid, bool):
                    sess.add((psid, sid))
                    all_sessoes.add((psid, sid))

        # 2b) Guarda a união das sessões antes de qualquer DELETE.
        if persistir is not None:
            persistir(sorted(all_sessoes))

        # 3) Antes de apagar `events_*`, verifica quais `user_pseudo_id` apareceram com outro `user_id`.
        exclusive_pseudo_ids: list[str] = []
        if pseudo_suffixes and candidate_pseudo_ids:
            shared_cfg = _bq_cfg(
                bigquery,
                query_parameters=[
                    bigquery.ScalarQueryParameter("uid", "STRING", uid),
                    bigquery.ArrayQueryParameter("pseudo_ids", "STRING", sorted(candidate_pseudo_ids)),
                ]
            )
            shared_rows = list(
                client.query(_bq_shared_pseudo_ids_sql(), job_config=shared_cfg, location=BQ_LOCATION).result()
            )
            shared_pseudo_ids = {
                r["user_pseudo_id"] for r in shared_rows if isinstance(r["user_pseudo_id"], str) and r["user_pseudo_id"]
            }
            exclusive_pseudo_ids = sorted(candidate_pseudo_ids - shared_pseudo_ids)

        tabelas_limpas = 0
        adiadas = 0

        # 4) DELETE tabela por tabela em `events_*` (nunca no wildcard, nunca em outro `user_id`).
        for suffix in sorted(tabelas):
            table_ref = f"`{GCP_PROJECT}.{BQ_DATASET}.events_{suffix}`"
            sessoes_list = sorted(tabelas[suffix])
            params: list = [bigquery.ScalarQueryParameter("uid", "STRING", uid)]
            if sessoes_list:
                params.append(_bq_sessoes_param(bigquery, "sessoes", sessoes_list))
            del_sql = _bq_delete_events_sql(table_ref, bool(sessoes_list))
            try:
                client.query(
                    del_sql,
                    job_config=_bq_cfg(bigquery, query_parameters=params),
                    location=BQ_LOCATION,
                ).result()
                tabelas_limpas += 1
            except Exception as e:  # noqa: BLE001
                if suffix.startswith("intraday_") and (_is_streaming_buffer_err(e) or _is_not_found(e)):
                    adiadas += 1
                elif _is_streaming_buffer_err(e):
                    adiadas += 1
                elif _is_not_found(e):
                    continue
                else:
                    raise

        # 5) DELETE tabela por tabela em `users_YYYYMMDD` existentes (`WHERE user_id = @uid`).
        if users_suffixes:
            users_disc_sql = (
                f"SELECT DISTINCT _TABLE_SUFFIX AS table_suffix\n"
                f"FROM `{GCP_PROJECT}.{BQ_DATASET}.users_*`\n"
                f"WHERE user_id = @uid"
            )
            u_rows = list(
                client.query(
                    users_disc_sql,
                    job_config=_bq_cfg(
                bigquery,
                        query_parameters=[bigquery.ScalarQueryParameter("uid", "STRING", uid)]
                    ),
                    location=BQ_LOCATION,
                ).result()
            )
            matched_u = sorted({
                r["table_suffix"]
                for r in u_rows
                if _full(BQ_DAY_SUFFIX_RE, r["table_suffix"]) and r["table_suffix"] in users_suffixes
            })
            for suffix in matched_u:
                del_u_sql = f"DELETE FROM `{GCP_PROJECT}.{BQ_DATASET}.users_{suffix}`\nWHERE user_id = @uid"
                try:
                    client.query(
                        del_u_sql,
                        job_config=_bq_cfg(
                bigquery,
                            query_parameters=[bigquery.ScalarQueryParameter("uid", "STRING", uid)]
                        ),
                        location=BQ_LOCATION,
                    ).result()
                    tabelas_limpas += 1
                except Exception as e:  # noqa: BLE001
                    if _is_streaming_buffer_err(e):
                        adiadas += 1
                    elif _is_not_found(e):
                        continue
                    else:
                        raise

        # 6) DELETE tabela por tabela em `pseudonymous_users_YYYYMMDD` existentes (só `user_pseudo_id` exclusivos).
        if pseudo_suffixes and exclusive_pseudo_ids:
            pseudo_disc_sql = (
                f"SELECT DISTINCT _TABLE_SUFFIX AS table_suffix\n"
                f"FROM `{GCP_PROJECT}.{BQ_DATASET}.pseudonymous_users_*`\n"
                f"WHERE pseudo_user_id IN UNNEST(@pseudo_ids)"
            )
            p_cfg = _bq_cfg(
                bigquery,
                query_parameters=[bigquery.ArrayQueryParameter("pseudo_ids", "STRING", exclusive_pseudo_ids)]
            )
            p_rows = list(client.query(pseudo_disc_sql, job_config=p_cfg, location=BQ_LOCATION).result())
            matched_p = sorted({
                r["table_suffix"]
                for r in p_rows
                if _full(BQ_DAY_SUFFIX_RE, r["table_suffix"]) and r["table_suffix"] in pseudo_suffixes
            })
            for suffix in matched_p:
                del_p_sql = (
                    f"DELETE FROM `{GCP_PROJECT}.{BQ_DATASET}.pseudonymous_users_{suffix}`\n"
                    f"WHERE pseudo_user_id IN UNNEST(@pseudo_ids)"
                )
                try:
                    client.query(
                        del_p_sql,
                        job_config=_bq_cfg(
                bigquery,
                            query_parameters=[bigquery.ArrayQueryParameter("pseudo_ids", "STRING", exclusive_pseudo_ids)]
                        ),
                        location=BQ_LOCATION,
                    ).result()
                    tabelas_limpas += 1
                except Exception as e:  # noqa: BLE001
                    if _is_streaming_buffer_err(e):
                        adiadas += 1
                    elif _is_not_found(e):
                        continue
                    else:
                        raise

        return {"tabelas_limpas": tabelas_limpas, "adiadas": adiadas, "sessoes": sorted(all_sessoes)}
    except GoogleIndisponivel:
        raise
    except Exception as e:  # noqa: BLE001
        if _is_google_err(e):
            raise GoogleIndisponivel(f"remoção no BigQuery falhou ({type(e).__name__})") from e
        raise


def processar_fila_ga4(
    c: TS.Clients,
    *,
    uids: list[str] | None = None,
    only_due: bool = False,
    include_uids: list[str] | None = None,
    ga4_delete=None,
    bq_delete=None,
    now_dt: datetime | None = None,
    stop_on_bq_error: bool = False,
) -> dict:
    """Processa itens da fila única `ga4_purge_queue` (migração 0014):
    - GA4 (`submitUserDeletion`) enquanto `ga4_pendente = 1`: sucesso zera `ga4_pendente`; 403/erro mantém 1 e conta
      em `ga4_remocao_pendente`;
    - BigQuery (`_bq_apagar_uid`) com os pares salvos em `sessoes` somados aos descobertos agora, repetido em D+1
      (tentativa 2), D+3 (tentativa 3) e enquanto houver tabela adiada por streaming buffer. As sessões descobertas
      são gravadas na fila antes do primeiro DELETE (`persistir`). Da 5ª tentativa em diante, tabela ainda adiada não
      tira o item da fila: ele continua todo dia e conta em `bq_remocao_travada` (o wrapper alerta).
      Em falha operacional do BigQuery, mantém `bq_tentativas` e agenda `proxima_em = agora` (tentativa imediata).
    O item só sai da fila quando GA4 está feito (`ga4_pendente = 0`) e o BigQuery terminou (`bq_concluido = 1`)."""
    res = {
        "ga4_removidos": 0,
        "ga4_remocao_pendente": 0,
        "bq_tabelas_limpas": 0,
        "bq_remocao_adiada": 0,
        "bq_remocao_falhou": 0,
        "bq_remocao_travada": 0,
        "bq_sessoes_excedidas": 0,
        "ga4_fila_processados": 0,
        "ga4_fila_restantes": 0,
        "sem_credencial": False,
    }
    now = now_dt or _now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)
    now_iso = stamp(now)

    rows: list[dict] = []
    seen_uids: set[str] = set()
    if uids is not None:
        valid_uids = sorted({u for u in uids if isinstance(u, str) and _full(SITE_UID_RE, u)})
        for i in range(0, len(valid_uids), MARK_CHUNK):
            part = valid_uids[i:i + MARK_CHUNK]
            ph = ",".join("?" * len(part))
            for r in c.d1(
                f"SELECT uid, ga4_pendente, sessoes, bq_tentativas, bq_concluido, proxima_em, criado_em "
                f"FROM ga4_purge_queue WHERE uid IN ({ph}) ORDER BY uid",
                part,
            ):
                if r["uid"] not in seen_uids:
                    seen_uids.add(r["uid"])
                    rows.append(r)
    elif only_due:
        for r in c.d1(
            "SELECT uid, ga4_pendente, sessoes, bq_tentativas, bq_concluido, proxima_em, criado_em "
            "FROM ga4_purge_queue WHERE proxima_em <= ? ORDER BY proxima_em, uid",
            [now_iso],
        ):
            if r["uid"] not in seen_uids:
                seen_uids.add(r["uid"])
                rows.append(r)
        if include_uids:
            extra = sorted({u for u in include_uids if isinstance(u, str) and _full(SITE_UID_RE, u) and u not in seen_uids})
            for i in range(0, len(extra), MARK_CHUNK):
                part = extra[i:i + MARK_CHUNK]
                ph = ",".join("?" * len(part))
                for r in c.d1(
                    f"SELECT uid, ga4_pendente, sessoes, bq_tentativas, bq_concluido, proxima_em, criado_em "
                    f"FROM ga4_purge_queue WHERE uid IN ({ph}) ORDER BY uid",
                    part,
                ):
                    if r["uid"] not in seen_uids:
                        seen_uids.add(r["uid"])
                        rows.append(r)

    if not rows:
        return res

    sa_file = (c.env.get("HEATING_GCP_SA_FILE") or "").strip()
    has_cred = bool(sa_file) or ga4_delete is not None or bq_delete is not None
    if not has_cred:
        res["sem_credencial"] = True
        res["ga4_fila_restantes"] = len(rows)
        return res

    for r in rows:
        uid = r.get("uid")
        if not _full(SITE_UID_RE, uid):
            continue
        ga4_pend = 1 if int(r.get("ga4_pendente") or 0) else 0
        saved_sessoes = _parse_sessoes_json(r.get("sessoes"))
        bq_tent = int(r.get("bq_tentativas") or 0)

        if ga4_pend == 1:
            if ga4_delete is not None:
                try:
                    ok_ga4 = bool(ga4_delete(uid))
                except (GoogleIndisponivel, OSError):
                    ok_ga4 = False
            elif sa_file:
                ok_ga4 = _ga4_submit_user_deletion(uid, sa_file)
            else:
                ok_ga4 = False
            if ok_ga4:
                ga4_pend = 0
                res["ga4_removidos"] += 1
            else:
                res["ga4_remocao_pendente"] += 1

        bq_done = bool(int(r.get("bq_concluido") or 0))
        bq_failed = False
        new_tent = bq_tent
        # Sessões conhecidas: as salvas e as que a descoberta desta tentativa gravar antes do primeiro DELETE.
        known = {"sessoes": list(saved_sessoes)}

        def persistir(novas, _uid=uid, _known=known):
            merged = _normalize_sessoes(list(_known["sessoes"]) + list(novas or ()))
            c.d1("UPDATE ga4_purge_queue SET sessoes = ? WHERE uid = ?", [_dump_sessoes_json(merged), _uid])
            _known["sessoes"] = merged

        next_due = stamp(now + timedelta(days=1))

        if not bq_done:
            try:
                if bq_delete is not None:
                    try:
                        accepts = "persistir" in inspect.signature(bq_delete).parameters
                    except (TypeError, ValueError):
                        accepts = False
                    bq_res = (bq_delete(uid, saved_sessoes, persistir=persistir) if accepts
                              else bq_delete(uid, saved_sessoes)) or {}
                elif sa_file:
                    bq_res = _bq_apagar_uid(uid, sa_file, sessoes_conhecidas=saved_sessoes, persistir=persistir)
                else:
                    raise GoogleIndisponivel("HEATING_GCP_SA_FILE ausente")
                res["bq_tabelas_limpas"] += int(bq_res.get("tabelas_limpas", 0))
                adiadas = int(bq_res.get("adiadas", 0))
                res["bq_remocao_adiada"] += adiadas
                known["sessoes"] = _normalize_sessoes(list(known["sessoes"]) + list(bq_res.get("sessoes") or ()))
                new_tent = min(bq_tent + 1, GA4_PURGE_MAX_TENTATIVAS)
                if new_tent == 1:
                    next_due = stamp(now + timedelta(days=1))  # D+1
                elif new_tent == 2:
                    next_due = stamp(now + timedelta(days=2))  # D+3
                elif adiadas == 0:
                    bq_done = True
                else:
                    next_due = stamp(now + timedelta(days=1))
                    if new_tent >= GA4_PURGE_MAX_TENTATIVAS:
                        res["bq_remocao_travada"] += 1
            except SessoesExcedidas:
                bq_failed = True
                res["bq_sessoes_excedidas"] += 1
                next_due = stamp(now + timedelta(days=1))
            except (GoogleIndisponivel, OSError):
                bq_failed = True
                res["bq_remocao_falhou"] += 1
                next_due = now_iso

        if ga4_pend == 0 and bq_done:
            c.d1("DELETE FROM ga4_purge_queue WHERE uid = ?", [uid])
            res["ga4_fila_processados"] += 1
        elif bq_failed:
            # As sessões já gravadas por `persistir` ficam como estão (nada de sobrescrever com uma lista velha).
            c.d1(
                "UPDATE ga4_purge_queue SET ga4_pendente = ?, bq_tentativas = ?, proxima_em = ? WHERE uid = ?",
                [ga4_pend, bq_tent, next_due, uid],
            )
            res["ga4_fila_restantes"] += 1
            if stop_on_bq_error:
                break
        else:
            c.d1(
                "UPDATE ga4_purge_queue SET ga4_pendente = ?, sessoes = ?, bq_tentativas = ?, bq_concluido = ?, "
                "proxima_em = ? WHERE uid = ?",
                [ga4_pend, _dump_sessoes_json(known["sessoes"]), new_tent, 1 if bq_done else 0, next_due, uid],
            )
            res["ga4_fila_processados"] += 1
            res["ga4_fila_restantes"] += 1

    return res


def forget(
    c: TS.Clients,
    email: str,
    *,
    apply: bool = False,
    out=None,
    forget_key: str | None = None,
    ga4_delete=None,
    bq_delete=None,
    now_dt: datetime | None = None,
) -> Counter:
    """Grava o marcador `forgotten` (issue #134) e a oposição permanente ao heating em `heating_optout` (decisão do
    Gui, 2026-11-27; épico #197) antes de apagar o resto, apaga as conversões do Twenty e do D1, limpa o heating do
    contexto `site` no D1 e na Pessoa do Twenty (sem tocar nos contextos de curso), apaga `site_identities` (cujo
    trigger enfileira os `uid`s em `ga4_purge_queue`), processa a fila imediatamente quando há credencial GCP e limpa
    marcadores vencidos de `forgotten`."""
    out = out or sys.stdout
    key = c.env.get("FORGET_HMAC_KEY", "") if forget_key is None else forget_key
    email = email.strip().lower()
    email_hmac = forget_hmac(email, key)  # valida chave e email antes de qualquer leitura ou escrita
    optout_hmac = heating_optout_hmac(email, key)
    counts: Counter = Counter()
    if apply:
        # 1) Marcadores no D1 logo após validar chave e email, antes de qualquer leitura do D1 ou do Twenty (#134, #197):
        #    mesmo que a leitura do Twenty falhe em seguida, links antigos já ficam bloqueados e a oposição permanente
        #    ao heating impede nova pontuação e novo vínculo pelo login (`/identidade`).
        _upsert_forget_mark(c, email_hmac)
        _upsert_heating_optout(c, optout_hmac)
    # As linhas do D1 são o diário: enquanto existirem, os ids delas acham as Conversões no Twenty e os
    # `twenty_person_id` de `conversions` e `heating` (contexto `site`) acham a Pessoa ativa mesmo se o email mudou.
    conv_rows = c.d1("SELECT id, twenty_person_id FROM conversions WHERE email = ?", [email])
    ids = {r["id"] for r in conv_rows}
    site_events = int(c.d1("SELECT count(*) AS n FROM engagement_events WHERE context = 'site' AND email = ?", [email])[0]["n"])
    heating_rows = c.d1("SELECT twenty_person_id FROM heating WHERE context = 'site' AND email = ?", [email])
    site_heating = len(heating_rows)
    raw_identities = c.d1("SELECT uid FROM site_identities WHERE email = ?", [email])
    site_uids = sorted({r["uid"] for r in raw_identities if _full(SITE_UID_RE, r.get("uid"))})
    ref_pids = {
        r["twenty_person_id"]
        for r in (*conv_rows, *heating_rows)
        if _full(TWENTY_PERSON_ID, r.get("twenty_person_id"))
    }
    expired_marks = _count_expired_marks(c, exclude_hmac=email_hmac)
    if apply and expired_marks:
        # Limpeza dos marcadores vencidos independe do Twenty (#134).
        _prune_expired_marks(c)
    meta = read_metadata(c)
    all_people = list_records(c, "people")
    persons = people_by_email(all_people).get(email) or []
    active_by_id = {p["id"]: p for p in all_people if not p.get("deletedAt")}
    heating_persons = list(persons)
    seen_pids = {p["id"] for p in heating_persons}
    for pid in sorted(ref_pids):
        if pid not in seen_pids and pid in active_by_id:
            seen_pids.add(pid)
            heating_persons.append(active_by_id[pid])
    convs = [x for x in list_records(c, PLURAL) if x.get("eventoId") in ids] if meta["conv_id"] else []
    heating_fields = [k for k in HEATING_PERSON_FIELDS if k in meta["person"]]
    to_clear = [p for p in heating_persons if any(_filled(p.get(k)) for k in heating_fields)]
    counts["d1_linhas"], counts["conversoes_no_crm"], counts["pessoas_mantidas"] = len(ids), len(convs), len(persons)
    counts["eventos_site"], counts["heating_site"], counts["pessoas_heating_limpas"] = site_events, site_heating, len(to_clear)
    counts["identidades_site"] = len(site_uids)
    counts["marcadores_vencidos"] = expired_marks
    ga4_pendente = False
    bq_falhou = False
    sem_cred_enfileirado = False
    if apply:
        # 2) Apaga no Twenty e relê para provar que nenhuma sobrou; limpa os quatro campos heating* na Pessoa.
        for x in convs:
            c.twenty("DELETE", f"/rest/{PLURAL}/{urllib.parse.quote(x['id'])}", op="apagar conversão")
        if meta["conv_id"] and any(x.get("eventoId") in ids for x in list_records(c, PLURAL)):
            raise SyncError("ainda há Conversões do email no Twenty; o D1 ficou intacto, rode de novo")
        if heating_fields:
            clear_patch = {k: None for k in heating_fields}
            for p in to_clear:
                c.twenty("PATCH", f"/rest/people/{urllib.parse.quote(p['id'])}", clear_patch, op="limpar heating da pessoa")
        # 3) Apaga as linhas do email em `conversions`, `engagement_events` (site) e `heating` (site) e confirma.
        c.d1("DELETE FROM conversions WHERE email = ?", [email])
        if c.d1("SELECT count(*) AS n FROM conversions WHERE email = ?", [email])[0]["n"] != 0:
            raise SyncError("o D1 ainda tem linhas do email depois da remoção")
        c.d1("DELETE FROM engagement_events WHERE context = 'site' AND email = ?", [email])
        if c.d1("SELECT count(*) AS n FROM engagement_events WHERE context = 'site' AND email = ?", [email])[0]["n"] != 0:
            raise SyncError("o D1 ainda tem eventos de site do email depois da remoção")
        c.d1("DELETE FROM heating WHERE context = 'site' AND email = ?", [email])
        if c.d1("SELECT count(*) AS n FROM heating WHERE context = 'site' AND email = ?", [email])[0]["n"] != 0:
            raise SyncError("o D1 ainda tem nota de site do email depois da remoção")
        # 4) Apaga `site_identities` (o trigger da 0014 enfileira os uids em `ga4_purge_queue`);
        #    quando há credencial GCP, processa a fila na hora; sem credencial (Mac), só enfileira e avisa.
        if raw_identities:
            c.d1("DELETE FROM site_identities WHERE email = ?", [email])
            if c.d1("SELECT count(*) AS n FROM site_identities WHERE email = ?", [email])[0]["n"] != 0:
                raise SyncError("o D1 ainda tem identidades do site do email depois da remoção")
        if site_uids:
            g_res = processar_fila_ga4(
                c,
                uids=site_uids,
                ga4_delete=ga4_delete,
                bq_delete=bq_delete,
                now_dt=now_dt,
            )
            if g_res["sem_credencial"]:
                counts["ga4_fila_enfileirados"] = len(site_uids)
                sem_cred_enfileirado = True
            else:
                counts["ga4_removidos"] = g_res["ga4_removidos"]
                counts["ga4_remocao_pendente"] = g_res["ga4_remocao_pendente"]
                counts["bq_tabelas_limpas"] = g_res["bq_tabelas_limpas"]
                counts["bq_remocao_adiada"] = g_res["bq_remocao_adiada"]
                counts["ga4_fila_restantes"] = g_res["ga4_fila_restantes"]
                if g_res["bq_remocao_falhou"]:
                    counts["bq_remocao_falhou"] = g_res["bq_remocao_falhou"]
                    bq_falhou = True
                ga4_pendente = g_res["ga4_remocao_pendente"] > 0
    if apply:
        # A fila não depende do vínculo deste email (já apagado numa rodada anterior): o comando repetido continua
        # avisando e saindo com código 2 enquanto houver remoção no GA4 pendente, até o job diário concluir.
        counts["ga4_fila_pendente_total"] = int(
            c.d1("SELECT count(*) AS n FROM ga4_purge_queue WHERE ga4_pendente = 1")[0]["n"]
        )
    _report(counts, out, FORGET_MARK if apply else "simulação: nada foi apagado (use --apply)")
    if apply and counts.get("ga4_fila_pendente_total", 0) > 0 and not ga4_pendente and not sem_cred_enfileirado:
        print(GA4_FILA_PENDENTE_MSG, file=out)
    if apply and sem_cred_enfileirado:
        print(GA4_FILA_SEM_CRED_MSG, file=out)
    else:
        if apply and ga4_pendente:
            print(GA4_PURGE_PENDENTE_MSG, file=out)
        if apply and bq_falhou:
            print(BQ_PURGE_PENDENTE_MSG, file=out)
    return counts


def _report(counts: Counter, out, tail: str) -> None:
    for k in sorted(counts):
        print(f"{k}: {counts[k]}", file=out)
    print(tail, file=out)


def main(
    argv=None,
    transport=TS._transport,
    sleep=time.sleep,
    stdin=None,
    *,
    ga4_delete=None,
    bq_delete=None,
    now_dt: datetime | None = None,
) -> int:
    ap = TS._Parser(description="Leva as conversões do site para o Twenty (simulação por padrão).")
    ap.add_argument("--apply", action="store_true", help="grava no Twenty e no D1")
    ap.add_argument("--forget", action="store_true", help="apaga as conversões do email lido da entrada padrão")
    a = ap.parse_args(argv)
    env = {k: os.environ.get(k, "") for k in TS.REQUIRED_ENV}
    if not all(env.values()):
        print("ERRO: faltam variáveis de ambiente: " + ", ".join(k for k, v in env.items() if not v), file=sys.stderr)
        return 2
    forget_key = os.environ.get("FORGET_HMAC_KEY", "") if a.forget else ""
    if a.forget and len(forget_key) < MIN_FORGET_KEY:
        msg = ("falta variável de ambiente: FORGET_HMAC_KEY" if not forget_key
               else f"FORGET_HMAC_KEY curta demais (mínimo {MIN_FORGET_KEY} caracteres)")
        print(f"ERRO: {msg}", file=sys.stderr)
        return 2
    if "HEATING_GCP_SA_FILE" in os.environ:
        env["HEATING_GCP_SA_FILE"] = os.environ["HEATING_GCP_SA_FILE"]
    lock = None
    rc = 0
    try:
        lock = open(os.environ.get("CONVERSOES_SYNC_LOCK") or LOCK_DEFAULT, "a")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SyncError("outra execução do sync ou do --forget em andamento") from None
        c = TS.Clients(env, transport, sleep)
        if a.forget:
            lines = [x for x in (stdin or sys.stdin).read().splitlines() if x.strip()]
            if len(lines) != 1:
                raise SyncError("--forget espera exatamente um email na entrada padrão")
            counts = forget(
                c,
                lines[0],
                apply=a.apply,
                forget_key=forget_key,
                ga4_delete=ga4_delete,
                bq_delete=bq_delete,
                now_dt=now_dt,
            )
            if a.apply and (counts.get("ga4_remocao_pendente", 0) > 0 or counts.get("bq_remocao_falhou", 0) > 0
                            or counts.get("ga4_fila_pendente_total", 0) > 0):
                rc = 2
        else:
            run(c, apply=a.apply)
    except SyncError as e:
        print(f"ERRO: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"ERRO: falha inesperada ({type(e).__name__})", file=sys.stderr)
        return 1
    finally:
        if lock is not None:
            lock.close()  # fechar o arquivo solta o lock
    return rc


if __name__ == "__main__":
    sys.exit(main())

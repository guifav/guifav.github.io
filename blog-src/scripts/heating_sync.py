"""Calcula o heating (temperatura de engajamento) dos contatos no D1 `blog-admin` e leva para o Twenty (crm.guifav.dev).
Épico #197, cortes 1 e 2.

Simulação por padrão: lê o D1, o Firestore (curso), o BigQuery (export do GA4 do site) e o Twenty e mostra só contagens.
Grava apenas com --apply.

Contextos separados (decisão do Gui, 2026-11-27):
- `site`: conversões do site, emails da newsletter e dos materiais, e eventos do site vindos do export do GA4 no
  BigQuery (corte 2). A nota vai para a tabela `heating` no D1 e para os quatro campos `heating*` na Person do Twenty.
- `curso:<slug>`: cadastro, aulas concluídas e emails do próprio curso. A nota vai para `heating` no D1 e para o objeto
  `courseEnrollment` ("Inscrição em curso") ligado à Person no Twenty.

Campos no Twenty (criados se faltarem; campos já existentes no Twenty nunca são renomeados nem alterados):
- `heatingSinais` na Person e `sinais` na Inscrição em curso guardam só os rótulos dos três principais sinais, em
  ordem, sem os pontos (os pontos decaídos ficam em `heating.signals` no D1, exibidos no painel).
- `heatingCalculadoEm` na Person (rótulo "Heating: nota mudou em") e `calculadoEm` na Inscrição em curso (rótulo
  "Nota mudou em") só mudam quando a nota, a faixa ou a lista de rótulos mudam, para não reescrever Pessoas e
  Inscrições todo dia. A data do último cálculo diário fica em `heating.computed_at` no D1 (exibida no painel
  `/admin/contatos?ordem=heating`). Se o campo já existir no Twenty com outro rótulo, o script mantém o campo existente
  sem renomeá-lo.

Decisões registradas do desenho:
- Criação de Pessoa pelos cursos: a Pessoa no Twenty é criada para todo inscrito de curso disponível, exceto email
  inválido (`bounced`), emails excluídos (`HEATING_EXCLUDE` e domínios de teste), quem está na lixeira ou em
  `curso_esquecido` — decisão do Gui de 2026-11-24 (os 42 inscritos vão ao CRM) e de 2026-11-27 ("o job do heating cria
  a pessoa que entrou").
- Teto no contexto de curso: as aulas (`curso_aula`) não têm teto somado (só 1 por dia UTC). O teto de 20 da régua
  original existia para o curso não inflar a nota do site; com o contexto próprio, aula é exatamente o que a nota do
  curso mede. O teto somado de 20 vale para os passivos (`email_aberto`) do curso.
- Status desconhecido no curso (Firestore indisponível na rodada ou inscrito ausente do Firestore): os sinais de email
  do curso não contam (bloqueio conservador).
- Oposição concorrente (`POST /prioridade` durante a rodada): todo `INSERT` em `engagement_events` e `heating` é
  condicional na mesma instrução (`WHERE NOT EXISTS (SELECT 1 FROM heating_optout WHERE email_hmac = ...)`), e no fim
  da rodada (após as escritas no D1 e no Twenty) o job relê `heating_optout` e limpa D1 e Twenty (Pessoa e Inscrições)
  para qualquer marca nova desde a leitura inicial.
- Id do evento de aula (`curso_aula`): usa `cu:<slug>:aula:<googleUid>:<sha16>:<dayId>`, incluindo os 16 primeiros
  hex do SHA-256 do email normalizado para que dois documentos com o mesmo `googleUid` e emails diferentes não colidam.
  Como ainda não há evento de curso gravado em produção, não precisa de migração de ids.
- Engajamento no site pelo export do GA4 no BigQuery (corte 2): ativado apenas quando `HEATING_GA4_DESDE` (`AAAA-MM-DD`)
  está definida no ambiente (ausente = fonte desligada e nada é lido). Como o GA4 atualiza a tabela diária por até
  3 dias, cada rodada relê os 3 dias até o marcador `heating_ga4_ultimo_dia` (D1 `meta`) além dos dias novos até ontem
  (UTC), no máximo 14 dias por rodada, consultando apenas a tabela fechada do dia
  (`mindapps.analytics_341040810.events_YYYYMMDD`, nunca intraday nem wildcard), com filtro `stream_id = '13120304692'`
  e `privacy_info.analytics_storage = 'Yes'` e ligação retroativa só dentro da mesma sessão (`user_pseudo_id` +
  `ga_session_id` quando a sessão tem exatamente um `user_id`). Os ids determinísticos (`ga:<yyyymmdd>:<sha16>:<sig>`)
  deduplicam a releitura e o marcador nunca regride. Resolve `uid -> email` em `site_identities` (migração 0013), que
  também é lida sempre que houver oposição pendente ou fila (`ga4_purge_queue`), mesmo sem dias novos do GA4; `uid` sem
  vínculo é ignorado (`ga4_sem_vinculo`). Um dia sem tabela (dia sem eventos) não trava a sequência: se um dia
  posterior já tem tabela, conta em `ga4_dia_sem_tabela` e o marcador segue; se nenhum dia posterior tem tabela ainda,
  espera sem erro. Erro/indisponibilidade do BigQuery marca `ga4_indisponivel: 1`, segue sem a fonte e não avança
  `heating_ga4_ultimo_dia`. O marcador só avança depois de gravar os eventos do dia.

Dependências e papéis da conta de serviço na KVM (`HEATING_GCP_SA_FILE`, projeto `mindapps`):
- Usa `google-auth`, `google-cloud-firestore` e `google-cloud-bigquery` (3.43.0), já instalados no Python do sistema da
  KVM (importados dentro das funções de produção). Se faltarem (ou se `HEATING_GCP_SA_FILE` estiver ausente ou falhar),
  o job marca `curso_indisponivel` e/ou `ga4_indisponivel` sem derrubar as demais fontes.
- Papéis mínimos da conta de serviço (`guifav-heating@mindapps`):
  - `roles/datastore.viewer` no projeto `mindapps` (leitura do Firestore do curso);
  - BigQuery Job User (`roles/bigquery.jobUser`) no projeto `mindapps`;
  - WRITER no dataset `analytics_341040810` (leitura diária e DELETE por tabela em `events_*`, `users_*` e `pseudonymous_users_*`);
  - Editor na propriedade GA4 `341040810` (para `properties.submitUserDeletion` na fila de remoção).

Modos:
- Padrão (`heating_sync.py [--apply]`): traz conversões, curso e GA4 (quando `HEATING_GA4_DESDE` está definida) para
  `engagement_events`, aplica oposição (`heating_optout`, apagando também `site_identities` remanescentes para disparar
  o trigger), exclusões (`HEATING_EXCLUDE` e domínios de teste), esquecidos de curso (`curso_esquecido`) e retenção
  (365 dias), processa itens vencidos da fila única `ga4_purge_queue` (migração 0014, alimentada por trigger `AFTER
  DELETE ON site_identities`: chama `submitUserDeletion` no GA4 enquanto `ga4_pendente = 1`, contando 403/erro em
  `ga4_remocao_pendente`, e executa o DELETE no BigQuery em `events_*`, `users_YYYYMMDD` existentes e linhas exclusivas
  de `pseudonymous_users_YYYYMMDD` somando as `sessoes` já salvas às descobertas agora, repetido em D+1, D+3 e enquanto
  houver tabela adiada por streaming buffer: da 5ª tentativa em diante repete diariamente e conta `bq_remocao_travada`;
  com o JSON das sessões acima de 1.000.000 de caracteres (cerca de 25 mil sessões) o item fica e conta
  `bq_sessoes_excedidas`; o fechamento vale só na `geracao` lida; o item só sai da fila quando GA4 e BigQuery
  terminam),
  calcula a régua por contexto em relação ao instante T, substitui `heating` no D1 via payload `batch` da API REST e
  atualiza o Twenty (somente o que mudou).
  Risco aceito na troca de `heating`: a documentação da Cloudflare garante transação no `batch()` do binding em
  Workers, não no `batch` da API REST; se a troca falhar no meio sem rollback (deixando `heating` vazia ou parcial),
  a rodada seguinte refaz a tabela inteira a partir de `engagement_events`.
- Oposição (`heating_sync.py --optout [--apply]`): lê UM email da entrada padrão, grava a marca permanente em
  `heating_optout`, apaga eventos e notas de todos os contextos no D1, limpa os campos `heating*` da Person e
  nota/faixa/sinais/calculadoEm das Inscrições no Twenty, apaga as linhas do email em `site_identities` (o trigger
  enfileira cada `uid` em `ga4_purge_queue`) e processa a fila na hora quando há credencial GCP (`HEATING_GCP_SA_FILE`).
  Sem credencial (por exemplo no Mac), apenas enfileira, informa `ga4_fila_pendente_total` e sai com código 2 (o job
  diário da KVM conclui a remoção). Com credencial, se GA4 responder
  403/erro ou o BigQuery falhar, o item fica pendente na fila para o job diário, imprime mensagem fixa (sem `uid`) e
  sai com código 2, sem impedir as outras remoções.
- Esquecimento de curso (`heating_sync.py --forget-curso <slug> [--apply]`): lê UM email da entrada padrão, grava a
  marca permanente em `curso_esquecido` (migração 0012) para impedir rematerialização do Firestore, apaga de vez a
  Inscrição no Twenty (inclusive da lixeira) conferindo na releitura e apaga eventos e nota daquele curso no D1, sem
  apagar a Pessoa nem tocar no contexto `site`, em `heating_optout` nem no GA4/BigQuery.

Lock compartilhado: usa o mesmo arquivo de lock do `conversoes_sync.py` (`CONVERSOES_SYNC_LOCK`, padrão
`/tmp/guifav-conversoes-sync.lock`), porque os dois apagam e gravam nas mesmas tabelas. Se o lock estiver ocupado,
espera até 10 minutos e falha com mensagem fixa.

Wrapper da KVM: roda diariamente às 10:53 (BRT) a partir da `main` atualizada, com alerta por email se o script sair
com erro ou se `curso_indisponivel`, `ga4_indisponivel` (que também sobe quando o DELETE do BigQuery falha na fila,
`bq_remocao_falhou`), `ga4_remocao_pendente`, `bq_remocao_travada`, `bq_sessoes_excedidas`, `conversao_invalida`,
`curso_invalido` ou `pessoa_na_lixeira` forem maiores que zero.

Rodar na KVM (todas as credenciais vêm do ambiente):
    CLOUDFLARE_API_TOKEN=... CLOUDFLARE_ACCOUNT_ID=... D1_DATABASE_ID=... TWENTY_API_KEY=... \\
    TWENTY_LOCAL_URL=http://127.0.0.1:3100 FORGET_HMAC_KEY=... [HEATING_GCP_SA_FILE=...] \\
    [HEATING_EMAIL_DESDE=...] [HEATING_GA4_DESDE=AAAA-MM-DD] [HEATING_EXCLUDE=...] python3 scripts/heating_sync.py [--apply]
    printf '%s\\n' "$EMAIL" | FORGET_HMAC_KEY=... [HEATING_GCP_SA_FILE=...] ... python3 scripts/heating_sync.py --optout [--apply]
    printf '%s\\n' "$EMAIL" | FORGET_HMAC_KEY=... ... python3 scripts/heating_sync.py --forget-curso chatgpt-no-dia-a-dia [--apply]
"""
from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
import re
import sys
import time
import urllib.parse
from collections import Counter, OrderedDict
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import conversoes_sync as CS  # noqa: E402
import twenty_sync as TS  # noqa: E402
from twenty_sync import SyncError  # noqa: E402

# ------------------------------------------------------------------ Regras da régua (constantes documentadas)

# Meia-vida de 30 dias: cada evento perde metade do peso a cada 30 dias (pontos * 0.5 ** (idade_em_dias / 30)).
MEIA_VIDA_DIAS = 30.0
# Janela de pontuação: eventos com mais de 180 dias (em relação a T) não entram na nota; futuros (> T) também ficam fora.
JANELA_DIAS = 180.0
# Retenção do registro: eventos mais velhos que 365 dias são apagados; email_spam vale nos últimos 365 dias.
RETENCAO_DIAS = 365
# Filtro de clique de robô: descarta clique até 60 s depois do email_entregue do mesmo email e ref,
# e todos os cliques do mesmo email e ref que tenham outro clique a até 5 s.
ROBO_ENTREGA_S = 60.0
ROBO_RAJADA_S = 5.0

# Pesos e tetos do contexto `site`:
# - newsletter_confirmada (10): só a mais recente conta.
# - material_pedido (2) e material_baixado (12): por material (`ref`), só o evento de maior ponto conta (entre iguais, o mais recente).
# - email_aberto (1, teto 1/dia UTC), email_clicado (4, teto 2/dia UTC): só contam com occurred_at >= HEATING_EMAIL_DESDE
#   e se o email não estiver descadastrado dos Expert Insights (`subscribers.unsubscribed = 1`).
# - site_visita (2, 1/dia), site_engajada (3, 1/dia), site_agende (50, 1/dia), site_contato (50, 1/dia).
# - email_entregue (0): usado só no filtro de robô, não pontua.
PONTOS_SITE: dict[str, float] = {
    "newsletter_confirmada": 10.0,
    "material_pedido": 2.0,
    "material_baixado": 12.0,
    "email_aberto": 1.0,
    "email_clicado": 4.0,
    "site_visita": 2.0,
    "site_engajada": 3.0,
    "site_agende": 50.0,
    "site_contato": 50.0,
    "email_entregue": 0.0,
}
TETO_DIA_SITE: dict[str, int] = {
    "email_aberto": 1,
    "email_clicado": 2,
    "site_visita": 1,
    "site_engajada": 1,
    "site_agende": 1,
    "site_contato": 1,
}
# Sinais passivos no site somados (já com meia-vida) param em 25, reduzidos na mesma proporção.
PASSIVOS_SITE = frozenset({"email_aberto", "site_visita", "site_engajada"})
TETO_PASSIVOS_SITE = 25.0

# Pesos e tetos do contexto `curso:<slug>`:
# - curso_cadastro (10): só o mais recente conta.
# - curso_aula (5, teto 1/dia UTC): sem teto somado (o teto de 20 da régua original existia para o curso não inflar a
#   nota do site; com o contexto próprio, aula é exatamente o que a nota do curso mede).
# - email_aberto (1, 1/dia UTC) e email_clicado (3, 2/dia UTC): mesma regra de robô e HEATING_EMAIL_DESDE;
#   inscrição `unsubscribed`, `bounced` ou com status desconhecido (Firestore indisponível ou inscrito ausente) perde os
#   sinais de email (bloqueio conservador).
# - passivos somados (`email_aberto`) param em 20.
PONTOS_CURSO: dict[str, float] = {
    "curso_cadastro": 10.0,
    "curso_aula": 5.0,
    "email_aberto": 1.0,
    "email_clicado": 3.0,
    "email_entregue": 0.0,
}
TETO_DIA_CURSO: dict[str, int] = {
    "curso_aula": 1,
    "email_aberto": 1,
    "email_clicado": 2,
}
PASSIVOS_CURSO = frozenset({"email_aberto"})
TETO_PASSIVOS_CURSO = 20.0

# Corte de nota e faixas: nota = min(100, round(soma)); frio < 15, morno 15..39, quente >= 40.
NOTA_MAXIMA = 100
FAIXA_MORNO = 15
FAIXA_QUENTE = 40
TOP_SINAIS = 3

ROTULOS_SINAIS: dict[str, str] = {
    "newsletter_confirmada": "Inscrição na newsletter",
    "material_pedido": "Pedido de material",
    "material_baixado": "Download de material",
    "email_aberto": "Abertura de email",
    "email_clicado": "Clique em email",
    "email_spam": "Reclamou de spam: não abordar",
    "site_visita": "Visita ao site",
    "site_engajada": "Visita engajada no site",
    "site_agende": "Clique em Agende",
    "site_contato": "Página de contato",
    "curso_cadastro": "Cadastro no curso",
    "curso_aula": "Aula concluída no curso",
}

CONV_SIGNAL_MAP: dict[str, str] = {
    "material_request": "material_pedido",
    "material_download": "material_baixado",
    "newsletter_confirm": "newsletter_confirmada",
}

# Catálogo de cursos: manter alinhado com HEATING_CURSO_LABELS em workers/admin/src/painel.ts e HEATING_CURSOS no Worker.
CURSOS: dict[str, dict[str, str]] = {
    "chatgpt-no-dia-a-dia": {
        "rotulo": "ChatGPT no dia a dia",
        "colecao_inscritos": "drip_subscribers",
        "colecao_usuarios": "users",
    },
}

CURSO_STATUS = OrderedDict([
    ("active", ("ATIVO", "Ativo", "green")),
    ("completed", ("CONCLUIDO", "Concluído", "blue")),
    ("unsubscribed", ("DESCADASTRADO", "Descadastrado", "gray")),
    ("bounced", ("EMAIL_INVALIDO", "Email inválido", "red")),
    ("churned", ("PARADO", "Parado", "yellow")),
    ("lead", ("SO_LOGIN", "Só login", "purple")),
])

CURSO_ID_RE = re.compile(r"[0-9A-Za-z_-]{1,64}")
MAX_EVENT_ID = 128

# Domínios de teste que não recebem nota (os eventos ficam no registro).
DOMINIOS_TESTE_EXATOS = frozenset({"example.com", "example.org", "example.net", "exemplo.com"})
DOMINIOS_TESTE_SUFIXOS = (".test", ".invalid", ".example")

# Prefixos HMAC (iguais aos do Worker / migração 0012).
OPTOUT_PREFIX = CS.HEATING_OPTOUT_PREFIX
CURSO_ESQUECIDO_PREFIX = "curso-esquecido-v1\n"

# Lock compartilhado com conversoes_sync.py: espera até 10 minutos.
LOCK_WAIT_S = 600.0
LOCK_POLL_S = 1.0

# Limite de 100 parâmetros por instrução do D1 (inclui 1 parâmetro `email_hmac` por linha no WHERE NOT EXISTS).
EVENT_CHUNK = 11       # 9 parâmetros * 11 = 99 <= 100
HEATING_CHUNK = 12     # 8 parâmetros * 12 = 96 <= 100
DELETE_CHUNK = 90      # <= 100

# ------------------------------------------------------------------ Metadados do Twenty

FAIXA_OPTIONS = [
    {"value": "FRIO", "label": "Frio", "color": "blue", "position": 0},
    {"value": "MORNO", "label": "Morno", "color": "orange", "position": 1},
    {"value": "QUENTE", "label": "Quente", "color": "red", "position": 2},
]

PERSON_HEATING_FIELDS = OrderedDict([
    ("heatingNota", {"type": "NUMBER", "label": "Heating: nota", "icon": "IconFlame"}),
    ("heatingFaixa", {"type": "SELECT", "label": "Heating: faixa", "icon": "IconTemperature", "options": FAIXA_OPTIONS}),
    ("heatingSinais", {"type": "TEXT", "label": "Heating: sinais", "icon": "IconListDetails"}),
    ("heatingCalculadoEm", {
        "type": "DATE_TIME",
        "label": "Heating: nota mudou em",
        "icon": "IconClock",
        "description": (
            "Data da última mudança da nota, faixa ou sinais no Twenty (não muda em rodadas sem alteração); "
            "a data do último cálculo diário fica em heating.computed_at no D1 (painel)."
        ),
    }),
])


def curso_option_value(slug: str) -> str:
    return slug.upper().replace("-", "_")


def _curso_options() -> list[dict]:
    return [
        {"value": curso_option_value(slug), "label": cfg["rotulo"], "color": CS.COLORS[i % len(CS.COLORS)], "position": i}
        for i, (slug, cfg) in enumerate(CURSOS.items())
    ]


ENROLL_OBJ = {
    "nameSingular": "courseEnrollment",
    "namePlural": "courseEnrollments",
    "labelSingular": "Inscrição em curso",
    "labelPlural": "Inscrições em cursos",
    "icon": "IconSchool",
    "isLabelSyncedWithName": False,
    "description": "Inscrições e engajamento nos cursos (épico #197). Gravado pelo heating_sync.py; não edite à mão.",
}
ENROLL_PLURAL = ENROLL_OBJ["namePlural"]
ENROLL_RELATION = {
    "name": "pessoa",
    "type": "RELATION",
    "label": "Pessoa",
    "icon": "IconUser",
    "inverse_label": "Inscrições em cursos",
    "inverse_icon": "IconSchool",
}
ENROLL_FIELDS = OrderedDict([
    ("inscricaoId", {"type": "TEXT", "label": "Id da inscrição", "icon": "IconHash", "isUnique": True}),
    ("curso", {"type": "SELECT", "label": "Curso", "icon": "IconSchool", "options": _curso_options()}),
    ("status", {"type": "SELECT", "label": "Status", "icon": "IconCircleCheck", "options": [
        {"value": v, "label": lbl, "color": col, "position": i}
        for i, (v, lbl, col) in enumerate(CURSO_STATUS.values())
    ]}),
    ("entrada", {"type": "DATE_TIME", "label": "Entrada", "icon": "IconCalendarEvent"}),
    ("aulasConcluidas", {"type": "NUMBER", "label": "Aulas concluídas", "icon": "IconCheckbox"}),
    ("ultimaAula", {"type": "DATE_TIME", "label": "Última aula", "icon": "IconClockCheck"}),
    ("nota", {"type": "NUMBER", "label": "Nota", "icon": "IconFlame"}),
    ("faixa", {"type": "SELECT", "label": "Faixa", "icon": "IconTemperature", "options": FAIXA_OPTIONS}),
    ("sinais", {"type": "TEXT", "label": "Sinais", "icon": "IconListDetails"}),
    ("calculadoEm", {
        "type": "DATE_TIME",
        "label": "Nota mudou em",
        "icon": "IconClock",
        "description": (
            "Data da última mudança da nota, faixa ou sinais no Twenty (não muda em rodadas sem alteração); "
            "a data do último cálculo diário fica em heating.computed_at no D1 (painel)."
        ),
    }),
])

RUN_COUNT_KEYS = (
    "eventos_conversao_novos",
    "eventos_curso_novos",
    "eventos_ga4_novos",
    "ga4_dias_lidos",
    "ga4_dia_sem_tabela",
    "ga4_sem_vinculo",
    "ga4_indisponivel",
    "ga4_remocao_pendente",
    "ga4_fila_processados",
    "bq_remocao_adiada",
    "bq_remocao_travada",
    "bq_sessoes_excedidas",
    "conversao_invalida",
    "curso_invalido",
    "curso_indisponivel",
    "oposicao_aplicada",
    "excluidos",
    "retencao_apagados",
    "notas_site",
    "notas_curso",
    "quentes_site",
    "pessoas_atualizadas",
    "pessoas_limpas",
    "pessoas_criadas",
    "inscricoes_criadas",
    "inscricoes_atualizadas",
    "sem_pessoa",
    "pessoa_na_lixeira",
)

# Export do GA4 no BigQuery (épico #197, corte 2).
GCP_PROJECT = CS.GCP_PROJECT
GA4_PROPERTY_ID = CS.GA4_PROPERTY_ID
GA4_STREAM_ID = CS.GA4_STREAM_ID
BQ_DATASET = CS.BQ_DATASET
BQ_LOCATION = CS.BQ_LOCATION
SITE_UID_RE = CS.SITE_UID_RE
GA4_PURGE_PENDENTE_MSG = CS.GA4_PURGE_PENDENTE_MSG
BQ_PURGE_PENDENTE_MSG = CS.BQ_PURGE_PENDENTE_MSG
GA4_FILA_SEM_CRED_MSG = CS.GA4_FILA_SEM_CRED_MSG
processar_fila_ga4 = CS.processar_fila_ga4

GA4_META_KEY = "heating_ga4_ultimo_dia"
GA4_MAX_DIAS = 14
GA4_REPROCESS_DIAS = 3
GA4_ENGAGED_MSEC_MIN = 60_000
GA4_DIA_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

FORGET_CURSO_AVISO = (
    "Aviso: a Pessoa no Twenty e os dados do curso no Firestore e no Identity Platform não foram apagados; "
    "o progresso e a conta do curso no Firestore e no Identity Platform saem pelo app do curso, se o pedido incluir isso."
)


class CursoIndisponivel(Exception):
    """Falha na leitura do Firestore ou ausência de HEATING_GCP_SA_FILE."""


class Ga4Indisponivel(Exception):
    """Falha na leitura do export do GA4 no BigQuery ou ausência de HEATING_GCP_SA_FILE."""


# ------------------------------------------------------------------ Helpers de HMAC, datas, exclusão e D1

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _check_key(key: str) -> None:
    if not isinstance(key, str) or len(key) < CS.MIN_FORGET_KEY:
        raise SyncError("FORGET_HMAC_KEY ausente ou curta demais")


def heating_optout_hmac(email: str, key: str) -> str:
    """HMAC-SHA256 em hex minúsculo de `heating-optout-v1\\n` + email normalizado, igual ao Worker."""
    return CS.heating_optout_hmac(email, key)


def curso_esquecido_hmac(context: str, email: str, key: str) -> str:
    """HMAC-SHA256 em hex minúsculo de `curso-esquecido-v1\\n` + context + `\\n` + email normalizado."""
    _check_key(key)
    norm = email.strip().lower() if isinstance(email, str) else ""
    if not CS.valid_email(norm):
        raise SyncError("email inválido na entrada")
    if not isinstance(context, str) or not context.startswith("curso:") or not CS._full(CS.SLUG, context[6:]):
        raise SyncError("contexto de curso inválido")
    msg = f"{CURSO_ESQUECIDO_PREFIX}{context}\n{norm}"
    return hmac.new(key.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).hexdigest()


def email_sha256_32(email: str) -> str:
    return hashlib.sha256(email.encode("utf-8")).hexdigest()[:32]


def inscricao_id(slug: str, email: str) -> str:
    return f"{slug}:{email_sha256_32(email)}"


def curso_iso_ms(value) -> str:
    """Normaliza string ISO ou datetime (ex.: Timestamp do Firestore) para ISO UTC com milissegundos; '' se inválido."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        d = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        try:
            d = d.astimezone(timezone.utc)
        except (ValueError, OverflowError):
            return ""
        return CS.stamp(d)
    if isinstance(value, str):
        return CS.iso_ms(value)
    return ""


def parse_iso_dt(iso_str: str) -> datetime | None:
    norm = CS.iso_ms(iso_str)
    if not norm:
        return None
    try:
        return datetime.fromisoformat(norm.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_exclude_env(raw: str | None) -> set[str]:
    if not raw:
        return set()
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


def is_excluded(email: str, excluded: set[str]) -> bool:
    """Diz se `email` fica fora das notas e da criação no CRM (`HEATING_EXCLUDE` e domínios de teste).

    Compara em minúsculas:
    - correspondência exata com qualquer item de `excluded`;
    - para itens de `excluded` sem `+` na parte local, exclui também aliases `local+tag@dominio`
      comparando pela forma canônica (parte local antes do primeiro `+`, mais `@` e o domínio),
      sem tratar pontos do Gmail;
    - um item de `excluded` que já contenha `+` na parte local vale apenas para aquele alias exato;
    - domínios de teste (`DOMINIOS_TESTE_EXATOS` e `DOMINIOS_TESTE_SUFIXOS`).
    """
    norm = email.strip().lower()
    excluded_norm = {e.strip().lower() for e in excluded if e and e.strip()}
    if norm in excluded_norm:
        return True
    at = norm.rfind("@")
    if at < 0:
        return False
    local, domain = norm[:at], norm[at + 1:]
    plus = local.find("+")
    if plus > 0 and f"{local[:plus]}@{domain}" in excluded_norm:
        return True
    if domain in DOMINIOS_TESTE_EXATOS:
        return True
    return any(domain.endswith(suf) for suf in DOMINIOS_TESTE_SUFIXOS)


def split_name(raw: str | None) -> dict[str, str] | None:
    """Divide o nome da conta Google em firstName (primeiro nome) e lastName (o resto), como o Twenty espera."""
    if not isinstance(raw, str):
        return None
    parts = raw.strip().split(None, 1)
    if not parts:
        return None
    return {"firstName": parts[0], "lastName": parts[1] if len(parts) > 1 else ""}


def _filled(v) -> bool:
    """Trata None e '' como vazios, preservando 0 (ex.: nota 0 de email_spam) e False."""
    return v is not None and v != ""


def formatar_sinais_texto(sinais: list[dict]) -> str:
    """Rótulos dos principais sinais em ordem, sem os pontos (os pontos ficam em `heating.signals` no D1)."""
    return "; ".join(s["rotulo"] for s in sinais)


def _insert_events_sql(part: list[dict], key: str) -> tuple[str, list]:
    """INSERT condicional em `engagement_events`: checa `heating_optout` na mesma instrução (<= 100 params)."""
    cols = ("id", "context", "email", "signal", "source", "ref", "occurred_at", "recorded_at")
    params: list = []
    selects: list[str] = []
    for idx_row, ev in enumerate(part):
        em_hmac = heating_optout_hmac(ev["email"], key)
        params.extend([
            ev["id"], ev["context"], ev["email"], ev["signal"],
            ev["source"], ev["ref"], ev["occurred_at"], ev["recorded_at"], em_hmac,
        ])
        if idx_row == 0:
            selects.append(
                "SELECT ? AS id, ? AS context, ? AS email, ? AS signal, ? AS source, "
                "? AS ref, ? AS occurred_at, ? AS recorded_at, ? AS email_hmac"
            )
        else:
            selects.append("SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?")
    sql = (
        f"INSERT INTO engagement_events ({', '.join(cols)}) "
        f"SELECT {', '.join('v.' + col for col in cols)} "
        f"FROM ({' UNION ALL '.join(selects)}) AS v "
        f"WHERE NOT EXISTS (SELECT 1 FROM heating_optout WHERE email_hmac = v.email_hmac) "
        f"ON CONFLICT(id) DO NOTHING"
    )
    return sql, params


def _insert_heating_sql(part: list[tuple], key: str) -> tuple[str, list]:
    """INSERT condicional em `heating`: checa `heating_optout` na mesma instrução (<= 100 params)."""
    cols = ("context", "email", "score", "band", "signals", "computed_at", "twenty_person_id")
    params: list = []
    selects: list[str] = []
    for idx_row, row in enumerate(part):
        em_hmac = heating_optout_hmac(row[1], key)
        params.extend([*row, em_hmac])
        if idx_row == 0:
            selects.append(
                "SELECT ? AS context, ? AS email, ? AS score, ? AS band, ? AS signals, "
                "? AS computed_at, ? AS twenty_person_id, ? AS email_hmac"
            )
        else:
            selects.append("SELECT ?, ?, ?, ?, ?, ?, ?, ?")
    sql = (
        f"INSERT INTO heating ({', '.join(cols)}) "
        f"SELECT {', '.join('v.' + col for col in cols)} "
        f"FROM ({' UNION ALL '.join(selects)}) AS v "
        f"WHERE NOT EXISTS (SELECT 1 FROM heating_optout WHERE email_hmac = v.email_hmac)"
    )
    return sql, params


def d1_batch(c: TS.Clients, stmts: list[tuple[str, list | None]]) -> list[list[dict]]:
    """Envia várias instruções no D1 via payload `batch` da API REST da Cloudflare.
    Risco aceito: a documentação da Cloudflare garante transação no `batch()` do binding em Workers, não na API REST;
    se a troca de `heating` falhar no meio sem rollback, a rodada seguinte refaz a tabela inteira."""
    e = c.env
    url = (f"https://api.cloudflare.com/client/v4/accounts/{e['CLOUDFLARE_ACCOUNT_ID']}/d1/database/"
           f"{e['D1_DATABASE_ID']}/query")
    batch = [{"sql": sql} if params is None else {"sql": sql, "params": params} for sql, params in stmts]
    st, data = c.t(
        "POST",
        url,
        {"Authorization": f"Bearer {e['CLOUDFLARE_API_TOKEN']}", "Content-Type": "application/json",
         "User-Agent": "guifav-heating-sync"},
        json.dumps({"batch": batch}),
    )
    if st != 200:
        raise SyncError(f"D1 respondeu {st}")
    if not isinstance(data, dict) or not data.get("success"):
        raise SyncError("D1 respondeu erro no batch")
    res = data.get("result")
    if not isinstance(res, list) or len(res) != len(stmts):
        raise SyncError("D1 respondeu formato inesperado no batch")
    out: list[list[dict]] = []
    for r in res:
        if not isinstance(r, dict) or ("success" in r and not r["success"]):
            raise SyncError("D1 falhou em instrução do batch")
        rows = r.get("results")
        if rows is None:
            out.append([])
        elif isinstance(rows, list):
            out.append(rows)
        else:
            raise SyncError("D1 respondeu formato inesperado no batch")
    return out


# ------------------------------------------------------------------ Leitura do curso (Firestore opcional)

def ler_curso_firestore(slug: str, sa_file: str | None = None) -> list[dict]:
    """Lê inscritos e progresso de um curso no Firestore do projeto `mindapps` (import opcional da biblioteca)."""
    cfg = CURSOS.get(slug)
    if not cfg:
        raise SyncError(f"curso desconhecido: {slug}")
    path = sa_file if sa_file is not None else os.environ.get("HEATING_GCP_SA_FILE", "").strip()
    if not path:
        raise CursoIndisponivel("HEATING_GCP_SA_FILE ausente")
    try:
        from google.cloud import firestore  # type: ignore[import-not-found]
        from google.oauth2 import service_account  # type: ignore[import-not-found]
    except ImportError as e:
        raise CursoIndisponivel("biblioteca do Firestore ausente") from e
    try:
        creds = service_account.Credentials.from_service_account_file(path)
        db = firestore.Client(project="mindapps", credentials=creds)
        out: list[dict] = []
        for doc in db.collection(cfg["colecao_inscritos"]).stream():
            d = doc.to_dict() or {}
            uid = d.get("googleUid")
            aulas: list[dict] = []
            if isinstance(uid, str) and CS._full(CURSO_ID_RE, uid):
                prog = db.collection(cfg["colecao_usuarios"]).document(uid).collection("progress").stream()
                for pdoc in prog:
                    pd = pdoc.to_dict() or {}
                    aulas.append({"dayId": pdoc.id, "completed": pd.get("completed"), "updatedAt": pd.get("updatedAt")})
            out.append({
                "email": d.get("email"),
                "status": d.get("status"),
                "source": d.get("source"),
                "googleUid": uid,
                "name": d.get("name"),
                "createdAt": d.get("createdAt"),
                "startISODate": d.get("startISODate"),
                "aulas": aulas,
            })
        return out
    except CursoIndisponivel:
        raise
    except Exception as e:  # noqa: BLE001
        # Erros de leitura/autenticação/rede/JSON do Firestore deixam o curso indisponível nesta rodada.
        name = type(e).__name__
        mod = type(e).__module__ or ""
        if isinstance(e, (OSError, ValueError)) or mod.startswith(("google.", "grpc")):
            raise CursoIndisponivel(f"leitura do Firestore falhou ({name})") from e
        raise


# ------------------------------------------------------------------ Leitura do export do GA4 (BigQuery opcional)

def parse_iso_date(raw: str | None) -> date | None:
    """Valida data no formato estrito AAAA-MM-DD; devolve None se inválida."""
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not CS._full(GA4_DIA_RE, s):
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def ga4_ts_to_iso_ms(ts_us) -> str:
    """Converte event_timestamp do GA4 (microssegundos UTC inteiros) para ISO UTC com milissegundos; '' se inválido."""
    if not isinstance(ts_us, int) or isinstance(ts_us, bool) or ts_us < 0:
        return ""
    secs, rem_us = divmod(ts_us, 1_000_000)
    try:
        dt = datetime.fromtimestamp(secs, tz=timezone.utc).replace(microsecond=rem_us)
    except (ValueError, OverflowError, OSError):
        return ""
    return CS.stamp(dt)


def uid_sha256_16(uid: str) -> str:
    return hashlib.sha256(uid.encode("utf-8")).hexdigest()[:16]


def ga4_day_query_sql(dia: date) -> str:
    """Monta a consulta diária sobre a tabela fechada `events_YYYYMMDD` (nunca wildcard nem intraday),
    com filtro de stream, consentimento (`analytics_storage = 'Yes'`) e ligação retroativa só dentro da mesma sessão."""
    if not isinstance(dia, date):
        raise SyncError("data inválida para consulta do GA4")
    yyyymmdd = dia.strftime("%Y%m%d")
    table_ref = f"`{GCP_PROJECT}.{BQ_DATASET}.events_{yyyymmdd}`"
    return (
        f"WITH base AS (\n"
        f"  SELECT\n"
        f"    user_id,\n"
        f"    user_pseudo_id,\n"
        f"    (SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'ga_session_id') AS ga_session_id,\n"
        f"    event_name,\n"
        f"    event_timestamp,\n"
        f"    COALESCE(\n"
        f"      (SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'engagement_time_msec'),\n"
        f"      0\n"
        f"    ) AS engagement_time_msec\n"
        f"  FROM {table_ref}\n"
        f"  WHERE stream_id = @stream_id\n"
        f"    AND privacy_info.analytics_storage = 'Yes'\n"
        f"),\n"
        f"sessoes_uid AS (\n"
        f"  SELECT\n"
        f"    user_pseudo_id,\n"
        f"    ga_session_id,\n"
        f"    ANY_VALUE(user_id) AS session_user_id\n"
        f"  FROM base\n"
        f"  WHERE user_id IS NOT NULL\n"
        f"    AND user_id != ''\n"
        f"    AND user_pseudo_id IS NOT NULL\n"
        f"    AND ga_session_id IS NOT NULL\n"
        f"  GROUP BY user_pseudo_id, ga_session_id\n"
        f"  HAVING COUNT(DISTINCT user_id) = 1\n"
        f"),\n"
        f"ligados AS (\n"
        f"  SELECT\n"
        f"    COALESCE(NULLIF(b.user_id, ''), s.session_user_id) AS uid,\n"
        f"    b.user_pseudo_id,\n"
        f"    b.ga_session_id,\n"
        f"    b.event_name,\n"
        f"    b.event_timestamp,\n"
        f"    b.engagement_time_msec\n"
        f"  FROM base AS b\n"
        f"  LEFT JOIN sessoes_uid AS s\n"
        f"    ON b.user_pseudo_id = s.user_pseudo_id\n"
        f"   AND b.ga_session_id = s.ga_session_id\n"
        f")\n"
        f"SELECT\n"
        f"  uid AS user_id,\n"
        f"  COUNT(DISTINCT CONCAT(COALESCE(user_pseudo_id, ''), ':', COALESCE(CAST(ga_session_id AS STRING), ''))) AS sessoes,\n"
        f"  COUNTIF(event_name = 'page_view') AS page_views,\n"
        f"  SUM(engagement_time_msec) AS engagement_time_msec,\n"
        f"  COUNTIF(event_name = 'scroll') AS scrolls,\n"
        f"  COUNTIF(event_name = 'contact_click') AS cliques_contato,\n"
        f"  COUNTIF(event_name = 'schedule_click') AS cliques_agenda,\n"
        f"  MIN(event_timestamp) AS min_ts_visita,\n"
        f"  MIN(IF(engagement_time_msec > 0 OR event_name = 'scroll', event_timestamp, NULL)) AS min_ts_engajamento,\n"
        f"  MIN(IF(event_name = 'contact_click', event_timestamp, NULL)) AS min_ts_contato,\n"
        f"  MIN(IF(event_name = 'schedule_click', event_timestamp, NULL)) AS min_ts_agenda\n"
        f"FROM ligados\n"
        f"WHERE uid IS NOT NULL AND uid != ''\n"
        f"GROUP BY uid"
    )


def ler_ga4_dia(dia: date, sa_file: str | None = None) -> list[dict] | None:
    """Lê sinais agregados por `user_id` na tabela fechada `events_YYYYMMDD` do export do GA4 no BigQuery.
    Devolve None se a tabela do dia ainda não existe; levanta Ga4Indisponivel se o BigQuery falhar."""
    if not isinstance(dia, date):
        raise SyncError("data inválida para leitura do GA4")
    path = sa_file if sa_file is not None else os.environ.get("HEATING_GCP_SA_FILE", "").strip()
    if not path:
        raise Ga4Indisponivel("HEATING_GCP_SA_FILE ausente")
    try:
        from google.cloud import bigquery  # type: ignore[import-not-found]
        from google.oauth2 import service_account  # type: ignore[import-not-found]
    except ImportError as e:
        raise Ga4Indisponivel("biblioteca do BigQuery ausente") from e

    table_id = f"{GCP_PROJECT}.{BQ_DATASET}.events_{dia.strftime('%Y%m%d')}"
    try:
        creds = service_account.Credentials.from_service_account_file(path)
        client = bigquery.Client(project=GCP_PROJECT, credentials=creds)
        try:
            table = client.get_table(table_id)
        except Exception as e:  # noqa: BLE001
            if CS._is_not_found(e):
                return None
            raise
        sql = ga4_day_query_sql(dia)
        cfg = CS._bq_cfg(bigquery, query_parameters=[bigquery.ScalarQueryParameter("stream_id", "STRING", GA4_STREAM_ID)])
        location = getattr(table, "location", None) or BQ_LOCATION
        rows = client.query(sql, job_config=cfg, location=location).result()
        out: list[dict] = []
        for row in rows:
            out.append({
                "user_id": row["user_id"],
                "sessoes": row["sessoes"],
                "page_views": row["page_views"],
                "engagement_time_msec": row["engagement_time_msec"],
                "scrolls": row["scrolls"],
                "cliques_contato": row["cliques_contato"],
                "cliques_agenda": row["cliques_agenda"],
                "min_ts_visita": row["min_ts_visita"],
                "min_ts_engajamento": row["min_ts_engajamento"],
                "min_ts_contato": row["min_ts_contato"],
                "min_ts_agenda": row["min_ts_agenda"],
            })
        return out
    except Ga4Indisponivel:
        raise
    except Exception as e:  # noqa: BLE001
        if CS._is_google_err(e):
            raise Ga4Indisponivel(f"leitura do BigQuery falhou ({type(e).__name__})") from e
        raise


# ------------------------------------------------------------------ Metadados e esquema no Twenty

def read_metadata(c: TS.Clients) -> dict:
    q = ("{ objects(paging:{first:200}){ edges{ node{ id nameSingular namePlural "
         "fields(paging:{first:500}){ edges{ node{ id name type options } } } } } } }")
    data = c.twenty("POST", "/metadata", {"query": q}, op="ler metadados")
    try:
        nodes = {e["node"]["nameSingular"]: e["node"] for e in data["data"]["objects"]["edges"]}
        person = nodes["person"]
        enroll = nodes.get(ENROLL_OBJ["nameSingular"])

        def fields(o):
            return {f["node"]["name"]: f["node"] for f in o["fields"]["edges"]} if o else {}

        meta = {
            "person_id": person["id"],
            "person": fields(person),
            "enroll_id": enroll["id"] if enroll else None,
            "enroll": fields(enroll),
        }
    except (KeyError, TypeError) as e:
        raise SyncError("metadados do Twenty em formato inesperado") from e
    if enroll and enroll.get("namePlural") != ENROLL_PLURAL:
        raise SyncError("objeto courseEnrollment com plural inesperado")
    for spec, have in ((PERSON_HEATING_FIELDS, meta["person"]), (ENROLL_FIELDS, meta["enroll"])):
        for k, f in spec.items():
            if k in have and have[k]["type"] != f["type"]:
                raise SyncError(f"campo {k} existe com outro tipo")
            if k in have and f["type"] == "SELECT" and k != "curso":
                missing = {o["value"] for o in f["options"]} - set(CS._option_values(have[k]))
                if missing:
                    raise SyncError(f"campo {k} existe sem as opções {', '.join(sorted(missing))}")
    if ENROLL_RELATION["name"] in meta["enroll"] and meta["enroll"][ENROLL_RELATION["name"]]["type"] != "RELATION":
        raise SyncError("campo pessoa existe com outro tipo")
    return meta


def ensure_schema(c: TS.Clients, meta: dict, *, need_person: bool, need_enroll: bool) -> None:
    fm = "mutation($input: CreateOneFieldMetadataInput!){ createOneField(input:$input){ id name type options } }"

    def create_field(object_id: str, name: str, spec: dict) -> dict:
        f = {"objectMetadataId": object_id, "name": name, "isNullable": True,
             **{k: v for k, v in spec.items() if k != "options"}}
        if spec["type"] == "SELECT":
            f["options"] = spec["options"]
        data = c.twenty("POST", "/metadata", {"query": fm, "variables": {"input": {"field": f}}}, op="criar campo")
        return data["data"]["createOneField"]

    if need_person:
        for name, spec in PERSON_HEATING_FIELDS.items():
            if name not in meta["person"]:
                meta["person"][name] = create_field(meta["person_id"], name, spec)

    if need_enroll:
        if not meta["enroll_id"]:
            m = "mutation($input: CreateOneObjectInput!){ createOneObject(input:$input){ id } }"
            data = c.twenty("POST", "/metadata", {"query": m, "variables": {"input": {"object": ENROLL_OBJ}}}, op="criar objeto")
            meta["enroll_id"] = data["data"]["createOneObject"]["id"]
        for name, spec in ENROLL_FIELDS.items():
            if name not in meta["enroll"]:
                meta["enroll"][name] = create_field(meta["enroll_id"], name, spec)
        if ENROLL_RELATION["name"] not in meta["enroll"]:
            f = {
                "objectMetadataId": meta["enroll_id"],
                "name": ENROLL_RELATION["name"],
                "type": "RELATION",
                "label": ENROLL_RELATION["label"],
                "icon": ENROLL_RELATION["icon"],
                "isNullable": True,
                "relationCreationPayload": {
                    "targetObjectMetadataId": meta["person_id"],
                    "targetFieldLabel": ENROLL_RELATION["inverse_label"],
                    "targetFieldIcon": ENROLL_RELATION["inverse_icon"],
                    "type": "MANY_TO_ONE",
                },
            }
            data = c.twenty("POST", "/metadata", {"query": fm, "variables": {"input": {"field": f}}}, op="criar relação")
            meta["enroll"][ENROLL_RELATION["name"]] = data["data"]["createOneField"]
        # Se um curso novo entrou no catálogo depois de `curso` existir no CRM, acrescenta a opção preservando as antigas.
        curso_field = meta["enroll"]["curso"]
        have_vals = set(CS._option_values(curso_field))
        add = [
            {"value": curso_option_value(slug), "label": cfg["rotulo"],
             "color": CS.COLORS[(len(have_vals) + i) % len(CS.COLORS)], "position": len(have_vals) + i}
            for i, (slug, cfg) in enumerate((s, cf) for s, cf in CURSOS.items() if curso_option_value(s) not in have_vals)
        ]
        if add:
            um = "mutation($input: UpdateOneFieldMetadataInput!){ updateOneField(input:$input){ id options } }"
            options = list(curso_field.get("options") or []) + add
            data = c.twenty(
                "POST",
                "/metadata",
                {"query": um, "variables": {"input": {"id": curso_field["id"], "update": {"options": options}}}},
                op="criar opção",
            )
            curso_field["options"] = data["data"]["updateOneField"]["options"]


# ------------------------------------------------------------------ Régua de pontuação

def _descartar_cliques_robo(events: list[dict]) -> set[str]:
    """Devolve os ids dos eventos `email_clicado` descartados como clique de robô (entrega <= 60 s ou rajada <= 5 s)."""
    entregas_por_ref: dict[str, list[datetime]] = {}
    cliques_por_ref: dict[str, list[tuple[str, datetime]]] = {}
    for ev in events:
        ref = ev.get("ref")
        if not ref:
            continue
        if ev["signal"] == "email_entregue":
            entregas_por_ref.setdefault(ref, []).append(ev["_dt"])
        elif ev["signal"] == "email_clicado":
            cliques_por_ref.setdefault(ref, []).append((ev["id"], ev["_dt"]))
    descartados: set[str] = set()
    for ref, cliques in cliques_por_ref.items():
        entregas = entregas_por_ref.get(ref) or []
        for cid, cdt in cliques:
            # Clique até 60 s depois do email_entregue do mesmo email e mesmo ref.
            if any(0.0 <= (cdt - ddt).total_seconds() <= ROBO_ENTREGA_S for ddt in entregas):
                descartados.add(cid)
        # Todos os cliques do mesmo email e ref que tenham outro clique a até 5 s.
        if len(cliques) > 1:
            cliques_ord = sorted(cliques, key=lambda x: x[1])
            for i in range(len(cliques_ord) - 1):
                cid_a, dt_a = cliques_ord[i]
                cid_b, dt_b = cliques_ord[i + 1]
                if (dt_b - dt_a).total_seconds() <= ROBO_RAJADA_S:
                    descartados.add(cid_a)
                    descartados.add(cid_b)
    return descartados


def calcular_heating_contexto(
    context: str,
    events: list[dict],
    now_dt: datetime,
    *,
    email_desde: str | None = None,
    email_bloqueado: bool = False,
) -> dict | None:
    """Calcula a nota, faixa e top 3 sinais de um (context, email) em relação ao instante `now_dt`.
    Devolve None se não houver nenhum evento válido no contexto."""
    is_site = context == "site"
    pontos_map = PONTOS_SITE if is_site else PONTOS_CURSO
    teto_dia = TETO_DIA_SITE if is_site else TETO_DIA_CURSO
    passivos = PASSIVOS_SITE if is_site else PASSIVOS_CURSO
    teto_passivos = TETO_PASSIVOS_SITE if is_site else TETO_PASSIVOS_CURSO
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)
    else:
        now_dt = now_dt.astimezone(timezone.utc)
    email_desde_dt = parse_iso_dt(email_desde) if email_desde else None

    # 1) Filtra eventos do contexto com data válida e não futuros (occurred_at <= T).
    valid_ts: list[dict] = []
    for ev in events:
        if ev.get("context") != context:
            continue
        dt = parse_iso_dt(ev.get("occurred_at") or "")
        if dt is None or dt > now_dt:
            continue
        idade = (now_dt - dt).total_seconds() / 86400.0
        valid_ts.append({**ev, "_dt": dt, "_idade": idade})

    if not valid_ts:
        return None

    # 2) Qualquer email_spam no contexto nos últimos 365 dias: nota 0, frio, sinal fixo.
    if any(ev["signal"] == "email_spam" and ev["_idade"] <= float(RETENCAO_DIAS) for ev in valid_ts):
        sinais = [{"sinal": "email_spam", "rotulo": ROTULOS_SINAIS["email_spam"], "pontos": 0}]
        return {
            "score": 0,
            "band": "frio",
            "signals_list": sinais,
            "signals": json.dumps(sinais, ensure_ascii=False, separators=(",", ":")),
        }

    # 3) Cliques de robô (usa todos os email_entregue e email_clicado <= T do contexto).
    robo_ids = _descartar_cliques_robo(valid_ts)

    # 4) Seleciona eventos dentro da janela de 180 dias que pontuam no contexto.
    elegiveis: list[dict] = []
    for ev in valid_ts:
        sig = ev["signal"]
        if sig not in pontos_map or pontos_map[sig] <= 0.0:
            continue
        if ev["_idade"] > JANELA_DIAS:
            continue
        if sig in ("email_aberto", "email_clicado"):
            if email_bloqueado or email_desde_dt is None:
                continue
            if ev["_dt"] < email_desde_dt:
                continue
            if sig == "email_clicado" and ev["id"] in robo_ids:
                continue
        peso = pontos_map[sig]
        ev_pts = peso * (0.5 ** (ev["_idade"] / MEIA_VIDA_DIAS))
        elegiveis.append({**ev, "_pts": ev_pts})

    if not elegiveis:
        return None

    # 5) Aplica as regras de deduplicação/teto por tipo de sinal.
    contrib: dict[str, float] = {}

    def add_contrib(sig: str, pts: float) -> None:
        contrib[sig] = contrib.get(sig, 0.0) + pts

    if is_site:
        # newsletter_confirmada: só a mais recente conta.
        nls = [e for e in elegiveis if e["signal"] == "newsletter_confirmada"]
        if nls:
            best = max(nls, key=lambda e: (e["_dt"], e["id"]))
            add_contrib("newsletter_confirmada", best["_pts"])

        # material_pedido e material_baixado: por material (`ref`), só o evento de maior ponto conta (entre iguais, o mais recente).
        mats_by_ref: dict[str, list[dict]] = {}
        for e in elegiveis:
            if e["signal"] in ("material_pedido", "material_baixado"):
                mats_by_ref.setdefault(e.get("ref") or "", []).append(e)
        for evs in mats_by_ref.values():
            best = max(evs, key=lambda e: (e["_pts"], e["_dt"], e["id"]))
            add_contrib(best["signal"], best["_pts"])
    else:
        # curso_cadastro: só o mais recente conta.
        cads = [e for e in elegiveis if e["signal"] == "curso_cadastro"]
        if cads:
            best = max(cads, key=lambda e: (e["_dt"], e["id"]))
            add_contrib("curso_cadastro", best["_pts"])

    # Demais sinais com teto por dia UTC: escolhe os N mais recentes (maior ponto decaído) daquele dia UTC.
    por_sig_dia: dict[tuple[str, str], list[dict]] = {}
    for e in elegiveis:
        sig = e["signal"]
        if sig in teto_dia:
            dia_utc = e["_dt"].strftime("%Y-%m-%d")
            por_sig_dia.setdefault((sig, dia_utc), []).append(e)
    for (sig, _dia), evs in por_sig_dia.items():
        limite = teto_dia[sig]
        evs_ord = sorted(evs, key=lambda e: (e["_dt"], e["id"]), reverse=True)[:limite]
        for e in evs_ord:
            add_contrib(sig, e["_pts"])

    if not contrib:
        return None

    # 6) Teto dos sinais passivos (redução proporcional).
    soma_passivos = sum(contrib.get(s, 0.0) for s in passivos)
    if soma_passivos > teto_passivos:
        fator = teto_passivos / soma_passivos
        for s in passivos:
            if s in contrib:
                contrib[s] *= fator

    soma = sum(contrib.values())
    nota = min(NOTA_MAXIMA, int(round(soma)))
    faixa = "quente" if nota >= FAIXA_QUENTE else ("morno" if nota >= FAIXA_MORNO else "frio")

    top = sorted(contrib.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_SINAIS]
    sinais = [
        {"sinal": sig, "rotulo": ROTULOS_SINAIS.get(sig, sig), "pontos": round(pts, 1)}
        for sig, pts in top
    ]
    return {
        "score": nota,
        "band": faixa,
        "signals_list": sinais,
        "signals": json.dumps(sinais, ensure_ascii=False, separators=(",", ":")),
    }


# ------------------------------------------------------------------ Leitura e parsing de curso

def _parse_curso_docs(slug: str, raw_docs: list[dict], counts: Counter) -> dict[str, dict]:
    """Valida documentos de `drip_subscribers` + `progress`, devolvendo estado consolidado por email normalizado.

    O id de evento `curso_aula` inclui os 16 primeiros hex do SHA-256 do email normalizado
    (`cu:<slug>:aula:<googleUid>:<sha16>:<dayId>`) para evitar colisão entre documentos com o mesmo `googleUid` e
    emails diferentes. Como ainda não há evento de curso gravado em produção, não precisa de migração de ids.
    """
    by_email: dict[str, dict] = {}
    for d in raw_docs:
        if not isinstance(d, dict):
            counts["curso_invalido"] += 1
            continue
        raw_email = d.get("email")
        email = raw_email.strip().lower() if isinstance(raw_email, str) else ""
        if not CS.valid_email(email):
            counts["curso_invalido"] += 1
            continue
        status = d.get("status")
        if status not in CURSO_STATUS:
            counts["curso_invalido"] += 1
            continue
        created_iso = curso_iso_ms(d.get("createdAt"))
        if not created_iso:
            if d.get("createdAt") is None and d.get("startISODate") is not None:
                created_iso = curso_iso_ms(d.get("startISODate"))
            if not created_iso:
                counts["curso_invalido"] += 1
                continue
        raw_uid = d.get("googleUid")
        uid: str | None = None
        if raw_uid is not None and raw_uid != "":
            if not isinstance(raw_uid, str) or not CS._full(CURSO_ID_RE, raw_uid):
                counts["curso_invalido"] += 1
                continue
            uid = raw_uid
        raw_name = d.get("name")
        name = raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else None

        aulas_ok: dict[str, tuple[str, str]] = {}  # dayId -> (ev_id, updated_at_iso)
        if uid:
            email_sha16 = email_sha256_32(email)[:16]
            for aula in d.get("aulas") or []:
                if not isinstance(aula, dict):
                    counts["curso_invalido"] += 1
                    continue
                if aula.get("completed") is not True:
                    continue
                day_id = aula.get("dayId")
                if not isinstance(day_id, str) or not CS._full(CURSO_ID_RE, day_id):
                    counts["curso_invalido"] += 1
                    continue
                ev_id = f"cu:{slug}:aula:{uid}:{email_sha16}:{day_id}"
                if len(ev_id) > MAX_EVENT_ID:
                    counts["curso_invalido"] += 1
                    continue
                upd_iso = curso_iso_ms(aula.get("updatedAt"))
                if not upd_iso:
                    counts["curso_invalido"] += 1
                    continue
                prev = aulas_ok.get(day_id)
                if prev is None or upd_iso > prev[1]:
                    aulas_ok[day_id] = (ev_id, upd_iso)

        prev_rec = by_email.get(email)
        if prev_rec is None:
            by_email[email] = {
                "slug": slug,
                "context": f"curso:{slug}",
                "email": email,
                "status": status,
                "entrada": created_iso,
                "name": name,
                "googleUid": uid,
                "aulas": aulas_ok,
            }
        else:
            # Mesmo email duplicado na coleção: mantém a entrada mais recente, nome se faltar e união das aulas.
            if created_iso >= prev_rec["entrada"]:
                prev_rec["status"] = status
                prev_rec["entrada"] = created_iso
                if name:
                    prev_rec["name"] = name
                if uid:
                    prev_rec["googleUid"] = uid
            elif not prev_rec["name"] and name:
                prev_rec["name"] = name
            for day_id, pair in aulas_ok.items():
                old_pair = prev_rec["aulas"].get(day_id)
                if old_pair is None or pair[1] > old_pair[1]:
                    prev_rec["aulas"][day_id] = pair
    return by_email


# ------------------------------------------------------------------ Rodada principal

def _person_heating_patch(p: dict, want: dict | None, now_iso: str) -> dict:
    """Devolve o PATCH necessário para os quatro campos heating* na Person, ou {} se nada mudou."""
    if want is None:
        if any(_filled(p.get(k)) for k in PERSON_HEATING_FIELDS):
            return {k: None for k in PERSON_HEATING_FIELDS}
        return {}
    sinais_txt = formatar_sinais_texto(want["signals_list"])
    faixa = want["band"].upper()
    nota = want["score"]
    same_nota = p.get("heatingNota") == nota
    same_faixa = p.get("heatingFaixa") == faixa
    same_sinais = (p.get("heatingSinais") or "") == sinais_txt
    has_calc = bool(CS.iso_ms(p.get("heatingCalculadoEm") or ""))
    if same_nota and same_faixa and same_sinais and has_calc:
        return {}
    return {
        "heatingNota": nota,
        "heatingFaixa": faixa,
        "heatingSinais": sinais_txt,
        "heatingCalculadoEm": now_iso,
    }


def _enroll_desired(insc: dict, pid: str, h: dict | None, now_iso: str) -> dict:
    aulas = insc["aulas"]
    ultima = max((u for _, u in aulas.values()), default=None)
    status_code = CURSO_STATUS[insc["status"]][0]
    body = {
        "name": CURSOS[insc["slug"]]["rotulo"],
        "inscricaoId": inscricao_id(insc["slug"], insc["email"]),
        "curso": curso_option_value(insc["slug"]),
        "status": status_code,
        "entrada": insc["entrada"],
        "aulasConcluidas": len(aulas),
        "ultimaAula": ultima,
        "pessoaId": pid,
    }
    if h is None:
        body.update({"nota": None, "faixa": None, "sinais": None, "calculadoEm": None})
    else:
        body.update({
            "nota": h["score"],
            "faixa": h["band"].upper(),
            "sinais": formatar_sinais_texto(h["signals_list"]),
            "calculadoEm": now_iso,
        })
    return body


def _enroll_patch(existing: dict, desired: dict) -> dict:
    """Devolve apenas os campos alterados da Inscrição; `calculadoEm` só muda quando nota, faixa ou sinais mudam (ou falta/sobra)."""
    meta_keys = ("curso", "status", "entrada", "aulasConcluidas", "ultimaAula", "pessoaId")
    score_keys = ("nota", "faixa", "sinais")
    out: dict = {}
    for k in (*meta_keys, *score_keys):
        have = existing.get(k)
        want = desired[k]
        if k in ("entrada", "ultimaAula"):
            have = CS.iso_ms(have or "") or None
        elif not _filled(have):
            have = None
        if not _filled(want):
            want = None
        if have != want:
            out[k] = want
    score_changed = any(k in out for k in score_keys)
    have_calc = CS.iso_ms(existing.get("calculadoEm") or "") or None
    if desired["nota"] is None:
        if have_calc is not None:
            out["calculadoEm"] = None
    elif score_changed or have_calc is None:
        out["calculadoEm"] = desired["calculadoEm"]
    return out


def _enroll_score_patch(existing: dict, h: dict | None, now_iso: str) -> dict:
    """Devolve o PATCH apenas para nota, faixa, sinais e calculadoEm da Inscrição, ou {} se nada mudou."""
    if h is None:
        if any(_filled(existing.get(k)) for k in ("nota", "faixa", "sinais", "calculadoEm")):
            return {"nota": None, "faixa": None, "sinais": None, "calculadoEm": None}
        return {}
    want = {
        "nota": h["score"],
        "faixa": h["band"].upper(),
        "sinais": formatar_sinais_texto(h["signals_list"]),
    }
    out: dict = {}
    for k, w in want.items():
        have = existing.get(k)
        if not _filled(have):
            have = None
        w_norm = w if _filled(w) else None
        if have != w_norm:
            out[k] = w_norm
    have_calc = CS.iso_ms(existing.get("calculadoEm") or "") or None
    if out or have_calc is None:
        out["calculadoEm"] = now_iso
    return out


def run(
    c: TS.Clients,
    *,
    apply: bool = False,
    out=None,
    now_dt: datetime | None = None,
    ler_curso=None,
    ler_ga4_dia=None,
    forget_key: str | None = None,
    exclude_emails: set[str] | None = None,
    email_desde: str | None = ...,
    ga4_desde: str | None = ...,
    ga4_delete=None,
    bq_delete=None,
) -> Counter:
    out = out or sys.stdout
    key = c.env.get("FORGET_HMAC_KEY", "") if forget_key is None else forget_key
    _check_key(key)
    now_dt = now_dt or _now()
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)
    else:
        now_dt = now_dt.astimezone(timezone.utc)
    now_iso = CS.stamp(now_dt)
    retention_cutoff = CS.stamp(now_dt - timedelta(days=RETENCAO_DIAS))

    excluded_set = parse_exclude_env(c.env.get("HEATING_EXCLUDE")) if exclude_emails is None else {e.strip().lower() for e in exclude_emails}

    if email_desde is ...:
        raw_desde = (c.env.get("HEATING_EMAIL_DESDE") or "").strip()
        if raw_desde:
            parsed_desde = CS.iso_ms(raw_desde)
            if not parsed_desde:
                raise SyncError("HEATING_EMAIL_DESDE inválida")
            email_desde_iso: str | None = parsed_desde
        else:
            email_desde_iso = None
    elif email_desde:
        parsed_desde = CS.iso_ms(email_desde)
        if not parsed_desde:
            raise SyncError("HEATING_EMAIL_DESDE inválida")
        email_desde_iso = parsed_desde
    else:
        email_desde_iso = None

    if ga4_desde is ...:
        raw_ga4_desde = (c.env.get("HEATING_GA4_DESDE") or "").strip()
        if raw_ga4_desde:
            ga4_desde_date = parse_iso_date(raw_ga4_desde)
            if ga4_desde_date is None:
                raise SyncError("HEATING_GA4_DESDE inválida")
        else:
            ga4_desde_date = None
    elif ga4_desde:
        ga4_desde_date = parse_iso_date(ga4_desde)
        if ga4_desde_date is None:
            raise SyncError("HEATING_GA4_DESDE inválida")
    else:
        ga4_desde_date = None

    counts: Counter = Counter({k: 0 for k in RUN_COUNT_KEYS})

    # ------------------------------------------------------------------ 1) Leituras e validações (antes de qualquer escrita)
    raw_convs = c.d1(
        "SELECT id, email, action, material, interests, occurred_at, verified, ga_client_id, request_id, file, "
        "twenty_person_id, "
        + ", ".join(CS.ORIGEM_COLS)
        + " FROM conversions ORDER BY occurred_at, id"
    )
    existing_events = c.d1("SELECT id, context, email, signal, source, ref, occurred_at, recorded_at FROM engagement_events")
    existing_heating_rows = c.d1("SELECT context, email, score, band, signals, computed_at, twenty_person_id FROM heating")
    optout_hmacs = {r["email_hmac"] for r in c.d1("SELECT email_hmac FROM heating_optout")}
    forgotten_courses = {(r["context"], r["email_hmac"]) for r in c.d1("SELECT context, email_hmac FROM curso_esquecido")}
    unsub_site = {
        r["email"].strip().lower()
        for r in c.d1("SELECT email FROM subscribers WHERE unsubscribed = 1")
        if isinstance(r.get("email"), str)
    }
    due_purges = c.d1(
        "SELECT uid, ga4_pendente, sessoes, bq_tentativas, proxima_em, criado_em "
        "FROM ga4_purge_queue WHERE proxima_em <= ? ORDER BY proxima_em, uid",
        [now_iso],
    )

    # Marcador do GA4 só é lido quando HEATING_GA4_DESDE está definida (ausente = fonte desligada).
    # O GA4 atualiza a tabela diária por até 3 dias: cada rodada relê os 3 dias até o marcador além dos novos.
    ga4_days_eligible: list[date] = []
    ga4_marker_before: date | None = None
    if ga4_desde_date is not None:
        meta_rows = c.d1("SELECT v FROM meta WHERE k = ?", [GA4_META_KEY])
        if meta_rows:
            ga4_marker_before = parse_iso_date(meta_rows[0].get("v"))
            if ga4_marker_before is None:
                raise SyncError("heating_ga4_ultimo_dia inválido no D1")
            inicio = max(ga4_desde_date, ga4_marker_before - timedelta(days=GA4_REPROCESS_DIAS - 1))
        else:
            inicio = ga4_desde_date
        ontem = now_dt.date() - timedelta(days=1)
        cur_dia = inicio
        while cur_dia <= ontem and len(ga4_days_eligible) < GA4_MAX_DIAS:
            ga4_days_eligible.append(cur_dia)
            cur_dia += timedelta(days=1)

    # `site_identities` é lida sempre que houver dias do GA4 a ler, oposição pendente ou fila vencida.
    uid_to_email: dict[str, str] = {}
    if ga4_days_eligible or optout_hmacs or due_purges:
        for r in c.d1("SELECT uid, email FROM site_identities"):
            u = r.get("uid")
            em = r.get("email").strip().lower() if isinstance(r.get("email"), str) else ""
            if CS._full(SITE_UID_RE, u) and CS.valid_email(em):
                uid_to_email[u] = em

    # Metadados e registros do Twenty (com lixeira).
    meta = read_metadata(c)
    people = CS.list_records(c, "people")
    existing_enrolls = CS.list_records(c, ENROLL_PLURAL) if meta["enroll_id"] else []

    # Leitura dos cursos (Firestore ou injetável).
    curso_reader = ler_curso if ler_curso is not None else (lambda slug: ler_curso_firestore(slug, c.env.get("HEATING_GCP_SA_FILE", "").strip()))
    cursos_disponiveis: dict[str, dict[str, dict]] = {}  # slug -> {email: insc}
    cursos_indisponiveis: set[str] = set()
    for slug in CURSOS:
        try:
            docs = curso_reader(slug)
        except (CursoIndisponivel, OSError):
            cursos_indisponiveis.add(slug)
            counts["curso_indisponivel"] += 1
            continue
        if not isinstance(docs, list):
            raise SyncError("retorno da leitura do curso inválido")
        cursos_disponiveis[slug] = _parse_curso_docs(slug, docs, counts)

    # Leitura do export do GA4 no BigQuery (dia por dia, no máximo 14 por rodada).
    # Dia sem tabela: se um dia posterior já tem tabela, conta em `ga4_dia_sem_tabela` e o marcador segue;
    # se nenhum dia posterior tem tabela ainda, espera sem erro e sem avançar o marcador além do último dia existente.
    ga4_raw_days: list[tuple[date, list[dict]]] = []
    if ga4_days_eligible:
        ga4_reader = (
            ler_ga4_dia
            if ler_ga4_dia is not None
            else (lambda dia: globals()["ler_ga4_dia"](dia, c.env.get("HEATING_GCP_SA_FILE", "").strip()))
        )
        day_results: list[tuple[date, list[dict] | None]] = []
        for dia in ga4_days_eligible:
            try:
                day_rows = ga4_reader(dia)
            except (Ga4Indisponivel, CS.GoogleIndisponivel, OSError):
                counts["ga4_indisponivel"] = 1
                day_results = []
                break
            if day_rows is not None and not isinstance(day_rows, list):
                raise SyncError("retorno da leitura do GA4 inválido")
            day_results.append((dia, day_rows))

        last_found_idx: int | None = None
        for i, (_dia, day_rows) in enumerate(day_results):
            if day_rows is not None:
                last_found_idx = i
        # Janela inteira sem tabela (por exemplo, 14 dias sem evento nenhum logo depois de ligar a fonte): sem isto o
        # marcador nunca nasceria e toda rodada voltaria ao mesmo bloco. Se a tabela de ontem já existe, os dias da
        # janela são dias sem evento e o marcador anda até o fim da janela (as linhas lidas de ontem ficam para a rodada
        # em que ontem entrar na janela).
        if last_found_idx is None and day_results and len(day_results) == len(ga4_days_eligible):
            ontem_probe = now_dt.date() - timedelta(days=1)
            if ga4_days_eligible[-1] < ontem_probe:
                try:
                    probe_rows = ga4_reader(ontem_probe)
                except (Ga4Indisponivel, CS.GoogleIndisponivel, OSError):
                    counts["ga4_indisponivel"] = 1
                    probe_rows = None
                    day_results = []
                if probe_rows is not None:
                    day_results = [(dia, []) for dia, _rows in day_results]
                    counts["ga4_dia_sem_tabela"] += sum(
                        1 for dia, _r in day_results if ga4_marker_before is None or dia > ga4_marker_before
                    )
                    last_found_idx = len(day_results) - 1
        if last_found_idx is not None:
            for dia, day_rows in day_results[:last_found_idx + 1]:
                if day_rows is None:
                    if ga4_marker_before is None or dia > ga4_marker_before:
                        counts["ga4_dia_sem_tabela"] += 1
                else:
                    ga4_raw_days.append((dia, day_rows))

    # ------------------------------------------------------------------ 2) Preparar novos eventos e aplicar oposição / esquecidos / retenção
    existing_event_ids = {ev["id"] for ev in existing_events}

    # Cache de HMAC de oposição e de curso esquecido por email visto.
    optout_cache: dict[str, bool] = {}

    def is_optout(email: str) -> bool:
        if email not in optout_cache:
            optout_cache[email] = CS.valid_email(email) and (heating_optout_hmac(email, key) in optout_hmacs)
        return optout_cache[email]

    esquecido_cache: dict[tuple[str, str], bool] = {}

    def is_curso_esquecido(context: str, email: str) -> bool:
        if context == "site":
            return False
        k = (context, email)
        if k not in esquecido_cache:
            esquecido_cache[k] = CS.valid_email(email) and ((context, curso_esquecido_hmac(context, email, key)) in forgotten_courses)
        return esquecido_cache[k]

    # Emails vistos em qualquer fonte para aplicar oposição.
    optout_emails_d1_delete: set[str] = set()
    optout_seen: set[str] = set()
    esquecido_d1_delete: set[tuple[str, str]] = set()

    # Passo 1: conversões -> eventos de engagement_events
    new_conv_events: list[dict] = []
    for r in raw_convs:
        p = CS.parse_row(r)
        if p is None:
            counts["conversao_invalida"] += 1
            continue
        email = p["email"]
        if is_optout(email):
            optout_seen.add(email)
            continue
        if p["when"] < retention_cutoff:
            continue
        ev_id = "cv:" + p["id"]
        if ev_id in existing_event_ids:
            continue
        existing_event_ids.add(ev_id)
        new_conv_events.append({
            "id": ev_id,
            "context": "site",
            "email": email,
            "signal": CONV_SIGNAL_MAP[p["action"]],
            "source": "conversoes",
            "ref": p["material"],
            "occurred_at": p["when"],
            "recorded_at": now_iso,
        })
    counts["eventos_conversao_novos"] = len(new_conv_events)

    # Passo 2: curso -> eventos de engagement_events + inscrições válidas (exceto curso_esquecido)
    new_curso_events: list[dict] = []
    inscricoes_ativas_por_curso: dict[str, dict[str, dict]] = {}
    for slug, docs_by_email in cursos_disponiveis.items():
        ctx = f"curso:{slug}"
        kept: dict[str, dict] = {}
        for email, insc in docs_by_email.items():
            if is_curso_esquecido(ctx, email):
                continue
            kept[email] = insc
            if is_optout(email):
                optout_seen.add(email)
                continue
            # Evento curso_cadastro
            if insc["entrada"] >= retention_cutoff:
                cad_id = f"cu:{slug}:cad:{email_sha256_32(email)}"
                if cad_id not in existing_event_ids:
                    existing_event_ids.add(cad_id)
                    new_curso_events.append({
                        "id": cad_id,
                        "context": ctx,
                        "email": email,
                        "signal": "curso_cadastro",
                        "source": "curso",
                        "ref": None,
                        "occurred_at": insc["entrada"],
                        "recorded_at": now_iso,
                    })
            # Eventos curso_aula
            for day_id, (ev_id, upd_iso) in sorted(insc["aulas"].items()):
                if upd_iso < retention_cutoff:
                    continue
                if ev_id in existing_event_ids:
                    continue
                existing_event_ids.add(ev_id)
                new_curso_events.append({
                    "id": ev_id,
                    "context": ctx,
                    "email": email,
                    "signal": "curso_aula",
                    "source": "curso",
                    "ref": day_id,
                    "occurred_at": upd_iso,
                    "recorded_at": now_iso,
                })
        inscricoes_ativas_por_curso[slug] = kept
    counts["eventos_curso_novos"] = len(new_curso_events)

    # Passo 2b: GA4 (BigQuery) -> eventos de engagement_events (por dia, para avançar o marcador após cada dia)
    ga4_days_to_write: list[tuple[date, list[dict]]] = []
    new_ga4_events: list[dict] = []
    for dia, day_rows in ga4_raw_days:
        yyyymmdd = dia.strftime("%Y%m%d")
        day_events: list[dict] = []
        for row in day_rows:
            if not isinstance(row, dict):
                continue
            uid = row.get("user_id")
            if not isinstance(uid, str) or not uid:
                continue
            email = uid_to_email.get(uid)
            if email is None:
                counts["ga4_sem_vinculo"] += 1
                continue
            if is_optout(email):
                optout_seen.add(email)
                continue
            uid_h = uid_sha256_16(uid)
            sessoes = int(row.get("sessoes") or 0)
            eng_ms = int(row.get("engagement_time_msec") or 0)
            scrolls = int(row.get("scrolls") or 0)
            cliques_contato = int(row.get("cliques_contato") or 0)
            cliques_agenda = int(row.get("cliques_agenda") or 0)

            sinais_dia: list[tuple[str, str]] = []
            ts_visita = ga4_ts_to_iso_ms(row.get("min_ts_visita"))
            if sessoes > 0 and ts_visita:
                sinais_dia.append(("site_visita", ts_visita))
            if eng_ms >= GA4_ENGAGED_MSEC_MIN or scrolls > 0:
                ts_eng = ga4_ts_to_iso_ms(row.get("min_ts_engajamento")) or ts_visita
                if ts_eng:
                    sinais_dia.append(("site_engajada", ts_eng))
            if cliques_contato > 0:
                ts_cont = ga4_ts_to_iso_ms(row.get("min_ts_contato"))
                if ts_cont:
                    sinais_dia.append(("site_contato", ts_cont))
            if cliques_agenda > 0:
                ts_ag = ga4_ts_to_iso_ms(row.get("min_ts_agenda"))
                if ts_ag:
                    sinais_dia.append(("site_agende", ts_ag))

            for sig, occurred_at in sinais_dia:
                if occurred_at < retention_cutoff:
                    continue
                ev_id = f"ga:{yyyymmdd}:{uid_h}:{sig}"
                if ev_id in existing_event_ids:
                    continue
                existing_event_ids.add(ev_id)
                ev = {
                    "id": ev_id,
                    "context": "site",
                    "email": email,
                    "signal": sig,
                    "source": "ga4",
                    "ref": None,
                    "occurred_at": occurred_at,
                    "recorded_at": now_iso,
                }
                day_events.append(ev)
                new_ga4_events.append(ev)
        ga4_days_to_write.append((dia, day_events))
    counts["ga4_dias_lidos"] = len(ga4_days_to_write)
    counts["eventos_ga4_novos"] = len(new_ga4_events)
    # Passo 3: oposição, esquecidos e retenção sobre eventos, notas e `site_identities` já existentes no D1
    optout_identities_delete: set[str] = set()
    optout_identities_uids: set[str] = set()
    for u, em in uid_to_email.items():
        if is_optout(em):
            optout_seen.add(em)
            optout_identities_delete.add(em)
            optout_identities_uids.add(u)
    if not apply:
        counts["ga4_fila_processados"] = len({r["uid"] for r in due_purges} | optout_identities_uids)

    kept_events: list[dict] = []
    retention_delete_count = 0
    for ev in existing_events:
        email = ev["email"]
        ctx = ev["context"]
        if is_optout(email):
            optout_seen.add(email)
            optout_emails_d1_delete.add(email)
            continue
        if is_curso_esquecido(ctx, email):
            esquecido_d1_delete.add((ctx, email))
            continue
        if ev["occurred_at"] < retention_cutoff:
            retention_delete_count += 1
            continue
        kept_events.append(ev)

    for hr in existing_heating_rows:
        if is_optout(hr["email"]):
            optout_seen.add(hr["email"])

    # Também detecta Pessoas no Twenty com campos heating preenchidos cujo email está em heating_optout.
    for p in people:
        if p.get("deletedAt"):
            continue
        if any(_filled(p.get(k)) for k in PERSON_HEATING_FIELDS):
            for e in TS._emails(p):
                if is_optout(e):
                    optout_seen.add(e)

    counts["oposicao_aplicada"] = len(optout_seen)
    counts["retencao_apagados"] = retention_delete_count

    all_active_events = kept_events + new_conv_events + new_curso_events + new_ga4_events

    # ------------------------------------------------------------------ 3) Passo 4: cálculo da régua por (context, email)
    events_by_ctx_email: dict[tuple[str, str], list[dict]] = {}
    for ev in all_active_events:
        events_by_ctx_email.setdefault((ev["context"], ev["email"]), []).append(ev)

    excluded_seen: set[str] = set()
    heating_by_ctx_email: dict[tuple[str, str], dict] = {}
    for (ctx, email), evs in sorted(events_by_ctx_email.items()):
        if is_optout(email) or is_curso_esquecido(ctx, email):
            continue
        if is_excluded(email, excluded_set):
            excluded_seen.add(email)
            continue
        if ctx == "site":
            email_blocked = email in unsub_site
        else:
            slug = ctx[6:]
            insc = (inscricoes_ativas_por_curso.get(slug) or {}).get(email)
            # Bloqueio conservador: sem status conhecido (Firestore indisponível ou inscrito ausente), sinais de email não contam.
            email_blocked = (insc is None) or (insc["status"] in ("unsubscribed", "bounced"))
        res = calcular_heating_contexto(ctx, evs, now_dt, email_desde=email_desde_iso, email_bloqueado=email_blocked)
        if res is not None:
            heating_by_ctx_email[(ctx, email)] = res

    counts["excluidos"] = len(excluded_seen)
    site_scores = {email: h for (ctx, email), h in heating_by_ctx_email.items() if ctx == "site"}
    curso_scores = {(ctx, email): h for (ctx, email), h in heating_by_ctx_email.items() if ctx != "site"}
    counts["notas_site"] = len(site_scores)
    counts["notas_curso"] = len(curso_scores)
    counts["quentes_site"] = sum(1 for h in site_scores.values() if h["band"] == "quente")

    # ------------------------------------------------------------------ 4) Plano do Twenty (Pessoas + Inscrições)
    idx = CS.people_by_email(people)
    trashed = CS.trashed_emails(people)
    people_by_id = {p["id"]: p for p in people}
    trashed_seen: set[str] = set()

    # 4a) Pessoas a criar a partir dos cursos disponíveis (exceto bounced, excluídos, lixeira e curso_esquecido;
    #     decisão do Gui de 24/11 e 27/11).
    people_to_create: OrderedDict[str, dict] = OrderedDict()  # email -> {"name": dict|None}
    for slug, kept_insc in inscricoes_ativas_por_curso.items():
        for email, insc in sorted(kept_insc.items()):
            if is_excluded(email, excluded_set):
                continue
            if idx.get(email):
                continue
            if email in trashed:
                trashed_seen.add(email)
                continue
            if insc["status"] == "bounced":
                continue
            name_obj = split_name(insc.get("name"))
            if email not in people_to_create:
                people_to_create[email] = {"name": name_obj}
            elif people_to_create[email]["name"] is None and name_obj is not None:
                people_to_create[email]["name"] = name_obj

    counts["pessoas_criadas"] = len(people_to_create)

    # 4b) Nota do site por Pessoa existente (se uma Pessoa tem mais de um email com nota site, vence a maior nota).
    site_want_by_person: dict[str, dict] = {}
    sem_pessoa_emails: set[str] = set()
    for email, h in sorted(site_scores.items()):
        persons = idx.get(email) or []
        if persons:
            p = persons[0]
            prev = site_want_by_person.get(p["id"])
            if prev is None or (h["score"], email) > (prev["score"], prev["_email"]):
                site_want_by_person[p["id"]] = {**h, "_email": email}
        elif email in people_to_create:
            # Pessoa será criada pelo curso nesta rodada e já nasce com os campos heating* do site.
            pass
        elif email in trashed:
            trashed_seen.add(email)
        else:
            sem_pessoa_emails.add(email)

    counts["sem_pessoa"] = len(sem_pessoa_emails)
    counts["pessoa_na_lixeira"] = len(trashed_seen)

    person_updates: list[tuple[str, dict]] = []
    person_clears: list[tuple[str, dict]] = []
    for p in sorted((x for x in people if not x.get("deletedAt")), key=lambda x: x["id"]):
        want = site_want_by_person.get(p["id"])
        patch = _person_heating_patch(p, want, now_iso)
        if not patch:
            continue
        if want is None:
            person_clears.append((p["id"], patch))
        else:
            person_updates.append((p["id"], patch))
    counts["pessoas_atualizadas"] = len(person_updates)
    counts["pessoas_limpas"] = len(person_clears)

    # 4c) Inscrições em curso (`courseEnrollment`): criação e atualização para cursos disponíveis nesta rodada.
    enroll_by_iid = {x["inscricaoId"]: x for x in existing_enrolls if x.get("inscricaoId")}
    enrolls_to_create: list[tuple[str, dict, dict | None]] = []  # (email, insc, heating|None)
    enrolls_to_update: list[tuple[str, dict]] = []  # (enroll_id, patch)
    enrolls_to_relink: list[tuple[str, str, dict, dict, dict | None]] = []  # (enroll_id, email, existing, insc, h)
    handled_iids: set[str] = set()

    for slug, kept_insc in inscricoes_ativas_por_curso.items():
        ctx = f"curso:{slug}"
        for email, insc in sorted(kept_insc.items()):
            if is_excluded(email, excluded_set):
                continue
            persons = idx.get(email) or []
            if not persons and email not in people_to_create:
                # Pessoa na lixeira ou status bounced sem Pessoa no CRM: não cria nem atualiza Inscrição.
                continue
            iid = inscricao_id(slug, email)
            existing = enroll_by_iid.get(iid)
            if existing and existing.get("deletedAt"):
                # Inscrição na lixeira: não recria (inscricaoId é único) nem restaura.
                continue
            handled_iids.add(iid)
            h = heating_by_ctx_email.get((ctx, email))
            if existing is None:
                enrolls_to_create.append((email, insc, h))
            elif persons:
                pid = persons[0]["id"]
                desired = _enroll_desired(insc, pid, h, now_iso)
                patch = _enroll_patch(existing, desired)
                if patch:
                    enrolls_to_update.append((existing["id"], patch))
            else:
                # Pessoa será criada em 5a: religa a Inscrição ao novo id retornado (nunca envia pessoaId vazio).
                enrolls_to_relink.append((existing["id"], email, existing, insc, h))

    # Inscrições ativas já existentes que não vieram na leitura atual do curso:
    # - oposição limpa nota/faixa/sinais/calculadoEm mesmo com o curso indisponível nesta rodada;
    # - para cursos disponíveis, se o email não tem nota hoje (expirado, excluído, etc.), limpa os 4 campos;
    #   se ainda tem nota no D1 para o contexto (eventos dentro da janela ou spam), atualiza só nota, faixa, sinais e
    #   calculadoEm quando mudarem (ou quando faltar calculadoEm).
    email_by_sha32: dict[str, str] = {}
    for e in (
        [ev["email"] for ev in existing_events]
        + [hr["email"] for hr in existing_heating_rows]
        + [em for _ctx, em in heating_by_ctx_email]
        + [em for p in people for em in TS._emails(p)]
        + [em for docs in cursos_disponiveis.values() for em in docs]
    ):
        if CS.valid_email(e):
            email_by_sha32.setdefault(email_sha256_32(e), e)

    all_curso_values = {curso_option_value(s): s for s in CURSOS}
    for x in existing_enrolls:
        if x.get("deletedAt"):
            continue
        iid = x.get("inscricaoId")
        if not iid or iid in handled_iids:
            continue
        slug = all_curso_values.get(x.get("curso") or "")
        if not slug:
            continue
        owner = people_by_id.get(x.get("pessoaId") or "")
        owner_emails = TS._emails(owner) if owner else []
        matched_email = next((e for e in owner_emails if inscricao_id(slug, e) == iid), None)
        if matched_email is None and iid.startswith(f"{slug}:"):
            cand = email_by_sha32.get(iid[len(slug) + 1:])
            if cand and inscricao_id(slug, cand) == iid:
                matched_email = cand
        if matched_email and is_optout(matched_email):
            patch = _enroll_score_patch(x, None, now_iso)
            if patch:
                optout_seen.add(matched_email)
                enrolls_to_update.append((x["id"], patch))
        elif slug in cursos_disponiveis:
            h = heating_by_ctx_email.get((f"curso:{slug}", matched_email)) if matched_email is not None else None
            patch = _enroll_score_patch(x, h, now_iso)
            if patch:
                enrolls_to_update.append((x["id"], patch))

    counts["oposicao_aplicada"] = len(optout_seen)
    counts["inscricoes_criadas"] = len(enrolls_to_create)
    counts["inscricoes_atualizadas"] = len(enrolls_to_update) + len(enrolls_to_relink)

    # ------------------------------------------------------------------ 5) Escritas (somente com --apply)
    if apply:
        need_person_schema = bool(
            person_updates
            or person_clears
            or any(e in site_scores for e in people_to_create)
        )
        need_enroll_schema = bool(enrolls_to_create or enrolls_to_update or enrolls_to_relink)
        if need_person_schema or need_enroll_schema:
            ensure_schema(c, meta, need_person=need_person_schema, need_enroll=need_enroll_schema)

        # 5a) Cria Pessoas novas dos cursos (já com campos heating* do site, se houver nota site).
        created_pid_by_email: dict[str, str] = {}
        for email, info in people_to_create.items():
            body: dict = {"emails": {"primaryEmail": email, "additionalEmails": []}}
            if info["name"] is not None:
                body["name"] = info["name"]
            if email in site_scores:
                h = site_scores[email]
                body.update({
                    "heatingNota": h["score"],
                    "heatingFaixa": h["band"].upper(),
                    "heatingSinais": formatar_sinais_texto(h["signals_list"]),
                    "heatingCalculadoEm": now_iso,
                })
            res = c.twenty("POST", "/rest/people", body, op="criar pessoa")
            created_pid_by_email[email] = res["data"]["createPerson"]["id"]

        # 5b) Atualiza e limpa campos heating* nas Pessoas existentes.
        for pid, patch in person_updates:
            c.twenty("PATCH", f"/rest/people/{urllib.parse.quote(pid)}", patch, op="atualizar heating da pessoa")
        for pid, patch in person_clears:
            c.twenty("PATCH", f"/rest/people/{urllib.parse.quote(pid)}", patch, op="limpar heating da pessoa")

        # 5c) Cria e atualiza Inscrições em curso.
        created_enrolls_by_email: dict[str, list[tuple[str, bool]]] = {}
        for email, insc, h in enrolls_to_create:
            persons = idx.get(email) or []
            pid = persons[0]["id"] if persons else created_pid_by_email[email]
            if not pid:
                raise SyncError("id da Pessoa ausente ao criar inscrição")
            body = _enroll_desired(insc, pid, h, now_iso)
            res = c.twenty("POST", f"/rest/{ENROLL_PLURAL}", body, op="criar inscrição em curso")
            created_eid = ((res.get("data") or {}).get("createCourseEnrollment") or {}).get("id")
            if created_eid:
                created_enrolls_by_email.setdefault(email, []).append((created_eid, h is not None))
        updated_enroll_patches: dict[str, dict] = {}
        for eid, patch in enrolls_to_update:
            c.twenty("PATCH", f"/rest/{ENROLL_PLURAL}/{urllib.parse.quote(eid)}", patch, op="atualizar inscrição em curso")
            updated_enroll_patches[eid] = patch
        for eid, email, existing, insc, h in enrolls_to_relink:
            pid = created_pid_by_email[email]
            if not pid:
                raise SyncError("id da Pessoa ausente ao atualizar inscrição")
            desired = _enroll_desired(insc, pid, h, now_iso)
            patch = _enroll_patch(existing, desired)
            if patch:
                c.twenty("PATCH", f"/rest/{ENROLL_PLURAL}/{urllib.parse.quote(eid)}", patch, op="atualizar inscrição em curso")
                updated_enroll_patches[eid] = patch

        # 5d) Atualiza `engagement_events` no D1: insere novos condicionalmente (WHERE NOT EXISTS heating_optout),
        #     grava GA4 dia por dia (só avançando `heating_ga4_ultimo_dia` após gravar os eventos do dia),
        #     apaga oposição, esquecidos e retenção (> 365d), e processa itens vencidos de `ga4_purge_queue`.
        all_new_events = new_conv_events + new_curso_events
        for i in range(0, len(all_new_events), EVENT_CHUNK):
            part = all_new_events[i:i + EVENT_CHUNK]
            sql, params = _insert_events_sql(part, key)
            c.d1(sql, params)
        ga4_marker = ga4_marker_before
        for dia, day_events in ga4_days_to_write:
            for i in range(0, len(day_events), EVENT_CHUNK):
                part = day_events[i:i + EVENT_CHUNK]
                sql, params = _insert_events_sql(part, key)
                c.d1(sql, params)
            if ga4_marker is None or dia > ga4_marker:
                c.d1(
                    "INSERT INTO meta (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = max(meta.v, excluded.v)",
                    [GA4_META_KEY, dia.isoformat()],
                )
                ga4_marker = dia
        if optout_emails_d1_delete:
            del_list = sorted(optout_emails_d1_delete)
            for i in range(0, len(del_list), DELETE_CHUNK):
                part = del_list[i:i + DELETE_CHUNK]
                c.d1(f"DELETE FROM engagement_events WHERE email IN ({','.join('?' * len(part))})", part)
        if optout_identities_delete:
            # Apagar `site_identities` dispara o trigger da 0014 e enfileira cada uid em `ga4_purge_queue`.
            id_del_list = sorted(optout_identities_delete)
            for i in range(0, len(id_del_list), DELETE_CHUNK):
                part = id_del_list[i:i + DELETE_CHUNK]
                c.d1(f"DELETE FROM site_identities WHERE email IN ({','.join('?' * len(part))})", part)
        for ctx, email in sorted(esquecido_d1_delete):
            c.d1("DELETE FROM engagement_events WHERE context = ? AND email = ?", [ctx, email])
        if retention_delete_count:
            c.d1("DELETE FROM engagement_events WHERE occurred_at < ?", [retention_cutoff])

        if due_purges or optout_identities_uids:
            q_res = processar_fila_ga4(
                c,
                only_due=True,
                include_uids=sorted(optout_identities_uids),
                ga4_delete=ga4_delete,
                bq_delete=bq_delete,
                now_dt=now_dt,
                stop_on_bq_error=True,
            )
            if q_res["sem_credencial"]:
                counts["ga4_indisponivel"] = 1
            else:
                counts["ga4_fila_processados"] += q_res["ga4_fila_processados"]
                counts["bq_remocao_adiada"] += q_res["bq_remocao_adiada"]
                counts["ga4_remocao_pendente"] += q_res["ga4_remocao_pendente"]
                counts["bq_remocao_travada"] += q_res["bq_remocao_travada"]
                counts["bq_sessoes_excedidas"] += q_res["bq_sessoes_excedidas"]
                if q_res["bq_remocao_falhou"]:
                    counts["ga4_indisponivel"] = 1

        # 5e) Troca a tabela `heating` inteira via `batch` da API REST (com INSERT condicional em heating_optout),
        #     se houve mudança (risco aceito: sem garantia de transação na API REST; se falhar no meio sem rollback,
        #     a rodada seguinte refaz a tabela inteira).
        desired_heating: list[tuple] = []
        for (ctx, email), h in sorted(heating_by_ctx_email.items()):
            persons = idx.get(email) or []
            raw_pid = persons[0]["id"] if persons else created_pid_by_email.get(email)
            valid_pid = raw_pid if CS._full(CS.TWENTY_PERSON_ID, raw_pid) else None
            desired_heating.append((ctx, email, h["score"], h["band"], h["signals"], now_iso, valid_pid))

        existing_heating_tuples = sorted(
            (r["context"], r["email"], int(r["score"]), r["band"], r["signals"], r["computed_at"], r["twenty_person_id"])
            for r in existing_heating_rows
        )
        if desired_heating != existing_heating_tuples:
            stmts: list[tuple[str, list | None]] = [("DELETE FROM heating", None)]
            for i in range(0, len(desired_heating), HEATING_CHUNK):
                part = desired_heating[i:i + HEATING_CHUNK]
                stmts.append(_insert_heating_sql(part, key))
            d1_batch(c, stmts)

        # 5f) Releitura de `heating_optout` depois das escritas no D1 e no Twenty: limpa D1 e Twenty (Pessoa e
        #     Inscrições) para qualquer oposição registrada pelo Worker enquanto o job rodava.
        optout_hmacs_after = {r["email_hmac"] for r in c.d1("SELECT email_hmac FROM heating_optout")}
        new_optout_hmacs = optout_hmacs_after - optout_hmacs
        if new_optout_hmacs:
            candidate_emails: set[str] = set()
            for ev in all_active_events:
                candidate_emails.add(ev["email"])
            for ev in existing_events:
                candidate_emails.add(ev["email"])
            for hr in existing_heating_rows:
                candidate_emails.add(hr["email"])
            for rc in raw_convs:
                if isinstance(rc.get("email"), str):
                    candidate_emails.add(rc["email"].strip().lower())
            for _ctx, em in heating_by_ctx_email:
                candidate_emails.add(em)
            for docs_by_email in cursos_disponiveis.values():
                candidate_emails.update(docs_by_email.keys())
            candidate_emails.update(people_to_create.keys())
            for p in people:
                if not p.get("deletedAt"):
                    candidate_emails.update(TS._emails(p))
            for r in c.d1("SELECT email FROM site_identities"):
                if isinstance(r.get("email"), str):
                    candidate_emails.add(r["email"].strip().lower())

            late_optout_emails = sorted(
                em for em in candidate_emails
                if CS.valid_email(em) and heating_optout_hmac(em, key) in new_optout_hmacs
            )
            if late_optout_emails:
                for i in range(0, len(late_optout_emails), DELETE_CHUNK):
                    part = late_optout_emails[i:i + DELETE_CHUNK]
                    ph = ",".join("?" * len(part))
                    c.d1(f"DELETE FROM engagement_events WHERE email IN ({ph})", part)
                    c.d1(f"DELETE FROM heating WHERE email IN ({ph})", part)
                    c.d1(f"DELETE FROM site_identities WHERE email IN ({ph})", part)

                person_heating_present = [k for k in PERSON_HEATING_FIELDS if k in meta["person"]]
                updated_pids = {pid for pid, _ in person_updates}
                cleared_pids = {pid for pid, _ in person_clears}
                active_people_by_id = {p["id"]: p for p in people if not p.get("deletedAt")}
                ref_pids_by_email: dict[str, set[str]] = {}
                for row in (*existing_heating_rows, *raw_convs):
                    raw_em = row.get("email")
                    ref_pid = row.get("twenty_person_id")
                    if isinstance(raw_em, str) and CS._full(CS.TWENTY_PERSON_ID, ref_pid):
                        ref_pids_by_email.setdefault(raw_em.strip().lower(), set()).add(ref_pid)

                late_pids_to_clear: set[str] = set()
                for em in late_optout_emails:
                    candidate_people: dict[str, dict] = {p["id"]: p for p in (idx.get(em) or [])}
                    for ref_pid in ref_pids_by_email.get(em, ()):
                        if ref_pid in active_people_by_id:
                            candidate_people[ref_pid] = active_people_by_id[ref_pid]
                    for pid, p in candidate_people.items():
                        had_before = any(_filled(p.get(k)) for k in person_heating_present)
                        if pid in updated_pids or (had_before and pid not in cleared_pids):
                            late_pids_to_clear.add(pid)
                    cpid = created_pid_by_email.get(em)
                    if cpid and em in site_scores:
                        late_pids_to_clear.add(cpid)
                if person_heating_present and late_pids_to_clear:
                    clear_p = {k: None for k in person_heating_present}
                    for pid in sorted(late_pids_to_clear):
                        c.twenty("PATCH", f"/rest/people/{urllib.parse.quote(pid)}", clear_p, op="limpar heating da pessoa")

                enroll_score_fields = [k for k in ("nota", "faixa", "sinais", "calculadoEm") if k in meta["enroll"]]
                if meta["enroll_id"] and enroll_score_fields:
                    late_suffixes = {":" + email_sha256_32(em) for em in late_optout_emails}
                    late_eids_to_clear: set[str] = set()
                    for x in existing_enrolls:
                        if x.get("deletedAt"):
                            continue
                        iid = x.get("inscricaoId")
                        if not isinstance(iid, str) or not any(iid.endswith(suf) for suf in late_suffixes):
                            continue
                        eid = x["id"]
                        eff = {**x, **(updated_enroll_patches.get(eid) or {})}
                        if any(_filled(eff.get(k)) for k in enroll_score_fields):
                            late_eids_to_clear.add(eid)
                    for em in late_optout_emails:
                        for eid, had_score in created_enrolls_by_email.get(em, []):
                            if had_score:
                                late_eids_to_clear.add(eid)
                    clear_e = {k: None for k in enroll_score_fields}
                    for eid in sorted(late_eids_to_clear):
                        c.twenty("PATCH", f"/rest/{ENROLL_PLURAL}/{urllib.parse.quote(eid)}", clear_e, op="limpar heating da inscrição")

                optout_seen.update(late_optout_emails)
                counts["oposicao_aplicada"] = len(optout_seen)

    CS._report(counts, out, "gravado" if apply else "simulação: nada foi gravado (use --apply)")
    return counts


# ------------------------------------------------------------------ Modos --optout e --forget-curso

def optout(
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
    """Registra oposição permanente ao heating (`heating_optout`), apaga eventos e notas de todos os contextos no D1,
    limpa campos heating* na Person (tanto achadas pelo email quanto pelos `twenty_person_id` salvos em `heating` e
    `conversions` antes de apagar o D1) e nota/faixa/sinais/calculadoEm das Inscrições no Twenty, apaga
    `site_identities` (cujo trigger enfileira os `uid`s em `ga4_purge_queue`) e processa a fila na hora quando há
    credencial GCP (sem credencial, apenas enfileira e informa)."""
    out = out or sys.stdout
    key = c.env.get("FORGET_HMAC_KEY", "") if forget_key is None else forget_key
    norm = email.strip().lower()
    opt_hmac = heating_optout_hmac(norm, key)
    counts: Counter = Counter()

    if apply:
        # Grava a marca permanente antes de ler o Twenty (mesmo upsert do Worker, com relógio do D1).
        CS._upsert_heating_optout(c, opt_hmac)

    ev_count = int(c.d1("SELECT count(*) AS n FROM engagement_events WHERE email = ?", [norm])[0]["n"])
    heat_rows = c.d1("SELECT twenty_person_id FROM heating WHERE email = ?", [norm])
    heat_count = len(heat_rows)
    conv_rows = c.d1("SELECT twenty_person_id FROM conversions WHERE email = ?", [norm])
    raw_identities = c.d1("SELECT uid FROM site_identities WHERE email = ?", [norm])
    site_uids = sorted({r["uid"] for r in raw_identities if CS._full(SITE_UID_RE, r.get("uid"))})
    ref_pids = {
        r["twenty_person_id"]
        for r in (*heat_rows, *conv_rows)
        if CS._full(CS.TWENTY_PERSON_ID, r.get("twenty_person_id"))
    }

    meta = read_metadata(c)
    all_people = CS.list_records(c, "people")
    persons = list(CS.people_by_email(all_people).get(norm) or [])
    active_by_id = {p["id"]: p for p in all_people if not p.get("deletedAt")}
    seen_pids = {p["id"] for p in persons}
    for pid in sorted(ref_pids):
        if pid not in seen_pids and pid in active_by_id:
            seen_pids.add(pid)
            persons.append(active_by_id[pid])
    person_heating_present = [k for k in PERSON_HEATING_FIELDS if k in meta["person"]]
    persons_to_clear = [p for p in persons if any(_filled(p.get(k)) for k in person_heating_present)]

    enrolls = CS.list_records(c, ENROLL_PLURAL) if meta["enroll_id"] else []
    suffix = ":" + email_sha256_32(norm)
    enroll_score_fields = [k for k in ("nota", "faixa", "sinais", "calculadoEm") if k in meta["enroll"]]
    enrolls_to_clear = [
        x for x in enrolls
        if not x.get("deletedAt")
        and isinstance(x.get("inscricaoId"), str)
        and x["inscricaoId"].endswith(suffix)
        and any(_filled(x.get(k)) for k in enroll_score_fields)
    ]

    counts["eventos_apagados"] = ev_count
    counts["notas_apagadas"] = heat_count
    counts["pessoas_limpas"] = len(persons_to_clear)
    counts["inscricoes_limpas"] = len(enrolls_to_clear)
    counts["identidades_site"] = len(site_uids)

    ga4_pendente = False
    bq_falhou = False
    sem_cred_enfileirado = False
    if apply:
        if person_heating_present:
            patch_p = {k: None for k in person_heating_present}
            for p in persons_to_clear:
                c.twenty("PATCH", f"/rest/people/{urllib.parse.quote(p['id'])}", patch_p, op="limpar heating da pessoa")
        if enroll_score_fields:
            patch_e = {k: None for k in enroll_score_fields}
            for x in enrolls_to_clear:
                c.twenty("PATCH", f"/rest/{ENROLL_PLURAL}/{urllib.parse.quote(x['id'])}", patch_e, op="limpar heating da inscrição")
        c.d1("DELETE FROM engagement_events WHERE email = ?", [norm])
        c.d1("DELETE FROM heating WHERE email = ?", [norm])
        if raw_identities:
            c.d1("DELETE FROM site_identities WHERE email = ?", [norm])
            if c.d1("SELECT count(*) AS n FROM site_identities WHERE email = ?", [norm])[0]["n"] != 0:
                raise SyncError("o D1 ainda tem identidades do site do email depois da oposição")
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
                if g_res["bq_sessoes_excedidas"]:
                    counts["bq_sessoes_excedidas"] = g_res["bq_sessoes_excedidas"]
                    bq_falhou = True
                ga4_pendente = g_res["ga4_remocao_pendente"] > 0

    if apply:
        # Mesmo repetido depois de o vínculo sumir, o comando continua avisando enquanto houver GA4 pendente na fila.
        counts["ga4_fila_pendente_total"] = int(
            c.d1("SELECT count(*) AS n FROM ga4_purge_queue WHERE ga4_pendente = 1")[0]["n"]
        )
    CS._report(counts, out, "oposição registrada" if apply else "simulação: nada foi apagado (use --apply)")
    if apply and counts.get("ga4_fila_pendente_total", 0) > 0 and not ga4_pendente and not sem_cred_enfileirado:
        print(CS.GA4_FILA_PENDENTE_MSG, file=out)
    if apply and sem_cred_enfileirado:
        print(GA4_FILA_SEM_CRED_MSG, file=out)
    else:
        if apply and ga4_pendente:
            print(GA4_PURGE_PENDENTE_MSG, file=out)
        if apply and bq_falhou:
            print(BQ_PURGE_PENDENTE_MSG, file=out)
    return counts


def forget_curso(c: TS.Clients, slug: str, email: str, *, apply: bool = False, out=None, forget_key: str | None = None) -> Counter:
    """Remove os dados de um curso específico: grava marca permanente em `curso_esquecido`, apaga de vez a Inscrição
    no Twenty (inclusive da lixeira) conferindo na releitura, e apaga eventos e nota do contexto no D1."""
    out = out or sys.stdout
    if not isinstance(slug, str) or not CS._full(CS.SLUG, slug):
        raise SyncError("slug de curso inválido")
    key = c.env.get("FORGET_HMAC_KEY", "") if forget_key is None else forget_key
    norm = email.strip().lower()
    context = f"curso:{slug}"
    esc_hmac = curso_esquecido_hmac(context, norm, key)
    iid = inscricao_id(slug, norm)
    counts: Counter = Counter()

    if apply:
        # Grava a marca permanente antes de ler o Twenty: impede rematerialização a partir do Firestore.
        c.d1(
            f"INSERT INTO curso_esquecido (context, email_hmac, forgotten_at) VALUES (?, ?, {CS._D1_NOW_SQL}) "
            f"ON CONFLICT(context, email_hmac) DO UPDATE SET forgotten_at = max(curso_esquecido.forgotten_at, {CS._D1_NOW_SQL})",
            [context, esc_hmac],
        )

    ev_count = int(c.d1("SELECT count(*) AS n FROM engagement_events WHERE context = ? AND email = ?", [context, norm])[0]["n"])
    heat_count = int(c.d1("SELECT count(*) AS n FROM heating WHERE context = ? AND email = ?", [context, norm])[0]["n"])

    meta = read_metadata(c)
    enrolls = [x for x in CS.list_records(c, ENROLL_PLURAL) if x.get("inscricaoId") == iid] if meta["enroll_id"] else []

    counts["eventos_apagados"] = ev_count
    counts["notas_apagadas"] = heat_count
    counts["inscricoes_no_crm"] = len(enrolls)

    if apply:
        for x in enrolls:
            c.twenty("DELETE", f"/rest/{ENROLL_PLURAL}/{urllib.parse.quote(x['id'])}", op="apagar inscrição em curso")
        if meta["enroll_id"] and any(x.get("inscricaoId") == iid for x in CS.list_records(c, ENROLL_PLURAL)):
            raise SyncError("ainda há Inscrição do curso no Twenty; o D1 ficou intacto, rode de novo")
        c.d1("DELETE FROM engagement_events WHERE context = ? AND email = ?", [context, norm])
        if c.d1("SELECT count(*) AS n FROM engagement_events WHERE context = ? AND email = ?", [context, norm])[0]["n"] != 0:
            raise SyncError("o D1 ainda tem eventos do curso depois da remoção")
        c.d1("DELETE FROM heating WHERE context = ? AND email = ?", [context, norm])
        if c.d1("SELECT count(*) AS n FROM heating WHERE context = ? AND email = ?", [context, norm])[0]["n"] != 0:
            raise SyncError("o D1 ainda tem nota do curso depois da remoção")

    for k in sorted(counts):
        print(f"{k}: {counts[k]}", file=out)
    print(FORGET_CURSO_AVISO, file=out)
    print("curso removido" if apply else "simulação: nada foi apagado (use --apply)", file=out)
    return counts


# ------------------------------------------------------------------ Lock e CLI

def _acquire_lock(path: str, *, wait_s: float = LOCK_WAIT_S, poll_s: float = LOCK_POLL_S,
                  sleep=time.sleep, monotonic=time.monotonic):
    fh = open(path, "a")
    deadline = monotonic() + wait_s
    while True:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fh
        except BlockingIOError:
            rem = deadline - monotonic()
            if rem <= 0:
                fh.close()
                raise SyncError("lock ocupado por mais de 10 minutos; outra execução em andamento") from None
            sleep(min(poll_s, rem))


def main(
    argv=None,
    transport=TS._transport,
    sleep=time.sleep,
    stdin=None,
    *,
    now_dt: datetime | None = None,
    ler_curso=None,
    ler_ga4_dia=None,
    ga4_delete=None,
    bq_delete=None,
    lock_wait_s: float = LOCK_WAIT_S,
    lock_poll_s: float = LOCK_POLL_S,
    monotonic=time.monotonic,
) -> int:
    ap = TS._Parser(description="Calcula o heating dos contatos e atualiza o D1 e o Twenty (simulação por padrão).")
    ap.add_argument("--apply", action="store_true", help="grava no D1 e no Twenty")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--optout", action="store_true", help="registra oposição ao heating para o email lido da entrada padrão")
    mode.add_argument("--forget-curso", dest="forget_curso", metavar="SLUG", help="remove o curso SLUG para o email lido da entrada padrão")
    a = ap.parse_args(argv)

    env = {k: os.environ.get(k, "") for k in TS.REQUIRED_ENV}
    if not all(env.values()):
        print("ERRO: faltam variáveis de ambiente: " + ", ".join(k for k, v in env.items() if not v), file=sys.stderr)
        return 2
    forget_key = os.environ.get("FORGET_HMAC_KEY", "")
    if len(forget_key) < CS.MIN_FORGET_KEY:
        msg = ("falta variável de ambiente: FORGET_HMAC_KEY" if not forget_key
               else f"FORGET_HMAC_KEY curta demais (mínimo {CS.MIN_FORGET_KEY} caracteres)")
        print(f"ERRO: {msg}", file=sys.stderr)
        return 2
    env["FORGET_HMAC_KEY"] = forget_key
    for opt_k in ("HEATING_EXCLUDE", "HEATING_GCP_SA_FILE", "HEATING_EMAIL_DESDE", "HEATING_GA4_DESDE"):
        if opt_k in os.environ:
            env[opt_k] = os.environ[opt_k]

    lock = None
    rc = 0
    try:
        lock_path = os.environ.get("CONVERSOES_SYNC_LOCK") or CS.LOCK_DEFAULT
        lock = _acquire_lock(lock_path, wait_s=lock_wait_s, poll_s=lock_poll_s, sleep=sleep, monotonic=monotonic)
        c = TS.Clients(env, transport, sleep)
        if a.optout or a.forget_curso is not None:
            lines = [x for x in (stdin or sys.stdin).read().splitlines() if x.strip()]
            if len(lines) != 1:
                flag = "--optout" if a.optout else "--forget-curso"
                raise SyncError(f"{flag} espera exatamente um email na entrada padrão")
            if a.optout:
                counts = optout(
                    c,
                    lines[0],
                    apply=a.apply,
                    forget_key=forget_key,
                    ga4_delete=ga4_delete,
                    bq_delete=bq_delete,
                    now_dt=now_dt,
                )
                if a.apply and (counts.get("ga4_remocao_pendente", 0) > 0 or counts.get("bq_remocao_falhou", 0) > 0
                                or counts.get("bq_sessoes_excedidas", 0) > 0
                                or counts.get("ga4_fila_pendente_total", 0) > 0):
                    rc = 2
            else:
                forget_curso(c, a.forget_curso, lines[0], apply=a.apply, forget_key=forget_key)
        else:
            run(
                c,
                apply=a.apply,
                now_dt=now_dt,
                ler_curso=ler_curso,
                ler_ga4_dia=ler_ga4_dia,
                forget_key=forget_key,
                ga4_delete=ga4_delete,
                bq_delete=bq_delete,
            )
    except SyncError as e:
        print(f"ERRO: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"ERRO: falha inesperada ({type(e).__name__})", file=sys.stderr)
        return 1
    finally:
        if lock is not None:
            lock.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())

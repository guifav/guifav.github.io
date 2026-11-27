-- Marca permanente de remoção de um curso específico (épico #197, corte 1, `heating_sync.py --forget-curso <slug>`).
-- Impede o job diário do heating de rematerializar eventos ou a Inscrição de um curso apagado a partir do Firestore.
-- Não guarda o email em claro: só o HMAC-SHA256 (64 hex minúsculos) de `curso-esquecido-v1\n` + context + `\n` + email
-- normalizado, calculado com FORGET_HMAC_KEY. Vale só para o contexto do curso; não toca no contexto `site` nem em
-- outros cursos.
CREATE TABLE IF NOT EXISTS curso_esquecido (
  context TEXT NOT NULL
    CHECK (
      context GLOB 'curso:[a-z0-9]*'
      AND length(context) BETWEEN 7 AND 70
      AND substr(context, 7) NOT GLOB '*[^a-z0-9-]*'
    ),
  email_hmac TEXT NOT NULL
    CHECK (length(email_hmac) = 64 AND email_hmac NOT GLOB '*[^0-9a-f]*'),
  forgotten_at TEXT NOT NULL
    CHECK (forgotten_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),
  PRIMARY KEY (context, email_hmac)
);

// Origem da visita gravada com a conversão (issue #114): um rótulo, sem identificador. O navegador classifica a visita
// (assets/track.js, na primeira página da aba) e manda o resultado em campos ocultos do formulário; aqui o Worker
// revalida cada campo contra as mesmas listas e padrões e descarta o que não casar. Ficam de fora a URL completa de
// onde a pessoa veio, o termo buscado, gclid e afins, IP, user agent e qualquer id.
//
// As listas e os padrões são os mesmos do assets/track.js, da migração 0006 (CHECK no D1) e do
// blog-src/scripts/conversoes_sync.py. Mudar um deles é mudar os quatro.

export const CANAIS = ['busca', 'assistente', 'social', 'email', 'pago', 'outro_site', 'direto', 'desconhecida'] as const;
export const FONTES = ['google', 'bing', 'chatgpt', 'perplexity', 'claude', 'copilot', 'gemini', 'linkedin', 'youtube', 'github',
  'outro'] as const;
export type Canal = (typeof CANAIS)[number];
export type Fonte = (typeof FONTES)[number];

/** utm_source, utm_medium e utm_campaign: só valem se casarem com este padrão (o navegador já manda em minúsculas). */
export const UTM = /^[a-z0-9._-]{1,64}$/;
/**
 * Caminho da página de entrada, sem query nem fragmento: segmentos não vazios só com os caracteres dos caminhos do site
 * (nada de "//"), até ENTRADA_MAX caracteres.
 */
export const ENTRADA = /^\/(?:[A-Za-z0-9._-]+\/)*[A-Za-z0-9._-]*$/;
export const ENTRADA_MAX = 200;
export const entradaValida = (v: string | null): v is string => v !== null && v.length <= ENTRADA_MAX && ENTRADA.test(v);

export interface Origem {
  canal: Canal;
  /** Nulo quando não há serviço de onde a visita veio (direto ou desconhecida). */
  fonte: Fonte | null;
  utmSource: string | null;
  utmMedium: string | null;
  utmCampaign: string | null;
  entrada: string | null;
}

/** Nomes dos campos ocultos que o assets/track.js põe nos formulários de inscrição e de material. */
export const CAMPOS = {
  canal: 'origem_canal', fonte: 'origem_fonte', utmSource: 'utm_source', utmMedium: 'utm_medium', utmCampaign: 'utm_campaign',
  entrada: 'origem_entrada',
} as const;

const umDe = <T extends string>(lista: readonly T[], v: string | null): T | null => (lista as readonly string[]).includes(v ?? '') ? (v as T) : null;
const casa = (rx: RegExp, v: string | null) => (v !== null && rx.test(v) ? v : null);
const entradaOuNulo = (v: string | null) => (entradaValida(v) ? v : null);

/**
 * Origem dos campos do formulário. Cada campo vale sozinho: um UTM fora do padrão some sem levar os outros junto. Sem
 * canal válido (formulário sem JavaScript, storage bloqueado ou campo adulterado), o canal é "desconhecida".
 */
export function parseOrigem(form: URLSearchParams): Origem {
  const canal = umDe(CANAIS, form.get(CAMPOS.canal)) ?? 'desconhecida';
  const fonte = canal === 'direto' || canal === 'desconhecida' ? null : umDe(FONTES, form.get(CAMPOS.fonte));
  return {
    canal,
    fonte,
    utmSource: casa(UTM, form.get(CAMPOS.utmSource)),
    utmMedium: casa(UTM, form.get(CAMPOS.utmMedium)),
    utmCampaign: casa(UTM, form.get(CAMPOS.utmCampaign)),
    entrada: entradaOuNulo(form.get(CAMPOS.entrada)),
  };
}

// No link de confirmação (inscrição), a origem vai num parâmetro só, `o`, dentro da assinatura: os seis campos, nessa
// ordem, separados por "~", que não cabe em nenhum dos padrões acima. Nulo vira campo vazio.
const SEP = '~';

export function encodeOrigem(o: Origem): string {
  return [o.canal, o.fonte, o.utmSource, o.utmMedium, o.utmCampaign, o.entrada].map((v) => v ?? '').join(SEP);
}

/** Origem do parâmetro `o` de um link já verificado, ou null se o formato não casar (campo a campo, como no formulário). */
export function decodeOrigem(s: string): Origem | null {
  const p = s.split(SEP);
  if (p.length !== 6) return null;
  const [canal, fonte, utmSource, utmMedium, utmCampaign, entrada] = p.map((v) => (v === '' ? null : v));
  const c = umDe(CANAIS, canal);
  if (!c) return null;
  const o: Origem = {
    canal: c, fonte: c === 'direto' || c === 'desconhecida' ? null : umDe(FONTES, fonte), utmSource: casa(UTM, utmSource),
    utmMedium: casa(UTM, utmMedium), utmCampaign: casa(UTM, utmCampaign), entrada: entradaOuNulo(entrada),
  };
  return encodeOrigem(o) === s ? o : null; // só a forma canônica
}

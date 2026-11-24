// Esquema do frontmatter dos posts: o contrato com o importador da issue #19.
// Fica fora de content.config.ts para poder ser testado com node --test.
import { z } from 'astro/zod';
import { spotifyEmbedUrl } from './blog.ts';

export const SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

// Temas do blog (#104, aprovados pelo Gui em 23/11/2026 a partir do mapa de demanda da #112). O id é também
// o endereço da página do tema, /blog/tema/<id>/. Tema novo ou renomeado passa por aprovação do Gui.
export const TOPIC_IDS = [
  'ia-sobre-dados-da-empresa',
  'custos-roi-e-escolha-de-modelo',
  'agentes-de-ia-em-producao',
  'implantacao-de-ia-legados-e-arquitetura',
  'engenharia-de-software-com-ia',
] as const;
export type TopicId = (typeof TOPIC_IDS)[number];

// Teto do summary: 2 ou 3 frases que respondem à pergunta principal do post (#108).
export const SUMMARY_MAX = 500;

// Data ISO 8601 existente no calendário: "2026-11-10" ou "2026-11-10T12:00:00Z" (com fuso).
// new Date() sozinho normaliza dias inexistentes ("2026-02-30" vira 2 de março) sem erro.
const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?(?:Z|[+-](\d{2}):(\d{2})))?$/;

export function validIsoDate(s: string): boolean {
  const m = s.match(ISO_DATE);
  if (!m) return false;
  const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])];
  if (mo < 1 || mo > 12 || d < 1) return false;
  const daysInMonth = new Date(Date.UTC(y, mo, 0)).getUTCDate();
  if (d > daysInMonth) return false;
  const num = (v: string | undefined) => (v === undefined ? 0 : Number(v));
  if (num(m[4]) > 23 || num(m[5]) > 59 || num(m[6]) > 59) return false;
  if (num(m[7]) > 14 || num(m[8]) > 59) return false;
  return true;
}

// Data do YAML (o parser do Astro já entrega Date para valores sem aspas; a checagem antes do
// build, em postfiles.ts, valida o texto desses) ou string ISO 8601 existente no calendário.
// Nulo, booleano e número são rejeitados: z.coerce.date() sozinho os transformaria em 1970.
const postDate = z
  .union([z.date(), z.string().refine(validIsoDate, { message: 'data ISO 8601 inexistente ou fora do formato' })])
  .transform((v) => (v instanceof Date ? v : new Date(v)))
  .refine((d) => !Number.isNaN(d.getTime()), { message: 'data inválida' })
  .pipe(z.date());

export const postSchema = z
  .object({
    title: z.string().min(1),
    subtitle: z.string().optional(),
    description: z.string().min(1),
    date: postDate,
    updated: postDate.optional(),
    // Idêntico ao slug do Substack e ao nome do arquivo (<slug>.md); vira a URL /blog/<slug>/.
    slug: z.string().regex(SLUG),
    // Caminho dentro do blog, sem o base: "img/<slug>/capa.webp".
    cover: z
      .string()
      .regex(/^img\/[a-z0-9-]+\/[A-Za-z0-9._-]+$/)
      .optional(),
    type: z.enum(['newsletter', 'podcast']).default('newsletter'),
    // Só links de episódio do Spotify em https (os mesmos que viram o player embutido).
    spotify_url: z
      .url()
      .refine((u) => spotifyEmbedUrl(u) !== null, { message: 'spotify_url precisa ser link https de episódio do Spotify' })
      .optional(),
    index: z.boolean().default(true),
    newsletter: z.boolean().default(false),
    // Temas do post (#104). Opcional: post fora do foco fica sem tema. O primeiro é o tema principal.
    topics: z
      .array(z.enum(TOPIC_IDS))
      .min(1)
      .refine((t) => new Set(t).size === t.length, { message: 'tema repetido em topics' })
      .optional(),
    // Resumo no topo do post (#108). Independente da description e nunca a substitui (#103).
    summary: z
      .string()
      .refine((s) => s.trim().length > 0, { message: 'summary vazio' })
      .refine((s) => s.length <= SUMMARY_MAX, { message: `summary passa de ${SUMMARY_MAX} caracteres` })
      .optional(),
  })
  .refine((d) => d.type !== 'podcast' || Boolean(d.spotify_url), {
    message: 'podcast precisa de spotify_url',
    path: ['spotify_url'],
  })
  .refine((d) => !d.updated || d.updated >= d.date, {
    message: 'updated não pode ser anterior a date',
    path: ['updated'],
  });

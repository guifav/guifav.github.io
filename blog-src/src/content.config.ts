// Coleção de posts. O esquema é o contrato com o importador da issue #19.
import { defineCollection } from 'astro:content';
import { glob } from 'astro/loaders';
import { z } from 'astro/zod';

const SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

const posts = defineCollection({
  loader: glob({ pattern: '**/*.md', base: './src/content/posts' }),
  schema: z
    .object({
      title: z.string().min(1),
      subtitle: z.string().optional(),
      description: z.string().min(1),
      date: z.coerce.date(),
      updated: z.coerce.date().optional(),
      // O glob loader usa este campo como id; tem de ser idêntico ao slug do Substack.
      slug: z.string().regex(SLUG),
      // Caminho dentro do blog, sem o base: "img/<slug>/capa.webp".
      cover: z
        .string()
        .regex(/^img\/[a-z0-9-]+\/[A-Za-z0-9._-]+$/)
        .optional(),
      type: z.enum(['newsletter', 'podcast']).default('newsletter'),
      spotify_url: z.url().optional(),
      index: z.boolean().default(true),
      newsletter: z.boolean().default(false),
    })
    .refine((d) => d.type !== 'podcast' || Boolean(d.spotify_url), {
      message: 'podcast precisa de spotify_url',
      path: ['spotify_url'],
    })
    .refine((d) => !d.updated || d.updated >= d.date, {
      message: 'updated não pode ser anterior a date',
      path: ['updated'],
    }),
});

export const collections = { posts };

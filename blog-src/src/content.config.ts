// Coleção de posts. O esquema (contrato com o importador da issue #19) está em src/lib/schema.ts.
// Só arquivos direto na pasta: o nome do arquivo é o slug (ver src/lib/postfiles.ts).
import { defineCollection } from 'astro:content';
import { glob } from 'astro/loaders';
import { postSchema } from './lib/schema.ts';

const posts = defineCollection({
  loader: glob({ pattern: '*.md', base: './src/content/posts' }),
  schema: postSchema,
});

export const collections = { posts };

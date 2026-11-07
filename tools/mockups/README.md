# Mockups do portfolio

As imagens de `portfolio/img/*.webp` saem de duas fontes:

1. **Telas reais com dados fictícios**, copiadas do repositório do projeto ou capturadas da página pública.
2. **Telas recriadas** em HTML, em `portfolio/mockups/<slug>.html`, e renderizadas por `render.mjs`.

As recriações existem porque a maior parte dos sistemas roda atrás de login e com dados reais de pessoas e clientes. Cada uma reproduz a interface real do app a partir do código (layout, navegação, componentes, tokens de cor, tipografia, rótulos), com dados inventados.

## Telas reais

| Imagem | Origem |
|---|---|
| `pesquisa-saude.webp` | Tela de demonstração do próprio projeto (pesquisa com dados sintéticos), recortada em 1440x900, com o cabeçalho trocado por um nome neutro: `magick in.png -crop 1440x900+0+0 +repage` e depois `-draw` sobre o cabeçalho, `cwebp -q 82` |

Todas as demais são recriações.

## Renderizar

```bash
cd tools/mockups
npm install
npx playwright install chromium
npm run render              # todos
npm run render -- culkin    # um ou mais slugs
```

Requer `cwebp` (`brew install webp`). Todas as telas são de computador: viewport 1440x900 a 2x, salvo com 1600 px de largura.

## Regras para um mockup recriado

- **Arquivo único** em `portfolio/mockups/<slug>.html`, com `<meta name="mockup-viewport" content="1440x900">`. CSS inline. Ícones em SVG inline (os paths do lucide servem). A única requisição externa permitida é Google Fonts; o renderizador falha se qualquer requisição falhar.
- **Só a interface do app**, sem moldura de navegador nem sombra externa: a página do portfolio desenha a moldura. A tela ocupa exatamente o viewport, sem barra de rolagem visível e sem conteúdo cortado no meio de um componente.
- **Fiel ao app real**: a mesma estrutura de navegação, os mesmos nomes de menu, os mesmos componentes e o mesmo idioma da interface real. Leia o código (layout, sidebar, páginas, tema do Tailwind/shadcn, `globals.css`) antes de desenhar. O resultado tem de ficar igual ou mais bonito que o original; nunca mais pobre.
- **Dados 100% fictícios**: nomes de pessoas e empresas inventados e plausíveis para o domínio, e-mails em `example.com`, telefones com `0000`. Nenhum nome real de membro, cliente, prospect, colaborador ou paciente. Nenhuma métrica interna real (NRR, pipeline, receita, investimento, contagem de membros). Nenhum host interno (`*.mygri.com`, IPs), chave, token ou nome de banco.
- **Marca**: só o GRI Institute pode aparecer. Clientes não aparecem, nem pelo nome, nem pelo logo; o texto os descreve pelo setor (por exemplo, "instituto de pesquisa em saúde", "distribuidora").
- **Nomes de projeto**: os nomes internos usados no GRI e nos clientes não aparecem, nem na tela, nem no texto, nem no nome do arquivo. Use um nome descritivo (por exemplo, "Plataforma de inteligência comercial", "Radar de sinais de mercado").
- **Conferência**: depois de renderizar, abra o `.webp` e verifique texto estourando, sobreposição, alinhamento e contraste.

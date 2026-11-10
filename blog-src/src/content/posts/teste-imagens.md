---
title: "Post de teste: imagens"
subtitle: "Página provisória para conferir capa, figuras com legenda e imagens responsivas."
description: "Post de teste do blog guifav.dev, com capa, figura com legenda e imagem responsiva em duas larguras. Sai quando os artigos forem importados."
date: 2026-11-09T12:00:00Z
updated: 2026-11-10T12:00:00Z
slug: teste-imagens
cover: img/teste-imagens/capa-1456.webp
index: false
---

Este post confere como as imagens aparecem. O importador da issue #19 gera as figuras neste mesmo formato: WebP em 1456 e 728 pixels de largura, com `srcset`, largura e altura declaradas.

<figure>
  <img src="/blog/img/teste-imagens/fluxo-1456.webp" srcset="/blog/img/teste-imagens/fluxo-728.webp 728w, /blog/img/teste-imagens/fluxo-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="819" alt="Diagrama de teste com quatro etapas ligadas por setas" loading="lazy" decoding="async">
  <figcaption>Figura de teste: quatro etapas ligadas por setas.</figcaption>
</figure>

Um parágrafo depois da figura, para conferir o espaçamento.

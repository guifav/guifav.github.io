// Ordena a tabela do /tools/llm-ranking/ por clique no cabeçalho (issue #113). Camada sobre as linhas que o build gera:
// sem JavaScript, a tabela é a mesma, na ordem da fonte. Coluna numérica ordena pelo data-valor da célula; coluna de
// texto, pelo texto em português. O primeiro clique ordena do maior para o menor nas numéricas, exceto posição e faixa,
// e de A a Z nas de texto; o segundo inverte.
(function () {
  var tabela = document.getElementById('ranking');
  if (!tabela || !tabela.tBodies.length) return;
  var corpo = tabela.tBodies[0];
  var cabecalhos = Array.prototype.slice.call(tabela.tHead.rows[0].cells);
  var crescentePrimeiro = { 0: true, 1: true };

  function valor(linha, i, tipo) {
    var c = linha.cells[i];
    if (tipo === 'num') return parseFloat(c.getAttribute('data-valor'));
    return c.textContent.trim();
  }

  function ordenar(i, tipo, crescente) {
    var linhas = Array.prototype.slice.call(corpo.rows);
    linhas.sort(function (a, b) {
      var va = valor(a, i, tipo), vb = valor(b, i, tipo);
      var r = tipo === 'num' ? va - vb : va.localeCompare(vb, 'pt-BR');
      if (r === 0) r = parseFloat(a.cells[0].getAttribute('data-valor')) - parseFloat(b.cells[0].getAttribute('data-valor'));
      else if (!crescente) r = -r;
      return r;
    });
    linhas.forEach(function (l) { corpo.appendChild(l); });
    cabecalhos.forEach(function (th, j) {
      if (j === i) th.setAttribute('aria-sort', crescente ? 'ascending' : 'descending');
      else th.removeAttribute('aria-sort');
    });
  }

  cabecalhos.forEach(function (th, i) {
    var tipo = th.getAttribute('data-ordem');
    if (!tipo) return;
    var botao = document.createElement('button');
    botao.type = 'button';
    botao.className = 'ordenar';
    botao.textContent = th.textContent;
    th.textContent = '';
    th.appendChild(botao);
    botao.addEventListener('click', function () {
      var atual = th.getAttribute('aria-sort');
      var crescente = atual ? atual !== 'ascending' : (tipo === 'texto' || crescentePrimeiro[i] === true);
      ordenar(i, tipo, crescente);
    });
  });
  cabecalhos[0].setAttribute('aria-sort', 'ascending');
})();

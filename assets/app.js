/* ==========================================================================
   Painel de Preços — site público (só consulta).
   Lê data/precos.json e data/historico.json (ficheiros estáticos, gerados
   localmente pelo painel de administração e publicados via `git push`).
   ========================================================================== */

const estado = {
  precos: [],
  marcaAtiva: "",
  modeloAtivo: "",
};

const els = {
  busca: document.getElementById("busca"),
  filtroMarca: document.getElementById("filtro-marca"),
  filtroModelo: document.getElementById("filtro-modelo"),
  corpoTabela: document.getElementById("corpo-tabela"),
  contagem: document.getElementById("contagem"),
  geradoEm: document.getElementById("gerado-em"),
};

async function carregar() {
  try {
    const resp = await fetch("data/precos.json", { cache: "no-store" });
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    const dados = await resp.json();

    // Pré-computa os campos de pesquisa normalizados uma única vez
    estado.precos = prepararCache(dados.precos || []);

    if (els.geradoEm && dados.gerado_em) {
      els.geradoEm.textContent = "Dados de " + formatarData(dados.gerado_em);
    }
    popularFiltros();
    render();
  } catch (e) {
    els.corpoTabela.innerHTML =
      '<tr><td colspan="5"><div class="empty-state">Não foi possível carregar os preços agora. ' +
      "Verifica se o ficheiro data/precos.json existe e foi publicado.</div></td></tr>";
    console.error(e);
  }
}

function popularFiltros() {
  els.filtroMarca.innerHTML =
    '<option value="">Todas as marcas</option>' +
    marcasUnicas(estado.precos)
      .map((m) => `<option value="${escapeHtml(m)}">${escapeHtml(m)}</option>`)
      .join("");
}

function atualizarFiltroModelos() {
  const modelos = modelosUnicos(estado.precos, estado.marcaAtiva);
  els.filtroModelo.innerHTML =
    '<option value="">Todos os modelos</option>' +
    modelos
      .map((m) => `<option value="${escapeHtml(m)}">${escapeHtml(m)}</option>`)
      .join("");
}

function render() {
  const resultado = filtrarPrecos(estado.precos, {
    texto: els.busca.value,
    marca: estado.marcaAtiva,
    modelo: estado.modeloAtivo,
  });

  els.contagem.textContent =
    resultado.length === 1 ? "1 resultado" : `${resultado.length} resultados`;

  if (resultado.length === 0) {
    els.corpoTabela.innerHTML =
      '<tr><td colspan="5"><div class="empty-state">Sem resultados para esta pesquisa.</div></td></tr>';
    return;
  }

  els.corpoTabela.innerHTML = resultado
    .map(
      (p) => `
      <tr>
        <td>${escapeHtml(p.marca)}</td>
        <td>${escapeHtml(p.modelo)}</td>
        <td>${escapeHtml(p.servico)}</td>
        <td><span class="badge ${classeBadgeQualidade(p.qualidade)}">${escapeHtml(p.qualidade)}</span></td>
        <td class="num price">${formatarPreco(p.preco_eur)}</td>
      </tr>`
    )
    .join("");
}

// Debounce de 180ms: evita travar enquanto o utilizador ainda está a escrever
els.busca.addEventListener("input", debounce(render, 180));

els.filtroMarca.addEventListener("change", () => {
  estado.marcaAtiva = els.filtroMarca.value;
  estado.modeloAtivo = "";
  atualizarFiltroModelos();
  render();
});

els.filtroModelo.addEventListener("change", () => {
  estado.modeloAtivo = els.filtroModelo.value;
  render();
});

carregar();

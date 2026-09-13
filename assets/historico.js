/* ==========================================================================
   Painel de Preços — página de histórico (site público).
   Lê data/historico.json e mostra 3 secções: execuções de sincronização,
   alterações de preço detetadas por scraping, e edições manuais.
   ========================================================================== */

async function carregarHistorico() {
  const secExecucoes = document.getElementById("sec-execucoes");
  const secAlteracoes = document.getElementById("sec-alteracoes");
  const secEdicoes = document.getElementById("sec-edicoes");

  try {
    const resp = await fetch("data/historico.json", { cache: "no-store" });
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    const dados = await resp.json();

    renderExecucoes(secExecucoes, dados.execucoes || []);
    renderAlteracoes(secAlteracoes, dados.alteracoes_preco || []);
    renderEdicoes(secEdicoes, dados.edicoes_manuais || []);
  } catch (e) {
    document.getElementById("historico-root").innerHTML =
      '<div class="empty-state">Não foi possível carregar o histórico. ' +
      "Verifica se o ficheiro data/historico.json existe e foi publicado.</div>";
    console.error(e);
  }
}

function renderExecucoes(container, lista) {
  if (lista.length === 0) {
    container.innerHTML = '<div class="empty-state">Ainda não há sincronizações registadas.</div>';
    return;
  }
  const linhas = lista
    .map((e) => {
      const duracao =
        e.data_inicio && e.data_fim
          ? Math.round((new Date(e.data_fim.replace(" ", "T")) - new Date(e.data_inicio.replace(" ", "T"))) / 60000) + " min"
          : "-";
      const estadoTxt = e.estado === "concluido" ? "Concluída" : e.estado === "erro" ? "Erro" : "Em curso";
      return `<tr>
        <td>${formatarData(e.data_inicio)}</td>
        <td>${e.modo === "so-precos" ? "Só preços" : "Completa"}</td>
        <td class="num">${e.modelos_novos ?? 0}</td>
        <td class="num">${e.precos_novos ?? 0}</td>
        <td class="num">${e.precos_alterados ?? 0}</td>
        <td class="num">${e.inativados ?? 0}</td>
        <td>${duracao}</td>
        <td>${estadoTxt}</td>
      </tr>`;
    })
    .join("");
  container.innerHTML = `
    <table>
      <thead><tr>
        <th>Data</th><th>Modo</th><th class="num">Modelos novos</th>
        <th class="num">Preços novos</th><th class="num">Alterados</th>
        <th class="num">Inativados</th><th>Duração</th><th>Estado</th>
      </tr></thead>
      <tbody>${linhas}</tbody>
    </table>`;
}

function renderAlteracoes(container, lista) {
  if (lista.length === 0) {
    container.innerHTML = '<div class="empty-state">Ainda não há alterações de preço registadas.</div>';
    return;
  }
  const linhas = lista
    .map((a) => {
      const subiu = a.diferenca > 0;
      const sinal = subiu ? "+" : "";
      return `<tr>
        <td>${formatarData(a.data)}</td>
        <td>${escapeHtml(a.marca)}</td>
        <td>${escapeHtml(a.modelo)}</td>
        <td>${escapeHtml(a.servico)} <span class="badge ${classeBadgeQualidade(a.qualidade)}">${escapeHtml(a.qualidade)}</span></td>
        <td class="num">${formatarPreco(a.preco_antigo)}</td>
        <td class="num">${formatarPreco(a.preco_novo)}</td>
        <td class="num ${subiu ? "up" : "down"}">${sinal}${formatarPreco(a.diferenca)}</td>
      </tr>`;
    })
    .join("");
  container.innerHTML = `
    <table>
      <thead><tr>
        <th>Data</th><th>Marca</th><th>Modelo</th><th>Serviço</th>
        <th class="num">Preço antigo</th><th class="num">Preço novo</th><th class="num">Diferença</th>
      </tr></thead>
      <tbody>${linhas}</tbody>
    </table>`;
}

function renderEdicoes(container, lista) {
  if (lista.length === 0) {
    container.innerHTML = '<div class="empty-state">Ainda não há edições manuais registadas.</div>';
    return;
  }
  const rotulos = { criado: "Criado", editado: "Editado", apagado: "Apagado" };
  const linhas = lista
    .map(
      (e) => `<tr>
        <td>${formatarData(e.data)}</td>
        <td>${rotulos[e.acao] || e.acao}</td>
        <td>${escapeHtml(e.marca)}</td>
        <td>${escapeHtml(e.modelo)}</td>
        <td>${escapeHtml(e.servico)} <span class="badge ${classeBadgeQualidade(e.qualidade)}">${escapeHtml(e.qualidade)}</span></td>
        <td class="num">${e.preco_antigo != null ? formatarPreco(e.preco_antigo) : "-"}</td>
        <td class="num">${e.preco_novo != null ? formatarPreco(e.preco_novo) : "-"}</td>
      </tr>`
    )
    .join("");
  container.innerHTML = `
    <table>
      <thead><tr>
        <th>Data</th><th>Ação</th><th>Marca</th><th>Modelo</th><th>Serviço</th>
        <th class="num">Preço antigo</th><th class="num">Preço novo</th>
      </tr></thead>
      <tbody>${linhas}</tbody>
    </table>`;
}

/* Nota: este ficheiro define as funções de renderização (renderExecucoes,
   renderAlteracoes, renderEdicoes) e a função carregarHistorico() usada pelo
   site público. É incluído também no painel de admin, mas aí a função
   carregarHistoricoAdmin() (definida em admin.js) é chamada em vez desta —
   por isso NÃO invocamos carregarHistorico() automaticamente aqui; cada
   página decide explicitamente quando carregar. */


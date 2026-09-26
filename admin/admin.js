/* ==========================================================================
   Painel de Preços — Administração local.
   Fala com o servidor Flask local (server.py) via fetch para editar/apagar/
   adicionar reparações e disparar a atualização (scraping) com progresso.
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
  modalRoot: document.getElementById("modal-root"),
  toastRoot: document.getElementById("toast-root"),
  tabPrecos: document.getElementById("tab-precos"),
  tabHistorico: document.getElementById("tab-historico"),
  paginaPrecos: document.getElementById("pagina-precos"),
  paginaHistorico: document.getElementById("pagina-historico"),
  btnAtualizar: document.getElementById("btn-atualizar"),
  btnPublicar: document.getElementById("btn-publicar"),
  btnRegistar: document.getElementById("btn-registar"),
};

const QUALIDADES_SUGERIDAS = ["Original", "Compatível (iServices)", "Compatível", "Padrão"];

/* ---------------------------------------------------------------------- */
/* Carregar / renderizar tabela de preços                                  */
/* ---------------------------------------------------------------------- */

async function carregarPrecos() {
  try {
    const resp = await fetch("/api/precos", { cache: "no-store" });
    // Pré-computa os campos de pesquisa normalizados uma única vez
    estado.precos = prepararCache(await resp.json());
    popularFiltroMarca();
    render();
  } catch (e) {
    toast("Não foi possível ligar ao servidor local. Confirma que o server.py está a correr.", "erro");
    console.error(e);
  }
}

function popularFiltroMarca() {
  const atual = els.filtroMarca.value;
  els.filtroMarca.innerHTML =
    '<option value="">Todas as marcas</option>' +
    marcasUnicas(estado.precos).map((m) => `<option value="${escapeHtml(m)}">${escapeHtml(m)}</option>`).join("");
  els.filtroMarca.value = atual;
}

function atualizarFiltroModelos() {
  const modelos = modelosUnicos(estado.precos, estado.marcaAtiva);
  els.filtroModelo.innerHTML =
    '<option value="">Todos os modelos</option>' +
    modelos.map((m) => `<option value="${escapeHtml(m)}">${escapeHtml(m)}</option>`).join("");
}

function render() {
  const resultado = filtrarPrecos(estado.precos, {
    texto: els.busca.value,
    marca: estado.marcaAtiva,
    modelo: estado.modeloAtivo,
  });

  els.contagem.textContent = resultado.length === 1 ? "1 resultado" : `${resultado.length} resultados`;

  if (resultado.length === 0) {
    els.corpoTabela.innerHTML = '<tr><td colspan="6"><div class="empty-state">Sem resultados.</div></td></tr>';
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
        <td class="actions">
          <div class="actions-cell">
            <button class="icon-btn icon-btn--edit" title="Editar" data-editar="${p.id}">&#9998;</button>
            <button class="icon-btn icon-btn--delete" title="Apagar" data-apagar="${p.id}">&times;</button>
          </div>
        </td>
      </tr>`
    )
    .join("");
}

els.corpoTabela.addEventListener("click", (ev) => {
  const btnEditar = ev.target.closest("[data-editar]");
  const btnApagar = ev.target.closest("[data-apagar]");
  if (btnEditar) {
    const item = estado.precos.find((p) => p.id === Number(btnEditar.dataset.editar));
    if (item) modalEditar(item);
  }
  if (btnApagar) {
    const item = estado.precos.find((p) => p.id === Number(btnApagar.dataset.apagar));
    if (item) modalApagar(item);
  }
});

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

/* ---------------------------------------------------------------------- */
/* Modais genéricos                                                        */
/* ---------------------------------------------------------------------- */

function abrirModal(html) {
  els.modalRoot.innerHTML = `<div class="modal-overlay" id="overlay-modal">${html}</div>`;
  document.getElementById("overlay-modal").addEventListener("click", (ev) => {
    if (ev.target.id === "overlay-modal") fecharModal();
  });
}

function fecharModal() {
  els.modalRoot.innerHTML = "";
}

function toast(msg, tipo = "sucesso") {
  const el = document.createElement("div");
  el.className = `toast toast--${tipo}`;
  el.textContent = msg;
  els.toastRoot.appendChild(el);
  setTimeout(() => el.remove(), 4200);
}

function datalistQualidades(idInput) {
  return `<input list="lista-qualidades" id="${idInput}" type="text" autocomplete="off" />
    <datalist id="lista-qualidades">
      ${QUALIDADES_SUGERIDAS.map((q) => `<option value="${escapeHtml(q)}"></option>`).join("")}
    </datalist>`;
}

/* ---------------------------------------------------------------------- */
/* Modal: Editar                                                           */
/* ---------------------------------------------------------------------- */

function modalEditar(item) {
  abrirModal(`
    <div class="modal">
      <h3>Editar reparação</h3>
      <p class="desc">${escapeHtml(item.marca)} · ${escapeHtml(item.modelo)}</p>

      <div class="field">
        <label>Serviço</label>
        <input type="text" id="edit-servico" value="${escapeHtml(item.servico)}" />
      </div>
      <div class="field">
        <label>Qualidade</label>
        ${datalistQualidades("edit-qualidade")}
      </div>
      <div class="field">
        <label>Preço (€)</label>
        <input type="number" id="edit-preco" step="0.01" min="0" value="${item.preco_eur}" />
        <div class="field-error" id="edit-erro"></div>
      </div>

      <div class="actions">
        <button class="btn btn--ghost" id="edit-cancelar">Cancelar</button>
        <button class="btn btn--primary" id="edit-rever">Rever alterações</button>
      </div>
    </div>
  `);
  document.getElementById("edit-qualidade").value = item.qualidade;
  document.getElementById("edit-cancelar").addEventListener("click", fecharModal);
  document.getElementById("edit-rever").addEventListener("click", () => {
    const servico = document.getElementById("edit-servico").value.trim();
    const qualidade = document.getElementById("edit-qualidade").value.trim();
    const precoStr = document.getElementById("edit-preco").value;
    const preco = parseFloat(precoStr.replace(",", "."));
    const erroEl = document.getElementById("edit-erro");

    if (!servico || !qualidade || Number.isNaN(preco) || preco < 0) {
      erroEl.textContent = "Preenche o serviço, a qualidade e um preço válido.";
      erroEl.style.display = "block";
      return;
    }
    modalConfirmarEdicao(item, { servico, qualidade, preco_eur: preco });
  });
}

function modalConfirmarEdicao(item, novo) {
  const mudouPreco = Math.abs(novo.preco_eur - item.preco_eur) > 0.001;
  abrirModal(`
    <div class="modal">
      <h3>Confirmar alteração</h3>
      <p class="desc">${escapeHtml(item.marca)} · ${escapeHtml(item.modelo)} · ${escapeHtml(novo.servico)}</p>
      <div class="diff-preview">
        ${mudouPreco
          ? `<span class="old">${formatarPreco(item.preco_eur)}</span><span class="new">${formatarPreco(novo.preco_eur)}</span>`
          : `<span>${formatarPreco(novo.preco_eur)}</span> (sem alteração de preço)`}
        <br/><span style="color:var(--text-faint)">Qualidade: ${escapeHtml(novo.qualidade)}</span>
      </div>
      <div class="actions">
        <button class="btn btn--ghost" id="conf-cancelar">Cancelar</button>
        <button class="btn btn--primary" id="conf-guardar">Guardar alteração</button>
      </div>
    </div>
  `);
  document.getElementById("conf-cancelar").addEventListener("click", fecharModal);
  document.getElementById("conf-guardar").addEventListener("click", async () => {
    try {
      const resp = await fetch(`/api/precos/${item.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(novo),
      });
      const dados = await resp.json();
      if (!resp.ok || !dados.ok) throw new Error(dados.erro || "Erro desconhecido.");
      fecharModal();
      toast("Reparação atualizada.");
      carregarPrecos();
    } catch (e) {
      toast("Erro ao guardar: " + e.message, "erro");
    }
  });
}

/* ---------------------------------------------------------------------- */
/* Modal: Apagar                                                           */
/* ---------------------------------------------------------------------- */

function modalApagar(item) {
  abrirModal(`
    <div class="modal modal--danger">
      <h3>Apagar reparação?</h3>
      <p class="desc">
        ${escapeHtml(item.marca)} · ${escapeHtml(item.modelo)} · ${escapeHtml(item.servico)}
        (${escapeHtml(item.qualidade)}) — ${formatarPreco(item.preco_eur)}
      </p>
      <p class="desc">Esta ação não pode ser desfeita a partir daqui (fica registada no histórico, mas deixa de aparecer na lista).</p>
      <div class="actions">
        <button class="btn btn--ghost" id="del-cancelar">Cancelar</button>
        <button class="btn btn--danger" id="del-confirmar">Apagar definitivamente</button>
      </div>
    </div>
  `);
  document.getElementById("del-cancelar").addEventListener("click", fecharModal);
  document.getElementById("del-confirmar").addEventListener("click", async () => {
    try {
      const resp = await fetch(`/api/precos/${item.id}`, { method: "DELETE" });
      const dados = await resp.json();
      if (!resp.ok || !dados.ok) throw new Error(dados.erro || "Erro desconhecido.");
      fecharModal();
      toast("Reparação apagada.");
      carregarPrecos();
    } catch (e) {
      toast("Erro ao apagar: " + e.message, "erro");
    }
  });
}

/* ---------------------------------------------------------------------- */
/* Modal: Adicionar (+ Registar)                                           */
/* ---------------------------------------------------------------------- */

function modalAdicionar() {
  abrirModal(`
    <div class="modal">
      <h3>Registar nova reparação</h3>
      <p class="desc">Todos os campos são obrigatórios.</p>

      <div class="field"><label>Marca</label><input type="text" id="add-marca" placeholder="ex: Apple" /></div>
      <div class="field"><label>Sub-categoria</label><input type="text" id="add-subcat" placeholder="ex: Iphone" /></div>
      <div class="field"><label>Modelo</label><input type="text" id="add-modelo" placeholder="ex: iPhone 16" /></div>
      <div class="field"><label>Serviço</label><input type="text" id="add-servico" placeholder="ex: Vidro / Ecrã / Touch" /></div>
      <div class="field"><label>Qualidade</label>${datalistQualidades("add-qualidade")}</div>
      <div class="field">
        <label>Preço (€)</label>
        <input type="number" id="add-preco" step="0.01" min="0" placeholder="ex: 99.95" />
        <div class="field-error" id="add-erro"></div>
      </div>

      <div class="actions">
        <button class="btn btn--ghost" id="add-cancelar">Cancelar</button>
        <button class="btn btn--primary" id="add-rever">Rever e registar</button>
      </div>
    </div>
  `);
  document.getElementById("add-cancelar").addEventListener("click", fecharModal);
  document.getElementById("add-rever").addEventListener("click", () => {
    const campos = {
      marca: document.getElementById("add-marca").value.trim(),
      sub_categoria: document.getElementById("add-subcat").value.trim(),
      modelo: document.getElementById("add-modelo").value.trim(),
      servico: document.getElementById("add-servico").value.trim(),
      qualidade: document.getElementById("add-qualidade").value.trim(),
    };
    const preco = parseFloat(document.getElementById("add-preco").value.replace(",", "."));
    const erroEl = document.getElementById("add-erro");
    const vazio = Object.values(campos).some((v) => !v);

    if (vazio || Number.isNaN(preco) || preco < 0) {
      erroEl.textContent = "Preenche todos os campos com um preço válido.";
      erroEl.style.display = "block";
      return;
    }
    modalConfirmarAdicao({ ...campos, preco_eur: preco });
  });
}

function modalConfirmarAdicao(novo) {
  abrirModal(`
    <div class="modal">
      <h3>Confirmar registo</h3>
      <div class="diff-preview">
        ${escapeHtml(novo.marca)} · ${escapeHtml(novo.sub_categoria)} · ${escapeHtml(novo.modelo)}<br/>
        ${escapeHtml(novo.servico)} — <span class="badge ${classeBadgeQualidade(novo.qualidade)}">${escapeHtml(novo.qualidade)}</span><br/>
        <span class="new">${formatarPreco(novo.preco_eur)}</span>
      </div>
      <div class="actions">
        <button class="btn btn--ghost" id="conf-cancelar">Cancelar</button>
        <button class="btn btn--primary" id="conf-registar">Registar</button>
      </div>
    </div>
  `);
  document.getElementById("conf-cancelar").addEventListener("click", fecharModal);
  document.getElementById("conf-registar").addEventListener("click", async () => {
    try {
      const resp = await fetch("/api/precos", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(novo),
      });
      const dados = await resp.json();
      if (!resp.ok || !dados.ok) throw new Error(dados.erro || "Erro desconhecido.");
      fecharModal();
      toast("Reparação registada.");
      carregarPrecos();
    } catch (e) {
      toast("Erro ao registar: " + e.message, "erro");
    }
  });
}

els.btnRegistar.addEventListener("click", modalAdicionar);

/* ---------------------------------------------------------------------- */
/* Atualizar preços (scraping) com barra de progresso                      */
/* ---------------------------------------------------------------------- */

function modalConfirmarScrape() {
  abrirModal(`
    <div class="modal">
      <h3>Atualizar preços agora?</h3>
      <p class="desc">
        Isto vai percorrer o site da iServices para atualizar preços e detetar
        modelos novos. Pode demorar vários minutos — não feches esta janela
        enquanto estiver a correr.
      </p>
      <div class="actions">
        <button class="btn btn--ghost" id="scrape-cancelar">Cancelar</button>
        <button class="btn btn--amber" id="scrape-confirmar">Começar atualização</button>
      </div>
    </div>
  `);
  document.getElementById("scrape-cancelar").addEventListener("click", fecharModal);
  document.getElementById("scrape-confirmar").addEventListener("click", () => {
    fecharModal();
    iniciarScrape();
  });
}

async function iniciarScrape() {
  try {
    const resp = await fetch("/api/scrape/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ modo: "completo" }),
    });
    const dados = await resp.json();
    if (!resp.ok || !dados.ok) throw new Error(dados.erro || "Não foi possível iniciar.");
    mostrarProgresso();
  } catch (e) {
    toast("Erro ao iniciar atualização: " + e.message, "erro");
  }
}

function mostrarProgresso() {
  els.modalRoot.innerHTML = `
    <div class="progress-overlay">
      <div class="progress-card">
        <h3>A atualizar preços…</h3>
        <p id="progress-msg">A iniciar…</p>
        <div class="progress-track"><div class="progress-fill" id="progress-fill"></div></div>
        <div class="progress-numbers" id="progress-numbers"></div>
      </div>
    </div>`;
  pollProgresso();
}

function pollProgresso() {
  const intervalo = setInterval(async () => {
    try {
      const resp = await fetch("/api/scrape/status", { cache: "no-store" });
      const p = await resp.json();
      const msgEl = document.getElementById("progress-msg");
      const fillEl = document.getElementById("progress-fill");
      const numEl = document.getElementById("progress-numbers");
      if (!msgEl) { clearInterval(intervalo); return; } // modal foi fechado entretanto

      msgEl.textContent = p.mensagem || "A processar…";
      if (p.total > 0) {
        const pct = Math.min(100, Math.round((p.atual / p.total) * 100));
        fillEl.style.width = pct + "%";
        numEl.textContent = `${p.atual} / ${p.total} (${pct}%)`;
      } else {
        fillEl.style.width = "8%";
        numEl.textContent = "A mapear o site…";
      }

      if (!p.a_correr) {
        clearInterval(intervalo);
        fecharModal();
        if (p.erro) {
          toast("A atualização terminou com um erro: " + p.erro, "erro");
        } else {
          toast("Atualização concluída.");
        }
        carregarPrecos();
      }
    } catch (e) {
      clearInterval(intervalo);
      console.error(e);
    }
  }, 1400);
}

els.btnAtualizar.addEventListener("click", modalConfirmarScrape);

/* ---------------------------------------------------------------------- */
/* Publicar no GitHub                                                      */
/* ---------------------------------------------------------------------- */

els.btnPublicar.addEventListener("click", async () => {
  const btn = els.btnPublicar;
  const textoOriginal = btn.textContent;

  // Confirmação simples antes de publicar
  if (!confirm("Publicar as alterações no GitHub?\nO site público fica atualizado em 1-2 minutos.")) return;

  btn.disabled = true;
  btn.textContent = "A publicar…";

  try {
    const resp = await fetch("/api/publicar", { method: "POST" });
    const dados = await resp.json();

    if (dados.ok) {
      toast(dados.mensagem, "sucesso");
    } else {
      toast("Erro ao publicar: " + dados.mensagem, "erro");
    }
  } catch (e) {
    toast("Não foi possível contactar o servidor. Confirma que o admin está a correr.", "erro");
  } finally {
    btn.disabled = false;
    btn.textContent = textoOriginal;
  }
});

/* ---------------------------------------------------------------------- */
/* Separador Histórico                                                     */
/* ---------------------------------------------------------------------- */

function mudarPagina(pagina) {
  const emPrecos = pagina === "precos";
  els.paginaPrecos.style.display = emPrecos ? "" : "none";
  els.paginaHistorico.style.display = emPrecos ? "none" : "";
  els.tabPrecos.classList.toggle("active", emPrecos);
  els.tabHistorico.classList.toggle("active", !emPrecos);
  if (!emPrecos) carregarHistoricoAdmin();
}

async function carregarHistoricoAdmin() {
  const secExec = document.getElementById("sec-execucoes");
  const secAlt = document.getElementById("sec-alteracoes");
  const secEd = document.getElementById("sec-edicoes");
  try {
    const resp = await fetch("/api/historico", { cache: "no-store" });
    const dados = await resp.json();
    renderExecucoes(secExec, dados.execucoes || []);
    renderAlteracoes(secAlt, dados.alteracoes_preco || []);
    renderEdicoes(secEd, dados.edicoes_manuais || []);
  } catch (e) {
    console.error(e);
  }
}

els.tabPrecos.addEventListener("click", (ev) => { ev.preventDefault(); mudarPagina("precos"); });
els.tabHistorico.addEventListener("click", (ev) => { ev.preventDefault(); mudarPagina("historico"); });

/* ---------------------------------------------------------------------- */

carregarPrecos();

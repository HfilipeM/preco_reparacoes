/* ==========================================================================
   Admin Online — comunica com a GitHub API diretamente do browser.

   SEGURANÇA:
   - O token é lido do localStorage do browser — NUNCA está no código.
   - O código pode estar num repositório público sem qualquer risco.
   - Quem vê o código vê apenas a lógica, nunca o token.
   ========================================================================== */

const REPO_OWNER = "HfilipeM";
const REPO_NAME  = "preco_reparacoes";
const BRANCH     = "main";
const FILE_PATH  = "data/precos.json";
const TOKEN_KEY  = "gh_admin_token";   // chave no localStorage

/* ---------------------------------------------------------------------- */
/* Estado da aplicação                                                     */
/* ---------------------------------------------------------------------- */

const estado = {
  precos:      [],   // array completo em memória
  fileSha:     "",   // SHA do ficheiro no GitHub (necessário para atualizar)
  geradoEm:    null,
  marcaAtiva:  "",
  modeloAtivo: "",
  alterado:    false, // há alterações por publicar?
};

/* ---------------------------------------------------------------------- */
/* Referências DOM                                                         */
/* ---------------------------------------------------------------------- */

const els = {
  ecraConfig:   document.getElementById("ecra-config"),
  ecraApp:      document.getElementById("ecra-app"),
  inputToken:   document.getElementById("input-token"),
  btnGuardarCfg:document.getElementById("btn-guardar-config"),

  busca:        document.getElementById("busca"),
  filtroMarca:  document.getElementById("filtro-marca"),
  filtroModelo: document.getElementById("filtro-modelo"),
  corpoTabela:  document.getElementById("corpo-tabela"),
  contagem:     document.getElementById("contagem"),
  modalRoot:    document.getElementById("modal-root"),
  toastRoot:    document.getElementById("toast-root"),

  btnPublicar:  document.getElementById("btn-publicar"),
  btnRegistar:  document.getElementById("btn-registar"),
  btnSair:      document.getElementById("btn-sair"),

  statusDot:    document.getElementById("status-dot"),
  statusTexto:  document.getElementById("status-texto"),
  statusSha:    document.getElementById("status-sha"),
};

const QUALIDADES = ["Original", "Compatível (iServices)", "Compatível", "Padrão"];

/* ---------------------------------------------------------------------- */
/* Token — guardar/ler/apagar no localStorage                             */
/* ---------------------------------------------------------------------- */

function lerToken()    { return localStorage.getItem(TOKEN_KEY) || ""; }
function guardarToken(t){ localStorage.setItem(TOKEN_KEY, t.trim()); }
function apagarToken() { localStorage.removeItem(TOKEN_KEY); }

/* ---------------------------------------------------------------------- */
/* GitHub API                                                              */
/* ---------------------------------------------------------------------- */

async function ghFetch(path, opts = {}) {
  const token = lerToken();
  const resp = await fetch(`https://api.github.com${path}`, {
    ...opts,
    headers: {
      "Authorization": `Bearer ${token}`,
      "Accept": "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      ...(opts.headers || {}),
    },
  });
  return resp;
}

/** Lê o precos.json do GitHub e devolve { precos, sha, geradoEm } */
async function ghLerPrecos() {
  const resp = await ghFetch(`/repos/${REPO_OWNER}/${REPO_NAME}/contents/${FILE_PATH}?ref=${BRANCH}`);
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.message || `HTTP ${resp.status}`);
  }
  const meta = await resp.json();
  const conteudo = JSON.parse(atob(meta.content.replace(/\n/g, "")));
  return {
    sha:      meta.sha,
    precos:   conteudo.precos || [],
    geradoEm: conteudo.gerado_em || null,
  };
}

/** Escreve o precos.json atualizado no GitHub via commit */
async function ghEscreverPrecos(precos, sha, mensagemCommit) {
  const agora = new Date().toISOString().replace("T", " ").substring(0, 19);
  const payload = {
    gerado_em:    agora,
    total_precos: precos.length,
    precos,
  };
  const conteudoBase64 = btoa(unescape(encodeURIComponent(JSON.stringify(payload, null, 2))));
  const resp = await ghFetch(`/repos/${REPO_OWNER}/${REPO_NAME}/contents/${FILE_PATH}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message: mensagemCommit,
      content: conteudoBase64,
      sha,
      branch: BRANCH,
    }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.message || `HTTP ${resp.status}`);
  }
  const resultado = await resp.json();
  return resultado.content.sha; // SHA novo para a próxima operação
}

/* ---------------------------------------------------------------------- */
/* Arranque                                                                */
/* ---------------------------------------------------------------------- */

function init() {
  const token = lerToken();
  if (!token) {
    mostrarEcraConfig();
  } else {
    mostrarApp();
    carregarPrecos();
  }
}

function mostrarEcraConfig() {
  els.ecraConfig.style.display = "flex";
  els.ecraApp.style.display    = "none";
}

function mostrarApp() {
  els.ecraConfig.style.display = "none";
  els.ecraApp.style.display    = "block";
}

els.btnGuardarCfg.addEventListener("click", () => {
  const token = els.inputToken.value.trim();
  if (!token.startsWith("gh")) {
    alert("O token não parece válido — deve começar com 'ghp_' ou 'github_pat_'.");
    return;
  }
  guardarToken(token);
  mostrarApp();
  carregarPrecos();
});

els.inputToken.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter") els.btnGuardarCfg.click();
});

els.btnSair.addEventListener("click", () => {
  if (!confirm("Apagar token e sair?\nTerás de inserir o token novamente da próxima vez.")) return;
  apagarToken();
  location.reload();
});

/* ---------------------------------------------------------------------- */
/* Carregar precos do GitHub                                               */
/* ---------------------------------------------------------------------- */

async function carregarPrecos() {
  setStatus("espera", "A carregar do GitHub…");
  els.corpoTabela.innerHTML = '<tr><td colspan="6"><div class="empty-state"><span class="spinner"></span>A carregar…</div></td></tr>';

  try {
    const { sha, precos, geradoEm } = await ghLerPrecos();
    estado.fileSha  = sha;
    estado.geradoEm = geradoEm;
    estado.precos   = prepararCache(precos);
    estado.alterado = false;
    atualizarBtnPublicar();
    popularFiltroMarca();
    render();
    setStatus("ok", `${precos.length} preços · atualizado ${geradoEm ? formatarData(geradoEm) : "—"}`);
    els.statusSha.textContent = `SHA: ${sha.substring(0, 7)}`;
  } catch (e) {
    setStatus("erro", "Erro ao carregar: " + e.message);
    els.corpoTabela.innerHTML = `<tr><td colspan="6"><div class="empty-state">Não foi possível carregar os preços.<br/><br/>${escapeHtml(e.message)}</div></td></tr>`;
    if (e.message.includes("401") || e.message.includes("Bad credentials")) {
      toast("Token inválido ou expirado. Clica em 'Sair' e insere um novo token.", "erro");
    }
  }
}

/* ---------------------------------------------------------------------- */
/* Filtros e render                                                        */
/* ---------------------------------------------------------------------- */

function popularFiltroMarca() {
  const atual = els.filtroMarca.value;
  els.filtroMarca.innerHTML =
    '<option value="">Todas as marcas</option>' +
    marcasUnicas(estado.precos).map(m => `<option value="${escapeHtml(m)}">${escapeHtml(m)}</option>`).join("");
  els.filtroMarca.value = atual;
}

function atualizarFiltroModelos() {
  const modelos = modelosUnicos(estado.precos, estado.marcaAtiva);
  els.filtroModelo.innerHTML =
    '<option value="">Todos os modelos</option>' +
    modelos.map(m => `<option value="${escapeHtml(m)}">${escapeHtml(m)}</option>`).join("");
}

function render() {
  const resultado = filtrarPrecos(estado.precos, {
    texto:  els.busca.value,
    marca:  estado.marcaAtiva,
    modelo: estado.modeloAtivo,
  });

  els.contagem.textContent = resultado.length === 1 ? "1 resultado" : `${resultado.length} resultados`;

  if (resultado.length === 0) {
    els.corpoTabela.innerHTML = '<tr><td colspan="6"><div class="empty-state">Sem resultados.</div></td></tr>';
    return;
  }

  els.corpoTabela.innerHTML = resultado.map(p => `
    <tr>
      <td>${escapeHtml(p.marca)}</td>
      <td>${escapeHtml(p.modelo)}</td>
      <td>${escapeHtml(p.servico)}</td>
      <td><span class="badge ${classeBadgeQualidade(p.qualidade)}">${escapeHtml(p.qualidade)}</span></td>
      <td class="num price">${formatarPreco(p.preco_eur)}</td>
      <td class="actions">
        <div class="actions-cell">
          <button class="icon-btn icon-btn--edit"   title="Editar"  data-editar="${p.id}">&#9998;</button>
          <button class="icon-btn icon-btn--delete" title="Apagar"  data-apagar="${p.id}">&times;</button>
        </div>
      </td>
    </tr>`).join("");
}

els.corpoTabela.addEventListener("click", ev => {
  const btnEditar = ev.target.closest("[data-editar]");
  const btnApagar = ev.target.closest("[data-apagar]");
  if (btnEditar) {
    const item = estado.precos.find(p => p.id === Number(btnEditar.dataset.editar));
    if (item) modalEditar(item);
  }
  if (btnApagar) {
    const item = estado.precos.find(p => p.id === Number(btnApagar.dataset.apagar));
    if (item) modalApagar(item);
  }
});

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
/* Atalhos de teclado                                                      */
/* ---------------------------------------------------------------------- */

document.addEventListener("keydown", ev => {
  // / ou Ctrl+K → focar pesquisa
  if ((ev.key === "/" || (ev.ctrlKey && ev.key === "k")) && !modalAberto()) {
    ev.preventDefault();
    els.busca.focus();
    els.busca.select();
  }
  // Escape → limpar pesquisa (se não houver modal aberto)
  if (ev.key === "Escape" && !modalAberto()) {
    els.busca.value = "";
    render();
  }
});

function modalAberto() { return els.modalRoot.innerHTML !== ""; }

/* ---------------------------------------------------------------------- */
/* Publicar (commit + push via GitHub API)                                 */
/* ---------------------------------------------------------------------- */

function atualizarBtnPublicar() {
  els.btnPublicar.textContent = estado.alterado ? "↑ Publicar *" : "↑ Publicar";
  els.btnPublicar.style.opacity = estado.alterado ? "1" : "0.7";
}

els.btnPublicar.addEventListener("click", async () => {
  if (!estado.alterado) {
    toast("Sem alterações para publicar.", "sucesso");
    return;
  }

  if (!confirm("Publicar as alterações?\nO site público fica atualizado em 1-2 minutos.")) return;

  const btn = els.btnPublicar;
  btn.disabled = true;
  btn.textContent = "A publicar…";
  setStatus("espera", "A fazer commit no GitHub…");

  try {
    const agora = new Date().toLocaleString("pt-PT", { dateStyle: "short", timeStyle: "short" });
    const novoSha = await ghEscreverPrecos(
      estado.precos,
      estado.fileSha,
      `Preços atualizados manualmente (${agora})`
    );
    estado.fileSha  = novoSha;
    estado.alterado = false;
    atualizarBtnPublicar();
    toast("Publicado! O site atualiza em 1-2 minutos.", "sucesso");
    setStatus("ok", `Publicado às ${agora}`);
    els.statusSha.textContent = `SHA: ${novoSha.substring(0, 7)}`;
  } catch (e) {
    toast("Erro ao publicar: " + e.message, "erro");
    setStatus("erro", "Erro ao publicar: " + e.message);
  } finally {
    btn.disabled = false;
    atualizarBtnPublicar();
  }
});

/* ---------------------------------------------------------------------- */
/* Modais                                                                  */
/* ---------------------------------------------------------------------- */

function abrirModal(html) {
  els.modalRoot.innerHTML = `<div class="modal-overlay" id="overlay-modal">${html}</div>`;
  document.getElementById("overlay-modal").addEventListener("click", ev => {
    if (ev.target.id === "overlay-modal") fecharModal();
  });
}
function fecharModal() { els.modalRoot.innerHTML = ""; }

function toast(msg, tipo = "sucesso") {
  const el = document.createElement("div");
  el.className = `toast toast--${tipo}`;
  el.textContent = msg;
  els.toastRoot.appendChild(el);
  setTimeout(() => el.remove(), 4200);
}

function datalistQualidades(idInput) {
  return `<input list="lista-qual" id="${idInput}" type="text" autocomplete="off" />
    <datalist id="lista-qual">
      ${QUALIDADES.map(q => `<option value="${escapeHtml(q)}"></option>`).join("")}
    </datalist>`;
}

function setStatus(tipo, texto) {
  els.statusTexto.textContent = texto;
  els.statusDot.className = "status-dot" +
    (tipo === "erro" ? " status-dot--erro" : tipo === "espera" ? " status-dot--espera" : "");
}

/* ---------------------------------------------------------------------- */
/* Modal: Editar                                                           */
/* ---------------------------------------------------------------------- */

function modalEditar(item) {
  abrirModal(`
    <div class="modal">
      <h3>Editar reparação</h3>
      <p class="desc">${escapeHtml(item.marca)} · ${escapeHtml(item.modelo)}</p>
      <div class="field"><label>Serviço</label><input type="text" id="edit-servico" value="${escapeHtml(item.servico)}" /></div>
      <div class="field"><label>Qualidade</label>${datalistQualidades("edit-qualidade")}</div>
      <div class="field">
        <label>Preço (€)</label>
        <input type="number" id="edit-preco" step="0.01" min="0" value="${item.preco_eur}" />
        <div class="field-error" id="edit-erro"></div>
      </div>
      <div class="actions">
        <button class="btn btn--ghost" id="edit-cancelar">Cancelar</button>
        <button class="btn btn--primary" id="edit-confirmar">Guardar alteração</button>
      </div>
    </div>`);

  document.getElementById("edit-qualidade").value = item.qualidade;
  document.getElementById("edit-cancelar").addEventListener("click", fecharModal);
  document.getElementById("edit-confirmar").addEventListener("click", () => {
    const servico   = document.getElementById("edit-servico").value.trim();
    const qualidade = document.getElementById("edit-qualidade").value.trim();
    const preco     = parseFloat(document.getElementById("edit-preco").value.replace(",", "."));
    const erroEl    = document.getElementById("edit-erro");

    if (!servico || !qualidade || isNaN(preco) || preco < 0) {
      erroEl.textContent = "Preenche o serviço, a qualidade e um preço válido.";
      erroEl.style.display = "block";
      return;
    }

    // Atualizar em memória
    const idx = estado.precos.findIndex(p => p.id === item.id);
    if (idx !== -1) {
      estado.precos[idx] = { ...estado.precos[idx], servico, qualidade, preco_eur: preco };
      prepararCache([estado.precos[idx]]);
    }
    estado.alterado = true;
    atualizarBtnPublicar();
    fecharModal();
    render();
    toast("Alteração guardada. Clica em 'Publicar' para enviar para o site.");
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
      <p class="desc">Clica em "Publicar" depois para gravar a remoção no site.</p>
      <div class="actions">
        <button class="btn btn--ghost" id="del-cancelar">Cancelar</button>
        <button class="btn btn--danger" id="del-confirmar">Apagar</button>
      </div>
    </div>`);

  document.getElementById("del-cancelar").addEventListener("click", fecharModal);
  document.getElementById("del-confirmar").addEventListener("click", () => {
    estado.precos = estado.precos.filter(p => p.id !== item.id);
    estado.alterado = true;
    atualizarBtnPublicar();
    fecharModal();
    popularFiltroMarca();
    render();
    toast("Apagado em memória. Clica em 'Publicar' para gravar no site.");
  });
}

/* ---------------------------------------------------------------------- */
/* Modal: Registar novo                                                    */
/* ---------------------------------------------------------------------- */

els.btnRegistar.addEventListener("click", () => {
  abrirModal(`
    <div class="modal">
      <h3>Registar nova reparação</h3>
      <p class="desc">Todos os campos são obrigatórios.</p>
      <div class="field"><label>Marca</label><input type="text" id="add-marca" placeholder="ex: Apple" /></div>
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
        <button class="btn btn--primary" id="add-confirmar">Registar</button>
      </div>
    </div>`);

  document.getElementById("add-cancelar").addEventListener("click", fecharModal);
  document.getElementById("add-confirmar").addEventListener("click", () => {
    const marca     = document.getElementById("add-marca").value.trim();
    const modelo    = document.getElementById("add-modelo").value.trim();
    const servico   = document.getElementById("add-servico").value.trim();
    const qualidade = document.getElementById("add-qualidade").value.trim();
    const preco     = parseFloat(document.getElementById("add-preco").value.replace(",", "."));
    const erroEl    = document.getElementById("add-erro");

    if (!marca || !modelo || !servico || !qualidade || isNaN(preco) || preco < 0) {
      erroEl.textContent = "Preenche todos os campos com um preço válido.";
      erroEl.style.display = "block";
      return;
    }

    // Gerar ID único (maior ID existente + 1)
    const maxId = estado.precos.reduce((m, p) => Math.max(m, p.id || 0), 0);
    const novo = { id: maxId + 1, marca, modelo, servico, qualidade, preco_eur: preco };
    prepararCache([novo]);
    estado.precos.push(novo);
    estado.alterado = true;
    atualizarBtnPublicar();
    fecharModal();
    popularFiltroMarca();
    render();
    toast("Registado em memória. Clica em 'Publicar' para gravar no site.");
  });
});

/* ---------------------------------------------------------------------- */
/* Arrancar                                                                */
/* ---------------------------------------------------------------------- */

init();

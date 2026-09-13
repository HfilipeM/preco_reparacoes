/* ==========================================================================
   Painel de Preços — lógica partilhada de pesquisa, filtro e renderização.
   Usada tanto pelo site público (index.html) como pelo painel de admin.
   Pesquisa/filtro correm inteiramente no browser (o array já está em
   memória), por isso são instantâneos mesmo com milhares de linhas.
   ========================================================================== */

/**
 * Normaliza texto para pesquisa:
 *  - Remove acentos (NFD + strip combining marks)
 *  - Passa a minúsculas
 *  - Colapsa espaços múltiplos num só
 *  - Remove espaços no início e fim
 * "Ecrã" e "ecra", "iphone  12" e "iphone 12" encontram o mesmo resultado.
 */
function normalizar(txt) {
  return (txt || "")
    .toString()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")  // remove diacríticos
    .toLowerCase()
    .replace(/\s+/g, " ")             // colapsa espaços múltiplos
    .trim();
}

/**
 * Tokeniza uma query normalizada em palavras individuais,
 * ignorando tokens vazios resultantes de espaços.
 * "  iphone  12  pro  " → ["iphone", "12", "pro"]
 */
function tokenizar(txt) {
  return normalizar(txt).split(" ").filter(Boolean);
}

/* ---------------------------------------------------------------------- */
/* Cache de campos normalizados (construído uma vez, reutilizado sempre)   */
/* ---------------------------------------------------------------------- */

/**
 * Pré-computa e guarda em cada item o campo _pesquisa (string normalizada
 * de todos os campos pesquisáveis concatenados).
 * Chamar depois de carregar os dados e antes de qualquer filtro.
 * Isto evita normalizar os mesmos dados em cada keystroke.
 */
function prepararCache(precos) {
  for (const p of precos) {
    p._pesquisa = normalizar(
      `${p.marca} ${p.modelo} ${p.servico} ${p.qualidade}`
    );
  }
  return precos;
}

/* ---------------------------------------------------------------------- */
/* Filtro principal — pesquisa por tokens                                  */
/* ---------------------------------------------------------------------- */

/**
 * Filtra o array de preços por texto livre + marca + modelo.
 *
 * PESQUISA POR TOKENS: cada palavra da query tem de existir algures nos
 * campos (marca, modelo, serviço ou qualidade), de forma independente.
 *
 * Exemplos:
 *   "display iphone 12 pro"  → encontra entradas com modelo "iPhone 12 Pro"
 *                              e serviço que contenha "display"
 *   "iphone 12 pro ecrã"     → idem (ecrã = ecra depois de normalizar)
 *   "iphone  12  pro"        → espaços duplos colapsados → funciona igual
 *   "sam bat"                → encontra "Samsung ... bateria"
 */
function filtrarPrecos(precos, { texto = "", marca = "", modelo = "" } = {}) {
  const tokens = tokenizar(texto);

  return precos.filter((p) => {
    // Filtros exactos de marca e modelo (dropdown)
    if (marca && p.marca !== marca) return false;
    if (modelo && p.modelo !== modelo) return false;

    // Sem texto livre? Passa.
    if (tokens.length === 0) return true;

    // Usa o campo pré-computado se existir, senão normaliza na hora
    const alvo = p._pesquisa ||
      normalizar(`${p.marca} ${p.modelo} ${p.servico} ${p.qualidade}`);

    // TODOS os tokens têm de estar presentes (lógica AND)
    return tokens.every((tok) => alvo.includes(tok));
  });
}

/* ---------------------------------------------------------------------- */
/* Debounce — evita disparar render() em cada keystroke                    */
/* ---------------------------------------------------------------------- */

/**
 * Retorna uma versão "debounced" da função fn:
 * só executa depois de `espera` ms sem novas chamadas.
 * Uso: els.busca.addEventListener("input", debounce(render, 150));
 */
function debounce(fn, espera) {
  let timer;
  return function (...args) {
    clearTimeout(timer);
    timer = setTimeout(() => fn.apply(this, args), espera);
  };
}

/* ---------------------------------------------------------------------- */
/* Helpers de filtros                                                      */
/* ---------------------------------------------------------------------- */

/** Lista de marcas únicas, ordenada, para popular o <select> de filtro. */
function marcasUnicas(precos) {
  return [...new Set(precos.map((p) => p.marca))].sort((a, b) =>
    a.localeCompare(b, "pt")
  );
}

/** Lista de modelos únicos (opcionalmente já filtrados por marca escolhida). */
function modelosUnicos(precos, marca = "") {
  const base = marca ? precos.filter((p) => p.marca === marca) : precos;
  return [...new Set(base.map((p) => p.modelo))].sort((a, b) =>
    a.localeCompare(b, "pt")
  );
}

/* ---------------------------------------------------------------------- */
/* Helpers de UI                                                           */
/* ---------------------------------------------------------------------- */

/** Classe CSS do "badge" de qualidade, consoante o texto vindo dos dados. */
function classeBadgeQualidade(qualidade) {
  const q = normalizar(qualidade);
  if (q === "original") return "badge--original";
  if (q.startsWith("compat")) return "badge--compat";
  return "badge--padrao";
}

/** Formata um valor numérico como euros, ex: 249.95 → "249,95 €" */
function formatarPreco(valor) {
  const n = Number(valor);
  if (Number.isNaN(n)) return "-";
  return (
    n.toLocaleString("pt-PT", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }) + " \u20AC"
  );
}

/** Formata uma data ISO/"YYYY-MM-DD HH:MM:SS" para "dd/mm/aaaa hh:mm". */
function formatarData(dataStr) {
  if (!dataStr) return "-";
  const norm = dataStr.includes("T") ? dataStr : dataStr.replace(" ", "T");
  const d = new Date(norm);
  if (Number.isNaN(d.getTime())) return dataStr;
  return d.toLocaleString("pt-PT", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Escapa texto para inserir em HTML em segurança (evita injeção). */
function escapeHtml(str) {
  return (str ?? "")
    .toString()
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

#!/usr/bin/env python3
"""
iServices Portugal — Sincronizador de Preços
=================================================
Reescrito do zero (Set 2026) porque o site passou a ser inteiramente
renderizado por JavaScript, com separadores (tabs) que só colocam
conteúdo no DOM quando clicados, e uma banner de cookies que bloqueia
esses cliques se não for fechada primeiro.

Estrutura do site:
  Nível 0: /reparacao/<marca>                        → cartões de família/modelo
           (pode ter separadores: iPhone/iPad/Watch, Pixel 9/8/7...)
  Nível 1: /reparacao/<marca>/.../<modelo>            → cartões de categorias
           (Ecrã, Bateria, Câmaras... cada um com preço "desde")
  Nível 2: /reparacao/<marca>/.../<modelo>/<categoria> → preços finais por qualidade
           (Original vs Compatível/iServices)

Arquitetura:
  - Todo o fetching usa Playwright (headless Chromium) — não há tentativa
    prévia com `requests`, porque o site nunca devolve conteúdo útil sem JS.
  - Um browser Playwright persistente por thread, reutilizado entre páginas
    (login/consentimento de cookies só precisa de ser resolvido uma vez).
  - Deteção de "cartões" (links de navegação) genérica: tenta várias classes
    CSS conhecidas (a.box, a.rb-card) e cai num seletor genérico se o site
    mudar outra vez, para não voltar a quebrar silenciosamente.
  - Navegação por separadores: clica em cada tab e junta os cartões de todos,
    porque o site só põe no DOM os do separador ativo por omissão.
  - Proteções contra apagar a base de dados se o scraping falhar ou não
    encontrar nada (site em baixo, bloqueado, ou estrutura mudou de vez).

Uso:
    python iservices_sync.py --db ../data/iservices.db --json ../data/precos.json
    python iservices_sync.py --marca Apple --limite 10
    python iservices_sync.py --diagnostico
"""

import sys, os, re, json, time, random, argparse, subprocess, threading, sqlite3
from datetime import datetime
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import deque

# ── Dependências ────────────────────────────────────────────────────────────────

def _instalar(pkg_import, pkg_pip=None):
    try:
        __import__(pkg_import)
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", pkg_pip or pkg_import,
                        "--break-system-packages", "-q"], check=False)

_instalar("bs4", "beautifulsoup4")
_instalar("playwright")

from bs4 import BeautifulSoup

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError
    _PLAYWRIGHT_OK = True
except ImportError:
    _PLAYWRIGHT_OK = False

# ── Configuração ────────────────────────────────────────────────────────────────

BASE_URL       = "https://iservices.pt"
THREADS        = 4                 # nº de browsers Playwright em paralelo
DELAY_PAGINA   = (0.3, 0.8)        # pausa entre páginas, por thread
NAV_TIMEOUT_MS = 25000
TAB_CLICK_TIMEOUT_MS = 5000
MAX_FALHAS_SEGUIDAS_TABS = 3

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
]

MARCAS_FALLBACK = [
    "apple","samsung","xiaomi","huawei","oppo","oneplus","google",
    "dyson","realme","microsoft","asus","alcatel","tcl","honor",
    "nokia","motorola","lenovo","dell","nintendo","wiko","bq","htc","nothing",
]

# Seletores de "cartão" (links de navegação), por ordem de tentativa.
# O site usa classes diferentes consoante a secção — mantemos uma lista
# de variantes conhecidas + um fallback genérico para resistir a mudanças futuras.
CARD_SELECTORS = [
    "a.box[href]",
    "a.rb-card[href]",
    "a[class*='card'][href]",
    "a[class*='box'][href]",
]
TITULO_SELECTORS = [
    ".box-content-title", ".rb-card__name",
    "[class*='card__name']", "[class*='card-title']",
    "[class*='box-content-title']", "[class*='name']", "[class*='title']",
]
PRECO_SELECTORS = [
    ".repair-price", "[class*='repair-price']", "[class*='card__price']", "[class*='box-price']",
    "[class*='price']",
]
TAB_SELECTOR = "button.rb-tab, [role='tab']"

# Banner de cookies (Klaro) — bloqueia cliques nos separadores se não for fechada.
COOKIE_ACCEPT_SELECTORS = [
    "#klaro button.cm-btn-success",
    "#klaro-cookie-notice button.cm-btn-success",
    "#klaro .cn-buttons button.cm-btn-success",
    "#klaro button:has-text('Aceitar')",
    "#klaro button:has-text('Aceito')",
    "#klaro .cm-btn-success",
]

_print_lock = threading.Lock()

def log(msg, nivel="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    s  = {"INFO": "i", "OK": "OK", "WARN": "!!", "ERR": "XX", "PW": "JS"}
    with _print_lock:
        print(f"[{ts}] {s.get(nivel, '-')}  {msg}", flush=True)


def _msg_curta(e):
    """Só a primeira linha da mensagem de erro — evita poluir os logs com
    o relatório completo (por vezes gigante) do Playwright."""
    return str(e).strip().splitlines()[0][:140]


def delay():
    time.sleep(random.uniform(*DELAY_PAGINA))

# ── Playwright: um browser persistente por thread ──────────────────────────────

_local_thread  = threading.local()
_navs_criados  = []
_navs_lock     = threading.Lock()
_chromium_ok   = False
_chromium_lock = threading.Lock()


class Navegador:
    """Envolve um browser Playwright persistente. Cada thread tem o seu (via
    obter_navegador), reutilizado entre páginas para não reabrir o browser
    a cada pedido e para o consentimento de cookies só ter de ser resolvido
    uma vez por thread."""

    def __init__(self):
        self._pw     = sync_playwright().start()
        self.browser = self._pw.chromium.launch(headless=True)
        self.context = self.browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={"width": 1366, "height": 850},
            locale="pt-PT",
        )
        # Bloquear imagens/fontes/vídeo acelera bastante, sem perder nenhum dado de preços.
        self.context.route(
            re.compile(r"\.(png|jpg|jpeg|gif|webp|svg|woff2?|ttf|mp4)(\?.*)?$"),
            lambda route: route.abort(),
        )
        self.page = self.context.new_page()
        self.page.set_default_timeout(NAV_TIMEOUT_MS)
        self._cookies_ok = False

    def _fechar_banner_cookies(self):
        """Tenta fechar a banner uma vez; barato e seguro chamar mesmo que já esteja fechada."""
        for sel in COOKIE_ACCEPT_SELECTORS:
            try:
                el = self.page.query_selector(sel)
                if el and el.is_visible():
                    el.click(timeout=3000)
                    self.page.wait_for_timeout(300)
                    self._cookies_ok = True
                    return True
            except Exception:
                continue
        return False

    def ir_para(self, url, referer=None):
        """Navega para o URL, espera o conteúdo carregar e fecha a banner de cookies."""
        self.page.goto(url, wait_until="domcontentloaded", referer=referer or BASE_URL)
        try:
            self.page.wait_for_load_state("networkidle", timeout=10000)
        except PWTimeoutError:
            pass
        self.page.wait_for_timeout(350)
        if not self._cookies_ok:
            self._fechar_banner_cookies()

    def html_atual(self):
        return self.page.content()

    def soup_atual(self):
        return BeautifulSoup(self.html_atual(), "html.parser")

    def fechar(self):
        try:
            self.context.close(); self.browser.close(); self._pw.stop()
        except Exception:
            pass


def _garantir_chromium():
    global _chromium_ok
    with _chromium_lock:
        if _chromium_ok:
            return
        try:
            with sync_playwright() as p:
                b = p.chromium.launch(headless=True); b.close()
        except Exception as e:
            if "Executable doesn't exist" in str(e):
                log("A instalar Chromium (só na 1ª vez, ~150MB)...", "PW")
                subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=False)
        _chromium_ok = True


def obter_navegador():
    if not _PLAYWRIGHT_OK:
        raise RuntimeError("Playwright não está instalado — não é possível continuar (site requer JS).")
    nav = getattr(_local_thread, "nav", None)
    if nav is None:
        _garantir_chromium()
        nav = Navegador()
        _local_thread.nav = nav
        with _navs_lock:
            _navs_criados.append(nav)
    return nav


def fechar_todos_navegadores():
    with _navs_lock:
        for nav in _navs_criados:
            nav.fechar()
        _navs_criados.clear()

# ── Deteção genérica de cartões / preços ────────────────────────────────────────

def _selecionar_cartoes(soup):
    for sel in CARD_SELECTORS:
        els = soup.select(sel)
        if els:
            return [a for a in els
                    if "disabled-status" not in a.get("class", []) and "disabled" not in a.get("class", [])]
    return []


def _titulo_cartao(a_el, fallback=""):
    for sel in TITULO_SELECTORS:
        el = a_el.select_one(sel)
        if el:
            t = el.get_text(strip=True)
            if t:
                return t
    return a_el.get_text(strip=True) or fallback


def _parse_preco_eur(texto):
    """Converte '406,00 €' ou '119.95€' para float."""
    texto = texto.replace("\xa0", "").replace(" ", "")
    m = re.search(r"(\d[\d\.]*,\d{2}|\d+\.\d{2})", texto)
    if not m:
        return None
    raw = m.group(1)
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def _preco_cartao(a_el):
    for sel in PRECO_SELECTORS:
        el = a_el.select_one(sel)
        if el:
            p = _parse_preco_eur(el.get_text())
            if p is not None:
                return p
    return None

# ── URLs ─────────────────────────────────────────────────────────────────────────

def segmentos(href, base=BASE_URL):
    abs_url = urljoin(base, href)
    p = urlparse(abs_url)
    if "iservices.pt" not in p.netloc:
        return None
    return tuple(s for s in p.path.split("/") if s)

def url_de(segs):
    return BASE_URL + "/" + "/".join(segs)

def eh_url_reparacao(segs, min_segs=2):
    return segs and segs[0] == "reparacao" and len(segs) >= min_segs

# ── Recolha de cartões, com navegação por separadores ──────────────────────────

def obter_cartoes(url, referer=None):
    """
    Abre a página, fecha a banner de cookies, e devolve TODOS os cartões
    (href, nome, preco) — incluindo os que só aparecem depois de clicar em
    separadores (tabs), que o site esconde por omissão.
    """
    nav = obter_navegador()
    try:
        nav.ir_para(url, referer=referer)
    except Exception as e:
        log(f"Falha ao abrir {url}: {_msg_curta(e)}", "WARN")
        return [], nav

    vistos = {}  # href -> (nome, preco)

    def _recolher():
        soup = nav.soup_atual()
        for a in _selecionar_cartoes(soup):
            href = a.get("href", "")
            if href:
                vistos.setdefault(href, (_titulo_cartao(a), _preco_cartao(a)))

    _recolher()

    try:
        n_tabs = len(nav.page.query_selector_all(TAB_SELECTOR))
    except Exception:
        n_tabs = 0

    falhas_seguidas = 0
    for i in range(n_tabs):
        try:
            tabs_atual = nav.page.query_selector_all(TAB_SELECTOR)
            if i >= len(tabs_atual):
                break
            tab = tabs_atual[i]
            if tab.get_attribute("aria-selected") != "true":
                try:
                    tab.click(timeout=TAB_CLICK_TIMEOUT_MS)
                except Exception:
                    nav._fechar_banner_cookies()
                    tab.click(timeout=TAB_CLICK_TIMEOUT_MS)
                nav.page.wait_for_timeout(450)
            _recolher()
            falhas_seguidas = 0
        except Exception as e:
            falhas_seguidas += 1
            log(f"Separador {i+1}/{n_tabs} falhou em .../{url.rstrip('/').split('/')[-1]}: {_msg_curta(e)}", "WARN")
            if falhas_seguidas >= MAX_FALHAS_SEGUIDAS_TABS:
                log("Vários separadores seguidos falharam — a desistir dos restantes nesta página.", "WARN")
                break

    cartoes = [(href, nome, preco) for href, (nome, preco) in vistos.items()]
    return cartoes, nav

# ── Extração de qualidades/preços (nível 2) ────────────────────────────────────

def _classificar_qualidade(titulo):
    """
    Ex: 'Vidro / Ecrã / Touch iServices' → ('Vidro / Ecrã / Touch', 'Compatível (iServices)')
        'Vidro / Ecrã / Touch Original'   → ('Vidro / Ecrã / Touch', 'Original')
        'Bateria'                          → ('Bateria', 'Padrão')
    """
    t, tl = titulo.strip(), titulo.strip().lower()
    if re.search(r"\boriginal\b", tl):
        nome = re.sub(r"\boriginal\b", "", t, flags=re.IGNORECASE).strip(" -/®")
        return nome or t, "Original"
    if "iservices" in tl:
        nome = re.sub(r"iservices", "", t, flags=re.IGNORECASE).strip(" -/®")
        return nome or t, "Compatível (iServices)"
    if re.search(r"\bcompat[ií]vel\b", tl):
        nome = re.sub(r"\bcompat[ií]vel\b", "", t, flags=re.IGNORECASE).strip(" -/")
        return nome or t, "Compatível"
    return t, "Padrão"


def extrair_precos_qualidade(soup, nome_servico_fallback=""):
    """A partir da página de categoria (nível 2), devolve [{"servico","qualidade","preco_eur"}]."""
    precos = []
    blocos = soup.select(".repair-quality-card") or soup.select("[class*='repair-quality']") or []

    if blocos:
        for bloco in blocos:
            titulo_el = bloco.select_one("h3, h2, .repair-quality-name, [class*='quality-name']")
            preco_el  = bloco.select_one(".repair-quality-price, strong.repair-quality-price, [class*='quality-price']")
            if not titulo_el or not preco_el:
                continue
            preco = _parse_preco_eur(preco_el.get_text())
            if preco is None:
                continue
            servico, qualidade = _classificar_qualidade(titulo_el.get_text(strip=True))
            precos.append({"servico": servico or nome_servico_fallback, "qualidade": qualidade, "preco_eur": preco})
        return precos

    # Fallback: h3 seguido do preço mais próximo, para resistir a pequenas mudanças de markup.
    for h3 in soup.select("h3"):
        preco_el = (h3.find_next(".repair-quality-price") or h3.find_next("strong")
                    or h3.find_next(class_=re.compile("price")))
        if not preco_el:
            continue
        preco = _parse_preco_eur(preco_el.get_text())
        if preco is None:
            continue
        servico, qualidade = _classificar_qualidade(h3.get_text(strip=True))
        precos.append({"servico": servico or nome_servico_fallback, "qualidade": qualidade, "preco_eur": preco})

    return precos


def extrair_links_categorias_de_cartoes(cartoes, url_modelo):
    """
    A partir de cartões (href, nome, preco) da página atual, devolve
    [(url_categoria, nome_categoria, preco_desde)] — MAS só os que são
    mesmo categorias de reparação.

    Critério: no site, cartões de MODELO (ex: 'iPhone Air', 'Pixel 8') nunca
    mostram preço; cartões de CATEGORIA (ex: 'Ecrã: 406€', 'Bateria: 119,95€')
    mostram sempre. Usamos isto para distinguir — contar segmentos do URL não
    chega, porque um link de modelo pode ter tantos ou mais segmentos que um
    link de categoria (ex: /apple/iphone/iphone-air/iphone-air tem 5 segmentos,
    mas é um MODELO, não uma categoria).
    """
    resultado = []
    for href, nome, preco in cartoes:
        if preco is None:
            continue  # sem preço = é um link de modelo/navegação, não uma categoria
        segs = segmentos(href)
        if not segs or not eh_url_reparacao(segs, min_segs=3):
            continue
        url_cat = url_de(segs)
        if url_cat == url_modelo:
            continue
        resultado.append((url_cat, nome or segs[-1].replace("-", " ").title(), preco))
    return resultado

# ── Descoberta de marcas ────────────────────────────────────────────────────────

def descobrir_marcas():
    log("A obter lista de marcas...")
    cartoes, nav = obter_cartoes(BASE_URL + "/reparacao")
    marcas = {}

    # Tentativa 1: cartões reconhecidos (a.box / a.rb-card / fallback genérico)
    for href, nome, _preco in cartoes:
        segs = segmentos(href)
        if segs and segs[0] == "reparacao" and len(segs) == 2:
            slug = segs[1]
            marcas.setdefault(slug, {"nome": nome or slug.capitalize(), "url": url_de(segs)})

    # Tentativa 2: a página de marcas pode não usar nenhuma das classes de "cartão"
    # conhecidas — varrer TODOS os <a> da página à procura de /reparacao/<marca>.
    if not marcas:
        log("Nenhum cartão reconhecido em /reparacao — a tentar varrer todos os links.", "WARN")
        soup = nav.soup_atual()
        for a in soup.find_all("a", href=True):
            segs = segmentos(a["href"])
            if segs and segs[0] == "reparacao" and len(segs) == 2:
                slug = segs[1]
                nome = a.get_text(strip=True) or slug.capitalize()
                marcas.setdefault(slug, {"nome": nome, "url": url_de(segs)})

    # Tentativa 3: lista fixa conhecida, só como último recurso.
    if not marcas:
        log("Continuo sem encontrar marcas. A usar lista fixa conhecida (pode estar desatualizada).", "WARN")
        marcas = {s: {"nome": s.capitalize(), "url": f"{BASE_URL}/reparacao/{s}"} for s in MARCAS_FALLBACK}

    log(f"{len(marcas)} marcas encontradas.", "OK")
    return list(marcas.values())

# ── Crawl de uma marca (níveis 0 e 1) ───────────────────────────────────────────

def crawl_marca(marca_nome, url_marca, stats):
    """
    Percorre em largura a partir da página da marca, seguindo cartões até
    encontrar páginas de modelo (que têm categorias de reparação com preço).
    Cada página visitada usa obter_cartoes(), que já lida com separadores.

    Devolve lista de tarefas:
      {"marca", "sub_cat", "modelo", "url_modelo", "categorias": [(url, nome, preco_desde)]}
    """
    tarefas   = []
    visitados = {url_marca}
    fila      = deque([(url_marca, marca_nome)])

    while fila:
        url, ctx_nome = fila.popleft()
        cartoes, _nav = obter_cartoes(url, referer=BASE_URL + "/reparacao")
        with stats["lock"]:
            stats["paginas"] = stats.get("paginas", 0) + 1
        delay()

        segs = segmentos(url) or ()

        cats = extrair_links_categorias_de_cartoes(cartoes, url)
        if cats:
            sub_cat = segs[2].replace("-", " ").title() if len(segs) > 2 else marca_nome
            modelo  = ctx_nome or (segs[-1].replace("-", " ").title() if segs else url)
            tarefas.append({
                "marca": marca_nome, "sub_cat": sub_cat, "modelo": modelo,
                "url_modelo": url, "categorias": cats,
            })
            continue

        if not cartoes:
            continue

        for href, nome, _preco in cartoes:
            sub_segs = segmentos(href)
            if not sub_segs or not eh_url_reparacao(sub_segs, min_segs=2):
                continue
            sub_url = url_de(sub_segs)
            if sub_url in visitados:
                continue
            visitados.add(sub_url)
            fila.append((sub_url, nome or sub_segs[-1].replace("-", " ").title()))

    return tarefas


def descobrir_tarefas(marcas, filtro_marca=None, threads_descoberta=4):
    marcas_proc = [m for m in marcas
                   if not filtro_marca or m["nome"].lower() == filtro_marca.lower()
                   or m["url"].rstrip("/").split("/")[-1].lower() == (filtro_marca or "").lower()]
    stats = {"paginas": 0, "lock": threading.Lock()}
    todas = []
    with ThreadPoolExecutor(max_workers=min(threads_descoberta, len(marcas_proc) or 1)) as ex:
        futuros = {ex.submit(crawl_marca, m["nome"], m["url"], stats): m for m in marcas_proc}
        for futuro in as_completed(futuros):
            m = futuros[futuro]
            try:
                t = futuro.result()
                todas.extend(t)
                log(f"[{m['nome']}] {len(t)} modelos mapeados.", "OK")
            except Exception as e:
                log(f"[{m['nome']}] Erro no crawl: {_msg_curta(e)}", "ERR")
    log(f"Descoberta: {stats['paginas']} páginas visitadas.", "OK")
    return todas

# ── Extração de preços por tarefa (nível 2) ────────────────────────────────────

def processar_tarefa(tarefa):
    """Para cada categoria do modelo, visita a página de qualidades e extrai preços."""
    precos_todos = []
    for url_cat, nome_cat, preco_desde in tarefa["categorias"]:
        nav = obter_navegador()
        try:
            nav.ir_para(url_cat, referer=tarefa["url_modelo"])
            soup = nav.soup_atual()
        except Exception as e:
            log(f"Falha ao abrir categoria {nome_cat}: {_msg_curta(e)}", "WARN")
            if preco_desde is not None:
                precos_todos.append({"servico": nome_cat, "qualidade": "Padrão", "preco_eur": preco_desde})
            continue

        precos_cat = extrair_precos_qualidade(soup, nome_servico_fallback=nome_cat)
        if precos_cat:
            precos_todos.extend(precos_cat)
        elif preco_desde is not None:
            precos_todos.append({"servico": nome_cat, "qualidade": "Padrão", "preco_eur": preco_desde})

        delay()

    return tarefa, precos_todos

# ── Base de dados SQLite (schema inalterado — compatível com admin/admin-web) ──

SCHEMA = """
CREATE TABLE IF NOT EXISTS modelos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    marca TEXT NOT NULL,
    sub_categoria TEXT NOT NULL,
    modelo TEXT NOT NULL,
    url TEXT NOT NULL,
    primeira_deteccao TEXT NOT NULL,
    UNIQUE(marca, sub_categoria, modelo)
);

CREATE TABLE IF NOT EXISTS precos_reparacao (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    marca TEXT NOT NULL,
    sub_categoria TEXT NOT NULL,
    modelo TEXT NOT NULL,
    servico TEXT NOT NULL,
    qualidade TEXT NOT NULL,
    preco_eur REAL NOT NULL,
    ativo INTEGER NOT NULL DEFAULT 1,
    origem TEXT NOT NULL DEFAULT 'scraping',
    primeira_deteccao TEXT NOT NULL,
    ultima_verificacao TEXT NOT NULL,
    UNIQUE(marca, sub_categoria, modelo, servico, qualidade)
);

CREATE TABLE IF NOT EXISTS historico_precos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    marca TEXT NOT NULL, sub_categoria TEXT NOT NULL, modelo TEXT NOT NULL,
    servico TEXT NOT NULL, qualidade TEXT NOT NULL,
    preco_antigo REAL NOT NULL, preco_novo REAL NOT NULL, diferenca REAL NOT NULL,
    data TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS edicoes_manuais (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    acao TEXT NOT NULL,
    marca TEXT, sub_categoria TEXT, modelo TEXT, servico TEXT, qualidade TEXT,
    preco_antigo REAL, preco_novo REAL,
    data TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS execucoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    data_inicio TEXT NOT NULL,
    data_fim TEXT,
    modo TEXT NOT NULL,
    modelos_novos INTEGER DEFAULT 0,
    precos_novos INTEGER DEFAULT 0,
    precos_alterados INTEGER DEFAULT 0,
    precos_iguais INTEGER DEFAULT 0,
    inativados INTEGER DEFAULT 0,
    estado TEXT NOT NULL DEFAULT 'em_curso'
);

CREATE INDEX IF NOT EXISTS idx_precos_lookup ON precos_reparacao(marca, modelo, servico);
CREATE INDEX IF NOT EXISTS idx_precos_ativo  ON precos_reparacao(ativo);
"""


def seed_bd_a_partir_de_json(conn, caminho_json):
    """
    No GitHub Actions, a BD SQLite nunca é publicada (está no .gitignore),
    por isso cada execução começa com uma BD vazia — o que impede a
    comparação preço-antigo-vs-novo e o registo de histórico de funcionarem
    nas execuções automáticas.

    Esta função semeia a BD (recém-criada e vazia) com os preços do último
    precos.json publicado, ANTES do scraping correr, para que:
      - upsert_preco tenha um preço anterior real para comparar (e registar
        no histórico se mudar)
      - marcar_inativos tenha uma base real para o limiar de segurança (não
        inativar se encontrar menos de 10% do que já existia)

    Local: se já tiveres uma BD com histórico próprio, isto não faz mal —
    usa INSERT OR IGNORE, só preenche o que ainda não existir.
    """
    if not os.path.exists(caminho_json):
        log(f"Sem JSON anterior em {caminho_json} — primeira execução, BD começa vazia.", "INFO")
        return 0
    try:
        with open(caminho_json, "r", encoding="utf-8") as f:
            dados = json.load(f)
    except Exception as e:
        log(f"Não consegui ler {caminho_json} para semear a BD: {_msg_curta(e)}", "WARN")
        return 0

    agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    n = 0
    for p in dados.get("precos", []):
        try:
            ts = p.get("ultima_verificacao") or agora
            cur = conn.execute("""
                INSERT OR IGNORE INTO precos_reparacao
                    (marca, sub_categoria, modelo, servico, qualidade, preco_eur,
                     ativo, origem, primeira_deteccao, ultima_verificacao)
                VALUES (?, ?, ?, ?, ?, ?, 1, 'scraping', ?, ?)
            """, (p["marca"], p["sub_categoria"], p["modelo"], p["servico"], p["qualidade"],
                  p["preco_eur"], ts, ts))
            n += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        except Exception:
            continue
    conn.commit()
    if n:
        log(f"BD semeada com {n} preços do JSON anterior (comparação/histórico ativos).", "OK")
    return n


def abrir_bd(caminho):
    os.makedirs(os.path.dirname(os.path.abspath(caminho)) or ".", exist_ok=True)
    conn = sqlite3.connect(caminho)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def upsert_modelo(conn, marca, sub_cat, modelo, url, agora):
    conn.execute("""
        INSERT INTO modelos (marca, sub_categoria, modelo, url, primeira_deteccao)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(marca, sub_categoria, modelo) DO UPDATE SET url=excluded.url
    """, (marca, sub_cat, modelo, url, agora))


def upsert_preco(conn, marca, sub_cat, modelo, servico, qualidade, preco, agora):
    cur = conn.execute("""
        SELECT preco_eur FROM precos_reparacao
        WHERE marca=? AND sub_categoria=? AND modelo=? AND servico=? AND qualidade=?
    """, (marca, sub_cat, modelo, servico, qualidade))
    row = cur.fetchone()

    if row is None:
        conn.execute("""
            INSERT INTO precos_reparacao
                (marca, sub_categoria, modelo, servico, qualidade, preco_eur,
                 ativo, origem, primeira_deteccao, ultima_verificacao)
            VALUES (?, ?, ?, ?, ?, ?, 1, 'scraping', ?, ?)
        """, (marca, sub_cat, modelo, servico, qualidade, preco, agora, agora))
        return "novo"

    preco_antigo = row[0]
    if abs(preco_antigo - preco) > 0.001:
        conn.execute("""
            UPDATE precos_reparacao SET preco_eur=?, ativo=1, ultima_verificacao=?
            WHERE marca=? AND sub_categoria=? AND modelo=? AND servico=? AND qualidade=?
        """, (preco, agora, marca, sub_cat, modelo, servico, qualidade))
        conn.execute("""
            INSERT INTO historico_precos
                (marca, sub_categoria, modelo, servico, qualidade,
                 preco_antigo, preco_novo, diferenca, data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (marca, sub_cat, modelo, servico, qualidade, preco_antigo, preco,
              round(preco - preco_antigo, 2), agora))
        return "alterado"

    conn.execute("""
        UPDATE precos_reparacao SET ativo=1, ultima_verificacao=?
        WHERE marca=? AND sub_categoria=? AND modelo=? AND servico=? AND qualidade=?
    """, (agora, marca, sub_cat, modelo, servico, qualidade))
    return "igual"


def marcar_inativos(conn, chaves_vistas, agora):
    if not chaves_vistas:
        log("ATENÇÃO: scraping encontrou 0 preços. BD preservada sem alterações.", "WARN")
        return 0

    total_ativos = conn.execute(
        "SELECT COUNT(*) FROM precos_reparacao WHERE ativo=1 AND origem='scraping'"
    ).fetchone()[0]
    if total_ativos > 0 and len(chaves_vistas) / total_ativos < 0.10:
        log(f"ATENÇÃO: apenas {len(chaves_vistas)}/{total_ativos} preços encontrados "
            f"({len(chaves_vistas)/total_ativos:.1%}). Inativação CANCELADA.", "WARN")
        return 0

    cur = conn.execute("""SELECT marca, sub_categoria, modelo, servico, qualidade
                          FROM precos_reparacao WHERE ativo=1 AND origem='scraping'""")
    n = 0
    for row in cur.fetchall():
        if tuple(row) not in chaves_vistas:
            conn.execute("""
                UPDATE precos_reparacao SET ativo=0
                WHERE marca=? AND sub_categoria=? AND modelo=? AND servico=? AND qualidade=?
            """, row)
            n += 1
    return n

# ── Exports JSON ────────────────────────────────────────────────────────────────

def exportar_json(conn, caminho):
    cur = conn.execute("""
        SELECT marca, sub_categoria, modelo, servico, qualidade, preco_eur, ultima_verificacao
        FROM precos_reparacao WHERE ativo=1
        ORDER BY marca, modelo, servico, qualidade
    """)
    precos = [
        {"marca": r[0], "sub_categoria": r[1], "modelo": r[2], "servico": r[3],
         "qualidade": r[4], "preco_eur": r[5], "ultima_verificacao": r[6]}
        for r in cur.fetchall()
    ]
    dados = {"gerado_em": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
             "total_precos": len(precos), "precos": precos}
    os.makedirs(os.path.dirname(os.path.abspath(caminho)) or ".", exist_ok=True)
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)
    log(f"JSON exportado: {caminho} ({len(precos)} preços)", "OK")


def _dedup_ordenado(lista, chave_fn, data_fn, limite):
    """Remove duplicados (por chave_fn), ordena por data decrescente, corta ao limite."""
    vistos, resultado = set(), []
    for item in lista:
        chave = chave_fn(item)
        if chave in vistos:
            continue
        vistos.add(chave)
        resultado.append(item)
    resultado.sort(key=data_fn, reverse=True)
    return resultado[:limite]


def exportar_historico_json(conn, caminho, limite=500):
    """
    Exporta o histórico de alterações, edições manuais e execuções.

    No GitHub Actions a BD começa vazia a cada execução, por isso as tabelas
    aqui só têm os registos DESTA execução. Para não perder o histórico de
    semanas anteriores, fazemos merge com o que já estava publicado em
    `caminho`, removendo duplicados, antes de escrever o ficheiro final.
    """
    cur = conn.execute("""
        SELECT marca, sub_categoria, modelo, servico, qualidade,
               preco_antigo, preco_novo, diferenca, data
        FROM historico_precos ORDER BY data DESC LIMIT ?
    """, (limite,))
    alteracoes_novas = [dict(zip(
        ["marca","sub_categoria","modelo","servico","qualidade","preco_antigo","preco_novo","diferenca","data"], r
    )) for r in cur.fetchall()]

    cur2 = conn.execute("""
        SELECT acao, marca, sub_categoria, modelo, servico, qualidade, preco_antigo, preco_novo, data
        FROM edicoes_manuais ORDER BY data DESC LIMIT ?
    """, (limite,))
    edicoes_novas = [dict(zip(
        ["acao","marca","sub_categoria","modelo","servico","qualidade","preco_antigo","preco_novo","data"], r
    )) for r in cur2.fetchall()]

    cur3 = conn.execute("""
        SELECT data_inicio, data_fim, modo, modelos_novos, precos_novos,
               precos_alterados, precos_iguais, inativados, estado
        FROM execucoes ORDER BY data_inicio DESC LIMIT 100
    """)
    execucoes_novas = [dict(zip(
        ["data_inicio","data_fim","modo","modelos_novos","precos_novos",
         "precos_alterados","precos_iguais","inativados","estado"], r
    )) for r in cur3.fetchall()]

    # Merge com o que já estava publicado (se existir)
    anterior = {"alteracoes_preco": [], "edicoes_manuais": [], "execucoes": []}
    if os.path.exists(caminho):
        try:
            with open(caminho, "r", encoding="utf-8") as f:
                anterior = json.load(f)
        except Exception as e:
            log(f"Não consegui ler o histórico anterior ({_msg_curta(e)}) — a começar um novo.", "WARN")

    alteracoes = _dedup_ordenado(
        alteracoes_novas + anterior.get("alteracoes_preco", []),
        chave_fn=lambda a: (a.get("marca"), a.get("sub_categoria"), a.get("modelo"),
                             a.get("servico"), a.get("qualidade"), a.get("preco_antigo"),
                             a.get("preco_novo"), a.get("data")),
        data_fn=lambda a: a.get("data", ""), limite=limite,
    )
    edicoes = _dedup_ordenado(
        edicoes_novas + anterior.get("edicoes_manuais", []),
        chave_fn=lambda e: (e.get("acao"), e.get("marca"), e.get("modelo"), e.get("servico"),
                             e.get("qualidade"), e.get("data")),
        data_fn=lambda e: e.get("data", ""), limite=limite,
    )
    execucoes = _dedup_ordenado(
        execucoes_novas + anterior.get("execucoes", []),
        chave_fn=lambda e: (e.get("data_inicio"), e.get("modo")),
        data_fn=lambda e: e.get("data_inicio", ""), limite=100,
    )

    dados = {"gerado_em": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
             "alteracoes_preco": alteracoes, "edicoes_manuais": edicoes, "execucoes": execucoes}
    os.makedirs(os.path.dirname(os.path.abspath(caminho)) or ".", exist_ok=True)
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)
    log(f"Histórico exportado: {caminho} "
        f"({len(alteracoes)} alterações, {len(execucoes)} execuções)", "OK")

# ── Diagnóstico ─────────────────────────────────────────────────────────────────

def diagnostico_site():
    """Analisa o HTML atual do site sem alterar a BD."""
    print()
    print("="*62)
    print("  DIAGNÓSTICO - Estrutura atual do site iServices")
    print("="*62)

    urls_teste = [
        (BASE_URL + "/reparacao/apple", "Marca Apple (nível 0)"),
        (BASE_URL + "/reparacao/apple/iphone/iphone-17-pro-17-pro-max/iphone-17-pro-max", "Modelo iPhone (nível 1)"),
        (BASE_URL + "/reparacao/apple/iphone/iphone-17-pro-17-pro-max/iphone-17-pro-max/ecra-14", "Categoria/qualidades iPhone (nível 2)"),
        (BASE_URL + "/reparacao/google", "Marca Google (nível 0, layout rb-card)"),
        (BASE_URL + "/reparacao/google/pixel-9/pixel-9", "Modelo Pixel (nível 1, layout rb-card)"),
    ]

    for url, desc in urls_teste:
        print(f"\n  [{desc}] {url}")

        cartoes, nav = obter_cartoes(url)
        soup = nav.soup_atual()

        print(f"    Cartões encontrados (com separadores): {len(cartoes)}")
        for href, nome, preco in cartoes[:6]:
            preco_txt = f"{preco}€" if preco is not None else "—"
            print(f"      - {nome}: {preco_txt}  → {href}")

        cats = extrair_links_categorias_de_cartoes(cartoes, url)
        if cats:
            print(f"    → Interpretado como página de MODELO: {len(cats)} categorias de reparação")

        qualidades = extrair_precos_qualidade(soup)
        if qualidades:
            print(f"    → Interpretado como página de CATEGORIA: {len(qualidades)} qualidades/preços")
            for q in qualidades[:5]:
                print(f"      - [{q['qualidade']}] {q['servico']}: {q['preco_eur']}€")

        if not cartoes and not qualidades:
            print("    ✗ Nada reconhecido nesta página — pode ser preciso ajustar os seletores.")

    print()
    print("="*62)
    print()

# ── Main ────────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Sincronizador de preços iServices (Playwright, 2026)")
    ap.add_argument("--db",          default="../data/iservices.db")
    ap.add_argument("--json",        default="../data/precos.json")
    ap.add_argument("--historico",   default="../data/historico.json")
    ap.add_argument("--so-precos",   action="store_true", help="Só actualiza preços (não redescobre modelos)")
    ap.add_argument("--marca",       default=None)
    ap.add_argument("--limite",      type=int, default=None, help="Limitar nº de modelos (teste)")
    ap.add_argument("--threads",     type=int, default=THREADS)
    ap.add_argument("--diagnostico", action="store_true", help="Analisa estrutura do site sem alterar BD")
    args = ap.parse_args()

    if args.diagnostico:
        try:
            diagnostico_site()
        finally:
            fechar_todos_navegadores()
        return

    inicio_dt  = datetime.now()
    inicio_str = inicio_dt.strftime("%Y-%m-%d %H:%M:%S")
    modo       = "so-precos" if args.so_precos else "completo"

    print()
    print("="*62)
    print("  iServices - Sincronizador de Precos (2026)")
    print("="*62)
    print(f"  BD      : {args.db}")
    print(f"  Modo    : {modo}")
    print(f"  Threads : {args.threads}")
    if args.marca:  print(f"  Marca   : {args.marca}")
    if args.limite: print(f"  Limite  : {args.limite}")
    print("="*62); print()

    conn = abrir_bd(args.db)
    seed_bd_a_partir_de_json(conn, args.json)
    cur  = conn.execute("INSERT INTO execucoes (data_inicio, modo, estado) VALUES (?, ?, 'em_curso')",
                        (inicio_str, modo))
    execucao_id = cur.lastrowid
    conn.commit()

    agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    novos_modelos = novos_precos = alterados = iguais = n_inativos = 0

    try:
        # ── Fase 1: descoberta de tarefas ──
        if args.so_precos:
            rows = conn.execute("SELECT marca, sub_categoria, modelo, url FROM modelos").fetchall()
            if args.marca:
                rows = [r for r in rows if r[0].lower() == args.marca.lower()]
            tarefas_base = [{"marca": r[0], "sub_cat": r[1], "modelo": r[2], "url_modelo": r[3]} for r in rows]
            log(f"{len(tarefas_base)} modelos na BD. A obter categorias actuais...", "OK")

            tarefas = []
            for tb in tarefas_base:
                cartoes, _nav = obter_cartoes(tb["url_modelo"])
                cats = extrair_links_categorias_de_cartoes(cartoes, tb["url_modelo"])
                if cats:
                    tarefas.append({**tb, "categorias": cats})
                delay()
        else:
            marcas  = descobrir_marcas()
            tarefas = descobrir_tarefas(marcas, filtro_marca=args.marca)
            for t in tarefas:
                exists = conn.execute(
                    "SELECT 1 FROM modelos WHERE marca=? AND sub_categoria=? AND modelo=?",
                    (t["marca"], t["sub_cat"], t["modelo"])
                ).fetchone()
                if not exists:
                    novos_modelos += 1
                upsert_modelo(conn, t["marca"], t["sub_cat"], t["modelo"], t["url_modelo"], agora)
            conn.commit()
            log(f"{novos_modelos} modelos NOVOS detetados.", "OK")

        if args.limite:
            tarefas = tarefas[:args.limite]

        if not tarefas:
            log("Nenhum modelo encontrado. Site pode ter mudado ou estar em baixo. BD preservada.", "ERR")
            conn.execute("UPDATE execucoes SET data_fim=?, estado='erro_sem_dados' WHERE id=?",
                         (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), execucao_id))
            conn.commit()
            exportar_json(conn, args.json)
            exportar_historico_json(conn, args.historico)
            return

        # ── Fase 2: extração de preços (paralela) ──
        total      = len(tarefas)
        concluidos = 0
        chaves_vistas = set()
        lock = threading.Lock()

        log(f"A extrair preços de {total} modelos com {args.threads} threads...", "OK")

        with ThreadPoolExecutor(max_workers=args.threads) as executor:
            futuros = {executor.submit(processar_tarefa, t): t for t in tarefas}
            for futuro in as_completed(futuros):
                try:
                    tarefa, precos = futuro.result()
                except Exception as e:
                    log(f"Erro numa thread: {_msg_curta(e)}", "ERR")
                    continue
                with lock:
                    concluidos += 1
                    marca, sub_cat, modelo = tarefa["marca"], tarefa["sub_cat"], tarefa["modelo"]
                    for p in precos:
                        chave = (marca, sub_cat, modelo, p["servico"], p["qualidade"])
                        chaves_vistas.add(chave)
                        estado = upsert_preco(conn, marca, sub_cat, modelo,
                                               p["servico"], p["qualidade"], p["preco_eur"], agora)
                        if estado == "novo":       novos_precos += 1
                        elif estado == "alterado": alterados    += 1
                        else:                      iguais       += 1
                    if concluidos % 25 == 0 or concluidos == total:
                        conn.commit()
                    log(f"[{concluidos}/{total}] {modelo} — {len(precos)} preços "
                        f"({novos_precos} novos, {alterados} alterados)")

        conn.commit()

        # ── Fase 3: inativar o que desapareceu ──
        if not args.so_precos and not args.limite:
            n_inativos = marcar_inativos(conn, chaves_vistas, agora)
            conn.commit()
            if n_inativos:
                log(f"{n_inativos} reparações marcadas como inativas.", "WARN")

        # ── Fase 4: exportar JSON (só se tivermos dados) ──
        total_ativos = conn.execute("SELECT COUNT(*) FROM precos_reparacao WHERE ativo=1").fetchone()[0]
        if total_ativos == 0 and (novos_precos + alterados + iguais) == 0:
            log("ATENÇÃO: JSON não substituído — BD ficaria vazia.", "WARN")
        else:
            exportar_json(conn, args.json)
        exportar_historico_json(conn, args.historico)

        conn.execute("""UPDATE execucoes SET data_fim=?, modelos_novos=?, precos_novos=?,
                         precos_alterados=?, precos_iguais=?, inativados=?, estado='concluido' WHERE id=?""",
                     (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), novos_modelos, novos_precos,
                      alterados, iguais, n_inativos, execucao_id))
        conn.commit()

        duracao = (datetime.now() - inicio_dt).total_seconds()
        print()
        print("="*62)
        print(f"  Concluído em {duracao/60:.1f} min")
        print(f"  Modelos novos : {novos_modelos}")
        print(f"  Preços novos  : {novos_precos}")
        print(f"  Alterados     : {alterados}")
        print(f"  Iguais        : {iguais}")
        print(f"  Inativados    : {n_inativos}")
        print("="*62)

    except Exception as e:
        conn.execute("UPDATE execucoes SET data_fim=?, estado='erro' WHERE id=?",
                     (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), execucao_id))
        conn.commit()
        raise
    finally:
        fechar_todos_navegadores()
        conn.close()


if __name__ == "__main__":
    main()

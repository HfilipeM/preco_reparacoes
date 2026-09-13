#!/usr/bin/env python3
"""
iServices Portugal - Sincronizador de Preços
================================================
Ver README.md na raiz do projeto para instruções completas.

Uso:
    python iservices_sync.py --db ../data/iservices.db --json ../data/precos.json
    python iservices_sync.py --marca Apple --limite 10
"""

import sys, os, re, json, time, random, argparse, subprocess, threading, sqlite3
from datetime import datetime
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import deque

def _instalar(pkg_import, pkg_pip=None):
    try:
        __import__(pkg_import)
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", pkg_pip or pkg_import,
                        "--break-system-packages", "-q"], check=False)

_instalar("bs4", "beautifulsoup4")
_instalar("requests")
_instalar("playwright")

import requests
from bs4 import BeautifulSoup

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError
    _PLAYWRIGHT_OK = True
except ImportError:
    _PLAYWRIGHT_OK = False

BASE_URL     = "https://iservices.pt"
THREADS      = 8
DELAY_PEDIDO = (0.1, 0.4)
DELAY_429    = (45, 90)
TIMEOUT      = 12

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
]

MARCAS_FALLBACK = [
    "apple","samsung","xiaomi","huawei","oppo","oneplus","google",
    "dyson","realme","microsoft","asus","alcatel","tcl","honor",
    "nokia","motorola","lenovo","dell","nintendo","wiko","bq","htc","nothing",
]

_print_lock = threading.Lock()

def log(msg, nivel="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    s  = {"INFO":"i","OK":"OK","WARN":"!!","ERR":"XX","PW":"JS"}
    with _print_lock:
        print(f"[{ts}] {s.get(nivel,'-')}  {msg}", flush=True)

# ── HTTP híbrido ──────────────────────────────────────────────────────────────

def _headers(referer=None):
    h = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pt-PT,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "DNT": "1",
    }
    if referer:
        h["Referer"] = referer
    return h


def get_html_requests(url, sessao, referer=None, tentativas=2):
    for i in range(tentativas):
        try:
            r = sessao.get(url, headers=_headers(referer), timeout=TIMEOUT, allow_redirects=True)
            if r.status_code == 200:
                r.encoding = r.apparent_encoding or "utf-8"
                return BeautifulSoup(r.text, "html.parser")
            elif r.status_code == 429:
                time.sleep(random.uniform(*DELAY_429))
            else:
                time.sleep(random.uniform(1, 3))
        except requests.exceptions.RequestException:
            time.sleep(random.uniform(1, 3))
    return None


def delay():
    time.sleep(random.uniform(*DELAY_PEDIDO))

# ── Playwright thread-local (fallback) ─────────────────────────────────────────

_local_thread = threading.local()
_navs_criados = []
_navs_lock = threading.Lock()
_chromium_verificado = False
_chromium_lock = threading.Lock()


class NavegadorJS:
    def __init__(self):
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch(headless=True)
        self.context = self.browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={"width": 1366, "height": 850}, locale="pt-PT",
        )
        self.context.route(
            re.compile(r"\.(png|jpg|jpeg|gif|webp|svg|woff2?|ttf|mp4)(\?.*)?$"),
            lambda route: route.abort(),
        )
        self.page = self.context.new_page()
        self.page.set_default_timeout(20000)

    def obter_html(self, url):
        try:
            self.page.goto(url, wait_until="domcontentloaded")
            try:
                self.page.wait_for_load_state("networkidle", timeout=8000)
            except PWTimeoutError:
                pass
            self.page.wait_for_timeout(300)
            return self.page.content()
        except Exception as e:
            log(f"Playwright falhou em {url}: {e}", "WARN")
            return None

    def fechar(self):
        try:
            self.context.close(); self.browser.close(); self._pw.stop()
        except Exception:
            pass


def _garantir_browser_chromium():
    global _chromium_verificado
    with _chromium_lock:
        if _chromium_verificado:
            return
        try:
            with sync_playwright() as p:
                b = p.chromium.launch(headless=True); b.close()
        except Exception as e:
            if "Executable doesn't exist" in str(e):
                log("A instalar o browser Chromium (só na 1ª vez, ~150MB)...", "PW")
                subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=False)
        _chromium_verificado = True


def obter_navegador_js():
    if not _PLAYWRIGHT_OK:
        return None
    nav = getattr(_local_thread, "nav", None)
    if nav is None:
        _garantir_browser_chromium()
        nav = NavegadorJS()
        _local_thread.nav = nav
        with _navs_lock:
            _navs_criados.append(nav)
    return nav


def fechar_todos_navegadores_js():
    with _navs_lock:
        for nav in _navs_criados:
            nav.fechar()
        _navs_criados.clear()


def _pagina_parece_util(soup):
    if not soup:
        return False
    if soup.select_one(".box-content"):
        return True
    if soup.find("a", href=True) or soup.find("option", value=True):
        return True
    return False


def obter_soup(url, sessao, referer=None):
    soup = get_html_requests(url, sessao, referer=referer)
    if _pagina_parece_util(soup):
        return soup, "requests"
    nav = obter_navegador_js()
    if nav is None:
        return soup, "requests"
    html_js = nav.obter_html(url)
    if html_js:
        return BeautifulSoup(html_js, "html.parser"), "playwright"
    return soup, "requests"

# ── Parsing de URLs ────────────────────────────────────────────────────────────

def segmentos(href, base=BASE_URL):
    abs_url = urljoin(base, href)
    p = urlparse(abs_url)
    if "iservices.pt" not in p.netloc:
        return None
    return tuple(s for s in p.path.split("/") if s)

def url_de(segs):
    return BASE_URL + "/" + "/".join(segs)

# ── Extração de preços ──────────────────────────────────────────────────────────

def classificar_qualidade(titulo):
    t = titulo.strip(); tl = t.lower()
    if re.search(r"\boriginal\b", tl):
        return re.sub(r"\boriginal\b", "", t, flags=re.IGNORECASE).strip(" -/"), "Original"
    if "iservices" in tl:
        return re.sub(r"iservices", "", t, flags=re.IGNORECASE).strip(" -/®"), "Compatível (iServices)"
    if re.search(r"\bcompat[ií]vel\b", tl):
        return re.sub(r"\bcompat[ií]vel\b", "", t, flags=re.IGNORECASE).strip(" -/"), "Compatível"
    return t, "Padrão"


def extrair_precos(soup):
    precos = []
    for bloco in soup.select("div.box-content"):
        titulo_el = bloco.select_one(".box-content-title")
        preco_el  = bloco.select_one(".repair-price")
        if not titulo_el or not preco_el:
            continue
        m = re.search(r"(\d[\d\s]*[,\.]\d{2})", preco_el.get_text().replace("\xa0", ""))
        if not m:
            continue
        try:
            preco = float(m.group(1).replace(".", "").replace(",", "."))
        except ValueError:
            continue
        titulo = re.sub(r"^Reparar\s+", "", titulo_el.get_text(strip=True)).strip()
        nome, qualidade = classificar_qualidade(titulo)
        if nome:
            precos.append({"servico": nome, "qualidade": qualidade, "preco_eur": preco})
    return precos

# ── Descoberta recursiva genérica ──────────────────────────────────────────────

def extrair_links_navegacao(soup, base_url):
    links = []
    fontes = soup.find_all("a", href=True) + soup.find_all("option", value=True)
    for el in fontes:
        raw = el.get("href") or el.get("value")
        if not raw:
            continue
        segs = segmentos(raw, base_url)
        if not segs or segs[0] != "reparacao" or len(segs) < 2:
            continue
        nome = el.get("label") or el.get_text(strip=True)
        links.append((url_de(segs), nome))
    return links


def crawl_marca(marca_nome, url_inicial, sessao, stats):
    tarefas = []
    visitados = {url_inicial}
    nomes_link = {}
    fila = deque([url_inicial])

    while fila:
        url = fila.popleft()
        soup, metodo = obter_soup(url, sessao, referer=BASE_URL + "/reparacao")
        with stats["lock"]:
            stats[metodo] = stats.get(metodo, 0) + 1
        if not soup:
            continue

        if soup.select_one(".box-content"):
            segs = segmentos(url) or ()
            sub_cat = segs[2].replace("-", " ").title() if len(segs) > 2 else marca_nome
            modelo_nome = nomes_link.get(url) or (segs[-1].replace("-", " ").title() if segs else url)
            tarefas.append({"marca": marca_nome, "sub_cat": sub_cat, "modelo": modelo_nome, "url": url})
            continue

        for link_url, nome in extrair_links_navegacao(soup, url):
            if link_url not in visitados:
                visitados.add(link_url)
                if nome and link_url not in nomes_link:
                    nomes_link[link_url] = nome
                fila.append(link_url)
        delay()

    return tarefas


def descobrir_marcas(sessao):
    log("A obter lista de marcas...")
    soup, _ = obter_soup(BASE_URL + "/reparacao", sessao)
    marcas = {}
    if soup:
        for link_url, nome in extrair_links_navegacao(soup, BASE_URL + "/reparacao"):
            segs = segmentos(link_url)
            if segs and len(segs) == 2:
                marcas.setdefault(segs[1], {"nome": nome or segs[1].capitalize(), "url": link_url})
    if not marcas:
        log("Não encontrei marcas no HTML. A usar fallback.", "WARN")
        marcas = {s: {"nome": s.capitalize(), "url": f"{BASE_URL}/reparacao/{s}"} for s in MARCAS_FALLBACK}
    log(f"{len(marcas)} marcas encontradas.", "OK")
    return list(marcas.values())


def descobrir_tarefas(marcas, sessao, filtro_marca=None, threads_descoberta=4):
    marcas_a_processar = [m for m in marcas
                          if not filtro_marca or m["nome"].lower() == filtro_marca.lower()]
    stats = {"requests": 0, "playwright": 0, "lock": threading.Lock()}
    todas_tarefas = []
    with ThreadPoolExecutor(max_workers=min(threads_descoberta, len(marcas_a_processar) or 1)) as ex:
        futuros = {ex.submit(crawl_marca, m["nome"], m["url"], sessao, stats): m for m in marcas_a_processar}
        for futuro in as_completed(futuros):
            m = futuros[futuro]
            try:
                t = futuro.result()
                todas_tarefas.extend(t)
                log(f"[{m['nome']}] {len(t)} modelos mapeados.", "OK")
            except Exception as e:
                log(f"[{m['nome']}] Erro: {e}", "ERR")
    log(f"Descoberta: {stats['requests']} requests, {stats.get('playwright',0)} Playwright.", "OK")
    return todas_tarefas

# ── Base de dados SQLite ─────────────────────────────────────────────────────

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
CREATE INDEX IF NOT EXISTS idx_precos_ativo ON precos_reparacao(ativo);
"""


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
        return "novo", None

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
        return "alterado", preco_antigo
    else:
        conn.execute("""
            UPDATE precos_reparacao SET ativo=1, ultima_verificacao=?
            WHERE marca=? AND sub_categoria=? AND modelo=? AND servico=? AND qualidade=?
        """, (agora, marca, sub_cat, modelo, servico, qualidade))
        return "igual", None


def marcar_inativos(conn, chaves_vistas_agora, agora):
    cur = conn.execute("""SELECT marca, sub_categoria, modelo, servico, qualidade
                           FROM precos_reparacao WHERE ativo=1 AND origem='scraping'""")
    n = 0
    for row in cur.fetchall():
        if tuple(row) not in chaves_vistas_agora:
            conn.execute("""
                UPDATE precos_reparacao SET ativo=0
                WHERE marca=? AND sub_categoria=? AND modelo=? AND servico=? AND qualidade=?
            """, row)
            n += 1
    return n

# ── Exports para a app web (JSON) ───────────────────────────────────────────────

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


def exportar_historico_json(conn, caminho, limite=500):
    cur = conn.execute("""
        SELECT marca, sub_categoria, modelo, servico, qualidade,
               preco_antigo, preco_novo, diferenca, data
        FROM historico_precos ORDER BY data DESC LIMIT ?
    """, (limite,))
    alteracoes = [dict(zip(
        ["marca","sub_categoria","modelo","servico","qualidade","preco_antigo","preco_novo","diferenca","data"], r
    )) for r in cur.fetchall()]

    cur2 = conn.execute("""
        SELECT acao, marca, sub_categoria, modelo, servico, qualidade, preco_antigo, preco_novo, data
        FROM edicoes_manuais ORDER BY data DESC LIMIT ?
    """, (limite,))
    edicoes = [dict(zip(
        ["acao","marca","sub_categoria","modelo","servico","qualidade","preco_antigo","preco_novo","data"], r
    )) for r in cur2.fetchall()]

    cur3 = conn.execute("""
        SELECT data_inicio, data_fim, modo, modelos_novos, precos_novos,
               precos_alterados, precos_iguais, inativados, estado
        FROM execucoes ORDER BY data_inicio DESC LIMIT 100
    """)
    execucoes = [dict(zip(
        ["data_inicio","data_fim","modo","modelos_novos","precos_novos",
         "precos_alterados","precos_iguais","inativados","estado"], r
    )) for r in cur3.fetchall()]

    dados = {"gerado_em": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
             "alteracoes_preco": alteracoes, "edicoes_manuais": edicoes, "execucoes": execucoes}
    os.makedirs(os.path.dirname(os.path.abspath(caminho)) or ".", exist_ok=True)
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)
    log(f"Histórico exportado: {caminho}", "OK")

# ── Worker de extração paralela ────────────────────────────────────────────────

def processar_modelo(tarefa, sessao):
    soup, metodo = obter_soup(tarefa["url"], sessao, referer=BASE_URL + "/reparacao")
    precos = extrair_precos(soup) if soup else []
    return tarefa, precos, metodo

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Sincronizador de preços iServices")
    ap.add_argument("--db",          default="../data/iservices.db")
    ap.add_argument("--json",        default="../data/precos.json")
    ap.add_argument("--historico",   default="../data/historico.json")
    ap.add_argument("--so-precos",   action="store_true")
    ap.add_argument("--marca",       default=None)
    ap.add_argument("--limite",      type=int, default=None)
    ap.add_argument("--threads",     type=int, default=THREADS)
    args = ap.parse_args()

    inicio_dt = datetime.now()
    inicio_str = inicio_dt.strftime("%Y-%m-%d %H:%M:%S")
    modo = "so-precos" if args.so_precos else "completo"

    print()
    print("="*62)
    print("  iServices - Sincronizador de Precos")
    print("="*62)
    print(f"  BD   : {args.db}")
    print(f"  Modo : {modo}")
    if args.marca:  print(f"  Marca: {args.marca}")
    if args.limite: print(f"  Limite: {args.limite}")
    print("="*62); print()

    conn = abrir_bd(args.db)
    cur = conn.execute("""INSERT INTO execucoes (data_inicio, modo, estado) VALUES (?, ?, 'em_curso')""",
                        (inicio_str, modo))
    execucao_id = cur.lastrowid
    conn.commit()

    sessao = requests.Session()
    sessao.headers.update({"Accept-Encoding": "gzip, deflate"})
    agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    novos_modelos = novos_precos = alterados = iguais = n_inativos = 0

    try:
        if args.so_precos:
            cur = conn.execute("SELECT marca, sub_categoria, modelo, url FROM modelos")
            tarefas = [{"marca": r[0], "sub_cat": r[1], "modelo": r[2], "url": r[3]} for r in cur.fetchall()]
            if args.marca:
                tarefas = [t for t in tarefas if t["marca"].lower() == args.marca.lower()]
            log(f"{len(tarefas)} modelos já conhecidos na BD.", "OK")
        else:
            marcas = descobrir_marcas(sessao)
            tarefas = descobrir_tarefas(marcas, sessao, filtro_marca=args.marca)
            for t in tarefas:
                cur = conn.execute("SELECT 1 FROM modelos WHERE marca=? AND sub_categoria=? AND modelo=?",
                                   (t["marca"], t["sub_cat"], t["modelo"]))
                if cur.fetchone() is None:
                    novos_modelos += 1
                upsert_modelo(conn, t["marca"], t["sub_cat"], t["modelo"], t["url"], agora)
            conn.commit()
            log(f"{novos_modelos} modelos NOVOS detetados.", "OK")

        if args.limite:
            tarefas = tarefas[: args.limite]

        if not tarefas:
            log("Nenhum modelo para processar.", "ERR")
            conn.execute("UPDATE execucoes SET data_fim=?, estado='erro' WHERE id=?",
                         (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), execucao_id))
            conn.commit()
            return

        total = len(tarefas)
        log(f"A extrair preços de {total} modelos com {args.threads} threads...", "OK")
        concluidos = 0
        chaves_vistas = set()
        lock = threading.Lock()

        with ThreadPoolExecutor(max_workers=args.threads) as executor:
            futuros = {executor.submit(processar_modelo, t, sessao): t for t in tarefas}
            for futuro in as_completed(futuros):
                try:
                    tarefa, precos, metodo = futuro.result()
                except Exception as e:
                    log(f"Erro numa thread: {e}", "ERR")
                    continue
                with lock:
                    concluidos += 1
                    marca, sub_cat, modelo = tarefa["marca"], tarefa["sub_cat"], tarefa["modelo"]
                    for p in precos:
                        chaves_vistas.add((marca, sub_cat, modelo, p["servico"], p["qualidade"]))
                        estado, preco_antigo = upsert_preco(
                            conn, marca, sub_cat, modelo, p["servico"], p["qualidade"], p["preco_eur"], agora
                        )
                        if estado == "novo": novos_precos += 1
                        elif estado == "alterado": alterados += 1
                        else: iguais += 1
                    if concluidos % 50 == 0 or concluidos == total:
                        conn.commit()
                    log(f"[{concluidos}/{total}] processados ({novos_precos} novos, {alterados} alterados)")

        conn.commit()

        if not args.so_precos and not args.limite:
            n_inativos = marcar_inativos(conn, chaves_vistas, agora)
            conn.commit()
            if n_inativos:
                log(f"{n_inativos} reparações marcadas como inativas.", "WARN")

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
        print(f"  Modelos novos: {novos_modelos} | Precos novos: {novos_precos} | "
              f"Alterados: {alterados} | Inativados: {n_inativos}")
        print("="*62)

    except Exception as e:
        conn.execute("UPDATE execucoes SET data_fim=?, estado='erro' WHERE id=?",
                     (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), execucao_id))
        conn.commit()
        raise
    finally:
        fechar_todos_navegadores_js()
        conn.close()


if __name__ == "__main__":
    main()

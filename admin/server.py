#!/usr/bin/env python3
"""
Painel de Preços - Servidor de administração local
=====================================================
Corre SÓ na tua máquina (nunca é publicado no GitHub Pages).
Serve o painel de administração e as APIs de editar/apagar/adicionar
reparações, e permite disparar a atualização (scraping) com barra de
progresso ao vivo.

Uso:
    python server.py
    (depois abre http://localhost:5050 no browser)
"""

import os
import re
import sys
import json
import sqlite3
import subprocess
import threading
import webbrowser
from datetime import datetime

try:
    from flask import Flask, jsonify, request, send_from_directory
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "flask", "--break-system-packages", "-q"], check=False)
    from flask import Flask, jsonify, request, send_from_directory

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
REPO_DIR   = os.path.dirname(BASE_DIR)
DATA_DIR   = os.path.join(REPO_DIR, "data")
DB_PATH    = os.path.join(DATA_DIR, "iservices.db")
JSON_PATH  = os.path.join(DATA_DIR, "precos.json")
HIST_PATH  = os.path.join(DATA_DIR, "historico.json")
SYNC_SCRIPT = os.path.join(REPO_DIR, "scripts", "iservices_sync.py")

os.makedirs(DATA_DIR, exist_ok=True)

app = Flask(__name__, static_folder=None)

CAMPOS_OBRIGATORIOS = ["marca", "sub_categoria", "modelo", "servico", "qualidade", "preco_eur"]

# ── Estado partilhado do progresso de sincronização ─────────────────────────

progresso_lock = threading.Lock()
progresso = {"a_correr": False, "atual": 0, "total": 0, "mensagem": "", "erro": None}


def _agora():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _bd():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS precos_reparacao (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            marca TEXT NOT NULL, sub_categoria TEXT NOT NULL, modelo TEXT NOT NULL,
            servico TEXT NOT NULL, qualidade TEXT NOT NULL, preco_eur REAL NOT NULL,
            ativo INTEGER NOT NULL DEFAULT 1, origem TEXT NOT NULL DEFAULT 'scraping',
            primeira_deteccao TEXT NOT NULL, ultima_verificacao TEXT NOT NULL,
            UNIQUE(marca, sub_categoria, modelo, servico, qualidade)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS edicoes_manuais (
            id INTEGER PRIMARY KEY AUTOINCREMENT, acao TEXT NOT NULL,
            marca TEXT, sub_categoria TEXT, modelo TEXT, servico TEXT, qualidade TEXT,
            preco_antigo REAL, preco_novo REAL, data TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


def _reexportar():
    """Depois de qualquer alteração manual, regenera precos.json e historico.json
    para que fiquem consistentes com a base de dados (prontos a publicar)."""
    conn = _bd()
    try:
        cur = conn.execute("""
            SELECT marca, sub_categoria, modelo, servico, qualidade, preco_eur, ultima_verificacao
            FROM precos_reparacao WHERE ativo=1 ORDER BY marca, modelo, servico, qualidade
        """)
        precos = [dict(zip(
            ["marca","sub_categoria","modelo","servico","qualidade","preco_eur","ultima_verificacao"], r
        )) for r in cur.fetchall()]
        with open(JSON_PATH, "w", encoding="utf-8") as f:
            json.dump({"gerado_em": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                       "total_precos": len(precos), "precos": precos}, f, ensure_ascii=False, indent=2)

        cur2 = conn.execute("""
            SELECT acao, marca, sub_categoria, modelo, servico, qualidade, preco_antigo, preco_novo, data
            FROM edicoes_manuais ORDER BY data DESC LIMIT 500
        """)
        edicoes = [dict(zip(
            ["acao","marca","sub_categoria","modelo","servico","qualidade","preco_antigo","preco_novo","data"], r
        )) for r in cur2.fetchall()]

        hist_existente = {"alteracoes_preco": [], "execucoes": []}
        if os.path.exists(HIST_PATH):
            try:
                with open(HIST_PATH, "r", encoding="utf-8") as f:
                    hist_existente = json.load(f)
            except Exception:
                pass
        hist_existente["edicoes_manuais"] = edicoes
        hist_existente["gerado_em"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        with open(HIST_PATH, "w", encoding="utf-8") as f:
            json.dump(hist_existente, f, ensure_ascii=False, indent=2)
    finally:
        conn.close()


# ── Páginas estáticas ────────────────────────────────────────────────────────

@app.route("/")
def home():
    return send_from_directory(BASE_DIR, "admin.html")


@app.route("/admin.js")
def admin_js():
    return send_from_directory(BASE_DIR, "admin.js")


@app.route("/assets/<path:filename>")
def assets(filename):
    """Serve os ficheiros partilhados (style.css, shared.js, historico.js)
    que vivem na pasta /assets na raiz do projeto, um nível acima de /admin."""
    return send_from_directory(os.path.join(REPO_DIR, "assets"), filename)


# ── API: listar preços ──────────────────────────────────────────────────────

@app.route("/api/precos")
def listar_precos():
    conn = _bd()
    cur = conn.execute("""
        SELECT id, marca, sub_categoria, modelo, servico, qualidade, preco_eur, ultima_verificacao
        FROM precos_reparacao WHERE ativo=1 ORDER BY marca, modelo, servico, qualidade
    """)
    dados = [dict(zip(
        ["id","marca","sub_categoria","modelo","servico","qualidade","preco_eur","ultima_verificacao"], r
    )) for r in cur.fetchall()]
    conn.close()
    return jsonify(dados)


# ── API: criar novo preço/reparação ─────────────────────────────────────────

@app.route("/api/precos", methods=["POST"])
def criar_preco():
    body = request.get_json(force=True, silent=True) or {}
    faltam = [c for c in CAMPOS_OBRIGATORIOS if body.get(c) in (None, "")]
    if faltam:
        return jsonify({"ok": False, "erro": f"Campos em falta: {', '.join(faltam)}"}), 400
    try:
        preco = float(str(body["preco_eur"]).replace(",", "."))
    except ValueError:
        return jsonify({"ok": False, "erro": "Preço inválido."}), 400

    agora = _agora()
    conn = _bd()
    try:
        conn.execute("""
            INSERT INTO precos_reparacao
                (marca, sub_categoria, modelo, servico, qualidade, preco_eur,
                 ativo, origem, primeira_deteccao, ultima_verificacao)
            VALUES (?, ?, ?, ?, ?, ?, 1, 'manual', ?, ?)
        """, (body["marca"], body["sub_categoria"], body["modelo"], body["servico"],
              body["qualidade"], preco, agora, agora))
        conn.execute("""
            INSERT INTO edicoes_manuais (acao, marca, sub_categoria, modelo, servico, qualidade,
                                         preco_antigo, preco_novo, data)
            VALUES ('criado', ?, ?, ?, ?, ?, NULL, ?, ?)
        """, (body["marca"], body["sub_categoria"], body["modelo"], body["servico"],
              body["qualidade"], preco, agora))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"ok": False, "erro": "Já existe uma reparação igual (mesma marca/modelo/serviço/qualidade)."}), 409
    conn.close()
    _reexportar()
    return jsonify({"ok": True})


# ── API: editar preço existente ──────────────────────────────────────────────

@app.route("/api/precos/<int:item_id>", methods=["PUT"])
def editar_preco(item_id):
    body = request.get_json(force=True, silent=True) or {}
    conn = _bd()
    cur = conn.execute("""
        SELECT marca, sub_categoria, modelo, servico, qualidade, preco_eur
        FROM precos_reparacao WHERE id=?
    """, (item_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return jsonify({"ok": False, "erro": "Reparação não encontrada."}), 404

    marca, sub_cat, modelo, servico_atual, qualidade_atual, preco_antigo = row
    novo_servico   = body.get("servico", servico_atual)
    nova_qualidade = body.get("qualidade", qualidade_atual)
    try:
        novo_preco = float(str(body.get("preco_eur", preco_antigo)).replace(",", "."))
    except ValueError:
        conn.close()
        return jsonify({"ok": False, "erro": "Preço inválido."}), 400

    agora = _agora()
    conn.execute("""
        UPDATE precos_reparacao SET servico=?, qualidade=?, preco_eur=?, ultima_verificacao=?
        WHERE id=?
    """, (novo_servico, nova_qualidade, novo_preco, agora, item_id))
    conn.execute("""
        INSERT INTO edicoes_manuais (acao, marca, sub_categoria, modelo, servico, qualidade,
                                     preco_antigo, preco_novo, data)
        VALUES ('editado', ?, ?, ?, ?, ?, ?, ?, ?)
    """, (marca, sub_cat, modelo, novo_servico, nova_qualidade, preco_antigo, novo_preco, agora))
    conn.commit()
    conn.close()
    _reexportar()
    return jsonify({"ok": True})


# ── API: apagar preço ────────────────────────────────────────────────────────

@app.route("/api/precos/<int:item_id>", methods=["DELETE"])
def apagar_preco(item_id):
    conn = _bd()
    cur = conn.execute("""
        SELECT marca, sub_categoria, modelo, servico, qualidade, preco_eur
        FROM precos_reparacao WHERE id=?
    """, (item_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return jsonify({"ok": False, "erro": "Reparação não encontrada."}), 404

    marca, sub_cat, modelo, servico, qualidade, preco = row
    agora = _agora()
    conn.execute("DELETE FROM precos_reparacao WHERE id=?", (item_id,))
    conn.execute("""
        INSERT INTO edicoes_manuais (acao, marca, sub_categoria, modelo, servico, qualidade,
                                     preco_antigo, preco_novo, data)
        VALUES ('apagado', ?, ?, ?, ?, ?, ?, NULL, ?)
    """, (marca, sub_cat, modelo, servico, qualidade, preco, agora))
    conn.commit()
    conn.close()
    _reexportar()
    return jsonify({"ok": True})


# ── API: histórico ───────────────────────────────────────────────────────────

@app.route("/api/historico")
def historico():
    if os.path.exists(HIST_PATH):
        with open(HIST_PATH, "r", encoding="utf-8") as f:
            return jsonify(json.load(f))
    return jsonify({"alteracoes_preco": [], "edicoes_manuais": [], "execucoes": []})


# ── API: disparar sincronização (scraping) ──────────────────────────────────

def _correr_sync(modo):
    global progresso
    cmd = [sys.executable, SYNC_SCRIPT, "--db", DB_PATH, "--json", JSON_PATH, "--historico", HIST_PATH]
    if modo == "so-precos":
        cmd.append("--so-precos")

    with progresso_lock:
        progresso.update(a_correr=True, atual=0, total=0, mensagem="A iniciar sincronização...", erro=None)

    padrao = re.compile(r"\[(\d+)/(\d+)\]")
    try:
        proc = subprocess.Popen(cmd, cwd=os.path.dirname(SYNC_SCRIPT),
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, bufsize=1)
        for linha in proc.stdout:
            linha = linha.strip()
            if not linha:
                continue
            m = padrao.search(linha)
            with progresso_lock:
                if m:
                    progresso["atual"] = int(m.group(1))
                    progresso["total"] = int(m.group(2))
                progresso["mensagem"] = linha
        proc.wait()
        with progresso_lock:
            if proc.returncode != 0:
                progresso["erro"] = f"O processo terminou com erro (código {proc.returncode})."
    except Exception as e:
        with progresso_lock:
            progresso["erro"] = str(e)
    finally:
        with progresso_lock:
            progresso["a_correr"] = False
            progresso["mensagem"] = "Concluído." if not progresso["erro"] else f"Erro: {progresso['erro']}"


@app.route("/api/scrape/start", methods=["POST"])
def scrape_start():
    with progresso_lock:
        if progresso["a_correr"]:
            return jsonify({"ok": False, "erro": "Já está uma atualização em curso."}), 409
    body = request.get_json(force=True, silent=True) or {}
    modo = body.get("modo", "completo")
    threading.Thread(target=_correr_sync, args=(modo,), daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/scrape/status")
def scrape_status():
    with progresso_lock:
        return jsonify(dict(progresso))


# ── API: publicar no GitHub (git add + commit + push) ───────────────────────

@app.route("/api/publicar", methods=["POST"])
def publicar():
    """Faz git add dos JSONs, commit e push para o GitHub.
    Retorna {ok, mensagem} — o frontend mostra o resultado ao utilizador."""
    try:
        import subprocess as sp

        # Garante que estamos na raiz do repositório
        repo = REPO_DIR

        # 1. git add dos ficheiros que podem ter mudado
        sp.run(["git", "add", "data/precos.json", "data/historico.json"],
               cwd=repo, check=True, capture_output=True, text=True)

        # 2. Verificar se há algo para commitar
        diff = sp.run(["git", "diff", "--cached", "--quiet"],
                      cwd=repo, capture_output=True)
        if diff.returncode == 0:
            return jsonify({"ok": True, "mensagem": "Sem alterações para publicar — o site já está atualizado."})

        # 3. Commit com data/hora automática
        agora = datetime.now().strftime("%d/%m/%Y %H:%M")
        sp.run(["git", "commit", "-m", f"Preços atualizados manualmente ({agora})"],
               cwd=repo, check=True, capture_output=True, text=True)

        # 4. Push
        push = sp.run(["git", "push"],
                      cwd=repo, capture_output=True, text=True)
        if push.returncode != 0:
            return jsonify({"ok": False, "mensagem": f"git push falhou: {push.stderr.strip()}"}), 500

        return jsonify({"ok": True, "mensagem": "Publicado! O site atualiza em 1-2 minutos."})

    except sp.CalledProcessError as e:
        return jsonify({"ok": False, "mensagem": f"Erro git: {e.stderr.strip() if e.stderr else str(e)}"}), 500
    except Exception as e:
        return jsonify({"ok": False, "mensagem": str(e)}), 500


if __name__ == "__main__":
    print()
    print("=" * 60)
    print("  Painel de Preços - servidor local de administração")
    print("=" * 60)
    print("  A abrir em: http://localhost:5050")
    print("  Prime Ctrl+C nesta janela para parar o servidor.")
    print("=" * 60)
    print()

    def _abrir_browser():
        try:
            webbrowser.open("http://localhost:5050")
        except Exception:
            pass  # sem browser disponível (ex: servidor sem interface gráfica) - sem problema

    if not os.environ.get("PRICEBOARD_NO_BROWSER"):
        threading.Timer(1.2, _abrir_browser).start()

    app.run(host="127.0.0.1", port=5050, debug=False, use_reloader=False)

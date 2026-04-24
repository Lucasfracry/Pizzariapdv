import os
import sys
import json
import sqlite3
import webbrowser
from threading import Timer
from flask import Flask, render_template, request, jsonify

# =========================
# PATHS / EXECUTÁVEL
# =========================
def resource_path(relative_path: str) -> str:
    try:
        base_path = sys._MEIPASS  # type: ignore[attr-defined]
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

APP_DIR = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else __file__)
DB_PATH = os.path.join(APP_DIR, "pizzas.db")

app = Flask(
    __name__,
    template_folder=resource_path("templates"),
    static_folder=resource_path("static"),
)

# =========================
# DB HELPERS
# =========================
def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def table_has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table});")
    cols = [r["name"] for r in cur.fetchall()]
    return column in cols

def init_db():
    conn = get_conn()
    cur = conn.cursor()

    # Itens do cardápio
    cur.execute("""
    CREATE TABLE IF NOT EXISTS items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT NOT NULL CHECK(type IN ('pizza','borda','outros')),
        code INTEGER NOT NULL UNIQUE,
        name TEXT NOT NULL,
        price_broto REAL,
        price_grande REAL,
        price REAL,
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    );
    """)

    # Caixa (sessões)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS cash_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        opened_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        closed_at TEXT,
        opening_amount REAL NOT NULL DEFAULT 0,
        closing_amount_reported REAL,
        closing_amount_expected REAL,
        diff REAL,
        status TEXT NOT NULL CHECK(status IN ('OPEN','CLOSED')) DEFAULT 'OPEN',
        notes TEXT
    );
    """)

    # Movimentos do caixa
    cur.execute("""
    CREATE TABLE IF NOT EXISTS cash_moves (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER NOT NULL,
        move_type TEXT NOT NULL CHECK(move_type IN ('SUPRIMENTO','SANGRIA')),
        amount REAL NOT NULL,
        reason TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        FOREIGN KEY(session_id) REFERENCES cash_sessions(id) ON DELETE CASCADE
    );
    """)

    # Pedido (cabeçalho)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_type TEXT NOT NULL CHECK(order_type IN ('BALCAO','MESA','DELIVERY')),
        customer TEXT,
        payment TEXT,
        notes TEXT,
        total REAL NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    );
    """)

    # Se não existir, adiciona a coluna session_id em orders (para vincular ao caixa)
    if not table_has_column(conn, "orders", "session_id"):
        cur.execute("ALTER TABLE orders ADD COLUMN session_id INTEGER;")

    # Itens do pedido
    cur.execute("""
    CREATE TABLE IF NOT EXISTS order_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL,
        kind TEXT NOT NULL,
        description TEXT NOT NULL,
        qty INTEGER NOT NULL DEFAULT 1,
        unit_price REAL NOT NULL,
        total REAL NOT NULL,
        meta_json TEXT,
        FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE CASCADE
    );
    """)

    conn.commit()
    conn.close()

def seed_if_empty():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM items;")
    c = cur.fetchone()["c"]
    if c == 0:
        cur.executemany("""
            INSERT INTO items(type, code, name, price_broto, price_grande)
            VALUES('pizza', ?, ?, ?, ?)
        """, [
            (1, "Mussarela", 25.0, 45.0),
            (2, "Calabresa", 27.0, 47.0),
            (3, "Frango c/ Catupiry", 30.0, 52.0),
        ])
        cur.executemany("""
            INSERT INTO items(type, code, name, price)
            VALUES('borda', ?, ?, ?)
        """, [
            (101, "Borda Catupiry", 8.0),
            (102, "Borda Cheddar", 8.0),
        ])
        cur.executemany("""
            INSERT INTO items(type, code, name, price)
            VALUES('outros', ?, ?, ?)
        """, [
            (201, "Coca-Cola Lata", 6.0),
            (202, "Guaraná Lata", 6.0),
        ])
        conn.commit()
    conn.close()

# =========================
# CAIXA HELPERS
# =========================
def get_current_session(conn: sqlite3.Connection):
    cur = conn.cursor()
    cur.execute("""
        SELECT * FROM cash_sessions
        WHERE status='OPEN'
        ORDER BY id DESC
        LIMIT 1;
    """)
    return cur.fetchone()

def compute_session_totals(conn: sqlite3.Connection, session_id: int) -> dict:
    cur = conn.cursor()

    cur.execute("SELECT opening_amount FROM cash_sessions WHERE id=?;", (session_id,))
    row = cur.fetchone()
    opening = float(row["opening_amount"]) if row else 0.0

    cur.execute("SELECT COALESCE(SUM(total), 0) AS s FROM orders WHERE session_id=?;", (session_id,))
    sales = float(cur.fetchone()["s"])

    cur.execute("""
        SELECT
          COALESCE(SUM(CASE WHEN move_type='SUPRIMENTO' THEN amount ELSE 0 END),0) AS supr,
          COALESCE(SUM(CASE WHEN move_type='SANGRIA' THEN amount ELSE 0 END),0) AS sang
        FROM cash_moves
        WHERE session_id=?;
    """, (session_id,))
    mv = cur.fetchone()
    supr = float(mv["supr"])
    sang = float(mv["sang"])

    expected = round(opening + supr - sang + sales, 2)
    return {
        "opening": round(opening, 2),
        "sales": round(sales, 2),
        "suprimento": round(supr, 2),
        "sangria": round(sang, 2),
        "expected": expected
    }

# =========================
# ROTAS PÁGINA
# =========================
@app.route("/")
def index():
    return render_template("index.html")

# =========================
# API: ITENS
# =========================
@app.get("/api/items")
def api_items_list():
    item_type = request.args.get("type")
    q = request.args.get("q", "").strip()

    conn = get_conn()
    cur = conn.cursor()

    where = ["active = 1"]
    params = []

    if item_type in ("pizza", "borda", "outros"):
        where.append("type = ?")
        params.append(item_type)

    if q:
        where.append("(name LIKE ? OR CAST(code AS TEXT) LIKE ?)")
        params.extend([f"%{q}%", f"%{q}%"])

    sql = f"SELECT * FROM items WHERE {' AND '.join(where)} ORDER BY code ASC;"
    cur.execute(sql, params)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return jsonify(rows)

@app.post("/api/items")
def api_items_create():
    data = request.get_json(force=True) or {}
    item_type = (data.get("type") or "").strip().lower()
    name = (data.get("name") or "").strip()
    code = data.get("code")

    if item_type not in ("pizza", "borda", "outros"):
        return jsonify({"error": "type inválido"}), 400
    if not name:
        return jsonify({"error": "name obrigatório"}), 400
    if code is None:
        return jsonify({"error": "code obrigatório"}), 400

    try:
        code = int(code)
    except Exception:
        return jsonify({"error": "code deve ser número"}), 400

    price_broto = data.get("price_broto")
    price_grande = data.get("price_grande")
    price = data.get("price")

    if item_type == "pizza":
        if price_broto is None or price_grande is None:
            return jsonify({"error": "pizza precisa de price_broto e price_grande"}), 400
        try:
            price_broto = float(price_broto)
            price_grande = float(price_grande)
        except Exception:
            return jsonify({"error": "preços inválidos"}), 400
        price = None
    else:
        if price is None:
            return jsonify({"error": "borda/outros precisa de price"}), 400
        try:
            price = float(price)
        except Exception:
            return jsonify({"error": "preço inválido"}), 400
        price_broto = None
        price_grande = None

    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO items(type, code, name, price_broto, price_grande, price, active)
            VALUES(?, ?, ?, ?, ?, ?, 1)
        """, (item_type, code, name, price_broto, price_grande, price))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"error": "Já existe um item com esse código (code)."}), 409

    new_id = cur.lastrowid
    conn.close()
    return jsonify({"ok": True, "id": new_id})

@app.delete("/api/items/<int:item_id>")
def api_items_delete(item_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE items SET active = 0 WHERE id = ?;", (item_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})

@app.get("/api/items/by-code/<int:code>")
def api_items_by_code(code: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM items WHERE code = ? AND active = 1;", (code,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "não encontrado"}), 404
    return jsonify(dict(row))

# =========================
# API: CAIXA
# =========================
@app.get("/api/cash/current")
def api_cash_current():
    conn = get_conn()
    sess = get_current_session(conn)
    if not sess:
        conn.close()
        return jsonify({"open": False})
    totals = compute_session_totals(conn, sess["id"])
    conn.close()
    return jsonify({
        "open": True,
        "session": dict(sess),
        "totals": totals
    })

@app.post("/api/cash/open")
def api_cash_open():
    data = request.get_json(force=True) or {}
    opening_amount = data.get("opening_amount", 0)
    notes = (data.get("notes") or "").strip() or None

    try:
        opening_amount = float(opening_amount)
    except Exception:
        return jsonify({"error": "opening_amount inválido"}), 400

    conn = get_conn()
    cur = conn.cursor()

    # não deixa abrir se já existe caixa aberto
    cur.execute("SELECT id FROM cash_sessions WHERE status='OPEN' LIMIT 1;")
    if cur.fetchone():
        conn.close()
        return jsonify({"error": "Já existe um caixa aberto."}), 409

    cur.execute("""
        INSERT INTO cash_sessions(opening_amount, status, notes)
        VALUES(?, 'OPEN', ?);
    """, (opening_amount, notes))
    conn.commit()
    session_id = cur.lastrowid
    totals = compute_session_totals(conn, session_id)
    conn.close()
    return jsonify({"ok": True, "session_id": session_id, "totals": totals})

@app.post("/api/cash/move")
def api_cash_move():
    data = request.get_json(force=True) or {}
    move_type = (data.get("move_type") or "").strip().upper()
    amount = data.get("amount")
    reason = (data.get("reason") or "").strip() or None

    if move_type not in ("SUPRIMENTO", "SANGRIA"):
        return jsonify({"error": "move_type inválido"}), 400
    try:
        amount = float(amount)
    except Exception:
        return jsonify({"error": "amount inválido"}), 400
    if amount <= 0:
        return jsonify({"error": "amount deve ser > 0"}), 400

    conn = get_conn()
    sess = get_current_session(conn)
    if not sess:
        conn.close()
        return jsonify({"error": "Não há caixa aberto."}), 409

    cur = conn.cursor()
    cur.execute("""
        INSERT INTO cash_moves(session_id, move_type, amount, reason)
        VALUES(?, ?, ?, ?);
    """, (sess["id"], move_type, amount, reason))
    conn.commit()

    totals = compute_session_totals(conn, sess["id"])
    conn.close()
    return jsonify({"ok": True, "totals": totals})

@app.post("/api/cash/close")
def api_cash_close():
    data = request.get_json(force=True) or {}
    closing_amount_reported = data.get("closing_amount_reported")
    notes = (data.get("notes") or "").strip() or None

    try:
        closing_amount_reported = float(closing_amount_reported)
    except Exception:
        return jsonify({"error": "closing_amount_reported inválido"}), 400

    conn = get_conn()
    sess = get_current_session(conn)
    if not sess:
        conn.close()
        return jsonify({"error": "Não há caixa aberto."}), 409

    totals = compute_session_totals(conn, sess["id"])
    expected = totals["expected"]
    diff = round(closing_amount_reported - expected, 2)

    cur = conn.cursor()
    cur.execute("""
        UPDATE cash_sessions
        SET status='CLOSED',
            closed_at=datetime('now','localtime'),
            closing_amount_reported=?,
            closing_amount_expected=?,
            diff=?,
            notes=COALESCE(notes,'') || CASE WHEN ? IS NULL OR ?='' THEN '' ELSE (' | FECHAMENTO: ' || ?) END
        WHERE id=?;
    """, (closing_amount_reported, expected, diff, notes, notes, notes, sess["id"]))
    conn.commit()
    conn.close()

    return jsonify({"ok": True, "expected": expected, "reported": closing_amount_reported, "diff": diff})

@app.get("/api/cash/sessions")
def api_cash_sessions():
    limit = request.args.get("limit", "30")
    try:
        limit = max(1, min(200, int(limit)))
    except Exception:
        limit = 30

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT * FROM cash_sessions
        ORDER BY id DESC
        LIMIT ?;
    """, (limit,))
    sessions = [dict(r) for r in cur.fetchall()]
    conn.close()
    return jsonify(sessions)

# =========================
# API: PEDIDOS
# =========================
@app.post("/api/orders")
def api_orders_create():
    data = request.get_json(force=True) or {}

    order_type = (data.get("order_type") or "BALCAO").strip().upper()
    if order_type not in ("BALCAO", "MESA", "DELIVERY"):
        return jsonify({"error": "order_type inválido"}), 400

    customer = (data.get("customer") or "").strip() or None
    payment = (data.get("payment") or "").strip() or None
    notes = (data.get("notes") or "").strip() or None

    items = data.get("items") or []
    if not isinstance(items, list) or len(items) == 0:
        return jsonify({"error": "pedido vazio"}), 400

    # valida total
    try:
        total = 0.0
        for it in items:
            qty = int(it.get("qty", 1))
            unit = float(it.get("unit_price", 0))
            total += qty * unit
        total = round(total, 2)
    except Exception:
        return jsonify({"error": "itens inválidos"}), 400

    conn = get_conn()

    # exige caixa aberto
    sess = get_current_session(conn)
    if not sess:
        conn.close()
        return jsonify({"error": "Abra o caixa antes de vender."}), 409

    cur = conn.cursor()
    cur.execute("""
        INSERT INTO orders(order_type, customer, payment, notes, total, session_id)
        VALUES(?, ?, ?, ?, ?, ?)
    """, (order_type, customer, payment, notes, total, sess["id"]))
    order_id = cur.lastrowid

    for it in items:
        kind = (it.get("kind") or "item").strip()
        desc = (it.get("description") or "").strip()
        qty = int(it.get("qty", 1))
        unit = float(it.get("unit_price", 0))
        line_total = round(qty * unit, 2)
        meta = it.get("meta") or {}
        meta_json = json.dumps(meta, ensure_ascii=False)

        cur.execute("""
            INSERT INTO order_items(order_id, kind, description, qty, unit_price, total, meta_json)
            VALUES(?, ?, ?, ?, ?, ?, ?)
        """, (order_id, kind, desc, qty, unit, line_total, meta_json))

    conn.commit()
    conn.close()
    return jsonify({"ok": True, "order_id": order_id, "total": total})

@app.get("/api/orders")
def api_orders_list():
    limit = request.args.get("limit", "20")
    try:
        limit = max(1, min(200, int(limit)))
    except Exception:
        limit = 20

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, order_type, customer, payment, total, created_at, session_id
        FROM orders
        ORDER BY id DESC
        LIMIT ?;
    """, (limit,))
    orders = [dict(r) for r in cur.fetchall()]

    for o in orders:
        cur.execute("""
            SELECT kind, description, qty, unit_price, total
            FROM order_items
            WHERE order_id = ?
            ORDER BY id ASC;
        """, (o["id"],))
        o["items"] = [dict(r) for r in cur.fetchall()]

    conn.close()
    return jsonify(orders)

# =========================
# API: HISTÓRICO DE VENDAS (RESUMO)
# =========================
@app.get("/api/sales/summary")
def api_sales_summary():
    # se passar session_id, resume o caixa
    session_id = request.args.get("session_id")
    conn = get_conn()
    cur = conn.cursor()

    if session_id:
        try:
            session_id = int(session_id)
        except Exception:
            conn.close()
            return jsonify({"error": "session_id inválido"}), 400
        totals = compute_session_totals(conn, session_id)
        conn.close()
        return jsonify(totals)

    # fallback: total geral (últimos X)
    cur.execute("SELECT COALESCE(SUM(total),0) AS s, COUNT(*) AS c FROM orders;")
    r = cur.fetchone()
    conn.close()
    return jsonify({"sales": float(r["s"]), "count": int(r["c"])})

# =========================
# EXEC
# =========================
def open_browser():
    webbrowser.open_new("http://127.0.0.1:5000/")

if __name__ == "__main__":
    init_db()
    seed_if_empty()

    if getattr(sys, "frozen", False):
        Timer(1.2, open_browser).start()

    app.run(host="127.0.0.1", port=5000, debug=False)
import os
import sys
import json
import sqlite3
import webbrowser
from threading import Timer
from datetime import datetime
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

def init_db():
    conn = get_conn()
    cur = conn.cursor()

    # Itens do cardápio
    cur.execute("""
    CREATE TABLE IF NOT EXISTS items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT NOT NULL CHECK(type IN ('pizza','borda','outros')),
        code INTEGER NOT NULL UNIQUE,                 -- número do item (para digitar e dar ENTER)
        name TEXT NOT NULL,
        price_broto REAL,                             -- só pizza
        price_grande REAL,                            -- só pizza
        price REAL,                                   -- borda / outros
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
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

    # Itens do pedido
    cur.execute("""
    CREATE TABLE IF NOT EXISTS order_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL,
        kind TEXT NOT NULL,                           -- 'item' ou 'meio_a_meio'
        description TEXT NOT NULL,
        qty INTEGER NOT NULL DEFAULT 1,
        unit_price REAL NOT NULL,
        total REAL NOT NULL,
        meta_json TEXT,                               -- detalhes (ex: ids sabores, tamanho, borda)
        FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE CASCADE
    );
    """)

    conn.commit()
    conn.close()

def seed_if_empty():
    """Cria alguns itens iniciais se o banco estiver vazio."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM items;")
    c = cur.fetchone()["c"]
    if c == 0:
        # pizzas
        cur.executemany("""
            INSERT INTO items(type, code, name, price_broto, price_grande)
            VALUES('pizza', ?, ?, ?, ?)
        """, [
            (1, "Mussarela", 25.0, 45.0),
            (2, "Calabresa", 27.0, 47.0),
            (3, "Frango c/ Catupiry", 30.0, 52.0),
        ])
        # bordas
        cur.executemany("""
            INSERT INTO items(type, code, name, price)
            VALUES('borda', ?, ?, ?)
        """, [
            (101, "Borda Catupiry", 8.0),
            (102, "Borda Cheddar", 8.0),
        ])
        # bebidas/outros
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
        # busca por nome ou por code
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

    # valida por tipo
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

    try:
        # confere total no backend pra evitar total errado no front
        total = 0.0
        for it in items:
            qty = int(it.get("qty", 1))
            unit = float(it.get("unit_price", 0))
            total += qty * unit
        total = round(total, 2)
    except Exception:
        return jsonify({"error": "itens inválidos"}), 400

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO orders(order_type, customer, payment, notes, total)
        VALUES(?, ?, ?, ?, ?)
    """, (order_type, customer, payment, notes, total))
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
        SELECT id, order_type, customer, payment, total, created_at
        FROM orders
        ORDER BY id DESC
        LIMIT ?;
    """, (limit,))
    orders = [dict(r) for r in cur.fetchall()]

    # pega itens de cada pedido (simples e suficiente pro histórico)
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
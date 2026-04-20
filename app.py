from flask import Flask, render_template, request, jsonify
import sqlite3

app = Flask(__name__)

def conectar():
    return sqlite3.connect("pizzas.db")

def criar():
    conn = conectar()
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS pizzas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        numero INTEGER,
        nome TEXT,
        tamanho TEXT,
        preco REAL
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS bordas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT,
        preco REAL
    )
    """)

    conn.commit()
    conn.close()

criar()

@app.route('/')
def index():
    conn = conectar()
    c = conn.cursor()

    c.execute("SELECT * FROM pizzas ORDER BY numero")
    pizzas = c.fetchall()

    c.execute("SELECT * FROM bordas")
    bordas = c.fetchall()

    conn.close()

    return render_template("index.html", pizzas=pizzas, bordas=bordas)


@app.route('/salvar_pizza', methods=['POST'])
def salvar_pizza():
    conn = conectar()
    c = conn.cursor()

    c.execute("INSERT INTO pizzas (numero, nome, tamanho, preco) VALUES (?, ?, ?, ?)",
              (request.form['numero'], request.form['nome'], request.form['tamanho'], request.form['preco']))

    conn.commit()
    conn.close()
    return "ok"


@app.route('/salvar_borda', methods=['POST'])
def salvar_borda():
    conn = conectar()
    c = conn.cursor()

    c.execute("INSERT INTO bordas (nome, preco) VALUES (?, ?)",
              (request.form['nome'], request.form['preco']))

    conn.commit()
    conn.close()
    return "ok"


@app.route('/finalizar', methods=['POST'])
def finalizar():
    print("\nPEDIDO:", request.json)
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(debug=True)
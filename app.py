import os
import sys
import sqlite3
import webbrowser
from threading import Timer
from flask import Flask, render_template, request, jsonify

# --- CONFIGURAÇÃO PARA O EXECUTÁVEL ---
def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

app = Flask(__name__, 
            template_folder=resource_path('templates'),
            static_folder=resource_path('static'))

# Banco de dados na mesma pasta do .exe
DB_PATH = os.path.join(os.path.dirname(sys.executable if getattr(sys, 'frozen', False) else __file__), 'pizzas.db')

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS pedidos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cliente TEXT,
            pedido TEXT,
            total REAL,
            pagamento TEXT,
            data TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

# Função para abrir o navegador padrão
def open_browser():
    webbrowser.open_new("http://127.0.0.1:5000/")

@app.route('/')
def index():
    return render_template('index.html')

# --- EXECUÇÃO ---
if __name__ == '__main__':
    init_db()
    
    # Abre o navegador automaticamente se for o .exe
    if getattr(sys, 'frozen', False):
        Timer(1.5, open_browser).start()
    
    app.run(host='127.0.0.1', port=5000, debug=False)
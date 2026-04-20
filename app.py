import webview
import os
import sys

# Esta função é vital para que o executável encontre o arquivo index.html
def resource_path(relative_path):
    try:
        # Quando vira EXE, o PyInstaller cria uma pasta temporária (_MEIPASS)
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

# Aqui o código busca especificamente o nome "index.html"
html_path = resource_path("index.html")

window = webview.create_window(
    'BARRA FUNDA PIZZA E VINHO - PDV',
    html_path,
    width=1300,
    height=850,
    resizable=True
)

if __name__ == '__main__':
    webview.start()
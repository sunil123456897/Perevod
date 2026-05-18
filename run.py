import sys
import os

# Это КЛЮЧЕВОЙ момент. Мы добавляем папку 'src' в пути поиска Python.
# Это гарантирует, что импорты вида 'from Perevod...' будут работать всегда.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "src")))

# Теперь, когда путь настроен, мы можем безопасно импортировать и запустить main.
from Perevod.main import main

if __name__ == "__main__":
    raise SystemExit(main())

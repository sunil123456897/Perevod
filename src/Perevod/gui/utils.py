# src/Perevod/gui/utils.py
import os
import subprocess
import platform
from tkinter import messagebox

def open_file_in_editor(path: str):
    """Открывает файл в системном редакторе по умолчанию, используя кросс-платформенный подход."""
    try:
        if platform.system() == "Windows":
            os.startfile(path)
        elif platform.system() == "Darwin":  # macOS
            subprocess.run(["open", path], check=True)
        else:  # Linux
            subprocess.run(["xdg-open", path], check=True)
    except (OSError, subprocess.CalledProcessError, AttributeError) as e:
        messagebox.showerror("Ошибка", f"Не удалось открыть файл '{path}': {e}")

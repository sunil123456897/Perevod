

import os

program_files_dir = "C:/Users/User/Desktop/kod/Perevod/program_files_for_neronka"
project_root = "C:/Users/User/Desktop/kod/Perevod"
file_path = os.path.join(project_root, "combined_program_files.txt")

updated_files_content = []

for txt_filename in os.listdir(program_files_dir):
    if txt_filename.endswith(".txt"):
        txt_full_path = os.path.join(program_files_dir, txt_filename)
        try:
            with open(txt_full_path, 'r', encoding='utf-8') as f_txt:
                content = f_txt.read()
            updated_files_content.append(f"--- {txt_filename} ---\n{content}")
        except Exception as e:
            print(f"Ошибка при чтении {txt_filename}: {e}")

try:
    with open(file_path, 'w', encoding='utf-8') as f_combined:
        f_combined.write("\n\n".join(updated_files_content))
    print(f"Файл {file_path} успешно создан.")
except Exception as e:
    print(f"Ошибка при создании файла {file_path}: {e}")

if os.path.exists(file_path):
    print(f"Повторная проверка: Файл {file_path} существует.")
else:
    print(f"Повторная проверка: Файл {file_path} ВСЕ ЕЩЕ НЕ существует.")


import os
import hashlib

def get_file_hash(filepath):
    if not os.path.exists(filepath):
        return None
    hasher = hashlib.md5()
    with open(filepath, 'rb') as f:
        buf = f.read()
        hasher.update(buf)
    return hasher.hexdigest()

program_files_dir = r'C:\Users\User\Desktop\kod\Perevod\program_files_for_neronka'
project_root = r'C:\Users\User\Desktop\kod\Perevod'

updated_files = []

# List of specific files to update
files_to_update = [
    'changelog.txt',
    'translation.log',
    'requirements.txt'
]

for root, _, files in os.walk(project_root):
    for file in files:
        # Check for .py files or specific files from the list
        if file.endswith('.py') or file in files_to_update:
            full_path = os.path.join(root, file)
            relative_path = os.path.relpath(full_path, project_root)
            
            # Construct the corresponding .txt path
            txt_file_name = relative_path.replace(os.sep, '_') + '.txt'
            txt_full_path = os.path.join(program_files_dir, txt_file_name)

            # Compare hashes
            original_hash = get_file_hash(full_path)
            txt_file_exists = os.path.exists(txt_full_path)
            txt_hash = get_file_hash(txt_full_path) if txt_file_exists else None

            if original_hash != txt_hash:
                with open(full_path, 'r', encoding='utf-8') as f_orig:
                    content = f_orig.read()
                with open(txt_full_path, 'w', encoding='utf-8') as f_txt:
                    f_txt.write(content)
                updated_files.append(relative_path)

if updated_files:
    print('Updated the following files:')
    for f in updated_files:
        print(f'- {f}')
else:
    print('No files needed updating.')


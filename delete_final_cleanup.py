import os
file_to_delete = 'C:/Users/User/Desktop/kod/Perevod/delete_delete_plan.py'
if os.path.exists(file_to_delete):
    os.remove(file_to_delete)
    print(f'Successfully deleted {file_to_delete}')
else:
    print(f'File not found: {file_to_delete}')
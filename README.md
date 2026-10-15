

# Getting started with our project.
## Requirements to be installed
- Python v >3.*
- pip
### 1. Cloning the repository
git clone <YOURGITLABREPOSITORYURL> cd blood_bank_donor
### 2. Create a virtual environment
python3 -m venv .venv
# Activate it the environment.
## ON Linux/macOS:
source .venv/bin/activate
## ON Windows PowerShell:
.venv\Scripts\Activate.ps1
### 3. Install dependencies
python3 -m pip install -r requirements.txt
### 4. Apply migrations
python3 manage.py migrate
### 5. Start the development server
python3 manage.py runserver
## Open http://127.0.0.1:8000/ from the browser
# Create a supper user
python3 manage.py createsuperuser
i am testing pushing and merge
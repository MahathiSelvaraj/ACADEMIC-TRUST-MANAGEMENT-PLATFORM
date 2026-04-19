# Academic Trust Management Platform

Beginner-friendly Flask project to manage academic record uploads and admin verification.

## Tech Stack
- Frontend: HTML, CSS, JavaScript
- Backend: Python + Flask
- Database: SQLite

## Features
- User registration/login with password hashing
- Separate admin login role
- User dashboard
- Academic record upload with details + file
- File path and metadata stored in SQLite
- Admin panel to verify/reject records
- Verification status shown to users
- Session-based authentication
- Basic responsive UI

## Project Structure
```text
MINI PROJECT/
|-- app.py
|-- requirements.txt
|-- README.md
|-- .gitignore
|-- instance/
|-- app/
|   |-- models.py
|   |-- uploads/
|   |   |-- .gitkeep
|   |-- static/
|   |   |-- css/
|   |   |   |-- style.css
|   |   |-- js/
|   |       |-- main.js
|   |-- templates/
|       |-- base.html
|       |-- index.html
|       |-- register.html
|       |-- login.html
|       |-- dashboard.html
|       |-- upload_record.html
|       |-- admin_panel.html
```

## Setup and Run
1. Create and activate virtual environment:
   - Windows (PowerShell):
     ```powershell
     python -m venv .venv
     .\.venv\Scripts\Activate.ps1
     ```
2. Install dependencies:
   ```powershell
   pip install -r requirements.txt
   ```
3. Run app:
   ```powershell
   python app.py
   ```
4. Open browser:
   - `http://127.0.0.1:5000`

## Deployment Notes
- Use environment variables in production:
  - `SECRET_KEY` = any long random secret string
  - `DATABASE_URL` = hosted database connection string
- The app now supports:
  - local SQLite by default
  - production database URLs such as PostgreSQL
- `Procfile` uses Gunicorn for deployment:
  - `web: gunicorn app:app`
- Recommended platforms:
  - Render
  - Railway
  - PythonAnywhere

## Example Deploy Environment Variables
```text
SECRET_KEY=replace-with-a-random-secret
DATABASE_URL=postgresql://username:password@host:5432/dbname
```

## Default Admin
- Email: `admin@trust.com`
- Password: `admin123`

Created automatically on first run.

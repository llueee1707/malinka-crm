# Deploy Malinka CRM on a Windows laptop

This guide is for a clean client laptop running Windows 10/11. The server runs locally on the laptop and can be opened from the local Wi-Fi network.

## Requirements

- Windows 10/11
- Python 3.11 or 3.12 with "Add Python to PATH" enabled
- Git
- Internet access during installation

## 1. Clone the repository

Open PowerShell in the folder where the project should live, for example `C:\`:

```powershell
git clone https://github.com/<OWNER>/<REPO>.git Salon
cd Salon
```

## 2. Create `.env`

```powershell
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Open `.env` in Notepad and set:

```dotenv
SECRET_KEY=<generated-secret-key>
SEED_DEMO_DATA=false
BOOTSTRAP_DIRECTOR_LOGIN=Admin
BOOTSTRAP_DIRECTOR_PASSWORD=<set-a-real-client-password>
BOOTSTRAP_DIRECTOR_FULL_NAME=<client-admin-name>
```

The bootstrap director is created only when the database is empty. Set the final login and password before the first server start.

## 3. Install dependencies

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 4. First start

```powershell
python -m uvicorn app.main:app --host 0.0.0.0 --port 8099
```

Open:

```text
http://127.0.0.1:8099/login
```

On the first start, the app creates a fresh `salon.db` with one director user from `.env`. No services, masters, clients, receipts, or appointments are created when `SEED_DEMO_DATA=false`.

## 5. Local network access

Find the laptop IP:

```powershell
ipconfig
```

Open the firewall port from an administrator PowerShell:

```powershell
netsh advfirewall firewall add rule name="Salon CRM 8099" dir=in action=allow protocol=TCP localport=8099
```

From a phone or tablet on the same Wi-Fi, open:

```text
http://<LAPTOP-IP>:8099
```

## 6. Autostart

Use Windows Task Scheduler:

- Trigger: at user logon
- Action: run `C:\Salon\restart_server.bat`
- Options: run with highest privileges

## Useful commands

Restart:

```powershell
.\restart_server.bat
```

Reset the database:

```powershell
Remove-Item salon.db -Force
```

The next server start creates a new clean database with the bootstrap director from `.env`.

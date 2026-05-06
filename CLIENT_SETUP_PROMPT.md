# Prompt for Codex on the client's laptop

Use this prompt after cloning the repository on the client's Windows laptop:

```text
Set up this FastAPI salon CRM on this Windows laptop for local production use.

Requirements:
- Create `.env` from `.env.example`.
- Generate a strong `SECRET_KEY`.
- Set `SEED_DEMO_DATA=false`.
- Set the first director account in `.env` using credentials I provide locally.
- Create a virtual environment and install `requirements.txt`.
- Start the app on `0.0.0.0:8099`.
- Make sure a fresh `salon.db` is created automatically.
- Do not import demo data.
- Help me open Windows Firewall for TCP port 8099.
- Show me the local URL and the LAN URL for phones on the same Wi-Fi.
- If useful, configure `restart_server.bat` for Task Scheduler autostart.

Before making changes, inspect the repo and explain what files will be created locally.
```

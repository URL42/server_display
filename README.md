# bossbitch-home

Home lab dashboard and automation server.

## Structure

```
bossbitch-home/
├── server/          # FastAPI server — runs in Docker on UM890
│   ├── main.py
│   ├── config.py
│   ├── Dockerfile
│   ├── requirements.txt
│   └── chores/      # Chore chart API (Todoist + XP state)
├── waveshare/       # MicroPython — deploy to Waveshare ESP32-S3-Touch-LCD-4.3
│   ├── main.py
│   ├── chore_screen.py
│   ├── n8n_screen.py
│   ├── display_driver.py
│   ├── ch422g.py
│   ├── gt911.py
│   ├── sdcard_driver.py
│   └── secrets.py.example
├── data/            # Docker volume (gitignored db files)
├── docker-compose.yml
├── .env.example
└── .gitignore
```

## Setup

### 1. Server (UM890)

```bash
cp .env.example .env
# edit .env with your Todoist token and project ID
docker compose up -d
```

Find your Todoist project ID:
```bash
curl -s https://api.todoist.com/rest/v2/projects \
  -H "Authorization: Bearer YOUR_TOKEN" | python3 -m json.tool | grep -A2 "Chores"
```

### 2. Waveshare display

```bash
cp waveshare/secrets.py.example waveshare/secrets.py
# edit secrets.py with wifi, n8n, bossbitch details
```

Deploy via mpremote or Thonny — copy all files in `waveshare/` to the device root.

```bash
mpremote cp waveshare/secrets.py :secrets.py
mpremote cp waveshare/main.py :main.py
mpremote cp waveshare/chore_screen.py :chore_screen.py
# ... etc
```

### 3. Display tiles (left → right)

| Tile | Screen |
|------|--------|
| 1 | Server stats (Docker / Ollama / Frigate) |
| 2 | Chore chart |
| 3 | BTC price chart |
| 4 | n8n workflow runner |
| 5 | Home Assistant (placeholder) |

## Chore XP system

- **Daily %** — tasks completed today / tasks due today
- **Weekly %** — tasks completed this week / tasks due this week
- Resets daily at midnight, weekly on Monday
- **Yun** earns levels (1–5) based on 4-week rolling weekly average
- Adults show bars only (no levels — it's for the kid)

## Tailscale

bossbitch is reachable on Tailscale as `bossbitch` — no IP needed.
The Waveshare display uses `BOSSBITCH_HOST = "bossbitch"` in secrets.py.

# server-display

Home lab dashboard and automation server for the Waveshare ESP32-S3-Touch-LCD-4.3.

## Repo structure

Flat layout — all server and waveshare files live in the root. The Dockerfile assembles the `chores/` package at build time.

```
server-display/
├── app.py                  # FastAPI server entrypoint
├── config.py               # pydantic-settings (reads .env)
├── router.py               # GET /chores, POST /chores/complete
├── models.py               # Pydantic request/response models
├── state.py                # SQLite XP/streak state
├── todoist.py              # Todoist API v1 client
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
├── .dockerignore
├── .gitignore
│
├── main.py                 # Waveshare MicroPython dashboard
├── chore_screen.py         # Chore chart tile (tile 2)
├── n8n_screen.py           # n8n workflow runner tile (tile 4)
├── display_driver.py       # RGB bus + framebuffer init
├── ch422g.py               # IO expander driver
├── gt911.py                # Touch driver
├── sdcard_driver.py        # SD card driver
├── secrets.py.example      # Waveshare credentials template
│
└── data/                   # Docker volume — SQLite DB (gitignored)
```

---

## Server setup (UM890 / server)

### 1. Clone and configure

```bash
git clone https://github.com/yourusername/server-display.git ~/apps/server_display
cd ~/apps/server_display
cp .env.example .env
nano .env
```

Fill in `TODOIST_API_TOKEN` and `TODOIST_PROJECT_ID`. Find your project ID:

```bash
curl -s https://api.todoist.com/api/v1/projects \
  -H "Authorization: Bearer YOUR_TOKEN" \
  | python3 -m json.tool | grep -E '"id"|"name"'
```

### 2. Open firewall port

```bash
sudo ufw allow 8099/tcp
sudo ufw reload
```

### 3. Build and start

```bash
docker compose up -d --build
```

### 4. Verify

```bash
curl http://localhost:8099/health
# {"ok": true}

curl http://localhost:8099/chores
# full JSON payload
```

### Updating

```bash
git pull
docker compose up -d --build
```

### Resetting XP state

If you need to wipe the XP/streak database:

```bash
rm data/server.db
docker compose restart
```

---

## Waveshare display setup

### 1. Configure secrets

```bash
cp secrets.py.example secrets.py
nano secrets.py
```

Set `WIFI_SSID`, `WIFI_PASSWORD`, `N8N_BASE_URL`, `N8N_API_KEY`, `server_HOST` (LAN IP of server), `server_PORT` (8099).

> Note: Tailscale hostnames don't resolve on the ESP32. Use the LAN IP directly.

### 2. Deploy to device

```bash
mpremote cp secrets.py :secrets.py
mpremote cp main.py :main.py
mpremote cp chore_screen.py :chore_screen.py
mpremote cp display_driver.py :display_driver.py
mpremote cp n8n_screen.py :n8n_screen.py
mpremote cp ch422g.py :ch422g.py
mpremote cp gt911.py :gt911.py
mpremote cp sdcard_driver.py :sdcard_driver.py
mpremote reboot
```

> `secrets.py` is gitignored — never deploy from the repo directly, always copy manually.

### 3. Display tiles (swipe left/right)

| Tile | Screen |
|------|--------|
| 1 | Server stats — Docker / Ollama / Frigate |
| 2 | Chore chart |
| 3 | BTC price chart (7-day) |
| 4 | n8n workflow runner |
| 5 | Home Assistant (placeholder) |

---

## Chore chart

### Data flow

```
Todoist API → server /chores → Waveshare display (every 30s)
Waveshare tap → server /chores/complete → Todoist API close task
```

### Display behaviour

- Default view shows **incomplete + overdue tasks only** (clean glance)
- Overdue tasks shown in **red**
- Tap a chore row to **mark complete** — optimistic UI, fires to server instantly
- Tap a person's **header** to toggle full view (shows completed tasks too)
- Screen only redraws if data actually changed (hash comparison)

### XP system

**Daily %**
- `tasks completed today ÷ tasks due today × 100`
- Resets at midnight Pacific
- Totals are set at reset time from live Todoist data

**Weekly %**
- `tasks completed this week ÷ tasks due Mon–Sun × 100`
- Resets every Monday midnight Pacific
- Accumulates across the week

**Yun's level (1–5)**
- Only updates at the weekly reset — never mid-week
- Based on 4-week rolling average of weekly completion %
- Moves **one step at a time** — gradual build up or down

| 4-week avg | Level |
|---|---|
| 90%+ | 5 ⭐⭐⭐⭐⭐ |
| 75–89% | 4 |
| 60–74% | 3 |
| 40–59% | 2 |
| Under 40% | 1 |

Adults show daily/weekly bars only — no levels (it's for the kid).

### Chore rotation

**Daily tasks** rotate on a fixed day-of-week schedule per person.

**Monthly deep cleans** — 4 chores across 4 Saturdays each month, alternating who does each one monthly:

| Saturday | Chore | May | Jun | Jul |
|---|---|---|---|---|
| 1st | Clean Refrigerator | Mama | Baba | Mama |
| 2nd | Clean Stove | Baba | Mama | Baba |
| 3rd | Clean Car Inside | Mama | Baba | Mama |
| 4th | Clean Freezer | Baba | Mama | Baba |

---

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Container health check |
| GET | `/chores` | Full chore payload for display |
| POST | `/chores/complete` | Mark task complete `{"task_id": "...", "member": "Mama"}` |

---

## Notes

- Todoist API v1 (`/api/v1/`) — v2 is deprecated
- Due dates from Todoist are UTC timestamps, converted to Pacific before comparison
- SQLite DB persists in `./data/server.db` via Docker volume mount
- Port 8099 chosen to avoid conflicts with existing services on server

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
├── btop_screen.py          # btop-style system monitor (tile 3)
├── n8n_screen.py           # n8n workflow runner tile (tile 5)
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
mpremote cp btop_screen.py :btop_screen.py
mpremote cp display_driver.py :display_driver.py
mpremote cp n8n_screen.py :n8n_screen.py
mpremote cp ch422g.py :ch422g.py
mpremote cp gt911.py :gt911.py
mpremote cp sdcard_driver.py :sdcard_driver.py
mpremote reboot
```

> `secrets.py` is gitignored — never deploy from the repo directly, always copy manually.

### 3. Display tiles (swipe left/right, or nav buttons — they wrap around)

| Tile | Screen |
|------|--------|
| 1 | Server stats — Docker / Ollama / Frigate |
| 2 | Chore chart |
| 3 | btop-style system monitor (CPU history, per-core bars, mem, disks, procs, net) |
| 4 | BTC price chart (7-day) |
| 5 | n8n workflow runner |
| 6 | Home Assistant (placeholder) |

The btop screen is fed from the same `/stats?detail=true` request as server
card 1 — no extra HTTP round-trip.

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

### XP economy

XP is **effort-weighted**, not task-counted. Design rationale:

- **Weighted tasks** — equal-value tasks let a rational kid farm the 30-second
  chores and skip the 20-minute ones while keeping a high completion %
  (Goodhart's law). Weights align the metric with actual effort:

| Tier | XP | Examples |
|---|---|---|
| Quick | 5 | couch pillows, school sheet, bathroom trash, water bottle |
| Standard | 10 | dishes, trash, pickup, surfaces, toilet, make beds |
| Effort | 15 | laundry, vacuum, dog poop, bathtub |
| Big | 25 | clean room, changing sheets, monthly deep cleans |

- **Diminishing returns** — same task completed twice in one day earns 50%,
  third time earns 0. Closes the farming door entirely.
- **Daily/weekly bars** show `XP earned / XP available` (Pacific time resets,
  daily at midnight, weekly on Monday).

**Yun's progression (kid-only):**

- **Levels** are a cumulative XP bank: L2 at 500, L3 at 1200, L4 at 2200,
  L5 at 3500. Widening gaps — early levels hook, later ones retain.
  Display shows level + % progress to next (e.g. `LV.3 67%`).
- **Decay** — a week under 50% completion costs 10% of the XP gap to the
  next level. Gradual slide, never a full-level cliff.
- **Streaks** — a day at ≥70% of available XP extends the streak (lightning
  icon on display). Every 7 consecutive days banks a **freeze token**
  (max 2, shown as `*`). A missed day auto-spends a freeze instead of
  breaking the streak — loss aversion drives consistency, freezes prevent
  one soccer practice from destroying motivation.
- **Rewards** at level boundaries are a family decision (pick dinner, movie
  night, etc.) — the menu mattering to the kid is most of whether token
  economies work.

Adults show XP bars only — no levels or streaks.

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
- **Display drift**: the horizontal drift on the RGB panel is caused by PSRAM
  bandwidth starvation — the RGB DMA streams pixels from PSRAM and WiFi/socket
  traffic can stall those reads, slipping the panel scan position.
  `display_driver.py` attempts to enable **bounce buffers** (internal-SRAM DMA
  staging, Espressif's recommended fix). If the firmware build doesn't support
  it, `main.py` falls back to a periodic reboot every 15 minutes. Check the
  boot log for `RGBBus OK with bounce buffer` vs `no bounce buffer support` —
  if unsupported, upgrading the lvgl_micropython firmware build fixes it properly.

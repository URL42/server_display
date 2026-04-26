import time
import gc
import ujson
import network
import socket
import ssl
import machine

import secrets

# ================= CONFIG =================
WIFI_SSID     = secrets.WIFI_SSID
WIFI_PASSWORD = secrets.WIFI_PASSWORD

SERVERS = [
    {"name": "BOSS (Docker)", "ip": "192.168.1.238", "role": "docker"},
    {"name": "AI (Ollama)",   "ip": "192.168.1.147", "role": "ai"},
    {"name": "CCTV (Frigate)","ip": "192.168.1.233", "role": "cctv"},
]

STATS_PORT    = 8000
STATS_PATH    = "/stats"
_SMOOTH_ALPHA = 0.3

BTC_HOST = "api.binance.us"
BTC_PORT = 443

SOCK_TIMEOUT_S   = 2
SSL_TIMEOUT_S    = 3
MAX_HEADER_BYTES = 4096
MAX_BODY_BYTES   = 32768

SCREEN_W    = 800
SCREEN_H    = 480
TOTAL_TILES = 5

GT911_SCL        = 9
GT911_SDA        = 8
GT911_ADDR       = 0x5D
GT911_REG_STATUS = 0x814E
GT911_REG_POINT1 = 0x8150

SERVER_INTERVAL_MS = 5_000
BTC_INTERVAL_MS    = 60_000
CHORE_INTERVAL_MS  = 30_000


# ================= WIFI =================
def connect_wifi():
    print("=== WIFI INIT ===")
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    try:
        wlan.config(pm=0)
    except Exception:
        pass
    if wlan.isconnected():
        print("ALREADY CONNECTED:", wlan.ifconfig()[0])
        return wlan
    wlan.connect(WIFI_SSID, WIFI_PASSWORD)
    timeout = 0
    while not wlan.isconnected() and timeout < 40:
        time.sleep(0.5)
        timeout += 1
    if wlan.isconnected():
        print("CONNECTED:", wlan.ifconfig()[0])
    else:
        print("WiFi Failed!")
    return wlan


# ================= HTTP HELPERS =================
def _recv_until(s, marker=b"\r\n\r\n", max_bytes=MAX_HEADER_BYTES):
    data = b""
    while marker not in data:
        chunk = s.recv(256)
        if not chunk:
            break
        data += chunk
        if len(data) >= max_bytes:
            break
    return data

def _read_http_body(s, max_body=MAX_BODY_BYTES):
    data = _recv_until(s, b"\r\n\r\n", MAX_HEADER_BYTES)
    if b"\r\n\r\n" in data:
        header, body = data.split(b"\r\n\r\n", 1)
    else:
        header, body = b"", b""
    while len(body) < max_body:
        try:
            chunk = s.recv(1024)
        except OSError:
            break
        if not chunk:
            break
        body += chunk
    return header, body

def _http_get_json(host, port, path, use_ssl=False, server_hostname=None):
    gc.collect()
    try:
        addr = socket.getaddrinfo(host, port)[0][-1]
    except Exception as e:
        print("DNS fail:", host, repr(e))
        return None
    s = None
    try:
        s = socket.socket()
        s.settimeout(SOCK_TIMEOUT_S)
        s.connect(addr)
        if use_ssl:
            s = ssl.wrap_socket(s, server_hostname=server_hostname or host)
            try:
                s.settimeout(SSL_TIMEOUT_S)
            except Exception:
                pass
        req = (
            "GET {path} HTTP/1.0\r\nHost: {host}\r\nUser-Agent: mpy-lvgl\r\n"
            "Accept: application/json\r\nAccept-Encoding: identity\r\n"
            "Connection: close\r\n\r\n"
        ).format(path=path, host=host)
        s.write(req.encode())
        _, body = _read_http_body(s, MAX_BODY_BYTES)
        if not body:
            return None
        try:
            return ujson.loads(body)
        except Exception as e:
            print("JSON fail:", host, repr(e))
            return None
    except Exception as e:
        print("HTTP fail:", host, repr(e))
        return None
    finally:
        try:
            if s: s.close()
        except Exception:
            pass

def fetch_stats(ip):
    return _http_get_json(ip, STATS_PORT, STATS_PATH)

def fetch_btc():
    data = _http_get_json(BTC_HOST, BTC_PORT,
        "/api/v3/ticker/price?symbol=BTCUSDT",
        use_ssl=True, server_hostname=BTC_HOST)
    if not data or "price" not in data:
        return None, None
    try:
        price = float(data["price"])
    except Exception:
        return None, None
    hist = _http_get_json(BTC_HOST, BTC_PORT,
        "/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=7",
        use_ssl=True, server_hostname=BTC_HOST)
    if not hist or not isinstance(hist, list):
        return price, None
    try:
        return price, [float(d[4]) for d in hist]
    except Exception:
        return price, None

def fetch_chores():
    """Fetch chore data from bossbitch. Returns parsed JSON or None."""
    return _http_get_json(
        secrets.BOSSBITCH_HOST,
        secrets.BOSSBITCH_PORT,
        "/chores"
    )


# ================= BOOT =================
connect_wifi()

import lvgl as lv
try:
    lv.init()
except Exception:
    pass

from display_driver import display
import n8n_screen
import chore_screen


# ================= TOUCH =================
_last_touch_print = 0

def _init_touch():
    i2c = machine.I2C(0,
        scl=machine.Pin(GT911_SCL),
        sda=machine.Pin(GT911_SDA),
        freq=400_000
    )
    devs = i2c.scan()
    print("I2C scan:", [hex(d) for d in devs])
    addr = None
    for cand in (0x5D, 0x14):
        if cand in devs:
            addr = cand
            break
    if addr is None:
        print("GT911 not found - touch disabled")
        return None
    print("GT911 at:", hex(addr))
    indev = lv.indev_create()
    indev.set_type(lv.INDEV_TYPE.POINTER)

    def _read_cb(drv, data):
        global _last_touch_print
        try:
            st = i2c.readfrom_mem(addr, GT911_REG_STATUS, 1, addrsize=16)[0]
        except Exception:
            data.state = lv.INDEV_STATE.RELEASED
            return False
        if (st & 0x80) and (st & 0x0F) > 0:
            try:
                pt = i2c.readfrom_mem(addr, GT911_REG_POINT1, 4, addrsize=16)
                x = pt[0] | (pt[1] << 8)
                y = pt[2] | (pt[3] << 8)
            except Exception:
                data.state = lv.INDEV_STATE.RELEASED
                return False
            finally:
                try:
                    i2c.writeto_mem(addr, GT911_REG_STATUS, b"\x00", addrsize=16)
                except Exception:
                    pass
            data.point.x = int(x)
            data.point.y = int(y)
            data.state = lv.INDEV_STATE.PRESSED
            now = time.ticks_ms()
            if time.ticks_diff(now, _last_touch_print) > 200:
                _last_touch_print = now
                print("TOUCH:", x, y)
        else:
            if st & 0x80:
                try:
                    i2c.writeto_mem(addr, GT911_REG_STATUS, b"\x00", addrsize=16)
                except Exception:
                    pass
            data.state = lv.INDEV_STATE.RELEASED
        return False

    indev.set_read_cb(_read_cb)
    print("Touch indev registered")
    return indev

_touch_indev = _init_touch()


# ================= UI HELPERS =================
def label_color(lbl, hexcolor=0xFFFFFF):
    try:
        lbl.set_style_text_color(lv.color_hex(hexcolor), lv.PART.MAIN | lv.STATE.DEFAULT)
    except Exception:
        try:
            lbl.set_style_text_color(lv.color_hex(hexcolor), lv.PART.MAIN)
        except Exception:
            lbl.set_style_text_color(lv.color_hex(hexcolor), 0)
    return lbl

def smooth(old, new):
    if old is None:
        return float(new)
    return (1.0 - _SMOOTH_ALPHA) * float(old) + _SMOOTH_ALPHA * float(new)

def usage_color(val):
    if val < 70:   return lv.color_hex(0x44FF44)
    elif val < 85: return lv.color_hex(0xFFDD00)
    else:          return lv.color_hex(0xFF4444)


# ================= UI BUILDERS =================
def create_server_card(parent, title, col_index):
    card = lv.obj(parent)
    card.set_size(250, 400)
    card.align(lv.ALIGN.TOP_LEFT, 12 + (col_index * 260), 10)
    card.set_style_bg_color(lv.color_hex(0x1A1A1A), 0)
    card.set_style_border_color(lv.color_hex(0x333333), 0)
    card.set_style_border_width(2, 0)
    lbl_title = label_color(lv.label(card))
    lbl_title.set_text(title)
    lbl_title.align(lv.ALIGN.TOP_MID, 0, 5)
    arc_cpu = lv.arc(card)
    arc_cpu.set_size(120, 120)
    arc_cpu.set_range(0, 100)
    arc_cpu.align(lv.ALIGN.TOP_MID, 0, 28)
    arc_cpu.set_style_arc_width(12, lv.PART.MAIN)
    arc_cpu.set_style_arc_width(12, lv.PART.INDICATOR)
    lbl_cpu = label_color(lv.label(arc_cpu))
    lbl_cpu.set_text("--%")
    lbl_cpu.center()
    lbl_mem = label_color(lv.label(card))
    lbl_mem.set_text("MEM: --")
    lbl_mem.align(lv.ALIGN.TOP_LEFT, 8, 160)
    bar_mem = lv.bar(card)
    bar_mem.set_size(232, 10)
    bar_mem.align(lv.ALIGN.TOP_MID, 0, 178)
    bar_mem.set_range(0, 100)
    lbl_disk = label_color(lv.label(card))
    lbl_disk.set_width(234)
    lbl_disk.set_text("DISK: --")
    lbl_disk.align(lv.ALIGN.TOP_LEFT, 8, 196)
    lbl_d1 = label_color(lv.label(card))
    lbl_d1.set_width(234)
    lbl_d1.set_text("")
    lbl_d1.align(lv.ALIGN.TOP_LEFT, 8, 262)
    lbl_d2 = label_color(lv.label(card))
    lbl_d2.set_width(234)
    lbl_d2.set_text("")
    lbl_d2.align(lv.ALIGN.TOP_LEFT, 8, 298)
    lbl_d3 = label_color(lv.label(card))
    lbl_d3.set_width(234)
    lbl_d3.set_text("")
    lbl_d3.align(lv.ALIGN.TOP_LEFT, 8, 334)
    return {
        "arc_cpu": arc_cpu, "lbl_cpu": lbl_cpu,
        "bar_mem": bar_mem, "lbl_mem": lbl_mem,
        "lbl_disk": lbl_disk,
        "lbl_d1": lbl_d1, "lbl_d2": lbl_d2, "lbl_d3": lbl_d3,
        "sm_cpu": None, "sm_mem": None,
    }

def create_btc_card(parent):
    card = lv.obj(parent)
    card.set_size(700, 400)
    card.align(lv.ALIGN.TOP_MID, 0, 10)
    card.set_style_bg_color(lv.color_hex(0x1A1A1A), 0)
    card.set_style_border_width(0, 0)
    lbl_title = label_color(lv.label(card), 0xF7931A)
    lbl_title.set_text("BTC / USD")
    lbl_title.align(lv.ALIGN.TOP_LEFT, 16, 10)
    lbl_price = label_color(lv.label(card), 0xFFFFFF)
    lbl_price.set_text("--")
    lbl_price.align(lv.ALIGN.TOP_LEFT, 16, 36)
    lbl_range = label_color(lv.label(card), 0xAAAAAA)
    lbl_range.set_text("7d H: --  L: --")
    lbl_range.align(lv.ALIGN.TOP_RIGHT, -16, 10)
    lbl_updated = label_color(lv.label(card), 0x666666)
    lbl_updated.set_text("updating...")
    lbl_updated.align(lv.ALIGN.TOP_RIGHT, -16, 36)
    chart = lv.chart(card)
    chart.set_size(660, 270)
    chart.align(lv.ALIGN.BOTTOM_MID, 0, -10)
    chart.set_type(lv.chart.TYPE.LINE)
    chart.set_update_mode(lv.chart.UPDATE_MODE.SHIFT)
    chart.set_point_count(7)
    chart.set_style_bg_color(lv.color_hex(0x111111), 0)
    chart.set_style_border_width(1, 0)
    chart.set_style_border_color(lv.color_hex(0x333333), 0)
    chart.set_div_line_count(3, 6)
    day_labels = []
    for i in range(7):
        lbl = label_color(lv.label(card), 0x888888)
        lbl.set_text("---")
        lbl.align(lv.ALIGN.BOTTOM_LEFT, 18 + i * 94, -8)
        day_labels.append(lbl)
    lbl_ymax = label_color(lv.label(card), 0x666666)
    lbl_ymax.set_text("--")
    lbl_ymax.align(lv.ALIGN.BOTTOM_LEFT, 4, -278)
    lbl_ymin = label_color(lv.label(card), 0x666666)
    lbl_ymin.set_text("--")
    lbl_ymin.align(lv.ALIGN.BOTTOM_LEFT, 4, -18)
    ser = chart.add_series(lv.color_hex(0xF7931A), lv.chart.AXIS.PRIMARY_Y)
    return {
        "lbl_price": lbl_price, "lbl_range": lbl_range,
        "lbl_updated": lbl_updated, "lbl_ymax": lbl_ymax,
        "lbl_ymin": lbl_ymin, "day_labels": day_labels,
        "chart": chart, "ser": ser,
    }

def create_placeholder(parent, title, fallback_symbol):
    card = lv.obj(parent)
    card.set_size(700, 400)
    card.align(lv.ALIGN.TOP_MID, 0, 10)
    card.set_style_bg_color(lv.color_hex(0x1A1A1A), 0)
    lbl = label_color(lv.label(card))
    lbl.set_text(fallback_symbol + " " + title)
    lbl.align(lv.ALIGN.TOP_LEFT, 20, 20)
    lbl2 = label_color(lv.label(card))
    lbl2.set_text("Awaiting layout integration...")
    lbl2.center()


# ================= UPDATE LOGIC =================
def update_card_ui(card, data, role):
    if not data:
        card["lbl_d1"].set_text("OFFLINE")
        card["lbl_d2"].set_text("")
        card["lbl_d3"].set_text("")
        return
    cpu_raw = float(data.get("cpu", 0.0))
    card["sm_cpu"] = smooth(card["sm_cpu"], cpu_raw)
    val_cpu = int(card["sm_cpu"])
    card["arc_cpu"].set_value(val_cpu)
    card["lbl_cpu"].set_text("{}%".format(val_cpu))
    card["arc_cpu"].set_style_arc_color(usage_color(val_cpu), lv.PART.INDICATOR)
    mem = data.get("mem", {})
    mem_pct  = float(mem.get("percent", 0.0))
    mem_used = mem.get("used_gb", None)
    mem_tot  = mem.get("total_gb", None)
    card["sm_mem"] = smooth(card["sm_mem"], mem_pct)
    val_mem = int(card["sm_mem"])
    card["bar_mem"].set_value(val_mem, 1)
    if mem_used is not None and mem_tot is not None:
        card["lbl_mem"].set_text("MEM {}% {:.0f}/{:.0f}GB".format(val_mem, mem_used, mem_tot))
    else:
        card["lbl_mem"].set_text("MEM: {}%".format(val_mem))
    disks = data.get("disks", [])
    if disks:
        d = disks[0]
        if len(disks) > 1:
            d2 = disks[1]
            card["lbl_disk"].set_text(
                "DISK {} {}% / {:.0f}GB\n     {} {}% / {:.0f}GB".format(
                d.get("mount","/"), d.get("used_pct",0), d.get("total_gb",0),
                d2.get("mount",""), d2.get("used_pct",0), d2.get("total_gb",0)))
        else:
            card["lbl_disk"].set_text("DISK {} {}% / {:.0f}GB".format(
                d.get("mount","/"), d.get("used_pct",0), d.get("total_gb",0)))
    else:
        card["lbl_disk"].set_text("DISK: --")
    if role == "docker":
        docker    = data.get("docker", {})
        running   = docker.get("running", "?")
        total     = docker.get("total", "?")
        unhealthy = docker.get("unhealthy", 0)
        names     = docker.get("unhealthy_names", [])
        card["lbl_d1"].set_text("Docker {}/{} running".format(running, total))
        if unhealthy:
            card["lbl_d2"].set_text("WARN: {} unhealthy".format(unhealthy))
            card["lbl_d3"].set_text(", ".join(names[:2]) if names else "")
        else:
            card["lbl_d2"].set_text("All containers healthy")
            card["lbl_d3"].set_text("")
    elif role == "ai":
        ollama  = data.get("ollama", {})
        card["lbl_d1"].set_text("Ollama: {}".format(ollama.get("status","?")))
        card["lbl_d2"].set_text("{} model(s) loaded".format(ollama.get("running_models",0)))
        models = ollama.get("current_models", [])
        card["lbl_d3"].set_text(", ".join(str(m) for m in models[:2]) if models else "")
    elif role == "cctv":
        frigate = data.get("frigate", {})
        cams    = frigate.get("cameras", {})
        coral   = frigate.get("coral", {})
        total   = cams.get("total", "?")
        down    = cams.get("down", 0)
        inf_ms  = coral.get("inference_ms", None)
        card["lbl_d1"].set_text("Frigate: {}".format(frigate.get("status","?")))
        cam_txt = "Cams: {}/{}".format(total - down if isinstance(total, int) else "?", total)
        if down:
            cam_txt += " ({} down)".format(down)
        card["lbl_d2"].set_text(cam_txt)
        card["lbl_d3"].set_text("Coral: {:.1f}ms".format(inf_ms) if inf_ms else "")
    else:
        card["lbl_d1"].set_text("host: {}".format(data.get("host","?")))
        card["lbl_d2"].set_text("")
        card["lbl_d3"].set_text("")

def update_btc_ui(ui, price, history):
    import time as _time
    if price is None or not history:
        return
    ui["lbl_price"].set_text("${:,.0f}".format(price))
    hi = max(history)
    lo = min(history)
    ui["lbl_range"].set_text("7d H:${:,.0f}  L:${:,.0f}".format(hi, lo))
    t = _time.localtime()
    ui["lbl_updated"].set_text("Updated {:02d}:{:02d}:{:02d}".format(t[3], t[4], t[5]))
    ui["lbl_ymax"].set_text("${:,.0f}".format(hi * 1.05))
    ui["lbl_ymin"].set_text("${:,.0f}".format(lo * 0.95))
    days  = ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"]
    t_now = _time.localtime()
    for i, lbl in enumerate(ui["day_labels"]):
        day_idx = (t_now[6] + 1 + (i - 6)) % 7
        lbl.set_text(days[day_idx])
    ui["chart"].set_axis_range(lv.chart.AXIS.PRIMARY_Y, int(lo * 0.95), int(hi * 1.05))
    ui["chart"].set_point_count(7)
    for v in history:
        ui["chart"].set_next_value(ui["ser"], int(v))
    ui["chart"].refresh()


# ================= BUILD UI =================
print("Building UI...")
scr = lv.screen_active()
scr.set_style_bg_color(lv.color_hex(0x000000), 0)

tv = lv.tileview(scr)
tv.set_size(800, 420)
tv.align(lv.ALIGN.TOP_MID, 0, 0)
tv.remove_flag(lv.obj.FLAG.GESTURE_BUBBLE)

# Tile order: server(0), chores(1), btc(2), n8n(3), ha(4)
t1 = tv.add_tile(0, 0, lv.DIR.RIGHT)
t2 = tv.add_tile(1, 0, lv.DIR.LEFT | lv.DIR.RIGHT)
t3 = tv.add_tile(2, 0, lv.DIR.LEFT | lv.DIR.RIGHT)
t4 = tv.add_tile(3, 0, lv.DIR.LEFT | lv.DIR.RIGHT)
t5 = tv.add_tile(4, 0, lv.DIR.LEFT)

cards = []
for i, s in enumerate(SERVERS):
    cards.append(create_server_card(t1, s["name"], i))

chore_screen.build(t2)
btc_ui = create_btc_card(t3)
n8n_screen.build(t4)
create_placeholder(t5, "Home Assistant", lv.SYMBOL.HOME)

# Bottom nav bar
bottom_bar = lv.obj(scr)
bottom_bar.set_size(800, 60)
bottom_bar.align(lv.ALIGN.BOTTOM_MID, 0, 0)
bottom_bar.set_style_bg_color(lv.color_hex(0x222222), 0)
bottom_bar.set_style_border_width(0, 0)
bottom_bar.remove_flag(lv.obj.FLAG.SCROLLABLE)

def get_page():
    a = tv.get_tile_active()
    if a == t1: return 0
    if a == t2: return 1
    if a == t3: return 2
    if a == t4: return 3
    if a == t5: return 4
    return 0

def nav_left(e):
    idx = get_page()
    if idx > 0: tv.set_tile_by_index(idx - 1, 0, 1)

def nav_right(e):
    idx = get_page()
    if idx < TOTAL_TILES - 1: tv.set_tile_by_index(idx + 1, 0, 1)

btn_prev = lv.button(bottom_bar)
btn_prev.set_size(80, 40)
btn_prev.align(lv.ALIGN.LEFT_MID, 10, 0)
btn_prev.add_event_cb(nav_left, lv.EVENT.PRESSED, None)
label_color(lv.label(btn_prev)).set_text(lv.SYMBOL.LEFT)

btn_next = lv.button(bottom_bar)
btn_next.set_size(80, 40)
btn_next.align(lv.ALIGN.RIGHT_MID, -10, 0)
btn_next.add_event_cb(nav_right, lv.EVENT.PRESSED, None)
label_color(lv.label(btn_next)).set_text(lv.SYMBOL.RIGHT)

force_refresh = True

def refresh_btn_pressed(e):
    global force_refresh
    lbl_refresh.set_text("Fetching...")
    force_refresh = True

refresh_btn = lv.button(bottom_bar)
refresh_btn.set_size(200, 40)
refresh_btn.align(lv.ALIGN.CENTER, 0, 0)
refresh_btn.add_event_cb(refresh_btn_pressed, lv.EVENT.PRESSED, None)
lbl_refresh = label_color(lv.label(refresh_btn))
lbl_refresh.set_text("Refresh Data")
lbl_refresh.center()

print("UI built. Starting main loop...")

# ================= MAIN LOOP =================
# State machine — one unit of work per iteration to stay LVGL-friendly.
# 0        — idle / check intervals
# 1,2,3    — fetch server stats (one server per state)
# 4        — fetch BTC
# 5        — fetch chores
# Back to 0 after state 5.

server_last        = 0
btc_last           = 0
chore_last         = 0
display_reset_last = 0
state              = 0

DISPLAY_RESET_MS = 20 * 60 * 1000  # reset display every 20 min to prevent drift

while True:
    n8n_screen.tick()
    chore_screen.tick()
    time.sleep_ms(5)

    now = time.ticks_ms()

    # Periodic display re-init to prevent RGB bus drift
    if time.ticks_diff(now, display_reset_last) > DISPLAY_RESET_MS:
        display.init()
        display_reset_last = now
        print("Display re-init")

    if state == 0:
        if force_refresh or time.ticks_diff(now, server_last) > SERVER_INTERVAL_MS:
            state = 1
            server_last = now

    elif state == 1:
        print("Fetch server 0")
        update_card_ui(cards[0], fetch_stats(SERVERS[0]["ip"]), SERVERS[0]["role"])
        state = 2

    elif state == 2:
        print("Fetch server 1")
        update_card_ui(cards[1], fetch_stats(SERVERS[1]["ip"]), SERVERS[1]["role"])
        state = 3

    elif state == 3:
        print("Fetch server 2")
        update_card_ui(cards[2], fetch_stats(SERVERS[2]["ip"]), SERVERS[2]["role"])
        state = 4

    elif state == 4:
        if force_refresh or time.ticks_diff(now, btc_last) > BTC_INTERVAL_MS:
            print("Fetch BTC")
            price, history = fetch_btc()
            update_btc_ui(btc_ui, price, history)
            btc_last = now
        state = 5

    elif state == 5:
        if force_refresh or time.ticks_diff(now, chore_last) > CHORE_INTERVAL_MS:
            print("Fetch chores")
            chore_data = fetch_chores()
            if chore_data:
                chore_screen.refresh(chore_data)
            chore_last = now
        state = 0
        force_refresh = False
        lbl_refresh.set_text("Refresh Data")

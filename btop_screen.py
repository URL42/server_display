# btop_screen.py
# btop-style system monitor screen for Waveshare ESP32-S3-Touch-LCD-4.3
# Tile 3 in the tileview.
#
# Pattern matches n8n_screen.py / chore_screen.py:
#   build(tile)          — call once during UI init
#   update(base, detail) — call from main loop after fetch_stats(ip, detail=True)
#
# Data comes from stats_server.py on the server via /stats?detail=true
#   base   = top-level JSON (host, cpu, mem, disks, docker, ts)
#   detail = JSON["detail"] (cores, mem_detail, swap, net, procs, load, uptime_s)
#
# FIRMWARE NOTES (this lvgl_micropython build):
#   - canvas.draw_rect() unavailable in LVGL9 — CPU/net graphs use lv.chart
#     (line, SHIFT mode) instead of the original dot-matrix canvases.
#   - lv.ANIM does not exist — bar.set_value(v, 0)
#   - remove_flag, not clear_flag
#   - fonts: montserrat 12/14/16 only (original used 10)
#   - Layout compressed from 480 to 420px tall (nav bar owns the bottom 60).

import lvgl as lv

# ── module state ──────────────────────────────────────────────────────────────
_built       = False
_net_dn_hist = []
_net_up_hist = []

_lbl_host    = None
_lbl_uptime  = None
_lbl_load    = None
_lbl_net_up  = None
_lbl_net_dn  = None
_cpu_chart   = None
_cpu_ser     = None
_net_chart   = None
_net_dn_ser  = None
_net_up_ser  = None
_core_bars   = []   # list of (lv.bar, lbl_pct, lbl_temp) per core
_mem_bars    = {}   # keys: "ram", "swap"
_mem_labels  = {}   # keys: "total","used","avail","cached","free","swap"
_proc_labels = []   # list of (pid,name,cpu,mem,thr,mb) label tuples
_disk_labels = []   # list of (mount,size,pct) label tuples

N_CORES = 8
N_PROCS = 9
N_DISKS = 5

# ── colours ───────────────────────────────────────────────────────────────────
C_BG       = lv.color_hex(0x0d0d0d)
C_BOX      = lv.color_hex(0x1c1c1c)
C_CPU_BDR  = lv.color_hex(0x1e4870)
C_MEM_BDR  = lv.color_hex(0x7a4f1a)
C_DISK_BDR = lv.color_hex(0x505015)
C_PROC_BDR = lv.color_hex(0x602020)
C_NET_BDR  = lv.color_hex(0x1e5560)
C_CPU_DOT  = lv.color_hex(0x1e5888)
C_NET_DOT  = lv.color_hex(0x1a6070)
C_GREEN    = lv.color_hex(0x5bbf61)
C_BLUE     = lv.color_hex(0x3878aa)
C_AMBER    = lv.color_hex(0xc07830)
C_RED      = lv.color_hex(0xcc3333)
C_DIM      = lv.color_hex(0x2a2a2a)
C_MID      = lv.color_hex(0x555555)
C_TEXT     = lv.color_hex(0xc8c8c8)


# ── helpers ───────────────────────────────────────────────────────────────────

def _label(parent, text, color, x, y, font=None):
    lb = lv.label(parent)
    lb.set_text(text)
    lb.set_style_text_color(color, 0)
    lb.set_style_text_font(font or lv.font_montserrat_12, 0)
    lb.set_pos(x, y)
    return lb


def _box(parent, x, y, w, h, border_color, title=None):
    cont = lv.obj(parent)
    cont.set_pos(x, y)
    cont.set_size(w, h)
    cont.set_style_bg_color(C_BOX, 0)
    cont.set_style_bg_opa(lv.OPA.COVER, 0)
    cont.set_style_border_color(border_color, 0)
    cont.set_style_border_width(1, 0)
    cont.set_style_radius(2, 0)
    cont.set_style_pad_all(5, 0)
    cont.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)
    cont.remove_flag(lv.obj.FLAG.SCROLLABLE)
    if title:
        t = lv.label(cont)
        t.set_text(title)
        t.set_style_text_color(border_color, 0)
        t.set_style_text_font(lv.font_montserrat_12, 0)
        t.align(lv.ALIGN.TOP_LEFT, 2, -4)
    return cont


def _mini_bar(parent, x, y, w, h, ind_color):
    b = lv.bar(parent)
    b.set_size(w, h)
    b.set_pos(x, y)
    b.set_range(0, 100)
    b.set_value(0, 0)
    b.set_style_bg_color(lv.color_hex(0x111111), 0)
    b.set_style_bg_color(ind_color, lv.PART.INDICATOR)
    b.set_style_radius(0, 0)
    b.set_style_radius(0, lv.PART.INDICATOR)
    return b


def _chart(parent, x, y, w, h, points):
    ch = lv.chart(parent)
    ch.set_size(w, h)
    ch.set_pos(x, y)
    ch.set_type(lv.chart.TYPE.LINE)
    ch.set_update_mode(lv.chart.UPDATE_MODE.SHIFT)
    ch.set_point_count(points)
    ch.set_axis_range(lv.chart.AXIS.PRIMARY_Y, 0, 100)
    ch.set_style_bg_color(lv.color_hex(0x090909), 0)
    ch.set_style_bg_opa(lv.OPA.COVER, 0)
    ch.set_style_border_width(0, 0)
    ch.set_style_size(0, 0, lv.PART.INDICATOR)   # hide point dots
    ch.set_div_line_count(3, 0)
    ch.set_style_line_color(lv.color_hex(0x1a1a1a), lv.PART.MAIN)
    return ch


def _bar_color(pct):
    if pct > 80: return C_RED
    if pct > 60: return C_AMBER
    return C_BLUE


def _fmt_uptime(secs):
    try:
        secs = int(secs)
    except Exception:
        secs = 0
    d = secs // 86400
    h = (secs % 86400) // 3600
    m = (secs % 3600) // 60
    return "up {}d {:02d}:{:02d}".format(d, h, m)


# ── build ─────────────────────────────────────────────────────────────────────

def build(tile):
    """Build the full btop layout on the tileview tile (800x420)."""
    global _built, _cpu_chart, _cpu_ser, _net_chart, _net_dn_ser, _net_up_ser
    global _lbl_host, _lbl_uptime, _lbl_load, _lbl_net_up, _lbl_net_dn

    tile.set_style_bg_color(C_BG, 0)
    tile.set_style_pad_all(4, 0)
    tile.remove_flag(lv.obj.FLAG.SCROLLABLE)

    # ── TOP BAR ──────────────────────────────────────────────────────────────
    topbar = lv.obj(tile)
    topbar.set_size(792, 18)
    topbar.set_pos(0, 0)
    topbar.set_style_bg_color(lv.color_hex(0x050505), 0)
    topbar.set_style_bg_opa(lv.OPA.COVER, 0)
    topbar.set_style_border_width(0, 0)
    topbar.set_style_pad_all(2, 0)
    topbar.remove_flag(lv.obj.FLAG.SCROLLABLE)

    _lbl_host   = _label(topbar, "----",         C_BLUE, 0,   0)
    _lbl_uptime = _label(topbar, "up 0d 00:00",  C_MID,  120, 0)

    # ── CPU HISTORY BOX (left) ───────────────────────────────────────────────
    cpu_box = _box(tile, 0, 22, 486, 150, C_CPU_BDR, "- CPU History -")
    _cpu_chart = _chart(cpu_box, 0, 14, 472, 118, 60)
    _cpu_ser   = _cpu_chart.add_series(C_CPU_DOT, lv.chart.AXIS.PRIMARY_Y)

    # ── CORE DETAIL BOX (right) ──────────────────────────────────────────────
    cd_box = _box(tile, 490, 22, 302, 150, C_CPU_BDR, "- Cores -")
    del _core_bars[:]
    for i in range(N_CORES):
        col = i % 2
        row = i // 2
        ox  = col * 148
        oy  = row * 27 + 12
        _label(cd_box, "C{}".format(i), C_MID, ox, oy)
        b  = _mini_bar(cd_box, ox + 20, oy + 3, 68, 8, C_BLUE)
        lp = _label(cd_box, "0%",  C_MID,   ox + 92,  oy)
        lt = _label(cd_box, "--",  C_AMBER, ox + 122, oy)
        _core_bars.append((b, lp, lt))
    _lbl_load = _label(cd_box, "Load: --", C_MID, 0, 122)

    # ── BOTTOM ROW: mem | disks | net | proc ─────────────────────────────────
    BY, BH = 176, 236

    # MEM BOX
    mem_box = _box(tile, 0, BY, 182, BH, C_MEM_BDR, "- mem -")
    _mem_labels.clear()
    _mem_bars.clear()
    mem_rows = [
        ("Total:",  "total",  None),
        ("Used:",   "used",   "ram"),
        ("Avail:",  "avail",  None),
        ("Cached:", "cached", None),
        ("Free:",   "free",   None),
        ("Swap:",   "swap",   "swap"),
    ]
    for idx, (lbl_txt, key, bar_key) in enumerate(mem_rows):
        ry = 12 + idx * 35
        _label(mem_box, lbl_txt, C_MID, 0, ry)
        vl = _label(mem_box, "--.-G", C_AMBER if key != "swap" else C_MID, 65, ry)
        _mem_labels[key] = vl
        if bar_key:
            _mem_bars[bar_key] = _mini_bar(
                mem_box, 0, ry + 15, 168, 7,
                C_AMBER if bar_key == "ram" else C_MID)

    # DISK BOX
    disk_box = _box(tile, 186, BY, 182, BH, C_DISK_BDR, "- disks -")
    del _disk_labels[:]
    for i in range(N_DISKS):
        dy = 12 + i * 44
        mn  = _label(disk_box, "", C_AMBER, 0, dy)
        sz  = _label(disk_box, "", C_MID,   0, dy + 14)
        pct = _label(disk_box, "", C_MID,  90, dy + 14)
        _disk_labels.append((mn, sz, pct))

    # NET BOX
    net_box = _box(tile, 372, BY, 114, BH, C_NET_BDR, "- net -")
    _lbl_net_up = _label(net_box, "UP --", C_GREEN, 0, 12)
    _lbl_net_dn = _label(net_box, "DN --", C_BLUE,  0, 28)
    _net_chart  = _chart(net_box, 0, 48, 100, 172, 40)
    _net_up_ser = _net_chart.add_series(C_GREEN,   lv.chart.AXIS.PRIMARY_Y)
    _net_dn_ser = _net_chart.add_series(C_NET_DOT, lv.chart.AXIS.PRIMARY_Y)

    # PROC BOX
    proc_box = _box(tile, 490, BY, 302, BH, C_PROC_BDR, "- proc -")
    _label(proc_box, "PID",  C_MID, 0,   12)
    _label(proc_box, "NAME", C_MID, 46,  12)
    _label(proc_box, "CPU%", C_MID, 132, 12)
    _label(proc_box, "MEM%", C_MID, 176, 12)
    _label(proc_box, "THR",  C_MID, 218, 12)
    _label(proc_box, "MB",   C_MID, 252, 12)
    del _proc_labels[:]
    for i in range(N_PROCS):
        py = 30 + i * 21
        pid  = _label(proc_box, "", C_MID,   0,   py)
        name = _label(proc_box, "", C_TEXT,  46,  py)
        cpu  = _label(proc_box, "", C_GREEN, 132, py)
        mem  = _label(proc_box, "", C_MID,   176, py)
        thr  = _label(proc_box, "", C_MID,   218, py)
        mb   = _label(proc_box, "", C_MID,   252, py)
        _proc_labels.append((pid, name, cpu, mem, thr, mb))

    _built = True
    print("btop_screen: build OK")


# ── update ────────────────────────────────────────────────────────────────────

def update(base, detail):
    """
    Push fresh data into the screen widgets.
    base   = full /stats?detail=true response dict
    detail = base["detail"] sub-dict
    """
    if not _built or not base:
        return
    detail = detail or {}

    # Host / uptime / load
    _lbl_host.set_text(str(base.get("host", "?")))
    _lbl_uptime.set_text(_fmt_uptime(detail.get("uptime_s", 0)))
    load = detail.get("load", [0, 0, 0])
    try:
        _lbl_load.set_text("Load: {:.2f} {:.2f} {:.2f}".format(*load))
    except Exception:
        _lbl_load.set_text("Load: --")

    # CPU history chart
    try:
        cpu = int(float(base.get("cpu", 0)))
    except Exception:
        cpu = 0
    if _cpu_chart:
        _cpu_chart.set_next_value(_cpu_ser, cpu)

    # Per-core bars (cores are dicts: {"pct":…, "temp":…}; tolerate plain numbers)
    cores = detail.get("cores", [])
    for i, (bar_obj, lp, lt) in enumerate(_core_bars):
        if i < len(cores):
            c = cores[i]
            if isinstance(c, dict):
                pct  = int(c.get("pct", 0))
                temp = c.get("temp")
            else:
                try:
                    pct = int(float(c))
                except Exception:
                    pct = 0
                temp = None
            bar_obj.set_value(pct, 0)
            bar_obj.set_style_bg_color(_bar_color(pct), lv.PART.INDICATOR)
            lp.set_text("{}%".format(pct))
            lt.set_text("{}".format(int(temp)) if temp is not None else "--")
        else:
            bar_obj.set_value(0, 0)
            lp.set_text("")
            lt.set_text("")

    # Memory
    md = detail.get("mem_detail", {})
    sw = detail.get("swap", {})
    for key, fmt, src in [
        ("total",  "{:.1f}G", md.get("total_gb",  0)),
        ("used",   "{:.2f}G", md.get("used_gb",   0)),
        ("avail",  "{:.1f}G", md.get("avail_gb",  0)),
        ("cached", "{:.1f}G", md.get("cached_gb", 0)),
        ("free",   "{:.1f}G", md.get("free_gb",   0)),
        ("swap",   "{:.2f}G", sw.get("used_gb",   0)),
    ]:
        if key in _mem_labels:
            try:
                _mem_labels[key].set_text(fmt.format(src))
            except Exception:
                _mem_labels[key].set_text("--")
    if "ram" in _mem_bars:
        _mem_bars["ram"].set_value(int(md.get("percent", 0)), 0)
    if "swap" in _mem_bars:
        _mem_bars["swap"].set_value(int(sw.get("percent", 0)), 0)

    # Disks
    disks = base.get("disks", [])
    for i, (mn, sz, pct) in enumerate(_disk_labels):
        if i < len(disks):
            d = disks[i]
            mn.set_text(str(d.get("mount", ""))[:16])
            sz.set_text("{:.0f}G".format(d.get("total_gb", 0)))
            p = d.get("used_pct", 0)
            pct.set_text("{}%".format(p))
        else:
            mn.set_text(""); sz.set_text(""); pct.set_text("")

    # Network — two chart series normalised against shared max
    net = detail.get("net", {})
    up  = net.get("up_kbs", 0)
    dn  = net.get("dn_kbs", 0)
    _net_dn_hist.append(dn)
    _net_up_hist.append(up)
    if len(_net_dn_hist) > 40: _net_dn_hist.pop(0)
    if len(_net_up_hist) > 40: _net_up_hist.pop(0)
    _lbl_net_dn.set_text("DN {:.1f}K".format(dn))
    _lbl_net_up.set_text("UP {:.1f}K".format(up))
    if _net_chart:
        mx = max(max(_net_dn_hist, default=1), max(_net_up_hist, default=1), 1)
        _net_chart.set_next_value(_net_up_ser, int(up / mx * 100))
        _net_chart.set_next_value(_net_dn_ser, int(dn / mx * 100))

    # Processes
    procs = detail.get("procs", [])
    for i, (pid, name, cpu_l, mem_l, thr, mb) in enumerate(_proc_labels):
        if i < len(procs):
            p       = procs[i]
            cpu_val = p.get("cpu", 0)
            pid.set_text(str(p.get("pid", "")))
            name.set_text(str(p.get("name", ""))[:12])
            cpu_l.set_text("{:.1f}".format(cpu_val))
            cpu_l.set_style_text_color(
                C_RED   if cpu_val > 50 else
                C_AMBER if cpu_val > 15 else C_GREEN, 0)
            mem_l.set_text("{:.1f}".format(p.get("mem", 0)))
            thr.set_text(str(p.get("thr", "")))
            mb.set_text("{:.0f}".format(p.get("mem_mb", 0)))
        else:
            for lbl in (pid, name, cpu_l, mem_l, thr, mb):
                lbl.set_text("")

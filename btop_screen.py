# btop_screen.py
# btop-style system monitor screen for Waveshare ESP32-S3-Touch-LCD-4.3
# Tile 3 (or whatever index screen 3 lives at) in the tileview.
#
# Pattern matches n8n_screen.py / chore_screen.py:
#   build(tile)          — call once during UI init
#   update(base, detail) — call from main loop after fetch_stats(detail=True)
#
# Data comes from stats_server.py on bossbitch via /stats?detail=true
# base   = top-level JSON (host, cpu, mem, disks, docker, ts)
# detail = JSON["detail"] (cores, mem_detail, swap, net, procs, load, uptime_s)

import lvgl as lv

# ── module state ──────────────────────────────────────────────────────────────
_built       = False
_cpu_history = [0] * 60   # rolling 60-sample history for dot graph
_net_dn_hist = [0] * 40
_net_up_hist = [0] * 40
_net_canvas  = None

# LVGL object refs populated by build()
_lbl_host    = None
_lbl_uptime  = None
_lbl_load    = None
_lbl_net_up  = None
_lbl_net_dn  = None
_cpu_canvas  = None
_core_bars   = []   # list of (lv.bar, lbl_pct, lbl_temp) per core
_mem_bars    = {}   # keys: "ram", "swap"
_mem_labels  = {}   # keys: "total","used","avail","cached","free","swap"
_proc_labels = []   # list of (pid,name,cpu,mem,thr,mb) label tuples
_disk_labels = []   # list of (mount,size,pct) label tuples


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
    lb.set_style_text_font(font or lv.font_montserrat_10, 0)
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
    cont.clear_flag(lv.obj.FLAG.SCROLLABLE)
    if title:
        t = lv.label(cont)
        t.set_text(title)
        t.set_style_text_color(border_color, 0)
        t.set_style_text_font(lv.font_montserrat_10, 0)
        t.align(lv.ALIGN.TOP_LEFT, 2, -8)
    return cont


def _bar_color(pct):
    if pct > 80: return C_RED
    if pct > 60: return C_AMBER
    return C_BLUE


def _fmt_uptime(secs):
    d = secs  // 86400
    h = (secs % 86400) // 3600
    m = (secs % 3600)  // 60
    return "up {}d {:02d}:{:02d}".format(d, h, m)


def _draw_dot_graph(canvas, history, color, w, h, y_offset=0):
    """
    Dot-matrix style graph. Fills from the bottom of its band upward,
    proportional to value (0-100).

    y_offset allows two graphs to share one canvas without overwriting
    each other — pass y_offset=0 for the top band, y_offset=h for the
    bottom band (where h is the band height, not the full canvas height).

    Each call redraws only its own band, so call order doesn't matter.
    """
    bg = lv.draw_rect_dsc_t()
    bg.init()
    bg.bg_color = lv.color_hex(0x090909)
    bg.bg_opa   = lv.OPA.COVER
    bg.radius   = 0
    canvas.draw_rect(0, y_offset, w, h, bg)

    grid = lv.draw_rect_dsc_t()
    grid.init()
    grid.bg_color = lv.color_hex(0x1a1a1a)
    grid.bg_opa   = lv.OPA.COVER
    grid.radius   = 0
    for frac in (0.25, 0.50, 0.75):
        canvas.draw_rect(0, y_offset + int(h * frac), w, 1, grid)

    DOT  = 2
    STEP = 4
    cols = w  // STEP
    rows = h  // STEP

    dot = lv.draw_rect_dsc_t()
    dot.init()
    dot.bg_color = color
    dot.bg_opa   = lv.OPA.COVER
    dot.radius   = 0

    for col, val in enumerate(history[-cols:]):
        filled = int(max(0, min(100, val)) / 100 * rows)
        for row in range(rows - filled, rows):
            canvas.draw_rect(col * STEP, y_offset + row * STEP, DOT, DOT, dot)


# ── build ─────────────────────────────────────────────────────────────────────

def build(tile):
    """
    Build the full btop layout on the tileview tile.
    Call once during UI init.
    """
    global _built, _cpu_canvas, _net_canvas
    global _core_bars, _mem_bars, _mem_labels
    global _proc_labels, _disk_labels
    global _lbl_host, _lbl_uptime, _lbl_load, _lbl_net_up, _lbl_net_dn

    tile.set_style_bg_color(C_BG, 0)
    tile.set_style_pad_all(4, 0)
    tile.clear_flag(lv.obj.FLAG.SCROLLABLE)

    # ── TOP BAR ───────────────────────────────────────────────────────────────
    topbar = lv.obj(tile)
    topbar.set_size(792, 18)
    topbar.set_pos(0, 0)
    topbar.set_style_bg_color(lv.color_hex(0x050505), 0)
    topbar.set_style_bg_opa(lv.OPA.COVER, 0)
    topbar.set_style_border_width(0, 0)
    topbar.set_style_pad_all(2, 0)
    topbar.clear_flag(lv.obj.FLAG.SCROLLABLE)

    _lbl_host   = _label(topbar, "bossbitch",   C_BLUE,  0,   0)
    _lbl_uptime = _label(topbar, "up 0d 00:00", C_DIM,  100,  0)

    # ── CPU HISTORY BOX ───────────────────────────────────────────────────────
    # Left side, 486px wide x 155px tall
    cpu_box = _box(tile, 0, 22, 486, 155, C_CPU_BDR, "- CPU History -")

    CB_W = 474
    CB_H = 138
    _cpu_canvas = lv.canvas(cpu_box)
    _cpu_canvas.set_size(CB_W, CB_H)
    _cpu_canvas.set_pos(0, 8)
    # FIX: lv.img.CF.TRUE_COLOR is LVGL8 API — use lv.COLOR_FORMAT.NATIVE in LVGL9
    _cpu_canvas.set_buffer(bytearray(CB_W * CB_H * 2), CB_W, CB_H, lv.COLOR_FORMAT.NATIVE)

    # ── CORE DETAIL BOX ───────────────────────────────────────────────────────
    # Right side, 302px wide x 155px tall, 2-column core grid
    cd_box = _box(tile, 490, 22, 302, 155, C_CPU_BDR, "- Cores -")
    _core_bars = []

    for i in range(8):
        col = i % 2
        row = i // 2
        ox  = col * 148
        oy  = row * 30 + 12

        _label(cd_box, "C{}".format(i), C_MID, ox, oy)

        b = lv.bar(cd_box)
        b.set_size(92, 8)
        b.set_pos(ox + 20, oy + 2)
        b.set_range(0, 100)
        b.set_value(0, lv.ANIM.OFF)
        b.set_style_bg_color(lv.color_hex(0x111111), 0)
        b.set_style_bg_color(C_BLUE, lv.PART.INDICATOR)
        b.set_style_radius(0, 0)
        b.set_style_radius(0, lv.PART.INDICATOR)

        # FIX: moved lp to ox+114, lt to ox+136 to avoid overlap at "100%"
        lp = _label(cd_box, "0%",   C_MID,   ox + 114, oy)
        lt = _label(cd_box, "--°",  C_AMBER, ox + 136, oy)
        _core_bars.append((b, lp, lt))

    _lbl_load = _label(cd_box, "Load: --", C_DIM, 0, 138)

    # ── BOTTOM ROW ────────────────────────────────────────────────────────────
    # mem | disk | net | proc

    # MEM BOX — 182px wide
    mem_box = _box(tile, 0, 181, 182, 295, C_MEM_BDR, "- mem -")
    _mem_labels = {}
    _mem_bars   = {}

    mem_rows = [
        ("Total:",  "total",  None),
        ("Used:",   "used",   "ram"),
        ("Avail:",  "avail",  None),
        ("Cached:", "cached", None),
        ("Free:",   "free",   None),
        ("Swap:",   "swap",   "swap"),
    ]
    for idx, (lbl_txt, key, bar_key) in enumerate(mem_rows):
        ry = 10 + idx * 44
        _label(mem_box, lbl_txt, C_MID, 0, ry)
        vl = _label(mem_box, "--.-G", C_AMBER if key != "swap" else C_MID, 65, ry)
        _mem_labels[key] = vl
        if bar_key:
            b = lv.bar(mem_box)
            b.set_size(168, 7)
            b.set_pos(0, ry + 14)
            b.set_range(0, 100)
            b.set_value(0, lv.ANIM.OFF)
            b.set_style_bg_color(lv.color_hex(0x111111), 0)
            b.set_style_bg_color(C_AMBER if bar_key == "ram" else C_MID, lv.PART.INDICATOR)
            b.set_style_radius(0, 0)
            b.set_style_radius(0, lv.PART.INDICATOR)
            _mem_bars[bar_key] = b

    # DISK BOX — 182px wide
    disk_box = _box(tile, 186, 181, 182, 295, C_DISK_BDR, "- disks -")
    _disk_labels = []
    for i in range(5):
        dy = 10 + i * 55
        mn  = _label(disk_box, "", C_AMBER, 0,  dy)
        sz  = _label(disk_box, "", C_DIM,   0,  dy + 14)
        pct = _label(disk_box, "", C_MID,   0,  dy + 28)
        _disk_labels.append((mn, sz, pct))

    # NET BOX — 114px wide
    net_box = _box(tile, 372, 181, 114, 295, C_NET_BDR, "- net -")
    _lbl_net_dn = _label(net_box, "DN --", C_BLUE,  0, 10)
    _lbl_net_up = _label(net_box, "UP --", C_GREEN, 0, 26)

    _net_canvas = lv.canvas(net_box)
    NW, NH = 102, 200
    _net_canvas.set_size(NW, NH)
    _net_canvas.set_pos(0, 46)
    # FIX: lv.img.CF.TRUE_COLOR is LVGL8 API — use lv.COLOR_FORMAT.NATIVE in LVGL9
    _net_canvas.set_buffer(bytearray(NW * NH * 2), NW, NH, lv.COLOR_FORMAT.NATIVE)

    # PROC BOX — fills remaining right side
    proc_box = _box(tile, 490, 181, 302, 295, C_PROC_BDR, "- proc · bossbitch -")

    _label(proc_box, "PID",  C_DIM,  0,  10)
    _label(proc_box, "NAME", C_DIM,  46, 10)
    _label(proc_box, "CPU%", C_DIM, 132, 10)
    _label(proc_box, "MEM%", C_DIM, 172, 10)
    _label(proc_box, "THR",  C_DIM, 210, 10)
    _label(proc_box, "MB",   C_DIM, 246, 10)

    _proc_labels = []
    for i in range(12):
        py = 24 + i * 22
        pid  = _label(proc_box, "", C_MID,   0,  py)
        name = _label(proc_box, "", C_TEXT,  46,  py)
        cpu  = _label(proc_box, "", C_GREEN, 132, py)
        mem  = _label(proc_box, "", C_MID,   172, py)
        thr  = _label(proc_box, "", C_DIM,   210, py)
        mb   = _label(proc_box, "", C_DIM,   246, py)
        _proc_labels.append((pid, name, cpu, mem, thr, mb))

    _built = True
    print("btop_screen: build OK")


# ── update ────────────────────────────────────────────────────────────────────

def update(base, detail):
    """
    Push fresh data into the screen widgets.
    base   = full /stats?detail=true response dict
    detail = base["detail"] sub-dict
    Called from the main loop state machine.
    """
    global _cpu_history, _net_dn_hist, _net_up_hist

    if not _built:
        return

    # Host / uptime / load
    _lbl_host.set_text(base.get("host", "?"))
    _lbl_uptime.set_text(_fmt_uptime(detail.get("uptime_s", 0)))
    load = detail.get("load", [0, 0, 0])
    _lbl_load.set_text("Load: {:.2f} {:.2f} {:.2f}".format(*load))

    # CPU history graph
    _cpu_history.append(base.get("cpu", 0))
    if len(_cpu_history) > 60:
        _cpu_history.pop(0)
    if _cpu_canvas:
        cw = _cpu_canvas.get_width()
        ch = _cpu_canvas.get_height()
        _draw_dot_graph(_cpu_canvas, _cpu_history, C_CPU_DOT, cw, ch)

    # Per-core bars
    cores = detail.get("cores", [])
    for i, (bar_obj, lp, lt) in enumerate(_core_bars):
        if i < len(cores):
            c    = cores[i]
            pct  = int(c.get("pct", 0))
            temp = c.get("temp")
            bar_obj.set_value(pct, lv.ANIM.OFF)
            bar_obj.set_style_bg_color(_bar_color(pct), lv.PART.INDICATOR)
            lp.set_text("{}%".format(pct))
            lt.set_text("{}°".format(int(temp)) if temp is not None else "--")

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
            _mem_labels[key].set_text(fmt.format(src))

    if "ram"  in _mem_bars:
        _mem_bars["ram"].set_value(int(md.get("percent", 0)), lv.ANIM.OFF)
    if "swap" in _mem_bars:
        _mem_bars["swap"].set_value(int(sw.get("percent", 0)), lv.ANIM.OFF)

    # Disks
    disks = base.get("disks", [])
    for i, (mn, sz, pct) in enumerate(_disk_labels):
        if i < len(disks):
            d = disks[i]
            mn.set_text(d.get("mount", ""))
            sz.set_text("{:.0f}G".format(d.get("total_gb", 0)))
            pct.set_text("{}%".format(d.get("used_pct", 0)))
        else:
            mn.set_text(""); sz.set_text(""); pct.set_text("")

    # Network
    # FIX: original code called _draw_dot_graph twice with no y_offset, so the
    # second draw's background clear erased the first graph entirely.
    # Now each call draws into its own half of the canvas via y_offset.
    net = detail.get("net", {})
    up  = net.get("up_kbs", 0)
    dn  = net.get("dn_kbs", 0)
    _net_dn_hist.append(dn)
    _net_up_hist.append(up)
    if len(_net_dn_hist) > 40: _net_dn_hist.pop(0)
    if len(_net_up_hist) > 40: _net_up_hist.pop(0)
    _lbl_net_dn.set_text("DN {:.1f}K".format(dn))
    _lbl_net_up.set_text("UP {:.1f}K".format(up))
    if _net_canvas:
        nw = _net_canvas.get_width()
        nh = _net_canvas.get_height()
        half = nh // 2
        # Normalise both series against shared max so they're visually comparable
        mx = max(max(_net_dn_hist, default=1), max(_net_up_hist, default=1), 1)
        norm_dn = [v / mx * 100 for v in _net_dn_hist]
        norm_up = [v / mx * 100 for v in _net_up_hist]
        # Upload in top half (y_offset=0), download in bottom half (y_offset=half)
        _draw_dot_graph(_net_canvas, norm_up, C_GREEN,   nw, half, y_offset=0)
        _draw_dot_graph(_net_canvas, norm_dn, C_NET_DOT, nw, half, y_offset=half)

    # Processes
    procs = detail.get("procs", [])
    for i, (pid, name, cpu, mem, thr, mb) in enumerate(_proc_labels):
        if i < len(procs):
            p       = procs[i]
            cpu_val = p.get("cpu", 0)
            pid.set_text(str(p.get("pid", "")))
            name.set_text(p.get("name", "")[:12])
            cpu.set_text("{:.1f}".format(cpu_val))
            cpu.set_style_text_color(
                C_RED   if cpu_val > 50 else
                C_AMBER if cpu_val > 15 else C_GREEN, 0)
            mem.set_text("{:.1f}".format(p.get("mem", 0)))
            thr.set_text(str(p.get("thr", "")))
            mb.set_text("{:.0f}".format(p.get("mem_mb", 0)))
        else:
            for lbl in (pid, name, cpu, mem, thr, mb):
                lbl.set_text("")

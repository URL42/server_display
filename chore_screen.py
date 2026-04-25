# chore_screen.py
# Chore Chart screen — Waveshare ESP32-S3-Touch-LCD-4.3
# Tile 2 (index 1) in the tileview.
#
# Pattern matches n8n_screen.py:
#   build(tile)   — call once during UI init
#   tick()        — call every main loop iteration (noop unless pending work)
#   refresh(data) — called from main.py after fetch_chores() returns data
#
# Expected JSON from bossbitch GET /chores:
# {
#   "date": "2026-04-25",
#   "members": [
#     {
#       "name": "Mama",
#       "tasks": [
#         {"id": "123", "title": "Dishes",  "done": false, "overdue": false},
#         {"id": "124", "title": "Laundry", "done": true,  "overdue": false},
#         {"id": "125", "title": "Poop",    "done": false, "overdue": true}
#       ],
#       "xp": {"daily_pct": 67, "weekly_pct": 80}
#     },
#     {"name": "Baba",  "tasks": [...], "xp": {...}},
#     {"name": "Yun",   "tasks": [...], "xp": {...}, "level": 3}
#   ]
# }

import time
import gc
import ujson
import socket
import lvgl as lv

# ── Colour palette ─────────────────────────────────────────────────────────
C_BG         = lv.color_hex(0x0F1117)
C_HEADER_BG  = lv.color_hex(0x1A1D27)
C_COL_BG     = lv.color_hex(0x14171F)
C_COL_BORDER = lv.color_hex(0x2A2D3A)
C_TEXT       = lv.color_hex(0xE8EAF0)
C_SUBTEXT    = lv.color_hex(0x6B7080)
C_DONE       = lv.color_hex(0x3A3D4A)
C_OVERDUE    = lv.color_hex(0xFF3B3B)
C_PENDING    = lv.color_hex(0xC8CAD4)
C_BAR_BG     = lv.color_hex(0x2A2D3A)
C_BAR_DAILY  = lv.color_hex(0x4A9EFF)
C_BAR_WEEKLY = lv.color_hex(0x9B59FF)
C_YUN_LEVEL  = lv.color_hex(0xFFD700)
C_YUN_BAR_D  = lv.color_hex(0xFF6B9D)
C_YUN_BAR_W  = lv.color_hex(0xFFD700)
C_MAMA       = lv.color_hex(0xFF8C69)
C_BABA       = lv.color_hex(0x69B4FF)
C_YUN        = lv.color_hex(0xFF6B9D)
C_TICK       = lv.color_hex(0x4CAF50)
C_SCREEN_HDR = lv.color_hex(0x1E2130)

SCREEN_W = 800
TV_H     = 420   # tileview height as set in main.py
HEADER_H = 44
COL_W    = 266
COL_H    = TV_H - HEADER_H

# ── Module state ────────────────────────────────────────────────────────────
_tile          = None
_col_state     = [False, False, False]  # True = expanded (show completed)
_current_data  = None
_date_lbl      = None
_pending_complete = None   # dict or None — set by tap, consumed by tick()


# ── Helpers ─────────────────────────────────────────────────────────────────

def _member_color(idx):
    return (C_MAMA, C_BABA, C_YUN)[idx]

def _set_bg(obj, color, radius=0):
    obj.set_style_bg_color(color, 0)
    obj.set_style_bg_opa(lv.OPA.COVER, 0)
    obj.set_style_radius(radius, 0)

def _set_text(obj, color, font=None):
    obj.set_style_text_color(color, 0)
    if font:
        obj.set_style_text_font(font, 0)

def _make_bar(parent, x, y, w, h, fill_color, pct):
    bar = lv.bar(parent)
    bar.set_size(w, h)
    bar.set_pos(x, y)
    bar.set_range(0, 100)
    bar.set_value(pct, lv.ANIM.OFF)
    bar.set_style_bg_color(C_BAR_BG, lv.PART.MAIN)
    bar.set_style_bg_opa(lv.OPA.COVER, lv.PART.MAIN)
    bar.set_style_radius(h // 2, lv.PART.MAIN)
    bar.set_style_bg_color(fill_color, lv.PART.INDICATOR)
    bar.set_style_bg_opa(lv.OPA.COVER, lv.PART.INDICATOR)
    bar.set_style_radius(h // 2, lv.PART.INDICATOR)
    return bar


# ── HTTP POST for completion (raw socket, same pattern as main.py) ──────────

def _post_complete(task_id, member_name):
    try:
        import secrets
        host = secrets.BOSSBITCH_HOST
        port = int(secrets.BOSSBITCH_PORT)
    except Exception as e:
        print("secrets error:", repr(e))
        return False

    body       = ujson.dumps({"task_id": task_id, "member": member_name})
    body_bytes = body.encode()
    req = (
        "POST /chores/complete HTTP/1.0\r\n"
        "Host: {host}\r\n"
        "Content-Type: application/json\r\n"
        "Content-Length: {length}\r\n"
        "Connection: close\r\n\r\n"
        "{body}"
    ).format(host=host, length=len(body_bytes), body=body)

    gc.collect()
    s = None
    try:
        addr = socket.getaddrinfo(host, port)[0][-1]
        s = socket.socket()
        s.settimeout(4)
        s.connect(addr)
        s.write(req.encode())
        resp = b""
        while True:
            try:
                chunk = s.recv(512)
                if not chunk:
                    break
                resp += chunk
            except OSError:
                break
        if b"\r\n\r\n" in resp:
            json_body = resp.split(b"\r\n\r\n", 1)[1]
            result = ujson.loads(json_body)
            return bool(result.get("ok", False))
        return False
    except Exception as e:
        print("complete POST error:", repr(e))
        return False
    finally:
        try:
            if s: s.close()
        except Exception:
            pass
        gc.collect()


# ── Column builder ───────────────────────────────────────────────────────────

def _build_column(parent, col_idx, member, expanded):
    name  = member["name"]
    tasks = member.get("tasks", [])
    xp    = member.get("xp", {"daily_pct": 0, "weekly_pct": 0})
    level = member.get("level", None)
    acc   = _member_color(col_idx)

    col = lv.obj(parent)
    col.set_size(COL_W - 2, COL_H)
    col.set_pos(col_idx * COL_W + 1, HEADER_H)
    _set_bg(col, C_COL_BG)
    col.set_style_border_color(C_COL_BORDER, 0)
    col.set_style_border_width(1, 0)
    col.set_style_pad_all(0, 0)
    col.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)

    # Header
    hdr = lv.obj(col)
    hdr.set_size(COL_W - 2, 42)
    hdr.set_pos(0, 0)
    _set_bg(hdr, C_HEADER_BG)
    hdr.set_style_border_side(lv.BORDER_SIDE.BOTTOM, 0)
    hdr.set_style_border_color(acc, 0)
    hdr.set_style_border_width(2, 0)
    hdr.set_style_pad_all(0, 0)

    name_lbl = lv.label(hdr)
    name_lbl.set_text(name.upper())
    name_lbl.set_pos(10, 12)
    _set_text(name_lbl, acc, lv.font_montserrat_16)

    arrow = lv.label(hdr)
    arrow.set_text("v" if expanded else ">")
    arrow.set_pos(COL_W - 30, 14)
    _set_text(arrow, C_SUBTEXT, lv.font_montserrat_12)

    if level is not None:
        lvl_lbl = lv.label(hdr)
        lvl_lbl.set_text("LV.{}".format(level))
        lvl_lbl.set_pos(COL_W - 72, 14)
        _set_text(lvl_lbl, C_YUN_LEVEL, lv.font_montserrat_14)

    hdr.add_flag(lv.obj.FLAG.CLICKABLE)
    def _hdr_tap(e, ci=col_idx):
        _col_state[ci] = not _col_state[ci]
        _rebuild_columns()
    hdr.add_event_cb(_hdr_tap, lv.EVENT.CLICKED, None)

    # Task list
    task_cont = lv.obj(col)
    task_cont.set_size(COL_W - 2, COL_H - 42 - 82)
    task_cont.set_pos(0, 42)
    _set_bg(task_cont, C_COL_BG)
    task_cont.set_style_border_width(0, 0)
    task_cont.set_style_pad_hor(6, 0)
    task_cont.set_style_pad_top(4, 0)
    task_cont.set_style_pad_bottom(4, 0)
    task_cont.set_flex_flow(lv.FLEX_FLOW.COLUMN)
    task_cont.set_flex_align(lv.FLEX_ALIGN.START, lv.FLEX_ALIGN.START, lv.FLEX_ALIGN.START)
    task_cont.set_style_pad_row(2, 0)

    visible = 0
    for task in tasks:
        done    = task.get("done", False)
        overdue = task.get("overdue", False)
        task_id = task.get("id", "")
        title   = task.get("title", "")

        if done and not expanded:
            continue
        visible += 1

        row = lv.obj(task_cont)
        row.set_size(COL_W - 16, 30)
        row.set_style_min_height(30, 0)
        _set_bg(row, C_COL_BG)
        row.set_style_border_width(0, 0)
        row.set_style_pad_all(0, 0)
        row.set_style_radius(4, 0)

        indicator = lv.label(row)
        indicator.set_pos(4, 7)
        if done:
            indicator.set_text(lv.SYMBOL.OK)
            _set_text(indicator, C_TICK, lv.font_montserrat_14)
        else:
            indicator.set_text("o")
            _set_text(indicator, C_OVERDUE if overdue else C_SUBTEXT, lv.font_montserrat_14)

        t_lbl = lv.label(row)
        t_lbl.set_text(title)
        t_lbl.set_pos(22, 7)
        t_lbl.set_size(COL_W - 42, 18)
        t_lbl.set_long_mode(lv.label.LONG.CLIP)

        if done:
            _set_text(t_lbl, C_DONE, lv.font_montserrat_14)
            t_lbl.set_style_text_decor(lv.TEXT_DECOR.STRIKETHROUGH, 0)
            row.set_style_opa(lv.OPA._70, 0)
        elif overdue:
            _set_text(t_lbl, C_OVERDUE, lv.font_montserrat_14)
        else:
            _set_text(t_lbl, C_PENDING, lv.font_montserrat_14)

        if not done:
            row.add_flag(lv.obj.FLAG.CLICKABLE)
            def _make_cb(tid, mname, robj):
                def _cb(e):
                    global _pending_complete
                    if _pending_complete is None:
                        _pending_complete = {"task_id": tid, "member": mname, "row_obj": robj}
                        robj.set_style_opa(lv.OPA._50, 0)
                return _cb
            row.add_event_cb(_make_cb(task_id, name, row), lv.EVENT.CLICKED, None)

    if visible == 0:
        empty = lv.label(task_cont)
        empty.set_text("All done! " + lv.SYMBOL.OK if not expanded else "No tasks today")
        _set_text(empty, C_TICK if not expanded else C_SUBTEXT, lv.font_montserrat_14)

    # XP bars
    xp_y  = COL_H - 78
    bar_w = COL_W - 28
    d_pct = xp.get("daily_pct", 0)
    w_pct = xp.get("weekly_pct", 0)
    is_yun = (name == "Yun")

    d_lbl = lv.label(col)
    d_lbl.set_text("Daily  {}%".format(d_pct))
    d_lbl.set_pos(10, xp_y)
    _set_text(d_lbl, C_SUBTEXT, lv.font_montserrat_12)
    _make_bar(col, 10, xp_y + 14, bar_w, 8, C_YUN_BAR_D if is_yun else C_BAR_DAILY, d_pct)

    w_lbl = lv.label(col)
    w_lbl.set_text("Weekly  {}%".format(w_pct))
    w_lbl.set_pos(10, xp_y + 28)
    _set_text(w_lbl, C_SUBTEXT, lv.font_montserrat_12)
    _make_bar(col, 10, xp_y + 42, bar_w, 8, C_YUN_BAR_W if is_yun else C_BAR_WEEKLY, w_pct)


def _rebuild_columns():
    if _tile is None or _current_data is None:
        return
    cnt = _tile.get_child_cnt()
    for i in range(cnt - 1, -1, -1):
        c = _tile.get_child(i)
        if c and c.get_y() >= HEADER_H and c.get_width() >= COL_W - 4:
            c.delete()
    for i, member in enumerate(_current_data.get("members", [])[:3]):
        _build_column(_tile, i, member, _col_state[i])


# ── Public API ───────────────────────────────────────────────────────────────

def build(tile):
    """Call once: chore_screen.build(t2)"""
    global _tile, _date_lbl
    _tile = tile
    tile.set_style_bg_color(C_BG, 0)
    tile.set_style_pad_all(0, 0)

    hdr_bar = lv.obj(tile)
    hdr_bar.set_size(SCREEN_W, HEADER_H)
    hdr_bar.set_pos(0, 0)
    _set_bg(hdr_bar, C_SCREEN_HDR)
    hdr_bar.set_style_border_width(0, 0)
    hdr_bar.set_style_pad_all(0, 0)
    hdr_bar.clear_flag(lv.obj.FLAG.CLICKABLE)

    title_lbl = lv.label(hdr_bar)
    title_lbl.set_text(lv.SYMBOL.HOME + "  CHORES")
    title_lbl.set_pos(14, 12)
    _set_text(title_lbl, C_TEXT, lv.font_montserrat_16)

    _date_lbl = lv.label(hdr_bar)
    _date_lbl.set_text("--")
    _date_lbl.set_pos(SCREEN_W - 160, 14)
    _set_text(_date_lbl, C_SUBTEXT, lv.font_montserrat_14)
    _date_lbl.set_width(150)
    _date_lbl.set_long_mode(lv.label.LONG.CLIP)

    for i in (1, 2):
        div = lv.obj(tile)
        div.set_size(1, TV_H - HEADER_H)
        div.set_pos(i * COL_W, HEADER_H)
        _set_bg(div, C_COL_BORDER)
        div.set_style_border_width(0, 0)
        div.clear_flag(lv.obj.FLAG.CLICKABLE)

    loading = lv.label(tile)
    loading.set_text("Loading chores...")
    loading.align(lv.ALIGN.CENTER, 0, 0)
    _set_text(loading, C_SUBTEXT, lv.font_montserrat_16)

    print("chore screen built")


def refresh(data):
    """Called from main.py after a successful fetch_chores()."""
    global _current_data
    _current_data = data
    if _date_lbl:
        _date_lbl.set_text(data.get("date", "--"))
    _rebuild_columns()


def tick():
    """Call every main loop iteration. Handles pending completions."""
    global _pending_complete
    if _pending_complete is None:
        return
    item = _pending_complete
    _pending_complete = None
    ok = _post_complete(item["task_id"], item["member"])
    try:
        robj = item["row_obj"]
        if ok:
            robj.delete()
        else:
            robj.set_style_opa(lv.OPA.COVER, 0)
            print("complete failed for task", item["task_id"])
    except Exception:
        pass

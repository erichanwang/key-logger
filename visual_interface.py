import pynput
import threading
import tkinter as tk
import ctypes
import time as _time

VERSION = '0.0.2'

# ── DPI fix ────────────────────────────────────────────────────────────────────
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# ── Theme ──────────────────────────────────────────────────────────────────────
# MAGIC_BG is the transparency key — any pixel this color becomes see-through
MAGIC_BG    = '#010101'   # near-black but not true black (avoids key fill conflicts)
KEY_IDLE    = '#1e1e1e'
KEY_BORDER  = '#3a3a3a'
KEY_PRESS   = '#cc2222'
KEY_HOLD    = '#ff6600'
KEY_TEXT    = '#cccccc'
KEY_TEXT_HI = '#ffffff'
MOUSE_IDLE  = '#1e1e1e'
MOUSE_CLICK = '#cc2222'
MOUSE_SCR   = '#555555'
MOUSE_SCR_A = '#ff6600'
LOG_BG      = '#111111'
LOG_TEXT    = '#aaaaaa'
LOG_HI      = '#ffffff'
POS_TEXT    = '#88aaff'
SECTION_LBL = '#555555'

# ── Scale (2×) ─────────────────────────────────────────────────────────────────
U  = 44    # key unit size px (was 36, doubled → 72 but 44 keeps it on-screen well)
G  = 4     # gap between keys
FONT_KEY    = ('Consolas', 9)
FONT_KEY_SM = ('Consolas', 8)
FONT_UI     = ('Consolas', 10)
FONT_LABEL  = ('Consolas', 9)

# ── Shared state ───────────────────────────────────────────────────────────────
pressed_keys  = set()
held_keys_ui  = set()
mouse_buttons = set()
scroll_flash  = None
recent_logs   = []
mouse_status  = 'idle'
held_debounce = {}
HOLD_DEBOUNCE = 0.3

# ── Modifier map ───────────────────────────────────────────────────────────────
MODIFIER_KEYS = {
    pynput.keyboard.Key.ctrl_l:  'Ctrl',
    pynput.keyboard.Key.ctrl_r:  'Ctrl',
    pynput.keyboard.Key.alt_l:   'Alt',
    pynput.keyboard.Key.alt_r:   'Alt',
    pynput.keyboard.Key.alt_gr:  'Alt',
    pynput.keyboard.Key.shift:   'Shift',
    pynput.keyboard.Key.shift_l: 'Shift',
    pynput.keyboard.Key.shift_r: 'Shift',
    pynput.keyboard.Key.cmd:     'Win',
    pynput.keyboard.Key.cmd_l:   'Win',
    pynput.keyboard.Key.cmd_r:   'Win',
}

# ── Helpers ────────────────────────────────────────────────────────────────────
def key_label(key):
    try:
        c = key.char
        return c.upper() if c else str(key).replace('Key.', '').title()
    except AttributeError:
        return str(key).replace('Key.', '').title()

def push_log(text):
    recent_logs.append(text)
    if len(recent_logs) > 5:
        recent_logs.pop(0)

# ── pynput callbacks ───────────────────────────────────────────────────────────
_start_pos      = None
_last_pos       = None
_move_timer     = None
_scroll_timer   = None
_held_modifiers = set()
_scroll_dir     = None
_scroll_count   = 0

def _flush_move():
    global _start_pos, _last_pos, mouse_status
    if _start_pos and _last_pos and _start_pos != _last_pos:
        sx, sy = _start_pos
        ex, ey = _last_pos
        mouse_status = f'({sx},{sy})→({ex},{ey})'
        push_log(f'[mouse] ({sx},{sy})→({ex},{ey})')
    _start_pos = None
    _last_pos  = None

def on_move(x, y):
    global _start_pos, _last_pos, _move_timer, mouse_status
    mouse_status = f'({x},{y})'   # compact, no spaces
    if _move_timer:
        _move_timer.cancel()
    if not _start_pos:
        _start_pos = (x, y)
    _last_pos = (x, y)
    _move_timer = threading.Timer(0.4, _flush_move)
    _move_timer.start()

def on_click(x, y, button, pressed):
    btn = button.name
    if pressed:
        mouse_buttons.add(btn)
        names = {'left': 'LMB', 'right': 'RMB', 'middle': 'MMB'}
        push_log(f'[click] {names.get(btn, btn)} at ({x},{y})')
    else:
        mouse_buttons.discard(btn)

def _finalize_scroll():
    global _scroll_dir, _scroll_count, scroll_flash
    scroll_flash  = None
    _scroll_dir   = None
    _scroll_count = 0

def on_scroll(x, y, dx, dy):
    global _scroll_dir, _scroll_count, scroll_flash, _scroll_timer
    new_dir = 'down' if dy < 0 else 'up'
    if _scroll_timer:
        _scroll_timer.cancel()
    if _scroll_dir and new_dir != _scroll_dir:
        push_log(f'[scroll] {"↓" if _scroll_dir == "down" else "↑"} ×{_scroll_count}')
        _scroll_count = 0
    _scroll_dir    = new_dir
    _scroll_count += 1
    scroll_flash   = new_dir
    # Update last scroll log in place rather than pushing duplicates
    tag = f'[scroll] {"↓" if new_dir == "down" else "↑"} ×{_scroll_count}'
    if recent_logs and recent_logs[-1].startswith('[scroll]'):
        recent_logs[-1] = tag
    else:
        push_log(tag)
    _scroll_timer = threading.Timer(0.8, _finalize_scroll)
    _scroll_timer.start()

def _promote_to_held(key):
    held_keys_ui.add(key)

def on_press(key):
    pressed_keys.add(key)
    if key in MODIFIER_KEYS:
        _held_modifiers.add(MODIFIER_KEYS[key])
    if key not in held_debounce:
        t = threading.Timer(HOLD_DEBOUNCE, _promote_to_held, args=(key,))
        held_debounce[key] = t
        t.start()
    active_mods = _held_modifiers - {'Shift'}
    label = key_label(key)
    if key not in MODIFIER_KEYS:
        if active_mods:
            parts = sorted(active_mods, key=lambda m: ['Ctrl','Alt','Win'].index(m) if m in ['Ctrl','Alt','Win'] else 99)
            push_log(f'[cmd]  {"+".join(parts)}+{label}')
        else:
            push_log(f'[key]  {label}')

def on_release(key):
    pressed_keys.discard(key)
    held_keys_ui.discard(key)
    if key in MODIFIER_KEYS:
        _held_modifiers.discard(MODIFIER_KEYS[key])
    t = held_debounce.pop(key, None)
    if t:
        t.cancel()

# ── Key matching ───────────────────────────────────────────────────────────────
def _key_matches(key, ref):
    if ref is None:
        return False
    if key == ref:
        return True
    if isinstance(ref, str) and len(ref) == 1:
        try:
            return key.char and key.char.lower() == ref.lower()
        except AttributeError:
            return False
    if isinstance(ref, pynput.keyboard.KeyCode):
        try:
            return key.char == ref.char
        except AttributeError:
            return False
    if isinstance(ref, pynput.keyboard.Key):
        aliases = {
            pynput.keyboard.Key.shift_l:  {pynput.keyboard.Key.shift, pynput.keyboard.Key.shift_l, pynput.keyboard.Key.shift_r},
            pynput.keyboard.Key.shift_r:  {pynput.keyboard.Key.shift, pynput.keyboard.Key.shift_l, pynput.keyboard.Key.shift_r},
            pynput.keyboard.Key.ctrl_l:   {pynput.keyboard.Key.ctrl_l, pynput.keyboard.Key.ctrl_r},
            pynput.keyboard.Key.ctrl_r:   {pynput.keyboard.Key.ctrl_l, pynput.keyboard.Key.ctrl_r},
            pynput.keyboard.Key.alt_l:    {pynput.keyboard.Key.alt_l, pynput.keyboard.Key.alt_r, pynput.keyboard.Key.alt_gr},
            pynput.keyboard.Key.alt_r:    {pynput.keyboard.Key.alt_l, pynput.keyboard.Key.alt_r, pynput.keyboard.Key.alt_gr},
            pynput.keyboard.Key.cmd:      {pynput.keyboard.Key.cmd, pynput.keyboard.Key.cmd_l, pynput.keyboard.Key.cmd_r},
            pynput.keyboard.Key.cmd_l:    {pynput.keyboard.Key.cmd, pynput.keyboard.Key.cmd_l, pynput.keyboard.Key.cmd_r},
            pynput.keyboard.Key.cmd_r:    {pynput.keyboard.Key.cmd, pynput.keyboard.Key.cmd_l, pynput.keyboard.Key.cmd_r},
        }
        return key in aliases.get(ref, {ref})
    return False

def _any_pressed(ref):
    return any(_key_matches(k, ref) for k in pressed_keys)

def _any_held(ref):
    return any(_key_matches(k, ref) for k in held_keys_ui)

# ── Keyboard layout ────────────────────────────────────────────────────────────
ROWS = [
    # Row 0 — Function row
    [
        ('Esc',   pynput.keyboard.Key.esc,          1.0),
        ('',      None,                              0.5),
        ('F1',    pynput.keyboard.Key.f1,            1.0),
        ('F2',    pynput.keyboard.Key.f2,            1.0),
        ('F3',    pynput.keyboard.Key.f3,            1.0),
        ('F4',    pynput.keyboard.Key.f4,            1.0),
        ('',      None,                              0.5),
        ('F5',    pynput.keyboard.Key.f5,            1.0),
        ('F6',    pynput.keyboard.Key.f6,            1.0),
        ('F7',    pynput.keyboard.Key.f7,            1.0),
        ('F8',    pynput.keyboard.Key.f8,            1.0),
        ('',      None,                              0.5),
        ('F9',    pynput.keyboard.Key.f9,            1.0),
        ('F10',   pynput.keyboard.Key.f10,           1.0),
        ('F11',   pynput.keyboard.Key.f11,           1.0),
        ('F12',   pynput.keyboard.Key.f12,           1.0),
        ('',      None,                              0.5),
        ('PrtSc', pynput.keyboard.Key.print_screen,  1.0),
        ('ScrLk', pynput.keyboard.Key.scroll_lock,   1.0),
        ('Pause', pynput.keyboard.Key.pause,         1.0),
    ],
    # Row 1 — Number row
    [
        ('`','`',1.0),('1','1',1.0),('2','2',1.0),('3','3',1.0),
        ('4','4',1.0),('5','5',1.0),('6','6',1.0),('7','7',1.0),
        ('8','8',1.0),('9','9',1.0),('0','0',1.0),('-','-',1.0),
        ('=','=',1.0),('Bksp', pynput.keyboard.Key.backspace, 2.0),
        ('',None,0.5),
        ('Ins',  pynput.keyboard.Key.insert,   1.0),
        ('Home', pynput.keyboard.Key.home,     1.0),
        ('PgUp', pynput.keyboard.Key.page_up,  1.0),
        ('',None,0.5),
        ('Num',  pynput.keyboard.Key.num_lock, 1.0),
        ('/',    pynput.keyboard.KeyCode.from_char('/'), 1.0),
        ('*',    pynput.keyboard.KeyCode.from_char('*'), 1.0),
        ('-',    pynput.keyboard.KeyCode.from_char('-'), 1.0),
    ],
    # Row 2 — QWERTY
    [
        ('Tab', pynput.keyboard.Key.tab, 1.5),
        ('Q','q',1.0),('W','w',1.0),('E','e',1.0),('R','r',1.0),('T','t',1.0),
        ('Y','y',1.0),('U','u',1.0),('I','i',1.0),('O','o',1.0),('P','p',1.0),
        ('[','[',1.0),(']',']',1.0),('\\','\\',1.5),
        ('',None,0.5),
        ('Del',  pynput.keyboard.Key.delete,    1.0),
        ('End',  pynput.keyboard.Key.end,       1.0),
        ('PgDn', pynput.keyboard.Key.page_down, 1.0),
        ('',None,0.5),
        ('7', pynput.keyboard.KeyCode.from_char('7'), 1.0),
        ('8', pynput.keyboard.KeyCode.from_char('8'), 1.0),
        ('9', pynput.keyboard.KeyCode.from_char('9'), 1.0),
        ('+', pynput.keyboard.KeyCode.from_char('+'), 1.0),
    ],
    # Row 3 — Home row
    [
        ('Caps', pynput.keyboard.Key.caps_lock, 1.75),
        ('A','a',1.0),('S','s',1.0),('D','d',1.0),('F','f',1.0),('G','g',1.0),
        ('H','h',1.0),('J','j',1.0),('K','k',1.0),('L','l',1.0),(';',';',1.0),
        ("'","'",1.0),
        ('Enter', pynput.keyboard.Key.enter, 2.25),
        ('',None,0.5),('',None,1.0),('',None,1.0),('',None,1.0),('',None,0.5),
        ('4', pynput.keyboard.KeyCode.from_char('4'), 1.0),
        ('5', pynput.keyboard.KeyCode.from_char('5'), 1.0),
        ('6', pynput.keyboard.KeyCode.from_char('6'), 1.0),
        ('',None,1.0),
    ],
    # Row 4 — Shift row
    [
        ('Shift', pynput.keyboard.Key.shift_l, 2.25),
        ('Z','z',1.0),('X','x',1.0),('C','c',1.0),('V','v',1.0),('B','b',1.0),
        ('N','n',1.0),('M','m',1.0),(',',',',1.0),('.','.', 1.0),('/','/',1.0),
        ('Shift', pynput.keyboard.Key.shift_r, 2.75),
        ('',None,0.5),
        ('↑', pynput.keyboard.Key.up,    1.0),
        ('',None,1.0),('',None,0.5),
        ('1', pynput.keyboard.KeyCode.from_char('1'), 1.0),
        ('2', pynput.keyboard.KeyCode.from_char('2'), 1.0),
        ('3', pynput.keyboard.KeyCode.from_char('3'), 1.0),
        ('↵',  pynput.keyboard.Key.enter, 1.0),
    ],
    # Row 5 — Bottom row
    [
        ('Ctrl',  pynput.keyboard.Key.ctrl_l, 1.25),
        ('Win',   pynput.keyboard.Key.cmd,    1.25),
        ('Alt',   pynput.keyboard.Key.alt_l,  1.25),
        ('Space', pynput.keyboard.Key.space,  6.25),
        ('Alt',   pynput.keyboard.Key.alt_r,  1.25),
        ('Win',   pynput.keyboard.Key.cmd_r,  1.25),
        ('Menu',  pynput.keyboard.Key.menu,   1.25),
        ('Ctrl',  pynput.keyboard.Key.ctrl_r, 1.25),
        ('',None,0.5),
        ('←', pynput.keyboard.Key.left,  1.0),
        ('↓', pynput.keyboard.Key.down,  1.0),
        ('→', pynput.keyboard.Key.right, 1.0),
        ('',None,0.5),
        ('0', pynput.keyboard.KeyCode.from_char('0'), 2.0),
        ('.', pynput.keyboard.KeyCode.from_char('.'), 1.0),
        ('',None,1.0),
    ],
]

# ── GUI ────────────────────────────────────────────────────────────────────────
class VisualInterface:
    def __init__(self, root):
        self.root = root
        self.root.title(f'Input Visualiser v{VERSION}')

        # ── Transparent window setup (Windows)
        self.root.overrideredirect(True)         # no title bar / border
        self.root.attributes('-topmost', True)
        self.root.attributes('-transparentcolor', MAGIC_BG)
        self.root.configure(bg=MAGIC_BG)

        # Allow dragging the window by clicking on keyboard area
        self._drag_x = 0
        self._drag_y = 0

        self._kb_canvas_items = []
        self._mouse_widgets   = {}
        self._log_labels      = []
        self._pos_label       = None

        self._build()
        self._tick()

    # ── Drag support ───────────────────────────────────────────────────────────
    def _drag_start(self, e):
        self._drag_x = e.x_root - self.root.winfo_x()
        self._drag_y = e.y_root - self.root.winfo_y()

    def _drag_move(self, e):
        self.root.geometry(f'+{e.x_root - self._drag_x}+{e.y_root - self._drag_y}')

    # ── Build ──────────────────────────────────────────────────────────────────
    def _build(self):
        pad = 14

        # Outer frame (transparent bg)
        outer = tk.Frame(self.root, bg=MAGIC_BG)
        outer.pack(padx=pad, pady=pad)

        # Version label (dim, top-left) — also drag handle
        ver = tk.Label(outer, text=f'Input Visualiser  v{VERSION}  [drag to move]',
                       bg=MAGIC_BG, fg='#333333', font=('Consolas', 8))
        ver.pack(anchor='w', pady=(0, 4))
        ver.bind('<ButtonPress-1>',   self._drag_start)
        ver.bind('<B1-Motion>',       self._drag_move)

        # Keyboard
        kb_frame = tk.Frame(outer, bg=MAGIC_BG)
        kb_frame.pack()
        self._build_keyboard(kb_frame)

        # Bottom panel
        bot = tk.Frame(outer, bg=MAGIC_BG)
        bot.pack(pady=(10, 0), fill='x')
        self._build_mouse(bot)
        self._build_pos(bot)
        self._build_log(bot)

    def _build_keyboard(self, parent):
        ROW_GAP = [10, 6, 4, 4, 4, 4]

        max_row_w = max(
            sum(span * (U + G) for _, _, span in row)
            for row in ROWS
        )
        total_h = len(ROWS) * (U + G) + sum(ROW_GAP) + 6

        c = tk.Canvas(parent, bg=MAGIC_BG, highlightthickness=0,
                      width=int(max_row_w) + 6, height=int(total_h))
        c.pack()
        c.bind('<ButtonPress-1>',   self._drag_start)
        c.bind('<B1-Motion>',       self._drag_move)
        self._kb_canvas = c

        row_y = 2
        for ri, row in enumerate(ROWS):
            row_y += ROW_GAP[ri]
            row_x  = 2
            for label, ref, span in row:
                w = int(span * (U + G)) - G
                if not label or ref is None:
                    row_x += int(span * (U + G))
                    continue
                r_id = c.create_rectangle(
                    row_x, row_y, row_x + w, row_y + U,
                    fill=KEY_IDLE, outline=KEY_BORDER, width=1
                )
                fs   = FONT_KEY_SM if len(label) > 4 else FONT_KEY
                t_id = c.create_text(
                    row_x + w // 2, row_y + U // 2,
                    text=label, fill=KEY_TEXT, font=fs, anchor='center'
                )
                self._kb_canvas_items.append((r_id, t_id, ref))
                row_x += int(span * (U + G))
            row_y += U + G

    def _build_mouse(self, parent):
        f = tk.Frame(parent, bg=MAGIC_BG)
        f.pack(side='left', anchor='n', padx=(0, 20))

        tk.Label(f, text='MOUSE', bg=MAGIC_BG, fg=SECTION_LBL,
                 font=FONT_LABEL).pack(anchor='w')

        bw, bh    = 36, 70
        scr_w, scr_h = 14, 38
        total_w   = bw * 2 + scr_w + 6
        c_w, c_h  = total_w + 20, bh + 80
        ox        = 10
        oy        = 6

        c = tk.Canvas(f, bg=MAGIC_BG, highlightthickness=0, width=c_w, height=c_h)
        c.pack()

        # Left button
        lb = c.create_rectangle(ox, oy, ox + bw, oy + bh,
                                 fill=MOUSE_IDLE, outline=KEY_BORDER, width=1)
        # Scroll wheel
        sx = ox + bw + 3
        sy = oy + (bh - scr_h) // 2
        mb = c.create_rectangle(sx, sy, sx + scr_w, sy + scr_h,
                                 fill=MOUSE_SCR, outline=KEY_BORDER, width=1)
        sa = c.create_text(sx + scr_w // 2, oy + bh // 2,
                            text='●', fill='#333333', font=('Consolas', 9))
        # Right button
        rx = sx + scr_w + 3
        rb = c.create_rectangle(rx, oy, rx + bw, oy + bh,
                                 fill=MOUSE_IDLE, outline=KEY_BORDER, width=1)
        # Body
        by = oy + bh + 2
        c.create_rectangle(ox, by, ox + total_w, by + 55,
                            fill='#1a1a1a', outline=KEY_BORDER, width=1)

        self._mouse_widgets = {
            'canvas': c, 'left': lb, 'right': rb, 'middle': mb, 'scroll_arrow': sa,
        }

    def _build_pos(self, parent):
        f = tk.Frame(parent, bg=MAGIC_BG)
        f.pack(side='left', anchor='n', padx=(0, 20))

        tk.Label(f, text='POSITION', bg=MAGIC_BG, fg=SECTION_LBL,
                 font=FONT_LABEL).pack(anchor='w')

        # Fixed width, no wrapping — coords always fit on one line
        self._pos_label = tk.Label(
            f, text='(0,0)', bg=LOG_BG, fg=POS_TEXT,
            font=('Consolas', 9), width=22, anchor='w',
            padx=6, pady=4, justify='left'
        )
        self._pos_label.pack(anchor='w')

    def _build_log(self, parent):
        f = tk.Frame(parent, bg=MAGIC_BG)
        f.pack(side='left', anchor='n', fill='x', expand=True)

        tk.Label(f, text='RECENT EVENTS', bg=MAGIC_BG, fg=SECTION_LBL,
                 font=FONT_LABEL).pack(anchor='w')

        for _ in range(5):
            lbl = tk.Label(f, text='', bg=LOG_BG, fg=LOG_TEXT,
                           font=FONT_UI, anchor='w', padx=8, width=34,
                           relief='flat', pady=2)
            lbl.pack(anchor='w', pady=1)
            self._log_labels.append(lbl)

    # ── Tick ───────────────────────────────────────────────────────────────────
    def _tick(self):
        self._update_keyboard()
        self._update_mouse()
        # Compact single line — trim to fit width=22 chars
        self._pos_label.config(text=mouse_status[:22])
        padded = [''] * (5 - len(recent_logs)) + recent_logs
        for i, lbl in enumerate(self._log_labels):
            lbl.config(text=padded[i],
                       fg=LOG_HI if (i == 4 and padded[i]) else LOG_TEXT)
        self.root.after(30, self._tick)

    def _update_keyboard(self):
        c = self._kb_canvas
        for r_id, t_id, ref in self._kb_canvas_items:
            if _any_held(ref):
                c.itemconfig(r_id, fill=KEY_HOLD, outline=KEY_HOLD)
                c.itemconfig(t_id, fill=KEY_TEXT_HI)
            elif _any_pressed(ref):
                c.itemconfig(r_id, fill=KEY_PRESS, outline=KEY_PRESS)
                c.itemconfig(t_id, fill=KEY_TEXT_HI)
            else:
                c.itemconfig(r_id, fill=KEY_IDLE, outline=KEY_BORDER)
                c.itemconfig(t_id, fill=KEY_TEXT)

    def _update_mouse(self):
        c  = self._mouse_widgets['canvas']
        lb = self._mouse_widgets['left']
        rb = self._mouse_widgets['right']
        mb = self._mouse_widgets['middle']
        sa = self._mouse_widgets['scroll_arrow']
        c.itemconfig(lb, fill=MOUSE_CLICK if 'left'  in mouse_buttons else MOUSE_IDLE)
        c.itemconfig(rb, fill=MOUSE_CLICK if 'right' in mouse_buttons else MOUSE_IDLE)
        if scroll_flash == 'up':
            c.itemconfig(mb, fill=MOUSE_SCR_A)
            c.itemconfig(sa, text='↑', fill=KEY_TEXT_HI)
        elif scroll_flash == 'down':
            c.itemconfig(mb, fill=MOUSE_SCR_A)
            c.itemconfig(sa, text='↓', fill=KEY_TEXT_HI)
        else:
            c.itemconfig(mb, fill=MOUSE_SCR)
            c.itemconfig(sa, text='●', fill='#333333')

# ── Listener threads ───────────────────────────────────────────────────────────
def mouse_thread():
    with pynput.mouse.Listener(on_move=on_move, on_click=on_click, on_scroll=on_scroll) as ml:
        ml.join()

def keyboard_thread():
    with pynput.keyboard.Listener(on_press=on_press, on_release=on_release) as kl:
        kl.join()

# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    threading.Thread(target=mouse_thread,    daemon=True).start()
    threading.Thread(target=keyboard_thread, daemon=True).start()

    root = tk.Tk()
    app  = VisualInterface(root)
    root.mainloop()
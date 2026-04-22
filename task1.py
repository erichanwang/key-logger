"""
task1.py — Parses and executes task1.ehw
Automates MCQ answering via Google Lens using:
  - pynput  : mouse & keyboard control
  - pyperclip: clipboard read
  - re / spacy (or fallback regex): NLP answer extraction
  - cv2 / pyautogui: screen capture + template matching for A/B/C/D circles
  - tkinter: red crosshair overlay (from input_tracker)
"""

import re
import os
import sys
import time
import threading
import tkinter as tk
import ctypes

# ── DPI fix ────────────────────────────────────────────────────────────────────
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import pynput.mouse    as pmouse
import pynput.keyboard as pkeyboard
import pyperclip
import pyautogui
import cv2
import numpy as np

# ── Config ─────────────────────────────────────────────────────────────────────
EHW_FILE      = os.path.join(os.path.dirname(__file__), 'task1.ehw')
LETTERS_DIR   = os.path.join(os.path.dirname(__file__), 'letters')
LETTER_FILES  = {
    'A': os.path.join(LETTERS_DIR, 'A.png'),
    'B': os.path.join(LETTERS_DIR, 'B.png'),
    'C': os.path.join(LETTERS_DIR, 'C.png'),
    'D': os.path.join(LETTERS_DIR, 'D.png'),
}
CLOSE_LENS_OFFSET  = (-1807, 182)
NEXT_Q_OFFSET      = (-221,  2090)
MOVE_DELAY         = 0.3    # seconds between actions
CV2_THRESHOLD      = 0.75   # template match confidence

# ── Overlay (red crosshair) ────────────────────────────────────────────────────
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED     = 0x00080000
WS_EX_NOACTIVATE  = 0x08000000
WS_EX_TOOLWINDOW  = 0x00000080
GWL_EXSTYLE       = -20

SIZE = 41
HALF = SIZE // 2

_overlay_root = None
_overlay_canvas = None

def _make_click_through(root):
    hwnd = ctypes.windll.user32.FindWindowW(None, root.title())
    if not hwnd:
        hwnd = root.winfo_id()
    style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    style |= WS_EX_TRANSPARENT | WS_EX_LAYERED | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
    ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)

def _update_crosshair():
    if not _overlay_canvas:
        return
    _overlay_canvas.delete('x')
    cx, cy = HALF, HALF
    r = HALF - 2
    _overlay_canvas.create_line(cx-r, cy, cx+r, cy, fill='red', width=2, tags='x')
    _overlay_canvas.create_line(cx, cy-r, cx, cy+r, fill='red', width=2, tags='x')

def _overlay_follow():
    """Poll mouse position and move overlay window to match."""
    mc = pmouse.Controller()
    while True:
        x, y = mc.position
        if _overlay_root:
            _overlay_root.geometry(f'{SIZE}x{SIZE}+{x-HALF}+{y-HALF}')
        time.sleep(0.016)  # ~60fps

def _run_overlay():
    global _overlay_root, _overlay_canvas
    _overlay_root = tk.Tk()
    _overlay_root.title('__crosshair__')
    _overlay_root.geometry(f'{SIZE}x{SIZE}+0+0')
    _overlay_root.overrideredirect(True)
    _overlay_root.attributes('-topmost', True)
    _overlay_root.attributes('-transparentcolor', 'black')
    _overlay_root.configure(bg='black')
    _overlay_canvas = tk.Canvas(_overlay_root, width=SIZE, height=SIZE,
                                bg='black', highlightthickness=0)
    _overlay_canvas.pack()
    _update_crosshair()
    _overlay_root.after(100, _make_click_through, _overlay_root)
    _overlay_root.mainloop()

# ── Mouse & keyboard controllers ───────────────────────────────────────────────
_mouse    = pmouse.Controller()
_keyboard = pkeyboard.Controller()

def _current_pos():
    return _mouse.position

def _move_mouse(dx, dy):
    x, y = _current_pos()
    _mouse.position = (x + dx, y + dy)
    time.sleep(MOVE_DELAY)

def _move_mouse_abs(x, y):
    _mouse.position = (x, y)
    time.sleep(MOVE_DELAY)

def _left_click():
    _mouse.click(pmouse.Button.left)
    time.sleep(0.1)

def _right_click():
    _mouse.click(pmouse.Button.right)
    time.sleep(0.2)

def _key(k):
    _keyboard.press(k)
    _keyboard.release(k)
    time.sleep(0.1)

def _type(text):
    _keyboard.type(text)
    time.sleep(0.15)

def _select_all():
    with _keyboard.pressed(pkeyboard.Key.ctrl):
        _key('a')
    time.sleep(0.1)

def _copy():
    with _keyboard.pressed(pkeyboard.Key.ctrl):
        _key('c')
    time.sleep(0.3)

def _read_clipboard():
    return pyperclip.paste()

# ── NLP: extract answer from clipboard text ────────────────────────────────────
_ANSWER_PATTERN = re.compile(
    r'[Tt]he correct answer is\s*[\[\(]?([A-Da-d])[.\)\]]?',
    re.IGNORECASE
)

def _nlp_extract_answer(text: str) -> str | None:
    """
    Look for 'The correct answer is [X]' in the pasted text.
    Returns the letter (A/B/C/D) or None if not found.
    Falls back to broader regex patterns if the strict one fails.
    """
    # Primary pattern: strict format
    m = _ANSWER_PATTERN.search(text)
    if m:
        return m.group(1).upper()

    # Fallback 1: 'answer is X' anywhere
    m2 = re.search(r'answer\s+is\s+([A-Da-d])\b', text, re.IGNORECASE)
    if m2:
        return m2.group(1).upper()

    # Fallback 2: just a lone letter in brackets/parens
    m3 = re.search(r'[\[\(]([A-Da-d])[\]\)]', text)
    if m3:
        return m3.group(1).upper()

    print(f'[NLP] Could not extract answer from text:\n{text[:300]}')
    return None

# ── CV2: find answer option on screen ─────────────────────────────────────────
def _locate_on_screen(letter: str) -> tuple[int, int] | None:
    """
    Screenshot the screen, template-match against letters/X.png.
    Returns (screen_x, screen_y) of the best match centre, or None.
    """
    template_path = LETTER_FILES.get(letter.upper())
    if not template_path or not os.path.exists(template_path):
        print(f'[CV2] Template not found for letter {letter}: {template_path}')
        return None

    template = cv2.imread(template_path, cv2.IMREAD_UNCHANGED)
    if template is None:
        print(f'[CV2] Failed to load template: {template_path}')
        return None

    # Capture full screen
    screenshot = pyautogui.screenshot()
    screen_np  = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)

    # If template has alpha channel, use it as mask
    if template.shape[2] == 4:
        mask     = template[:, :, 3]
        template = template[:, :, :3]
        result   = cv2.matchTemplate(screen_np, template, cv2.TM_CCORR_NORMED, mask=mask)
    else:
        result = cv2.matchTemplate(screen_np, template, cv2.TM_CCOEFF_NORMED)

    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    print(f'[CV2] Match for {letter}: confidence={max_val:.3f} at {max_loc}')

    if max_val >= CV2_THRESHOLD:
        th, tw = template.shape[:2]
        cx = max_loc[0] + tw // 2
        cy = max_loc[1] + th // 2
        return (cx, cy)

    print(f'[CV2] Confidence {max_val:.3f} below threshold {CV2_THRESHOLD} — no match')
    return None

# ── High-level actions ─────────────────────────────────────────────────────────
def _close_google_lens():
    print('[action] close_google_lens')
    dx, dy = CLOSE_LENS_OFFSET
    _move_mouse(dx, dy)
    _left_click()
    time.sleep(0.5)

def _next_question():
    print('[action] next_question')
    dx, dy = NEXT_Q_OFFSET
    _move_mouse(dx, dy)
    _left_click()
    time.sleep(0.5)

def _locate_answer(letter: str) -> tuple[int, int] | None:
    print(f'[action] locate_answer({letter})')
    pos = _locate_on_screen(letter)
    if pos:
        print(f'[CV2]   Found {letter} at screen position {pos}')
    else:
        print(f'[CV2]   Could not find {letter} on screen')
    return pos

def _click_answer(pos: tuple[int, int] | None):
    if pos is None:
        print('[action] click_answer — no position, skipping')
        return
    print(f'[action] click_answer at {pos}')
    _mouse.position = pos
    time.sleep(MOVE_DELAY)
    _left_click()

# ── .ehw parser & executor ─────────────────────────────────────────────────────
class EHWRunner:
    """
    Parses a .ehw file line by line and executes each instruction.

    Supported instructions:
        move_mouse(dx, dy)          — relative move
        move_mouse(x, y)            — absolute if called with abs=True variant
        right_click()
        left_click()
        key("x")
        type("text")
        select_all()
        copy()
        read_from_clipboard()
        correct_answer = nlp_extract_answer()
        close_google_lens()
        locate_answer(correct_answer)
        click_answer(correct_answer)
        next_question()
        # anything after # is a comment, ignored
    """

    def __init__(self, path: str):
        self.path           = path
        self.clipboard_text = ''
        self.correct_answer = None
        self.answer_pos     = None
        self._vars          = {}   # simple variable store

    def run(self):
        with open(self.path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        for lineno, raw in enumerate(lines, 1):
            line = raw.strip()
            if not line or line.startswith('#'):
                continue
            try:
                self._exec_line(line)
            except Exception as e:
                print(f'[EHW] Error on line {lineno}: {line!r}  →  {e}')

    # ── Dispatch ───────────────────────────────────────────────────────────────
    def _exec_line(self, line: str):
        # Assignment: var = func()
        assign_m = re.match(r'^(\w+)\s*=\s*(.+)$', line)
        if assign_m:
            varname = assign_m.group(1)
            expr    = assign_m.group(2).strip()
            result  = self._eval_expr(expr)
            self._vars[varname] = result
            print(f'[EHW] {varname} = {result!r}')
            return

        # Plain call
        self._eval_expr(line)

    def _eval_expr(self, expr: str):
        # move_mouse(dx, dy)
        m = re.match(r'^move_mouse\((-?\d+),\s*(-?\d+)\)$', expr)
        if m:
            dx, dy = int(m.group(1)), int(m.group(2))
            print(f'[EHW] move_mouse({dx}, {dy})')
            _move_mouse(dx, dy)
            return None

        # right_click()
        if re.match(r'^right_click\(\)$', expr):
            print('[EHW] right_click()')
            _right_click()
            return None

        # left_click()
        if re.match(r'^left_click\(\)$', expr):
            print('[EHW] left_click()')
            _left_click()
            return None

        # key("x")
        m = re.match(r'^key\("(.+?)"\)$', expr)
        if m:
            k = m.group(1)
            print(f'[EHW] key({k!r})')
            _key(k)
            return None

        # type("text")
        m = re.match(r'^type\("(.+?)"\)$', expr)
        if m:
            text = m.group(1)
            print(f'[EHW] type({text[:40]!r}...)')
            _type(text)
            return None

        # select_all()
        if re.match(r'^select_all\(\)$', expr):
            print('[EHW] select_all()')
            _select_all()
            return None

        # copy()
        if re.match(r'^copy\(\)$', expr):
            print('[EHW] copy()')
            _copy()
            return None
        
        # pause()
        if re.match(r'^pause\(\)$', expr):
            print('[EHW] pause()')
            time.sleep(5.0)

        # read_from_clipboard()
        if re.match(r'^read_from_clipboard\(\)$', expr):
            print('[EHW] read_from_clipboard()')
            self.clipboard_text = _read_clipboard()
            print(f'[EHW] clipboard ({len(self.clipboard_text)} chars): {self.clipboard_text[:80]!r}...')
            return self.clipboard_text

        # nlp_extract_answer()
        if re.match(r'^nlp_extract_answer\(\)$', expr):
            print('[EHW] nlp_extract_answer()')
            answer = _nlp_extract_answer(self.clipboard_text)
            self.correct_answer = answer
            self._vars['correct_answer'] = answer
            return answer

        # close_google_lens()
        if re.match(r'^close_google_lens\(\)$', expr):
            _close_google_lens()
            return None

        # locate_answer(correct_answer) or locate_answer("A")
        m = re.match(r'^locate_answer\((.+?)\)$', expr)
        if m:
            arg = m.group(1).strip().strip('"\'')
            letter = self._vars.get(arg, arg)
            if letter:
                self.answer_pos = _locate_answer(str(letter))
            return self.answer_pos

        # click_answer(correct_answer)
        m = re.match(r'^click_answer\((.+?)\)$', expr)
        if m:
            _click_answer(self.answer_pos)
            return None

        # next_question()
        if re.match(r'^next_question\(\)$', expr):
            _next_question()
            return None

        # key("page_down") shorthand without quotes sometimes
        m = re.match(r'^key\((\w+)\)$', expr)
        if m:
            k = m.group(1)
            print(f'[EHW] key({k})')
            special = getattr(pkeyboard.Key, k, None)
            if special:
                _keyboard.press(special)
                _keyboard.release(special)
            else:
                _key(k)
            return None

        print(f'[EHW] Unknown expression (skipped): {expr!r}')
        return None

# ── Visual interface ───────────────────────────────────────────────────────────
# Reuse the same shared-state approach as visual_interface.py but embedded here

pressed_keys  = set()
held_keys_ui  = set()
mouse_buttons = set()
scroll_flash  = None
recent_logs   = []
mouse_status  = 'idle'
held_debounce_vi = {}
HOLD_DEBOUNCE_VI = 0.3
_vi_held_mods = set()

VI_MODIFIER_KEYS = {
    pkeyboard.Key.ctrl_l:  'Ctrl',  pkeyboard.Key.ctrl_r:  'Ctrl',
    pkeyboard.Key.alt_l:   'Alt',   pkeyboard.Key.alt_r:   'Alt',
    pkeyboard.Key.alt_gr:  'Alt',
    pkeyboard.Key.shift:   'Shift', pkeyboard.Key.shift_l: 'Shift',
    pkeyboard.Key.shift_r: 'Shift',
    pkeyboard.Key.cmd:     'Win',   pkeyboard.Key.cmd_l:   'Win',
    pkeyboard.Key.cmd_r:   'Win',
}

def _vi_key_label(key):
    try:
        c = key.char
        return c.upper() if c else str(key).replace('Key.', '').title()
    except AttributeError:
        return str(key).replace('Key.', '').title()

def _vi_push_log(text):
    recent_logs.append(text)
    if len(recent_logs) > 5:
        recent_logs.pop(0)

_vi_start_pos = None
_vi_last_pos  = None
_vi_move_timer = None
_vi_scroll_dir = None
_vi_scroll_count = 0
_vi_scroll_timer = None

def _vi_flush_move():
    global _vi_start_pos, _vi_last_pos, mouse_status
    if _vi_start_pos and _vi_last_pos and _vi_start_pos != _vi_last_pos:
        sx, sy = _vi_start_pos
        ex, ey = _vi_last_pos
        mouse_status = f'({sx},{sy})→({ex},{ey})'
        _vi_push_log(f'[mouse] ({sx},{sy})→({ex},{ey})')
    _vi_start_pos = None
    _vi_last_pos  = None

def _vi_on_move(x, y):
    global _vi_start_pos, _vi_last_pos, _vi_move_timer, mouse_status
    mouse_status = f'({x},{y})'
    if _vi_move_timer:
        _vi_move_timer.cancel()
    if not _vi_start_pos:
        _vi_start_pos = (x, y)
    _vi_last_pos = (x, y)
    _vi_move_timer = threading.Timer(0.4, _vi_flush_move)
    _vi_move_timer.start()

def _vi_on_click(x, y, button, pressed):
    btn = button.name
    if pressed:
        mouse_buttons.add(btn)
        names = {'left': 'LMB', 'right': 'RMB', 'middle': 'MMB'}
        _vi_push_log(f'[click] {names.get(btn, btn)} ({x},{y})')
    else:
        mouse_buttons.discard(btn)

def _vi_finalize_scroll():
    global _vi_scroll_dir, _vi_scroll_count, scroll_flash
    scroll_flash      = None
    _vi_scroll_dir    = None
    _vi_scroll_count  = 0

def _vi_on_scroll(x, y, dx, dy):
    global _vi_scroll_dir, _vi_scroll_count, scroll_flash, _vi_scroll_timer
    new_dir = 'down' if dy < 0 else 'up'
    if _vi_scroll_timer:
        _vi_scroll_timer.cancel()
    if _vi_scroll_dir and new_dir != _vi_scroll_dir:
        _vi_scroll_count = 0
    _vi_scroll_dir    = new_dir
    _vi_scroll_count += 1
    scroll_flash = new_dir
    tag = f'[scroll] {"↓" if new_dir=="down" else "↑"} ×{_vi_scroll_count}'
    if recent_logs and recent_logs[-1].startswith('[scroll]'):
        recent_logs[-1] = tag
    else:
        _vi_push_log(tag)
    _vi_scroll_timer = threading.Timer(0.8, _vi_finalize_scroll)
    _vi_scroll_timer.start()

def _vi_promote_held(key):
    held_keys_ui.add(key)

def _vi_on_press(key):
    pressed_keys.add(key)
    if key in VI_MODIFIER_KEYS:
        _vi_held_mods.add(VI_MODIFIER_KEYS[key])
    if key not in held_debounce_vi:
        t = threading.Timer(HOLD_DEBOUNCE_VI, _vi_promote_held, args=(key,))
        held_debounce_vi[key] = t
        t.start()
    active_mods = _vi_held_mods - {'Shift'}
    label = _vi_key_label(key)
    if key not in VI_MODIFIER_KEYS:
        if active_mods:
            parts = sorted(active_mods, key=lambda m: ['Ctrl','Alt','Win'].index(m) if m in ['Ctrl','Alt','Win'] else 99)
            _vi_push_log(f'[cmd] {"+".join(parts)}+{label}')
        else:
            _vi_push_log(f'[key] {label}')

def _vi_on_release(key):
    pressed_keys.discard(key)
    held_keys_ui.discard(key)
    if key in VI_MODIFIER_KEYS:
        _vi_held_mods.discard(VI_MODIFIER_KEYS[key])
    t = held_debounce_vi.pop(key, None)
    if t:
        t.cancel()

def _vi_key_matches(key, ref):
    if ref is None: return False
    if key == ref:  return True
    if isinstance(ref, str) and len(ref) == 1:
        try: return key.char and key.char.lower() == ref.lower()
        except AttributeError: return False
    if isinstance(ref, pkeyboard.KeyCode):
        try: return key.char == ref.char
        except AttributeError: return False
    if isinstance(ref, pkeyboard.Key):
        aliases = {
            pkeyboard.Key.shift_l: {pkeyboard.Key.shift, pkeyboard.Key.shift_l, pkeyboard.Key.shift_r},
            pkeyboard.Key.shift_r: {pkeyboard.Key.shift, pkeyboard.Key.shift_l, pkeyboard.Key.shift_r},
            pkeyboard.Key.ctrl_l:  {pkeyboard.Key.ctrl_l, pkeyboard.Key.ctrl_r},
            pkeyboard.Key.ctrl_r:  {pkeyboard.Key.ctrl_l, pkeyboard.Key.ctrl_r},
            pkeyboard.Key.alt_l:   {pkeyboard.Key.alt_l, pkeyboard.Key.alt_r, pkeyboard.Key.alt_gr},
            pkeyboard.Key.alt_r:   {pkeyboard.Key.alt_l, pkeyboard.Key.alt_r, pkeyboard.Key.alt_gr},
            pkeyboard.Key.cmd:     {pkeyboard.Key.cmd, pkeyboard.Key.cmd_l, pkeyboard.Key.cmd_r},
            pkeyboard.Key.cmd_l:   {pkeyboard.Key.cmd, pkeyboard.Key.cmd_l, pkeyboard.Key.cmd_r},
            pkeyboard.Key.cmd_r:   {pkeyboard.Key.cmd, pkeyboard.Key.cmd_l, pkeyboard.Key.cmd_r},
        }
        return key in aliases.get(ref, {ref})
    return False

def _vi_any_pressed(ref): return any(_vi_key_matches(k, ref) for k in pressed_keys)
def _vi_any_held(ref):    return any(_vi_key_matches(k, ref) for k in held_keys_ui)

# ── Keyboard layout (same as visual_interface.py) ─────────────────────────────
U  = 38
G  = 3
MAGIC = '#010101'
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
SL          = '#555555'

ROWS = [
    [('Esc', pkeyboard.Key.esc, 1.0), ('', None, 0.5),
     ('F1', pkeyboard.Key.f1, 1.0), ('F2', pkeyboard.Key.f2, 1.0),
     ('F3', pkeyboard.Key.f3, 1.0), ('F4', pkeyboard.Key.f4, 1.0), ('', None, 0.5),
     ('F5', pkeyboard.Key.f5, 1.0), ('F6', pkeyboard.Key.f6, 1.0),
     ('F7', pkeyboard.Key.f7, 1.0), ('F8', pkeyboard.Key.f8, 1.0), ('', None, 0.5),
     ('F9', pkeyboard.Key.f9, 1.0), ('F10', pkeyboard.Key.f10, 1.0),
     ('F11', pkeyboard.Key.f11, 1.0), ('F12', pkeyboard.Key.f12, 1.0), ('', None, 0.5),
     ('Prt', pkeyboard.Key.print_screen, 1.0),
     ('Scr', pkeyboard.Key.scroll_lock, 1.0),
     ('Pau', pkeyboard.Key.pause, 1.0)],
    [('`','`',1.0),('1','1',1.0),('2','2',1.0),('3','3',1.0),('4','4',1.0),
     ('5','5',1.0),('6','6',1.0),('7','7',1.0),('8','8',1.0),('9','9',1.0),
     ('0','0',1.0),('-','-',1.0),('=','=',1.0),('Bksp',pkeyboard.Key.backspace,2.0),
     ('',None,0.5),('Ins',pkeyboard.Key.insert,1.0),('Hm',pkeyboard.Key.home,1.0),
     ('PU',pkeyboard.Key.page_up,1.0),('',None,0.5),
     ('Num',pkeyboard.Key.num_lock,1.0),('/',pkeyboard.KeyCode.from_char('/'),1.0),
     ('*',pkeyboard.KeyCode.from_char('*'),1.0),('-',pkeyboard.KeyCode.from_char('-'),1.0)],
    [('Tab',pkeyboard.Key.tab,1.5),
     ('Q','q',1.0),('W','w',1.0),('E','e',1.0),('R','r',1.0),('T','t',1.0),
     ('Y','y',1.0),('U','u',1.0),('I','i',1.0),('O','o',1.0),('P','p',1.0),
     ('[','[',1.0),(']',']',1.0),('\\','\\',1.5),('',None,0.5),
     ('Del',pkeyboard.Key.delete,1.0),('End',pkeyboard.Key.end,1.0),
     ('PD',pkeyboard.Key.page_down,1.0),('',None,0.5),
     ('7',pkeyboard.KeyCode.from_char('7'),1.0),
     ('8',pkeyboard.KeyCode.from_char('8'),1.0),
     ('9',pkeyboard.KeyCode.from_char('9'),1.0),
     ('+',pkeyboard.KeyCode.from_char('+'),1.0)],
    [('Caps',pkeyboard.Key.caps_lock,1.75),
     ('A','a',1.0),('S','s',1.0),('D','d',1.0),('F','f',1.0),('G','g',1.0),
     ('H','h',1.0),('J','j',1.0),('K','k',1.0),('L','l',1.0),(';',';',1.0),
     ("'","'",1.0),('Enter',pkeyboard.Key.enter,2.25),
     ('',None,0.5),('',None,1.0),('',None,1.0),('',None,1.0),('',None,0.5),
     ('4',pkeyboard.KeyCode.from_char('4'),1.0),
     ('5',pkeyboard.KeyCode.from_char('5'),1.0),
     ('6',pkeyboard.KeyCode.from_char('6'),1.0),('',None,1.0)],
    [('Shift',pkeyboard.Key.shift_l,2.25),
     ('Z','z',1.0),('X','x',1.0),('C','c',1.0),('V','v',1.0),('B','b',1.0),
     ('N','n',1.0),('M','m',1.0),(',',',',1.0),('.','.', 1.0),('/','/',1.0),
     ('Shift',pkeyboard.Key.shift_r,2.75),('',None,0.5),
     ('↑',pkeyboard.Key.up,1.0),('',None,1.0),('',None,0.5),
     ('1',pkeyboard.KeyCode.from_char('1'),1.0),
     ('2',pkeyboard.KeyCode.from_char('2'),1.0),
     ('3',pkeyboard.KeyCode.from_char('3'),1.0),
     ('↵',pkeyboard.Key.enter,1.0)],
    [('Ctrl',pkeyboard.Key.ctrl_l,1.25),('Win',pkeyboard.Key.cmd,1.25),
     ('Alt',pkeyboard.Key.alt_l,1.25),('Space',pkeyboard.Key.space,6.25),
     ('Alt',pkeyboard.Key.alt_r,1.25),('Win',pkeyboard.Key.cmd_r,1.25),
     ('Menu',pkeyboard.Key.menu,1.25),('Ctrl',pkeyboard.Key.ctrl_r,1.25),
     ('',None,0.5),('←',pkeyboard.Key.left,1.0),
     ('↓',pkeyboard.Key.down,1.0),('→',pkeyboard.Key.right,1.0),
     ('',None,0.5),('0',pkeyboard.KeyCode.from_char('0'),2.0),
     ('.',pkeyboard.KeyCode.from_char('.'),1.0),('',None,1.0)],
]

class VisualInterfaceEmbedded:
    def __init__(self, root):
        self.root = root
        self.root.title('task1 — Input Visualiser')
        self.root.overrideredirect(True)
        self.root.attributes('-topmost', True)
        self.root.attributes('-transparentcolor', MAGIC)
        self.root.configure(bg=MAGIC)
        self._drag_x = self._drag_y = 0
        self._kb_items = []
        self._mw = {}
        self._log_labels = []
        self._pos_label  = None
        self._build()
        self._tick()

    def _drag_start(self, e):
        self._drag_x = e.x_root - self.root.winfo_x()
        self._drag_y = e.y_root - self.root.winfo_y()

    def _drag_move(self, e):
        self.root.geometry(f'+{e.x_root - self._drag_x}+{e.y_root - self._drag_y}')

    def _build(self):
        pad = 12
        outer = tk.Frame(self.root, bg=MAGIC)
        outer.pack(padx=pad, pady=pad)

        ver = tk.Label(outer, text='task1.py — Input Visualiser  [drag]',
                       bg=MAGIC, fg='#333333', font=('Consolas', 8))
        ver.pack(anchor='w', pady=(0, 4))
        ver.bind('<ButtonPress-1>', self._drag_start)
        ver.bind('<B1-Motion>',     self._drag_move)

        kb_f = tk.Frame(outer, bg=MAGIC)
        kb_f.pack()
        self._build_keyboard(kb_f)

        # Horizontal bottom panel
        bot = tk.Frame(outer, bg=MAGIC)
        bot.pack(pady=(8, 0), fill='x')
        self._build_mouse(bot)
        self._build_pos(bot)
        self._build_log(bot)

    def _build_keyboard(self, parent):
        ROW_GAP   = [10, 6, 4, 4, 4, 4]
        max_row_w = max(sum(s * (U + G) for _, _, s in row) for row in ROWS)
        total_h   = len(ROWS) * (U + G) + sum(ROW_GAP) + 6
        c = tk.Canvas(parent, bg=MAGIC, highlightthickness=0,
                      width=int(max_row_w) + 6, height=int(total_h))
        c.pack()
        c.bind('<ButtonPress-1>', self._drag_start)
        c.bind('<B1-Motion>',     self._drag_move)
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
                fs = ('Consolas', 7) if len(label) > 4 else ('Consolas', 8)
                r_id = c.create_rectangle(row_x, row_y, row_x+w, row_y+U,
                                          fill=KEY_IDLE, outline=KEY_BORDER, width=1)
                t_id = c.create_text(row_x+w//2, row_y+U//2, text=label,
                                     fill=KEY_TEXT, font=fs, anchor='center')
                self._kb_items.append((r_id, t_id, ref))
                row_x += int(span * (U + G))
            row_y += U + G

    def _build_mouse(self, parent):
        f = tk.Frame(parent, bg=MAGIC)
        f.pack(side='left', anchor='n', padx=(0, 14))
        tk.Label(f, text='MOUSE', bg=MAGIC, fg=SL,
                 font=('Consolas', 8)).pack(anchor='w')

        bw, bh, sw, sh = 28, 56, 11, 30
        total_w = bw*2 + sw + 6
        c = tk.Canvas(f, bg=MAGIC, highlightthickness=0,
                      width=total_w+16, height=bh+60)
        c.pack()
        ox, oy = 8, 4
        lb = c.create_rectangle(ox, oy, ox+bw, oy+bh,
                                 fill=MOUSE_IDLE, outline=KEY_BORDER)
        sx = ox+bw+3
        sy = oy+(bh-sh)//2
        mb = c.create_rectangle(sx, sy, sx+sw, sy+sh,
                                 fill=MOUSE_SCR, outline=KEY_BORDER)
        sa = c.create_text(sx+sw//2, oy+bh//2, text='●',
                           fill='#333333', font=('Consolas', 7))
        rx = sx+sw+3
        rb = c.create_rectangle(rx, oy, rx+bw, oy+bh,
                                 fill=MOUSE_IDLE, outline=KEY_BORDER)
        c.create_rectangle(ox, oy+bh+2, ox+total_w, oy+bh+44,
                           fill='#1a1a1a', outline=KEY_BORDER)
        self._mw = {'canvas': c, 'left': lb, 'right': rb,
                    'middle': mb, 'scroll_arrow': sa}

    def _build_pos(self, parent):
        f = tk.Frame(parent, bg=MAGIC)
        f.pack(side='left', anchor='n', padx=(0, 14))
        tk.Label(f, text='POSITION', bg=MAGIC, fg=SL,
                 font=('Consolas', 8)).pack(anchor='w')
        # Single line, monospace, fixed-width — no wrapping
        self._pos_label = tk.Label(
            f, text='(0,0)', bg=LOG_BG, fg=POS_TEXT,
            font=('Consolas', 9), width=22, anchor='w',
            padx=6, pady=4, justify='left'
        )
        self._pos_label.pack(anchor='w')

    def _build_log(self, parent):
        f = tk.Frame(parent, bg=MAGIC)
        f.pack(side='left', anchor='n', fill='x', expand=True)
        tk.Label(f, text='RECENT EVENTS', bg=MAGIC, fg=SL,
                 font=('Consolas', 8)).pack(anchor='w')
        for _ in range(5):
            lbl = tk.Label(f, text='', bg=LOG_BG, fg=LOG_TEXT,
                           font=('Consolas', 9), anchor='w',
                           padx=6, width=38, relief='flat', pady=1)
            lbl.pack(anchor='w', pady=1)
            self._log_labels.append(lbl)

    def _tick(self):
        c = self._kb_canvas
        for r_id, t_id, ref in self._kb_items:
            if _vi_any_held(ref):
                c.itemconfig(r_id, fill=KEY_HOLD, outline=KEY_HOLD)
                c.itemconfig(t_id, fill=KEY_TEXT_HI)
            elif _vi_any_pressed(ref):
                c.itemconfig(r_id, fill=KEY_PRESS, outline=KEY_PRESS)
                c.itemconfig(t_id, fill=KEY_TEXT_HI)
            else:
                c.itemconfig(r_id, fill=KEY_IDLE, outline=KEY_BORDER)
                c.itemconfig(t_id, fill=KEY_TEXT)

        mc = self._mw['canvas']
        mc.itemconfig(self._mw['left'],  fill=MOUSE_CLICK if 'left'  in mouse_buttons else MOUSE_IDLE)
        mc.itemconfig(self._mw['right'], fill=MOUSE_CLICK if 'right' in mouse_buttons else MOUSE_IDLE)
        if scroll_flash == 'up':
            mc.itemconfig(self._mw['middle'], fill=MOUSE_SCR_A)
            mc.itemconfig(self._mw['scroll_arrow'], text='↑', fill=KEY_TEXT_HI)
        elif scroll_flash == 'down':
            mc.itemconfig(self._mw['middle'], fill=MOUSE_SCR_A)
            mc.itemconfig(self._mw['scroll_arrow'], text='↓', fill=KEY_TEXT_HI)
        else:
            mc.itemconfig(self._mw['middle'], fill=MOUSE_SCR)
            mc.itemconfig(self._mw['scroll_arrow'], text='●', fill='#333333')

        # Position — compact single line, trim if too long
        pos_txt = mouse_status[:22]
        self._pos_label.config(text=pos_txt)

        padded = [''] * (5 - len(recent_logs)) + recent_logs
        for i, lbl in enumerate(self._log_labels):
            lbl.config(text=padded[i],
                       fg=LOG_HI if (i == 4 and padded[i]) else LOG_TEXT)

        self.root.after(30, self._tick)

# ── Threads ────────────────────────────────────────────────────────────────────
def _vi_mouse_thread():
    with pmouse.Listener(on_move=_vi_on_move, on_click=_vi_on_click,
                         on_scroll=_vi_on_scroll) as ml:
        ml.join()

def _vi_keyboard_thread():
    with pkeyboard.Listener(on_press=_vi_on_press,
                            on_release=_vi_on_release) as kl:
        kl.join()

# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    # Start overlay crosshair
    threading.Thread(target=_run_overlay,  daemon=True).start()
    threading.Thread(target=_overlay_follow, daemon=True).start()

    # Start visual interface listeners
    threading.Thread(target=_vi_mouse_thread,    daemon=True).start()
    threading.Thread(target=_vi_keyboard_thread, daemon=True).start()

    # Run EHW script in background after 1s (lets UI settle first)
    def _run_ehw():
        print('[task1] Waiting 5 seconds before starting EHWRunner...')
        time.sleep(1)
        print('[task1] Starting EHWRunner...')
        time.sleep(4)
        print(f'[task1] Parsing and running {EHW_FILE}')
        runner = EHWRunner(EHW_FILE)
        runner.run()
        print('[task1] Done.')

    threading.Thread(target=_run_ehw, daemon=True).start()

    # Start visual interface (main thread)
    root = tk.Tk()
    VisualInterfaceEmbedded(root)
    root.mainloop()
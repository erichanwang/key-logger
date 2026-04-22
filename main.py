import pynput
import threading
import tkinter as tk
import ctypes
import time as _time
import os
import sys
from datetime import datetime

# ── DPI fix — must be before anything else ─────────────────────────────────────
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# ── Windows constants for click-through overlay ────────────────────────────────
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED     = 0x00080000
WS_EX_NOACTIVATE  = 0x08000000
WS_EX_TOOLWINDOW  = 0x00000080
GWL_EXSTYLE       = -20

VERSION = '0.0.2'

# ── Logging setup ──────────────────────────────────────────────────────────────
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
os.makedirs(LOG_DIR, exist_ok=True)

session_start = datetime.now()
log_filename  = session_start.strftime('%Y-%m-%d_%H-%M-%S') + '.log'
log_path      = os.path.join(LOG_DIR, log_filename)
_log_file     = open(log_path, 'w', encoding='utf-8', buffering=1)

def log(text):
    """Print to terminal and write to log file."""
    print(text)
    _log_file.write(text + '\n')

def log_inline(text):
    """Overwrite current terminal line — only the final committed version goes to file."""
    print(text, end='\r', flush=True)

def log_commit(text):
    """Commit an inline line — newline on terminal and write to file."""
    print(text)
    _log_file.write(text + '\n')

# ── Globals ────────────────────────────────────────────────────────────────────
mouse_pos      = (0, 0)
start_pos      = None
last_pos       = None
move_timer     = None
overlay_root   = None
canvas         = None
mouse_listener = None

typing_buffer  = []
display_buffer = []

scroll_count     = 0
scroll_timer     = None
scroll_direction = None

held_modifiers = set()

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

HOLD_DEBOUNCE = 0.30  # seconds before a keypress is considered a hold
held_keys = {}        # key -> {press_time, debounce, live, holding}

# ── Overlay ────────────────────────────────────────────────────────────────────
SIZE = 41
HALF = SIZE // 2

def make_click_through(root):
    hwnd = ctypes.windll.user32.FindWindowW(None, root.title())
    if not hwnd:
        hwnd = root.winfo_id()
    style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    style |= WS_EX_TRANSPARENT | WS_EX_LAYERED | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
    ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)

def update_overlay():
    if not canvas:
        return
    canvas.delete('crosshair')
    cx, cy = HALF, HALF
    r, w = HALF - 2, 2
    canvas.create_line(cx - r, cy, cx + r, cy, fill='red', width=w, tags='crosshair')
    canvas.create_line(cx, cy - r, cx, cy + r, fill='red', width=w, tags='crosshair')

def on_move(x, y):
    global mouse_pos, start_pos, last_pos, move_timer
    mouse_pos = (x, y)
    if overlay_root:
        overlay_root.geometry(f'{SIZE}x{SIZE}+{x - HALF}+{y - HALF}')
    update_overlay()
    if move_timer:
        move_timer.cancel()
    if not start_pos:
        start_pos = (x, y)
    last_pos = (x, y)
    move_timer = threading.Timer(0.3, flush_move)
    move_timer.start()

def create_overlay():
    global overlay_root, canvas
    overlay_root = tk.Tk()
    overlay_root.title('overlay')
    overlay_root.geometry(f'{SIZE}x{SIZE}+0+0')
    overlay_root.overrideredirect(True)
    overlay_root.attributes('-topmost', True)
    overlay_root.attributes('-alpha', 1.0)
    overlay_root.configure(bg='black')
    overlay_root.attributes('-transparentcolor', 'black')
    canvas = tk.Canvas(overlay_root, width=SIZE, height=SIZE, bg='black', highlightthickness=0)
    canvas.pack()
    update_overlay()
    overlay_root.after(100, make_click_through, overlay_root)
    overlay_root.mainloop()

# ── Mouse logs ─────────────────────────────────────────────────────────────────
def flush_move():
    global start_pos, last_pos, move_timer
    if start_pos and last_pos and start_pos != last_pos:
        log(f'[mouse]  {start_pos} → {last_pos}')
    start_pos = None
    last_pos  = None

def on_click(x, y, button, pressed):
    if not pressed:
        return
    names = {
        pynput.mouse.Button.left:   'LMB',
        pynput.mouse.Button.right:  'RMB',
        pynput.mouse.Button.middle: 'MMB',
    }
    log(f'[click]  {names.get(button, str(button))}  {(x, y)}')

def on_scroll(x, y, dx, dy):
    global scroll_count, scroll_timer, scroll_direction
    new_dir = '↓' if dy < 0 else '↑'
    if scroll_timer:
        scroll_timer.cancel()
    if scroll_direction and new_dir != scroll_direction:
        log_commit(f'[scroll] {scroll_direction} ×{scroll_count}')
        scroll_count = 0
    scroll_direction = new_dir
    scroll_count += 1
    log_inline(f'[scroll] {scroll_direction} ×{scroll_count}')
    scroll_timer = threading.Timer(1.0, finalize_scroll)
    scroll_timer.start()

def finalize_scroll():
    global scroll_count, scroll_direction
    log_commit(f'[scroll] {scroll_direction} ×{scroll_count}')
    scroll_count     = 0
    scroll_direction = None

# ── Keyboard logs ──────────────────────────────────────────────────────────────
def print_typing():
    text = ''.join(display_buffer)
    log_inline(f'[type]   {text}    ')

def commit_typing():
    if display_buffer:
        log_commit(f'[type]   {"".join(display_buffer)}')
    typing_buffer.clear()
    display_buffer.clear()

def key_name(key):
    try:
        return key.char.upper() if key.char else str(key)
    except AttributeError:
        return str(key).replace('Key.', '').title()

# ── Hold helpers ───────────────────────────────────────────────────────────────
def _hold_start(key):
    if key not in held_keys:
        return
    held_keys[key]['holding'] = True
    elapsed_ms = int((_time.monotonic() - held_keys[key]['press_time']) * 1000)
    log_inline(f'[hold]   {key_name(key)} held for {elapsed_ms}ms')
    _schedule_live(key)

def _schedule_live(key):
    if key not in held_keys or not held_keys[key]['holding']:
        return
    t = threading.Timer(0.1, _live_update, args=(key,))
    held_keys[key]['live'] = t
    t.start()

def _live_update(key):
    if key not in held_keys or not held_keys[key]['holding']:
        return
    elapsed_ms = int((_time.monotonic() - held_keys[key]['press_time']) * 1000)
    log_inline(f'[hold]   {key_name(key)} held for {elapsed_ms}ms')
    _schedule_live(key)

def _hold_end(key):
    info = held_keys.pop(key, None)
    if not info:
        return
    if info.get('debounce'):
        info['debounce'].cancel()
    if info.get('live'):
        info['live'].cancel()
    if info['holding']:
        elapsed_ms = int((_time.monotonic() - info['press_time']) * 1000)
        log_commit(f'[hold]   {key_name(key)} held for {elapsed_ms}ms')

def _register_hold(key):
    if key in held_keys:
        return  # OS key-repeat — ignore
    press_time = _time.monotonic()
    t = threading.Timer(HOLD_DEBOUNCE, _hold_start, args=(key,))
    held_keys[key] = {
        'press_time': press_time,
        'debounce':   t,
        'live':       None,
        'holding':    False,
    }
    t.start()

# ── Key handler ────────────────────────────────────────────────────────────────
def handle_key(key):
    """Returns True if ESC pressed (stop signal)."""
    global mouse_listener, overlay_root

    # Track modifier down
    if key in MODIFIER_KEYS:
        held_modifiers.add(MODIFIER_KEYS[key])
        return False

    # ESC with no modifiers = stop
    if key == pynput.keyboard.Key.esc and not held_modifiers:
        if move_timer:   move_timer.cancel()
        if scroll_timer: scroll_timer.cancel()
        flush_move()
        commit_typing()
        if mouse_listener:  mouse_listener.stop()
        if overlay_root:    overlay_root.quit()
        log('[info]   stopped.')
        _log_file.close()
        return True

    # Modifier combo = command
    active = held_modifiers - {'Shift'}
    if active:
        commit_typing()
        parts = sorted(active, key=lambda m: ['Ctrl', 'Alt', 'Win'].index(m) if m in ['Ctrl', 'Alt', 'Win'] else 99)
        combo = '+'.join(parts) + '+' + key_name(key)
        log(f'[cmd]    {combo}')
        return False

    # Start hold detection
    _register_hold(key)

    # Normal keys
    if key == pynput.keyboard.Key.backspace:
        if typing_buffer:
            popped = typing_buffer.pop()
            if display_buffer:
                display_buffer.pop()
            display_buffer.append(f'[←{popped}]')
        print_typing()
        return False

    if key == pynput.keyboard.Key.enter:
        commit_typing()
        return False

    if key == pynput.keyboard.Key.space:
        typing_buffer.append(' ')
        display_buffer.append(' ')
        print_typing()
        return False

    if key == pynput.keyboard.Key.tab:
        typing_buffer.append('\t')
        display_buffer.append('  ')
        print_typing()
        return False

    # Arrow keys — log as navigation, don't add to typing buffer
    ARROW_KEYS = {
        pynput.keyboard.Key.up:    '↑',
        pynput.keyboard.Key.down:  '↓',
        pynput.keyboard.Key.left:  '←',
        pynput.keyboard.Key.right: '→',
    }
    if key in ARROW_KEYS:
        commit_typing()  # flush any in-progress text first
        log(f'[arrow]  {ARROW_KEYS[key]}')
        return False

    try:
        char = key.char
        if char:
            typing_buffer.append(char)
            display_buffer.append(char)
            print_typing()
    except AttributeError:
        pass  # ignore other special keys

    return False

def on_press(key):
    if handle_key(key):
        return False

def on_release(key):
    if key in MODIFIER_KEYS:
        held_modifiers.discard(MODIFIER_KEYS[key])
    _hold_end(key)

# ── Mouse thread ───────────────────────────────────────────────────────────────
def mouse_thread():
    global mouse_listener
    mouse_listener = pynput.mouse.Listener(
        on_move=on_move,
        on_click=on_click,
        on_scroll=on_scroll,
    )
    mouse_listener.start()
    mouse_listener.join()

# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    header = (
        f'========================================\n'
        f'  Version: {VERSION}\n'
        f'  session started: {session_start.strftime("%Y-%m-%d %H:%M:%S")}\n'
        f'  log file: {log_path}\n'
        f'========================================'
    )
    log(header)
    log('[info]   running — press ESC to stop.\n')

    threading.Thread(target=mouse_thread, daemon=True).start()
    threading.Thread(target=create_overlay, daemon=True).start()

    with pynput.keyboard.Listener(on_press=on_press, on_release=on_release) as kl:
        kl.join()
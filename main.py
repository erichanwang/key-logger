import pynput
import threading
import tkinter as tk
import ctypes

# Must be called before anything else — fixes DPI so pynput & tkinter coords match
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# Windows constants for click-through / no-focus window
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED     = 0x00080000
WS_EX_NOACTIVATE  = 0x08000000
WS_EX_TOOLWINDOW  = 0x00000080
GWL_EXSTYLE       = -20

# ── Globals ────────────────────────────────────────────────────────────────────
mouse_pos      = (0, 0)
start_pos      = None
last_pos       = None
move_timer     = None
overlay_root   = None
canvas         = None
mouse_listener = None

typing_buffer  = []   # raw chars for word tracking
display_buffer = []   # what we show (includes [←x] markers)

scroll_count = 0
scroll_timer = None

# ── Overlay ────────────────────────────────────────────────────────────────────
SIZE = 41   # window size in px
HALF = SIZE // 2

def make_click_through(root):
    """Make the overlay window completely click-through and never steal focus."""
    hwnd = ctypes.windll.user32.FindWindowW(None, root.title())
    if not hwnd:
        hwnd = root.winfo_id()
    style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    style |= WS_EX_TRANSPARENT | WS_EX_LAYERED | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
    ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)

def update_overlay():
    if not canvas:
        return
    canvas.delete("crosshair")
    cx, cy = HALF, HALF
    r = HALF - 2   # arm length
    w = 2          # line width
    canvas.create_line(cx - r, cy, cx + r, cy, fill="red", width=w, tags="crosshair")
    canvas.create_line(cx, cy - r, cx, cy + r, fill="red", width=w, tags="crosshair")

def on_move(x, y):
    global mouse_pos, start_pos, last_pos, move_timer
    mouse_pos = (x, y)
    if overlay_root:
        overlay_root.geometry(f"{SIZE}x{SIZE}+{x - HALF}+{y - HALF}")
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
    overlay_root.title("overlay")
    overlay_root.geometry(f"{SIZE}x{SIZE}+0+0")
    overlay_root.overrideredirect(True)
    overlay_root.attributes('-topmost', True)
    overlay_root.attributes('-alpha', 1.0)
    overlay_root.configure(bg='black')
    overlay_root.attributes('-transparentcolor', 'black')

    canvas = tk.Canvas(overlay_root, width=SIZE, height=SIZE,
                       bg='black', highlightthickness=0)
    canvas.pack()
    update_overlay()

    # Apply click-through after window is fully drawn
    overlay_root.after(100, make_click_through, overlay_root)
    overlay_root.mainloop()

# ── Mouse logs ─────────────────────────────────────────────────────────────────
def flush_move():
    global start_pos, last_pos, move_timer
    if start_pos and last_pos and start_pos != last_pos:
        print(f'[mouse]  {start_pos} → {last_pos}')
    start_pos = None
    last_pos  = None

def on_click(x, y, button, pressed):
    if not pressed:
        return  # only log on press, not release
    names = {
        pynput.mouse.Button.left:   'LMB',
        pynput.mouse.Button.right:  'RMB',
        pynput.mouse.Button.middle: 'MMB',
    }
    print(f'[click]  {names.get(button, str(button))}  {(x, y)}')

def on_scroll(x, y, dx, dy):
    global scroll_count, scroll_timer
    if scroll_timer:
        scroll_timer.cancel()
    scroll_count += 1
    direction = '↓' if dy < 0 else '↑'
    print(f'[scroll] {direction} ×{scroll_count}', end='\r', flush=True)
    scroll_timer = threading.Timer(1.0, finalize_scroll)
    scroll_timer.start()

def finalize_scroll():
    global scroll_count
    print(f'[scroll] ×{scroll_count}')
    scroll_count = 0

# ── Keyboard logs ──────────────────────────────────────────────────────────────
def print_typing():
    text = ''.join(display_buffer)
    print(f'\r[type]   {text}    ', end='', flush=True)

def commit_typing():
    if display_buffer:
        print(f'\r[type]   {"".join(display_buffer)}')
    typing_buffer.clear()
    display_buffer.clear()

def handle_key(key):
    """Unified key handler. Returns True if ESC (stop signal)."""
    global mouse_listener, overlay_root

    if key == pynput.keyboard.Key.esc:
        if move_timer:   move_timer.cancel()
        if scroll_timer: scroll_timer.cancel()
        flush_move()
        commit_typing()
        if mouse_listener:  mouse_listener.stop()
        if overlay_root:    overlay_root.quit()
        print('[info]   stopped.')
        return True

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

    try:
        char = key.char
        if char:
            typing_buffer.append(char)
            display_buffer.append(char)
            print_typing()
    except AttributeError:
        pass  # silently ignore other special keys

    return False

def on_press(key):
    if handle_key(key):
        return False  # stops the keyboard listener

def on_release(key):
    pass

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
    print('[info]   running — press ESC to stop.\n')

    threading.Thread(target=mouse_thread, daemon=True).start()
    threading.Thread(target=create_overlay, daemon=True).start()

    with pynput.keyboard.Listener(on_press=on_press, on_release=on_release) as kl:
        kl.join()
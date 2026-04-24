"""
ai_cursor.py
─────────────────────────────────────────────────────────────────────
An AI-controlled red crosshair that moves around the screen
independently of the user's real mouse.

Flow:
  1. Screenshot the screen
  2. Template-match A/B/C/D circle images (from ./letters/)
  3. Animate the AI cursor flying to the best candidate
  4. "Click" via SendInput (no real mouse movement)
  5. Wait for user to press Y (correct) or N (wrong)
     - N → mark that answer as bad, try next candidate
     - Y → celebrate, wait for next round

The real mouse never moves. All clicking is done through
the Windows SendInput API at the AI cursor's screen position.

Controls (global hotkeys, always active):
  F9  → trigger a new scan + click attempt
  F10 → exit
"""

import os
import sys
import time
import math
import random
import threading
import tkinter as tk
import ctypes
import ctypes.wintypes as wintypes

import cv2
import numpy as np
import pyautogui

# ── DPI fix ────────────────────────────────────────────────────────────────────
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import pynput.keyboard as pkeyboard

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────
LETTERS_DIR   = os.path.join(os.path.dirname(__file__), 'letters')
LETTER_FILES  = {
    'A': os.path.join(LETTERS_DIR, 'A.png'),
    'B': os.path.join(LETTERS_DIR, 'B.png'),
    'C': os.path.join(LETTERS_DIR, 'C.png'),
    'D': os.path.join(LETTERS_DIR, 'D.png'),
}
CV2_THRESHOLD   = 0.65    # match confidence floor
CURSOR_SIZE     = 51      # overlay window px (odd)
CURSOR_HALF     = CURSOR_SIZE // 2
ANIM_FPS        = 60
ANIM_DURATION   = 0.55    # seconds to fly between positions
IDLE_DRIFT      = True    # AI cursor drifts gently when idle
SCAN_SCALES     = [1.0, 0.85, 0.70, 1.15]  # multi-scale search

# ─────────────────────────────────────────────────────────────────────────────
# Windows SendInput — click without touching real mouse
# ─────────────────────────────────────────────────────────────────────────────
MOUSEEVENTF_MOVE        = 0x0001
MOUSEEVENTF_LEFTDOWN    = 0x0002
MOUSEEVENTF_LEFTUP      = 0x0004
MOUSEEVENTF_ABSOLUTE    = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000

INPUT_MOUSE    = 0
SM_CXSCREEN    = 0
SM_CYSCREEN    = 1

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ('dx',          ctypes.c_long),
        ('dy',          ctypes.c_long),
        ('mouseData',   ctypes.c_ulong),
        ('dwFlags',     ctypes.c_ulong),
        ('time',        ctypes.c_ulong),
        ('dwExtraInfo', ctypes.POINTER(ctypes.c_ulong)),
    ]

class _INPUT_UNION(ctypes.Union):
    _fields_ = [('mi', MOUSEINPUT)]

class INPUT(ctypes.Structure):
    _fields_ = [('type', ctypes.c_ulong), ('_input', _INPUT_UNION)]

def _send_click(screen_x: int, screen_y: int):
    """
    Send a left-click at (screen_x, screen_y) via SendInput.
    The real mouse cursor position is NOT changed.
    Windows normalises coords to 0–65535 across the virtual desktop.
    """
    desk_w = ctypes.windll.user32.GetSystemMetrics(SM_CXSCREEN)
    desk_h = ctypes.windll.user32.GetSystemMetrics(SM_CYSCREEN)

    norm_x = int(screen_x * 65535 / desk_w)
    norm_y = int(screen_y * 65535 / desk_h)

    flags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK

    # Move (invisible — does NOT update cursor position shown on screen)
    move = INPUT(type=INPUT_MOUSE,
                 _input=_INPUT_UNION(mi=MOUSEINPUT(
                     dx=norm_x, dy=norm_y, mouseData=0,
                     dwFlags=flags, time=0,
                     dwExtraInfo=ctypes.pointer(ctypes.c_ulong(0)))))

    down = INPUT(type=INPUT_MOUSE,
                 _input=_INPUT_UNION(mi=MOUSEINPUT(
                     dx=norm_x, dy=norm_y, mouseData=0,
                     dwFlags=MOUSEEVENTF_LEFTDOWN, time=0,
                     dwExtraInfo=ctypes.pointer(ctypes.c_ulong(0)))))

    up   = INPUT(type=INPUT_MOUSE,
                 _input=_INPUT_UNION(mi=MOUSEINPUT(
                     dx=norm_x, dy=norm_y, mouseData=0,
                     dwFlags=MOUSEEVENTF_LEFTUP, time=0,
                     dwExtraInfo=ctypes.pointer(ctypes.c_ulong(0)))))

    ctypes.windll.user32.SendInput(1, ctypes.byref(move), ctypes.sizeof(INPUT))
    time.sleep(0.04)
    ctypes.windll.user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(INPUT))
    time.sleep(0.05)
    ctypes.windll.user32.SendInput(1, ctypes.byref(up),   ctypes.sizeof(INPUT))

# ─────────────────────────────────────────────────────────────────────────────
# Overlay — transparent click-through window with the red AI crosshair
# ─────────────────────────────────────────────────────────────────────────────
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED     = 0x00080000
WS_EX_NOACTIVATE  = 0x08000000
WS_EX_TOOLWINDOW  = 0x00000080
GWL_EXSTYLE       = -20

class AICursorOverlay:
    """Animated transparent overlay window — draws the AI cursor."""

    def __init__(self):
        self.root   = tk.Tk()
        self.root.title('__ai_cursor__')
        self.root.overrideredirect(True)
        self.root.attributes('-topmost', True)
        self.root.attributes('-transparentcolor', 'black')
        self.root.configure(bg='black')
        self.root.geometry(f'{CURSOR_SIZE}x{CURSOR_SIZE}+100+100')

        self.canvas = tk.Canvas(self.root, width=CURSOR_SIZE, height=CURSOR_SIZE,
                                bg='black', highlightthickness=0)
        self.canvas.pack()

        # Current & target screen positions
        self._cx   = 100.0   # current (float for smooth anim)
        self._cy   = 100.0
        self._tx   = 100.0   # target
        self._ty   = 100.0
        self._sx   = 100.0   # anim start
        self._sy   = 100.0
        self._t0   = time.monotonic()
        self._dur  = ANIM_DURATION
        self._idle_phase = 0.0

        # State
        self.state      = 'idle'   # idle | flying | waiting | wrong | correct
        self.status_msg = 'Press F9 to scan'
        self._pulse     = 0.0

        self.root.after(100, self._make_click_through)
        self.root.after(16,  self._tick)

    def _make_click_through(self):
        hwnd  = ctypes.windll.user32.FindWindowW(None, self.root.title())
        if not hwnd:
            hwnd = self.root.winfo_id()
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        style |= WS_EX_TRANSPARENT | WS_EX_LAYERED | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
        ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)

    def fly_to(self, tx: float, ty: float, duration: float = ANIM_DURATION):
        self._sx  = self._cx
        self._sy  = self._cy
        self._tx  = tx
        self._ty  = ty
        self._t0  = time.monotonic()
        self._dur = duration
        self.state = 'flying'

    def _ease(self, t: float) -> float:
        """Cubic ease-in-out."""
        t = max(0.0, min(1.0, t))
        return t * t * (3 - 2 * t)

    def _tick(self):
        now = time.monotonic()
        self._pulse = (self._pulse + 0.08) % (2 * math.pi)
        self._idle_phase = (self._idle_phase + 0.015) % (2 * math.pi)

        # ── Animate position
        if self.state == 'flying':
            elapsed = now - self._t0
            p = self._ease(elapsed / max(self._dur, 0.001))
            self._cx = self._sx + (self._tx - self._sx) * p
            self._cy = self._sy + (self._ty - self._sy) * p
            if elapsed >= self._dur:
                self._cx    = self._tx
                self._cy    = self._ty
                self.state  = 'waiting'

        elif self.state == 'idle' and IDLE_DRIFT:
            # Gentle sinusoidal drift so it looks alive
            self._cx = self._tx + math.sin(self._idle_phase * 1.3) * 6
            self._cy = self._ty + math.cos(self._idle_phase * 0.9) * 4

        # ── Move window
        wx = int(self._cx - CURSOR_HALF)
        wy = int(self._cy - CURSOR_HALF)
        self.root.geometry(f'{CURSOR_SIZE}x{CURSOR_SIZE}+{wx}+{wy}')

        # ── Draw crosshair
        self._draw()
        self.root.after(16, self._tick)   # ~60fps

    def _draw(self):
        c  = self.canvas
        c.delete('all')
        cx = cy = CURSOR_HALF

        # Color & arm length depend on state
        if self.state == 'idle':
            color = '#cc2222'
            r     = 18
            w     = 2
        elif self.state == 'flying':
            # Stretch arms in direction of travel
            color = '#ff4400'
            r     = 22
            w     = 2
        elif self.state == 'waiting':
            # Pulsing white ring + red cross
            pulse_r = int(10 + 5 * abs(math.sin(self._pulse)))
            c.create_oval(cx - pulse_r, cy - pulse_r,
                          cx + pulse_r, cy + pulse_r,
                          outline='#ffffff', width=1, tags='ring')
            color = '#ff2222'
            r     = 20
            w     = 2
        elif self.state == 'wrong':
            color = '#ff6600'
            r     = 20
            w     = 3
        elif self.state == 'correct':
            color = '#00ff88'
            r     = 22
            w     = 3
        else:
            color = '#cc2222'
            r     = 18
            w     = 2

        # Centre dot
        c.create_oval(cx-2, cy-2, cx+2, cy+2, fill=color, outline='')
        # Cross arms
        c.create_line(cx-r, cy, cx+r, cy, fill=color, width=w)
        c.create_line(cx, cy-r, cx, cy+r, fill=color, width=w)

        # Small diagonal ticks at arm tips for style
        t = 5
        for dx2, dy2 in [(-r, 0), (r, 0), (0, -r), (0, r)]:
            ex, ey = cx + dx2, cy + dy2
            c.create_line(ex-t//2, ey, ex+t//2, ey, fill=color, width=1)
            c.create_line(ex, ey-t//2, ex, ey+t//2, fill=color, width=1)

    def run(self):
        self.root.mainloop()

# ─────────────────────────────────────────────────────────────────────────────
# CV2 — multi-scale template matching
# ─────────────────────────────────────────────────────────────────────────────
def _screenshot_np() -> np.ndarray:
    img = pyautogui.screenshot()
    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)

def _match_template_multiscale(screen: np.ndarray, template_path: str,
                                scales=SCAN_SCALES, threshold=CV2_THRESHOLD):
    """
    Match template at multiple scales. Returns (confidence, cx, cy) or None.
    """
    tmpl = cv2.imread(template_path, cv2.IMREAD_UNCHANGED)
    if tmpl is None:
        return None

    has_alpha = (tmpl.shape[2] == 4)
    if has_alpha:
        mask = tmpl[:, :, 3]
        tmpl = tmpl[:, :, :3]
    else:
        mask = None

    screen_gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
    tmpl_gray   = cv2.cvtColor(tmpl,   cv2.COLOR_BGR2GRAY)

    best = None  # (confidence, cx, cy)

    for scale in scales:
        th = int(tmpl_gray.shape[0] * scale)
        tw = int(tmpl_gray.shape[1] * scale)
        if th < 8 or tw < 8:
            continue
        t_scaled = cv2.resize(tmpl_gray, (tw, th))

        if mask is not None:
            m_scaled = cv2.resize(mask, (tw, th))
            result   = cv2.matchTemplate(screen_gray, t_scaled,
                                         cv2.TM_CCORR_NORMED, mask=m_scaled)
        else:
            result = cv2.matchTemplate(screen_gray, t_scaled,
                                       cv2.TM_CCOEFF_NORMED)

        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        if max_val >= threshold:
            cx = max_loc[0] + tw // 2
            cy = max_loc[1] + th // 2
            if best is None or max_val > best[0]:
                best = (max_val, cx, cy)

    return best

def scan_all_letters(excluded: set[str] = None) -> list[dict]:
    """
    Scan screen for all A/B/C/D templates.
    Returns list of dicts sorted by confidence desc:
      {'letter': 'A', 'conf': 0.92, 'x': 540, 'y': 320}
    """
    excluded = excluded or set()
    screen   = _screenshot_np()
    results  = []

    for letter, path in LETTER_FILES.items():
        if letter in excluded:
            continue
        if not os.path.exists(path):
            print(f'[CV2] Template missing: {path}')
            continue
        match = _match_template_multiscale(screen, path)
        if match:
            conf, cx, cy = match
            results.append({'letter': letter, 'conf': conf, 'x': cx, 'y': cy})
            print(f'[CV2] {letter}: conf={conf:.3f} at ({cx},{cy})')
        else:
            print(f'[CV2] {letter}: no match above threshold')

    results.sort(key=lambda r: r['conf'], reverse=True)
    return results

# ─────────────────────────────────────────────────────────────────────────────
# AI brain — state machine
# ─────────────────────────────────────────────────────────────────────────────
class AIBrain:
    """
    Controls the AI cursor logic:
      - Scans for answers
      - Picks best candidate
      - Animates cursor there
      - Clicks via SendInput
      - Handles Y/N feedback
    """

    def __init__(self, overlay: AICursorOverlay):
        self.overlay   = overlay
        self.excluded  = set()   # wrong answers this round
        self.candidates = []     # sorted list from last scan
        self.current   = None    # current candidate dict
        self._lock     = threading.Lock()
        self._active   = False

    def trigger_scan(self):
        """Called on F9 — start a fresh scan in background."""
        if self._active:
            print('[AI] Already working, wait…')
            return
        self.excluded.clear()
        threading.Thread(target=self._scan_and_click, daemon=True).start()

    def feedback_correct(self):
        """User pressed Y."""
        if self.current:
            print(f'[AI] ✓ Correct! Answer was {self.current["letter"]}')
            self.overlay.state = 'correct'
            self._active = False
            self.excluded.clear()
            self.current = None

    def feedback_wrong(self):
        """User pressed N — mark current as wrong, try next."""
        if not self.current:
            return
        bad = self.current['letter']
        print(f'[AI] ✗ Wrong ({bad}), trying next…')
        self.excluded.add(bad)
        self.overlay.state = 'wrong'
        time.sleep(0.4)
        threading.Thread(target=self._try_next, daemon=True).start()

    def _scan_and_click(self):
        self._active = True
        print('[AI] Scanning screen…')
        self.overlay.state = 'flying'

        # Do a few "thinking" drifts before committing
        self._thinking_wander(steps=4)

        self.candidates = scan_all_letters(self.excluded)

        if not self.candidates:
            print('[AI] No candidates found.')
            self.overlay.status_msg = 'Nothing found — check letter templates'
            self.overlay.state      = 'idle'
            self._active            = False
            return

        self._click_top_candidate()

    def _try_next(self):
        remaining = [c for c in self.candidates if c['letter'] not in self.excluded]
        if not remaining:
            # Re-scan with lower threshold
            print('[AI] All candidates exhausted, re-scanning…')
            self._scan_and_click()
            return
        self.candidates = remaining
        self._click_top_candidate()

    def _click_top_candidate(self):
        c = self.candidates[0]
        self.current = c
        print(f'[AI] Flying to {c["letter"]} at ({c["x"]},{c["y"]}) conf={c["conf"]:.3f}')

        # Animate cursor to target
        self.overlay.fly_to(c['x'], c['y'])

        # Wait for animation to finish
        time.sleep(ANIM_DURATION + 0.1)

        # Small jitter to look natural
        jx = c['x'] + random.randint(-3, 3)
        jy = c['y'] + random.randint(-3, 3)

        print(f'[AI] Clicking at ({jx},{jy})')
        _send_click(jx, jy)

        self.overlay.state = 'waiting'
        print('[AI] Waiting for feedback: Y=correct  N=wrong')

    def _thinking_wander(self, steps: int = 3):
        """Move the AI cursor around as if 'thinking' before committing."""
        sw = ctypes.windll.user32.GetSystemMetrics(SM_CXSCREEN)
        sh = ctypes.windll.user32.GetSystemMetrics(SM_CYSCREEN)
        cx, cy = self.overlay._cx, self.overlay._cy

        for _ in range(steps):
            # Random-ish wander within a central region
            tx = random.randint(int(sw * 0.2), int(sw * 0.8))
            ty = random.randint(int(sh * 0.2), int(sh * 0.8))
            self.overlay.fly_to(tx, ty, duration=0.25)
            time.sleep(0.3)

# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
def main():
    overlay = AICursorOverlay()
    brain   = AIBrain(overlay)

    print('─' * 50)
    print('  AI Cursor — MCQ Answering Bot')
    print('  F9  → scan screen & attempt answer')
    print('  Y   → correct (reset for next question)')
    print('  N   → wrong   (try next candidate)')
    print('  F10 → exit')
    print('─' * 50)
    print(f'  Looking for templates in: {LETTERS_DIR}')
    for k, v in LETTER_FILES.items():
        exists = '✓' if os.path.exists(v) else '✗ MISSING'
        print(f'    {k}: {v}  [{exists}]')
    print('─' * 50)

    # ── Keyboard hotkeys ───────────────────────────────────────────────────────
    def on_press(key):
        try:
            if key == pkeyboard.Key.f9:
                brain.trigger_scan()
            elif key == pkeyboard.Key.f10:
                print('[AI] Exiting.')
                overlay.root.quit()
                return False  # stop listener
            elif hasattr(key, 'char'):
                if key.char and key.char.lower() == 'y':
                    brain.feedback_correct()
                elif key.char and key.char.lower() == 'n':
                    brain.feedback_wrong()
        except Exception as e:
            print(f'[key] {e}')

    kb_listener = pkeyboard.Listener(on_press=on_press)
    kb_listener.daemon = True
    kb_listener.start()

    # Run overlay on main thread (tkinter requirement)
    overlay.run()
    kb_listener.stop()

if __name__ == '__main__':
    main()
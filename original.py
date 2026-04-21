from pynput import keyboard, mouse
import threading

start_pos = None
last_pos = None
timer = None

def flush_move():
    global start_pos, last_pos
    if start_pos and last_pos and start_pos != last_pos:
        print(f'Mouse moved from {start_pos} to {last_pos}')
    start_pos = None
    last_pos = None

def on_move(x, y):
    global start_pos, last_pos, timer
    if timer:
        timer.cancel()
    if not start_pos:
        start_pos = (x, y)
    last_pos = (x, y)
    timer = threading.Timer(0.2, flush_move)
    timer.start()

def on_click(x, y, button, pressed):
    action = 'pressed' if pressed else 'released'
    btn_name = 'LMB' if button == mouse.Button.left else 'RMB' if button == mouse.Button.right else 'MMB'
    print(f'{btn_name} {action} at ({x}, {y})')

def on_scroll(x, y, dx, dy):
    print(f'Scrolled {"down" if dy < 0 else "up"} at ({x}, {y})')

def on_release(key):
    if key == keyboard.Key.esc:
        if timer:
            timer.cancel()
        flush_move()
        mouse_listener.stop()
        return False

mouse_listener = mouse.Listener(on_move=on_move, on_click=on_click, on_scroll=on_scroll)
mouse_listener.start()

with keyboard.Listener(on_release=on_release) as keyboard_listener:
    keyboard_listener.join()
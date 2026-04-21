from pynput import keyboard, mouse

# --- Mouse handlers ---
def on_move(x, y):
    print(f'Mouse moved to ({x}, {y})')

def on_click(x, y, button, pressed):
    action = 'pressed' if pressed else 'released'
    print(f'{button} {action} at ({x}, {y})')

def on_scroll(x, y, dx, dy):
    print(f'Scrolled {"down" if dy < 0 else "up"} at ({x}, {y})')

# --- Keyboard handlers ---
def on_press(key):
    try:
        print(f'Alphanumeric key {key.char} pressed')
    except AttributeError:
        print(f'Special key {key} pressed')

def on_release(key):
    print(f'{key} released')
    if key == keyboard.Key.esc:
        # Stop both listeners by returning False
        mouse_listener.stop()
        return False

# --- Start both listeners (non-blocking) ---
mouse_listener = mouse.Listener(
    on_move=on_move,
    on_click=on_click,
    on_scroll=on_scroll
)
mouse_listener.start()

# Keyboard listener blocks until Escape is pressed
with keyboard.Listener(
    on_press=on_press,
    on_release=on_release
) as keyboard_listener:
    keyboard_listener.join()
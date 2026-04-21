from pynput import keyboard

def on_press(key):
    try:
        print(f'{key.char} pressed')
    except AttributeError:
        print(f'{key} pressed')

def on_release(key):
    print(f'{key} released')
    if key == keyboard.Key.esc:
        return False

with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
    listener.join()
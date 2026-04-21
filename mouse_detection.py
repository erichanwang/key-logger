# key logger

import pynput
from pynput import keyboard

def on_press(key):
    try:
        print('Alphanumeric key {0} pressed'.format(
            key.char))
    except AttributeError:
        print('Special key {0} pressed'.format(
            key))
        
def on_release(key):
    print('{0} released'.format(
        key))
    if key == keyboard.Key.esc:
        # Stop listener
        return False
    
def get_mouse_position():
    with pynput.mouse.Controller() as mouse:
        print('The current pointer position is {0}'.format(
            mouse.position))

if __name__ == "__main__":
    #track mouse clicks, mouse position, and keyboard key presses continuously
    while True:
        get_mouse_position()
        with keyboard.Listener(
            on_press=on_press,
            on_release=on_release) as listener:
            listener.join()
        
        
        
    
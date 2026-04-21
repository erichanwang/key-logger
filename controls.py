# file for methods that control input

import pyautogui

def press_key(key):
    # simulate key press
    pyautogui.press(key)
    
def move_mouse(x, y):
    # move mouse to (x, y)
    pyautogui.moveTo(x, y)
    
# child method of press key for press tab
def press_tab():
    press_key('tab')
    
def left_click():
    pyautogui.leftClick()

def right_click():
    pyautogui.rightClick()
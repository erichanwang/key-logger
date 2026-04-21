import controls.py
import keyboard_detection.py
import mouse_detection.py

import pynput 
import pyautogui
import time
import random
import threading

def main():
    # Start mouse and keyboard listeners in separate threads
    mouse_thread = threading.Thread(target=mouse_detection.main)
    keyboard_thread = threading.Thread(target=keyboard_detection.main)
    
    mouse_thread.start()
    keyboard_thread.start()
    
    # Wait for both threads to finish (they won't, since they run indefinitely)
    mouse_thread.join()
    keyboard_thread.join()
    
if __name__ == "__main__":
    main()
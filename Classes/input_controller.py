import pyautogui

class InputController:
    def __init__(self):
        pass

    def click(self, x, y):
        # Store the current active window and mouse position
        try:
            prev_active_window = pyautogui.getActiveWindow()
        except:
            prev_active_window = None
            
        prev_mouse_x, prev_mouse_y = pyautogui.position()

        try:
            pyautogui.click(x, y)
            if prev_active_window:
                prev_active_window.activate()
            pyautogui.moveTo(prev_mouse_x, prev_mouse_y)
        except Exception as e:
            print(f"Error during click execution: {e}")
            return False
        return True

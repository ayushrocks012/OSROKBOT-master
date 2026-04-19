import sys
content = open('Classes/window_handler.py', 'r', encoding='utf-8').read()
old = """    @staticmethod
    def _restore_no_activate(hwnd):
        if not WindowHandler._win32_available():
            return
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
            win32gui.SetWindowPos(
                hwnd,
                None,
                0,
                0,
                0,
                0,
                win32con.SWP_NOMOVE
                | win32con.SWP_NOSIZE
                | win32con.SWP_NOZORDER
                | win32con.SWP_NOACTIVATE,
            )"""
new = """    @staticmethod
    def _restore_no_activate(hwnd):
        if not WindowHandler._win32_available():
            return
        try:
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
                win32gui.SetWindowPos(
                    hwnd,
                    None,
                    0,
                    0,
                    0,
                    0,
                    win32con.SWP_NOMOVE
                    | win32con.SWP_NOSIZE
                    | win32con.SWP_NOZORDER
                    | win32con.SWP_NOACTIVATE,
                )
        except WINDOW_HANDLER_EXCEPTIONS as exc:
            LOGGER.warning("Unable to restore window handle %s: %s", hwnd, exc)"""

new_content = content.replace(old, new)
if content == new_content:
    print('Failed to replace.')
else:
    open('Classes/window_handler.py', 'w', encoding='utf-8').write(new_content)
    print('Success.')

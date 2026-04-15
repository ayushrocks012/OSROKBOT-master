"""
OSROKBOT Template Capture Tool
===============================
Use this tool to capture new template images from your Rise of Kingdoms game window.

How to use:
1. Have Rise of Kingdoms open on your PC
2. Run this script: python screenshot_helper.py
3. The tool will switch to the game window, wait, then screenshot it
4. A window shows the screenshot — click and drag to select a UI element
5. Press ENTER to confirm, type a filename, and save
6. Repeat or press 'q' to quit
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from window_handler import WindowHandler
import cv2
import numpy as np
import time

class TemplateCapturer:
    def __init__(self):
        self.window_handler = WindowHandler()
        self.drawing = False
        self.start_x = 0
        self.start_y = 0
        self.end_x = 0
        self.end_y = 0
        self.screenshot_cv = None

    def mouse_callback(self, event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            self.drawing = True
            self.start_x, self.start_y = x, y
            self.end_x, self.end_y = x, y
        elif event == cv2.EVENT_MOUSEMOVE and self.drawing:
            self.end_x, self.end_y = x, y
        elif event == cv2.EVENT_LBUTTONUP:
            self.drawing = False
            self.end_x, self.end_y = x, y

    def take_game_screenshot(self):
        """Activate the game window, wait for it to come to front, then screenshot."""
        print("\n  Activating 'Rise of Kingdoms' window...")
        self.window_handler.activate_window('Rise of Kingdoms')
        
        print("  Waiting 3 seconds for the game to come to foreground...")
        for i in range(3, 0, -1):
            print(f"    {i}...")
            time.sleep(1)
        
        print("  Taking screenshot now!")
        screenshot, win = self.window_handler.screenshot_window('Rise of Kingdoms')
        return screenshot, win

    def capture(self):
        print("\n" + "="*60)
        print("  OSROKBOT Template Capture Tool")
        print("="*60)
        print("\nLooking for 'Rise of Kingdoms' window...")

        # Check if window exists first
        win = self.window_handler.get_window('Rise of Kingdoms')
        if win is None:
            print("\n[ERROR] Could not find 'Rise of Kingdoms' window!")
            print("Make sure the game is running.")
            return

        print(f"Found window: {win.width}x{win.height}")
        if win.width != 1280 or win.height != 720:
            print(f"[NOTE] Your resolution is {win.width}x{win.height}, not 1280x720.")
            print(f"       The bot auto-scales, but 720p templates work best.")

        # Take initial screenshot with window activation
        screenshot, win = self.take_game_screenshot()
        if screenshot is None:
            print("[ERROR] Failed to capture screenshot.")
            return

        self.screenshot_cv = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)

        while True:
            print("\n" + "-"*60)
            print("Instructions:")
            print("  1. A window will show your game screenshot")
            print("  2. Click and DRAG to select a UI element")
            print("  3. Press ENTER to confirm your selection")
            print("  4. Press 'r' to reset selection")
            print("  5. Press 'n' to take a NEW screenshot (re-activates game)")
            print("  6. Press 'q' to quit")
            print("-"*60)

            self.start_x = self.start_y = self.end_x = self.end_y = 0

            cv2.namedWindow('Select Template Region', cv2.WINDOW_NORMAL)
            
            # Resize window to fit screen
            img_h, img_w = self.screenshot_cv.shape[:2]
            scale = min(1400 / img_w, 900 / img_h, 1.0)
            cv2.resizeWindow('Select Template Region', int(img_w * scale), int(img_h * scale))
            cv2.setMouseCallback('Select Template Region', self.mouse_callback)

            while True:
                display = self.screenshot_cv.copy()
                if self.start_x != self.end_x and self.start_y != self.end_y:
                    cv2.rectangle(display,
                                  (self.start_x, self.start_y),
                                  (self.end_x, self.end_y),
                                  (0, 255, 0), 2)
                    w = abs(self.end_x - self.start_x)
                    h = abs(self.end_y - self.start_y)
                    cv2.putText(display, f"{w}x{h}", 
                                (min(self.start_x, self.end_x), min(self.start_y, self.end_y) - 10),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

                cv2.imshow('Select Template Region', display)
                key = cv2.waitKey(30) & 0xFF

                if key == ord('q'):
                    cv2.destroyAllWindows()
                    print("\nGoodbye!")
                    return
                elif key == ord('r'):
                    self.start_x = self.start_y = self.end_x = self.end_y = 0
                elif key == ord('n'):
                    # Take a fresh screenshot — minimize this window first, activate game
                    cv2.destroyAllWindows()
                    print("\n  Minimizing capture tool...")
                    time.sleep(0.5)
                    screenshot, win = self.take_game_screenshot()
                    if screenshot:
                        self.screenshot_cv = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)
                        print("  ✓ New screenshot captured!")
                    break  # Restart the outer loop to show new screenshot
                elif key == 13:  # Enter
                    # Validate selection
                    x1 = min(self.start_x, self.end_x)
                    y1 = min(self.start_y, self.end_y)
                    x2 = max(self.start_x, self.end_x)
                    y2 = max(self.start_y, self.end_y)

                    if x2 - x1 < 5 or y2 - y1 < 5:
                        print("[WARNING] Selection too small. Please try again.")
                        continue

                    cv2.destroyAllWindows()

                    cropped = self.screenshot_cv[y1:y2, x1:x2]

                    # Show preview
                    cv2.namedWindow('Preview - Press any key', cv2.WINDOW_NORMAL)
                    preview_scale = max(1.0, 200 / max(cropped.shape[:2]))
                    cv2.resizeWindow('Preview - Press any key', 
                                     int(cropped.shape[1] * preview_scale), 
                                     int(cropped.shape[0] * preview_scale))
                    cv2.imshow('Preview - Press any key', cropped)
                    cv2.waitKey(0)
                    cv2.destroyAllWindows()

                    # List existing templates
                    print("\nExisting template images in Media/:")
                    media_files = sorted([f for f in os.listdir('Media') 
                                          if f.endswith('.png') and os.path.isfile(os.path.join('Media', f))])
                    for i, f in enumerate(media_files):
                        print(f"  {f:<35s}", end="")
                        if (i + 1) % 3 == 0:
                            print()
                    print()
                    
                    filename = input("\nEnter filename (without .png, e.g., 'attackaction'): ").strip()
                    if not filename:
                        print("Skipped.")
                        continue

                    filepath = f"Media/{filename}.png"
                    
                    if os.path.exists(filepath):
                        overwrite = input(f"  '{filepath}' already exists. Overwrite? (y/n): ").strip().lower()
                        if overwrite != 'y':
                            print("Skipped.")
                            continue

                    cv2.imwrite(filepath, cropped)
                    print(f"\n  ✓ Saved: {filepath} ({x2-x1}x{y2-y1} pixels)")
                    
                    # Continue with same screenshot or take new one
                    choice = input("\n  [s] Select another region from same screenshot")
                    print("  [n] Take a new screenshot")
                    choice = input("  Choice (s/n, default=s): ").strip().lower()
                    if choice == 'n':
                        screenshot, win = self.take_game_screenshot()
                        if screenshot:
                            self.screenshot_cv = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)
                            print("  ✓ New screenshot captured!")
                    break  # Restart outer loop
            else:
                continue


if __name__ == "__main__":
    capturer = TemplateCapturer()
    capturer.capture()

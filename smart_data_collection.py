import os
import sys
import time
import cv2
import numpy as np
from PIL import Image

# Tell Python to include the 'Classes' folder in its search path
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "Classes"))

# Now we can import the classes directly
from window_handler import WindowHandler
from diagnostic_screenshot import save_diagnostic_screenshot

def is_screen_different(img1: Image.Image, img2: Image.Image, threshold: float = 2.0) -> bool:
    """
    Compares two Pillow images. Returns True if they are significantly different.
    """
    if img1 is None or img2 is None:
        return True
    
    # Convert images to numpy arrays and change to grayscale for faster, simpler comparison
    arr1 = cv2.cvtColor(np.array(img1), cv2.COLOR_RGB2GRAY)
    arr2 = cv2.cvtColor(np.array(img2), cv2.COLOR_RGB2GRAY)
    
    # --- THE FIX ---
    # If the window buffer shifted by even 1 pixel, OpenCV will crash.
    # If the shapes don't match perfectly, we count it as a screen change!
    if arr1.shape != arr2.shape:
        return True
    
    # Calculate the absolute difference between the two frames
    diff = cv2.absdiff(arr1, arr2)
    mean_diff = np.mean(diff)
    
    # If the average pixel change is greater than our threshold, the screen moved!
    return mean_diff > threshold

def capture_smart_training_data(window_title="Rise of Kingdoms", check_interval=1.0, total_images=100):
    handler = WindowHandler()
    
    print(f"Standardizing window shape for '{window_title}'...")
    handler.enforce_aspect_ratio(title=window_title)
    handler.activate_window(title=window_title)
    
    print(f"Starting Smart Capture: Waiting for screen changes...")
    
    saved_count = 0
    last_screenshot = None
    
    while saved_count < total_images:
        # Pull the frame directly from the Windows rendering buffer
        current_screenshot, _ = handler.screenshot_window(title=window_title)
        
        if current_screenshot:
            # Check if this frame is actually new compared to the last one we saved
            if is_screen_different(last_screenshot, current_screenshot, threshold=2.5):
                # Save it using your built-in artifact retention system
                filepath = save_diagnostic_screenshot(current_screenshot, label="yolo_smart_data")
                saved_count += 1
                print(f"[{saved_count}/{total_images}] Screen changed! Saved high-quality capture: {filepath}")
                
                # Update our baseline to the new frame
                last_screenshot = current_screenshot
            else:
                # The screen is basically identical. Do nothing and wait for the next check.
                pass 
        else:
            print("Failed to capture window. Is the game running?")
            
        # Wait a short moment before checking the screen again
        time.sleep(check_interval)
        
    print("Smart data collection complete. Your high-definition dataset is ready for YOLO!")

if __name__ == "__main__":
    # Ensure your game/emulator is running, then execute this script.
    capture_smart_training_data(window_title="Rise of Kingdoms", check_interval=1.0, total_images=200)
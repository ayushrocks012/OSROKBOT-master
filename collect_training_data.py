import time
from window_handler import WindowHandler
from diagnostic_screenshot import save_diagnostic_screenshot

def capture_training_images(window_title="Rise of Kingdoms", interval_seconds=5, total_images=50):
    # Initialize your advanced window handler
    handler = WindowHandler()
    
    # Step 1: Force the window to be exactly 16:9 so data is standardized
    print(f"Standardizing window shape for '{window_title}'...")
    handler.enforce_aspect_ratio(title=window_title)
    
    # Step 2: Ensure the window is ready
    handler.activate_window(title=window_title)
    
    print(f"Starting capture: {total_images} images, every {interval_seconds} seconds.")
    
    for i in range(total_images):
        # Take the screenshot using the robust Win32 API background capture
        screenshot, rect = handler.screenshot_window(title=window_title)
        
        if screenshot:
            # Step 3: Use your built-in diagnostic saver to store the file
            # This automatically generates timestamped names and saves to your 'diagnostics' folder
            filepath = save_diagnostic_screenshot(screenshot, label="yolo_training_data")
            print(f"[{i+1}/{total_images}] Saved high-quality capture: {filepath}")
        else:
            print(f"[{i+1}/{total_images}] Failed to capture window. Is the game running?")
            
        time.sleep(interval_seconds)
        
    print("Data collection complete. Upload the 'diagnostics' folder to Roboflow!")

if __name__ == "__main__":
    # Ensure your game/emulator is running, then execute this script.
    capture_training_images(window_title="Rise of Kingdoms") # Change title if using an emulator like "BlueStacks App Player"
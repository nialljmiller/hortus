import os
import time
from datetime import datetime
from picamera2 import Picamera2
import threading
import glob

# Global variables
camera_thread_running = False
camera_thread = None
picam2 = None
latest_image_path = None

def is_stable(prev_meta, curr_meta, threshold=0.05):
    """
    Compare selected metadata values between two frames.
    Returns True if all values change less than the threshold (relative difference).
    """
    # List the keys you want to check
    keys_to_check = ["ExposureTime", "AnalogGain"]
    
    for key in keys_to_check:
        if key in prev_meta and key in curr_meta:
            # Avoid division by zero in case a value is zero
            if prev_meta[key] == 0:
                continue
            print(f"{key} : {curr_meta[key]}")
            relative_change = abs(curr_meta[key] - prev_meta[key]) / prev_meta[key]
            if relative_change > threshold:
                return False
    return True

def take_picture(image_dir="/home/nill/plant_images"):
    """Take a picture with the camera and save it to the specified directory."""
    global picam2, latest_image_path
    
    try:
        # Ensure the image directory exists
        os.makedirs(image_dir, exist_ok=True)
        
        # Initialize camera if not already initialized
        if picam2 is None:
            picam2 = Picamera2()
            picam2.configure(picam2.create_still_configuration())
        
        # Start the camera
        picam2.start()

        # Apply fully automatic settings
        picam2.set_controls({
            "AeEnable": True,  # Enable Auto Exposure
            "AwbEnable": True,  # Enable Auto White Balance
            "Saturation": 1.0,  # Normal color saturation
            "Contrast": 1.0,    # Normal contrast
            "Sharpness": 1.1,   # Slightly enhance details        
        })

        time.sleep(0.5)  # Allow auto-settings to initialize

        # Get initial metadata
        metadata = picam2.capture_metadata()
        
        # Check for low light conditions using metadata
        light_level = metadata.get("Lux", 200)  # Default to bright if value missing
        
        if light_level < 100:  # Low light condition
            print("Low light detected. Adjusting settings...")
            picam2.set_controls({
                "AnalogueGain": 9.0,  # Higher gain for low light
                "Saturation": 0.0,    # Reduce saturation in low light
                "Contrast": 1.2,      # Increase contrast
                "Sharpness": 1.5,     # Increase sharpness
            })

        time.sleep(0.5)  # Allow settings to apply

        # Stabilization loop
        prev_metadata = None
        stable_count = 0
        required_stable_iterations = 3
        max_iterations = 10
        iteration = 0

        while iteration < max_iterations:
            _ = picam2.capture_array("main")  # Dummy capture to update settings
            curr_metadata = picam2.capture_metadata()

            if prev_metadata is not None:
                # Check if settings have stabilized
                if is_stable(prev_metadata, curr_metadata, threshold=0.02):  
                    stable_count += 1
                    print(f"Stability check passed {stable_count}/{required_stable_iterations}")
                else:
                    # Only reset if the fluctuation is major
                    if "ExposureTime" in prev_metadata and "ExposureTime" in curr_metadata:
                        if abs(prev_metadata["ExposureTime"] - curr_metadata["ExposureTime"]) > 5000:
                            stable_count = 0

            prev_metadata = curr_metadata
            iteration += 1

            if stable_count >= required_stable_iterations:
                print("Camera settings have stabilized.")
                break

            time.sleep(0.5)  # Waiting between stability checks

        if iteration == max_iterations:
            print("Max iterations reached; proceeding with capture regardless.")

        # Capture final image
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        image_filename = os.path.join(image_dir, f"plant_{timestamp_str}.jpg")
        picam2.capture_file(image_filename)
        
        # Create a symbolic link to the latest image
        latest_image = os.path.join(image_dir, "latest.jpg")
        if os.path.exists(latest_image):
            os.remove(latest_image)
        try:
            os.symlink(image_filename, latest_image)
        except Exception as e:
            print(f"Error creating symlink: {e}")
            
        print(f"Image captured: {image_filename}")
        latest_image_path = image_filename
        
        # Stop the camera
        picam2.stop()
        return image_filename
    except Exception as e:
        print(f"Error capturing image: {e}")
        try:
            if picam2 is not None:
                picam2.stop()
        except:
            pass
        return None

def get_latest_image():
    """Return the path to the latest captured image."""
    global latest_image_path
    return latest_image_path

def camera_thread_function(image_dir, interval_minutes=10):
    """Background thread to take pictures periodically."""
    global camera_thread_running
    
    while camera_thread_running:
        try:
            capture_successful = take_picture(image_dir)
            if not capture_successful:
                print("Failed to capture image, will retry later.")
        except Exception as e:
            print(f"Exception in camera thread: {e}")
        
        # Sleep for the specified interval
        for _ in range(interval_minutes * 60):
            if not camera_thread_running:
                break
            time.sleep(1)

def start_camera_control(image_dir="/home/nill/plant_images", interval_minutes=10):
    """Start the camera control in a background thread."""
    global camera_thread_running, camera_thread
    
    if not camera_thread_running:
        camera_thread_running = True
        camera_thread = threading.Thread(target=camera_thread_function, args=(image_dir, interval_minutes))
        camera_thread.daemon = True  # Thread will exit when main program exits
        camera_thread.start()
        print(f"Camera control system started with {interval_minutes} minute interval!")
        return True
    else:
        print("Camera control is already running.")
        return False

def stop_camera_control():
    """Stop the camera control thread."""
    global camera_thread_running, picam2
    
    if camera_thread_running:
        camera_thread_running = False
        if picam2 is not None:
            try:
                picam2.stop()
            except:
                pass
            picam2 = None
        print("Camera control system stopped!")
        return True
    else:
        print("Camera control is not running.")
        return False

def cleanup_old_images(image_dir="/home/nill/plant_images", keep_last=100):
    """Delete old images, keeping only the specified number of most recent ones."""
    try:
        image_files = sorted(glob.glob(f"{image_dir}/plant_*.jpg"))
        if len(image_files) > keep_last:
            for old_file in image_files[:-keep_last]:
                try:
                    os.remove(old_file)
                    print(f"Removed old image: {old_file}")
                except Exception as e:
                    print(f"Error removing old image {old_file}: {e}")
            return len(image_files) - keep_last  # Return number of deleted files
        return 0
    except Exception as e:
        print(f"Error cleaning up images: {e}")
        return -1  # Error occurred

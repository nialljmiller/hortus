import glob
import numpy as np
import time
import board
import busio
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn
from adafruit_bme280 import basic as adafruit_bme280
import csv
import os
from datetime import datetime
import subprocess
import sys
import psutil
import RPi.GPIO as GPIO
# Import camera library
from picamera2 import Picamera2
import heater_control
import gc  # Garbage collection

# Functions for system monitoring
def get_cpu_temp():
    try:
        with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
            return int(f.read()) / 1000
    except FileNotFoundError:
        return "Unavailable"

def get_cpu_usage():
    return psutil.cpu_percent(interval=1)

def get_memory_usage():
    return psutil.virtual_memory().percent


def read_sensor(sensor_index):
    """Turn on a specific sensor, wait for stabilization, read its value, then turn it off."""
    # Define the sensors inline
    sensors = [
        AnalogIn(ads, ADS.P0),
        AnalogIn(ads, ADS.P1),
        AnalogIn(ads, ADS.P2),
        AnalogIn(ads, ADS.P3),
    ]
    sensor = sensors[sensor_index]  # Get the current sensor

    # Power on the specific sensor
    for i, pin in enumerate(SENSOR_POWER_PINS):
        GPIO.output(pin, GPIO.HIGH if i == sensor_index else GPIO.LOW)

    time.sleep(0.1)  # Add stabilization time
    value = sensor.value  # Read the sensor
    GPIO.output(SENSOR_POWER_PINS[sensor_index], GPIO.LOW)  # Turn it off
    return value

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

def take_picture():
    """Take a picture with the camera and save it to the specified directory."""
    try:
        # Initialize the camera
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
        picam2.stop()
        return image_filename
    except Exception as e:
        print(f"Error capturing image: {e}")
        try:
            picam2.stop()
        except:
            pass
        return None


# Function to log sensor data
def makedata(sample_duration=1, sample_interval=0.1):
    soil_moistures = [[], [], [], []]  # Lists for 4 soil moisture sensors
    temperatures = []
    humidities = []
    pressures = []
    cpu_temps = []
    cpu_usages = []
    memory_usages = []

    end_time = time.time() + sample_duration
    while time.time() < end_time:
        try:
            # Read soil moisture sensors
            for i in range(4):
                soil_moistures[i].append(read_sensor(i))

            # Read environmental data from BME280
            temperature = bme280.temperature
            humidity = bme280.humidity
            pressure = bme280.pressure
            heater_control.update_temperature(temperature)
            
            # Read system performance metrics
            cpu_temp = get_cpu_temp()
            cpu_usage = get_cpu_usage()
            memory_usage = get_memory_usage()

            # Append readings to respective lists
            temperatures.append(temperature)
            humidities.append(humidity)
            pressures.append(pressure)
            cpu_temps.append(cpu_temp)
            cpu_usages.append(cpu_usage)
            memory_usages.append(memory_usage)

        except Exception as e:
            print(f"Error reading sensor: {e}")
            time.sleep(sample_interval)
            continue

        time.sleep(sample_interval)

    # Calculate median values
    if soil_moistures[0]:
        median_soil = [np.median(soil) for soil in soil_moistures]
        median_temp = np.median(temperatures)
        median_humidity = np.median(humidities)
        median_pressure = np.median(pressures)
        median_cpu_temp = np.median(cpu_temps)
        median_cpu_usage = np.median(cpu_usages)
        median_memory_usage = np.median(memory_usages)
    else:
        print("No samples collected!")
        return None

    timestamp = datetime.now()
    # Log sensor data locally
    with open(local_csv, "a", newline="") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow([timestamp, *median_soil, median_temp, median_humidity, median_pressure])

    with open(system_csv_file, mode='a', newline='') as file:
        writer = csv.writer(file)
        writer.writerow([timestamp, median_cpu_temp, median_cpu_usage, median_memory_usage])

    print("\n\t-----------------------------------------")
    print(f"\tData logged at {timestamp}")
    for i, moisture in enumerate(median_soil):
        print(f"\tSoil Moisture Sensor {i + 1}: {moisture}")
    print(f"\tTemperature: {median_temp:.2f} °C, Humidity: {median_humidity:.2f} %, Pressure: {median_pressure:.2f} hPa")
    print(f"\tCPU Temperature: {median_cpu_temp}°C, CPU Usage: {median_cpu_usage}%, Memory Usage: {median_memory_usage}%")
    print("\t-----------------------------------------\n")

# Function to transfer data
def send_data():
    print("Taking picture and transferring data to the server...")
    try:
        # Take a picture first
        image_file = take_picture()
        
        # Set up retry mechanism and bandwidth control
        max_retries = 3
        bandwidth_limit = "500"  # Bandwidth limit in Kbps
        connection_timeout = "10"  # Connection timeout in seconds
        
        # Transfer the image if capture was successful
        if image_file and os.path.exists(image_file):
            for attempt in range(1, max_retries + 1):
                try:
                    print(f"Transferring image file {image_file} to server (attempt {attempt}/{max_retries})...")
                    subprocess.run(
                        [
                            "scp", "-v", "-l", bandwidth_limit,
                            "-o", f"ConnectTimeout={connection_timeout}",
                            image_file, f"{server_address}:{server_image_dir}"
                        ],
                        check=True
                    )
                    print(f"Image file successfully transferred on attempt {attempt}.")
                    
                    break
                except subprocess.CalledProcessError as e:
                    print(f"Error during image transfer attempt {attempt}: {e}")
                    if attempt < max_retries:
                        print("Retrying image transfer...")
                        time.sleep(2)  # Wait before retrying
                    else:
                        print(f"All {max_retries} attempts failed for image transfer.")
        
        # Transfer sensor data CSV
        for attempt in range(1, max_retries + 1):
            try:
                print(f"Transferring plant data to server (attempt {attempt}/{max_retries})...")
                subprocess.run(
                    [
                        "scp", "-v", "-l", bandwidth_limit,
                        "-o", f"ConnectTimeout={connection_timeout}",
                        local_csv, f"{server_address}:{server_csv_path}"
                    ],
                    check=True
                )
                print(f"Plant data successfully transferred on attempt {attempt}.")
                break
            except subprocess.CalledProcessError as e:
                print(f"Error during plant data transfer attempt {attempt}: {e}")
                if attempt < max_retries:
                    print("Retrying plant data transfer...")
                    time.sleep(2)
                else:
                    print(f"All {max_retries} attempts failed for plant data transfer.")
        
        # Transfer system data CSV
        for attempt in range(1, max_retries + 1):
            try:
                print(f"Transferring system data to server (attempt {attempt}/{max_retries})...")
                subprocess.run(
                    [
                        "scp", "-v", "-l", bandwidth_limit,
                        "-o", f"ConnectTimeout={connection_timeout}",
                        system_csv_file, f"{server_address}:{system_server_csv_path}"
                    ],
                    check=True
                )
                print(f"System data successfully transferred on attempt {attempt}.")
                break
            except subprocess.CalledProcessError as e:
                print(f"Error during system data transfer attempt {attempt}: {e}")
                if attempt < max_retries:
                    print("Retrying system data transfer...")
                    time.sleep(2)
                else:
                    print(f"All {max_retries} attempts failed for system data transfer.")
        
        print("All data successfully transferred to the server.")
    except Exception as e:
        print(f"Error in data transfer process: {e}")

# Function to delete local data
def del_data():
    print("Cleaning up local data...")
    
    # Clear local CSV files
    with open(local_csv, "w", newline="") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["Timestamp", "Soil_Moisture_1", "Soil_Moisture_2", "Soil_Moisture_3", "Soil_Moisture_4",
                         "Temperature_C", "Humidity_percent", "Pressure_hPa"])

    with open(system_csv_file, mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(["Timestamp", "CPU_Temperature_C", "CPU_Usage_percent", "Memory_Usage_percent"])
    
    # Cleanup old images but keep the latest 100
    try:
        image_files = sorted(glob.glob(f"{image_dir}/plant_*.jpg"))
        if len(image_files) > 100:
            for old_file in image_files[:-100]:
                try:
                    os.remove(old_file)
                    print(f"Removed old image: {old_file}")
                except Exception as e:
                    print(f"Error removing old image {old_file}: {e}")
    except Exception as e:
        print(f"Error cleaning up images: {e}")

    print("Local data cleared.")

# GPIO pins for sensor power
SENSOR_POWER_PINS = [17, 27, 22, 23]

# Initialize GPIO
GPIO.setmode(GPIO.BCM)
for pin in SENSOR_POWER_PINS:
    GPIO.setup(pin, GPIO.OUT)
    GPIO.output(pin, GPIO.LOW)  # Turn off all sensors initially

# Initialize sensors
i2c = busio.I2C(board.SCL, board.SDA)
ads = ADS.ADS1115(i2c)
bme280 = adafruit_bme280.Adafruit_BME280_I2C(i2c, address=0x76)
heater_control.start_heater_control()

# Initialize camera
picam2 = Picamera2()
# Default configuration
picam2.configure(picam2.create_still_configuration())

# File paths
BASE_DIR = "/home/nill"
local_csv = f"{BASE_DIR}/plant_data.csv"
system_csv_file = f"{BASE_DIR}/system_data.csv"
image_dir = f"{BASE_DIR}/plant_images"
server_address = "nill@nillmill.ddns.net"
server_csv_path = "/media/bigdata/plant_station/plant_data.csv"
system_server_csv_path = "/media/bigdata/plant_station/plant_system_data.csv"
server_image_dir = "/media/bigdata/plant_station/images"

# Ensure directories exist
os.makedirs(image_dir, exist_ok=True)

# Ensure local CSV files exist
if not os.path.exists(local_csv):
    with open(local_csv, "w", newline="") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["Timestamp", "Soil_Moisture_1", "Soil_Moisture_2", "Soil_Moisture_3", "Soil_Moisture_4",
                         "Temperature_C", "Humidity_percent", "Pressure_hPa"])

if not os.path.exists(system_csv_file):
    with open(system_csv_file, mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(["Timestamp", "CPU_Temperature_C", "CPU_Usage_percent", "Memory_Usage_percent"])

print("Plant Monitoring System Initialized!\n")


# Main loop with memory management
send_counter = 0
memory_threshold = 75  # Set a memory threshold percentage
while True:
    try:
        # Check memory usage before proceeding
        current_memory = get_memory_usage()
        if current_memory > memory_threshold:
            print(f"WARNING: High memory usage detected: {current_memory}%. Performing cleanup...")
            gc.collect()  # Force garbage collection
            # If still high, restart the process
            if get_memory_usage() > memory_threshold:
                print("Memory still high after cleanup. Restarting process...")
                # Optional: Save state before exit
                os.execv(sys.executable, ['python'] + sys.argv)
        
        makedata()
        send_counter = (send_counter + 1) % 600  # Use modulo to avoid unbounded growth
        
        if send_counter % 10 == 0:
            send_data()
        if send_counter == 0:  # This will happen when it reaches 600
            del_data()
            
        # Force garbage collection after operations
        gc.collect()
        
    except Exception as e:
        print(f"Unexpected error: {e}")
        # Clean up GPIO before exiting
        GPIO.cleanup()
        break

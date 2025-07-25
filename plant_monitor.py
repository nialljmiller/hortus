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
import gc  # Garbage collection
import logging

# Logging configuration
LOG_FILE = "/home/nill/plant_monitor.log"
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

# System thresholds
MEMORY_THRESHOLD = 75  # percent
CPU_TEMP_THRESHOLD = 80  # Celsius

# Import custom modules
import heater_control
import camera_control

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


def shutdown():
    """Gracefully stop all subsystems and clean up GPIO."""
    logging.info("Initiating shutdown sequence")
    try:
        heater_control.stop_heater_control()
        camera_control.stop_camera_control()
    finally:
        GPIO.cleanup()
    logging.info("Shutdown complete")


def check_system_resources():
    """Monitor system resources and act on critical conditions."""
    cpu_temp = get_cpu_temp()
    if cpu_temp != "Unavailable" and cpu_temp > CPU_TEMP_THRESHOLD:
        warning = f"High CPU temperature detected: {cpu_temp}°C"
        print(f"WARNING: {warning}")
        logging.warning(warning)
        heater_control.stop_heater_control()
        camera_control.stop_camera_control()
        time.sleep(30)
        cpu_temp = get_cpu_temp()
        if cpu_temp > CPU_TEMP_THRESHOLD:
            logging.error("CPU temperature critical after cooldown, restarting")
            shutdown()
            os.execv(sys.executable, ['python'] + sys.argv)
        else:
            logging.info("CPU temperature normalized, restarting subsystems")
            heater_control.start_heater_control()
            camera_control.start_camera_control(image_dir, interval_minutes=CAMERA_INTERVAL)


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
        # Use the most recent image from the background camera thread
        image_file = camera_control.get_latest_image()
        if not image_file or not os.path.exists(image_file):
            # Fallback to capture a new image if none is available
            image_file = camera_control.take_picture(image_dir)
        
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
                        check=True,
                        timeout=60
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
        else:
            print("No image file captured or file doesn't exist. Skipping image transfer.")
        
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
                    check=True,
                    timeout=60
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
                    check=True,
                    timeout=60
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
    
    # Cleanup old images using the camera_control module
    deleted_count = camera_control.cleanup_old_images(image_dir, keep_last=100)
    if deleted_count > 0:
        print(f"Cleaned up {deleted_count} old images.")
    elif deleted_count == 0:
        print("No old images needed to be cleaned up.")
    else:
        print("Error occurred during image cleanup.")

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

# Start heater control
heater_control.start_heater_control()

# File paths
BASE_DIR = "/home/nill"
local_csv = f"{BASE_DIR}/plant_data.csv"
system_csv_file = f"{BASE_DIR}/system_data.csv"
image_dir = f"{BASE_DIR}/plant_images"
server_address = "nill@nillmill.ddns.net"
server_csv_path = "/media/bigdata/plant_station/plant_data.csv"
system_server_csv_path = "/media/bigdata/plant_station/plant_system_data.csv"
server_image_dir = "/media/bigdata/plant_station/images"

# Camera capture interval in minutes
CAMERA_INTERVAL = 10

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
logging.info("Plant Monitoring System Initialized")

# Main loop with memory and temperature management
send_counter = 0

try:
    # Start the camera control module (takes pictures in background thread)
    camera_control.start_camera_control(image_dir, interval_minutes=CAMERA_INTERVAL)
    
    while True:
        try:
            # Check memory usage before proceeding
            current_memory = get_memory_usage()
            if current_memory > MEMORY_THRESHOLD:
                warning = (
                    f"High memory usage detected: {current_memory}%"
                )
                print(f"WARNING: {warning} Performing cleanup...")
                logging.warning(warning)
                gc.collect()  # Force garbage collection
                # If still high, restart the process
                if get_memory_usage() > MEMORY_THRESHOLD:
                    logging.error("Memory still high after cleanup. Restarting process")
                    print("Memory still high after cleanup. Restarting process...")
                    # Stop modules before exiting
                    heater_control.stop_heater_control()
                    camera_control.stop_camera_control()
                    # Optional: Save state before exit
                    os.execv(sys.executable, ['python'] + sys.argv)

            check_system_resources()
            
            makedata()
            send_counter = (send_counter + 1) % 600  # Use modulo to avoid unbounded growth
            
            if send_counter % 10 == 0:
                send_data()
            if send_counter == 0:  # This will happen when it reaches 600
                del_data()
                
            # Force garbage collection after operations
            gc.collect()
            time.sleep(6)
            
        except Exception as e:
            print(f"Unexpected error in main loop: {e}")
            time.sleep(6)  # Wait a bit before retrying
            
except KeyboardInterrupt:
    print("Program interrupted by user. Cleaning up...")
except Exception as e:
    print(f"Critical error: {e}")
finally:
    # Clean up resources
    print("Shutting down plant monitoring system...")
    shutdown()
    print("Plant monitoring system shutdown complete.")

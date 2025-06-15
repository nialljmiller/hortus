import asyncio
import time
from kasa import SmartPlug
import threading

# Configuration for heater control
HEATER_IP = "192.168.0.147"  # Your Kasa plug IP
TEMP_THRESHOLD = 10.0        # Temperature threshold in Celsius
HEATER_ON_TIME = 120         # 2 minutes (in seconds)
HEATER_OFF_TIME = 60         # 1 minute (in seconds)

# Global variable to store current temperature
current_temperature = None
heater_thread_running = False

async def control_heater(turn_on):
    """Control the heater plug"""
    try:
        plug = SmartPlug(HEATER_IP)
        await plug.update()
        
        if turn_on and not plug.is_on:
            await plug.turn_on()
            print(f"Heater turned ON - Temperature: {current_temperature:.2f}°C")
        elif not turn_on and plug.is_on:
            await plug.turn_off()
            print(f"Heater turned OFF - Temperature: {current_temperature:.2f}°C")
    except Exception as e:
        print(f"Error controlling heater: {e}")

def heater_control_loop():
    """Background thread to control the heater based on temperature"""
    global heater_thread_running
    
    while heater_thread_running:
        if current_temperature is not None:
            if current_temperature < TEMP_THRESHOLD:
                # Temperature below threshold, activate duty cycle
                asyncio.run(control_heater(True))  # Turn on
                time.sleep(HEATER_ON_TIME)         # Stay on for 2 minutes
                
                # Check if we should continue the cycle
                if heater_thread_running and current_temperature < TEMP_THRESHOLD:
                    asyncio.run(control_heater(False))  # Turn off
                    time.sleep(HEATER_OFF_TIME)         # Stay off for 1 minute
            else:
                # Temperature above threshold, ensure heater is off
                asyncio.run(control_heater(False))
                time.sleep(10)  # Check again in 10 seconds
        else:
            # No temperature reading yet
            time.sleep(5)

def start_heater_control():
    """Start the heater control in a background thread"""
    global heater_thread_running
    
    if not heater_thread_running:
        heater_thread_running = True
        heater_thread = threading.Thread(target=heater_control_loop)
        heater_thread.daemon = True  # Thread will exit when main program exits
        heater_thread.start()
        print("Heater control system started!")

def stop_heater_control():
    """Stop the heater control thread"""
    global heater_thread_running
    heater_thread_running = False
    print("Heater control system stopped!")

def update_temperature(new_temp):
    """Update the current temperature (called from your main script)"""
    global current_temperature
    current_temperature = new_temp

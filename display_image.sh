#!/bin/bash

# Set display environment
export DISPLAY=:0
export XAUTHORITY=/home/nill/.Xauthority  # Ensure this is your actual home directory

# SSH details
REMOTE_USER="nill"
REMOTE_HOST="nillmill.ddns.net"
REMOTE_IMAGE_PATH="/media/bigdata/plant_station/last_24h_plant_plot.png"
LOCAL_IMAGE_PATH="/tmp/current_image.png"

# Fetch the latest image and update every few minutes
while true; do
    pkill feh  # Kill any existing feh instance before launching a new one
    scp "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_IMAGE_PATH}" "$LOCAL_IMAGE_PATH"
    feh --fullscreen --hide-pointer --reload 10 "$LOCAL_IMAGE_PATH" &
    sleep 210  # Adjust refresh rate as needed
done

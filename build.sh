#!/usr/bin/env bash
# exit on error
set -o errexit

# Create a local bin directory if it doesn't exist
mkdir -p /opt/render/project/src/bin

# Download static FFmpeg binary if not already present
if [ ! -f /opt/render/project/src/bin/ffmpeg ]; then
  echo "Downloading FFmpeg..."
  wget https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz
  tar -xf ffmpeg-release-amd64-static.tar.xz
  # Move ffmpeg and ffprobe to our local bin
  mv ffmpeg-*-amd64-static/ffmpeg /opt/render/project/src/bin/
  mv ffmpeg-*-amd64-static/ffprobe /opt/render/project/src/bin/
  # Clean up download files
  rm -rf ffmpeg-*-amd64-static*
fi

# Install python dependencies
pip install -r requirements.txt

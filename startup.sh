#!/bin/bash
# This script runs before the app starts to make sure everything is set up correctly

# Print environment info for debugging
echo "Environment:"
echo "PORT: $PORT"
echo "HOME: $HOME"
echo "PWD: $(pwd)"
echo "Python: $(which python)"
echo "Gunicorn: $(which gunicorn)"

# List all files in the current directory
echo "Files in app directory:"
ls -la

# Make sure app.py exists
if [ ! -f "app.py" ]; then
  echo "ERROR: app.py not found!"
  exit 1
fi

# Verify app.py contains Flask app
if ! grep -q "app = Flask" app.py; then
  echo "ERROR: Flask app not defined in app.py"
  exit 1
fi

echo "All checks passed. Starting application..."
exec "$@"

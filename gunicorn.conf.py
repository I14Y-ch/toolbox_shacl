import os

# Get port from environment
port = os.environ.get("PORT", "8080")

# Bind to the correct address and port
bind = f"0.0.0.0:{port}"

# Worker options
workers = 2
worker_class = "sync"
worker_tmp_dir = "/dev/shm"

# Logging
accesslog = "-"
errorlog = "-"
loglevel = "info"

# Timeout settings
timeout = 120
keepalive = 5

# Preload app for better performance
preload_app = True

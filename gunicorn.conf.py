import os

port = os.environ.get('PORT', '8080')
bind = f'0.0.0.0:{port}'

workers = int(os.environ.get('WEB_CONCURRENCY', '2'))
worker_class = 'sync'
worker_tmp_dir = '/dev/shm'  # nosec B108

accesslog = '-'
errorlog = '-'
loglevel = 'info'

timeout = 45
graceful_timeout = 15
keepalive = 5
max_requests = 500
max_requests_jitter = 50
limit_request_fields = 50
limit_request_field_size = 8190

preload_app = True
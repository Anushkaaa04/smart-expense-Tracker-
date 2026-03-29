import multiprocessing
import os

# Workers = (2 x CPU cores) + 1 — optimal for I/O bound apps
workers = int(os.environ.get('WEB_CONCURRENCY', multiprocessing.cpu_count() * 2 + 1))
worker_class = 'gthread'       # threaded workers handle more concurrent requests
threads = 4                    # threads per worker
timeout = 120
keepalive = 5
max_requests = 1000            # restart workers after 1000 requests (prevents memory leaks)
max_requests_jitter = 100
bind = '0.0.0.0:' + os.environ.get('PORT', '8000')
accesslog = '-'
errorlog = '-'
loglevel = 'info'

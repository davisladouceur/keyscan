"""
Celery application configuration.

The Celery worker runs image analysis off the main FastAPI request thread,
preventing HTTP timeouts on slow images.
"""

import os

from celery import Celery

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "keyscan",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["api.analyze_task"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    task_acks_late=True,          # Re-queue on worker crash
    worker_prefetch_multiplier=1,  # One task at a time per worker (image analysis is heavy)
    result_expires=86400,          # Results expire after 24h
)

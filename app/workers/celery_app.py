"""Celery application — broker and result backend are both Redis (settings.redis_url).

WHY Celery over FastAPI BackgroundTasks: tasks persist in the broker (they survive an API
crash), retry automatically, and run in separate worker processes so slow work — invoice PDF
generation, email/SMS, inventory alerts — never blocks the API response. Each task lives in its
own module (see invoice_worker / notification_worker / inventory_worker) so they can be scaled
and monitored independently via Flower.
"""
from celery import Celery

from ..config import settings

celery_app = Celery(
    "order_management",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "app.workers.invoice_worker",
        "app.workers.notification_worker",
        "app.workers.inventory_worker",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_acks_late=True,               # ack AFTER the task finishes -> a crashed worker requeues it
    task_reject_on_worker_lost=True,   # requeue if the worker process dies mid-task
    worker_prefetch_multiplier=1,      # one task at a time per process -> fair dispatch for long tasks
)

"""Notification worker — email/SMS a customer on order status changes.

WHY a separate Celery task: external notification providers (SendGrid/Twilio) are slow and
flaky; Celery retries ensure a notification is eventually delivered rather than lost. Triggered
on PAID and CANCELLED status changes (see routers/orders.py).
"""
import logging
import time

from .celery_app import celery_app

logger = logging.getLogger(__name__)

# Demo-only: simulated work duration (see invoice_worker.SIMULATED_DURATION_SEC).
SIMULATED_DURATION_SEC = 10


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    name="workers.send_order_notification",
)
def send_order_notification(self, user_email: str, order_id: int, status: str) -> None:
    """Send email/SMS notification when order status changes.

    WHY: external notification services (email/SMS) can be slow and fail.
    Celery retry ensures notifications are eventually delivered.
    Triggered on: PAID, CANCELLED status changes.
    """
    try:
        logger.info(
            "Sending notification: order_id=%s status=%s to=%s", order_id, status, user_email
        )
        # Simulate notification (replace with real email/SMS service: SendGrid, Twilio).
        time.sleep(SIMULATED_DURATION_SEC)
        logger.info("Notification sent: order_id=%s status=%s", order_id, status)
    except Exception as exc:
        logger.error("Notification failed: order_id=%s error=%s", order_id, exc)
        raise self.retry(exc=exc)

"""Inventory worker — alert admins when a product drops to low stock after a payment.

WHY a separate Celery task: admins need to know when to restock; polling stock levels doesn't
scale, so we push an alert at the moment stock is reduced (on successful payment). The task is
independent of invoice/notification — one failing doesn't affect the others.
"""
import logging
import time

from .celery_app import celery_app

logger = logging.getLogger(__name__)

# Threshold below which an admin should be alerted to restock. Configurable.
LOW_STOCK_THRESHOLD = 5

# Demo-only: simulated work duration (see invoice_worker.SIMULATED_DURATION_SEC).
SIMULATED_DURATION_SEC = 15


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="workers.check_low_stock",
)
def check_low_stock(self, product_id: int, product_name: str, current_stock: int) -> None:
    """Alert admin when product stock falls at/below threshold after payment.

    WHY: admin needs to know when to restock — manual checking doesn't scale.
    Triggered only when stock reduces (on successful payment).
    Threshold: LOW_STOCK_THRESHOLD units (configurable).
    """
    try:
        # Simulate the work of checking stock / dispatching an alert (replace with a real
        # lookup + email). Demo delay so the task is visible in Flower/logs.
        time.sleep(SIMULATED_DURATION_SEC)
        if current_stock <= LOW_STOCK_THRESHOLD:
            logger.warning(
                "LOW STOCK ALERT: product_id=%s name=%s stock=%s threshold=%s",
                product_id,
                product_name,
                current_stock,
                LOW_STOCK_THRESHOLD,
            )
            # In production: send an email to admin (SendGrid/SES). For now: log (Flower + logs).
    except Exception as exc:
        logger.error("Inventory alert failed: product_id=%s error=%s", product_id, exc)
        raise self.retry(exc=exc)

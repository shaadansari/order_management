"""Invoice worker — generate an invoice after a successful payment.

WHY a separate Celery task: PDF generation is slow; running it async keeps the API response
immediate, and Celery's retry gives us durability BackgroundTasks can't — a transient failure is
retried 3x with a 60s back-off instead of being lost.
"""
import logging
import time

from .celery_app import celery_app

logger = logging.getLogger(__name__)

# Demo-only: how long the simulated work takes. Real tasks spend this time on actual work
# (PDF render, SMTP call, ...). Kept at 5s here so the task is plainly visible in Flower and
# the worker logs instead of flashing by in a few ms. Tune or remove for real work.
SIMULATED_DURATION_SEC = 5


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="workers.generate_invoice",
)
def generate_invoice(self, order_id: int) -> None:
    """Generate invoice after successful payment.

    WHY: PDF generation is slow — runs async so the API response is immediate.
    Retries 3 times on failure with a 60s delay.
    """
    try:
        logger.info("Generating invoice: order_id=%s", order_id)
        # Simulate invoice generation (replace with real PDF logic + storage upload).
        time.sleep(SIMULATED_DURATION_SEC)
        logger.info("Invoice generated: order_id=%s", order_id)
    except Exception as exc:
        logger.error("Invoice failed: order_id=%s error=%s", order_id, exc)
        raise self.retry(exc=exc)

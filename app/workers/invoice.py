

"""Background tasks that run AFTER a successful payment.

WHY these are background tasks and not inline: invoice generation and email are not
part of the critical payment path. Running them after the response is sent keeps
POST /orders/{id}/pay fast and non-blocking.

Production upgrade: replace FastAPI BackgroundTasks with a durable queue
(RabbitMQ + Celery) so failed jobs retry automatically and survive restarts.
"""
import logging
from pathlib import Path

from ..database import SessionLocal
from ..models import Order

logger = logging.getLogger(__name__)

# In dev we write invoices to a local folder. Production would use object storage (S3).
INVOICE_DIR = Path("invoices")


def generate_invoice(order_id: int) -> str | None:
    """Create a simple text invoice for the order. Opens its OWN DB session.

    WHY a fresh session + order_id argument: this runs after the request's session has
    closed, so we cannot reuse a detached ORM object. We re-open a session, load the
    order fresh, and close it when done.
    """
    db = SessionLocal()
    try:
        order = db.query(Order).filter(Order.id == order_id).first()
        if not order:
            logger.warning("generate_invoice: order %s not found", order_id)
            return None

        INVOICE_DIR.mkdir(exist_ok=True)
        path = INVOICE_DIR / f"invoice_{order.id}.txt"
        lines = [
            f"INVOICE #{order.id}",
            f"Status: {order.status}",
            f"Total: {order.total_amount}",
            "",
            "Items:",
        ]
        for item in order.items:
            lines.append(
                f"  - product_id={item.product_id}  qty={item.quantity}  unit_price={item.unit_price}"
            )
        path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("Invoice generated for order %s -> %s", order.id, path)
        return str(path)
    except Exception:
        # A background task must NEVER crash the app — log and swallow.
        logger.exception("Failed to generate invoice for order %s", order_id)
        return None
    finally:
        db.close()


def send_email_notification(customer_email: str, order_id: int) -> None:
    """Send an order-confirmation email. Stubbed here (no real SMTP)."""
    try:
        # Real impl would call an ESP (SES / SendGrid / Postmark).
        logger.info(
            "[email-stub] Order confirmation -> %s for order %s", customer_email, order_id
        )
    except Exception:
        logger.exception("Failed to send email for order %s", order_id)

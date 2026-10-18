"""Turn a cart into an order and pay for it (synthetic demo code)."""

from services.payments_api.capture import capture


def place_order(cart: dict) -> dict:
    total = sum(item["price_cents"] * item["qty"] for item in cart["items"])
    payment = capture({"amount": total / 100, "customer": cart["customer"]})
    return {"order_total_cents": total, "payment": payment}

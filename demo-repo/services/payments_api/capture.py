"""Authorise and capture a card payment (synthetic demo code)."""

from services.payments_api import fraud_client

BLOCK_ABOVE = 0.8


def capture(payment: dict) -> dict:
    risk = fraud_client.score(payment)
    if risk > BLOCK_ABOVE:
        return {"status": "declined", "reason": "fraud", "risk": risk}
    return {"status": "captured", "risk": risk}

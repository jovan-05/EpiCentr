"""Client for fraud-svc: scores a payment inline, before capture (ADR-017).

Synthetic demo code for Keystone's decision guard. Never deployed anywhere.
"""

import time

from services.payments_api import http

FRAUD_URL = "http://fraud-svc.internal/v2/score"
TIMEOUT_S = 0.3   # 300 ms budget for the inline fraud check
RETRIES = 2


def rules_only_score(payment: dict) -> float:
    """Fallback when fraud-svc is slow: a cheap rules-only verdict, re-scored asynchronously later."""
    return 0.9 if payment.get("amount", 0) > 5_000 else 0.1


def score(payment: dict) -> float:
    """Fraud score between 0 (fine) and 1 (block). Never waits more than ~1 s in total."""
    for attempt in range(RETRIES + 1):
        try:
            resp = http.post(FRAUD_URL, json=payment, timeout=TIMEOUT_S)
            return resp.json()["score"]
        except http.Timeout:
            if attempt == RETRIES:
                return rules_only_score(payment)
            time.sleep(0.05 * (attempt + 1))
    return rules_only_score(payment)

"""Write merchant payouts to the ledger (synthetic demo code).

Every payout write carries an idempotency key.
"""

_processed: set[str] = set()


def already_processed(idempotency_key: str) -> bool:
    return idempotency_key in _processed


def write_payout(merchant_id: str, amount_cents: int, idempotency_key: str) -> dict:
    if already_processed(idempotency_key):
        return {"status": "duplicate_ignored", "idempotency_key": idempotency_key}
    _processed.add(idempotency_key)
    return {"status": "written", "merchant": merchant_id, "amount_cents": amount_cents}

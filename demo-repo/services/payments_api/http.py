"""Tiny stand-in for an HTTP client (synthetic demo code)."""


class Timeout(Exception):
    pass


class Response:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


def post(url: str, json: dict, timeout: float | None = None) -> Response:
    return Response({"score": 0.1, "url": url, "timeout": timeout, "payload": json})

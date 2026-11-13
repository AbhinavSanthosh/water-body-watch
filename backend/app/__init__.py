"""Water Body Watch backend.

Use the operating system trust store so local development works behind the
corporate TLS proxies that are common on municipal networks. This is a no-op
on platforms where truststore is unavailable.
"""

try:  # pragma: no cover - environment dependent
    import truststore

    truststore.inject_into_ssl()
except Exception:  # noqa: BLE001
    pass

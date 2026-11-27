"""HTTP transport shared by the provider adapters: clients, timeouts, the
request deadline and rate-limit headers. The proxy routing they share with
every other managed client is ``host.security.http_proxy``.
"""

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest
from sqlalchemy import text


class Metrics:
    def __init__(self):
        self.registry = CollectorRegistry()
        self.requests = Counter(
            "studyhub_http_requests_total",
            "Completed HTTP requests",
            ["method", "route", "status"],
            registry=self.registry,
        )
        self.duration = Histogram(
            "studyhub_http_duration_seconds",
            "HTTP latency",
            ["method", "route"],
            registry=self.registry,
        )
        self.dependencies = Gauge(
            "studyhub_dependency_up",
            "Dependency connectivity",
            ["dependency"],
            registry=self.registry,
        )
        self.outbox_pending = Gauge(
            "studyhub_outbox_pending", "Undelivered events", registry=self.registry
        )
        self.outbox_age = Gauge(
            "studyhub_outbox_oldest_age_seconds",
            "Age of oldest undelivered event",
            registry=self.registry,
        )
        self.outbox_retries = Gauge(
            "studyhub_outbox_retries",
            "Accumulated attempts of pending events",
            registry=self.registry,
        )
        self.dispatcher = Gauge(
            "studyhub_dispatcher_last_success_timestamp_seconds",
            "End-to-end scheduler/worker heartbeat",
            registry=self.registry,
        )
        self.websockets = Gauge(
            "studyhub_websocket_connections",
            "Connections on this API process",
            registry=self.registry,
        )

    def observe(self, method, route, status, duration):
        method = (
            method
            if method in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}
            else "OTHER"
        )
        self.requests.labels(method, route, str(status)).inc()
        self.duration.labels(method, route).observe(duration)

    async def render(self, state):
        # Aggregate queries; no school/user IDs are exported as metric labels.
        try:
            async with state.engine.connect() as connection:
                row = (
                    await connection.execute(
                        text(
                            "SELECT count(*), COALESCE(EXTRACT(EPOCH FROM now()-min(created_at)),0), COALESCE(sum(attempts),0) FROM outbox WHERE delivered_at IS NULL"
                        )
                    )
                ).one()
            self.outbox_pending.set(row[0])
            self.outbox_age.set(row[1])
            self.outbox_retries.set(row[2])
            self.dependencies.labels("database").set(1)
        except Exception:
            self.dependencies.labels("database").set(0)
        for name, client in (
            ("control", state.control),
            ("broker", state.broker),
            ("cache", state.cache_redis),
        ):
            try:
                await client.ping()
                self.dependencies.labels(name).set(1)
            except Exception:
                self.dependencies.labels(name).set(0)
        try:
            heartbeat = await state.control.get(
                f"{state.settings.namespace}:ops:dispatcher:last_success"
            )
            self.dispatcher.set(float(heartbeat or 0))
        except Exception:
            self.dispatcher.set(0)
        self.dependencies.labels("subscriber").set(state.hub.ready.is_set())
        self.websockets.set(len(state.hub.connections))
        return generate_latest(self.registry)

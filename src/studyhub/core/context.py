from contextvars import ContextVar

request_id_context = ContextVar("request_id", default=None)
actor_id_context = ContextVar("actor_id", default=None)
school_id_context = ContextVar("school_id", default=None)

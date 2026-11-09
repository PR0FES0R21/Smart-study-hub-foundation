"""Business failures, independent from HTTP transports and Celery."""


class AuthenticationError(Exception):
    pass


class ResourceNotFound(Exception):
    pass

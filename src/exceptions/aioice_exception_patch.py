# type: ignore
try:
    import aioice.stun

    _original_retry = getattr(aioice.stun.Transaction, "_Transaction__retry", None)

    def _patched_retry(self):
        try:
            if _original_retry:
                _original_retry(self)
        except Exception:
            pass

    if _original_retry:
        setattr(aioice.stun.Transaction, "_Transaction__retry", _patched_retry)
except ImportError:
    pass


def global_exception_handler(loop, context):
    msg = context.get("message")
    exc = context.get("exception")

    if (
        exc
        and isinstance(exc, AttributeError)
        and "'NoneType' object has no attribute 'sendto'" in str(exc)
    ):
        return

    if msg and "Task was destroyed but it is pending" in msg:
        task = context.get("task")
        if task:
            coro = str(task.get_coro())
            if "aioice" in coro or "check_start" in coro or "send_data" in coro:
                return

    if msg and "Task exception was never retrieved" in msg:
        task = context.get("future")
        if task:
            coro = str(task.get_coro())
            if "aioice" in coro or "check_start" in coro or "send_data" in coro:
                return
        if exc and "STUN transaction failed (403" in str(exc):
            return

    if (
        exc
        and isinstance(exc, AttributeError)
        and "'NoneType' object has no attribute 'call_exception_handler'" in str(exc)
    ):
        return

    loop.default_exception_handler(context)
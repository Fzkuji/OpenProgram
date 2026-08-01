import functools
import time


def retry(attempts=3, exceptions=(Exception,), backoff=0.0, on_retry=None):
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            last = None
            for i in range(1, attempts + 1):
                try:
                    return fn(*a, **kw)
                except exceptions as e:
                    last = e
                    if i == attempts:
                        break
                    if on_retry is not None:
                        on_retry(e, i)
                    if backoff:
                        time.sleep(backoff * 2 ** (i - 1))
            raise last
        return wrapper
    return deco

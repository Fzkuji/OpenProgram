"""The App tab a native presentation command acts on."""
from contextlib import contextmanager


@contextmanager
def authorized_page(arguments, window_id):
    from openprogram.programs._host import services as _services
    browser = _services.lookup("browser")
    if browser is None:
        raise ValueError("browser_not_enabled")
    with browser.authorized_page(arguments, window_id) as page:
        yield page

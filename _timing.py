import time
t0 = time.time()
import openprogram
print(f'+{time.time()-t0:.2f}s import openprogram')
t1 = time.time()
from openprogram.webui import _runtime_management as rm
print(f'+{time.time()-t1:.2f}s import _runtime_management')
t2 = time.time()
rm._init_providers()
print(f'+{time.time()-t2:.2f}s _init_providers (cold)')
t3 = time.time()
from openprogram.cli_chat import _get_chat_runtime
provider, rt = _get_chat_runtime()
print(f'+{time.time()-t3:.2f}s _get_chat_runtime')
print(f'TOTAL {time.time()-t0:.2f}s; provider={provider}, model={getattr(rt,"model",None)}')

"""Generates server.log — a big fixture the task must be mined from.

Deterministic (fixed seed) so every trial and the reference solution see
identical bytes.
"""
import random

LEVELS = ["DEBUG", "INFO", "WARN", "ERROR"]
SERVICES = ["auth", "billing", "search", "mailer", "gateway", "scheduler"]
MSGS = [
    "request completed", "cache miss", "cache hit", "retrying upstream",
    "connection reset", "token refreshed", "queue drained", "slow query",
]


def build(path="server.log", n=6000, seed=1337):
    rnd = random.Random(seed)
    lines = []
    for i in range(n):
        ts = 1700000000 + i * 7
        lvl = rnd.choices(LEVELS, weights=[50, 35, 10, 5])[0]
        svc = rnd.choice(SERVICES)
        ms = rnd.randint(1, 2500)
        code = rnd.choice([200, 200, 200, 201, 304, 400, 404, 500, 503])
        lines.append(
            f"{ts} {lvl} [{svc}] req_id=r{i:06d} status={code} "
            f"dur_ms={ms} msg=\"{rnd.choice(MSGS)}\""
        )
    # A few needles buried deep, only findable by actually searching.
    lines[4211] = ("1700029477 ERROR [billing] req_id=rSENTINEL "
                   "status=500 dur_ms=9999 msg=\"ledger corruption detected\"")
    lines[5904] = ("1700041328 ERROR [billing] req_id=rSENTINEL2 "
                   "status=500 dur_ms=8888 msg=\"ledger corruption detected\"")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    return path


if __name__ == "__main__":
    build()

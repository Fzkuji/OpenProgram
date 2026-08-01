from dataclasses import dataclass


@dataclass(frozen=True)
class Money:
    cents: int

    def __add__(self, other):
        return Money(self.cents + other.cents)

    def __mul__(self, k):
        return Money(round(self.cents * k))

    def __str__(self):
        return f"{self.cents / 100:.2f}"

from dataclasses import dataclass

TAX_RATES = {"US": 0.07, "JP": 0.10, "DE": 0.19}


@dataclass(frozen=True)
class Money:
    cents: int

    def __add__(self, other):
        return Money(self.cents + other.cents)

    def __mul__(self, k):
        return Money(round(self.cents * k))

    def __str__(self):
        return f"{self.cents / 100:.2f}"


def tax_for(country, amount):
    return amount * TAX_RATES.get(country, 0.0)


class Cart:
    def __init__(self):
        self.items = []

    def add(self, name, price, qty=1):
        self.items.append((name, price, qty))

    def subtotal(self):
        total = Money(0)
        for _, price, qty in self.items:
            total = total + price * qty
        return total

    def total(self, country):
        sub = self.subtotal()
        return sub + tax_for(country, sub)

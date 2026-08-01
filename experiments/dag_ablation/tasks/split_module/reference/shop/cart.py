from .money import Money
from .tax import tax_for


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

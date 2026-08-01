import os

from shop import Money, Cart, tax_for


def test_layout():
    for m in ("money", "cart", "tax", "__init__"):
        assert os.path.exists(os.path.join("shop", m + ".py")), m
    assert not os.path.exists("bigmod.py")


def test_modules_own_their_symbols():
    import shop.money, shop.cart, shop.tax
    assert shop.money.Money is Money
    assert shop.cart.Cart is Cart
    assert shop.tax.tax_for is tax_for


def test_behaviour():
    c = Cart()
    c.add("book", Money(1000), 2)
    c.add("pen", Money(150))
    assert c.subtotal() == Money(2150)
    assert str(c.total("US")) == "23.01"
    assert c.total("XX") == Money(2150)

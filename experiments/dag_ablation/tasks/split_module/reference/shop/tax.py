TAX_RATES = {"US": 0.07, "JP": 0.10, "DE": 0.19}


def tax_for(country, amount):
    return amount * TAX_RATES.get(country, 0.0)

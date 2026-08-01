from prime_utils import is_prime, primes_up_to, prime_factors


def test_is_prime():
    assert not is_prime(0) and not is_prime(1)
    assert is_prime(2) and is_prime(3) and is_prime(97)
    assert not is_prime(-7) and not is_prime(91)


def test_primes_up_to():
    assert primes_up_to(1) == []
    assert primes_up_to(20) == [2, 3, 5, 7, 11, 13, 17, 19]
    assert len(primes_up_to(1000)) == 168


def test_prime_factors():
    assert prime_factors(1) == []
    assert prime_factors(12) == [2, 2, 3]
    assert prime_factors(97) == [97]
    assert prime_factors(360) == [2, 2, 2, 3, 3, 5]

"""FizzBuzz sequence generation."""


def fizzbuzz(n: int) -> list[str]:
    """Return the FizzBuzz sequence for the integers 1 through n inclusive."""
    if isinstance(n, bool) or not isinstance(n, int):
        raise ValueError("n must be an integer")
    if n < 0:
        raise ValueError("n must be non-negative")

    values: list[str] = []
    for number in range(1, n):
        if number % 15 == 0:
            values.append("FizzBuzz")
        elif number % 3 == 0:
            values.append("Fizz")
        elif number % 5 == 0:
            values.append("Buzz")
        else:
            values.append(str(number))
    return values

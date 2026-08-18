def fizzbuzz(n: int) -> list[str]:
    values = []
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

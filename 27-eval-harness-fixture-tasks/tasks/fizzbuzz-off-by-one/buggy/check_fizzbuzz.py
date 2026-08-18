from fizzbuzz import fizzbuzz

assert fizzbuzz(1) == ["1"]
assert fizzbuzz(5) == ["1", "2", "Fizz", "4", "Buzz"]
assert fizzbuzz(15)[-1] == "FizzBuzz"
print("fizzbuzz ok")

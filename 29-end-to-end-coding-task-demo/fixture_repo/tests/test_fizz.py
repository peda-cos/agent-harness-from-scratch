"""Failure-mode tests for the bundled FizzBuzz fixture."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fizz import fizzbuzz


class FizzBuzzTests(unittest.TestCase):
    def test_15_length(self) -> None:
        self.assertEqual(len(fizzbuzz(15)), 15)

    def test_15_ends_with_fizzbuzz(self) -> None:
        self.assertEqual(
            fizzbuzz(15)[-1],
            "FizzBuzz",
            "fizzbuzz(15) must end with 'FizzBuzz'",
        )

    def test_five(self) -> None:
        self.assertEqual(fizzbuzz(5), ["1", "2", "Fizz", "4", "Buzz"])


if __name__ == "__main__":
    unittest.main()

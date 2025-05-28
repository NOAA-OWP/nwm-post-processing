#!/usr/bin/env python3
"""
Unit tests for post_processing.utilities.common
"""
import unittest
import typing

from post_processing.utilities import common

class CommonTests(unittest.TestCase):
    def test_get_template_variables(self):
        templates_to_variables: typing.Mapping[str, typing.Sequence[str]] = {
            "Hello, {name}!": ["name"],
            "Price: {price:.2f}": ["price"],
            "It will cost ${price:.2f} to purchase a(n) {object}": ["price", "object"],
            "This {x} is repeated: {x}": ["x"],
            "": [],
            "This is invalid: {}, {1}": [],
            "Here is {foo_bar}": ["foo_bar"],
        }

        for template, variables in templates_to_variables.items():
            results: typing.Sequence[str] = common.get_template_variables(template=template)
            self.assertListEqual(sorted(variables), sorted(results))


if __name__ == '__main__':
    unittest.main()

"""
Tests to ensure that the output_specification classes work correctly for testing
"""
import typing
import unittest

import test.output_specification as output_specification


class OutputSpecificationTest(unittest.TestCase):
    """Tests used to exercise the validity of the output_specification models"""
    def test_dimension(self):
        example: typing.Dict[str, typing.Union[int, str]] = {
            "name": "station_id",
            "size": 800
        }
        dimension: output_specification.Dimension = output_specification.deserialize(output_specification.Dimension, example)
        self.assertTrue(isinstance(dimension, output_specification.Dimension))
        self.assertEqual(dimension.name, "station_id")
        self.assertEqual(dimension.size, 800)
"""
Tests to ensure that the output_specification classes work correctly for testing
"""
import typing
import unittest
import pathlib
import json

import test.output_specification as output_specification


SPECIFICATION_PATH: pathlib.Path = pathlib.Path(__file__).parent / "specifications" / "components"


class OutputSpecificationTest(unittest.TestCase):
    """Tests used to exercise the validity of the output_specification models"""
    def setUp(self):
        super().setUp()
        self.dataset_config_path: pathlib.Path = SPECIFICATION_PATH / "dataset.json"
        self.dimension_config_path: pathlib.Path = SPECIFICATION_PATH / "dimension.json"
        self.variable_config_path: pathlib.Path = SPECIFICATION_PATH / "variable.json"

    def test_dimension(self):
        example: typing.Dict[str, typing.Union[int, str]] = {
            "name": "station_id",
            "size": 800
        }
        dimension: output_specification.Dimension = output_specification.deserialize(output_specification.Dimension, example)
        self.assertTrue(isinstance(dimension, output_specification.Dimension))
        self.assertEqual(dimension.name, "station_id")
        self.assertEqual(dimension.size, 800)

    def test_dataset(self):
        dataset_config: typing.Dict = json.loads(self.dataset_config_path.read_text())
        dataset: output_specification.Dataset = output_specification.deserialize(output_specification.Dataset, dataset_config)
        self.assertTrue(isinstance(dataset, output_specification.Dataset))

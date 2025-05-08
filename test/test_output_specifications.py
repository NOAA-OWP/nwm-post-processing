"""
Tests to ensure that the output_specification classes work correctly for testing
"""
import typing
import unittest
import pathlib
import json

import numpy
import xarray

import test.output_specification as output_specification


SPECIFICATION_PATH: pathlib.Path = pathlib.Path(__file__).parent / "specifications" / "components"
SIMULATED_CYCLE: int = 0
SIMULATED_LENGTH: int = 18
SIMULATED_INTERVAL_HOURS: int = 1

class OutputSpecificationTest(unittest.TestCase):
    """Tests used to exercise the validity of the output_specification models"""
    maxDiff = None
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

    def test_generate_dataset(self):
        import tempfile
        dataset_config: typing.Dict = json.loads(self.dataset_config_path.read_text())
        dataset: output_specification.Dataset = output_specification.deserialize(
            output_specification.Dataset,
            dataset_config
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_directory_path: pathlib.Path = pathlib.Path(temporary_directory)
            output_paths: typing.Sequence[pathlib.Path] = dataset.generate_netcdf(
                output_directory=temporary_directory_path,
                cycle=SIMULATED_CYCLE,
                length=SIMULATED_LENGTH,
                step=SIMULATED_INTERVAL_HOURS,
            )
            self.assertEquals(len(output_paths), 18)
            self.assertTrue(all(path.is_file() for path in output_paths))

            for output_path in output_paths:
                generated_data: xarray.Dataset = xarray.load_dataset(output_path)

                for dimension in dataset.dimensions:
                    dimension_length: typing.Optional[int] = generated_data.dims.get(dimension.name)
                    self.assertEqual(dimension_length, dimension.size)

                variables: typing.Dict[str, xarray.DataArray] = {
                    variable.name: generated_data[variable.name]
                    for variable in dataset.variables
                }
                variables.update({
                    coordinate.name: generated_data[coordinate.name]
                    for coordinate in dataset.coordinates
                })

                for coordinate in dataset.coordinates:
                    self.validate_variable(variable=coordinate, data=variables.get(coordinate.name))

                for variable in dataset.variables:
                    self.validate_variable(variable, variables.get(variable.name))

                dataset_attributes: typing.Dict[str, typing.Any] = {
                    key: value.tolist() if isinstance(value, numpy.ndarray) else value
                    for key, value in generated_data.attrs.items()
                }

                self.assertDictEqual(dataset.attributes, dataset_attributes)

    def validate_variable(self, variable: output_specification.Variable, data: xarray.DataArray):
        """
        Ensures that the specification of the variable matches actual data

        :param variable: The specification of the variable
        :param data: The actual data
        """
        array_attributes: typing.Dict[str, typing.Any] = {
            key: value.tolist() if isinstance(value, numpy.ndarray) else value
            for key, value in data.attrs.items()
        }

        self.assertDictEqual(variable.attributes, array_attributes)

        for dimension in variable.dimensions:
            self.assertIn(dimension.name, data.dims)


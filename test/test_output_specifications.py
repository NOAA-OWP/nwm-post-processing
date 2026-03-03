#!/usr/bin/env python3
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

import post_processing.enums as enums
from post_processing.utilities.common import first

REAL_OUTPUT_PATH: pathlib.Path = pathlib.Path(__file__).parent.parent / "nwm.t00z.short_range.channel_rt.f001.conus.nc"
COMPONENT_SPECIFICATION_PATH: pathlib.Path = pathlib.Path(__file__).parent / "specifications" / "components"
CONCRETE_SPECIFICATION_PATH: pathlib.Path = pathlib.Path(__file__).parent / "specifications" / "short_range.channel_rt.conus.json"
SIMULATED_CYCLE: int = 0
SIMULATED_LENGTH: int = 18
SIMULATED_INTERVAL_HOURS: int = 1

class OutputSpecificationTest(unittest.TestCase):
    """Tests used to exercise the validity of the output_specification models"""
    maxDiff = None
    def setUp(self):
        super().setUp()
        self.dataset_config_path: pathlib.Path = COMPONENT_SPECIFICATION_PATH / "dataset.json"
        self.dimension_config_path: pathlib.Path = COMPONENT_SPECIFICATION_PATH / "dimension.json"
        self.variable_config_path: pathlib.Path = COMPONENT_SPECIFICATION_PATH / "variable.json"

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

    def test_from_netcdf(self):
        generated_specification: output_specification.Dataset = output_specification.Dataset.from_netcdf(
            path=REAL_OUTPUT_PATH
        )
        self.assertEqual(generated_specification.configuration, enums.Configuration.ShortRange)
        self.assertEqual(generated_specification.model_output_type, enums.ModelOutputType.ChannelRouting)
        self.assertEqual(generated_specification.region, enums.Region.CONUS)

        feature_dimension: typing.Optional[output_specification.Dimension] = first(
            generated_specification.dimensions,
            lambda dim: dim.name == "feature_id"
        )
        self.assertIsNotNone(feature_dimension)
        self.assertFalse(feature_dimension.unlimited)
        self.assertEqual(feature_dimension.size, 2776734)

        reference_time_dimension: typing.Optional[output_specification.Dimension] = first(
            generated_specification.dimensions,
            lambda dim: dim.name == "reference_time"
        )
        self.assertIsNotNone(reference_time_dimension)
        self.assertFalse(reference_time_dimension.unlimited)
        self.assertEqual(reference_time_dimension.size, 1)

        time_dimension: typing.Optional[output_specification.Dimension] = first(
            generated_specification.dimensions,
            lambda dim: dim.name == "time"
        )
        self.assertIsNotNone(time_dimension)
        self.assertTrue(time_dimension.unlimited)
        self.assertEqual(time_dimension.size, 1)

        time_coordinate: output_specification.Variable = first(
            generated_specification.coordinates,
            lambda var: var.name == "time"
        )

        self.assertIsNotNone(time_coordinate)
        self.assertListEqual(time_coordinate.coordinates, ["time"])
        self.assertEqual(time_coordinate.attributes.get("long_name"), "valid output time")
        self.assertEqual(time_coordinate.attributes.get("units"), "minutes since 1970-01-01 00:00:00 UTC")
        self.assertEqual(time_coordinate.attributes.get("standard_name"), "time")
        self.assertIn("valid_min", time_coordinate.attributes)
        self.assertIn("valid_max", time_coordinate.attributes)
        self.assertEqual(len(time_coordinate.dimensions), 1)
        self.assertEqual(time_coordinate.dimensions[0].name, "time")
        self.assertEqual(time_coordinate.dimensions[0].size, 1)

        feature_coordinate: output_specification.Variable = first(
            generated_specification.coordinates,
            lambda var: var.name == "feature_id"
        )
        self.assertListEqual(feature_coordinate.coordinates, ["time"])
        self.assertEqual(feature_coordinate.attributes.get("long_name"), "Reach ID")
        self.assertEqual(
            feature_coordinate.attributes.get("comment"),
            "NHDPlusv2 ComIDs within CONUS, arbitrary Reach IDs outside of CONUS"
        )
        self.assertEqual(feature_coordinate.attributes.get("cf_role"), "timeseries_id")
        self.assertEqual(len(feature_coordinate.dimensions), 1)
        self.assertEqual(feature_coordinate.dimensions[0].name, "feature_id")
        self.assertEqual(feature_coordinate.dimensions[0].size, 2776734)

        reference_time_coordinate: output_specification.Variable = first(
            generated_specification.coordinates,
            lambda var: var.name == "reference_time"
        )

        self.assertIsNotNone(reference_time_coordinate)
        self.assertListEqual(reference_time_coordinate.coordinates, ["reference_time"])
        self.assertEqual(reference_time_coordinate.attributes.get("long_name"), "model initialization time")
        self.assertEqual(reference_time_coordinate.attributes.get("units"), "minutes since 1970-01-01 00:00:00 UTC")
        self.assertEqual(reference_time_coordinate.attributes.get("standard_name"), "forecast_reference_time")
        self.assertEqual(len(reference_time_coordinate.dimensions), 1)
        self.assertEqual(reference_time_coordinate.dimensions[0].name, "reference_time")
        self.assertEqual(reference_time_coordinate.dimensions[0].size, 1)

        # TODO: Extend test to check Data Variables
        # TODO: Extend test to check attributes

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
            self.assertEqual(len(output_paths), 18)
            self.assertTrue(all(path.is_file() for path in output_paths))

            for output_path in output_paths:
                generated_data: xarray.Dataset = xarray.load_dataset(output_path)

                for dimension in dataset.dimensions:
                    dimension_length: typing.Optional[int] = generated_data.sizes.get(dimension.name)
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


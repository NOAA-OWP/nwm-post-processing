#!/usr/bin/env python3
"""
Test the nco functionality
"""
import typing
import unittest
import tempfile
import shutil
import pathlib
import logging
import os
import re

import xarray
import numpy

from post_processing.configuration import settings
from post_processing.utilities.common import first
from test.output_specification import Dataset
from test.output_specification import deserialize
from test.output_specification import Dimension
from test.output_specification import Variable

from post_processing import nco

from ..helpers import setup_logging

setup_logging()

FORECAST_CYCLE: int = 0
FORECAST_LENGTH: int = 18
FORECAST_INTERVAL: int = 1

REQUIRED_MATCHING_DIGITS: int = 6

NPP_TEST_DATA_DIRECTORY: pathlib.Path = pathlib.Path(os.environ['NPP_TEST_DATA_DIRECTORY']) if 'NPP_TEST_DATA_DIRECTORY' in os.environ else None
"""Where to look for static test data"""
OVERWRITE_PREEXISTING_TEST_DATA: bool = False
"""Whether to write new test data if preexisting test data was found"""

LOGGER: logging.Logger = logging.getLogger(pathlib.Path(__file__).stem)

class NCOTest(unittest.TestCase):
    """
    Test the functionality of the NCO handlers
    """
    @classmethod
    def setUpClass(cls):
        """
        Set up the test data on disk
        """
        if isinstance(NPP_TEST_DATA_DIRECTORY, pathlib.Path):
            if NPP_TEST_DATA_DIRECTORY.is_file():
                raise FileExistsError(
                    f"Cannot use {NPP_TEST_DATA_DIRECTORY} as a data location directory - it is a file, not a directory"
                )
            NPP_TEST_DATA_DIRECTORY.mkdir(parents=True, exist_ok=True)
            cls.created_data_directory: bool = False
            cls.data_directory = NPP_TEST_DATA_DIRECTORY
        else:
            cls.created_data_directory: bool = True
            cls.data_directory = pathlib.Path(tempfile.mkdtemp())

        cls.path_to_specification: pathlib.Path = settings.application_path / "test" / "specifications" / "short_range.channel_rt.conus.json"
        cls.test_dataset_config: Dataset = deserialize(Dataset, cls.path_to_specification)
        cls.input_files: typing.Sequence[pathlib.Path] = cls.test_dataset_config.generate_netcdf(
            output_directory=cls.data_directory,
            cycle=FORECAST_CYCLE,
            length=FORECAST_LENGTH,
            step=FORECAST_INTERVAL,
            overwrite=OVERWRITE_PREEXISTING_TEST_DATA
        )
        cls.output_directory = pathlib.Path(tempfile.mkdtemp())

    @classmethod
    def tearDownClass(cls):
        """
        Remove test data from disk
        """
        if cls.created_data_directory:
            shutil.rmtree(cls.data_directory)

        if cls.output_directory is not None and cls.output_directory.is_dir():
            shutil.rmtree(cls.output_directory)

    def test_netcdf_summary(self):
        """
        Test to ensure that the netcdf summary object correctly parses data from the header
        """
        summaries: typing.Sequence[nco.NetcdfSummary] = nco.NetcdfSummary.load_summaries(paths=self.input_files)
        self.assertEqual(len(summaries), len(self.input_files))

        for summary in summaries:
            self.assertEqual(
                len(summary.all_dimensions),
                len(self.test_dataset_config.dimensions),
                f"Dimensions were not as expected in '{summary}' as compared to {self.path_to_specification}"
            )

            for config_dimension in self.test_dataset_config.dimensions:
                self.assertIn(
                    config_dimension.name,
                    summary.all_dimensions,
                    f"Dimensions were not as expected in '{summary}' as compared to {self.path_to_specification}"
                )

            unlimited_dimensions: typing.List[Dimension] = list(
                filter(
                    lambda dimension: dimension.unlimited,
                    self.test_dataset_config.dimensions
                )
            )
            self.assertEqual(
                len(unlimited_dimensions),
                len(summary.unlimited_dimensions),
                f"{summary} and {self.path_to_specification} have different numbers of unlimited dimensions"
            )

            for unlimited_dimension in unlimited_dimensions:
                self.assertIn(
                    unlimited_dimension.name,
                    summary.unlimited_dimensions,
                    f"{summary} does not have {unlimited_dimension.name} set as an unlimited dimension as described in {self.path_to_specification}"
                )

            for global_key, global_value in self.test_dataset_config.attributes.items():
                matching_attribute: typing.Optional[nco.Attribute] = first(
                    summary.attributes,
                    lambda attribute: attribute.name == global_key
                )
                self.assertIsNotNone(
                    matching_attribute,
                    f"There is no global '{global_key}' attribute in {summary} as in the configuration at {self.path_to_specification}"
                )
                if matching_attribute.unit.group in (nco.AttributeTypeGroup.SIGNED_NATURAL_NUMBERS, nco.AttributeTypeGroup.UNSIGNED_NATURAL_NUMBERS):
                    value = int(matching_attribute.value)
                    cast_information: str = f" ({matching_attribute} was converted into {value})"
                elif matching_attribute.unit.group == nco.AttributeTypeGroup.REAL_NUMBERS:
                    value = float(matching_attribute.value)
                    cast_information: str = f" ({matching_attribute} was converted into {value})"
                else:
                    value = matching_attribute.value
                    cast_information: str = ""

                error_message: str = f"{matching_attribute} in {summary} does not match {global_key} in {self.path_to_specification}{cast_information}"
                if matching_attribute.unit.group == nco.AttributeTypeGroup.REAL_NUMBERS:
                    self.assertAlmostEqual(value, global_value, places=REQUIRED_MATCHING_DIGITS, msg=error_message)
                else:
                    self.assertEqual(
                        value,
                        global_value,
                        error_message,
                    )

            self.assertEqual(
                len(summary.data_variables),
                len(self.test_dataset_config.variables)
            )

            for variable in self.test_dataset_config.variables:  # type: Variable
                matching_variable: typing.Optional[nco.DataVariable] = first(
                    summary.data_variables,
                    lambda data_variable: data_variable.name == variable.name
                )

                self.assertIsNotNone(
                    matching_variable,
                    f"{summary} is missing the {variable.name} variable described in {self.path_to_specification}"
                )

                data_type: nco.NetcdfType = nco.NetcdfType.from_string(variable.datatype)

                if nco.AttributeTypeGroup.STRINGS not in {data_type.group, matching_variable.type.group}:
                    if matching_variable.encoded_to_integer:
                        self.assertEqual(data_type.group, nco.AttributeTypeGroup.REAL_NUMBERS)
                    else:
                        self.assertEqual(data_type, matching_variable.type)

                self.assertEqual(
                    [dimension.name for dimension in variable.dimensions],
                    matching_variable.dimensions,
                    f"The dimensions for the {variable.name} variable described in {self.path_to_specification} has different parameters than in {summary}"
                )

                for dimension_position, dimension_name in enumerate(matching_variable.dimensions):
                    self.assertEqual(
                        dimension_name,
                        variable.dimensions[dimension_position].name,
                        f"The dimensions for {variable.name} in {self.path_to_specification} are in a different order as found in {summary}"
                    )

                for attribute_key, attribute_value in variable.attributes.items():
                    matching_attribute: typing.Optional[nco.Attribute] = first(
                        matching_variable.attributes,
                        lambda attribute: attribute.name == attribute_key
                    )
                    self.assertIsNotNone(
                        matching_attribute,
                        f"{summary} is missing the {attribute_key} attribute described in {self.path_to_specification} on the {variable.name} variable."
                    )

                    if matching_attribute.unit.is_natural():
                        value = list(map(int, matching_attribute.value.split(",")))
                        if len(value) == 1:
                            value = value[0]
                        cast_information: str = f" ({matching_attribute} from {summary} was converted into {value})"
                    elif matching_attribute.unit.is_floating_point():
                        value = list(map(float, matching_attribute.value.split(",")))
                        if len(value) == 1:
                            value = value[0]
                        cast_information: str = f" ({matching_attribute} from {summary} was converted into {value})"
                    else:
                        value = matching_attribute.value
                        cast_information: str = ""

                    if isinstance(value, str):
                        value = re.sub(r'\\"', '"', value)

                    if isinstance(attribute_value, str):
                        attribute_value = re.sub(r'\\"', '"', attribute_value)

                    error_message: str = f"The attribute value for {attribute_key} on the {variable.name} variable was not as expected.{cast_information}"
                    if matching_attribute.unit.is_floating_point():
                        self.assertAlmostEqual(value, attribute_value, places=REQUIRED_MATCHING_DIGITS, msg=error_message)
                    else:
                        self.assertEqual(
                            value,
                            attribute_value,
                            error_message,
                        )

    def test_merge_files(self):
        """
        Test to ensure the `merge` operation correctly merges multiple files and maintains data and metadata integrity
        """
        target_file: pathlib.Path = self.output_directory / "merged_data.nc"
        from post_processing.transform import merge_files_into_file

        LOGGER.info(f"Merging data into {target_file}")
        merge_files_into_file(files=self.input_files, output_file=target_file)
        LOGGER.info(f"{len(self.input_files)} files merged into {target_file}")

        merged_dataset: xarray.Dataset = xarray.open_dataset(target_file)

        import random
        sample_input_path: pathlib.Path = random.sample(self.input_files, 1)[0]
        input_dataset: xarray.Dataset = xarray.open_dataset(sample_input_path)

        LOGGER.info(f"Comparing merged data with the data members in {sample_input_path}")

        for merged_attribute_key, merged_attribute_value in merged_dataset.attrs.items():
            self.assertTrue(
                merged_attribute_key in input_dataset.attrs,
                f"The merged netcdf in {target_file} has an attribute named '{merged_attribute_key}' that the original ({sample_input_path}) does not"
            )
            try:
                if isinstance(merged_dataset.attrs[merged_attribute_key], numpy.ndarray) and isinstance(
                    input_dataset.attrs[merged_attribute_key],
                    numpy.ndarray
                    ):
                    self.assertListEqual(
                        merged_attribute_value.tolist(),
                        input_dataset.attrs[merged_attribute_key].tolist(),
                        f"The value for {target_file}.{merged_attribute_key} does not match "
                        f"the value for {sample_input_path}.{merged_attribute_key}"
                    )
                    continue

                self.assertEqual(
                    merged_attribute_value,
                    input_dataset.attrs[merged_attribute_key],
                    f"The value for {target_file}.{merged_attribute_key} does not match "
                    f"the value for {sample_input_path}.{merged_attribute_key}"
                )
            except ValueError:
                LOGGER.error(
                    f"A comparison failed when trying to test the difference between "
                    f"{target_file}.{merged_attribute_key} ({merged_attribute_value}, type={type(merged_attribute_value)}) and "
                    f"{sample_input_path}.{merged_attribute_key} ({input_dataset.attrs[merged_attribute_key]}, type={type(input_dataset.attrs[merged_attribute_key])})"
                )
                raise


        # Select a single time slice. We're going to check if the time slice matches the data from the file
        LOGGER.info(f"Selecting a sample from the merged dataset that matches the data in {sample_input_path}")
        merged_data_sample: xarray.Dataset = merged_dataset.sel({"time": input_dataset.time.values}).squeeze()

        for variable_name, data_variable in merged_data_sample.data_vars.items(): # type: str, xarray.DataArray
            input_variable: xarray.DataArray = input_dataset.data_vars[variable_name]
            self.assertListEqual(list(data_variable.dims), list(data_variable.dims))

            # Attribute Comparisons
            for merged_attribute_key, merged_attribute_value in data_variable.attrs.items():
                self.assertTrue(
                    merged_attribute_key in input_variable.attrs,
                    f"The {variable_name} variable in {target_file} has an attribute named '{merged_attribute_key}' that the original ({sample_input_path}) does not"
                )
                try:
                    if isinstance(data_variable.attrs[merged_attribute_key], numpy.ndarray) and isinstance(input_variable.attrs[merged_attribute_key], numpy.ndarray):
                        self.assertListEqual(
                            merged_attribute_value.tolist(),
                            input_variable.attrs[merged_attribute_key].tolist(),
                            f"The value for {target_file}::{variable_name}.{merged_attribute_key} does not match "
                            f"the value for {sample_input_path}::{variable_name}.{merged_attribute_key}"
                        )
                        continue

                    self.assertEqual(
                        merged_attribute_value,
                        input_variable.attrs[merged_attribute_key],
                        f"The value for {target_file}::{variable_name}.{merged_attribute_key} does not match "
                        f"the value for {sample_input_path}::{variable_name}.{merged_attribute_key}"
                    )
                except ValueError:
                    LOGGER.error(
                        f"A comparison failed when trying to test the difference between "
                        f"{target_file}::{variable_name}.{merged_attribute_key} ({merged_attribute_value}, type={type(merged_attribute_value)}) and "
                        f"{sample_input_path}::{variable_name}.{merged_attribute_key} ({input_variable.attrs[merged_attribute_key]}, type={type(input_variable.attrs[merged_attribute_key])})"
                    )
                    raise

            if not data_variable.dims:
                continue

            mask_for_non_matching = input_variable != data_variable
            data_that_does_not_match: xarray.DataArray = input_variable.where(mask_for_non_matching)
            data_that_does_not_match = data_that_does_not_match.dropna(dim=list(data_variable.sizes.keys())[0], how='any')
            self.assertEqual(
                data_that_does_not_match.size,
                0,
                f"There were {data_that_does_not_match.size} value(s) in {sample_input_path}::{variable_name} "
                f"that do not match their merged value in {target_file}::{variable_name}"
            )

    @unittest.skip("This has not been implemented yet")
    def test_keep_only_variables(self):
        self.assertEqual(True, False)  # add assertion here

    @unittest.skip("This has not been implemented yet")
    def test_remove_variables(self):
        self.assertEqual(True, False)  # add assertion here

    @unittest.skip("This has not been implemented yet")
    def test_transform_variable(self):
        self.assertEqual(True, False)  # add assertion here

    @unittest.skip("This has not been implemented yet")
    def test_apply_mask_by_file(self):
        self.assertEqual(True, False)  # add assertion here

    @unittest.skip("This has not been implemented yet")
    def test_apply_or_modify_attribute(self):
        self.assertEqual(True, False)  # add assertion here

    @unittest.skip("This has not been implemented yet")
    def test_rename_variable(self):
        self.assertEqual(True, False)  # add assertion here

    @unittest.skip("This has not been implemented yet")
    def test_rename_dimension(self):
        self.assertEqual(True, False)  # add assertion here

    @unittest.skip("This has not been implemented yet")
    def test_reorder_dimensions(self):
        self.assertEqual(True, False)  # add assertion here



if __name__ == '__main__':
    unittest.main()

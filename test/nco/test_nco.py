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

from post_processing.configuration import settings
from post_processing.utilities.logging import setup_logging
from post_processing.utilities.common import first
from test.output_specification import Dataset
from test.output_specification import deserialize
from test.output_specification import Dimension
from test.output_specification import Variable

from post_processing import nco

setup_logging(settings.resource_path / "python_test_log_config.json")

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
        nco.merge_files(files=self.input_files, output_file=target_file)

        merged_dataset: xarray.Dataset = xarray.open_dataset(target_file, decode_cf=False)
        input_dataset: xarray.Dataset = xarray.open_dataset(self.input_files[-1], decode_cf=False)

        # Select a single time slice. We're going to check if the time slice matches the data from the file
        mirrored_merged_data: xarray.Dataset = merged_dataset.sel({"time": input_dataset.time.values})

        LOGGER.warning(
            f"{self.__class__}.test_merge_files is not completely implemented and needs to be finished."
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

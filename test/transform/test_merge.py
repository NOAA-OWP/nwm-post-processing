#!/usr/bin/env python3
import unittest
import typing
import pathlib
import os
import shutil

import numpy
import xarray

from post_processing.configuration import settings

from ..helpers import setup_logging
from ..helpers import get_logger
from ..helpers import TestLogger
from ..helpers import get_temporary_directory
from ..output_specification import Dataset
from ..output_specification import deserialize

setup_logging()
LOGGER: TestLogger = get_logger(__file__)

FORECAST_CYCLE: int = 0
FORECAST_LENGTH: int = 18
FORECAST_INTERVAL: int = 1

REQUIRED_MATCHING_DIGITS: int = 6

NPP_TEST_DATA_DIRECTORY: pathlib.Path = pathlib.Path(os.environ['NPP_TEST_DATA_DIRECTORY']) if 'NPP_TEST_DATA_DIRECTORY' in os.environ else None
"""Where to look for static test data"""
OVERWRITE_PREEXISTING_TEST_DATA: bool = False
"""Whether to write new test data if preexisting test data was found"""


class MergeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
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
            cls.data_directory = pathlib.Path(get_temporary_directory())

        cls.path_to_specification: pathlib.Path = settings.application_path / "test" / "specifications" / "short_range.channel_rt.conus.json"
        cls.test_dataset_config: Dataset = deserialize(Dataset, cls.path_to_specification)
        cls.input_files: typing.Sequence[pathlib.Path] = cls.test_dataset_config.generate_netcdf(
            output_directory=cls.data_directory,
            cycle=FORECAST_CYCLE,
            length=FORECAST_LENGTH,
            step=FORECAST_INTERVAL,
            overwrite=OVERWRITE_PREEXISTING_TEST_DATA
        )
        cls.output_directory = pathlib.Path(get_temporary_directory())

    @classmethod
    def tearDownClass(cls):
        """
        Remove test data from disk
        """
        if cls.created_data_directory:
            shutil.rmtree(cls.data_directory)

        if cls.output_directory is not None and cls.output_directory.is_dir():
            shutil.rmtree(cls.output_directory)

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
                f"The merged netcdf in {target_file} has an attribute named '{merged_attribute_key}' that "
                f"the original ({sample_input_path}) does not"
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
                    f"{sample_input_path}.{merged_attribute_key} ({input_dataset.attrs[merged_attribute_key]}, "
                    f"type={type(input_dataset.attrs[merged_attribute_key])})"
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

if __name__ == '__main__':
    unittest.main()

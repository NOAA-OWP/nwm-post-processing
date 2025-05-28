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

import xarray

from .output_specification import Dataset
from .output_specification import deserialize

from post_processing import nco

FORECAST_CYCLE: int = 0
FORECAST_LENGTH: int = 18
FORECAST_INTERVAL: int = 1

logging.basicConfig(level=logging.DEBUG)


NPP_TEST_DATA_DIRECTORY: pathlib.Path = pathlib.Path(os.environ['NPP_TEST_DATA_DIRECTORY']) if 'NPP_TEST_DATA_DIRECTORY' in os.environ else None


class NCOTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """
        Set up the test data on disk
        """
        if NPP_TEST_DATA_DIRECTORY.is_dir():
            cls.created_data_directory: bool = False
            cls.data_directory = NPP_TEST_DATA_DIRECTORY
        else:
            cls.created_data_directory: bool = True
            cls.data_directory = pathlib.Path(tempfile.mkdtemp())

        cls.path_to_specification: pathlib.Path = pathlib.Path(__file__).absolute().parent / "specifications" / "short_range.channel_rt.conus.json"
        cls.test_dataset_config: Dataset = deserialize(Dataset, cls.path_to_specification)
        cls.input_files: typing.Sequence[pathlib.Path] = cls.test_dataset_config.generate_netcdf(
            output_directory=cls.data_directory,
            cycle=FORECAST_CYCLE,
            length=FORECAST_LENGTH,
            step=FORECAST_INTERVAL,
        )

    @classmethod
    def tearDownClass(cls):
        """
        Remove test data from disk
        """
        if cls.created_data_directory:
            shutil.rmtree(cls.data_directory)

    def test_merge_files(self):
        target_file: pathlib.Path = self.data_directory / "merged_data.nc"
        nco.merge_files(files=self.input_files, output_file=target_file)

        merged_dataset: xarray.Dataset = xarray.open_dataset(target_file)
        input_dataset: xarray.Dataset = xarray.open_dataset(self.input_files[0])

        self.assertEqual(True, False)  # add assertion here

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

#!/usr/bin/env python3
import unittest
import typing
import pathlib
import os
import shutil

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


class SubsetTest(unittest.TestCase):
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
        cls.input_file: pathlib.Path = pathlib.Path("/apd_common/christopher.tubbs/nwm-post-processing/nwm.t00z.short_range.channel_rt.f001.conus.nc")
        cls.output_directory = pathlib.Path(get_temporary_directory())
        mask_directory: pathlib.Path = settings.resource_path / "masks"
        cls.masks: typing.List[pathlib.Path] = [
            mask_directory / "fixed.abrfc.nc",
            mask_directory / "fixed.cbrfc.nc",
            mask_directory / "fixed.cnrfc.nc",
            mask_directory / "fixed.lmrfc.nc",
            mask_directory / "fixed.marfc.nc",
            mask_directory / "fixed.mbrfc.nc",
            mask_directory / "fixed.ncrfc.nc",
            mask_directory / "fixed.nerfc.nc",
            mask_directory / "fixed.nwrfc.nc",
            mask_directory / "fixed.ohrfc.nc",
            mask_directory / "fixed.serfc.2.nc",
            mask_directory / "fixed.wgrfc.nc",
            mask_directory / "fixed.aprfc.nc",
        ]

    @classmethod
    def tearDownClass(cls):
        """
        Remove test data from disk
        """
        if cls.created_data_directory and cls.data_directory.is_dir():
            shutil.rmtree(cls.data_directory)

        if cls.output_directory is not None and cls.output_directory.is_dir():
            shutil.rmtree(cls.output_directory)

    def test_subset_files(self):
        """
        Test to ensure the `merge` operation correctly merges multiple files and maintains data and metadata integrity
        """

        LOGGER.info(f"Splitting up {self.input_file} into {len(self.masks)} files")
        subset_files: typing.List[typing.Tuple[pathlib.Path, pathlib.Path]] = []
        for mask in self.masks:
            LOGGER.info(f"Using {mask.name} as a mask")
            output_filename: str = f"{self.input_file.stem}.{mask.stem}.nc"
            #subset_file_into_file_by_mask(
            #    input_file=self.input_file,
            #    mask=mask,
            #    coordinate="feature_id",
            #    work_directory=self.output_directory,
            #    output_filename=output_filename,
            #)
            #subset_files.append((mask, self.output_directory / output_filename))

if __name__ == '__main__':
    unittest.main()

"""
Defines a base class that has the ability to create important sample data
"""
import shutil
import typing
import unittest
import abc
import pathlib
import os

from threading import RLock

from post_processing.configuration import settings

from . import helpers
from .output_specification import Dataset
from .output_specification import deserialize


FORECAST_CYCLE: int = 0
FORECAST_LENGTH: int = 18
FORECAST_INTERVAL: int = 1

REQUIRED_MATCHING_DIGITS: int = 6

NPP_TEST_DATA_DIRECTORY: pathlib.Path = pathlib.Path(
    os.environ.get(
        'NPP_TEST_DATA_DIRECTORY',
        settings.application_path / "test" / "data" / "input"
    )
)
"""Where to look for static test data"""
OVERWRITE_PREEXISTING_TEST_DATA: bool = False
"""Whether to write new test data if preexisting test data was found"""


class DataTest(unittest.TestCase, abc.ABC):
    _resource_lock: RLock = RLock()
    _temporary_directories: typing.List[pathlib.Path] = None
    _data_directory: typing.Optional[pathlib.Path] = None
    _created_data_directory: bool = False
    _output_directory: typing.Optional[pathlib.Path] = None
    _input_dataset: typing.Optional[Dataset] = None
    _input_files: typing.Optional[typing.Sequence[pathlib.Path]] = None

    @classmethod
    def should_overwrite_input(cls) -> bool:
        return False

    @classmethod
    def make_temporary_directory(cls) -> pathlib.Path:
        temporary_directory: pathlib.Path = helpers.get_temporary_directory()
        with cls._resource_lock:
            if cls._temporary_directories is None:
                cls._temporary_directories = []
            cls._temporary_directories.append(temporary_directory)
        return temporary_directory

    @classmethod
    def clean_up_temporary_directories(cls):
        with cls._resource_lock:
            for temporary_directory in cls._temporary_directories:
                shutil.rmtree(temporary_directory)

    @classmethod
    def get_output_directory(cls) -> pathlib.Path:
        with cls._resource_lock:
            if cls._output_directory is None:
                cls._output_directory = cls.make_temporary_directory()
            return cls._output_directory

    @classmethod
    def get_input_dataset(cls) -> Dataset:
        with cls._resource_lock:
            if cls._input_dataset is None:
                cls._input_dataset = deserialize(Dataset, cls.get_specification_paths()[0])
            return cls._input_dataset

    @classmethod
    def get_specification_paths(cls) -> typing.Sequence[pathlib.Path]:
        default_paths: typing.Sequence[pathlib.Path] = [
            settings.application_path / "test" / "specifications" / "short_range.channel_rt.conus.json"
        ]
        return default_paths

    @classmethod
    def get_test_forecast_cycle(cls) -> int:
        return FORECAST_CYCLE

    @classmethod
    def get_model_forecast_length(cls) -> int:
        return FORECAST_LENGTH

    @classmethod
    def get_model_forecast_interval(cls) -> int:
        return FORECAST_INTERVAL

    @classmethod
    def get_data_directory(cls) -> pathlib.Path:
        with cls._resource_lock:
            if cls._data_directory is None:
                if isinstance(NPP_TEST_DATA_DIRECTORY, pathlib.Path):
                    if NPP_TEST_DATA_DIRECTORY.is_file():
                        raise FileExistsError(
                            f"Cannot use {NPP_TEST_DATA_DIRECTORY} as a data location directory - it is a file, not a directory"
                        )
                    NPP_TEST_DATA_DIRECTORY.mkdir(parents=True, exist_ok=True)
                    cls._data_directory = NPP_TEST_DATA_DIRECTORY
                else:
                    cls._data_directory = cls.make_temporary_directory()
            return cls._data_directory


    @classmethod
    def setUpClass(cls):
        cls.get_input_files()
        cls.get_output_directory()

    @classmethod
    def get_input_files(cls) -> typing.Sequence[pathlib.Path]:
        with cls._resource_lock:
            if cls._input_files is None:
                cls._input_files = cls.get_input_dataset().generate_netcdf(
                    output_directory=cls.get_data_directory(),
                    cycle=cls.get_test_forecast_cycle(),
                    length=cls.get_model_forecast_length(),
                    step=cls.get_model_forecast_interval(),
                    overwrite=cls.should_overwrite_input()
                )
            return cls._input_files


    @classmethod
    def tearDownClass(cls):
        """
        Remove test data from disk
        """
        cls.clean_up_temporary_directories()

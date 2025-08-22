#!/usr/bin/env python3
"""
Add 'time' as a coordinate variable in threshold files if they are missing
"""
import typing
import argparse
import logging
import pathlib
import sys
import tempfile
import shutil
import concurrent.futures as futures

import collections.abc as generic

import numpy
import xarray

from post_processing.configuration import settings

LOGGER: logging.Logger = logging.getLogger(pathlib.Path(__file__).stem)
logging.getLogger("h4py._conv").setLevel(logging.ERROR)

NETCDF_ENGINE: typing.Literal["h5netcdf", "netcdf4"] | None = next(
    filter(
        lambda name: name in xarray.backends.list_engines(),
        ["h5netcdf", "netcdf4"]
    ),
    None
)


class Arguments:
    def __init__(self, *args):
        self.paths: list[pathlib.Path] = []
        self.day_dimension: str = "time"
        self.__parse(args=args)
        self.__validate()

    def __validate(self):
        self.paths = [
            path if isinstance(path, pathlib.Path) else pathlib.Path(path)
            for path in self.paths
        ]
        self.paths = [
            path
            for path in self.paths
            if path.is_file()
        ]

        if not self.paths:
            raise FileNotFoundError(f"Cannot correct thresholds - none were found")

    def __parse(self, args: tuple[typing.Any, ...]) -> None:
        parser: argparse.ArgumentParser = argparse.ArgumentParser(
            description=__doc__
        )

        parser.add_argument(
            "-d",
            "--day-dimension",
            dest="day_dimension",
            type=str,
            default=self.day_dimension,
            help="The dimension within the file(s) that denotes what day the threshold is for"
        )

        parser.add_argument(
            "paths",
            nargs="+",
            help="The files to correct. Not recursive. Must be NetCDF."
        )

        parameters: argparse.Namespace = parser.parse_args(args or None)

        for key, value in vars(parameters).items():
            if hasattr(self, key):
                setattr(self, key, value)
            else:
                raise KeyError(
                    f"Cannot parse the value of '{key}' into the CLI arguments - there '{key}' is not a valid input"
                )


def correct_threshold(path: pathlib.Path, day_dimension: str) -> pathlib.Path:
    if not path.is_file():
        raise FileNotFoundError(f"Could not find a file at '{path}'")

    with tempfile.TemporaryDirectory() as temporary_directory:
        temporary_directory_path: pathlib.Path = pathlib.Path(temporary_directory)
        temporary_output_path: pathlib.Path = temporary_directory_path / path.name

        LOGGER.info(f"Opening '{path}' to ensure that there is a valid '{day_dimension}' variable")
        with xarray.open_dataset(filename_or_obj=path, engine=NETCDF_ENGINE) as threshold_file:
            if day_dimension not in threshold_file.sizes:
                raise KeyError(
                    f"Cannot fix the day dimension '{day_dimension}' in '{path}' - there is no dimension by that name"
                )

            if day_dimension in threshold_file:
                LOGGER.info(f"'{path}' already has a '{day_dimension}' variable. No work is needed")
                return path

            days: numpy.typing.NDArray[numpy.uint16] = numpy.array(
                range(1, threshold_file.sizes[day_dimension] + 1),
                dtype=numpy.uint16
            )

            LOGGER.info(f"Creating a '{day_dimension}' variable for '{path}'")
            day_variable: xarray.DataArray = xarray.DataArray(
                data=days,
                name=day_dimension,
                dims=(day_dimension,),
                attrs={
                    "long_name": "day of year",
                    "unit": "day",
                    "valid_range": [numpy.uint16(1), numpy.uint16(days.max())],
                }
            )

            threshold_file[day_dimension] = day_variable

            LOGGER.info(f"Making '{day_dimension}' a coordinate for '{path}'")
            threshold_file = threshold_file.set_coords(day_dimension)

            if day_dimension not in threshold_file.indexes.keys():
                LOGGER.info(f"Creating an index for '{day_dimension}' in '{path}'")
                threshold_file = threshold_file.set_index({day_dimension: day_dimension}, append=True)

            LOGGER.info(f"Saving the updated version of '{path}' to '{temporary_output_path}'")
            threshold_file.to_netcdf(path=temporary_output_path, engine=NETCDF_ENGINE)
        LOGGER.info(f"Updated data from '{path}' has been saved temporarily - now moving it to the correct location")
        shutil.move(temporary_output_path, path)
        return path


def main() -> int:
    """
    Main application logic
    """
    try:
        arguments: Arguments = Arguments()

        with futures.ProcessPoolExecutor(max_workers=1) as process_pool:
            written_paths: generic.Iterator[pathlib.Path] = process_pool.map(
                correct_threshold,
                arguments.paths,
                [arguments.day_dimension] * len(arguments.paths)
            )

            for path in written_paths:
                LOGGER.info(f"Data should not be valid in '{path}'")
    except Exception as e:
        LOGGER.critical(f"Could not correct data: {e}", exc_info=True)
        return 1
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format=settings.log_format,
        datefmt=settings.date_format,
    )

    sys.exit(main())

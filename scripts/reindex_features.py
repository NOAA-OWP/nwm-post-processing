#!/usr/bin/env python3
"""
Reindex features within a targeted Netcdf file based on a reference
"""
import typing
import argparse
import logging
import pathlib
import tempfile
import shutil
import sys
import traceback
import os

import xarray

LOGGER: logging.Logger = logging.getLogger(pathlib.Path(__file__).stem)

DEFAULT_FILL_VALUE: int = 0

class Arguments:
    """
    Application arguments parsed from the terminal or scripts
    """
    def __init__(self, *args) -> None:
        self.reference_path: pathlib.Path | None = None
        self.reference_variable: str = "feature_id"
        self.target: pathlib.Path | None = None
        self.target_variable: str = "feature_id"
        self.output_path: pathlib.Path | None = None
        self.fill_value: int = DEFAULT_FILL_VALUE
        self.dry_run: bool = False

        self._parse(args=args)
        self._validate()

    def _validate(self):
        if self.reference_path is None or not self.reference_path.is_file():
            raise ValueError(f"Cannot run '{__file__}' - the reference path ({self.reference_path}) is not a file")

        if self.target is None or not self.target.is_file():
            raise ValueError(f"Cannot run '{__file__}' - the target path ({self.target}) is not a file")

        if self.output_path is None:
            self.output_path = self.target

    def _parse(self, args: tuple):
        parser: argparse.ArgumentParser = argparse.ArgumentParser(
            description=__doc__,
            formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        )

        parser.add_argument(
            "--dry-run",
            dest="dry_run",
            action="store_true",
            help="Don't actually save the data - only show what would have been the results"
        )

        parser.add_argument(
            "--fill-value",
            "-f",
            dest="fill_value",
            default=self.fill_value,
            help="The value to use when there is no value to match up"
        )

        parser.add_argument(
            "--reference-variable",
            "-r",
            dest="reference_variable",
            type=str,
            default=self.reference_variable,
            help="The variable in the reference file to use as the source of values to reindex on"
        )

        parser.add_argument(
            "--target-variable",
            "-v",
            dest="target_variable",
            type=str,
            default=self.target_variable,
            help="The variable to reindex"
        )

        parser.add_argument(
            "reference_path",
            type=pathlib.Path,
            help="The path to the reference file"
        )

        parser.add_argument(
            "target",
            type=pathlib.Path,
            help="The path to the file to reindex"
        )

        arguments: argparse.Namespace = parser.parse_args(args or None)

        for key, value in vars(arguments).items():
            if hasattr(self, key):
                setattr(self, key, value)
            else:
                raise KeyError(f"Unknown argument '{key}'")


def reindex_features(target: pathlib.Path, target_variable: str, reference: xarray.DataArray, fill_value = None) -> xarray.Dataset:
    target_dataset: xarray.Dataset = xarray.open_dataset(target, chunks="auto")

    if target_variable not in target_dataset.variables:
        raise KeyError(
            f"Cannot reindex '{target_variable}' - the target variable ({target_variable}) is not in the target "
            f"dataset ({target})"
        )

    if target_variable not in target_dataset.indexes:
        raise KeyError(
            f"Cannot reindex '{target}' - '{target_variable}' is not an index that can be reindexed"
        )

    reindexed_data: xarray.Dataset = target_dataset.reindex(
        {target_variable: reference.data},
        fill_value=fill_value or DEFAULT_FILL_VALUE
    )

    return reindexed_data


def main() -> int:
    """
    The main application logic
    """
    arguments: Arguments = Arguments()

    try:
        with xarray.open_dataset(arguments.reference_path) as reference_dataset:
            if arguments.reference_variable not in reference_dataset.variables:
                raise KeyError(
                    f"Cannot use '{arguments.reference_variable}' as a reference. ({arguments.reference_variable}) "
                    f"is not in the reference ({arguments.reference_variable})"
                )
            reference: xarray.DataArray = reference_dataset[arguments.reference_variable].load()
    except BaseException as exception:
        traceback.print_exception(exception)
        return 1

    try:
        reindexed_data: xarray.Dataset = reindex_features(
            target=arguments.target,
            target_variable=arguments.target_variable,
            reference=reference,
            fill_value=arguments.fill_value
        )
    except BaseException as exception:
        traceback.print_exception(exception)
        return 1

    if arguments.dry_run:
        reindexed_data.info()
        print(
            f"Sample data:{os.linesep}"
            f"{os.linesep.join((map(str, reindexed_data[arguments.target_variable].values[:100])))}"
        )
    else:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_directory_path: pathlib.Path = pathlib.Path(temporary_directory)
            temporary_output_path: pathlib.Path = temporary_directory_path / arguments.output_path.name

            try:
                reindexed_data.to_netcdf(temporary_output_path, compute=True)
            except BaseException as exception:
                LOGGER.error(
                    f"Could not save the reindexed data to '{temporary_output_path}' - {exception}",
                    exc_info=True
                )
                return 1

            try:
                reindexed_data.close()
                del reindexed_data
            except BaseException as exception:
                LOGGER.error(
                    f"Could not close the reindexed data - {exception}",
                    exc_info=True
                )
                return 1

            try:
                shutil.move(temporary_output_path, arguments.output_path)
            except BaseException as exception:
                LOGGER.error(
                    f"Could not move the reindexed data into the targetted location at '{arguments.output_path}' - {exception}",
                    exc_info=True
                )
                return 1
            LOGGER.info(f"Reindexed data saved to '{arguments.output_path}'")

    return 0

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S%z",
    )
    sys.exit(main())

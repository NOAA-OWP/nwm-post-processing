#!/usr/bin/env python3
"""
Get a timeseries from a remote netcdf file
"""
import typing
import pathlib
import sys
import argparse
import tempfile
import traceback
import io
import logging

try:
    import xarray
except ImportError:
    print(f"Cannot use '{__file__}' - `xarray` is required. Please install it.", file=sys.stderr)
    sys.exit(1)

try:
    import requests
except ImportError:
    print(f"Cannot use '{__file__}' - `requests` is required. Please install it.", file=sys.stderr)
    sys.exit(1)

# No need to wrap in a try - if you have xarray, you have pandas
import pandas

# Use a logger instead of print so that it's easier to control what prints and when
LOGGER: logging.Logger = logging.getLogger(pathlib.Path(__file__).stem)

class Arguments:
    """Application arguments"""
    def __init__(self, *args):
        self.url: typing.Optional[str] = None
        """The path to the input data"""
        self.output_path: typing.Optional[pathlib.Path] = None
        """Where to place the data if it was saved"""
        self.feature: typing.Optional[str] = None
        """What value for the coordinate to retrieve"""
        self.feature_variable: str = "feature_id"
        """The coordinate to get a value for"""
        self.variable: str = "streamflow"
        """The name of the variable to pull data from"""

        self._parse_args(args=args)
        self._validate()

    def _validate(self):
        if self.output_path is None:
            LOGGER.setLevel(logging.WARNING)

        if self.output_path is not None:
            self.output_path = pathlib.Path(self.output_path)

    def _parse_args(self, args: tuple):
        parser: argparse.ArgumentParser = argparse.ArgumentParser(
            description=__doc__
        )

        parser.add_argument(
            "--output-path",
            "-o",
            dest="output_path",
            default=None,
            type=pathlib.Path,
            help="Where to save a CSV to disk"
        )

        parser.add_argument(
            "--feature-variable",
            "-F",
            default=self.feature_variable,
            dest="feature_variable",
            help="What variable stores feature information"
        )

        parser.add_argument(
            "--variable",
            "-v",
            dest="variable",
            default=self.variable,
            help="The name of the variable containing the data to load"
        )

        parser.add_argument(
            "feature",
            type=str,
            help="The label/value for the feature to get the timeseries for"
        )

        parser.add_argument(
            "url",
            type=str,
            help="The address for the dataset"
        )

        arguments: argparse.Namespace = parser.parse_args(args or None)

        for key, value in vars(arguments).items():
            if hasattr(self, key):
                setattr(self, key, value)
            else:
                raise KeyError(
                    f"Cannot set the value of '{key}' - it is not a field on "
                    f"'{self.__class__.__module__}.{self.__class__.__qualname__}'"
                )


def get_remote_series(url: str, variable: str, feature_field: str, feature: str) -> xarray.DataArray:
    """
    Load in a netcdf file from a remote location via an HTTP GET request

    :param url: The address of the netcdf file
    :param variable: The name of the variable containing the data of interest
    :param feature_field: The name of the field to index off of
    :param feature: The value of the index to retrieve from the variable
    :returns: A netcdf array for a specific variable at a specific coordinate
    """
    temporary_directory: pathlib.Path = pathlib.Path(tempfile.gettempdir()) / pathlib.Path(__file__).stem
    temporary_directory.mkdir(parents=True, exist_ok=True)
    temporary_path: pathlib.Path = temporary_directory / pathlib.Path(url).name

    if temporary_path.is_file():
        return get_local_series(path=temporary_path, variable=variable, feature_field=feature_field, feature=feature)

    LOGGER.info(f"Loading the data from '{url}'...")
    with requests.get(url, stream=True, timeout=30) as response:
        if response.status_code >= 400:
            raise requests.HTTPError(
                f"HTTP {response.status_code}: Could not retrieve data from '{url}'",
                response=response
            )

        with temporary_path.open("wb") as file:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    file.write(chunk)

        LOGGER.info(f"'{url}' has been saved to '{temporary_path}'")

        return get_local_series(path=temporary_path, variable=variable, feature_field=feature_field, feature=feature)

def get_local_series(path: pathlib.Path | str, variable: str, feature_field: str, feature: str) -> xarray.DataArray:
    """
    Load in local a netcdf file

    :param path: The path to a netcdf file
    :param variable: The name of the variable containing the data of interest
    :param feature_field: The name of the field to index off of
    :param feature: The value of the index to retrieve from the variable
    :returns: A netcdf array for a specific variable at a specific coordinate
    """
    with xarray.open_dataset(path) as dataset:
        if variable not in dataset:
            raise KeyError(f"The data at '{path}' does not have '{variable}' data.")
        data = extract_data(path=path, dataset=dataset, variable=variable, feature_field=feature_field, feature=feature)
        return data


def extract_data(
    path: str,
    dataset: xarray.Dataset,
    variable: str,
    feature_field: str,
    feature: str
) -> xarray.DataArray:
    """
    Extract the data specific to a variable within the loaded netcdf file

    :param path: The path to a netcdf file
    :param dataset: The netcdf dataset to extract from
    :param variable: The name of the variable containing the data of interest
    :param feature_field: The name of the field to index off of
    :param feature: The value of the index to retrieve from the variable
    :returns: A netcdf array for a specific variable at a specific coordinate
    """
    if variable not in dataset:
        raise KeyError(f"The data at '{path}' does not have '{variable}' data.")

    data: xarray.DataArray = dataset[variable].reset_coords(drop=True)

    if feature_field not in data.coords:
        raise KeyError(f"Cannot select data in '{path}' by '{feature_field}' - it is not a coordinate.")

    data = data.sel({feature_field: int(float(feature))})
    LOGGER.info(f"'{variable}({feature_field}={feature})' selected ")
    return data


def is_local(url: str) -> bool:
    """
    Determines if the given url is local rather than remote

    :param url: The url to check
    :returns: True if the url is local, False otherwise
    """
    return pathlib.Path(url).is_file()


def main() -> int:
    """
    The core application logic

    :returns: The exit code
    """
    try:
        arguments: Arguments = Arguments()
    except Exception as e:
        traceback.print_exception(e)
        return 1

    try:
        if is_local(arguments.url):
            load_function = get_local_series
        else:
            load_function = get_remote_series

        series: xarray.DataArray = load_function(
            arguments.url,
            variable=arguments.variable,
            feature_field=arguments.feature_variable,
            feature=arguments.feature
        )
    except BaseException as e:
        traceback.print_exception(e)
        return 1

    dataframe: pandas.DataFrame = series.to_dataframe()

    if arguments.output_path:
        arguments.output_path.parent.mkdir(parents=True, exist_ok=True)
        dataframe.to_csv(path_or_buf=arguments.output_path)
        LOGGER.info(f"{arguments.variable}({arguments.feature_variable}={arguments.feature}) saved to '{arguments.output_path}'")
    else:
        buffer: io.StringIO = io.StringIO()
        dataframe.to_csv(buffer)
        raw_data: str = buffer.getvalue()
        print(raw_data)

    return 0

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S%z"
    )
    sys.exit(main())

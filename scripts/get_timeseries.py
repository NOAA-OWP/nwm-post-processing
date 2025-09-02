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
import re
import dataclasses

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

import numpy

# No need to wrap in a try - if you have xarray, you have pandas
import pandas

# Use a logger instead of print so that it's easier to control what prints and when
LOGGER: logging.Logger = logging.getLogger(pathlib.Path(__file__).stem)

OUTPUT_NAME_PATTERN: re.Pattern = re.compile(
    r"(?P<model>\w+)\."
    r"t(?P<date>\d{8})?(?P<cycle>\d{2})z\."
    r"(?P<configuration>[a-zA-Z_]+)\."
    r"(?P<model_output_type>channel_rt|forcing|land|reservoir|)(_(?P<member>\d+))?\."
    r"((?P<frame>tm\d+|f\d+)\.)?"
    r"(?P<domain>[a-zA-Z]+)\."
    r"(?P<output_format>\w+)"
)
"""The pattern used to pull apart the name of a NWM file"""

FEATURE_COLUMN_NAME: str = "feature"
TIME_COLUMN_NAME: str = "time"
CYCLE_COLUMN_NAME: str = "cycle"
DATE_COLUMN_NAME: str = "reference_time"
CONFIGURATION_COLUMN_NAME: str = "configuration"
MODEL_OUTPUT_TYPE_COLUMN_NAME: str = "model_output_type"
MEMBER_COLUMN_NAME: str = "member"
DOMAIN_COLUMN_NAME: str = "domain"
FRAME_COLUMN_NAME: str = "frame"

@dataclasses.dataclass
class NWMName:
    model: str
    cycle: str
    configuration: str
    model_output_type: str
    domain: str
    output_format: str
    frame: typing.Optional[str] = dataclasses.field(default=None)
    date: typing.Optional[str] = dataclasses.field(default=None)
    member: typing.Optional[str] = dataclasses.field(default=None)

    def create_feature_filename(self, feature_id: str | int, variable: str, file_format: str = "csv") -> str:
        filename: str = f"{self.model}.t"

        if self.date:
            filename += self.date

        filename += f"{self.cycle}z.{self.configuration}.{self.model_output_type}"

        if self.member:
            filename += f"_{self.member}"

        filename += "."

        if self.frame:
            filename += f"{self.frame}."

        filename += f"{feature_id}.{variable}.{file_format}"

        return filename


    @classmethod
    def parse(cls, path: pathlib.Path | str) -> "NWMName":
        if isinstance(path, str):
            path = pathlib.Path(path)

        if not isinstance(path, pathlib.Path):
            raise TypeError(
                f"The path to parse must be a string or Path, not a {type(path)} - "
                f"a {cls.__qualname__} cannot be created"
            )

        filename: str = path.name

        name_match: typing.Optional[re.Match] = OUTPUT_NAME_PATTERN.match(filename)

        if not name_match:
            raise ValueError(
                f"Cannot create a '{cls.__qualname__}' from '{filename}' - it is not a valid name. "
                f"The pattern must match '{OUTPUT_NAME_PATTERN.pattern}'"
            )

        name_parts: dict[str, str | None] = name_match.groupdict()

        return cls(**name_parts)

    def __str__(self):
        representation: str = f"{self.model}.t"

        if self.date:
            representation += self.date

        representation += f"{self.cycle}z.{self.configuration}.{self.model_output_type}"

        if self.member:
            representation += f"_{self.member}"

        representation += "."

        if self.frame:
            representation += f"{self.frame}."

        representation += f"{self.domain}.{self.output_format}"

        return representation

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
        self.time_dimension: str = "time"
        """The name of the temporal dimension for the variable of note"""

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
            "--time-dimension",
            "-t",
            default=self.time_dimension,
            dest="time_dimension",
            help="The name of the temporal dimension on the variable of note"
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

    if feature_field not in dataset:
        raise KeyError(f"Can't select '{feature_field}={feature}' - '{feature_field}' is not in the dataset.")

    data: xarray.DataArray = dataset[variable].reset_coords(drop=True)

    if feature_field in data.coords:
        data = data.sel({feature_field: int(float(feature))})
        LOGGER.info(f"'{variable}({feature_field}={feature})' selected ")
    elif len(dataset[feature_field].dims) == 1 and dataset[feature_field].dims[0] in data.dims:
        feature_dimension = dataset[feature_field].dims[0]
        LOGGER.info(
            f"'{feature_field}' is not a coordinate, but references '{feature_dimension}' singularly, "
            f"which is a coordinate of '{variable}' while '{feature_dimension}' has no assigned coordinate. "
            f"Now indexing '{feature_dimension}' by '{feature_field}'. This will change the name of '{feature_field}' "
            f"to '{feature_dimension}'."
        )
        indexed_dataset: xarray.Dataset = dataset.set_index({feature_dimension: feature_field})
        data = indexed_dataset[variable].sel({feature_dimension: int(float(feature))})
    else:
        raise KeyError(
            f"While {feature_field}({', '.join(map(str, dataset[feature_field].dims))}) is in '{path}', "
            f"it cannot be used to access {variable}({', '.join(map(str, data.dims))})"
        )

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

    filename_details: NWMName = NWMName.parse(arguments.url)

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

    name_remapping: dict[str, str] = {
        str([coordinate for coordinate in series.coords.keys() if coordinate != arguments.time_dimension][0]): FEATURE_COLUMN_NAME
    }

    if list(name_remapping.keys())[0] != arguments.feature_variable:
        series = series.rename(name_remapping)

    reference_time: numpy.datetime64 = (
        series.coords[arguments.time_dimension] - series.coords[arguments.time_dimension].diff(arguments.time_dimension)
    ).min().dt.date.item()

    dataframe: pandas.DataFrame = series.to_dataframe()

    dataframe[CYCLE_COLUMN_NAME] = filename_details.cycle
    dataframe[DOMAIN_COLUMN_NAME] = filename_details.domain
    dataframe[CONFIGURATION_COLUMN_NAME] = filename_details.configuration
    dataframe[MODEL_OUTPUT_TYPE_COLUMN_NAME] = filename_details.model_output_type
    dataframe[MEMBER_COLUMN_NAME] = filename_details.member
    dataframe[FRAME_COLUMN_NAME] = filename_details.frame
    dataframe[DATE_COLUMN_NAME] = reference_time

    output_path: pathlib.Path | None = arguments.output_path

    if isinstance(output_path, pathlib.Path) and output_path.is_dir():
        if not re.search(r"\d{8}$", output_path.parent.name):
            output_path = output_path / f"{filename_details.model}.{reference_time.strftime('%Y%m%d')}"
        output_path = output_path / filename_details.create_feature_filename(
            feature_id=arguments.feature,
            variable=arguments.variable,
            file_format="csv"
        )

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        dataframe.to_csv(path_or_buf=output_path)
        LOGGER.info(f"{arguments.variable}({arguments.feature_variable}={arguments.feature}) saved to '{output_path}'")
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

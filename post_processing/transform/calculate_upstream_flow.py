#!/usr/bin/env python3
"""
Contains the logic for calculating upstream flow
"""
import typing
import pathlib
import logging
import enum

try:
    from post_processing.configuration import settings
    from post_processing.enums import Verbosity
    LOG_FORMAT: str = settings.log_format
    DATE_FORMAT: str = settings.date_format
    NETCDF_ENGINE: typing.Literal['netcdf4', 'h5netcdf'] = settings.default_netcdf_engine
    LAZY_LOAD: bool = settings.lazy_load_netcdf
    PRINT_DETAILED_INFORMATION: bool = settings.verbosity >= Verbosity.LOUD
except ImportError:
    settings = None
    NETCDF_ENGINE = "netcdf4"
    LAZY_LOAD: bool = True
    LOG_FORMAT = "[%(asctime)s] %(levelname)s %(name)s: %(message)s"
    DATE_FORMAT = "%Y-%m-%d %H:%M:%S%z"
    PRINT_DETAILED_INFORMATION: bool = False

LOGGER: logging.Logger = logging.getLogger(pathlib.Path(__file__).stem)

class RoutelinkFormat(enum.StrEnum):
    """
    An enumeration of what formats a routelink may be loaded as
    """
    GEOPACKAGE = "geopackage"
    CSV = "csv"
    NETCDF = "netcdf"


def calculate_upstream_flow(
    input_path: typing.Union[pathlib.Path, str],
    output_path: typing.Union[pathlib.Path, str],
    routelink_path: typing.Union[pathlib.Path, str],
    variable: str = "streamflow",
    target_variable: str = "upstreamflow",
    routelink_to_variable: str = "to",
    routelink_from_variable: str = "link",
    routelink_format: RoutelinkFormat = RoutelinkFormat.NETCDF,
    *,
    encoding: typing.Mapping[str, typing.Any] = None,
    **attributes
) -> pathlib.Path:
    """
    Add an upstream flow variable

    The sum of all streamflow leading into a feature. Requires a routelink.

    :param input_path: Path to the file containing streamflows to base upstream flow off of
    :param output_path: Where to put the resulting data
    :param routelink_path: The location of the routelink file. A routelink contains mappings from a feature to its downstream location
    :param variable: The name of the variable within the file at `input_path` that contains streamflow values
    :param target_variable: What to name the upstream flow variable
    :param routelink_to_variable: The name of the field that details where a feature_id leads
    :param routelink_from_variable: The name of the field that details the ids of the features that lead to the
        downstream feature. The feature_id variable is not guaranteed to contain the actual values.
    :param routelink_format: The file format of the routelink
    :param encoding: Values used to dictate how the new variable is written to the resulting value
    :param attributes: Specialized attributes to add to the resulting variable
    :returns: The path to the generated data
    """
    if isinstance(input_path, str):
        input_path = pathlib.Path(input_path)
    if isinstance(output_path, str):
        output_path = pathlib.Path(output_path)
    if isinstance(routelink_path, str):
        routelink_path = pathlib.Path(routelink_path)
    if input_path == output_path:
        raise ValueError(
            f"Upstreamflow calculation is not an inplace operation - the input path and output path cannot match"
        )
    import xarray
    import pandas
    import numpy

    import tempfile
    import shutil

    if "long_name" not in attributes:
        attributes['long_name'] = "Upstream River Flow"

    try:
        from post_processing.utilities.netcdf import load_netcdf
        from post_processing.utilities.netcdf import save_netcdf
        from post_processing.utilities.netcdf import load_variable
    except ImportError:
        from functools import partial
        load_netcdf = partial(
            xarray.open_dataset,
            chunks={} if LAZY_LOAD else None,
            engine=NETCDF_ENGINE,
        )
        def load_variable(path: pathlib.Path, variable_name: str) -> xarray.DataArray:
            with xarray.open_dataset(path, engine=NETCDF_ENGINE, chunks={} if LAZY_LOAD else None) as dataset:
                if variable_name not in dataset:
                    raise KeyError(f"'{variable_name}' is not a variable within '{path}'")
                return dataset[variable_name].compute()

        def save_netcdf(path: typing.Union[str, pathlib.Path], dataset: xarray.Dataset):
            dataset.to_netcdf(
                path=path,
                engine=NETCDF_ENGINE,
            )

    if encoding is None:
        encoding = {}

    with tempfile.TemporaryDirectory(dir=settings.intermediate_directory) as temporary_directory:
        temporary_path: pathlib.Path = pathlib.Path(temporary_directory)
        temporary_output_path: pathlib.Path = temporary_path / output_path.name
        with load_netcdf(input_path) as data_to_transform:
            raw_data = data_to_transform[variable].values

            if routelink_format == RoutelinkFormat.GEOPACKAGE:
                import geopandas
                routelink = geopandas.read_file(routelink_path, driver="GPKG")
                from_values = routelink[routelink_from_variable]
                to_values = routelink[routelink_to_variable]
            elif routelink_format == RoutelinkFormat.CSV:
                routelink: pandas.DataFrame = pandas.read_csv(input_path)
                from_values = routelink[routelink_from_variable]
                to_values = routelink[routelink_to_variable]
            elif routelink_format == RoutelinkFormat.NETCDF:
                from_values = load_variable(path=routelink_path, variable_name=routelink_from_variable).values
                to_values = load_variable(path=routelink_path, variable_name=routelink_to_variable).values
            else:
                raise ValueError(
                    f"Cannot load the routelink needed to calculate upstream flow - "
                    f"'{routelink_format}' is not a supported format"
                )

            # TODO: This may lead to issues if the length of the arrays aren't the same - it's linking on array index,
            #  not index value

            # Create a series containing the raw data, then group it by where the values lead
            #   * Based on the routelink structure, a single feature may have multiple features pointing at it,
            #       but will only ever point to, at most, one feature
            series: pandas.Series = pandas.Series(raw_data)
            upstream_values: pandas.Series = series.groupby(to_values).sum()

            # Create a mapping of feature ids to their upstream flow values
            #   * Provides an easier access pattern to the values based off of feature_id - going by Series isn't worth it
            organized_values: typing.Dict[int, float] = upstream_values.to_dict()

            # Create a new array of values, in the order of the 'from' values matching the organized_values.
            # The 'to' values won't be in the order of the 'from' values and there won't be matches for all 'from' values
            #   * numpy.vectorize is used here for a large performance improvement based on the relatively simple operation
            mapped_flow: numpy.ndarray = numpy.vectorize(organized_values.get)(from_values)
            encoding: typing.Dict[str, typing.Any] = {**data_to_transform[variable].encoding, **encoding}

            # Create the upstreamflow variable and add it to the dataset
            upstreamflow_variable = xarray.Variable(
                dims=data_to_transform[variable].dims,
                data=mapped_flow,
                attrs={**data_to_transform[variable].attrs, **attributes},
            )

            # Make sure that there aren't any 'None' values from the above 'get' operation and instead hold
            # the missing_value or '_FillValue' encoding value
            fill_value: typing.Union[int, float] = encoding.get("missing_value")

            if fill_value is None:
                fill_value = encoding.get("_FillValue")

            if fill_value is None:
                raise ValueError(
                    f"A fill value could not be found on '{input_path}::{variable}' - "
                    f"'{target_variable}' cannot be encoded and written to '{output_path}'"
                )

            # Add the 'add_offset' encoding value to the resulting value. This often ensures that the stored data is of the
            # correct data type
            add_offset: typing.Optional[float] = encoding.get('add_offset')

            if add_offset is None:
                LOGGER.warning(
                    f"No 'add_offset' encoding was found on '{target_variable}' - it may not be encoded as the right type"
                )
            else:
                fill_value += add_offset

            upstreamflow_variable = upstreamflow_variable.fillna(fill_value)

            upstreamflow_variable.encoding.update(encoding)

            data_to_transform[target_variable] = upstreamflow_variable

            try:
                save_netcdf(path=temporary_output_path, dataset=data_to_transform)
            except OSError:
                LOGGER.error(
                    f"Could not write the modified version of '{input_path}' with the new '{target_variable}' variable to '{output_path}'"
                )
                raise
        shutil.move(temporary_output_path, output_path)

        if PRINT_DETAILED_INFORMATION:
            LOGGER.debug(f"Saved the updated version of '{input_path}' to '{output_path}'")

    return output_path


def main() -> int:
    """
    A basic example of how to use the contained code
    """
    import argparse
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description=f"A simple test for {__file__}",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "input_path",
        type=pathlib.Path,
        help="What data to create upstreamflow for"
    )
    parser.add_argument(
        "output_path",
        type=pathlib.Path,
        help="Where to store the output"
    )
    parser.add_argument(
        "routelink_path",
        type=pathlib.Path,
        help="The path to the routelink to use"
    )
    parser.add_argument(
        "-v",
        "--variable",
        dest="variable",
        type=str,
        default="streamflow",
        help="The variable containing the input data"
    )
    parser.add_argument(
        "-n",
        "--target-variable",
        dest="target_variable",
        type=str,
        default="upstreamflow",
        help="The name of the variable to create"
    )
    parser.add_argument(
        "-t",
        "--to-field",
        type=str,
        dest="routelink_to_variable",
        default='to',
        help="The field in the routelink that dictates where variable values lead"
    )
    parser.add_argument(
        "-f",
        "--from-field",
        type=str,
        dest="routelink_from_variable",
        default="link",
        help="The field in the routelink that dictates where variable values came from"
    )
    parser.add_argument(
        "-r",
        "--routelink-format",
        dest="routelink_format",
        type=RoutelinkFormat,
        default=RoutelinkFormat.NETCDF,
        help="The format of the routelink to use"
    )

    parameters: argparse.Namespace = parser.parse_args()

    generated_file_path: pathlib.Path = calculate_upstream_flow(**vars(parameters))

    # Load and print the generated data
    import xarray
    output_dataset: xarray.Dataset = xarray.open_dataset(
        generated_file_path,
        chunks={} if LAZY_LOAD else None,
        engine=NETCDF_ENGINE
    )
    output_dataset.info()
    return 0

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format=LOG_FORMAT,
        datefmt=DATE_FORMAT,
    )
    import sys
    sys.exit(main())

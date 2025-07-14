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
    LOG_FORMAT: str = settings.log_format
    DATE_FORMAT: str = settings.date_format
except ImportError:
    settings = None
    LOG_FORMAT = "[%(asctime)s] %(levelname)s %(name)s: %(message)s"
    DATE_FORMAT = "%Y-%m-%d %H:%M:%S%z"

LOGGER: logging.Logger = logging.getLogger(pathlib.Path(__file__).stem)

class RoutelinkFormat(enum.StrEnum):
    """
    An enumeration of what formats a routelink may be loaded as
    """
    GEOPACKAGE = "geopackage"
    CSV = "csv"
    NETCDF = "netcdf"


def calculate_upstream_flow(
    input_path: pathlib.Path,
    output_path: pathlib.Path,
    routelink_path: pathlib.Path,
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
    import xarray
    import pandas
    import numpy
    import geopandas

    if encoding is None:
        encoding = {}

    data_to_transform: xarray.Dataset = xarray.open_dataset(input_path, chunks={})
    raw_data = data_to_transform[variable].values

    if routelink_format == RoutelinkFormat.GEOPACKAGE:
        routelink = geopandas.read_file(routelink_path, driver="GPKG")
        from_values = routelink[routelink_from_variable]
        to_values = routelink[routelink_to_variable]
    elif routelink_format == RoutelinkFormat.CSV:
        routelink = pandas.read_csv(input_path)
        from_values = routelink[routelink_from_variable]
        to_values = routelink[routelink_to_variable]
    elif routelink_format == RoutelinkFormat.NETCDF:
        routelink = xarray.open_dataset(routelink_path, chunks={})
        from_values = routelink[routelink_from_variable].values
        to_values = routelink[routelink_to_variable].values
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
    upstream_values: pandas.Series = pandas.Series(raw_data).groupby(to_values).sum()

    # Create a mapping of feature ids to their upstream flow values
    #   * Provides an easier access pattern to the values based off of feature_id - going by Series isn't worth it
    organized_values: typing.Dict[int, float] = upstream_values.to_dict()

    # Create a new array of values, in the order of the 'from' values matching the organized_values.
    # The 'to' values won't be in the order of the 'from' values and there won't be matches for all 'from' values
    #   * numpy.vectorize is used here for a large performance improvement based on the relatively simple operation
    mapped_flow: numpy.ndarray = numpy.vectorize(organized_values.get)(from_values)

    # Create the upstreamflow variable and add it to the dataset
    upstreamflow_variable = xarray.Variable(
        dims=data_to_transform[variable].dims,
        data=mapped_flow,
        attrs={**attributes, **data_to_transform[variable].attrs},
        encoding={**encoding, **data_to_transform[variable].encoding}
    )
    data_to_transform[target_variable] = upstreamflow_variable

    # Make sure that there aren't any 'None' values from the above 'get' operation and instead hold
    # the missing_value or '_FillValue' encoding value
    fill_value: typing.Union[int, float] = upstreamflow_variable.encoding.get("missing_value")

    if fill_value is None:
        fill_value = upstreamflow_variable.encoding.get("_FillValue")

    if fill_value is None:
        raise ValueError(
            f"A fill value could not be found on '{input_path}::{variable}' - "
            f"'{target_variable}' cannot be encoded and written to '{output_path}'"
        )

    # Add the 'add_offset' encoding value to the resulting value. This often ensures that the stored data is of the
    # correct data type
    add_offset: typing.Optional[float] = upstreamflow_variable.encoding.get('add_offset')

    if add_offset is None:
        LOGGER.warning(
            f"No 'add_offset' encoding was found on '{target_variable}' - it may not be encoded as the right type"
        )
    else:
        fill_value += add_offset

    data_to_transform[target_variable] = upstreamflow_variable.fillna(fill_value)
    data_to_transform.to_netcdf(output_path)

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
    output_dataset: xarray.Dataset = xarray.open_dataset(generated_file_path, chunks={})
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

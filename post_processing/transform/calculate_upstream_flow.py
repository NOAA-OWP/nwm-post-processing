#!/usr/bin/env python3
"""
Contains the logic for calculating upstream flow
"""
import typing
import pathlib
import logging
import enum
import dataclasses

import collections.abc as generic

from post_processing.configuration import settings
from post_processing.enums import Verbosity
from post_processing.utilities.common import timed_function
from post_processing.schema.base import member

if typing.TYPE_CHECKING:
    import numpy

LOGGER: logging.Logger = logging.getLogger(pathlib.Path(__file__).stem)

class RoutelinkFormat(enum.StrEnum):
    """
    An enumeration of what formats a routelink may be loaded as
    """
    GEOPACKAGE = "geopackage"
    CSV = "csv"
    NETCDF = "netcdf"

@dataclasses.dataclass
class Linkage:
    path: pathlib.Path | str
    to_key: str
    from_key: str
    to_: typing.Optional["numpy.ndarray"] = member(default=None)
    from_: typing.Optional["numpy.ndarray"] = member(default=None)
    format: RoutelinkFormat = dataclasses.field(default=RoutelinkFormat.NETCDF)

    def __post_init__(self):
        if isinstance(self.path, str):
            self.path = pathlib.Path(self.path)

        if self.format == RoutelinkFormat.GEOPACKAGE:
            import geopandas
            routelink = geopandas.read_file(self.path, driver="GPKG")
            from_values = routelink[self.from_key].values
            to_values = routelink[self.to_key].values
        elif self.format == RoutelinkFormat.CSV:
            import pandas
            routelink: pandas.DataFrame = pandas.read_csv(self.path)
            from_values = routelink[self.from_key].values
            to_values = routelink[self.to_key].values
        elif self.format == RoutelinkFormat.NETCDF:
            from post_processing.utilities.netcdf import load_variable
            from post_processing.utilities.netcdf import operate_on_variable
            LOGGER.debug(f"Loading the '{self.from_key}' variable from '{self.path}'")
            from_values = operate_on_variable(
                path=self.path,
                variable_name=self.from_key,
                operation=lambda array: array.data
            )
            LOGGER.debug(f"Loading the '{self.to_key}' variable from '{self.path}'")
            to_values = operate_on_variable(
                path=self.path,
                variable_name=self.to_key,
                operation=lambda array: array.data
            )
        else:
            raise ValueError(
                f"Cannot load the routelink needed to calculate upstream flow - "
                f"'{self.format}' is not a supported format"
            )

        self.to_ = to_values
        self.from_ = from_values

    def __del__(self):
        del self.to_
        del self.from_

class _RouteLinks:
    def __init__(self):
        import threading
        self.__links: dict[tuple[pathlib.Path, RoutelinkFormat, str, str], Linkage] = {}
        self.__lock: threading.RLock = threading.RLock()

    def get_linkage(self, path: pathlib.Path, format: RoutelinkFormat, to_key: str, from_key: str) -> Linkage:
        with self.__lock:
            key: tuple[pathlib.Path, RoutelinkFormat, str, str] = (path, format, to_key, from_key)

            if key not in self.__links:
                self.__links[key] = Linkage(path=path, to_key=to_key, from_key=from_key, format=format)

            return self.__links[key]

    def clear(self):
        with self.__lock:
            while self.__links:
                linkage = self.__links.popitem()
                del linkage


ROUTELINKS = _RouteLinks()


def clean():
    ROUTELINKS.clear()


@timed_function()
def calculate_upstream_flow_binned(
    input_path: typing.Union[pathlib.Path, str, generic.Sequence[pathlib.Path]],
    output_path: typing.Union[pathlib.Path, str, generic.Sequence[pathlib.Path]],
    routelink_path: typing.Union[pathlib.Path, str],
    variable: str = "streamflow",
    target_variable: str = "upstreamflow",
    routelink_to_variable: str = "to",
    routelink_feature_variable: str = "link",
    routelink_format: RoutelinkFormat = RoutelinkFormat.NETCDF,
    work_directory: pathlib.Path = None,
    *,
    encoding: typing.Mapping[str, typing.Any] = None,
    **attributes
) -> pathlib.Path | generic.Sequence[pathlib.Path]:
    """
    Add an upstreamflow variable calculated via numpy bincounting

    :param input_path: Where to find the input data
    :param output_path: Where to save the resulting data
    :param routelink_path: Where to find the routelink data
    :param variable: The name of the variable whose data to use as input
    :param target_variable: The name of the variable that will hold the resulting data
    :param routelink_to_variable: The name of the variable that holds the reference to the downstream feature
    :param routelink_feature_variable: The name of the variable in the routelink that describes the name for each entry in the routelink
    :param routelink_format: How the routelink is stored
    :param work_directory: Where intermediate data should be saved
    :param encoding: Override properties to use when encoding the resuling data
    :param attributes: Extra attributes to assign to the new variable
    :returns: The path to the file that contains the netcdf with the new upstreamflow variable
    """
    input_is_paths: bool = (
        isinstance(input_path, generic.Sequence)
        and not isinstance(input_path, (str, bytes, generic.Mapping))
        and all(map(lambda path: isinstance(path, (str, pathlib.Path)), input_path))
    )
    output_is_paths: bool = (
        isinstance(output_path, generic.Sequence)
        and not isinstance(output_path, (str, bytes, generic.Mapping))
        and all(map(lambda path: isinstance(path, (str, pathlib.Path)), output_path))
    )

    if input_is_paths and output_is_paths and len(input_path) == len(output_path):
        results: list[pathlib.Path] = [
            calculate_upstream_flow_binned(
                input_path=read_path,
                output_path=write_path,
                routelink_path=routelink_path,
                variable=variable,
                target_variable=target_variable,
                routelink_to_variable=routelink_to_variable,
                routelink_feature_variable=routelink_feature_variable,
                routelink_format=routelink_format,
                work_directory=work_directory,
                encoding=encoding,
                **attributes
            )
            for read_path, write_path in zip(input_path, output_path)
        ]
        return results

    import xarray
    import numpy
    import tempfile
    import shutil

    import pandas

    from post_processing.utilities.netcdf import load_netcdf
    from post_processing.utilities.netcdf import save_netcdf

    if work_directory is None:
        work_directory = settings.intermediate_directory

    if encoding is None:
        encoding = {}

    if isinstance(output_path, str):
        output_path = pathlib.Path(output_path)

    with tempfile.TemporaryDirectory(dir=work_directory) as temporary_directory:
        temporary_path: pathlib.Path = pathlib.Path(temporary_directory)
        temporary_output: pathlib.Path = temporary_path / output_path.name

        with load_netcdf(input_path, chunks="auto") as input_data:
            linkage: Linkage = ROUTELINKS.get_linkage(
                path=routelink_path,
                format=routelink_format,
                to_key=routelink_to_variable,
                from_key=routelink_feature_variable
            )

            input_variable: xarray.DataArray = input_data[variable]
            input_feature_ids: numpy.typing.NDArray = input_variable[list(input_variable.sizes)[0]].to_numpy()
            data: numpy.typing.NDArray[numpy.floating] = input_variable.to_numpy()

            if len(data.shape) > 2:
                raise ValueError(f"Only 1 and 2 dimensional upstreamflow calculation is currently supported")

            LOGGER.debug(f"Creating a specialized index to link feature ids with the 'to' array")
            target_feature_index_position: numpy.typing.NDArray[numpy.integer] = pandas.Index(input_feature_ids).get_indexer(linkage.to_)
            LOGGER.debug(f"Specialized index has been created")

            inflow_mask: numpy.typing.NDArray[numpy.bool_] = (
                (linkage.to_ >= input_feature_ids.min()) &
                (linkage.to_ <= input_feature_ids.max()) &
                (target_feature_index_position >= 0)
            )

            if '_FillValue' in input_variable.encoding:
                fill_value = input_variable.encoding['_FillValue']
            elif "missing_value" in input_variable.encoding:
                fill_value = input_variable.encoding['missing_value']
            else:
                fill_value = numpy.nan

            output_array: numpy.typing.NDArray = numpy.full(
                shape=data.shape,
                fill_value=fill_value,
                dtype=input_variable.dtype
            )

            if len(data.shape) == 2:
                LOGGER.debug(f"Starting to perform the upstream bincount")
                for second_dimension_position in range(data.shape[1]):
                    output_array[:, second_dimension_position] = numpy.bincount(
                        target_feature_index_position[inflow_mask],
                        weights=data[inflow_mask, second_dimension_position],
                        minlength=data.shape[0]
                    )
                LOGGER.debug(f"Upstream bincount is complete")
            else:
                LOGGER.debug(f"Starting to perform the upstream bincount")
                output_array[:] = numpy.bincount(
                    target_feature_index_position[inflow_mask],
                    weights=data[inflow_mask],
                    minlength=data.shape[0],
                )
                LOGGER.debug(f"Upstream bincount is complete")

            LOGGER.debug(f"Finding locations that need to have the fill value applied")
            inbound_count: numpy.typing.NDArray[numpy.integer] = numpy.bincount(
                target_feature_index_position[inflow_mask],
                minlength=data.shape[0]
            )
            has_inbound_flow: numpy.typing.NDArray[numpy.bool_] = inbound_count > 0

            LOGGER.debug(f"Creating the fill value mask")
            overwrite: numpy.typing.NDArray = numpy.asarray(fill_value, dtype=output_array.dtype)
            LOGGER.debug(f"Overwriting values with the fill value...")
            output_array[~has_inbound_flow] = overwrite

            LOGGER.debug("Adding everything to the upstream value array")
            upstreamflow: xarray.DataArray = xarray.DataArray(
                name=target_variable,
                data=output_array,
                dims=input_variable.dims,
                attrs={
                    **input_variable.attrs,
                    "long_name": "Upstream River Flow",
                    **attributes
                },
            )

            input_data[target_variable] = upstreamflow
            input_data[target_variable].encoding = {
                **input_variable.encoding,
                **encoding
            }
            LOGGER.debug(f"Upstream value array created, added to the input data, and encoded.")
            save_netcdf(path=temporary_output, dataset=input_data)
            LOGGER.debug(f"Upstream flow data has been saved")
        shutil.move(temporary_output, output_path)
    return output_path


@timed_function()
def calculate_upstream_flow(
    input_path: typing.Union[pathlib.Path, str],
    output_path: typing.Union[pathlib.Path, str],
    routelink_path: typing.Union[pathlib.Path, str],
    variable: str = "streamflow",
    target_variable: str = "upstreamflow",
    routelink_to_variable: str = "to",
    routelink_feature_variable: str = "link",
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
    :param routelink_feature_variable: The name of the field that details the ids of the features that lead to the
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

    if settings.verbosity >= Verbosity.VERBOSE:
        LOGGER.debug(f"Calculating upstream flow on '{input_path}'")
    import xarray
    import pandas
    import numpy

    import tempfile
    import shutil

    if "long_name" not in attributes:
        attributes['long_name'] = "Upstream River Flow"

    from post_processing.utilities.netcdf import load_netcdf
    from post_processing.utilities.netcdf import save_netcdf

    if encoding is None:
        encoding = {}

    with tempfile.TemporaryDirectory(dir=settings.intermediate_directory) as temporary_directory:
        temporary_path: pathlib.Path = pathlib.Path(temporary_directory)
        temporary_output_path: pathlib.Path = temporary_path / output_path.name
        with load_netcdf(input_path) as data_to_transform:
            raw_data = data_to_transform[variable].data
            linkage: Linkage = ROUTELINKS.get_linkage(
                path=routelink_path,
                to_key=routelink_to_variable,
                from_key=routelink_feature_variable,
                format=routelink_format,
            )

            # TODO: This may lead to issues if the length of the arrays aren't the same - it's linking on array index,
            #  not index value

            # Create a series containing the raw data, then group it by where the values lead
            #   * Based on the routelink structure, a single feature may have multiple features pointing at it,
            #       but will only ever point to, at most, one feature
            series: pandas.Series = pandas.Series(raw_data)
            upstream_values: pandas.Series = series.groupby(linkage.to_).sum()
            upstream_values = upstream_values.reindex(linkage.from_).sort_index()
            upstream_values = upstream_values.fillna(
                data_to_transform[variable].encoding['_FillValue']
            )

            encoding: dict[str, typing.Any] = {**data_to_transform[variable].encoding, **encoding}

            # Create the upstreamflow variable and add it to the dataset
            upstreamflow_variable: xarray.DataArray = xarray.DataArray(
                name=target_variable,
                dims=data_to_transform[variable].dims,
                data=upstream_values.to_numpy(),
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
            data_to_transform[target_variable] = upstreamflow_variable
            data_to_transform[target_variable].encoding.update({
                **data_to_transform[variable].encoding,
                **encoding,
            })

            try:
                save_netcdf(path=temporary_output_path, dataset=data_to_transform)
            except:
                from post_processing.utilities.netcdf import describe_netcdf
                import os

                description: str = describe_netcdf(data_to_transform, variable_name=target_variable)
                LOGGER.error(
                    f"Could not write the modified version of '{input_path}' with the new '{target_variable}' "
                    f"variable to '{output_path}'. '{target_variable}':{os.linesep}"
                    f"{description}"
                )
                raise

        shutil.move(temporary_output_path, output_path)

        if settings.verbosity >= Verbosity.VERBOSE:
            LOGGER.debug(f"Saved the updated version of '{input_path}' to '{output_path}'")

    return output_path

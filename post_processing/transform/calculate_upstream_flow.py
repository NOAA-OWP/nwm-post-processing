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

from threading import RLock

import xarray
import numpy

from post_processing.configuration import settings
from post_processing.enums import Verbosity
from post_processing.utilities.common import timed_function
from post_processing.schema.base import member

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
    _lock: RLock = dataclasses.field(default_factory=RLock, init=False, repr=False, hash=False, compare=False)
    _last_alignment: typing.Optional[int] = dataclasses.field(default=None, init=False, repr=False, hash=False, compare=False)

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

    @property
    def to_values(self) -> numpy.ndarray:
        with self._lock:
            return self.to_

    @property
    def from_values(self) -> numpy.ndarray:
        with self._lock:
            return self.from_

    def realign(self, features: numpy.ndarray | xarray.DataArray):
        if isinstance(features, xarray.DataArray):
            features = features.data

        import pandas
        index: pandas.Index = pandas.Index(self.from_values)
        index_to_features = index.get_indexer(features)
        with self._lock:
            feature_hash: int = hash(tuple(features))
            if self._last_alignment is not None and feature_hash == self._last_alignment:
                return
            self.to_ = self.to_[index_to_features]
            self.from_ = self.from_[index_to_features]
            self._last_alignment = feature_hash

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

    import pandas

    from post_processing.utilities.netcdf import load
    from post_processing.utilities.netcdf import write

    if encoding is None:
        encoding = {}

    if isinstance(output_path, str):
        output_path = pathlib.Path(output_path)

    with load(input_path, chunks="auto") as input_data:
        linkage: Linkage = ROUTELINKS.get_linkage(
            path=routelink_path,
            format=routelink_format,
            to_key=routelink_to_variable,
            from_key=routelink_feature_variable
        )

        input_variable: xarray.DataArray = input_data[variable]
        input_feature_ids: numpy.typing.NDArray = input_variable[list(input_variable.sizes)[0]].to_numpy()

        # TODO: This should ideally return a new linkage rather than a mutated one for safety (linkage.to_
        #  could theoretically change between realignment and use), but in practice (for now) that's not going to
        #  happen
        linkage.realign(features=input_feature_ids)
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

        fill_value = numpy.nan

        output_array: numpy.typing.NDArray = numpy.full(
            shape=data.shape,
            fill_value=fill_value,
            dtype=numpy.float32
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
        write(target=output_path, dataset=input_data)
        LOGGER.debug(f"Upstream flow data has been saved")
    return output_path


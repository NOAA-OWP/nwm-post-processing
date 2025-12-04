"""
Task classes used for reading data
"""
import logging
import pathlib
import typing
import collections.abc as generic
import dataclasses

from typing import Concatenate

import xarray
import numpy

from post_processing.work.tasks import base
from post_processing.utilities.logging import get_logger
from post_processing.configuration import settings
from post_processing.work import exceptions
from post_processing.utilities.common import timed_function

from post_processing.interfaces.aliases import DataArrayFunction
from post_processing.interfaces.aliases import DatasetFunction

T = typing.TypeVar("T")
P = typing.ParamSpec("P")

LOGGER: logging.Logger = get_logger(__file__)

@timed_function()
def _load(
    target: pathlib.Path | generic.Sequence[pathlib.Path],
    load_kwargs: dict[str, typing.Any] = None,
    full_load: bool = False,
    chunks: str | dict[str, typing.Any] | None = "auto",
    engine: str = settings.default_netcdf_engine
) -> xarray.Dataset:
    """
    Load a netcdf file from disk

    :param target: Where the data to load exists
    :param load_kwargs: Additional arguments to feed to the lower level loading functions
    :param full_load: Whether to load all data and immediately close the netcdf file
    :param chunks: How to read chunks of data from the targetted netcdf file
    :param engine: What engine to use to read netcdf data
    :returns: The loaded dataset
    """
    if load_kwargs is None:
        load_kwargs = {}

    if "chunks" in load_kwargs:
        chunks = load_kwargs.pop("chunks")

    load_kwargs['cache'] = False

    if isinstance(target, generic.Sequence) and len(target) == 1:
        target = target[0]

    import time
    if isinstance(target, (pathlib.Path, str)):
        maximum_retries: int = 5
        attempts: int = 0
        last_exception: typing.Optional[Exception] = None
        while attempts < maximum_retries:
            try:
                if settings.this_is_very_verbose:
                    LOGGER.debug(f"Loading '{target}'", stack_info=True)

                if full_load:
                    if engine.lower() == "netcdf4":
                        import netCDF4
                        from xarray.backends import NetCDF4DataStore
                        netcdf4_dataset = netCDF4.Dataset(target)
                        datastore = NetCDF4DataStore(netcdf4_dataset)
                        dataset: xarray.Dataset = xarray.load_dataset(datastore)
                        dataset = dataset.load()
                        dataset.close()
                    else:
                        LOGGER.debug(f"Loading '{target}' in full, but without the specific netcdf4 approach")
                        dataset: xarray.Dataset = xarray.load_dataset(target, engine=engine, **load_kwargs).copy(deep=True)
                        dataset.close()
                else:
                    if settings.this_is_very_verbose:
                        LOGGER.debug(f"Loading '{target} lazily...")
                    dataset: xarray.Dataset = xarray.open_dataset(
                        target,
                        engine=engine,
                        chunks=chunks,
                        **load_kwargs
                    )

                return dataset

                # NOTE: It would be safer to load everything in full and move on, but that adds a significant
                # performance cost. For now, full loads won't be performed by default.
            except Exception as e:
                last_exception = e
                last_exception.args = (f"Could not load data at '{target}'. {e.args[0]}", *e.args[1:])
            attempts += 1
            LOGGER.error(
                f"Failed to load {target}{' due to ' + str(last_exception) if last_exception else ''}. "
                f"Waiting and trying again...",
                exc_info=last_exception,
            )
            time.sleep(1)

        if last_exception is None:
            raise RuntimeError(f"Could not load '{target}'")
        raise last_exception
    else:
        # Your IDE may complain about the `data` parameter - it is a false positive. A sequence of paths is fine
        dataset: xarray.Dataset = xarray.open_mfdataset(
            paths=target,
            chunks=chunks,
            combine="by_coords",
            engine=engine,
            **load_kwargs
        )
        if full_load:
            dataset = dataset.load()
            dataset.close()
        return dataset


@dataclasses.dataclass
class ArrayLoadTask(base.DataTask[numpy.typing.NDArray]):
    """
    Load and return the raw array for a Netcdf variable
    """
    @timed_function()
    def __call__(self) -> T:
        with _load(target=self.target, load_kwargs=self.kwargs, full_load=False, chunks="auto", engine=self.engine) as data:
            if self.variable_name not in data:
                raise KeyError(f"The '{self.variable_name}' variable is missing from '{self.target}'")

            variable: xarray.DataArray = data[self.variable_name]
            array: numpy.typing.NDArray = variable.data.copy()
            data.close()
        return array

    variable_name: str

    @classmethod
    def get_associated_error_type(cls) -> typing.Type[exceptions.GatewayError]:
        return exceptions.LoadCanceledByGatewayError

    def __str__(self):
        return f"Load the '{self.variable_name}' array from '{self.target}': {self.status}"


@dataclasses.dataclass
class LoadTask(base.DataTask[xarray.Dataset]):
    """
    The information needed to load xarray data from disk
    """

    @timed_function()
    def __call__(self) -> T:
        return _load(target=self.target, full_load=self.full_load, engine=self.engine, **self.kwargs)

    full_load: bool = dataclasses.field(default=False)

    def __str__(self):
        return f"Load {self.target}: {self.status}"

    @classmethod
    def get_associated_error_type(cls) -> typing.Type[exceptions.GatewayError]:
        return exceptions.LoadCanceledByGatewayError

@dataclasses.dataclass
class LoadVariableTask(base.DataTask[xarray.DataArray]):
    """
    Load a complete data array from a netcdf file
    """
    variable_name: str
    full_load: bool = dataclasses.field(default=False)

    @timed_function()
    def __call__(self) -> xarray.DataArray:
        with _load(target=self.target, load_kwargs=self.kwargs, full_load=False, chunks="auto", engine=self.engine) as data:
            if self.variable_name not in data:
                raise KeyError(
                    f"Cannot select '{self.variable_name}' from '{self.target}' - it is not a variable to select"
                )
            variable: xarray.DataArray = data[self.variable_name]

            if self.full_load:
                variable = variable.load().copy(deep=True)

            return variable

@dataclasses.dataclass
class TransformDatasetTask(base.DataTask[T]):
    """
    Perform a quick operation on a dataset and return the result
    """
    function: DatasetFunction
    load_kwargs: dict[str, typing.Any] = dataclasses.field(default_factory=dict)
    full_load: bool = dataclasses.field(default=False)

    def __str__(self):
        arguments: str = ', '.join(map(
            lambda pair: f"{pair[0]}={pair[1]}",
            self.kwargs.items()
        ))
        return (
            f"{self.__class__.__name__}: {self.function.__name__}(dataset{', ' + arguments if arguments else ''}) "
            f"on '{self.target}', {'fully loaded' if self.full_load else 'lazily loaded'}, via '{self.engine}'"
        )

    def __post_init__(self):
        super().__post_init__()

        if not callable(self.function):
            raise TypeError(
                f"The function for a {self.__class__.__name__} is expected to be a function but is instead "
                f"{self.function} (type={type(self.function)})"
            )
        if self.function.__name__ == "<lambda>":
            raise TypeError(
                f"The function for a {self.__class__.__name__} is expected to be a named function or constructor "
                f"but is instead an anonymous function/lambda"
            )

        if self.kwargs is None:
            self.kwargs = {}

        if self.load_kwargs is None:
            self.load_kwargs = {}

    def __call__(self) -> T:
        additional_arguments: dict[str, typing.Any] = {
            "engine": self.engine,
            "full_load": self.full_load,
            "chunks": None if self.full_load else "auto",
        }

        with _load(target=self.target, load_kwargs=self.load_kwargs, **additional_arguments) as data:
            result: T = self.function(data, **self.kwargs)

            if isinstance(result, (xarray.DataArray, xarray.Dataset)):
                LOGGER.debug(
                    f"The result from '{self}' is of type "
                    f"{type(result)} - there may be lingering connections to lazy elements or file caches that may "
                    f"cause segfaults down the line."
                )
            return result

@dataclasses.dataclass
class TransformVariableTask(base.DataTask[T]):
    """
    Perform a quick operation on a variable and return the result
    """
    variable_name: str
    function: DataArrayFunction
    full_load: bool = dataclasses.field(default=False)
    data_filter: typing.Optional[generic.Callable[[xarray.DataArray], xarray.DataArray]] = dataclasses.field(default=None)
    selector: typing.Optional[typing.Dict[str, typing.Any]] = dataclasses.field(default=None)
    selector_method: str = dataclasses.field(default=None)
    drop_unselected: bool = dataclasses.field(default=True)

    def __post_init__(self):
        super().__post_init__()

        if not callable(self.function):
            raise TypeError(
                f"The function for a {self.__class__.__name__} is expected to be a function but is instead "
                f"{self.function} (type={type(self.function)})"
            )
        if self.function.__name__ == "<lambda>":
            raise TypeError(
                f"The function for a {self.__class__.__name__} is expected to be a named function or constructor "
                f"but is instead an anonymous function/lambda"
            )

        if self.data_filter is not None and not callable(self.data_filter):
            raise TypeError(
                f"The data filter for {self.__class__.__name__} is supposed to be a callable object but is instead "
                f"'{self.data_filter}' (type={type(self.data_filter)})"
            )
        if self.data_filter is not None and self.data_filter.__name__ == "<lambda>":
            raise TypeError(
                f"The data filter for a {self.__class__.__name__} is expected to be a named function "
                f"but is instead an anonymous function/lambda"
            )

    @timed_function()
    def __call__(self) -> T:
        with _load(target=self.target, full_load=False, chunks="auto", engine=self.engine) as data:
            if self.variable_name not in data:
                raise KeyError(
                    f"Cannot select '{self.variable_name}' from '{self.target}' - it is not a variable to select"
                )
            variable: xarray.DataArray = data[self.variable_name]

            if isinstance(self.selector, generic.Mapping) and len(self.selector) > 0:
                variable = variable.sel(self.selector, method=self.selector_method, drop=self.drop_unselected)

            if self.data_filter is not None:
                variable = self.data_filter(variable)

            if self.full_load:
                variable = variable.compute().copy(deep=True)

            if settings.this_is_verbose:
                LOGGER.debug(
                    f"Calling {self.function.__qualname__}(<{self.variable_name}>"
                    f"{', ' + ', '.join(map(str, self.kwargs)) if self.kwargs else ''})"
                )

            try:
                if self.kwargs:
                    result: T = self.function(variable, **self.kwargs)
                else:
                    result: T = self.function(variable)
            except Exception as e:
                import os
                LOGGER.error(f"Failed to perform '{self.function}' due to {e}. Data: {os.linesep}{repr(data)}")
                raise e

            if isinstance(result, (xarray.DataArray, xarray.Dataset)):
                LOGGER.debug(
                    f"The result from '{self}' is of type "
                    f"{type(result)} - there may be lingering connections to lazy elements or file caches that may "
                    f"cause segfaults down the line."
                )

            return result


@dataclasses.dataclass
class SelectTask(base.DataTask[xarray.DataArray]):
    """
    Select data from an individual dataarray
    """
    variable_name: str
    criteria: dict[str, typing.Any]
    method: typing.Optional[str] = dataclasses.field(default=None)
    drop: bool = dataclasses.field(default=False)

    def __call__(self) -> xarray.DataArray:
        with _load(target=self.target, load_kwargs=self.kwargs, full_load=False, chunks="auto") as data:
            if self.variable_name not in data:
                raise KeyError(
                    f"Cannot select '{self.variable_name}' from '{self.target}' - it is not a variable to select"
                )
            variable: xarray.DataArray = data[self.variable_name]
            missing_dimensions: generic.Mapping[str, typing.Any] = {
                key: value
                for key, value in self.criteria.items()
                if key not in variable.sizes
            }

            if len(missing_dimensions) > 0:
                raise KeyError(
                    f"Cannot select data - the following dimensional criteria are not valid on '{self.variable_name}': "
                    f"{missing_dimensions}"
                )

            try:
                selected_data: xarray.DataArray = variable.sel(
                    self.criteria,
                    method=self.method,
                    drop=self.drop
                ).compute().copy(deep=True)
            except Exception as e:
                message: str = (
                    f"Failed to select data from '{self.target}::{self.variable_name}' based on criteria selecting "
                    f"labels from the following coordinates: {', '.join(map(str, self.criteria.keys()))}. "
                    f"Available coordinates are: {', '.join(map(str, variable.coords.keys()))}."
                )

                if len(self.criteria) == 1:
                    coordinate, desired_labels = list(self.criteria.items())[0]

                    if isinstance(desired_labels, generic.Sequence) and not isinstance(desired_labels, str) and len(desired_labels) < 10:
                        label_descriptions: list[str] = [
                            f"{label} (dtype={type(label)})"
                            for label in desired_labels
                        ]
                        message += (
                            f" Desired values for '{coordinate}' are: {', '.join(label_descriptions)}"
                        )
                    elif isinstance(desired_labels, str) or not isinstance(desired_labels, generic.Sequence):
                        message += f" The desired value for '{coordinate}' is: {desired_labels} (type={type(desired_labels)})"

                LOGGER.error(message)
                raise e
            data.close()
            return selected_data

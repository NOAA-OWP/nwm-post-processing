"""
Task classes used for reading data
"""
import logging
import pathlib
import typing
import collections.abc as generic
import dataclasses

import xarray
import numpy

from post_processing.work.tasks import base
from post_processing.utilities.logging import get_logger
from post_processing.configuration import settings
from post_processing.work import exceptions

T = typing.TypeVar("T")

LOGGER: logging.Logger = get_logger(__file__)


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
                    LOGGER.debug(f"Loading '{target} lazily...")
                    dataset: xarray.Dataset = xarray.open_dataset(target, engine=engine, chunks=chunks, **load_kwargs)

                return dataset

                # NOTE: It would be safer to load everything in full and move on, but that adds a significant
                # performance cost. For now, full loads won't be performed by default.
            except Exception as e:
                last_exception = e
                last_exception.args = (f"Could not load data at '{target}'. {e.args[0]}", *e.args[1:])
            attempts += 1
            LOGGER.error(
                f"Failed to load {target}{' due to ' + str(last_exception) if last_exception else ''}. "
                f"Waiting and trying again..."
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
            selected_data: xarray.DataArray = variable.sel(
                self.criteria,
                method=self.method,
                drop=self.drop
            ).compute().copy(deep=True)
            data.close()
            return selected_data

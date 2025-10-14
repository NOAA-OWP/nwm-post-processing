"""
The functions and objects used to define the operation to total a variables values over time
"""
import dataclasses
import typing
import collections.abc as generic
import pathlib
import tempfile
import shutil

from datetime import timedelta
import re

import numpy
import xarray

from post_processing.utilities import logging
from post_processing.schema import profile as base_profiles
from post_processing.utilities import netcdf
from post_processing.utilities.common import starmap_threaded
from post_processing.utilities.common import NWM_FILENAME_PATTERN
from post_processing.enums import TimeUnit

LOGGER: logging.Logger = logging.get_logger(__file__)


AccumulationFunction = typing.Callable[
    [
        pathlib.Path,
        pathlib.Path,
        str,
        str,
        timedelta,
        timedelta,
        str,
        str,
        dict[str, typing.Any],
        pathlib.Path
    ],
    pathlib.Path
]
"""A function that will read a file, convert its rate to a total, and save to disk"""


def calculate_time_bounds(
    input_path: pathlib.Path,
    output_path: pathlib.Path,
    duration: numpy.timedelta64 | timedelta,
    time_variable: str,
    output_variable_name: str,
    bound_dimension_name: str,
    attributes: dict[str, typing.Any],
    work_directory: pathlib.Path
) -> pathlib.Path:
    """
    The function that will accumulate the values from an individual variable from an individual file.
    Meant to be called in parallel.
    """
    if isinstance(duration, timedelta):
        duration: numpy.timedelta64 = numpy.timedelta64(duration)
    if not isinstance(duration, numpy.timedelta64):
        raise TypeError(f"Cannot create timebounds based on a duration of '{duration}' (type={type(duration)})")


    with netcdf.load(input_path, full_load=True) as netcdf_data:
        if time_variable not in netcdf_data.variables.keys():
            raise KeyError(
                f"Cannot accumulate data from '{input_path.name}::{time_variable}' - "
                f"there is no variable by that name"
            )

        time: xarray.DataArray = netcdf_data[time_variable]
        lower_bound: numpy.datetime64 = time.min(skipna=True).values - duration
        upper_bound: numpy.datetime64 = time.max(skipna=True).values
        data: numpy.typing.NDArray[numpy.datetime64] = numpy.array([[lower_bound, upper_bound]])

        time_bound_variable: xarray.DataArray = xarray.DataArray(
            name=output_variable_name,
            data=data,
            coords={
                time.name: time
            },
            dims=tuple([*time.dims, bound_dimension_name])
        )
        netcdf_data[output_variable_name] = time_bound_variable
        netcdf_data[output_variable_name].attrs.update(attributes)
        netcdf.write(target=output_path, dataset=netcdf_data)

    return output_path


@dataclasses.dataclass
class TimeBoundOperation(base_profiles.PathToPathOperation, base_profiles.FileOutputMixin):
    """
    Integrates the total value of a variable over time
    """
    @classmethod
    def operation(cls) -> base_profiles.OperationType:
        return base_profiles.OperationType.TIME_BOUND

    def __call__(
        self,
        profile: base_profiles.Profile,
        process_identifier: str,
        work_directory: pathlib.Path,
        data: typing.Sequence[pathlib.Path],
        previous_operations: generic.Sequence[base_profiles.ProfileOperation],
        metadata: dict[str, typing.Any]
    ) -> generic.Sequence[pathlib.Path]:
        if isinstance(data, pathlib.Path):
            data = [data]

        arguments: list[dict[str, typing.Any]] = []
        duration: timedelta = self.time_unit * self.amount_of_time

        for path in data:
            path_metadata: dict[str, typing.Any] = metadata.copy()
            filename_metadata_match: re.Match | None = NWM_FILENAME_PATTERN.search(path.name)

            if filename_metadata_match is not None:
                path_metadata.update({
                    key: value
                    for key, value in filename_metadata_match.groupdict().items()
                    if value is not None
                })

            output_path: pathlib.Path = self.get_output_path(
                work_directory=work_directory,
                input_path=path,
                **path_metadata
            )

            path_arguments: dict[str, typing.Any] = {
                "input_path": path,
                "output_path": output_path,
                "duration": duration,
                "time_variable": self.time_variable,
                "output_variable_name": self.output_name,
                "bound_dimension_name": self.bound_dimension_name,
                "attributes": self.time_bound_attributes.copy(),
                "work_directory": work_directory
            }

            arguments.append(path_arguments)

        files_with_accumulated_rates: generic.Sequence[pathlib.Path] = starmap_threaded(
            function=calculate_time_bounds,
            args=arguments
        )

        return files_with_accumulated_rates


    def __hash__(self) -> int:
        try:
            parent_hash: int = super().__hash__()
        except AttributeError:
            parent_hash = 0

        return hash((
            parent_hash,
            self.time_variable,
            self.time_unit,
            self.amount_of_time,
            self.bound_dimension_name,
            self.output_name,
            *[f"{key}={value}" for key, value in self.time_bound_attributes.items()]
        ))

    def __eq__(self, other):
        if not isinstance(other, self.__class__):
            return False
        return hash(self) == hash(other)

    time_variable: str = dataclasses.field(default="time")
    """The variable the specifies the times of the values"""
    time_unit: TimeUnit = dataclasses.field(default=TimeUnit.HOURS)
    """
    The unit of time over which the rate value was established. For instance, 'hours' if the rate was calculated 
    as the mean rate over 1 hour.
    """
    amount_of_time: int = dataclasses.field(default=1)
    """
    The number of time units over which the value models. For instance 1 if the time_unit is hours and the value of 
    8mm/s is the mean over an hour
    """
    output_name: str = dataclasses.field(default="time_bounds")
    """The name of the variable that will hold the new information"""
    bound_dimension_name: str = dataclasses.field(default="nv")
    """The name of the 2-item dimension that indicates the lower and upper bound"""
    time_bound_attributes: dict[str, typing.Any] = dataclasses.field(default_factory=dict)
    """
    Attributes that should be on the resulting variable
    """



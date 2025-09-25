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
from post_processing.transform.unit_conversion import convert_variable_unit

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


def accumulate_variable(
    input_path: pathlib.Path,
    output_path: pathlib.Path,
    input_variable_name: str,
    output_variable_name: str,
    temporal_unit: timedelta,
    aggregation_period: timedelta,
    quantity_unit: str,
    target_unit: str,
    attributes: dict[str, typing.Any],
    work_directory: pathlib.Path
) -> pathlib.Path:
    """
    The function that will accumulate the values from an individual variable from an individual file.
    Meant to be called in parallel.
    """
    multiplier: float = aggregation_period / temporal_unit

    with tempfile.TemporaryDirectory(dir=work_directory) as temporary_directory:
        temporary_directory_path: pathlib.Path = pathlib.Path(temporary_directory)
        temporary_output_path: pathlib.Path = temporary_directory_path / output_path.name

        with netcdf.load_netcdf(input_path, full_load=True) as netcdf_data:
            if input_variable_name not in netcdf_data.variables.keys():
                raise KeyError(
                    f"Cannot accumulate data from '{input_path.name}::{input_variable_name}' - "
                    f"there is no variable by that name"
                )
            if input_variable_name not in netcdf_data.data_vars.keys():
                raise KeyError(
                    f"Cannot accumulate data from '{input_path.name}::{input_variable_name}' "
                    f"- it is a coordinate, not a data variable."
                )
            variable: xarray.DataArray = netcdf_data[input_variable_name]
            accumulated_data: xarray.DataArray = variable * multiplier
            accumulated_data.name = output_variable_name

            if accumulated_data.dtype == numpy.float64:
                accumulated_data = accumulated_data.astype(numpy.float32)

            if quantity_unit != target_unit:
                accumulated_data = convert_variable_unit(
                    variable=accumulated_data,
                    to_unit=target_unit,
                    from_unit=quantity_unit
                )

            accumulated_data.attrs = {
                key: f"Accumulated {value}" if isinstance(value, str) and 'name' in key.lower() else value
                for key, value in variable.attrs.items()
            }
            accumulated_data.attrs['cell_methods'] = "time: sum"
            accumulated_data.attrs['units'] = target_unit

            accumulated_data.attrs.update(attributes)

            netcdf_data[output_variable_name] = accumulated_data
            netcdf_data[output_variable_name].encoding = {
                **variable.encoding,
                "units": target_unit,
            }
            netcdf_data[output_variable_name].attrs.update(attributes)
            netcdf.save_netcdf(path=temporary_output_path, dataset=netcdf_data)

        shutil.move(temporary_output_path, output_path)

    return output_path


@dataclasses.dataclass
class TotalOverTimeOperation(base_profiles.PathToPathOperation, base_profiles.FileOutputMixin):
    """
    Integrates the total value of a variable over time
    """
    @classmethod
    def operation(cls) -> base_profiles.OperationType:
        return base_profiles.OperationType.TOTAL_OVER_TIME

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

        function: AccumulationFunction = accumulate_variable

        arguments: list[dict[str, typing.Any]] = []
        aggregation_period: timedelta = timedelta(**{self.time_unit: self.amount_of_time})
        temporal_unit: timedelta = timedelta(**{self.input_time_unit: 1})

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
                "input_variable_name": self.rate_variable_name,
                "output_variable_name": self.total_variable_name,
                "temporal_unit": temporal_unit,
                "aggregation_period": aggregation_period,
                "quantity_unit": self.input_quantity_unit,
                "target_unit": self.output_unit,
                "attributes": self.total_variable_attributes.copy(),
                "work_directory": work_directory
            }

            arguments.append(path_arguments)

        files_with_accumulated_rates: generic.Sequence[pathlib.Path] = starmap_threaded(
            function=function,
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
            self.rate_variable_name,
            self.total_variable_name,
            self.output_unit,
            self.input_time_unit,
            self.time_unit,
            self.amount_of_time,
            *[f"{key}={value}" for key, value in self.total_variable_attributes.items()]
        ))

    def __eq__(self, other):
        if not isinstance(other, self.__class__):
            return False
        return hash(self) == hash(other)

    rate_variable_name: str
    """The name of the variable that is a rate"""
    total_variable_name: str
    """What the variable should be named"""
    output_unit: str
    """The unit that the value should be converted to"""
    input_quantity_unit: str
    """The unit of quantity (the unit over time) that the rate was measured in"""
    input_time_unit: str = dataclasses.field(default="seconds")
    """
    The unit of time that the rate spans. For instance, if the input rate was mm/s, the input time unit would be 'seconds'
    """
    time_unit: str = dataclasses.field(default="hours")
    """
    The unit of time over which the rate value was established. For instance, 'hours' if the rate was calculated 
    as the mean rate over 1 hour.
    """
    amount_of_time: int = dataclasses.field(default=1)
    """
    The number of time units over which the value models. For instance 1 if the time_unit is hours and the value of 
    8mm/s is the mean over an hour
    """
    total_variable_attributes: dict[str, typing.Any] = dataclasses.field(default_factory=dict)
    """
    Attributes that should be on the resulting variable
    """



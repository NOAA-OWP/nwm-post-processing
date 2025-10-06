"""
Defines an operation that combines two variables of the same length into one
"""
import typing
import collections.abc as generic
import pathlib
import logging
import dataclasses

import xarray
import numpy

import post_processing.schema.profile as base_profile
from post_processing.utilities.logging import get_logger
from post_processing.utilities import netcdf
from post_processing.utilities import common

LOGGER: logging.Logger = get_logger(__file__)

def combine(
    input_path: pathlib.Path,
    output_path: pathlib.Path,
    input_variables: generic.Sequence[str],
    name: str,
    new_dimension: str,
    attributes: generic.Mapping[str, typing.Any],
    encoding: generic.Mapping[str, typing.Any],
) -> pathlib.Path:
    with netcdf.load(input_path) as input_data:
        missing_variables: list[str] = [
            variable
            for variable in input_variables
            if variable not in input_data
        ]
        if len(missing_variables) > 0:
            raise KeyError(
                f"Cannot combine the {', '.join(input_variables)} variables because {', '.join(missing_variables)} "
                f"are not in {input_path}"
            )

        variables: list[xarray.DataArray] = [
            input_data[variable_name]
            for variable_name in input_variables
        ]

        shapes: set[tuple[int, ...]] = set(variable.shape for variable in variables)

        if len(shapes) > 1:
            shape_descriptions: list[str] = [
                f"{variable.name}={variable.shape}"
                for variable in variables
            ]
            raise ValueError(
                f"Cannot combine the following variables because their shapes don't all match: {', '.join(shape_descriptions)}"
            )

        combined_data: numpy.typing.NDArray = numpy.array(variables).transpose()
        combined_variable: xarray.DataArray = xarray.DataArray(
            name=name,
            data=combined_data,
            dims=(*variables[0].dims, new_dimension),
            coords={dimension: input_data[dimension] for dimension in variables[0].dims},
        )
        input_data[name] = combined_variable
        input_data[name].attrs = attributes
        input_data[name].encoding = encoding
        netcdf.write(dataset=input_data, target=output_path)
    return output_path

@dataclasses.dataclass
class CombineOperation(base_profile.PathToPathOperation, base_profile.FileOutputMixin):
    """
    An operation that combines two variables of the same shape into one
    """
    variables: list[str]
    name: str
    new_dimension: str
    attributes: dict[str, typing.Any] = dataclasses.field(default_factory=dict)
    encoding: dict[str, typing.Any] = dataclasses.field(default_factory=dict)

    def __hash__(self):
        try:
            parent_hash: int = super().__hash__()
        except:
            parent_hash = 0

        return hash((
            parent_hash,
            *self.variables,
            self.name,
            self.new_dimension,
            *self.attributes.items(),
            *self.encoding.items()
        ))

    @classmethod
    def operation(cls) -> base_profile.OperationType:
        return base_profile.OperationType.COMBINE

    def __call__(
        self,
        profile: base_profile.Profile,
        process_identifier: str,
        work_directory: pathlib.Path,
        data: typing.Sequence[pathlib.Path],
        previous_operations: list[base_profile.ProfileOperation],
        metadata: dict[str, typing.Any]
    ) -> generic.Sequence[pathlib.Path]:
        arguments: list[dict[str, typing.Any]] = []

        for path in data:
            arguments.append({
                "input_path": path,
                "output_path": self.get_output_path(
                    work_directory=work_directory,
                    input_path=path,
                    **metadata
                ),
                "input_variables": self.variables,
                "name": self.name,
                "new_dimension": self.new_dimension,
                "attributes": self.attributes,
                "encoding": self.encoding,
            })

        updated_files: generic.Sequence[pathlib.Path] = common.starmap_threaded(
            function=combine,
            args=arguments,
            thread_prefix=self.__class__.__name__,
        )

        return updated_files

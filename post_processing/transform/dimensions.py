"""
Functions and objects used to change dimensions on netcdf variables
"""
import typing
import collections.abc as generic
import pathlib

from post_processing.utilities import logging
from post_processing.configuration import settings
from post_processing.utilities.common import timed_function


LOGGER: logging.Logger = logging.get_logger(__file__)

# NOTE: The basic NCO operators aren't being used here because ncap2 drops encoding
@timed_function()
def adjust_dimensions(
    input_path: pathlib.Path,
    output_path: pathlib.Path,
    mapping: generic.Mapping[str, generic.Sequence[str]],
) -> pathlib.Path:
    """
    Change the dimensions of variables within a netcdf file

    :param input_path: The path to the netcdf file to change
    :param output_path: Where to write the output
    :param mapping: The mapping of variables with their new variable orders
    :returns: The path to the new netcdf file
    """
    import xarray

    from post_processing.utilities import netcdf

    all_dimensions: set[str] = set(dimension for dimensions in mapping.values() for dimension in dimensions)

    # NOTE: If you don't run with full_load=True, you run the risk of file handles being left open, causing segfaults
    with netcdf.load(input_path, full_load=True) as input_dataset:
        extra_dimensions: set[str] = set(all_dimensions).difference(input_dataset.sizes.keys())

        if extra_dimensions:
            raise KeyError(
                f"Cannot add dimensions to variables in '{input_path}' - the following dimensions aren't within "
                f"the dataset: '{', '.join(extra_dimensions)}'"
            )

        for variable_name, new_dimensions in mapping.items():
            if variable_name not in input_dataset:
                LOGGER.warning(
                    f"Cannot adjust the dimensions on the '{variable_name}' variable in '{input_path}' - "
                    f"it does not have a '{variable_name}' variable. Available variables are: "
                    f"{', '.join([*input_dataset.data_vars.keys()])}"
                )
                continue
            variable: xarray.DataArray = input_dataset[variable_name]
            axis_mapping: dict[str, int] = {
                new_dimension: intended_index
                for intended_index, new_dimension in enumerate(new_dimensions)
                if new_dimension not in variable.dims
            }
            if axis_mapping:
                original_encoding: dict[str, typing.Any] = variable.encoding.copy()
                input_dataset[variable_name] = variable.expand_dims(
                    dim=list(axis_mapping.keys()), axis=list(axis_mapping.values())
                )
                input_dataset[variable_name].encoding = original_encoding
        netcdf.write(target=output_path, dataset=input_dataset)

    if settings.this_is_verbose:
        LOGGER.debug(f"Variables from '{input_path}' have had their dimensions altered and saved in '{output_path}'")

    return output_path

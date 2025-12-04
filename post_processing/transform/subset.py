"""
Contains logic for subsetting netcdf files
"""
import os
import typing
import pathlib
import logging
import re

from collections import abc as generic
import concurrent.futures as futures

import numpy
import xarray

from post_processing import enums

from post_processing.utilities.common import timed_function
from post_processing.transform.subsetting.cache import MASK_PROVIDER
from post_processing.utilities.netcdf import submit_write
from post_processing.utilities.netcdf import load
from post_processing.configuration import settings
from post_processing.work import cycle_future_list
from post_processing.utilities.common import condense_exceptions

LOGGER: logging.Logger = logging.getLogger(pathlib.Path(__file__).stem)

T = typing.TypeVar("T")


@timed_function()
def mask_array(
    input_data: xarray.DataArray,
    mask: numpy.typing.NDArray[numpy.float32],
) -> xarray.DataArray:
    """
    Mask a numeric numpy array by an array of 1s and 0s

    :param input_data: The data to mask
    :param mask: The mask of 1s and 0s
    :return: A new data array with the originals metadata, but with all values not makes set to NaN
    """
    encoding: dict[str, typing.Any] = input_data.encoding.copy()
    masked_data: xarray.DataArray = input_data.where(mask)
    masked_data.attrs = input_data.attrs.copy()
    masked_data.encoding = encoding

    if settings.this_is_very_verbose:
        LOGGER.debug(f"Subset the {input_data.name} variable")
    return masked_data

def clean():
    """
    Clean out all resources that might have been used
    """
    try:
        MASK_PROVIDER.clean()
    except Exception as exc:
        LOGGER.debug(f"Could not clean the mask provider: {exc}", exc_info=True)

@timed_function()
def mask_dataset(
    input_path: pathlib.Path,
    masks: generic.Sequence[pathlib.Path],
    mask_variable: str,
    mask_coordinates: generic.Sequence[str],
    work_directory: pathlib.Path,
    output_pattern: str,
    identifier_pattern: re.Pattern,
    metadata: generic.Mapping[str, typing.Any],
    mask_metadata: generic.Mapping[pathlib.Path, generic.Mapping[str, typing.Any]],
) -> generic.Sequence[pathlib.Path]:
    """
    Iterate through each 2D+ mask and generate a new version of the input data with only the allowed data

    NOTE: This operates on multidimensional data - use post_processing.transform.subsetting.vector for 1D

    :param input_path: The path to the data to mask
    :param masks: The paths to each mask to use
    :param mask_variable: The variable in the mask files that contain the mask grid
    :param mask_coordinates: The coordinates in the mask files that contain the mask grid (deprecated)
    :param work_directory: The directory where temporary files may be placed
    :param output_pattern: The pattern to use when forming file names
    :param identifier_pattern: The pattern to use to identify variables within mask names
    :param metadata: Information that may be used to generate file names
    :param mask_metadata: General information about the masks in use
    :returns: The path to the results of each mask application
    """
    from post_processing.interfaces.work import PendingTaskResult
    masked_files: list[pathlib.Path] = []

    write_tasks: list[PendingTaskResult[pathlib.Path]] = []

    if settings.this_is_verbose:
        LOGGER.debug(f"Subsetting '{input_path}' by {len(masks)} masks")

    # TODO: Using a transform function rather than a full load may relieve memory pressure
    with load(target=input_path, full_load=True, load_kwargs=dict(chunks=None)) as input_data:
        if settings.this_is_very_verbose:
            LOGGER.debug(f"Loaded '{input_path}'")

        # TODO: With loads and writes now being run through its own gateway, this following section may probably be
        #  threaded
        for mask_path in masks:
            identifiers: dict[str, typing.Any] = dict(mask_metadata.get(mask_path, {}))
            identifier_match: re.Match | None = identifier_pattern.search(mask_path.name)

            if identifier_match is not None:
                identifiers.update(identifier_match.groupdict())

            if "rfc" in identifiers:
                if 'RFC_ABBREVIATION' not in identifiers:
                    rfc_abbreviation: enums.RFC | None = enums.RFC.from_string(identifiers['rfc'], strict=False)
                    if rfc_abbreviation is not None:
                        identifiers['RFC_ABBREVIATION'] = rfc_abbreviation
                identifiers['rfc'] = identifiers['rfc'].lower()

            formatting_options: dict[str, typing.Any] = {
                **metadata,
                "mask_variable": mask_variable,
                "mask_name": mask_path.stem,
                **identifiers,
            }
            try:
                output_name: str = output_pattern.format_map(formatting_options)
            except KeyError as key_error:
                LOGGER.error(
                    f"Could not substitute values when building an output name: {key_error}{os.linesep}"
                    f"Available Keys:{os.linesep}"
                    f"    - {(os.linesep + '    - ').join(map(lambda kv: str(kv[0]) + '=' + str(kv[1]), formatting_options.items()))}"
                )
                raise

            output_path: pathlib.Path = work_directory / mask_path.stem / output_name
            if settings.this_is_very_verbose:
                LOGGER.debug(f"Retrieving the mask at '{mask_path}' for '{input_path}'")
            mask_data: numpy.ndarray = MASK_PROVIDER.get_mask(path=mask_path, variable=mask_variable)
            mask_data[mask_data == 0] = numpy.nan
            if settings.this_is_very_verbose:
                LOGGER.debug(f"Retrieved the mask at '{mask_path}' for '{input_path}'")

            # TODO: Can this be threaded?
            masked_variables: dict[str, xarray.DataArray] = {
                variable_name: mask_array(input_data=variable.load().copy(deep=True), mask=mask_data)
                for variable_name, variable in input_data.data_vars.items()
                if variable.shape[-1 * len(mask_data.shape):] == mask_data.shape
            }

            copied_input_data: xarray.Dataset = xarray.Dataset(
                data_vars={
                    data_name: data_variable.copy()
                    for data_name, data_variable in input_data.data_vars.items()
                    if data_name not in masked_variables
                },
                coords={
                    coordinate_name: coordinate.copy()
                    for coordinate_name, coordinate in input_data.coords.items()
                },
                attrs=input_data.attrs.copy(),
            )

            for coordinate_name in copied_input_data.coords.keys():
                copied_input_data[coordinate_name].encoding = copied_input_data[coordinate_name].encoding.copy()

            while masked_variables:
                variable_name, variable = masked_variables.popitem()
                if isinstance(variable, xarray.Dataset):
                    LOGGER.warning(
                        f"Something happened and the '{variable_name}' variable from '{input_path.name}' "
                        f"was a dataset, not a data array."
                    )
                    variable = variable[variable_name]
                copied_input_data[variable_name] = variable
                copied_input_data[variable_name].encoding = variable.encoding.copy()

            copied_input_data.encoding.update(input_data.encoding)
            if settings.this_is_verbose:
                LOGGER.debug(f"Subset '{input_path}' by '{mask_path}")

            write_task: PendingTaskResult[pathlib.Path] = submit_write(dataset=copied_input_data, target=output_path)
            write_tasks.append(write_task)

        LOGGER.debug(f"Now waiting for subset files to be written for stage {metadata['stage']}")
        masked_files, errors = cycle_future_list(futures=write_tasks)

        if len(errors) == 1:
            raise errors[0]

        if errors:
            raise condense_exceptions(
                f"{len(errors)} errors were encountered trying to save masked datasets",
                exceptions=errors
            )

    return masked_files

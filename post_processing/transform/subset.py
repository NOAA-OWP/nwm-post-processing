"""
Contains logic for subsetting netcdf files
"""
import shutil
import typing
import pathlib
import logging
import re
import tempfile

from collections import abc as generic

import numpy
import xarray

from post_processing import enums

from post_processing.utilities.common import timed_function
from post_processing.transform.subsetting.cache import MASK_PROVIDER
from post_processing.utilities.netcdf import load_netcdf
from post_processing.utilities.netcdf import save_netcdf
from post_processing.configuration import settings


LOGGER: logging.Logger = logging.getLogger(pathlib.Path(__file__).name)

T = typing.TypeVar("T")


@timed_function()
def mask_array(
    input_data: xarray.DataArray,
    mask: numpy.typing.NDArray[numpy.float32],
) -> xarray.DataArray:
    encoding: dict[str, typing.Any] = input_data.encoding.copy()
    masked_data: xarray.DataArray = input_data * mask
    masked_data.encoding = encoding

    if settings.this_is_very_verbose:
        LOGGER.debug(f"Subset the {input_data.name} variable")
    return masked_data

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

    masked_files: list[pathlib.Path] = []

    with tempfile.TemporaryDirectory(dir=work_directory) as temporary_directory:
        temporary_directory_path: pathlib.Path = pathlib.Path(temporary_directory)
        files_to_move: list[pathlib.Path] = []

        LOGGER.debug(f"Subsetting '{input_path}' by {len(masks)} masks")
        with load_netcdf(input_path, full_load=True, chunks=None) as input_data:
            if settings.this_is_very_verbose:
                LOGGER.debug(f"Loaded '{input_path}'")
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

                output_name: str = output_pattern.format_map({
                    **metadata,
                    "mask_variable": mask_variable,
                    "mask_name": mask_path.stem,
                    **identifiers,
                })

                temporary_output_path: pathlib.Path = temporary_directory_path / output_name
                if settings.this_is_very_verbose:
                    LOGGER.debug(f"Retrieving the mask at '{mask_path}' for '{input_path}'")
                mask_data: numpy.ndarray = MASK_PROVIDER.get_mask(path=mask_path, variable=mask_variable)
                mask_data[mask_data == 0] = numpy.nan
                if settings.this_is_verbose:
                    LOGGER.debug(f"Retrieved the mask at '{mask_path}' for '{input_path}'")

                masked_variables: dict[str, xarray.DataArray] = {
                    variable_name: mask_array(input_data=variable, mask=mask_data)
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
                save_netcdf(path=temporary_output_path, dataset=copied_input_data)

                if not temporary_output_path.is_file():
                    raise FileNotFoundError(f"Data was supposed to be saved to '{temporary_output_path}' but it could not be found")
                files_to_move.append(temporary_output_path)

        missing_files: list[pathlib.Path] = list(filter(lambda file: not file.is_file(), files_to_move))

        if missing_files:
            raise FileNotFoundError(f"{len(missing_files)} temporary subset files were not found when preparing to move them.")

        for file_to_move in files_to_move:
            output_path: pathlib.Path = work_directory / file_to_move.name
            if output_path != file_to_move:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(file_to_move, output_path)
            masked_files.append(output_path)

    return masked_files

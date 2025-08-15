"""
Contains logic for subsetting netcdf files
"""
import os
import shutil
import typing
import pathlib
import logging

from collections import abc as generic


from post_processing.configuration import settings
from post_processing.utilities.common import timed_function

LOGGER: logging.Logger = logging.getLogger(pathlib.Path(__file__).name)

T = typing.TypeVar("T")

@timed_function(logger=LOGGER)
def subset_vector_file_into_file_by_value(
    input_file: pathlib.Path,
    mask: pathlib.Path,
    coordinate: str,
    work_directory: pathlib.Path,
    mask_coordinate: str = None,
    output_filename: str = None,
    identifiers: generic.Mapping[str, typing.Any] = None,
    output_pattern: str = None,
    drop: bool = True,
) -> pathlib.Path:
    """
    Subset a one-dimensional netcdf file on disk by a mask that is also on disk

    :param input_file: The path to the file to subset
    :param mask: the path to the file containing what values to include in the coordinate
    :param coordinate: The name of the coordinate variable within the input file that will be masked
    :param work_directory: The directory where data may be written
    :param mask_coordinate: The name of the coordinate variable in the mask containing the coordinates to keep
    :param output_filename: What to name the extracted data. The name will be generated as a mix between the mask and input file if not provided
    :param identifiers: Dictionary of identifiers to use when generating a name
    :param output_pattern: The format string to use when generating a name
    :param drop: Whether to drop data that was filtered out
    :returns: The path to the subset data
    """
    if mask_coordinate is None:
        mask_coordinate = coordinate

    if identifiers is None:
        identifiers = {}

    if output_filename is None:
        if output_pattern and identifiers:
            output_filename = output_pattern.format(**identifiers)
        else:
            output_filename = f"{identifiers.get('input_name', input_file.stem)}.{identifiers.get('mask_name', mask.stem)}.nc"

    output_path: pathlib.Path = work_directory / output_filename

    import xarray
    import numpy

    from post_processing.utilities import netcdf

    this_is_verbose: bool = settings.verbosity > 0
    this_is_very_verbose: bool = settings.verbosity > 1

    if this_is_very_verbose:
        LOGGER.debug(
            f"Formatting the path for the masked version of '{input_file}'{os.linesep}"
            f"Available Identifiers:{os.linesep}"
            f"    - {(os.linesep + '    - ').join([str(key) + ': ' + str(value) for key, value in identifiers.items()])}"
            f"{os.linesep}"
        )

    if this_is_verbose:
        LOGGER.debug(f"Loading the '{input_file}' and '{mask}'")

    import tempfile
    import shutil

    with tempfile.TemporaryDirectory(dir=settings.intermediate_directory) as temporary_directory:
        temporary_path: pathlib.Path = pathlib.Path(temporary_directory)
        temporary_output_path: pathlib.Path = temporary_path / output_path.name

        with netcdf.load_netcdf(path=input_file) as input_data:
            if coordinate not in input_data.variables and coordinate in input_data.sizes:
                raise ValueError(
                    f"Cannot subset values based off of '{coordinate}' in '{input_file}' - "
                    f"it is a dimension, not a variable, and may only provide indices, not values"
                )
            if coordinate not in input_data.variables:
                raise KeyError(f"{coordinate} is not a valid variable to subset on within '{input_file}'")
            if this_is_verbose:
                LOGGER.debug(f"Loaded '{input_file}' to be masked by '{mask}'")

            mask_data: xarray.DataArray = netcdf.load_variable(path=mask, variable_name=mask_coordinate)

            if this_is_verbose:
                LOGGER.debug(f"Loaded the mask at '{mask}'")

            mask_ids: numpy.ndarray = numpy.unique(mask_data.values)

            if this_is_verbose:
                LOGGER.debug(f"Loading '{coordinate}' values from the input ({input_file})")

            input_ids: numpy.typing.NDArray[numpy.integer] = input_data[coordinate].values

            if this_is_verbose:
                LOGGER.debug("Finding missing ids")

            # NOTE: This will only work on 1D arrays - not grids
            missing_mask_ids: numpy.typing.NDArray[numpy.integer] = numpy.setdiff1d(
                mask_ids,
                input_ids,
                assume_unique=True
            )
            """A collection of ids that are in the mask but not the input"""

            # Log a warning and reduce the size of the mask if the mask has ids not in the input
            if len(missing_mask_ids) > 0:
                if missing_mask_ids.size > 20:
                    missing_mask_ids = missing_mask_ids[:20]
                    continue_text = f"{os.linesep}[...]{os.linesep}"
                else:
                    continue_text = ""

                missing_id_line_joiner: str = f"{os.linesep}[{coordinate} missing from '{input_file.name}']    "
                LOGGER.warning(
                    f"There are {len(missing_mask_ids)} missing '{coordinate}' values within {input_file}::{coordinate} "
                    f"from the mask at {mask}. An evaluation of the mask might be required as requested data will not "
                    f"be in the output. Missing IDs:"
                    f"{missing_id_line_joiner}{missing_id_line_joiner.join(map(str, missing_mask_ids))}{continue_text}{os.linesep}"
                    f"Samples:{os.linesep}"
                    f"{mask.name}: {mask_data[mask_coordinate].values[:5]}{os.linesep}"
                    f"{input_file.name}: {input_ids[:5]}{os.linesep}"
                    f"Are you using the right variables and/or dimensions?{os.linesep}"
                    f"Mask Variables: {mask_data.sizes}, {list(mask_data.variables.keys())}{os.linesep}"
                    f"Input Variables: {input_data.sizes}, {list(input_data.variables.keys())}{os.linesep}"
                )
                mask_ids = mask_ids[numpy.isin(mask_ids, input_ids)]
            elif this_is_very_verbose:
                LOGGER.debug("All mask IDs are available")

            try:
                if this_is_verbose:
                    LOGGER.debug(f"Extracting data from '{input_file}' that matches the allowable ids")

                if drop and coordinate in input_data.indexes:
                    subset_data: xarray.Dataset = input_data.sel(**{coordinate: mask_ids})
                elif drop and coordinate in input_data.coords and len(input_data.coords[coordinate].dims) == 1:
                    # If the coordinate is for a single dimension, we can match it up with its dimension to find the
                    # actual usable indices
                    target_dimension: str = str(input_data.coords[coordinate].dims[0])

                    # Isolate the values from within the coordinate values that are within the allowable ids
                    matching_values_mask: numpy.ndarray[numpy.bool_] = input_data.coords[coordinate].isin(mask_ids).values

                    # Identify the indices that are nonzero (i.e. all the `True` values)
                    indices_that_fit_per_dimension: tuple[numpy.ndarray, ...] = numpy.nonzero(matching_values_mask)

                    # nonzero breaks up data per dimension. Get only the first, since we're one-dimensional
                    fitting_indices_for_target_dimension: numpy.ndarray[numpy.integer] = indices_that_fit_per_dimension[0]

                    # Now select the data by index by the dimension that this coordinate represents
                    subset_data: xarray.Dataset = input_data.isel(**{target_dimension: fitting_indices_for_target_dimension})
                else:
                    LOGGER.warning(
                        f"'{coordinate}' is not an index in '{input_file.name}' - this might result in a slowdown."
                    )
                    subset_data: xarray.Dataset = input_data.where(
                        input_data[coordinate].compute().isin(mask_ids),
                        drop=drop
                    )
            except Exception as e:
                if "not all values found in index" in str(e):
                    input_ids: numpy.typing.NDArray[numpy.integer] = input_data[coordinate].values
                    missing_input_ids: numpy.typing.NDArray[numpy.integer] = mask_ids[~numpy.isin(mask_ids, input_ids)]
                    if len(missing_input_ids) > 20:
                        missing_input_ids = missing_input_ids[:20]
                        continue_text = f"{os.linesep}[...]{os.linesep}"
                    else:
                        continue_text = ""

                    LOGGER.error(
                        f"Cannot subset the input data - missing the following IDs:{os.linesep}"
                        f"    - {(os.linesep + '    - ').join(list(map(str, missing_input_ids)))}{continue_text}{os.linesep}"
                        f"Samples:{os.linesep}"
                        f"{mask.name}: {mask_data[mask_coordinate].values[:5]}{os.linesep}"
                        f"{input_file.name}: {input_ids[:5]}{os.linesep}"
                        f"Are you using the right variables and/or dimensions?{os.linesep}"
                        f"Mask Variables: {mask_data.sizes}, {list(mask_data.variables.keys())}{os.linesep}"
                        f"Input Variables: {input_data.sizes}, {list(input_data.variables.keys())}{os.linesep}"
                    )
                raise

            if len(subset_data[coordinate].values) == 0:
                raise Exception(
                    f"The mask at '{mask}' is invalid for the data at '{input_file}' - "
                    f"none of the IDs within '{mask.name}::{mask_coordinate}' are available within {input_file.name}::{coordinate}. {os.linesep}"
                    f"Samples:{os.linesep}"
                    f"{mask.name}: {mask_data[mask_coordinate].values[:5]}{os.linesep}"
                    f"{input_file.name}: {input_ids[:5]}{os.linesep}"
                    f"Are you using the right variables and/or dimensions?{os.linesep}"
                    f"Mask Variables: {mask_data.sizes}, {list(mask_data.variables.keys())}{os.linesep}"
                    f"Input Variables: {input_data.sizes}, {list(input_data.variables.keys())}{os.linesep}"
                )

            if this_is_verbose:
                LOGGER.debug(f"Saving extracted data that matches '{mask}' to '{output_path}'")

            if 'stage' in (identifiers or {}):
                subset_data.attrs['process_step'] = identifiers.get('stage')

            successfully_saved: bool = netcdf.save_netcdf(path=temporary_output_path, dataset=subset_data)

            if not successfully_saved:
                raise Exception(
                    f"Something kept masked data from being saved to '{temporary_output_path}' without a suitable error"
                )
        shutil.move(temporary_output_path, output_path)

        if this_is_verbose:
            LOGGER.debug(f"Masked data saved to '{output_path}'")

    return output_path

@timed_function(logger=LOGGER)
def subset_gridded_file_into_file_by_mask(
    input_file: pathlib.Path,
    mask_path: pathlib.Path,
    work_directory: pathlib.Path,
    mask_variables: generic.Sequence[str],
    identifiers: generic.Mapping[str, typing.Any] = None,
    output_pattern: str = None,
    drop: bool = False
) -> generic.Sequence[pathlib.Path]:
    """
    Subset a multidimensional netcdf file on disk by a multidimensional netcdf variable

    :param input_file: The path to the file to subset
    :param mask_path: the path to the file containing what values to include in the coordinate
    :param work_directory: The directory where data may be written
    :param mask_variables: The names of coordinate variables in the mask containing seperate sets of coordinates to keep
    :param output_filename: What to name the extracted data. The name will be generated as a mix between the mask and input file if not provided
    :param identifiers: Dictionary of identifiers to use when generating a name
    :param output_pattern: The format string to use when generating a name
    :param drop: Whether to drop the data that was filtered out
    :returns: The path to the subset data
    """
    if input_file.is_dir():
        raise FileNotFoundError(
            f"'{input_file.resolve()}' is a directory, not a file. It may not be used as data to be masked"
        )
    if not input_file.exists():
        raise FileNotFoundError(f"The input file does not exist at '{input_file.resolve()}'")

    if mask_path.is_dir():
        raise FileNotFoundError(f"'{mask_path.resolve()}' is a directory, not a file. It may not be used as a mask")

    if not mask_path.exists():
        raise FileNotFoundError(
            f"There is not a file at '{mask_path.resolve()}'. It may not be used as a mask"
        )

    if identifiers is None:
        identifiers = {
            "input_file": input_file.name,
            "input_name": input_file.stem,
        }

    output_paths: list[pathlib.Path] = []
    temporary_output_paths: dict[str, pathlib.Path] = {}

    import tempfile
    import xarray

    from post_processing.utilities import netcdf

    this_is_verbose: bool = settings.verbosity > 0

    with tempfile.TemporaryDirectory(dir=settings.intermediate_directory) as temp_directory:
        temporary_path: pathlib.Path = pathlib.Path(temp_directory)

        with netcdf.load_netcdf(path=input_file) as input_data:
            if this_is_verbose:
                LOGGER.debug(
                    f"{identifiers['stage'] + ': ' if 'stage' in identifiers else ''}"
                    f"Loaded '{input_file}' to mask by the data within '{mask_path}'"
                )

            with netcdf.load_netcdf(path=mask_path) as mask_data:
                for mask_variable in mask_variables:
                    if len(mask_variables) > 1 and this_is_verbose:
                        LOGGER.debug(
                            f"{identifiers['stage'] + ': ' if 'stage' in identifiers else ''}"
                            f"Masking '{input_file}' based on data within '{mask_path}::{mask_variable}'"
                        )

                    if output_pattern and identifiers:
                        output_filename = output_pattern.format(mask_variable=mask_variable, **identifiers)
                    elif len(mask_variables) == 1:
                        output_filename = (
                            f"{identifiers['stage'] + '.' if 'stage' in identifiers else ''}"
                            f"{identifiers.get('input_name', input_file.stem)}."
                            f"{identifiers.get('mask_name', mask_path.stem)}.nc"
                        )
                    else:
                        output_filename = (
                            f"{identifiers['stage'] + '.' if 'stage' in identifiers else ''}"
                            f"{identifiers.get('input_name', input_file.stem)}."
                            f"{mask_variable.lower()}.nc"
                        )

                    temporary_output_path: pathlib.Path = temporary_path / output_filename
                    if this_is_verbose:
                        LOGGER.debug(
                            f"{identifiers['stage'] + ': ' if 'stage' in identifiers else ''}Loaded data from '{mask_path}' "
                            f"to filter out data from '{input_file}"
                        )
                    if mask_variable not in mask_data.data_vars:
                        raise KeyError(
                            f"'{mask_variable}' is not a valid data variable in '{input_file}'"
                        )

                    mask: xarray.DataArray = mask_data[mask_variable]

                    missing_dimensions: typing.Sequence[str] = [
                        str(dimension)
                        for dimension in mask.sizes.keys()
                        if dimension not in input_data.sizes.keys()
                    ]

                    if missing_dimensions:
                        raise KeyError(
                            f"Cannot mask the data within '{input_file}' by '{mask_path.name}::{mask_variable}' - "
                            f"'{input_file.name}' is missing the following required dimensions: "
                            f"{', '.join(missing_dimensions)}"
                        )

                    subset_data: xarray.Dataset = input_data.where(mask, drop=drop)

                    if 'stage' in (identifiers or {}):
                        subset_data.attrs['process_step'] = identifiers.get('stage')
                    successfully_saved: bool = netcdf.save_netcdf(path=temporary_output_path, dataset=subset_data)

                    if not successfully_saved:
                        raise Exception(
                            f"Something occurred and the data from '{input_file}' masked by '{mask_path}' could not be "
                            f"saved to the temporary path at '{temporary_output_path}'"
                        )
                    # TODO: This may be a great place to put the 'each' operations. Somthing like adding information
                    #  about this saved file to a queue that is read by a separate thread
                    temporary_output_paths[output_filename] = temporary_output_path

        for output_name, temporary_output_path in temporary_output_paths.items():
            output_path: pathlib.Path = work_directory / output_name
            try:
                if this_is_verbose:
                    LOGGER.debug(
                        f"{identifiers['stage'] + ': ' if 'stage' in identifiers else ''}"
                        f"A reduced version of '{input_file}' was temporarily saved to '{temporary_output_path}' "
                        f"and will now be saved to '{output_path}'"
                    )
                shutil.move(temporary_output_path, output_path)
            except Exception as e:
                LOGGER.error(
                    f"Could not move the temporary output data from '{temporary_output_path}' to '{output_path}' due to: {e}"
                )
                raise e
            output_paths.append(output_path)

    return output_paths

@timed_function(logger=LOGGER)
def subset_file_into_file_by_mask(
    input_file: pathlib.Path,
    mask: pathlib.Path,
    coordinate: str | generic.Sequence[str],
    work_directory: pathlib.Path,
    mask_coordinate: str | generic.Sequence[str] = None,
    output_filename: str = None,
    identifiers: generic.Mapping[str, typing.Any] = None,
    output_pattern: str = None,
) -> pathlib.Path:
    """
    Subset a netcdf file on disk by a mask that is also on disk

    :param input_file: The path to the file to subset
    :param mask: the path to the file containing what values to include in the coordinate
    :param coordinate: The name of the coordinate variable within the input file that will be masked
    :param work_directory: The directory where data may be written
    :param mask_coordinate: The name of the coordinate variable in the mask containing the coordinates to keep
    :param output_filename: What to name the extracted data. The name will be generated as a mix between the mask and input file if not provided
    :param identifiers: Dictionary of identifiers to use when generating a name
    :param output_pattern: The format string to use when generating a name
    :returns: The path to the subset data
    """
    if coordinate and not mask_coordinate:
        mask_coordinate = coordinate

    source_is_spatial: bool = isinstance(coordinate, generic.Sequence) and not isinstance(coordinate, str)
    mask_is_spatial: bool = (
        (mask_coordinate is None and source_is_spatial)
            or (isinstance(mask_coordinate, generic.Sequence) and not isinstance(mask_coordinate, str))
    )

    if source_is_spatial and len(coordinate) == 1:
        coordinate = coordinate[0]
        source_is_spatial = False

    if mask_is_spatial and len(mask_coordinate) == 1:
        mask_coordinate = mask_coordinate[0]
        mask_is_spatial = False

    if not source_is_spatial and not mask_is_spatial:
        return subset_vector_file_into_file_by_value(
            input_file=input_file,
            mask=mask,
            coordinate=coordinate,
            work_directory=work_directory,
            mask_coordinate=mask_coordinate,
            output_filename=output_filename,
            identifiers=identifiers,
            output_pattern=output_pattern,
        )
    elif source_is_spatial and mask_is_spatial:
        return subset_gridded_file_into_file_by_mask(
            input_file=input_file,
            mask_path=mask,
            work_directory=work_directory,
            mask_variable=mask_coordinate,
            output_filename=output_filename,
            identifiers=identifiers,
            output_pattern=output_pattern,
        )

    raise TypeError(
        f"'{coordinate}' for the input data and '{mask_coordinate}' for the mask do not match - both need to of the "
        f"same type - either a single string or a series of strings."
    )

"""
Contains logic for subsetting netcdf files
"""
import os
import typing
import pathlib
import logging

LOGGER: logging.Logger = logging.getLogger(pathlib.Path(__file__).name)

def subset_file_into_file_by_mask(
    input_file: pathlib.Path,
    mask: pathlib.Path,
    coordinate: str,
    work_directory: pathlib.Path,
    mask_coordinate: str = None,
    output_filename: str = None,
    identifiers: typing.Mapping[str, typing.Any] = None,
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
    from post_processing.configuration import settings

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

            mask_data: xarray.Dataset = netcdf.load_netcdf(path=mask)

            if mask_coordinate not in mask_data.variables and mask_coordinate in mask_data.sizes:
                raise ValueError(
                    f"Cannot subset values based off of '{mask_coordinate}' in the mask at '{mask}' - "
                    f"it is a dimension, not a variable, and may only provide indices, not values"
                )
            if mask_coordinate not in mask_data.variables:
                raise KeyError(
                    f"'{mask_coordinate}' is not a valid variable to subset on within the mask at '{input_file}'"
                )

            if this_is_verbose:
                LOGGER.debug(f"Loaded the mask at '{mask}'")

            if this_is_verbose:
                LOGGER.debug(f"Loading '{mask_coordinate}' values from the mask ({mask})")

            allowable_ids: numpy.ndarray = mask_data[mask_coordinate].values

            if this_is_verbose:
                LOGGER.debug(f"Loading '{coordinate}' values from the input ({input_file})")

            available_ids: numpy.ndarray = input_data[coordinate].values

            if this_is_verbose:
                LOGGER.debug("Finding missing ids")

            # NOTE: This will only work on 1D arrays - not grids
            missing_ids: typing.Union[numpy.ndarray, typing.Set] = numpy.setdiff1d(allowable_ids, available_ids)

            if len(missing_ids) > 0:
                if missing_ids.size > 20:
                    missing_ids = missing_ids[:20]
                    continue_text = f"{os.linesep}[...]{os.linesep}"
                else:
                    continue_text = ""
                missing_id_line_joiner: str = f"{os.linesep}[{coordinate} missing from '{input_file.name}']    "
                LOGGER.warning(
                    f"There are {len(missing_ids)} missing '{coordinate}' values within {input_file}({coordinate}) "
                    f"from the mask at {mask}. An evaluation of the mask might be required as requested data will not "
                    f"be in the output. Missing IDs:"
                    f"{missing_id_line_joiner}{missing_id_line_joiner.join(map(str, missing_ids))}{continue_text}{os.linesep}"
                    f"Samples:{os.linesep}"
                    f"{mask.name}: {mask_data[mask_coordinate].values[:5]}{os.linesep}"
                    f"{input_file.name}: {available_ids[:5]}{os.linesep}"
                    f"Are you using the right variables and/or dimensions?{os.linesep}"
                    f"Mask Variables: {mask_data.sizes}, {list(mask_data.variables.keys())}{os.linesep}"
                    f"Input Variables: {input_data.sizes}, {list(input_data.variables.keys())}{os.linesep}"
                )
                allowable_ids = allowable_ids[numpy.isin(allowable_ids, input_data[coordinate].values)]
            elif this_is_very_verbose:
                LOGGER.debug("All mask IDs are available")

            try:
                if this_is_verbose:
                    LOGGER.debug(f"Extracting data from '{input_file}' that matches the allowable ids")
                if coordinate in input_data.indexes:
                    subset_data: xarray.Dataset = input_data.sel(**{coordinate: allowable_ids})
                else:
                    LOGGER.warning(
                        f"'{coordinate}' is not an index in '{input_file.name}' - this might result in a slowdown."
                    )
                    subset_data: xarray.Dataset = input_data.where(input_data[coordinate].compute().isin(allowable_ids), drop=True)
            except Exception as e:
                if "not all values found in index" in str(e):
                    expected_ids: typing.Set = set(allowable_ids)
                    available_ids: typing.Set = set(input_data[coordinate].values)
                    missing_ids: typing.Set = expected_ids - available_ids
                    if len(missing_ids) > 20:
                        missing_ids = set(list(missing_ids)[:20])
                        continue_text = f"{os.linesep}[...]{os.linesep}"
                    else:
                        continue_text = ""

                    LOGGER.error(
                        f"Cannot subset the input data - missing the following IDs:{os.linesep}"
                        f"    - {(os.linesep + '    - ').join(list(map(str, missing_ids)))}{continue_text}{os.linesep}"
                        f"Samples:{os.linesep}"
                        f"{mask.name}: {mask_data[mask_coordinate].values[:5]}{os.linesep}"
                        f"{input_file.name}: {available_ids[:5]}{os.linesep}"
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
                    f"{input_file.name}: {available_ids[:5]}{os.linesep}"
                    f"Are you using the right variables and/or dimensions?{os.linesep}"
                    f"Mask Variables: {mask_data.sizes}, {list(mask_data.variables.keys())}{os.linesep}"
                    f"Input Variables: {input_data.sizes}, {list(input_data.variables.keys())}{os.linesep}"
                )

            if this_is_verbose:
                LOGGER.debug(f"Saving extracted data that matches '{mask}' to '{output_path}'")

            successfully_saved: bool = netcdf.save_netcdf(path=temporary_output_path, dataset=subset_data)

            if not successfully_saved:
                raise Exception(
                    f"Something kept masked data from being saved to '{temporary_output_path}' without a suitable error"
                )
        shutil.move(temporary_output_path, output_path)

        if this_is_verbose:
            LOGGER.debug(f"Masked data saved to '{output_path}'")

    return output_path

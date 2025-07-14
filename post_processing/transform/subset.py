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
    output_filename: str = None
) -> pathlib.Path:
    """
    Subset a netcdf file on disk by a mask that is also on disk

    :param input_file: The path to the file to subset
    :param mask: the path to the file containing what values to include in the coordinate
    :param coordinate: The name of the coordinate variable containing the values to keep
    :param work_directory: The directory where data may be written
    :param output_filename: What to name the extracted data. The name will be generated as a mix between the mask and input file if not provided
    :returns: The path to the subset data
    """
    if output_filename is None:
        output_filename = f"{mask.stem}.{input_file.stem}.nc"

    output_path: pathlib.Path = work_directory / output_filename

    import xarray
    import numpy

    input_data: xarray.Dataset = xarray.open_dataset(input_file, chunks={})
    mask_data: xarray.Dataset = xarray.open_dataset(mask, chunks={})

    LOGGER.info(f"Loading '{coordinate}' values from the mask ({mask})")
    allowable_ids: numpy.ndarray = mask_data[coordinate].values

    LOGGER.info(f"Loading '{coordinate}' values from the input ({input_file})")
    available_ids: numpy.ndarray = input_data[coordinate].values

    missing_ids: numpy.ndarray = numpy.setdiff1d(allowable_ids, available_ids)

    if len(missing_ids) > 0:
        missing_id_line_joiner: str = f"{os.linesep}[{coordinate} missing from '{input_file.name}']    "
        LOGGER.warning(
            f"There are {len(missing_ids)} missing '{coordinate}' values within {input_file}({coordinate}) "
            f"from the mask at {mask}. An evaluation of the mask might be required as requested data will not "
            f"be in the output. Missing IDs:"
            f"{missing_id_line_joiner}{missing_id_line_joiner.join(map(str, missing_ids))}"
        )
        allowable_ids = allowable_ids[numpy.isin(allowable_ids, input_data[coordinate].values)]
    else:
        LOGGER.info("All mask IDs are available")

    try:
        subset_data: xarray.Dataset = input_data.sel(**{coordinate: allowable_ids})
    except Exception as e:
        if "not all values found in index" in str(e):
            expected_ids: typing.Set = set(allowable_ids)
            available_ids: typing.Set = set(input_data[coordinate].values)
            missing_ids: typing.Set = expected_ids - available_ids
            LOGGER.error(
                f"Cannot subset the input data - missing the following IDs:{os.linesep}"
                f"    - {(os.linesep + '    - ').join(list(map(str, missing_ids)))}"
            )
        raise
    
    subset_data.to_netcdf(output_path)

    return output_path

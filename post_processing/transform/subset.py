"""
Contains logic for subsetting netcdf files
"""
import pathlib

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

    allowable_ids: numpy.ndarray = mask_data[coordinate].values
    subset_data: xarray.Dataset = input_data.sel(**{coordinate: allowable_ids})
    subset_data.to_netcdf(output_path)

    return output_path

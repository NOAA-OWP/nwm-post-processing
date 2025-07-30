"""
Contains logic for merging netcdf files
"""
import logging
import typing
import pathlib

import xarray

LOGGER: logging.Logger = logging.getLogger(pathlib.Path(__file__).stem)

def merge_files_into_file(
    files: typing.Sequence[typing.Union[str, pathlib.Path]],
    output_file: typing.Union[str, pathlib.Path]
) -> None:
    from post_processing.utilities.netcdf import save_netcdf
    with merge_files(files=files) as merged_files:
        save_netcdf(path=output_file, dataset=merged_files)

def merge_files(files: typing.Sequence[typing.Union[str, pathlib.Path]]) -> xarray.Dataset:
    from post_processing.utilities.netcdf import load_netcdf
    files = [
        file if isinstance(file, pathlib.Path) else pathlib.Path(file)
        for file in files
    ]
    combined_files: xarray.Dataset = load_netcdf(files)
    dimension_groups: typing.Set[typing.Tuple[str, ...]] = {
        tuple(map(str, variable.dims))
        for variable in combined_files.data_vars.values()
        if variable.dims
    }
    new_order: typing.List[str] = [*combined_files.encoding.get("unlimited_dims", [])]
    for dimension_group in dimension_groups:
        for dimension in dimension_group:
            if dimension not in new_order:
                new_order.insert(0, str(dimension))

    for dimension in combined_files.sizes.keys():
        if dimension not in new_order:
            new_order.append(str(dimension))
    combined_files = combined_files.transpose(*new_order)
    return combined_files


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

    for variable_name, variable in combined_files.data_vars.items():
        if len(variable.dims) != 1:
            continue

        first_value: typing.Any = variable.isel({dimension: 0 for dimension in variable.dims})

        if hasattr(first_value, 'compute') and callable(getattr(first_value, 'compute')):
            first_value = first_value.compute()

        first_value = first_value.item()

        from post_processing.utilities.common import is_nan_safe
        if is_nan_safe(first_value):
            values_match_first: xarray.DataArray = variable.isnull().all()
        else:
            values_match_first: xarray.DataArray = (variable == first_value).all()

        if hasattr(values_match_first, 'compute') and callable(getattr(values_match_first, 'compute')):
            values_match_first = values_match_first.compute()

        data_is_uniform: bool = values_match_first.item()

        if data_is_uniform:
            simplified_variable: xarray.DataArray = xarray.DataArray(
                data=first_value,
                attrs=variable.attrs,
                name=variable.name
            )
            combined_files[variable_name] = simplified_variable

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


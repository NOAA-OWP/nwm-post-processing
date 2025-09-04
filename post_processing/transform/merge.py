"""
Contains logic for merging netcdf files
"""
import logging
import typing
import pathlib

import collections.abc as generic

import numpy
import xarray

from post_processing.utilities.common import timed_function

LOGGER: logging.Logger = logging.getLogger(pathlib.Path(__file__).stem)


@timed_function()
def merge_files_into_file(
    files: typing.Sequence[typing.Union[str, pathlib.Path]],
    output_file: typing.Union[str, pathlib.Path]
) -> None:
    from post_processing.utilities.netcdf import save_netcdf
    with merge_files(files=files) as merged_files:
        save_netcdf(path=output_file, dataset=merged_files)

@timed_function()
def merge_files(files: typing.Sequence[str | pathlib.Path]) -> xarray.Dataset:
    from post_processing.utilities.netcdf import load_netcdf
    files: generic.Sequence[pathlib.Path] = [
        file if isinstance(file, pathlib.Path) else pathlib.Path(file)
        for file in files
    ]

    LOGGER.debug(f"Merging {len(files)} files")
    combined_files: xarray.Dataset = load_netcdf(path=files)
    LOGGER.debug(f"Data from {len(files)} have been merged. Now they are being encoded.")
    for coordinate_name, variable in combined_files.coords.items():
        new_dtype = variable.dtype

        if variable.dtype in (numpy.int8, numpy.int16, numpy.int32, numpy.int64, numpy.uint16, numpy.uint32, numpy.uint64):
            minimum = variable.min(skipna=True).item()
            maximum = variable.max(skipna=True).item()

            if isinstance(minimum, numpy.integer) and isinstance(maximum, numpy.integer):
                unsigned: bool = minimum >= 0

                if unsigned:
                    if maximum < (2 ** 8):
                        new_dtype = numpy.uint8
                    elif maximum < (2 ** 16):
                        new_dtype = numpy.uint16
                    elif maximum <= (2 ** 32):
                        new_dtype = numpy.uint32
                    else:
                        new_dtype = numpy.uint64
                else:
                    if -1 * ((2 ** 8) / 2) <= minimum and maximum < (2 ** 8) / 2:
                        new_dtype = numpy.int8
                    elif -1 * ((2 ** 16) / 2) <= minimum and maximum < (2 ** 16) / 2:
                        new_dtype = numpy.int16
                    elif -1 * ((2 ** 32) / 2) <= minimum and maximum < (2 ** 32) / 2:
                        new_dtype = numpy.int32
                    else:
                        new_dtype = numpy.int64
        else:
            continue

        if variable.dtype != new_dtype:
            variable = variable.astype(new_dtype)
            combined_files = combined_files.assign_coords({coordinate_name: variable})

    for variable_name, variable in combined_files.data_vars.items():
        new_dtype = variable.dtype
        if variable.dtype == numpy.float64:
            new_dtype = numpy.float32
        elif variable.dtype in (numpy.int8, numpy.int16, numpy.int32, numpy.int64, numpy.uint16, numpy.uint32, numpy.uint64):
            minimum = variable.min(skipna=True).item()
            maximum = variable.max(skipna=True).item()

            if isinstance(minimum, numpy.integer) and isinstance(maximum, numpy.integer):
                unsigned: bool = minimum >= 0

                if unsigned:
                    if maximum < (2 ** 8):
                        new_dtype = numpy.uint8
                    elif maximum < (2 ** 16):
                        new_dtype = numpy.uint16
                    elif maximum <= (2 ** 32):
                        new_dtype = numpy.uint32
                    else:
                        new_dtype = numpy.uint64
                else:
                    if -1 * ((2 ** 8) / 2) <= minimum and maximum < (2 ** 8) / 2:
                        new_dtype = numpy.int8
                    elif -1 * ((2 ** 16) / 2) <= minimum and maximum < (2 ** 16) / 2:
                        new_dtype = numpy.int16
                    elif -1 * ((2 ** 32) / 2) <= minimum and maximum < (2 ** 32) / 2:
                        new_dtype = numpy.int32
                    else:
                        new_dtype = numpy.int64

        if variable.dtype != new_dtype:
            previous_encoding = variable.encoding.copy()
            variable = variable.astype(new_dtype)
            variable.encoding.update(previous_encoding)

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

        if variable.dtype == numpy.float64:
            previous_encoding: dict[str, typing.Any] = variable.encoding.copy()
            combined_files[variable_name] = variable.astype(numpy.float32)
            combined_files[variable_name].encoding.update(previous_encoding)

    dimension_groups: set[tuple[str, ...]] = {
        tuple(map(str, variable.dims))
        for variable in combined_files.data_vars.values()
        if variable.dims
    }

    new_order: list[str] = [*combined_files.encoding.get("unlimited_dims", [])]
    for dimension_group in dimension_groups:
        for dimension in dimension_group:
            if dimension not in new_order:
                new_order.insert(0, str(dimension))

    for dimension in combined_files.sizes.keys():
        if dimension not in new_order:
            new_order.append(str(dimension))

    combined_files = combined_files.transpose(*new_order)
    combined_files = combined_files.compute()
    LOGGER.debug(f"Data from {len(files)} files have been merged and encoded")
    return combined_files


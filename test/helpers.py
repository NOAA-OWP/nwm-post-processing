"""
Helper functions and variables used to standardize testing behavior
"""
import typing
import pathlib

def create_random_mask(
    data_path: pathlib.Path,
    coordinate_variable: str,
    output_path: pathlib.Path,
    size: int = 100,
    seed: int = 123456789
) -> typing.Sequence:
    """
    Create a random mask file that may be used for masking operations in NCO

    :param data_path: The path to a netcdf file to base the mask off of
    :param coordinate_variable: The name of the variable to mask on
    :param output_path: Where to put the result
    :param size: The number of values to include in the mask
    :param seed: The random seed value to use for consistent results
    :returns: The values included in the mask's coordinate variable
    """
    import numpy
    import xarray

    numpy.random.seed(seed=seed)

    source_data: xarray.Dataset = xarray.open_dataset(data_path)

    if coordinate_variable not in source_data.coords:
        raise ValueError(
            f"There is no coordinate in {data_path} named {coordinate_variable}"
        )

    raw_values: numpy.ndarray = source_data[coordinate_variable].values

    if size > raw_values.size:
        raise ValueError(
            f"Cannot make a mask with {size} values - there are only {raw_values.size} values in the source"
        )

    selected_values: numpy.ndarray = numpy.random.choice(raw_values, size=size, replace=False)
    
    subset: xarray.Dataset = source_data.sel(**{coordinate_variable: selected_values})

    subset = subset.drop_vars(names=[name for name in subset.variables.mapping.keys() if name != coordinate_variable])
    subset[coordinate_variable].attrs.clear()
    subset.attrs.clear()

    subset.to_netcdf(path=output_path)

    return sorted(selected_values.tolist())

"""
Functions and objects used to rename a variable and/or dimension in a netcdf file
"""
import typing
import pathlib
import logging

from post_processing.utilities.common import timed_function

LOGGER: logging.Logger = logging.getLogger(pathlib.Path(__file__).stem)


@timed_function()
def rename_variable(
    input_path: pathlib.Path,
    output_path: pathlib.Path,
    mapping: typing.Mapping[str, str]
) -> pathlib.Path:
    """
    Rename variables and/or coordinates within a netcdf file

    :param input_path: The path to the file to modify
    :param output_path: Where to save the output file
    :param mapping: What elements should be renamed to
    :returns: The path to the newly generated data
    """
    if not mapping:
        raise ValueError(f"No name mapping was passed when attempting to rename elements within '{input_path}'")

    from post_processing.utilities.netcdf import write
    from post_processing.utilities.netcdf import load

    if output_path.is_dir():
        output_path = output_path / input_path.name

    with load(input_path) as input_file:
        coordinates_to_assign: typing.Sequence[str] = [
            new_name
            for original_name, new_name in mapping.items()
            if original_name in input_file.coords
        ]

        try:
            input_file = input_file.rename_vars(name_dict=mapping)
        except:
            import os
            LOGGER.error(
                f"Could not rename a variable in '{input_path}'. Available Variables:{os.linesep}"
                f"    - {(os.linesep + '    - ').join([str(variable) for variable in input_file.variables])}"
            )
            raise

        input_file = input_file.set_coords(coordinates_to_assign)

        for key, value in mapping.items():
            keys_to_update: list[str] = [
                attribute_key
                for attribute_key, attribute_value in input_file.attrs.items()
                if attribute_value == key
            ]
            for key_to_update in keys_to_update:
                input_file.attrs[key_to_update] = value

        write(dataset=input_file, target=output_path)

    return output_path

@timed_function()
def rename_dimension(
    input_path: pathlib.Path,
    output_path: pathlib.Path,
    mapping: typing.Mapping[str, str]
) -> pathlib.Path:
    """
    Rename dimensions within a netcdf file

    :param input_path: The path to the file to modify
    :param output_path: Where to save the output file
    :param mapping: What elements should be renamed to
    :returns: The path to the newly generated data
    """
    if not mapping:
        raise ValueError(f"No name mapping was passed when attempting to rename elements within '{input_path}'")

    if not input_path.exists():
        raise FileNotFoundError(f"Cannot rename dimensions in '{input_path}' - it doesn't exist")

    from post_processing.utilities.netcdf import load
    from post_processing.utilities.netcdf import write

    if output_path.is_dir():
        output_path = output_path / input_path.name

    with load(input_path) as input_file:
        previous_indices: set[str] = set(input_file.indexes.keys())
        input_file = input_file.rename_dims(dims_dict=mapping)
        missing_indexes: set[str] = set(input_file.indexes.keys()).difference(previous_indices)
        if missing_indexes:
            input_file = input_file.set_xindex(list(missing_indexes))
        write(dataset=input_file, target=output_path)

    return output_path

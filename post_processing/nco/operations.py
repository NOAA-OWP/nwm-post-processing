#!/usr/bin/env python3
"""
Core wrappers around crucial netcdf operations
"""
import typing
import logging
import pathlib
import collections.abc as generic

from .operation_helpers import run_command
from .operation_helpers import NCOFunction
from .operation_helpers import EditMode

from .structure import NetcdfType

LOGGER: logging.Logger = logging.getLogger(pathlib.Path(__file__).stem)


def keep_only_variables(
    input_file: typing.Union[str, pathlib.Path],
    output_file: typing.Union[str, pathlib.Path],
    variables: generic.Sequence[str]
) -> pathlib.Path:
    """
    Remove all variables from the input file that aren't in the variables list and move all the resulting data
    into the output file

    :param input_file: The netcdf file to pull data from
    :param output_file: The output file to write to
    :param variables: The list of variables to keep
    :returns: The path to the new or updated file
    """
    run_command(
        NCOFunction.KITCHEN_SINK,
        "-O",
        "-C",
        "-v",
        ",".join(variables),
        input_file,
        output_file
    )

    return output_file


def remove_variables(
    input_file: typing.Union[str, pathlib.Path],
    output_file: typing.Union[str, pathlib.Path],
    variables: list[str]
) -> pathlib.Path:
    """
    Remove variables by name

    :param input_file: The netcdf file to pull data from
    :param output_file: The output file to write to
    :param variables: The list of variables to remove
    :returns: The path to the new or updated file
    """
    run_command(
        NCOFunction.KITCHEN_SINK,
        "-O",
        "-C",
        "-x",
        "-v",
        ",".join(variables),
        input_file,
        output_file
    )

    return output_file


def transform_variable(
    input_file: typing.Union[str, pathlib.Path],
    output_file: typing.Union[str, pathlib.Path],
    expr: str
) -> pathlib.Path:
    """
    Perform some sort of arithmetic within a netcdf file

    :param input_file: The netcdf file to pull data from
    :param output_file: The output file to write to
    :param expr: The arithmetic expression to perform
    :returns: The path to the new or updated file
    """
    if "'" not in expr and not expr.startswith('"') and not expr.endswith('"'):
        expr = f"'{expr}'"
    elif '"' not in expr and not expr.startswith("'") and not expr.endswith("'"):
        expr = f'"{expr}"'

    run_command(
        NCOFunction.PERFORM_ARITHMETIC,
        "-O",
        "-s",
        expr,
        input_file,
        output_file
    )

    return output_file


def add_or_modify_attribute(
    input_file: typing.Union[str, pathlib.Path],
    attribute_name: str,
    attribute_value: str,
    variable_name: str = "global",
    attribute_type: NetcdfType = NetcdfType.CHAR,
    mode: EditMode = EditMode.OVERWRITE,
    output_file: typing.Union[str, pathlib.Path] = None,
) -> pathlib.Path:
    """
    Add or update a global or variable attribute

    :param input_file: The path to the file whose attribute is to be added or modified
    :param attribute_name: The name of the attribute to add or modify
    :param attribute_value: The value of the attribute
    :param variable_name: The name of the variable that receives the attribute. 'global' sets the value in the
        global scope
    :param attribute_type: The type of value that this should be
    :param mode: How the attribute should be manipulated
    :param output_file: Where to place the changes. Changes are made in-place if not provided
    :returns: The path to the new or updated file
    """
    if output_file is None:
        output_file = input_file

    import string
    if any(character not in string.ascii_letters + string.digits for character in attribute_value):
        attribute_value = f'"{attribute_value}"'

    run_command(
        NCOFunction.EDIT_ATTRIBUTES,
        "-O",
        "-a",
        f"{attribute_name},{variable_name},{mode.value},{attribute_type.code},{attribute_value}",
        input_file,
        output_file
    )

    return output_file


def rename_variable(
    input_file: typing.Union[str, pathlib.Path],
    old_name: str,
    new_name: str,
    output_file: typing.Union[str, pathlib.Path] = None
) -> pathlib.Path:
    """
    Rename a single variable

    :param input_file: The path to the netcdf file to pull data from
    :param old_name: The name of the variable to rename
    :param new_name: The new name for the variable
    :param output_file: Where to place the changes. Changes are made in-place if not provided
    :returns: The path to the new or updated file
    """
    if output_file is None:
        output_file = input_file

    run_command(
        NCOFunction.RENAME,
        "-O",
        "-v",
        f"{old_name},{new_name}",
        input_file,
        output_file
    )

    return output_file


def reorder_dimensions(
    input_file: typing.Union[str, pathlib.Path],
    output_file: typing.Union[str, pathlib.Path],
    dimension_order: generic.Sequence[str]
) -> pathlib.Path:
    """
    Change the order of dimensions within the input file

    Affects all variables with the given dimensions.

    Example:

    Say you have "Variable1(y)", "Variable2(y, x)", and "Variable3(y, x, Z)" - using ["x", "y"] will reorder all
    dimensions to look like:

    "Variable1(y)", "Variable2(x, y)", and "Variable3(x, y, z)"

    :param input_file: The path to the netcdf file that holds dimensions that should be reordered
    :param output_file: The path to where the altered data should be written
    :param dimension_order: The dimensions to order and what order they should be in
    :returns: The path to the new or updated file
    """
    run_command(
        NCOFunction.MANIPULATE_DIMENSIONS,
        "-O",
        "-a",
        ",".join(dimension_order),
        input_file,
        output_file
    )

    return output_file

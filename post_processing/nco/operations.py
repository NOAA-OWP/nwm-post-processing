"""
Core wrappers around crucial netcdf operations
"""
import typing
import logging
import pathlib
import os
import tempfile

import xarray

from post_processing.utilities.common import starmap
from post_processing.utilities.common import first

from .operation_helpers import run_command
from .operation_helpers import NCOFunction
from .operation_helpers import get_header
from .operation_helpers import EditMode

from .structure import DataVariable
from .structure import NetcdfSummary
from .structure import NetcdfType
from .structure import Attribute

LOGGER: logging.Logger = logging.getLogger(pathlib.Path(__file__).stem)


def keep_only_variables(
    input_file: typing.Union[str, pathlib.Path],
    output_file: typing.Union[str, pathlib.Path],
    variables: typing.Sequence[str]
) -> None:
    """
    Remove all variables from the input file that aren't in the variables list and move all the resulting data
    into the output file

    :param input_file: The netcdf file to pull data from
    :param output_file: The output file to write to
    :param variables: The list of variables to keep
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


def remove_variables(
    input_file: typing.Union[str, pathlib.Path],
    output_file: typing.Union[str, pathlib.Path],
    variables: typing.List[str]
) -> None:
    """
    Remove variables by name

    :param input_file: The netcdf file to pull data from
    :param output_file: The output file to write to
    :param variables: The list of variables to remove
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


def transform_variable(
    input_file: typing.Union[str, pathlib.Path],
    output_file: typing.Union[str, pathlib.Path],
    expr: str
) -> None:
    """
    Perform some sort of arithmetic within a netcdf file

    :param input_file: The netcdf file to pull data from
    :param output_file: The output file to write to
    :param expr: The arithmetic expression to perform
    """
    run_command(
        NCOFunction.PERFORM_ARITHMETIC,
        "-O",
        "-s",
        expr,
        input_file,
        output_file
    )


def copy_and_correct_merge_input(
    file: typing.Union[str, pathlib.Path],
    work_directory: pathlib.Path
) -> pathlib.Path:
    """
    Create a copy of a passed in netcdf file that obeys the rules for merging

    :param file: The netcdf file to copy
    :param work_directory: Where a file may be worked on out of memory
    :returns: The path to the new file
    """
    filename = pathlib.Path(file).name

    summary: NetcdfSummary = NetcdfSummary.load(path=file)

    # Files cannot be merged without an unlimited dimension. There must be a dimension in which the data may grow
    if not summary.unlimited_dimensions:
        header: str = get_header(target=file)
        raise ValueError(
            f"Cannot merge {file} in with other netcdf data - it lacks a record dimension{os.linesep}{header}"
        )

    adjustment_assignments: typing.Dict[typing.Tuple[str, str], str] = {}

    # Determine if adjustments need to be made
    for data_variable in summary.data_variables:  # type: DataVariable
        # Exclude variables like 'crs' which aren't records
        if len(data_variable.dimensions) == 0:
            continue

        # If an unlimited dimension is not in the dimensions of this variable, it must be added
        if not set(summary.unlimited_dimensions).intersection(data_variable.dimensions):
            total_dimensions: typing.List[str] = data_variable.dimensions

            # Add the unlimited dimension to the end of the list. This ensures that the indexing strategy for
            # the data is maintained. This will do something like convert (feature_id) to (feature_id, time).
            #   *You generally want al times for a feature_id, not all feature_ids for a time, so we add time to the
            #       end to ensure that we can continue to index our data as intended. A smarter way to perform this
            #       would be to order the values from largest to smallest, but that could yield inconsistent results.
            total_dimensions += [
                dimension
                for dimension in summary.unlimited_dimensions
                if dimension not in data_variable.dimensions
            ]
            old_variable_name: str = data_variable.name
            new_variable_name: str = f"{old_variable_name}_tmp"
            variable_type: NetcdfType = data_variable.type

            rename_arguments: typing.Tuple[str, str] = (new_variable_name, old_variable_name)
            adjustment_assignments[rename_arguments] = (
                f"{new_variable_name}[{','.join(total_dimensions)}]={old_variable_name}*1.0"
            )

    # Make the adjustments and reassign the path if necessary
    if adjustment_assignments:
        new_filename = f"with_record_{filename}"
        new_filepath: pathlib.Path = work_directory / new_filename

        LOGGER.debug(
            f"Adding the record variable(s) to {len(adjustment_assignments)} data variables for later concatenation"
        )

        run_command(
            NCOFunction.PERFORM_ARITHMETIC,
            "-O",
            "-s",
            f'"{";".join(adjustment_assignments.values())}"',
            file,
            new_filepath
        )

        for temporary_variable_name, original_variable_name in adjustment_assignments:
            from post_processing.utilities.common import first
            variable_summary: DataVariable = first(
                summary.data_variables,
                lambda variable: variable.name == original_variable_name
            )

            for attribute in variable_summary.attributes:
                run_command(NCOFunction.EDIT_ATTRIBUTES, attribute.ncatted_argument, new_filepath)

        remove_variables(
            new_filepath,
            new_filepath,
            list(map(lambda temp_and_og_name: temp_and_og_name[1], adjustment_assignments.keys()))
        )

        for temporary_variable_name, original_variable_name in adjustment_assignments:
            rename_variable(new_filepath, temporary_variable_name, original_variable_name)

        new_header = get_header(target=new_filepath)
        LOGGER.debug(f"A copy of {file} was created with the header:{os.linesep}{new_header}")
        file = new_filepath

    return file


def add_or_modify_attribute(
    input_file: typing.Union[str, pathlib.Path],
    attribute_name: str,
    attribute_value: str,
    variable_name: str = "global",
    attribute_type: NetcdfType = NetcdfType.CHAR,
    mode: EditMode = EditMode.OVERWRITE,
    output_file: typing.Union[str, pathlib.Path] = None,
) -> None:
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
    """
    if output_file is None:
        output_file = input_file
    run_command(
        NCOFunction.EDIT_ATTRIBUTES,
        "-O",
        "-a",
        f"{attribute_name},{variable_name},{mode.value},{attribute_type.value},{attribute_value}",
        input_file,
        output_file
    )


def rename_variable(
    input_file: typing.Union[str, pathlib.Path],
    old_name: str,
    new_name: str,
    output_file: typing.Union[str, pathlib.Path] = None
) -> None:
    """
    Rename a single variable

    :param input_file: The path to the netcdf file to pull data from
    :param old_name: The name of the variable to rename
    :param new_name: The new name for the variable
    :param output_file: Where to place the changes. Changes are made in-place if not provided
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


def rename_dimension(
    input_file: typing.Union[str, pathlib.Path],
    old_name: str,
    new_name: str,
    output_file: typing.Union[str, pathlib.Path] = None
) -> None:
    """
    Rename a single dimension

    :param input_file: The path to the netcdf file to pull data from
    :param old_name: The name of the dimension to rename
    :param new_name: The new name for the dimension
    :param output_file: Where to place the changes. Changes are made in-place if not provided
    """
    if output_file is None:
        output_file = input_file

    run_command(
        NCOFunction.RENAME,
        "-O",
        "-d",
        f"{old_name},{new_name}",
        input_file,
        output_file
    )


def reorder_dimensions(
    input_file: typing.Union[str, pathlib.Path],
    output_file: typing.Union[str, pathlib.Path],
    dimension_order: typing.Sequence[str]
) -> None:
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
    """
    run_command(
        NCOFunction.MANIPULATE_DIMENSIONS,
        "-O",
        "-a",
        ",".join(dimension_order),
        input_file,
        output_file
    )

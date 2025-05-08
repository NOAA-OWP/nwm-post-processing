"""
Wrapper functions that call nco programs like ncks
"""
from __future__ import annotations
import typing
import logging
import pathlib
import subprocess
import os
import enum
import tempfile
import dataclasses

LOGGER: logging.Logger = logging.getLogger(pathlib.Path(__file__).stem)

_NCO_IS_AVAILABLE: typing.Optional[bool] = None

class DataVariable(typing.TypedDict):
    name: str
    dimensions: typing.List[str]

class NCOFunction(enum.StrEnum):
    """
    The names of nco functions
    """
    KITCHEN_SINK = "ncks"
    CONCATENATE = "ncrcat"
    PERFORM_ARITHMETIC = "ncap2"
    EDIT_ATTRIBUTES = "ncatted"
    RENAME = "ncrename"
    MANIPULATE_DIMENSIONS = "ncpdq"

@dataclasses.dataclass
class NetcdfSummary:
    """
    Contains a parsed header of a netcdf file, showing details of its dimensions and data variables
    """
    unlimited_dimensions: typing.List[str]
    """All dimensions that don't have a limit and may be used as records"""
    all_dimensions: typing.List[str]
    """The names of all dimensions"""
    data_variables: typing.List[DataVariable]
    """All variables that contain data (i.e. not coordinates) paired with their dimensions"""

    @classmethod
    def load(cls, path: typing.Union[str, pathlib.Path]) -> NetcdfSummary:
        """
        Load netcdf data into a summary object
        """
        import re

        header: str = get_header(target=path)
        dimension_name_parameter: str = 'dimension_name'
        count_parameter: str = 'count'
        variable_name_parameter: str = 'variable_name'
        dimension_list_parameter: str = 'dimension_list'
        dimension_pattern: re.Pattern = re.compile(rf"\s+(?P<{dimension_name_parameter}>\w+) = (?P<{count_parameter}>\w+)\s+;")
        variable_definition_pattern: re.Pattern = re.compile(
            rf"\s+(?P<dtype>\w+) (?P<{variable_name_parameter}>\w+)\((?P<{dimension_list_parameter}>[^)]+)\) ;"
        )

        dimension_matches: typing.Sequence[typing.Mapping[str, str]] = [
            match.groupdict()
            for match in dimension_pattern.finditer(header)
        ]

        variable_matches: typing.Sequence[typing.Mapping[str, str]] = [
            match.groupdict()
            for match in variable_definition_pattern.finditer(header)
        ]

        dimension_names: typing.List[str] = [
            group[dimension_name_parameter]
            for group in dimension_matches
        ]

        unlimited_dimension_names: typing.List[str] = [
            group[dimension_name_parameter]
            for group in dimension_matches
            if group[count_parameter] == "UNLIMITED"
        ]

        data_variables: typing.List[DataVariable] = [
            {
                "name": group[variable_name_parameter],
                "dimensions": [dimension.strip() for dimension in group[dimension_list_parameter].split(",")],
            }
            for group in variable_matches
            if group[variable_name_parameter] not in dimension_names
        ]

        return cls(
            unlimited_dimensions=unlimited_dimension_names,
            all_dimensions=dimension_names,
            data_variables=data_variables,
        )

    @classmethod
    def load_summaries(cls, paths: typing.Sequence[typing.Union[str, pathlib.Path]]) -> typing.Sequence[NetcdfSummary]:
        """
        Load a series of netcdf data into summary objects
        """
        summaries: typing.List[NetcdfSummary] = list(map(NetcdfSummary.load, paths))
        return summaries


def run_command(command: str, *positional_args) -> typing.Tuple[str, str]:
    """
    A consistent function used to execute the CLI commands for running

    :param command: The command to execute
    :param positional_args: Positional arguments to add to the call
    :returns: Stdout and stderr
    """
    if not {'-h', '--hst', '--history'}.intersection(positional_args):
        positional_args = ["--history", *positional_args]

    if positional_args:
        command = f"{command} {' '.join(map(str, positional_args))}"

    command_result: subprocess.CompletedProcess = subprocess.run(
        command,
        capture_output=True,
        text=True,
        shell=True,
    )
    if command_result.returncode != 0:
        print(command_result.stderr)
        raise RuntimeError(
            f"Netcdf command failed:{os.linesep}"
            f"    {command}{os.linesep}"
            f"{command_result.stderr}{os.linesep}"
            f"{command_result.stdout}{os.linesep}"
            f""
        )
    return command_result.stdout.strip(), command_result.stderr.strip()

def keep_only_variables(input_file: typing.Union[str, pathlib.Path], output_file: typing.Union[str, pathlib.Path], variables: typing.Sequence[str]) -> None:
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


def remove_variables(input_file: typing.Union[str, pathlib.Path], output_file: typing.Union[str, pathlib.Path], variables: typing.List[str]) -> None:
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


def merge_files_by_variable(
    files: typing.Sequence[typing.Union[str, pathlib.Path]],
    output_file: typing.Union[str, pathlib.Path],
    variables: typing.Optional[typing.Union[typing.Sequence[str], str]] = None
) -> None:
    """
    Combine files with the given variables and write to a new file

    :param files: The files to merge
    :param output_file: The output file to write to
    :param variables: The list of variables to combine
    """
    with tempfile.TemporaryDirectory() as temporary_directory:
        temporary_directory_path: pathlib.Path = pathlib.Path(temporary_directory)
        new_files: typing.List[pathlib.Path] = []

        for file in files:
            filename = pathlib.Path(file).name
            new_filename = f"with_record_{filename}"
            new_filepath: pathlib.Path = temporary_directory_path / new_filename
            summary: NetcdfSummary = NetcdfSummary.load(path=file)
            if not summary.unlimited_dimensions:
                header: str = get_header(target=file)
                raise ValueError(f"Cannot merge {file} in with other netcdf data - it lacks a record dimension{os.linesep}{header}")
            adjustment_assignments: typing.Dict[typing.Tuple[str, str], str] = {}
            for data_variable in summary.data_variables:
                if not set(summary.unlimited_dimensions).intersection(data_variable['dimensions']):
                    total_dimensions: typing.List[str] = [
                        dimension
                        for dimension in summary.unlimited_dimensions
                        if dimension not in data_variable['dimensions']
                    ]
                    total_dimensions += data_variable['dimensions']
                    old_variable_name: str = data_variable['name']
                    new_variable_name: str = f"{old_variable_name}_tmp"

                    rename_arguments: typing.Tuple[str, str] = (new_variable_name, old_variable_name)
                    adjustment_assignments[rename_arguments] = f"{new_variable_name}[{','.join(total_dimensions)}]={old_variable_name}"

            if adjustment_assignments:
                LOGGER.info(
                    f"Adding the record variable(s) to {len(adjustment_assignments)} data variables for later concatenation"
                )
                stdout, stderr = run_command(
                    NCOFunction.PERFORM_ARITHMETIC,
                    "-O",
                    "-s",
                    f'"{";".join(adjustment_assignments.values())}"',
                    file,
                    new_filepath
                )

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
            new_files.append(file)

        run_command(NCOFunction.CONCATENATE, *new_files, output_file)
        new_header = get_header(target=output_file)
        LOGGER.info(f"Merged Output:{os.linesep}{new_header}")


def apply_mask_by_file(
    input_file: typing.Union[str, pathlib.Path],
    mask_file: typing.Union[str, pathlib.Path],
    output_file: typing.Union[str, pathlib.Path],
    dimension: str = "feature_id") -> None:
    """
    Filter out data by applying a mask

    :param input_file: The path to the netcdf file to filter
    :param mask_file: The path to the file containing the mask
    :param output_file: The path to where to write the output to
    :param dimension: The dimension to apply the mask to
    """
    run_command(
        NCOFunction.KITCHEN_SINK,
        "-O",
        "-X",
        "-d",
        f"{dimension},,",
        "--cmp",
        mask_file,
        input_file,
        output_file
    )


def get_header(target: pathlib.Path) -> str:
    stdout, stderr = run_command("ncdump", "-h", target)
    if stderr:
        raise RuntimeError(stderr)
    return stdout


def add_or_modify_attribute(
    input_file: typing.Union[str, pathlib.Path],
    attribute_name: str,
    attribute_value: str,
    variable_name: str = "global"
) -> None:
    """
    Add or update a global or variable attribute

    :param input_file: The path to the file whose attribute is to be added or modified
    :param attribute_name: The name of the attribute to add or modify
    :param attribute_value: The value of the attribute
    :param variable_name: The name of the variable that receives the attribute. 'global' sets the value in the
        global scope
    """
    run_command(
        NCOFunction.EDIT_ATTRIBUTES,
        "-O",
        "-a",
        f"{attribute_name},{variable_name},o,c,{attribute_value}",
        input_file
    )


def rename_variable(
    input_file: typing.Union[str, pathlib.Path],
    old_name: str,
    new_name: str
) -> None:
    """
    Rename a single variable

    :param input_file: The path to the netcdf file to pull data from
    :param old_name: The name of the variable to rename
    :param new_name: The new name for the variable
    """
    run_command(
        NCOFunction.RENAME,
        "-O",
        "-v",
        f"{old_name},{new_name}",
        input_file
    )


def rename_dimension(input_file: typing.Union[str, pathlib.Path], old_name: str, new_name: str) -> None:
    """
    Rename a single dimension

    :param input_file: The path to the netcdf file to pull data from
    :param old_name: The name of the dimension to rename
    :param new_name: The new name for the dimension
    """
    run_command(
        NCOFunction.RENAME,
        "-O",
        "-d",
        f"{old_name},{new_name}",
        input_file
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


if _NCO_IS_AVAILABLE is None:
    _NCO_IS_AVAILABLE = bool(run_command(f"{NCOFunction.KITCHEN_SINK} --version"))

if not _NCO_IS_AVAILABLE:
    raise RuntimeError(f"Cannot use nco - make sure it is properly installed")

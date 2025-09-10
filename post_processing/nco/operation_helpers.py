"""
Helper classes and functions for netcdf operations
"""
import typing
import enum
import logging
import pathlib
import os
import subprocess

from datetime import datetime

LOGGER: logging.Logger = logging.getLogger(pathlib.Path(__file__).stem)

from post_processing.utilities.common import starmap
from post_processing.utilities.common import program_exists


class EditMode(enum.StrEnum):
    """
    Modes used when editing
    """
    APPEND = "a"
    """
    Append value to current attribute value, if any. If the attribute does not exist, it is created

    WARNING: For non-string scalar values, this is convert the attribute to an array and add the value to the new 
    array. For a string, this will concatenate.
    """
    CREATE = "c"
    """
    Create the attribute with the given value if it does not yet exist. Nothing happens if it already exists
    """
    DELETE = "d"
    """
    Remove the attribute if it exists
    """
    MODIFY = "m"
    """
    Change the value if the attribute exists. Nothing is done if it does not exist
    """
    APPEND_IF_EXISTS = "n"
    """
    Append the value, but only if it exists

    WARNING: For non-string scalar values, this is convert the attribute to an array and add the value to the new 
    array. For a string, this will concatenate.
    """
    OVERWRITE = "o"
    """
    Add the attribute if it does not exist or modify it if it does
    """
    PREPEND = "p"
    """
    Prepend the attribute with the given value

    WARNING: For non-string scalar values, this is convert the attribute to an array and add the value to the new 
    array. For a string, this will concatenate.
    """

    @classmethod
    def from_string(cls, string: str) -> "EditMode":
        string = string.strip().lower()
        mapping: dict[str, EditMode] = {
            "append_if_exists": cls.APPEND_IF_EXISTS
        }

        for member in cls:
            mapping[member.name.lower()] = member
            value: str = str(member.value)
            if member.value not in mapping:
                mapping[value] = member

        edit_mode: EditMode = mapping.get(string, None)

        if edit_mode is None:
            raise AttributeError(
                f"There is no edit mode in NCO that may be referred to as '{string}'"
            )

        return edit_mode


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
    HEADER = "ncdump"

    @classmethod
    def is_usable(cls) -> bool:
        programs_exist: typing.Sequence[bool] = starmap(
            function=program_exists,
            args=[{"program_name": str(member.value)} for member in cls],
            thread_count=True
        )

        return all(programs_exist)


def run_command(command: str, *positional_args, prevent_history: bool = True) -> tuple[str, str]:
    """
    A consistent function used to execute the CLI commands for running

    :param command: The command to execute
    :param positional_args: Positional arguments to add to the call
    :param prevent_history: Whether to ensure that NCO doesn't add global attributes showing the last used command
    :returns: Stdout and stderr
    """
    if prevent_history and not {'-h', '--hst', '--history'}.intersection(positional_args):
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
        raise RuntimeError(
            f"Netcdf command failed:{os.linesep}"
            f"    {command}{os.linesep}"
            f"STDOUT:{os.linesep}"
            f"{command_result.stderr}{os.linesep}"
            f"STDERR:{os.linesep}"
            f"{command_result.stdout}{os.linesep}"
            f""
        )

    return command_result.stdout.strip(), command_result.stderr.strip()


def get_header(target: pathlib.Path) -> str:
    """
    Get the header of a netcdf file at the indicated path

    :param target: The path to the netcdf file
    :return: The header of the netcdf file
    """
    stdout, stderr = run_command(NCOFunction.HEADER, "-h", target)
    if stderr:
        raise RuntimeError(stderr)
    return stdout


# Fail if NCO is not available for use
if not NCOFunction.is_usable():
    raise OSError("Cannot use nco - applications are missing")

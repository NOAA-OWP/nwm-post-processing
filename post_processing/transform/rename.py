"""
Functions and objects used to rename a variable and/or dimension in a netcdf file
"""
import typing
import pathlib
import logging

LOGGER: logging.Logger = logging.getLogger(pathlib.Path(__file__).stem)


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

    import tempfile
    import shutil
    from post_processing.utilities.netcdf import load_netcdf
    from post_processing.utilities.netcdf import save_netcdf

    if output_path.is_dir():
        output_path = output_path / input_path.name

    with tempfile.TemporaryDirectory() as temporary_directory:
        temporary_path: pathlib.Path = pathlib.Path(temporary_directory)
        temporary_output_path: pathlib.Path = temporary_path / output_path.name

        with load_netcdf(path=input_path) as input_file:
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

            input_file.set_coords(coordinates_to_assign)
            save_netcdf(temporary_output_path, input_file)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(temporary_output_path, output_path)

    return output_path

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

    import tempfile
    import shutil
    from post_processing.utilities.netcdf import load_netcdf
    from post_processing.utilities.netcdf import save_netcdf

    if output_path.is_dir():
        output_path = output_path / input_path.name

    with tempfile.TemporaryDirectory() as temporary_directory:
        temporary_path: pathlib.Path = pathlib.Path(temporary_directory)
        temporary_output_path: pathlib.Path = temporary_path / output_path.name

        try:
            with load_netcdf(path=input_path) as input_file:
                input_file = input_file.rename_dims(dims_dict=mapping)
                save_netcdf(temporary_output_path, input_file)
        except:
            from post_processing.configuration import settings
            new_path: pathlib.Path = settings.application_path / "failed" / input_path.name
            shutil.copy(input_path, new_path)
            LOGGER.error(f"Copied the broken file at '{input_path}' to '{new_path}'")
            raise

        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(temporary_output_path, output_path)

    return output_path

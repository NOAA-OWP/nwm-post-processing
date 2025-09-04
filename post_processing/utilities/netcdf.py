"""
Helper functions and objects used to standardize file IO operations
"""
import typing
import logging
import pathlib
import os
from threading import RLock
import collections.abc as generic

from post_processing import enums
from post_processing.configuration import settings
from post_processing.enums import Verbosity

from post_processing.utilities.common import timed_function

if typing.TYPE_CHECKING:
    import xarray
    import numpy


LOGGER: logging.Logger = logging.getLogger(pathlib.Path(__file__).stem)

OPEN_LOCK: RLock = RLock()
"""Lock to help secure file opening"""

os.environ.setdefault("HDF5_USE_FILE_LOCKING", "FALSE")

def get_default_encoding() -> dict["numpy.dtype", dict[str, typing.Any]]:
    """
    Get default encoding values for different dtypes to enforce if one is not provided on netcdf values to save

    TODO: It might be more appropriate to add a configuration for this
    :returns: A dictionary mapping dtypes to default configuration values
    """
    import numpy
    encodings: dict[numpy.dtype, dict[str, typing.Any]] = {}
    encodings[numpy.dtype("float32")] = {
        "_FillValue": numpy.int32(-99_900),
        "scale_factor": numpy.float32(0.01),
        "missing_value": numpy.int32(-99_900),
        "zlib": True,
        "shuffle": True,
        "complevel": 3,
        "dtype": numpy.int32,
    }
    encodings[numpy.dtype("float64")] = encodings[numpy.dtype("float32")].copy()
    return encodings

@timed_function()
def operate_on_variable(
    path: pathlib.Path | str,
    variable_name: str,
    operation: generic.Callable[["xarray.DataArray"], typing.Union["xarray.DataArray", "numpy.ndarray"]],
    engine: str | typing.Literal['h5netcdf', 'netcdf4'] = settings.default_netcdf_engine,
    chunks: generic.Mapping[str, typing.Any] | typing.Literal['auto'] | None = "auto",
    **kwargs,
) -> typing.Union["xarray.DataArray", "numpy.ndarray"]:
    """
    Perform a quick operation on a single variable within a single netcdf file

    :param path: The path to the netcdf file of interest
    :param variable_name: The name of the variable to operate on
    :param operation: The function to perform
    :param engine: The netcdf engine to use to load data
    :param chunks: The configuration for loading in data as chunks
    :param kwargs: Keyword arguments for xarray.open_dataset
    :returns: The numpy array containing the results of the operation
    """
    if isinstance(path, str):
        path = pathlib.Path(path)

    if not isinstance(path, pathlib.Path):
        raise TypeError(
            f"Cannot operate on a variable - the path must be a pathlike object but received '{path}' (type={type(path)})"
        )

    import xarray

    with OPEN_LOCK:
        with xarray.open_dataset(filename_or_obj=path, engine=engine, chunks=chunks, **kwargs) as dataset:
            if variable_name not in dataset:
                raise KeyError(
                    f"Cannot operate on '{path.name}::{variable_name}' - '{variable_name}' is not a member of '{path}'"
                )
            result = operation(dataset[variable_name])
            return result

@timed_function()
def load_variable(
    path: pathlib.Path | str | generic.Sequence[pathlib.Path | str],
    variable_name: str,
    engine: str | typing.Literal['h5netcdf', "netcdf4"] = settings.default_netcdf_engine,
    chunks: generic.Mapping[str, typing.Any] | typing.Literal['auto'] = "auto",
    **kwargs
) -> "xarray.DataArray":
    """

    """
    if chunks is None:
        raise ValueError(f"Individual Netcdf Variables cannot be loaded without chunking")

    import xarray
    with OPEN_LOCK:
        with xarray.open_dataset(filename_or_obj=path, engine=engine, chunks=chunks, **kwargs) as dataset:
            if variable_name not in dataset:
                raise KeyError(f"There is no '{variable_name}' variable in {path}")

            variable: xarray.DataArray = dataset[variable_name]

            if hasattr(variable, "compute") and callable(variable.compute):
                variable = variable.compute().copy()
            return variable


# NOTE: The SimpleCache is no longer used here due to memory bloat and stagnation; most files will only be loaded once
@timed_function()
def load_netcdf(
    path: typing.Union[pathlib.Path, str, typing.Sequence[typing.Union[pathlib.Path, str]]],
    engine: typing.Union[str, typing.Literal["h5netcdf", "zarr", "netcdf4"]] = settings.default_netcdf_engine,
    chunks: typing.Optional[typing.Union[typing.Mapping[str, typing.Any], typing.Literal['auto', 'force']]] = None,
    full_load: bool = False,
    **kwargs
) -> "xarray.Dataset":
    """
    Load a thread-safe, lazy netcdf file

    :param path: path to netcdf file
    :param engine: The engine to use to load the netcdf data into memory
    :param chunks: The chunks to load into memory. Use 'force' to force a lazy load.
    :param kwargs: Keyword arguments to pass to xarray.open_dataset. See: https://docs.xarray.dev/en/stable/generated/xarray.open_dataset.html
    """
    if settings.lazy_load_netcdf and chunks is None:
        chunks = "auto"
    elif isinstance(chunks, str) and chunks.lower() == 'force':
        chunks = 'auto'
    elif not settings.lazy_load_netcdf:
        chunks = None

    if engine not in ("h5netcdf", "zarr", "netcdf4"):
        raise ValueError(f"{engine} is not a supported engine - only 'h5netcdf', 'netcdf4', and 'zarr' are supported")

    import xarray

    if isinstance(path, xarray.Dataset):
        LOGGER.warning("A dataset was passed to 'load_netcdf' instead of a path - this was an unneeded operation")
        return path

    if isinstance(path, typing.Sequence) and len(path) == 1:
        path = path[0]

    import time
    if isinstance(path, (pathlib.Path, str)):
        maximum_retries: int = 5
        attempts: int = 0
        dataset: typing.Optional[xarray.Dataset] = None
        last_exception: typing.Optional[Exception] = None
        while attempts < maximum_retries and dataset is None:
            try:
                if settings.verbosity >= enums.Verbosity.LOUD:
                    LOGGER.debug(f"Loading '{path}'", stack_info=True)

                with OPEN_LOCK:
                    dataset: xarray.Dataset = xarray.open_dataset(path, engine=engine, chunks=chunks, **kwargs)
                    if full_load:
                        dataset = dataset.load()
                        dataset.close()
                    return dataset

                # NOTE: It would be safer to load everything in full and move on, but that adds a significant
                # performance cost. For now, full loads won't be performed by default.
            except Exception as e:
                last_exception = e
                last_exception.args = (f"Could not load data at '{path}'. {e.args[0]}", *e.args[1:])
            attempts += 1
            LOGGER.error(
                f"Failed to load {path}{' due to ' + str(last_exception) if last_exception else ''}. "
                f"Waiting and trying again..."
            )
            time.sleep(1)
        if dataset is None:
            raise (last_exception or RuntimeError(f"Could not load '{path}'"))
    else:
        # Your IDE may complain about the `data` parameter - it is a false positive. A sequence of paths is fine
        dataset: xarray.Dataset = xarray.open_mfdataset(
            paths=path,
            chunks=chunks,
            combine="by_coords",
            engine=engine,
            **kwargs
        )

    return dataset


@timed_function()
def load_metadata(
    path: typing.Union[pathlib.Path, str, typing.Sequence[typing.Union[pathlib.Path, str]]],
    engine: typing.Union[str, typing.Literal["h5netcdf", "zarr", "netcdf4"]] = settings.default_netcdf_engine,
) -> dict[str, typing.Any]:
    """
    Get the metadata attached to a netcdf file and its variables

    Variable attributes will be prefixed by the variable name. If variable 'streamflow' has an attribute named
    'standard_name', the value will be found at 'streamflow.standard_name'

    :param path: The path (or paths) to the netcdf data to inspect
    :param engine: The engine that will load and interpret the data
    :returns: A dictionary containing all the attributes in it and on its variables. Variable attributes will be prefixed by the variable name
    """
    import xarray

    if isinstance(path, pathlib.Path):
        path = [path]

    metadata: dict[str, typing.Any] = {}
    for input_path in path:
        with OPEN_LOCK:
            with xarray.open_dataset(filename_or_obj=input_path, engine=engine, chunks="auto") as source:
                metadata.update({
                    str(key): format_value(value)
                    for key, value in source.attrs.items()
                })

                for coordinate_name, coordinate_data in source.coords.items():
                    metadata.update(_get_variable_metadata(variable=coordinate_data))

                for variable_name, variable_data in source.data_vars.items():
                    metadata.update(_get_variable_metadata(variable=variable_data))

    return metadata

@timed_function()
def _get_variable_metadata(variable: "xarray.DataArray") -> dict[str, typing.Any]:
    """
    Get the metadata for specific netcdf variable

    :param variable: The variable to inspect
    :returns: The variable's metadata in the form of a dictionary
    """
    import numpy
    metadata = {
        f"{variable.name}.{attribute_name}": format_value(attribute_value)
        for attribute_name, attribute_value in variable.attrs.items()
    }
    if variable.shape == (1,):
        if isinstance(variable.values, typing.Iterable):
            value: typing.Any = variable.values[0]
        else:
            value: typing.Any = variable.values

        if isinstance(value, numpy.datetime64):
            from datetime import datetime
            value: datetime = value.astype('datetime64[ms]').item()
            metadata[f"{variable.name}__date"] = value.strftime("%Y%m%d")
            metadata[f"{variable.name}__hour"] = value.hour
            metadata[f"{variable.name}__minute"] = value.minute
            metadata[f"{variable.name}__second"] = value.second
            metadata[f"{variable.name}__day"] = value.day
            metadata[f"{variable.name}__month"] = value.month
            metadata[f"{variable.name}__year"] = value.year
        else:
            metadata[str(variable.name)] = value.item()
    return metadata


def format_value(value: object) -> str:
    """
    Format values so that they are a little more friendly for presentation

    :param value: The value to format
    :returns: The value formatted in a way that's easy to see in a log or terminal
    """
    import numpy
    from post_processing.configuration import settings
    from datetime import datetime
    if isinstance(value, numpy.datetime64):
        value = value.astype('datetime64[ms]').item()
    elif isinstance(value, numpy.ndarray):
        value = list(map(format_value, value.tolist()))
    elif hasattr(value, "item"):
        try:
            value = value.item()
        except:
            pass

    if isinstance(value, (numpy.floating, float)):
        value = f"{float(value):,.2f}"
    elif isinstance(value, (int, numpy.integer)):
        value = f"{int(value):,}"
    elif isinstance(value, datetime):
        value = value.strftime(settings.date_format)

    return str(value)


@timed_function()
def format_variable(var: "xarray.DataArray") -> typing.Sequence[str]:
    """
    Format a block of text describing a variable

    :param var: The variable to format
    :returns: Lines of text describing the variable
    """
    tab: str = "    "

    variable_definition_template: str = "{dtype} {variable_name}({dimensions}):"
    lines_for_variable: list[str] = [
        variable_definition_template.format(
            dtype=str(var.dtype),
            variable_name=var.name,
            dimensions=", ".join(map(lambda kv: f"{kv[0]}={kv[1]}", var.sizes.items()))
        ),
    ]

    attribute_template: str = tab + tab + "{variable_name}::{attribute_name} = {attribute_value}"

    if var.coords:
        longest_coordinate_name: str = max(map(str, var.coords.keys()), key=len)
        name_length: int = len(longest_coordinate_name) + 5
        lines_for_variable.extend([
            tab + "Coordinates:",
            *[
                tab + tab + f"{str(coordinate_name).ljust(name_length)}: {coordinate.shape}"
                for coordinate_name, coordinate in var.coords.items()
            ]
        ])

    if var.attrs:
        longest_attr_name: str = max(map(str, var.attrs.keys()), key=len)
        name_length: int = len(longest_attr_name) + 5
        lines_for_variable.extend([
            tab + "Attributes:",
            *[
                attribute_template.format(
                    variable_name=var.name,
                    attribute_name=attr_name.ljust(name_length),
                    attribute_value=format_value(value=attr_value)
                )
                for attr_name, attr_value in var.attrs.items()
            ]
        ])

    if var.encoding:
        lines_for_variable.extend([
            tab + "Encoding:",
            *[
                attribute_template.format(
                    variable_name=var.name,
                    attribute_name=attr_name,
                    attribute_value=format_value(value=attr_value)
                )
                for attr_name, attr_value in var.encoding.items()
                if bool(attr_value)
            ]
        ])
    return lines_for_variable


@timed_function()
def describe_netcdf(
    netcdf_file: "xarray.Dataset",
    variable_name: str = None,
    max_line_length: int = None
) -> str:
    """
    Create a detailed string representation of the netcdf and its data

    :param netcdf_file: An already opened netcdf file
    :param variable_name: The optional name of a variable to describe
    :param max_line_length: A limit on the length of text to render
    :returns: A detailed description of what is in the given netcdf dataset
    """
    import os

    separator_placeholder: str = "{separator}"
    separator: str = "="
    tab: str = "    "

    lines: list[str] = [
        separator_placeholder,
    ]
    if variable_name is not None:
        description: typing.Sequence[str] = format_variable(var=netcdf_file.get(variable_name))
        return os.linesep.join(description)

    longest_dimension_name: str = max(map(str, netcdf_file.sizes.keys()), key=len)
    dimension_name_length: int = len(longest_dimension_name) + 5
    lines.extend([
        "Dimensions:",
        *[
            f"{tab}{str(dimension).ljust(dimension_name_length)}: {count:,}"
            for dimension, count in netcdf_file.sizes.items()
        ],
        separator_placeholder,
    ])

    if len(netcdf_file.indexes) > 0:
        lines.extend([
            "Indices:",
            *[
                f"{tab}- {index_name}"
                for index_name in netcdf_file.indexes.keys()
            ],
            separator_placeholder,
        ])

    lines.append("Variables:")
    for variable in [*list(netcdf_file.data_vars.values()), *list(netcdf_file.coords.values())]:
        variable_lines: typing.Iterable[str] = map(lambda line: tab + line, format_variable(var=variable))
        lines.extend(variable_lines)

    lines.append(separator_placeholder)

    if netcdf_file.attrs:
        lines.append(f"{tab}Attributes:")
        indent: str = tab * 2
        longest_attribute_name: str = max(map(str, netcdf_file.attrs.keys()), key=len)
        attribute_name_length: int = len(longest_attribute_name) + 5
        for attribute_name, attribute_value in netcdf_file.attrs.items():
            lines.append(
                f"{indent}{attribute_name.ljust(attribute_name_length)}: {format_value(value=attribute_value)}"
            )

    longest_line: int = max(*map(len, lines), 1)
    separator_character_count: int = longest_line + 5
    lines = [
        separator * separator_character_count if line == separator_placeholder else line
        for line in lines
    ]
    if max_line_length is not None and max_line_length > 0:
        lines = [
            line[:max_line_length]
            for line in lines
        ]
    return os.linesep.join(lines)


@timed_function()
def peek(
    path: typing.Union[str, pathlib.Path],
    engine: typing.Union[str, typing.Literal["h5netcdf", "netcdf4"]] = settings.default_netcdf_engine,
    max_line_length: int = 150,
    *,
    variable_name: str = None,
    **kwargs
) -> str:
    """
    Generate a string that peeks into the contents of a NetCDF file

    :param path: The path to the file
    :param engine: The engine that will load and interpret the data
    :param max_line_length: The maximum width of the display
    :param variable_name: The name of the variable to peek into instead of the entire file
    :param kwargs: Additional arguments to pass to the engine
    :returns: A human friendly representation of the contents of the file
    """
    import xarray
    with OPEN_LOCK:
        with xarray.open_dataset(filename_or_obj=path, engine=engine, chunks={}, **kwargs) as netcdf_file:
            return describe_netcdf(
                netcdf_file=netcdf_file,
                variable_name=variable_name,
                max_line_length=max_line_length
            )

@timed_function()
def save_netcdf(
    path: typing.Union[str, pathlib.Path],
    dataset: "xarray.Dataset",
    engine: typing.Literal["h5netcdf", "netcdf4"] = settings.default_netcdf_engine,
    **kwargs
) -> bool:
    """
    Safely save an xarray dataset to netcdf. Only saves locally.

    :param path: The path to where the data should be saved
    :param dataset: The data to save
    :param engine: The name of the netcdf engine to use to write the data
    :param kwargs: Arguments to pass to the xarray.Dataset.to_netcdf function. See: https://docs.xarray.dev/en/stable/generated/xarray.Dataset.to_netcdf.html
    :returns: Whether the netcdf file that was supposed to be saved exists
    """
    import numpy
    if isinstance(path, str):
        path = pathlib.Path(path)

    if isinstance(path, pathlib.Path):
        path.parent.mkdir(parents=True, exist_ok=True)
    else:
        raise TypeError(f"{path} (type={type(path)}) is not a valid path. It must be a str or pathlib.Path")

    if engine not in ("h5netcdf", "netcdf4"):
        raise ValueError(f"{engine} is not a supported engine - only 'h5netcdf' and 'netcdf4' are supported")

    compute = str(kwargs.get('compute', True)).lower() in ('true', 'yes', 't', 'y', '1')

    try:
        default_encodings: dict[numpy.dtype, dict[str, typing.Any]] = get_default_encoding()
        for variable_name, variable in [*dataset.coords.items(), *dataset.data_vars.items()]:
            default_encoding: dict[str, typing.Any] = default_encodings.get(variable.dtype, {})
            for encoding_key, encoding_value in default_encoding.items():
                if encoding_key not in dataset[variable_name].encoding:
                    dataset[variable_name].encoding[encoding_key] = encoding_value
    except BaseException as e:
        LOGGER.error(f"Ran into issues when configuring encoding settings for '{path.name}': {e}")
        raise

    # TODO: manually scale floats to ints - numpy will end up being more efficient
    try:
        with OPEN_LOCK:
            delayed_write: typing.Optional = dataset.to_netcdf(path=path, engine=engine, **kwargs, compute=compute)
    except BaseException as e:
        LOGGER.error(f"Could not write to '{path}' - {e}")
        raise

    if delayed_write is not None:
        import dask
        dask.compute(delayed_write)

    if settings.verbosity >= Verbosity.LOUD:
        LOGGER.debug(f"Saved netcdf data to: {path}")

    if hasattr(peek, "add"):
        description: str = describe_netcdf(netcdf_file=dataset)
        peek.add(args=(path,), result=description)
        peek.add(kwargs={"path": path}, result=description)

    return path.is_file()

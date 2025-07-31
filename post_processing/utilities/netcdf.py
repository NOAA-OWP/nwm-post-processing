"""
Helper functions and objects used to standardize file IO operations
"""
import typing
import logging
import pathlib
from threading import RLock

from post_processing.configuration import settings
from post_processing.utilities.simple_cache import simple_cache
from post_processing.utilities.simple_cache import CacheEntry
from post_processing.enums import Verbosity

if typing.TYPE_CHECKING:
    import xarray


LOGGER: logging.Logger = logging.getLogger(pathlib.Path(__file__).stem)
SAVED_FILES: typing.List[pathlib.Path] = []
"""
A list of files that have been saved. This will indicate if there are frequent rewrites to a file that may end up
 invalidating data
"""
SAVED_FILE_LOCK: RLock = RLock()
"""A lock to ensure that SAVED_FILES is only acted upon by one thread at a time"""


def record_saved_file(path: pathlib.Path):
    """
    Record a saved file in order to issue an alert if a netcdf file is written to multiple times, putting the system at risk of a segfault

    :param path: The path to where a file was saved
    """
    with SAVED_FILE_LOCK:
        if path in SAVED_FILES:
            LOGGER.warning(f"File '{path}' has now been saved to {len(list(filter(lambda p: p == path, SAVED_FILES)))} times")
        SAVED_FILES.append(path)


def _invalidate_netcdf_cache(cache_entry: CacheEntry["xarray.Dataset"]) -> bool:
    """
    Invalidate the cache entry for this if the object on disk has changed since this was cached

    Args:
        cache_entry: The cache entry to be invalidated

    Returns:
        True if the cache entry should be invalidated
    """
    if settings.lazy_load_netcdf:
        return True

    from datetime import datetime
    path: typing.Union[pathlib.Path, str, typing.Sequence[typing.Union[pathlib.Path, str]]] = cache_entry.key.kwargs.get("path")

    if path is None:
        path = cache_entry.key.args[0]

    if isinstance(path, str):
        path = pathlib.Path(path)
    elif isinstance(path, typing.Sequence):
        path: typing.Sequence[pathlib.Path] = list(map(pathlib.Path, path))

    if isinstance(path, pathlib.Path):
        path: typing.Sequence[pathlib.Path] = [path]

    last_modified: datetime = max(
        datetime.fromtimestamp(referenced_path.stat().st_mtime)
        for referenced_path in path
    )
    last_cache_access: datetime = cache_entry.last_accessed
    return last_modified <= last_cache_access


@simple_cache(invalidator_function=_invalidate_netcdf_cache, max_size=settings.netcdf_cache_size)
def load_netcdf(
    path: typing.Union[pathlib.Path, str, typing.Sequence[typing.Union[pathlib.Path, str]]],
    engine: typing.Union[str, typing.Literal["h5netcdf", "zarr", "netcdf4"]] = settings.default_netcdf_engine,
    chunks: typing.Optional[typing.Union[typing.Mapping[str, typing.Any], typing.Literal['auto', 'force']]] = None,
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

    if isinstance(path, typing.Sequence) and len(path) == 1:
        path = path[0]

    if isinstance(path, (pathlib.Path, str)):
        maximum_retries: int = 5
        attempts: int = 0
        dataset: typing.Optional[xarray.Dataset] = None
        last_exception: typing.Optional[Exception] = None
        while attempts < maximum_retries and dataset is None:
            try:
                dataset: xarray.Dataset = xarray.open_dataset(path, engine=engine, chunks=chunks, **kwargs)
                break
            except Exception as e:
                last_exception = e
                last_exception.args = (f"Could not load data at '{path}'. {e.args[0]}", *e.args[1:])
            attempts += 1
            LOGGER.error(f"Failed to load {path}. Waiting and trying again...")
            import time
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


@simple_cache(invalidator_function=_invalidate_netcdf_cache)
def load_metadata(
    path: typing.Union[pathlib.Path, str, typing.Sequence[typing.Union[pathlib.Path, str]]],
    engine: typing.Union[str, typing.Literal["h5netcdf", "zarr", "netcdf4"]] = settings.default_netcdf_engine,
) -> typing.Dict[str, typing.Any]:
    """
    Get the metadata attached to a netcdf file and its variables

    Variable attributes will be prefixed by the variable name. If variable 'streamflow' has an attribute named
    'standard_name', the value will be found at 'streamflow.standard_name'

    :param path: The path (or paths) to the netcdf data to inspect
    :param engine: The engine that will load and interpret the data
    :returns: A dictionary containing all the attributes in it and on its variables. Variable attributes will be prefixed by the variable name
    """
    if isinstance(path, pathlib.Path):
        path = [path]

    metadata: typing.Dict[str, typing.Any] = {}
    for input_path in path:
        source: xarray.Dataset = load_netcdf(path=input_path, engine=engine)
        metadata.update({
            str(key): format_attribute_value(value)
            for key, value in source.attrs.items()
        })

        for coordinate_name, coordinate_data in source.coords.items():
            metadata.update(_get_variable_metadata(variable=coordinate_data))

        for variable_name, variable_data in source.data_vars.items():
            metadata.update(_get_variable_metadata(variable=variable_data))

    return metadata


def _get_variable_metadata(variable: "xarray.DataArray") -> typing.Dict[str, typing.Any]:
    """
    Get the metadata for specific netcdf variable

    :param variable: The variable to inspect
    :returns: The variable's metadata in the form of a dictionary
    """
    import numpy
    metadata = {
        f"{variable.name}.{attribute_name}": format_attribute_value(attribute_value)
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


def format_attribute_value(value: typing.Any) -> typing.Any:
    import numpy
    if isinstance(value, numpy.datetime64):
        value = value.astype('datetime64[ms]').item()
    elif isinstance(value, numpy.ndarray):
        value = value.tolist()
    elif hasattr(value, "item"):
        try:
            value = value.item()
        except:
            value = str(value)
    return value


def peek(
    path: typing.Union[str, pathlib.Path],
    engine: typing.Union[str, typing.Literal["h5netcdf", "netcdf4"]] = settings.default_netcdf_engine,
    max_line_length: int = 150,
    **kwargs
) -> str:
    """
    Generate a string that peeks into the contents of a NetCDF file

    :param path: The path to the file
    :param engine: The engine that will load and interpret the data
    :param max_line_length: The maximum width of the display
    :param kwargs: Additional arguments to pass to the engine
    :returns: A human friendly representation of the contents of the file
    """
    import os
    import xarray
    separator_placeholder: str = "{separator}"
    separator: str = "="
    tab: str = "    "

    variable_definition_template: str = "{dtype} {variable_name}({dimensions}):"
    lines: typing.List[str] = [
        separator_placeholder,
    ]

    def format_variable(var: xarray.DataArray) -> typing.Sequence[str]:
        """
        Format a block of text describing a variable

        :param var: The variable to format
        :returns: Lines of text describing the variable
        """
        lines_for_variable: typing.List[str] = [
            variable_definition_template.format(
                dtype=str(var.dtype),
                variable_name=var.name,
                dimensions=", ".join(map(lambda kv: f"{kv[0]}={kv[1]}", var.sizes.items()))
            ),
        ]

        attribute_template: str = tab + tab + "{variable_name}::{attribute_name} = {attribute_value}"

        if var.attrs:
            lines_for_variable.extend([
                tab + "Attributes:",
                *[
                    attribute_template.format(
                        variable_name=var.name,
                        attribute_name=attribute_name,
                        attribute_value=str(attribute_value)
                    )
                    for attribute_name, attribute_value in var.attrs.items()
                ]
            ])

        if var.encoding:
            lines_for_variable.extend([
                tab + "Encoding:",
                *[
                    attribute_template.format(
                        variable_name=var.name,
                        attribute_name=attribute_name,
                        attribute_value=str(attribute_value)
                    )
                    for attribute_name, attribute_value in var.encoding.items()
                ]
            ])
        return lines_for_variable

    with load_netcdf(path=path, engine=engine, chunks='force', **kwargs) as netcdf_file:
        longest_dimension_name: str = max(map(str, netcdf_file.sizes.keys()), key=len)
        dimension_name_length: int = len(longest_dimension_name) + 5
        lines.extend([
            "Dimensions:",
            *[
                f"{tab}{str(dimension).ljust(dimension_name_length)}: {count}"
                for dimension, count in netcdf_file.sizes.items()
            ],
            separator_placeholder,
            "Variables:",
        ])

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
                lines.append(f"{indent}{attribute_name.ljust(attribute_name_length)}: {attribute_value}")

    longest_line: int = max(*map(len, lines), 1)
    separator_character_count: int = longest_line + 5
    lines = [
        separator * separator_character_count if line == separator_placeholder else line
        for line in lines
    ]
    lines = [
        line[:max_line_length]
        for line in lines
    ]
    return os.linesep.join(lines)

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
    if isinstance(path, str):
        path = pathlib.Path(path)

    if isinstance(path, pathlib.Path):
        path.parent.mkdir(parents=True, exist_ok=True)
    else:
        raise TypeError(f"{path} (type={type(path)}) is not a valid path. It must be a str or pathlib.Path")

    if engine not in ("h5netcdf", "netcdf4"):
        raise ValueError(f"{engine} is not a supported engine - only 'h5netcdf' and 'netcdf4' are supported")

    compute = str(kwargs.get('compute', True)).lower() in ('true', 'yes', 't', 'y', '1')
    dataset.to_netcdf(path=path, engine=engine, **kwargs, compute=compute)

    if settings.verbosity >= Verbosity.LOUD:
        LOGGER.debug(f"Saved netcdf data to: {path}")
    record_saved_file(path=path)
    return path.is_file()

"""
Helper functions and objects used to standardize file IO operations
"""
import typing
import logging
import pathlib
import os
from threading import RLock
import collections.abc as generic
import atexit
import dataclasses

from post_processing.configuration import settings

from post_processing.utilities.common import timed_function
from post_processing.interfaces.aliases import DatasetFunction
from post_processing.interfaces.aliases import DataArrayFunction

VariableParameters = typing.ParamSpec("VariableParameters")
T = typing.TypeVar("T")
if typing.TYPE_CHECKING:
    import xarray
    import numpy
    from post_processing.work import gateway
    from post_processing.interfaces.work import PendingTaskResult


LOGGER: logging.Logger = logging.getLogger(pathlib.Path(__file__).stem)

_DEFAULT_ENCODING: generic.Mapping["numpy.dtype", dict[str, typing.Any]] | None = None

WRITER_QUEUE_LENGTH: int = int(float(os.environ.get(f"{settings.prefix}_WRITER_QUEUE_LENGTH", 5)))
WRITER_WAIT_SECONDS: float = float(os.environ.get(f"{settings.prefix}_WRITER_WAIT_SECONDS", 1.5))

OPEN_LOCK: RLock = RLock()
"""Lock to help secure file opening"""

__IO_GATEWAY: typing.Optional["gateway.Gateway"] = None

os.environ.setdefault("HDF5_USE_FILE_LOCKING", "FALSE")

@atexit.register
def close_gateway():
    """
    Shut down the thread handler for Netcdf IO
    """
    global __IO_GATEWAY
    if __IO_GATEWAY is not None and __IO_GATEWAY.running:
        with OPEN_LOCK:
            if __IO_GATEWAY is not None and __IO_GATEWAY.running:
                __IO_GATEWAY.shutdown()
                del __IO_GATEWAY
                __IO_GATEWAY = None

def _open_gateway():
    """
    Open up the pathway for NetCDF IO
    """
    from post_processing.work.gateway import get_gateway
    global __IO_GATEWAY
    if __IO_GATEWAY is None or not __IO_GATEWAY.running:
        with OPEN_LOCK:
            if __IO_GATEWAY is None or not __IO_GATEWAY.running:
                __IO_GATEWAY = get_gateway(
                    queue_length=WRITER_QUEUE_LENGTH,
                    wait_seconds=WRITER_WAIT_SECONDS,
                )
                __IO_GATEWAY.start()

def submit_write(
    dataset: "xarray.Dataset",
    target: pathlib.Path,
    write_arguments: dict[str, typing.Any] = None,
    *,
    compute: bool = True,
    **kwargs
) -> "PendingTaskResult[pathlib.Path]":
    """
    Submit a task to the Netcdf gateway to write a dataset to disk

    :param dataset: The data to write to disk
    :param target: Where to write the data
    :param write_arguments: Keyword arguments to feed to the netcdf writer
    :param compute: Whether to compute writes because the data is a dask dataset with unevaluated expressions
    :returns: The future reference to the written path
    """
    _open_gateway()
    #if compute and hasattr(dataset, "compute") and callable(dataset.compute):
    #    dataset = dataset.compute()

    try:
        default_encodings: dict[numpy.dtype, dict[str, typing.Any]] = get_default_encoding()
        for variable_name, variable in [*dataset.coords.items(), *dataset.data_vars.items()]:
            if variable.encoding:
                continue
            default_encoding: dict[str, typing.Any] = default_encodings.get(variable.dtype, {})
            for encoding_key, encoding_value in default_encoding.items():
                if encoding_key not in dataset[variable_name].encoding:
                    dataset[variable_name].encoding[encoding_key] = encoding_value
    except BaseException as e:
        LOGGER.error(f"Ran into issues when configuring encoding settings for '{target.name}': {e}")
        raise

    if write_arguments is None:
        write_arguments = {}

    write_arguments.update(kwargs)

    from post_processing.work.tasks.writing import SaveTask
    from post_processing.interfaces.work import PendingTaskResult

    task: SaveTask = SaveTask(
        dataset=dataset,
        target=target,
        kwargs=write_arguments,
    )

    future_result: PendingTaskResult[pathlib.Path] = __IO_GATEWAY.enqueue(task=task)
    return future_result

def submit_load_array(
    target: pathlib.Path,
    variable_name: str,
    load_kwargs: dict[str, typing.Any] = None,
    engine: str = settings.default_netcdf_engine,
    **kwargs
) -> "PendingTaskResult[numpy.typing.NDArray]":
    """
    Submit a task to the IO Gateway that will return the numpy array from a netcdf variable

    :param target: Where the data to load lives
    :param variable_name: Name of the variable to load
    :param load_kwargs: Keyword arguments to feed to the netcdf load function
    :param engine: Which engine to use to interpret the netcdf data
    """
    _open_gateway()
    if load_kwargs is None:
        load_kwargs = {}

    load_kwargs.update(kwargs)

    from post_processing.work.tasks.reading import ArrayLoadTask
    from post_processing.interfaces.work import PendingTaskResult

    task: ArrayLoadTask = ArrayLoadTask(
        target=target,
        variable_name=variable_name,
        kwargs=load_kwargs,
        engine=engine
    )

    future_result: PendingTaskResult["numpy.typing.NDArray"] = __IO_GATEWAY.enqueue(task=task)

    return future_result


def submit_select(
    target: pathlib.Path | generic.Sequence[pathlib.Path],
    variable_name: str,
    criteria: dict[str, typing.Any],
    load_kwargs: dict[str, typing.Any] = None,
    engine: str = settings.default_netcdf_engine,
    method: str = None,
    drop: bool = True,
    **kwargs
) -> "PendingTaskResult[xarray.DataArray]":
    """
    Submit a task to the IO gateway that will select a portion of data from a file
    """
    _open_gateway()
    if load_kwargs is None:
        load_kwargs = {}

    load_kwargs.update(kwargs)

    from post_processing.work.tasks.reading import SelectTask
    from post_processing.interfaces.work import PendingTaskResult

    task: SelectTask = SelectTask(
        target=target,
        variable_name=variable_name,
        criteria=criteria,
        method=method,
        drop=drop,
        kwargs=load_kwargs,
        engine=engine
    )

    future_result: PendingTaskResult["xarray.DataArray"] = __IO_GATEWAY.enqueue(task=task)

    return future_result

def submit_variable_transformation(
    target: pathlib.Path,
    variable_name: str,
    function: DataArrayFunction,
    kwargs: dict[str, typing.Any] = None,
    selector: dict[str, typing.Any] = None,
    selector_method: str = None,
    drop_unselected: bool = True,
    data_filter: typing.Callable[["xarray.DataArray"], "xarray.DataArray"] = None,
) -> "PendingTaskResult[T]":
    """
    Submit a job to transform a variable
    """
    _open_gateway()

    from post_processing.work.tasks.reading import TransformVariableTask
    from post_processing.interfaces.work import PendingTaskResult

    task: TransformVariableTask[T] = TransformVariableTask(
        target=target,
        variable_name=variable_name,
        function=function,
        kwargs=kwargs,
        selector=selector,
        selector_method=selector_method,
        drop_unselected=drop_unselected,
        data_filter=data_filter,
    )

    pending_result: PendingTaskResult[T] = __IO_GATEWAY.enqueue(task=task)
    return pending_result


def submit_dataset_transformation(
    target: pathlib.Path,
    function: generic.Callable[typing.Concatenate["xarray.Dataset", VariableParameters], T],
    function_kwargs: dict[str, typing.Any] = None,
    load_kwargs: dict[str, typing.Any] = None,
    full_load: bool = False,
    engine: str = settings.default_netcdf_engine,
) -> "PendingTaskResult[T]":
    _open_gateway()
    from post_processing.work.tasks.reading import TransformDatasetTask
    from post_processing.interfaces.work import PendingTaskResult

    task: TransformDatasetTask[T] = TransformDatasetTask(
        target=target,
        function=function,
        load_kwargs=load_kwargs,
        kwargs=function_kwargs,
        engine=engine,
        full_load=full_load,
    )

    future_result: PendingTaskResult[T] = __IO_GATEWAY.enqueue(task=task)
    return future_result


@typing.runtime_checkable
class DatasetMutator(typing.Protocol[VariableParameters]):
    def __call__(self, dataset: "xarray.Dataset", **kwargs: VariableParameters.kwargs) -> "xarray.Dataset":
        ...

def submit_dataset_operation(
    target: pathlib.Path,
    output_path: pathlib.Path,
    function: generic.Callable[typing.Concatenate["xarray.Dataset", VariableParameters], "xarray.Dataset"],
    kwargs: dict[str, typing.Any] = None,
    read_kwargs: dict[str, typing.Any] = None,
    write_kwargs: dict[str, typing.Any] = None,
    engine: str = settings.default_netcdf_engine,
) -> "PendingTaskResult[pathlib.Path]":
    _open_gateway()

    from post_processing.work.tasks.writing import OperateOnDatasetTask
    from post_processing.interfaces.work import PendingTaskResult

    task: OperateOnDatasetTask = OperateOnDatasetTask(
        target=target,
        function=function,
        output_path=output_path,
        kwargs=kwargs,
        read_arguments=read_kwargs,
        write_arguments=write_kwargs,
        engine=engine
    )

    pending_result: PendingTaskResult[pathlib.Path] = __IO_GATEWAY.enqueue(task=task)
    return pending_result

def submit_load(
    target: pathlib.Path,
    load_kwargs: dict[str, typing.Any] = None,
    full_load: bool = False,
    engine: str = settings.default_netcdf_engine,
    **kwargs
) -> "PendingTaskResult[xarray.Dataset]":
    """
    Submit a task to the IO Gateway that will return a reference to an xarray Dataset

    :param target: Where the data to load lives
    :param load_kwargs: Keyword arguments to feed to the netcdf load function
    :param full_load: Whether to load the data fully into memory and disconnect it from the source file
    :param engine: Which engine to use to interpret the netcdf data
    """
    _open_gateway()
    if load_kwargs is None:
        load_kwargs = {}

    load_kwargs.update(kwargs)

    from post_processing.work.tasks.reading import LoadTask
    from post_processing.interfaces.work import PendingTaskResult

    task: LoadTask = LoadTask(
        target=target,
        kwargs=load_kwargs,
        full_load=full_load,
        engine=engine
    )

    future_result: PendingTaskResult["xarray.Dataset"] = __IO_GATEWAY.enqueue(task=task)

    return future_result

def submit_load_variable(
    target: pathlib.Path,
    variable_name: str,
    load_arguments: dict[str, typing.Any] = None,
    full_load: bool = False,
    engine: str = settings.default_netcdf_engine,
) -> "PendingTaskResult[xarray.DataArray]":
    from post_processing.work.tasks.reading import LoadVariableTask
    from post_processing.interfaces.work import PendingTaskResult

    task: LoadVariableTask = LoadVariableTask(
        target=target,
        variable_name=variable_name,
        kwargs=load_arguments,
        full_load=full_load,
        engine=engine
    )

    future_result: PendingTaskResult["xarray.DataArray"] = __IO_GATEWAY.enqueue(task=task)
    return future_result

@timed_function()
def write(
    dataset: "xarray.Dataset",
    target: pathlib.Path,
    write_arguments: dict[str, typing.Any] = None,
    *,
    compute: bool = True,
    **kwargs
) -> pathlib.Path:
    """
    Submit a request to save a netcdf dataset to disk and wait until completion

    :param dataset: The data to write to disk
    :param target: Where to write the data
    :param write_arguments: Keyword arguments to feed to the netcdf writer
    :param compute: Whether to compute writes because the data is a dask dataset with unevaluated expressions
    :returns: The path to where data was written
    """
    from post_processing.utilities.common import cycle_future
    from post_processing.interfaces.work import PendingTaskResult
    future_result: PendingTaskResult[pathlib.Path] = submit_write(
        dataset=dataset,
        target=target,
        write_arguments=write_arguments,
        compute=compute,
        **kwargs
    )

    result, error = cycle_future(future_result)

    if error:
        raise error
    elif result is None:
        raise RuntimeError(f"Could not save '{target.name}'")

    return result

@timed_function()
def load(
    target: pathlib.Path | generic.Sequence[pathlib.Path],
    load_kwargs: dict[str, typing.Any] = None,
    full_load: bool = False,
    engine: str = settings.default_netcdf_engine,
    transformation: typing.Callable[["xarray.Dataset"], T] = None,
    **kwargs
) -> "xarray.Dataset" | T:
    from post_processing.utilities.common import cycle_future
    from post_processing.interfaces.work import PendingTaskResult
    future_result: PendingTaskResult["xarray.Dataset"] = submit_load(
        target=target,
        load_kwargs=load_kwargs,
        full_load=full_load,
        engine=engine,
        **kwargs
    )
    result, error = cycle_future(
        future_result,
        transform=transformation
    )

    if error is not None:
        raise error
    elif result is None:
        raise RuntimeError(f"Could not load '{target.name}'")

    return result

@timed_function()
def load_variable(
    target: pathlib.Path | generic.Sequence[pathlib.Path],
    variable_name: str,
    load_kwargs: dict[str, typing.Any] = None,
    full_load: bool = False,
    engine: str = settings.default_netcdf_engine,
    transformation: typing.Callable[["xarray.DataArray"], T] = None,
    **kwargs
) -> "xarray.DataArray" | T:
    from post_processing.utilities.common import cycle_future
    from post_processing.interfaces.work import PendingTaskResult
    future_result: PendingTaskResult["xarray.DataArray"] = submit_load_variable(
        target=target,
        variable_name=variable_name,
        load_arguments=load_kwargs,
        full_load=full_load,
        engine=engine
    )

    result, error = cycle_future(
        future_result,
        transform=transformation
    )

    if error is not None:
        raise error
    elif result is None:
        raise RuntimeError(f"Could not load '{target.name}'")

    return result

@timed_function()
def select(
    target: pathlib.Path | generic.Sequence[pathlib.Path],
    variable_name: str,
    criteria: dict[str, typing.Any],
    load_kwargs: dict[str, typing.Any] = None,
    engine: str = settings.default_netcdf_engine,
    method: str = None,
    drop: bool = False,
    transformation: typing.Callable[["xarray.DataArray"], T] = None,
    **kwargs
) -> "xarray.DataArray" | T:
    import xarray
    from post_processing.interfaces.work import PendingTaskResult
    from post_processing.utilities.common import cycle_future

    submission: PendingTaskResult[xarray.DataArray] = submit_select(
        target=target,
        variable_name=variable_name,
        criteria=criteria,
        engine=engine,
        method=method,
        drop=drop,
        load_kwargs=load_kwargs,
        **kwargs
    )

    result, error = cycle_future(
        submission,
        transform=transformation
    )

    if error is not None:
        raise error
    if result is None:
        raise RuntimeError(f"Something went awry and no data was selected from '{target}'")

    return result

@timed_function()
def load_array(
    target: pathlib.Path | generic.Sequence[pathlib.Path],
    variable_name: str,
    load_kwargs: dict[str, typing.Any] = None,
    engine: str = settings.default_netcdf_engine,
    transformation: typing.Callable[["numpy.typing.NDArray"], T] = None,
    **kwargs
) -> "numpy.typing.NDArray" | T:
    from post_processing.interfaces.work import PendingTaskResult
    from post_processing.utilities.common import cycle_future

    submission: PendingTaskResult["numpy.typing.NDArray"] = submit_load_array(
        target=target,
        variable_name=variable_name,
        load_kwargs=load_kwargs,
        engine=engine,
        **kwargs
    )

    result, error = cycle_future(
        submission,
        transform=transformation,
    )

    if error is not None:
        raise error
    if result is None:
        raise RuntimeError(f"Something went awry and no data was loaded from '{target}'")

    return result


def get_default_encoding() -> dict["numpy.dtype", dict[str, typing.Any]]:
    """
    Get default encoding values for different dtypes to enforce if one is not provided on netcdf values to save

    TODO: It might be more appropriate to add a configuration for this
    :returns: A dictionary mapping dtypes to default configuration values
    """
    global _DEFAULT_ENCODING
    if not _DEFAULT_ENCODING:
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
        _DEFAULT_ENCODING = encodings
    return _DEFAULT_ENCODING

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

def _load_metadata_from_dataset(dataset: "xarray.Dataset") -> dict[str, typing.Any]:
    metadata: dict[str, typing.Any] = {
        str(key): format_value(value)
        for key, value in dataset.attrs.items()
    }

    for coordinate_data in dataset.coords.values():
        metadata.update(_get_variable_metadata(variable=coordinate_data))

    for variable in dataset.data_vars.values():
        metadata.update(_get_variable_metadata(variable=variable))

    return metadata


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
    from post_processing.interfaces.work import PendingTaskResult

    if isinstance(path, pathlib.Path):
        path = [path]

    future_metadata_from_paths: dict[pathlib.Path, PendingTaskResult[dict[str, typing.Any]]] = {
        source_path: submit_dataset_transformation(
            target=source_path,
            function=_load_metadata_from_dataset,
            engine=engine,
        )
        for source_path in path
    }

    from post_processing.utilities.common import cycle_futures
    metadata_from_paths, errors = cycle_futures(future_metadata_from_paths)

    if errors:
        if len(errors) == 1:
            raise errors[0]
        else:
            from post_processing.utilities.common import condense_exceptions
            raise condense_exceptions(f"Could not load metadata from netcdf files", errors)

    metadata: dict[str, typing.Any] = {}
    for source_path, file_metadata in metadata_from_paths.items():
        metadata.update(file_metadata)
        metadata.update({
            f"{source_path.name}.{key}": value
            for key, value in file_metadata.items()
        })

    return metadata

def _get_variable_metadata(variable: "xarray.DataArray") -> dict[str, typing.Any]:
    """
    Get the metadata for specific netcdf variable

    :param variable: The variable to inspect
    :returns: The variable's metadata in the form of a dictionary
    """
    import numpy
    metadata: dict[str, typing.Any] = {
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

@dataclasses.dataclass
class DeconstructedVariable:
    """
    Contains the raw pieces of a netcdf variable
    """
    name: str
    dimensions: list[str]
    data: "numpy.typing.NDArray"
    attributes: dict[str, typing.Any] = dataclasses.field(default_factory=dict)
    encoding: dict[str, typing.Any] = dataclasses.field(default_factory=dict)

    def to_data_array(self) -> "xarray.DataArray":
        import xarray

        array: xarray.DataArray = xarray.DataArray(
            name=self.name,
            data=self.data,
            attrs=self.attributes,
            dims=self.dimensions,
        )

        if self.encoding is not None:
            array.encoding.update(self.encoding)

        return array

    def attach_to_dataset(self, dataset: "xarray.Dataset") -> "xarray.Dataset":
        import xarray

        arguments: dict[str, typing.Any] = {
            "name": self.name,
            "data": self.data,
            "dims": self.dimensions,
        }

        coordinates: list[xarray.DataArray] = [
            dataset[dimension_name]
            for dimension_name in self.dimensions
            if dimension_name in dataset
        ]

        if coordinates:
            arguments['coords'] = coordinates

        data_array: xarray.DataArray = xarray.DataArray(**arguments)
        dataset[self.name] = data_array
        dataset[self.name].attrs.update(self.attributes)
        dataset[self.name].encoding.update(self.encoding)
        return dataset

    @classmethod
    def from_array(cls, variable: "xarray.DataArray") -> "DeconstructedVariable":
        return cls(
            name=str(variable.name),
            dimensions=list(map(str, variable.dims)),
            data=variable.data.copy(),
            attributes=variable.attrs.copy(),
            encoding=variable.encoding.copy(),
        )

@dataclasses.dataclass
class DeconstructedDataset:
    coordinates: list[DeconstructedVariable]
    variables: list[DeconstructedVariable]
    attributes: dict[str, typing.Any] = dataclasses.field(default_factory=dict)
    encoding: dict[str, typing.Any] = dataclasses.field(default_factory=dict)

    def to_dataset(self) -> "xarray.Dataset":
        import xarray
        dataset: xarray.Dataset = xarray.Dataset(
            data_vars={
                variable.name: variable.to_data_array()
                for variable in self.variables
            },
            coords={
                coordinate.name: coordinate.to_data_array()
                for coordinate in self.coordinates
            },
            attrs=self.attributes.copy(),
        )

        if self.encoding:
            dataset.encoding.update(self.encoding)

        return dataset

    @classmethod
    def from_dataset(cls, dataset: "xarray.Dataset") -> "DeconstructedDataset":
        deconstructed_dataset: cls = cls(
            coordinates=[
                DeconstructedVariable.from_array(coordinate)
                for coordinate in dataset.coords.values()
            ],
            variables=[
                DeconstructedVariable.from_array(variable)
                for variable in dataset.data_vars.values()
            ],
            attributes=dataset.attrs.copy(),
            encoding=dataset.encoding.copy(),
        )
        return deconstructed_dataset

"""
Common functions
"""
import typing
import pathlib
import re

from datetime import datetime

import numpy
import numpy.typing
import xarray

T = typing.TypeVar("T")
"""A generic type"""

VT = typing.TypeVar("VT")
"""A value type"""

RT = typing.TypeVar("RT")
"""A generic return type"""

FunctionParameters = typing.ParamSpec("FunctionParameters")

ArgsAndKwargs = typing.Union[
    typing.Sequence[typing.Any],
    typing.Mapping[str, typing.Any],
    typing.Tuple[typing.Sequence[typing.Any], typing.Mapping[str, typing.Any]]
]
"""
Either a series of positional arguments, a dictionary of keyword arguments, 
or a tuple of the first item being positional arguments and the second being keyword arguments
"""

CYCLE_PATTERN_VARIABLE: str = "cycle"
CONFIGURATION_PATTERN_VARIABLE: str = "configuration"
OUTPUT_TYPE_PATTERN_VARIABLE: str = "output_type"
MEMBER_PATTERN_VARIABLE: str = "member"
FRAME_PATTERN_VARIABLE: str = "frame"
TMINUS_PATTERN_VARIABLE: str = "tminus"
REGION_PATTERN_VARIABLE: str = "region"

NWM_FILENAME_PATTERN: re.Pattern = re.compile(
    r"^nwm\."
    rf"t(?P<{CYCLE_PATTERN_VARIABLE}>[0-2]\d)z\."
    rf"(?P<{CONFIGURATION_PATTERN_VARIABLE}>[^.]+)\."
    rf"(?P<{OUTPUT_TYPE_PATTERN_VARIABLE}>channel_rt|land|forcing)(_(?P<{MEMBER_PATTERN_VARIABLE}>\d))?\."
    rf"(f(?P<{FRAME_PATTERN_VARIABLE}>\d+)|tm(?P<{TMINUS_PATTERN_VARIABLE}>\d+))\."
    rf"(?P<{REGION_PATTERN_VARIABLE}>[a-z]+(\.\w\wrfc)?)\."
    r"nc$"
)
"""A regular expression that matches on an NWM file name and can pull out important variables"""

def starmap(
    function: typing.Callable[[FunctionParameters], RT],
    args: typing.Iterable[ArgsAndKwargs],
    threaded: bool = False
) -> typing.Sequence[RT]:
    """
    Call the given function with each of sequence of positional arguments

    :param function: The function to call
    :param args: Each set of arguments to pass
    :param threaded: If true, process each item in its own thread
    :returns: The result of each function call
    """
    results: typing.List[RT] = []

    if not isinstance(args, typing.Iterable) or isinstance(args, (str, bytes)):
        raise TypeError(f"Arguments for starmap must be an iterable collection. Received '{args}' (type={type(args)})")

    if threaded:
        results.extend(
            starmap_threaded(function=function, args=args)
        )
    else:
        for arg in args:
            if isinstance(arg, typing.Mapping):
                result: RT = function(**arg)
            elif isinstance(arg, typing.Sequence) and len(arg) == 2 and isinstance(args[0], typing.Sequence) and isinstance(args[1], typing.Mapping):
                result: RT = function(*arg[0], **arg[1])
            elif isinstance(arg, typing.Sequence) and not isinstance(arg, str):
                result: RT = function(*arg)
            else:
                result: RT = function(arg)

            results.append(result)

    return results


def starmap_threaded(
    function: typing.Callable[[FunctionParameters], RT],
    args: typing.Iterable[ArgsAndKwargs],
    thread_count: int = None,
) -> typing.Sequence[RT]:
    """
    Call the given function with each of sequence of positional arguments

    :param function: The function to call
    :param args: Each set of arguments to pass
    :param thread_count: The maximum amount of threads to process at once
    :returns: The result of each function call
    """
    results: typing.List[RT] = []
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=thread_count) as executor:
        future_results: typing.List[concurrent.futures.Future[RT]] = []
        for arg in args:
            if isinstance(arg, typing.Mapping):
                future_result: concurrent.futures.Future[RT] = executor.submit(
                    function,
                    **arg
                )
            elif isinstance(arg, typing.Sequence) and len(arg) == 2 and isinstance(
                args[0],
                typing.Sequence
                ) and isinstance(args[1], typing.Mapping):
                future_result: concurrent.futures.Future[RT] = executor.submit(
                    function,
                    *arg[0],
                    **arg[1]
                )
            elif isinstance(arg, typing.Sequence) and len(arg) == 2 and isinstance(
                args[1],
                typing.Sequence
                ) and isinstance(args[0], typing.Mapping):
                future_result: concurrent.futures.Future[RT] = executor.submit(
                    function,
                    *arg[1],
                    **arg[0]
                )
            elif isinstance(arg, typing.Sequence) and not isinstance(arg, str):
                future_result: concurrent.futures.Future[RT] = executor.submit(
                    function,
                    *arg
                )
            else:

                future_result: concurrent.futures.Future[RT] = executor.submit(
                    function,
                    arg
                )
            future_results.append(future_result)

        exceptions: typing.List[Exception] = []

        while future_results:
            future_result: concurrent.futures.Future[RT] = future_results.pop()
            try:
                result: RT = future_result.result(timeout=1)
                results.append(result)
            except concurrent.futures.TimeoutError:
                future_results.append(future_result)
            except Exception as e:
                exceptions.append(e)

        if exceptions:
            raise ExceptionGroup(f"Could not perform {function} across {len(args)} set of arguments", exceptions)

    return results


def partition(
    condition: typing.Callable[[T], bool],
    collection: typing.Iterable[T]
) -> typing.Tuple[typing.Sequence[T], typing.Sequence[T]]:
    """
    Split the collection into a collection that follows the condition and a collection that doesn't

    :param condition: A function telling if an encountered value was acceptable
    :param collection: The collection to split
    :returns: The collection that follows the condition and the collection of values that don't
    """
    passing: typing.List[T] = []
    failing: typing.List[T] = []

    for item in collection:
        if condition(item):
            passing.append(item)
        else:
            failing.append(item)

    return passing, failing


def first(
    collection: typing.Union[typing.Mapping[T, VT], typing.Iterable[T]],
    condition: typing.Union[typing.Callable[[T], bool], typing.Callable[[T, VT], bool]] = None
) -> typing.Optional[typing.Union[T, VT]]:
    """
    Return the first element of the given collection that matches the given condition.
    Returns only the first item if there is no condition

    :param condition: The function to that determines if the item encountered is the one we want
    :param collection: The collection to check
    :returns: The first element of the given collection that matches the given condition or None
    """
    if callable(condition) and isinstance(collection, typing.Mapping):
        collection: typing.Iterator[VT] = (
            value
            for key, value in collection.items()
            if condition(key, value)
        )
    elif isinstance(collection, typing.Mapping):
        collection = iter(collection.values())
    elif callable(condition):
        collection: typing.Iterator[T] = filter(condition, collection)
    elif not isinstance(collection, typing.Iterator):
        collection: typing.Iterator[T] = iter(collection)
    return next(collection, None)

def last(
    collection: typing.Union[typing.Mapping[T, VT], typing.Iterable[T]],
    condition: typing.Union[typing.Callable[[T], bool], typing.Callable[[T, VT], bool]] = None
) -> typing.Optional[typing.Union[T, VT]]:
    """
    Finds the last item in the collection that matches the given condition.
    Returns just the last item if there is no condition

    :param collection: The collection to check
    :param condition: The function to that determines if the item encountered is the one we want
    :returns: The last value that matches the given condition or None
    """
    if callable(condition) and isinstance(collection, typing.Mapping):
        collection: typing.Sequence[VT] = [
            value
            for key, value in collection.items()
            if condition(key, value)
        ]
    elif isinstance(collection, typing.Mapping):
        collection = list(collection.values())
    elif callable(condition):
        collection: typing.Sequence[T] = [value for value in collection if condition(value)]

    return collection[-1] if collection else None

def flatten_iterable(
    iterable: typing.Iterable[typing.Iterable[T]],
    condition: typing.Callable[[T], bool] = None
) -> typing.Sequence[T]:
    """
    Flatten a collections of collections into a single list.

    This will reduce the dimension of a collection by 1 - if you have a list of lists, you will get one list.
    If you have a list of lists of lists, you will get a list of lists.

    :param iterable: A collection of collections to flatten
    :param condition: A function that may be used to test for inclusion - if the input returns True, it will end up in the final collection
    :returns: The collection flattened by 1 dimension
    """
    if condition is None:
        condition = lambda item: True

    flattened_collections: typing.Set[T] = set()

    for collection in iterable:
        flattened_collections.update(filter(condition, collection))

    return list(flattened_collections)


def get_template_variables(template: str) -> typing.Sequence[str]:
    """
    Get all keyed template variables from a formatting string

    Example:
        >>> get_template_variables("It will cost ${price:.2f} to purchase a(n) {object}")
        ["price", "object"]

    :param template: The template string to get variables from
    :returns: A list of variable names
    """
    template_pattern: re.Pattern = re.compile(r"\{(?P<name>[a-zA-Z_]\w*)(:[^}]*)?}")

    matches: typing.Iterable[typing.Iterable[str]] = [
        match.groupdict().values()
        for match in template_pattern.finditer(template)
    ]

    variables: typing.Sequence[str] = flatten_iterable(iterable=matches)
    return variables

def get_cycle_files(filepath: pathlib.Path, expected_count: int = None) -> typing.Sequence[pathlib.Path]:
    """
    Get all files that match the patten of the given file path for a single cycle

    :param filepath: The path to a file that's a member of the cycle
    :param expected_count: The expected number of files to return. Raises an exception if the number is not correct
    :returns: A list of all files for this cycle
    """
    if not filepath.is_file():
        raise FileNotFoundError(f"{filepath} is not a file")

    regex_result: typing.Optional[re.Match] = NWM_FILENAME_PATTERN.match(filepath.name)

    if regex_result is None:
        raise FileNotFoundError(f"{filepath} is not a valid NWM file")

    extracted_values: typing.Dict[str, str] = regex_result.groupdict()

    raw_pattern_for_this_cycle: str = f"^nwm\.t{extracted_values['cycle']}z\.{extracted_values['configuration']}\."
    raw_pattern_for_this_cycle += f"{extracted_values['output_type']}"

    if extracted_values['member']:
        raw_pattern_for_this_cycle += f"_{extracted_values['member']}"

    raw_pattern_for_this_cycle += "\.(f|tm)\d+\."
    raw_pattern_for_this_cycle += f"{extracted_values['region']}\."
    raw_pattern_for_this_cycle += "nc$"

    pattern_for_this_cycle: re.Pattern = re.compile(raw_pattern_for_this_cycle)

    cycle_files: typing.List[pathlib.Path] = [
        path
        for path in filepath.parent.iterdir()
        if path.is_file()
           and pattern_for_this_cycle.match(path.name)
    ]

    if expected_count is not None and len(cycle_files) != expected_count:
        raise Exception(
            f"The expected number of files for the cycle that {filepath} was not as expected - "
            f"received '{len(cycle_files)}' files but expected {expected_count}"
        )

    return cycle_files

def datetime64_to_datetime(numpy_date: numpy.datetime64) -> datetime:
    """
    Convert a numpy datetime to the vanilla python datetime object

    :param numpy_date: The numpy datetime to convert
    :returns: The vanilla python datetime object
    """
    if not isinstance(numpy_date, numpy.datetime64):
        raise TypeError(f"{numpy_date} is not a numpy datetime64")

    # Convert the resolution to seconds if it isn't already -
    # otherwise converting it to a datetime instead converts it to a timestamp integer, not a python datetime
    resolution: str = get_datetime64_resolution(numpy_date=numpy_date)

    if resolution != 's':
        numpy_date = numpy_date.astype('datetime64[s]')

    python_date: datetime = numpy_date.astype(datetime)
    return python_date


def get_datetime64_resolution(numpy_date: numpy.datetime64) -> str:
    """
    Get the time resolution of a datetime64

    :param numpy_date: The numpy datetime64 to interpret
    :returns: The time resolution, either "s" for seconds, "us" for microseconds, "ms" for milliseconds, "ns" for nanoseconds
    """
    if not isinstance(numpy_date, numpy.datetime64):
        raise TypeError(f"{numpy_date} is not a numpy datetime64")

    # Get the dtype name - it'll either be:
    # - 'datetime64[us]'
    # - 'datetime64[ns]'
    # - 'datetime64[ms]'
    # - 'datetime64[s]'
    dtype_name: str = numpy_date.dtype.name
    resolution: str = dtype_name.replace("datetime64[", "").replace("]", "")

    return resolution


def get_time_from_nwm_file(path: pathlib.Path, variable_name: str = 'time') -> typing.Tuple[pathlib.Path, numpy.datetime64]:
    """

    """
    dataset: xarray.Dataset = xarray.open_dataset(path)
    if variable_name not in dataset.variables:
        raise KeyError(f"{variable_name} is not a valid variable name within {path}")

    time_values: numpy.typing.NDArray[1, numpy.datetime64] = dataset[variable_name].values

    if len(time_values) != 1:
        raise ValueError(
            f"Cannot get the time from an NWM file - "
            f"it must only have a single time value but instead has {len(time_values)}"
        )
    return path, time_values[0]

def sort_nwm_filepaths(filepaths: typing.Sequence[pathlib.Path]) -> typing.Sequence[pathlib.Path]:
    """
    Sort all nwm files by their time variable

    :param filepaths: The list of filepaths to sort
    :returns: A list of sorted filepaths
    """
    import concurrent.futures

    with concurrent.futures.ProcessPoolExecutor() as executor:
        paths_and_times: typing.List[typing.Tuple[pathlib.Path, numpy.datetime64]] = list(
                executor.map(
                get_time_from_nwm_file,
                filepaths
            )
        )
    sorted_paths_and_times: typing.List[typing.Tuple[pathlib.Path, numpy.datetime64]] = sorted(
        paths_and_times,
        key=lambda pair: pair[1]
    )
    sorted_paths: typing.List[pathlib.Path] = [path for path, time in sorted_paths_and_times]
    return sorted_paths

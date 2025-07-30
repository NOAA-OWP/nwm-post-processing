"""
Common functions
"""
import logging
import os
import typing
import pathlib
import re
import dataclasses
import json

from datetime import datetime

LOGGER: logging.Logger = logging.getLogger(pathlib.Path(__file__).stem)

if typing.TYPE_CHECKING:
    import numpy
    import numpy.typing
    import xarray
    from concurrent.futures import Future

T = typing.TypeVar("T")
"""A generic type"""

KT = typing.TypeVar("KT")
"""A generic Key type"""

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
RFC_PATTERN_VARIABLE: str = "rfc"

NWM_FILENAME_PATTERN: re.Pattern = re.compile(
    r"nwm\."
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
    thread_count: int = 0
) -> typing.Sequence[RT]:
    """
    Call the given function with each of sequence of positional arguments

    :param function: The function to call
    :param args: Each set of arguments to pass
    :param thread_count: If true, process each item in its own thread
    :returns: The result of each function call
    """
    results: typing.List[RT] = []

    from post_processing.configuration import settings
    from post_processing.enums import Verbosity

    if not isinstance(args, typing.Iterable) or isinstance(args, (str, bytes)):
        raise TypeError(f"Arguments for starmap must be an iterable collection. Received '{args}' (type={type(args)})")

    if settings.allow_threading and thread_count is not None and thread_count > 0:
        results.extend(
            starmap_threaded(function=function, args=args, thread_count=thread_count)
        )
    else:
        for argument_index, arg in enumerate(args):
            if settings.verbosity >= Verbosity.ALL:
                LOGGER.debug(f"Running through iteration {argument_index + 1} of {function}")
            if isinstance(arg, typing.Mapping):
                result: RT = function(**arg)
            elif isinstance(arg, typing.Sequence) and len(arg) == 2 and isinstance(args[0], typing.Sequence) and isinstance(args[1], typing.Mapping):
                result: RT = function(*arg[0], **arg[1])
            elif isinstance(arg, typing.Sequence) and not isinstance(arg, str):
                result: RT = function(*arg)
            else:
                result: RT = function(arg)
            if settings.verbosity >= Verbosity.ALL:
                LOGGER.debug(f"Completed iteration {argument_index + 1} of {function}")
            results.append(result)

    return results


def expand_path(path: typing.Union[str, pathlib.Path], strict: bool = True) -> typing.Sequence[pathlib.Path]:
    """
    Expand the given path to ensure that it catches everything if it contains a glob

    :param path: The path to expand
    :param strict: Whether to only return paths that are files
    :returns: All paths that the given path refers to
    """
    if isinstance(path, str):
        path = pathlib.Path(path)

    if path.is_file():
        return [path]

    glob_index: int = next(
        (
            part_index
            for part_index, part in enumerate(path.parts)
            if '*' in part
                or '?' in part
                or '[' in part
        ), -1
    )

    if glob_index < 0:
        return [path] if path.is_file() or not strict else []

    path_prefix: pathlib.Path = pathlib.Path(*path.parts[:glob_index])
    glob: str = str(pathlib.Path(*path.parts[glob_index:]))
    matching_paths: typing.List[pathlib.Path] = [
        found_path
        for found_path in path_prefix.glob(glob)
        if found_path.is_file()
            or not strict
    ]
    return matching_paths


def expand_paths(
    paths: typing.Iterable[typing.Union[str, pathlib.Path]],
    base_path: pathlib.Path = None,
    strict: bool = True
) -> typing.List[pathlib.Path]:
    """
    Expand a series of paths into more paths if given paths contain glob strings

    Example:
        >>> example_paths: typing.Sequence[typing.Union[pathlib.Path]] = [
        ...     "resources/*/*.dbf",
        ...      pathlib.Path("non-existent.log"),
        ...      pathlib.Path("/path/to/app/resources/*/nwm.*/*.nc")
        ... ]
        >>> expand_paths(paths=example_paths)
        [
            pathlib.Path('/path/to/app/resources/example/conus.dbf'),
            pathlib.Path('/path/to/app/resources/other/conus.dbf'),
            pathlib.Path('/path/to/app/resources/other/hawaii.test.28.dbf'),
            pathlib.Path('/path/to/app/resources/nwm/nwm.20250405/nwm.t00z.short_range.conus.f001.nc'),
            pathlib.Path('/path/to/app/resources/nwm/nwm.20250405/nwm.t00z.short_range.conus.f002.nc'),
            pathlib.Path('/path/to/app/resources/nwm/nwm.20250405/nwm.t00z.short_range.conus.f003.nc'),
            pathlib.Path('/path/to/app/resources/nwm/nwm.20250405/nwm.t00z.short_range.conus.f004.nc'),
            pathlib.Path('/path/to/app/resources/para/nwm.20250423/nwm.t00z.short_range.conus.f001.nc'),
            pathlib.Path('/path/to/app/resources/para/nwm.20250501/nwm.t00z.short_range.conus.f002.nc'),
            pathlib.Path('/path/to/app/resources/para/nwm.20250602/nwm.t00z.short_range.conus.f003.nc'),
            pathlib.Path('/path/to/app/resources/para/nwm.20250603/nwm.t00z.short_range.conus.f004.nc'),
        ]

    :param paths: A list of paths to expand
    :param base_path: The path to search as the root if given paths are not absolute
    :param strict: Whether to only bring back paths if it is confirmed that they are files
    :returns: All globbed paths
    """
    from post_processing.configuration import settings

    if base_path is None:
        base_path = pathlib.Path.cwd()

    template_variables: typing.Dict[str, str] = {key: str(value) for key, value in settings.to_dict().items()}

    try:
        templated_paths: typing.List[pathlib.Path] = list(map(
            lambda given_path: pathlib.Path(str(given_path).format(**template_variables)),
            paths
        ))

        expanded_paths: typing.List[pathlib.Path] = []

        for path in templated_paths:
            if not path.is_absolute():
                path = base_path / path
            found_paths: typing.Sequence[pathlib.Path] = expand_path(path=path, strict=strict)
            expanded_paths.extend(found_paths)
    except Exception as e:
        LOGGER.error(
            f"Could not find paths matching the following specifications:{os.linesep}"
            f"    - {(os.linesep + '     - ').join(map(str, paths))}{os.linesep}"
            f"The base path was: {base_path}{os.linesep}"
            f"The available additional paths used for replacement were:{os.linesep}"
            f"    - {(os.linesep + '    - ').join([str(key) + ': ' + str(value) for key, value in template_variables.items()])}"
        )
        raise e

    return expanded_paths


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
    from post_processing.configuration import settings
    from post_processing.enums import Verbosity

    if not settings.allow_threading:
        raise RuntimeError(f"Cannot thread function - threading is disabled.")

    if thread_count is None:
        thread_count = settings.maximum_additional_threads

    results: typing.List[RT] = []
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=thread_count) as executor:
        future_results: typing.List[concurrent.futures.Future[RT]] = []
        for arg in args:
            arguments_are_keyword: bool = isinstance(arg, typing.Mapping)
            arguments_are_positional: bool = isinstance(arg, typing.Sequence) and not isinstance(arg, str)
            arguments_are_positional_and_keyword: bool = (
                isinstance(arg, typing.Sequence)
                    and len(arg) == 2
                    and isinstance(arg[0], typing.Sequence) and not isinstance(arg[0], str)
                    and isinstance(arg[1], typing.Mapping)
            )
            if arguments_are_keyword:
                future_result: concurrent.futures.Future[RT] = executor.submit(
                    function,
                    **arg
                )
            elif arguments_are_positional_and_keyword:
                future_result: concurrent.futures.Future[RT] = executor.submit(
                    function,
                    *arg[0],
                    **arg[1]
                )
            elif arguments_are_positional:
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

        if settings.verbosity >= Verbosity.ALL:
            LOGGER.debug(f"{len(future_results)} jobs have been scheduled for {function}")

        results, exceptions = cycle_futures(futures=future_results)

        if exceptions:
            raise condense_exceptions(
                f"Could not perform {function.__name__} across {len(args)} sets of arguments",
                exceptions
            )

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


@typing.overload
def cycle_future_list(
    values: typing.List["Future[T]"],
    *,
    block_seconds: float = 1.0,
    backoff_seconds: float = 1.0,
    exception_handler: typing.Callable[[Exception], Exception] = None,
) -> typing.Tuple[typing.Union[typing.Sequence[T], typing.Sequence[VT]], typing.Sequence[Exception]]:
    ...

@typing.overload
def cycle_future_list(
    futures: typing.Sequence["Future[T]"],
    *,
    transform: typing.Callable[[T, typing.Sequence[T]], VT],
    block_seconds: float = 1.0,
    backoff_seconds: float = 1.0,
    exception_handler: typing.Callable[[Exception], Exception] = None,
) -> typing.Tuple[typing.Union[typing.Sequence[T], typing.Sequence[VT]], typing.Sequence[Exception]]:
    ...


def cycle_future_list(
    futures: typing.Iterable["Future[T]"],
    *,
    transform: typing.Callable[[T, typing.Sequence[T]], VT] = None,
    block_seconds: float = 1.0,
    backoff_seconds: float = 1.0,
    exception_handler: typing.Callable[[Exception], Exception] = None,
) -> typing.Tuple[typing.Union[typing.Sequence[T], typing.Sequence[VT]], typing.Sequence[Exception]]:
    """
    Cycle through the list of values and apply and transforms as the contents are generated

    :param futures: The list of values to cycle through
    :param transform: The function to apply to each value
    :param block_seconds: The number of seconds to wait for a result
    :param backoff_seconds: The number of seconds to wait after timing out while waiting for a result that just timed out
    :param exception_handler: Special handling for exceptions
    :returns: The results from all the futures
    """
    from concurrent.futures import Future
    import time

    if transform is None:
        transform = lambda x, _: x
    elif not callable(transform):
        raise TypeError("transform must be callable")

    if exception_handler is None:
        exception_handler = lambda exc: exc
    elif not callable(exception_handler):
        raise ValueError(f"{exception_handler} (type={type(exception_handler)}) is not callable")

    current_values: typing.List[Future[T]] = list(futures)

    results: typing.List[VT] = []
    last_item_id: typing.Optional[int] = None
    exceptions: typing.List[Exception] = []

    while current_values:
        value: Future[T] = current_values.pop(0)

        try:
            result: T = value.result(timeout=block_seconds)
            transformed_result: VT = transform(result, results)
            results.append(transformed_result)
        except TimeoutError:
            current_values.append(value)
            future_id: int = id(value)
            if future_id == last_item_id:
                time.sleep(backoff_seconds)
            last_item_id = future_id
        except Exception as e:
            processed_exception: Exception = exception_handler(e)
            exceptions.append(processed_exception)

    return results, exceptions

@typing.overload
def cycle_future_mapping(
    futures: typing.Mapping[KT, "Future[T]"],
    *,
    block_seconds: float = 1.0,
    backoff_seconds: int = 1.0,
    exception_handler: typing.Callable[[Exception], Exception] = None,
) -> typing.Tuple[typing.Union[typing.Sequence[T], typing.Sequence[VT]], typing.Sequence[Exception]]:
    ...

@typing.overload
def cycle_future_mapping(
    futures: typing.Mapping[KT, "Future[T]"],
    *,
    transform: typing.Callable[[KT, T, typing.Sequence[T]], VT],
    block_seconds: float = 1.0,
    backoff_seconds: int = 1.0,
    exception_handler: typing.Callable[[Exception], Exception] = None,
) -> typing.Tuple[typing.Union[typing.Sequence[T], typing.Sequence[VT]], typing.Sequence[Exception]]:
    ...

def cycle_future_mapping(
    futures: typing.Mapping[KT, "Future[T]"],
    *,
    transform: typing.Callable[[KT, T, typing.Sequence[T]], VT] = None,
    block_seconds: float = 1.0,
    backoff_seconds: float = 1.0,
    exception_handler: typing.Callable[[Exception], Exception] = None,
) -> typing.Tuple[typing.Union[typing.Sequence[T], typing.Sequence[VT]], typing.Sequence[Exception]]:
    """
    Cycle through the list of values and apply and transforms as the contents are generated

    :param futures: The list of values to cycle through
    :param transform: The function to apply to each value
    :param block_seconds: The number of seconds to wait for a result
    :param backoff_seconds: The number of seconds to wait after timing out while waiting for a result that just timed out
    :param exception_handler: Special handling for exceptions
    :returns: The results from all the futures
    """
    from concurrent.futures import Future
    import time

    if transform is None:
        transform = lambda _, future_result, __: future_result
    elif not callable(transform):
        raise TypeError("transform must be callable")

    if exception_handler is None:
        exception_handler = lambda exc: exc
    elif not callable(exception_handler):
        raise ValueError(f"{exception_handler} (type={type(exception_handler)}) is not callable")

    current_values: typing.Dict[KT, Future[T]] = dict(**futures)

    results: typing.List[VT] = []
    last_item_id: typing.Optional[int] = None
    exceptions: typing.List[Exception] = []

    while current_values:
        key, future = current_values.popitem()

        try:
            result: T = future.result(timeout=block_seconds)
            transformed_result: VT = transform(key, result, results)
            results.append(transformed_result)
        except TimeoutError:
            current_values[key] = future
            future_id: int = id(key)
            if future_id == last_item_id:
                time.sleep(backoff_seconds)
            last_item_id = future_id
        except Exception as e:
            processed_exception: Exception = exception_handler(e)
            exceptions.append(processed_exception)

    return results, exceptions


@typing.overload
def cycle_futures(
    futures: typing.Sequence["Future[T]"],
    *,
    block_seconds: float = 1.0,
    backoff_seconds: float = 1.0
) -> typing.Tuple[typing.Sequence[T], typing.Sequence[Exception]]:
    ...

@typing.overload
def cycle_futures(
    futures: typing.Sequence["Future[T]"],
    *,
    transform: typing.Callable[[T, typing.Sequence[T]], VT],
    block_seconds: float = 1.0,
    backoff_seconds: float = 1.0
) -> typing.Tuple[typing.Sequence[VT], typing.Sequence[Exception]]:
    ...

@typing.overload
def cycle_futures(
    futures: typing.Mapping[KT, "Future[T]"],
    *,
    block_seconds: float = 1.0,
    backoff_seconds: int = 1.0,
    exception_handler: typing.Callable[[Exception], Exception] = None,
) -> typing.Tuple[typing.Sequence[T], typing.Sequence[Exception]]:
    ...

@typing.overload
def cycle_futures(
    futures: typing.Mapping[KT, "Future[T]"],
    *,
    transform: typing.Callable[[KT, T, typing.Sequence[T]], VT],
    block_seconds: float = 1.0,
    backoff_seconds: int = 1.0,
    exception_handler: typing.Callable[[Exception], Exception] = None,
) -> typing.Tuple[typing.Sequence[VT], typing.Sequence[Exception]]:
    ...


def cycle_futures(
    futures: typing.Union[typing.Mapping[KT, "Future[T]"], typing.Sequence["Future[T]"]],
    *,
    transform: typing.Union[typing.Callable[[KT, T, typing.Sequence[T]], VT], typing.Callable[[T, typing.Sequence[T]], VT]] = None,
    block_seconds: float = 1.0,
    backoff_seconds: float = 1.0,
    exception_handler: typing.Callable[[Exception], Exception] = None,
) -> typing.Tuple[typing.Union[typing.Sequence[VT], typing.Sequence[T]], typing.Sequence[Exception]]:
    """
    Step through a collection of futures, trying to process and act on them as soon as possible rather than
    waiting for each to finish

    Similar to 'as_completed' but offers extra flexibility for error handling and processing

    :param futures: The collection of futures
    :param transform: An optional function to process results as they come in
    :param block_seconds: How many seconds to wait for a future's result before timing out
    :param backoff_seconds: How many seconds to wait for a future's result when before querying it again
    :param exception_handler: An optional handler for any exceptions thrown
    :returns: The results from all the futures along with all encountered exceptions
    """
    cycler: typing.Callable = cycle_future_mapping if isinstance(futures, typing.Mapping) else cycle_future_list

    results, exceptions = cycler(
        futures=futures,
        transform=transform,
        block_seconds=block_seconds,
        backoff_seconds=backoff_seconds,
        exception_handler=exception_handler,
    )

    assert isinstance(results, typing.Sequence)
    assert isinstance(exceptions, typing.Sequence)
    return results, exceptions


def get_property_values(obj: object) -> typing.Dict[str, typing.Any]:
    """
    Get the values of properties on an object

    :param obj: The object to get properties from
    :returns: The values of properties on an object mapped to their keys
    """
    if isinstance(obj, type):
        raise TypeError(f"Cannot get property values from {obj} - it is a type and an instance of the type is required")

    if obj is None:
        raise ValueError("Cannot get property values from 'None'. Pass a non-null object")

    import inspect
    properties: typing.Dict[str, typing.Any] = {
        name: prop.fget(obj)
        for name, prop in inspect.getmembers(obj.__class__, lambda member: isinstance(member, property))
        if not name.startswith("_")
    }
    return properties


def flatten_iterable(
    iterable: typing.Iterable[typing.Iterable[T]],
    condition: typing.Callable[[T], bool] = None,
    return_unique: bool = False,
) -> typing.Sequence[T]:
    """
    Flatten a collections of collections into a single list.

    This will reduce the dimension of a collection by 1 - if you have a list of lists, you will get one list.
    If you have a list of lists of lists, you will get a list of lists.

    :param iterable: A collection of collections to flatten
    :param condition: A function that may be used to test for inclusion - if the input returns True, it will end up in the final collection
    :param return_unique: Only return unique values
    :returns: The collection flattened by 1 dimension
    """
    if condition is None:
        condition = lambda item: True

    if return_unique:
        flattened_collections: typing.Set[T] = set()

        for collection in iterable:
            flattened_collections.update(filter(condition, collection))

        return list(flattened_collections)
    return [value for inner_collection in iterable for value in inner_collection]


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

def format_identifier_to_title(raw_string: str) -> str:
    """
    Converts strings like 'myExampleTest', 'This_has_a_number1234',
    or 'ThisHasAnAbbreviationOWP' into 'My Example Test',
    'This Has A Number 1234', and 'This Has An Abbreviation OWP' respectively.

    :param raw_string: The input string to format
    :return: A cleaned-up, human-readable title-cased string
    """
    # Step 1: Replace underscores with spaces
    cleaned = raw_string.replace("_", " ")

    # Step 2: Insert space between lowercase and uppercase transitions (e.g., "myExample" -> "my Example")
    cleaned = re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', cleaned)

    # Step 3: Insert space between letter and digit (e.g., "number123" -> "number 123")
    cleaned = re.sub(r'(?<=[a-zA-Z])(?=\d)', ' ', cleaned)

    # Step 4: Insert space between digit and letter (e.g., "123abc" -> "123 abc")
    cleaned = re.sub(r'(?<=\d)(?=[a-zA-Z])', ' ', cleaned)

    return cleaned


def datetime64_to_datetime(numpy_date: "numpy.datetime64") -> datetime:
    """
    Convert a numpy datetime to the vanilla python datetime object

    :param numpy_date: The numpy datetime to convert
    :returns: The vanilla python datetime object
    """
    import numpy
    if not isinstance(numpy_date, numpy.datetime64):
        raise TypeError(f"{numpy_date} is not a numpy datetime64")

    # Convert the resolution to seconds if it isn't already -
    # otherwise converting it to a datetime instead converts it to a timestamp integer, not a python datetime
    resolution: str = get_datetime64_resolution(numpy_date=numpy_date)

    if resolution != 's':
        numpy_date = numpy_date.astype('datetime64[s]')

    python_date: datetime = numpy_date.astype(datetime)
    return python_date


def get_datetime64_resolution(numpy_date: "numpy.datetime64") -> str:
    """
    Get the time resolution of a datetime64

    :param numpy_date: The numpy datetime64 to interpret
    :returns: The time resolution, either "s" for seconds, "us" for microseconds, "ms" for milliseconds, "ns" for nanoseconds
    """
    import numpy
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


def get_time_from_nwm_file(path: pathlib.Path, variable_name: str = 'time') -> typing.Tuple[pathlib.Path, "numpy.datetime64"]:
    """

    """
    import xarray
    import numpy
    import numpy.typing
    from post_processing.utilities.netcdf import load_netcdf
    dataset: xarray.Dataset = load_netcdf(path)
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
    import numpy

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

def program_exists(program_name: str) -> bool:
    """
    Determines if the CLI application exists

    :param program_name: The program name
    :returns: True if the program exists and can be called
    """
    try:
        import subprocess
        import os
        run_result = subprocess.run(f"which {program_name}", shell=True, capture_output=True, text=True)
        if run_result.stderr:
            logging.error(f"When looking for {program_name}:{os.linesep}{run_result.stderr}")
        return run_result.returncode == 0
    except Exception as exception:
        logging.error(f"Could not check if {program_name} exists: {exception}")
        return False

def condense_exceptions(
    message: str,
    exceptions: typing.Iterable[Exception],
    *additional_exceptions: Exception
) -> typing.Union[Exception, ExceptionGroup]:
    """
    Condenses multiple exceptions into a single exception group containing unique errors or just a single error if it becomes one
    """
    import traceback
    all_exceptions: typing.List[Exception] = [*exceptions, *additional_exceptions]

    if len(all_exceptions) == 0:
        exception: Exception = all_exceptions[0]
        exception.args = (f"{message}: {exception.args[0]}", *exception.args[1:])
        return exception

    exception_hashes: typing.Dict[int, Exception] = {}

    for exception in all_exceptions:
        stack_hashes: typing.Tuple[int, ...] = tuple(
            hash((frame.filename, frame.line, frame.lineno)) for frame in
            traceback.extract_tb(exception.__traceback__)
        )

        exception_hash: int = hash(stack_hashes)

        exception_hashes[exception_hash] = exception

    if len(exception_hashes) == 1:
        hash_value, exception = exception_hashes.popitem()
        exception.args = (f"{message}: {exception.args[0]}", *exception.args[1:])
        return exception

    unique_exceptions: typing.List[Exception] = list(exception_hashes.values())
    return ExceptionGroup(message, unique_exceptions)


class RecursiveEncoder(json.JSONEncoder):
    """
    A custom encoder that will recurse through objects and serialize based on behavior from:

    - dataclasses
    - items with `__dict__`
    - items with `__slots__`
    - `typing.Mapping`s
    - Anything that may be iterated through
    """
    def default(self, item_to_serialize: typing.Any):
        import inspect

        if isinstance(item_to_serialize, (datetime, pathlib.Path)):
            return str(item_to_serialize)

        if dataclasses.is_dataclass(item_to_serialize):
            converted_dataclass: typing.Dict[str, typing.Any] = dataclasses.asdict(item_to_serialize)
            return self.default(converted_dataclass)

        if isinstance(item_to_serialize, typing.Mapping):
            return {
                key: self.default(value)
                for key, value in item_to_serialize.items()
            }

        if isinstance(item_to_serialize, bytes):
            item_to_serialize = item_to_serialize.decode()

        if isinstance(item_to_serialize, str):
            return item_to_serialize

        if isinstance(item_to_serialize, (typing.Iterator, typing.Iterable)):
            return [
                self.default(item)
                for item in item_to_serialize
            ]

        if hasattr(item_to_serialize, '__dict__'):
            return {
                key: self.default(value)
                for key, value in vars(item_to_serialize).items()
            }

        if hasattr(item_to_serialize, '__slots__') and len(item_to_serialize.__slots__) > 0:
            return {
                key: self.default(getattr(item_to_serialize, key))
                for key in item_to_serialize.__slots__
                if hasattr(item_to_serialize, key)
                   and not inspect.isdatadescriptor(getattr(item_to_serialize.__class__, key))
            }

        from enum import Enum
        if isinstance(item_to_serialize, Enum):
            return item_to_serialize.value

        from decimal import Decimal
        if isinstance(item_to_serialize, Decimal):
            return float(item_to_serialize)

        return item_to_serialize

def to_json(obj: object) -> str:
    """
    Convert an arbitrary object into a JSON string
    """
    return json.dumps(obj, cls=RecursiveEncoder, indent=4)

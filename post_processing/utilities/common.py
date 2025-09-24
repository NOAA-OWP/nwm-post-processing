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

import collections.abc as generic

from datetime import datetime

LOGGER: logging.Logger = logging.getLogger(pathlib.Path(__file__).stem)

if typing.TYPE_CHECKING:
    import numpy
    import numpy.typing
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
    generic.Sequence[typing.Any],
    generic.Mapping[str, typing.Any],
    tuple[generic.Sequence[typing.Any], generic.Mapping[str, typing.Any]]
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
    rf"(?P<{OUTPUT_TYPE_PATTERN_VARIABLE}>channel_rt|land|forcing|reservoir(\.full)?)(_(?P<{MEMBER_PATTERN_VARIABLE}>\d))?\."
    rf"(f(?P<{FRAME_PATTERN_VARIABLE}>\d+)|tm(?P<{TMINUS_PATTERN_VARIABLE}>\d+))\."
    rf"(?P<{REGION_PATTERN_VARIABLE}>[a-z]+(\.\w\wrfc)?)\."
    r"nc$"
)
"""A regular expression that matches on an NWM file name and can pull out important variables"""


def is_nan_safe(value: typing.Any) -> bool:
    """
    Detects if numpy.isnan(value), but handles the TypeError if it's not supported

    :param value: The value to test
    :returns: True if the value is nan
    """
    try:
        import numpy
        return numpy.isnan(value)
    except TypeError:
        return False


def starmap(
    function: generic.Callable[FunctionParameters, RT],
    args: generic.Iterable[ArgsAndKwargs],
    thread_count: int = 0
) -> generic.Sequence[RT]:
    """
    Eagerly call the given function with each of sequence of positional arguments

    :param function: The function to call
    :param args: Each set of arguments to pass
    :param thread_count: The number of threads to use if threading is enabled
    :returns: The result of each function call
    """
    results: list[RT] = []

    from post_processing.configuration import settings
    from post_processing.enums import Verbosity

    if not isinstance(args, generic.Iterable) or isinstance(args, (str, bytes)):
        raise TypeError(f"Arguments for starmap must be an iterable collection. Received '{args}' (type={type(args)})")

    if settings.allow_threading and thread_count is not None and thread_count > 0:
        results.extend(
            starmap_threaded(function=function, args=args, thread_count=thread_count)
        )
    else:
        for argument_index, arg in enumerate(args):
            if settings.verbosity >= Verbosity.ALL:
                LOGGER.debug(f"Running through iteration {argument_index + 1} of {function}")
            if isinstance(arg, generic.Mapping):
                result: RT = function(**arg)
            elif isinstance(arg, generic.Sequence) and len(arg) == 2 and isinstance(arg[0], generic.Sequence) and isinstance(arg[1], generic.Mapping):
                result: RT = function(*arg[0], **arg[1])
            elif isinstance(arg, generic.Sequence) and not isinstance(arg, str):
                result: RT = function(*arg)
            else:
                result: RT = function(arg)
            if settings.verbosity >= Verbosity.ALL:
                LOGGER.debug(f"Completed iteration {argument_index + 1} of {function}")
            results.append(result)

    return results


def expand_path(path: typing.Union[str, pathlib.Path], strict: bool = True) -> generic.Sequence[pathlib.Path]:
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

    matching_paths: list[pathlib.Path] = [
        found_path
        for found_path in path_prefix.glob(glob)
        if found_path.is_file()
            or not strict
    ]
    return matching_paths


def expand_paths(
    paths: generic.Iterable[str | pathlib.Path],
    base_path: pathlib.Path = None,
    strict: bool = True
) -> list[pathlib.Path]:
    """
    Expand a series of paths into more paths if given paths contain glob strings

    Example:
        >>> example_paths: generic.Sequence[typing.Union[pathlib.Path]] = [
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
        base_path = settings.base_path

    template_variables: dict[str, str] = {key: str(value) for key, value in settings.to_dict().items()}

    try:
        templated_paths: list[pathlib.Path] = list(map(
            lambda given_path: pathlib.Path(str(given_path).format(**template_variables)),
            paths
        ))

        templated_paths = [
            (base_path / path if not path.is_absolute() else path).resolve()
            for path in templated_paths
        ]

        expanded_paths: list[pathlib.Path] = []

        for path in templated_paths:
            found_paths: generic.Sequence[pathlib.Path] = expand_path(path=path, strict=strict)
            expanded_paths.extend(found_paths)

        if not expanded_paths:
            LOGGER.error(
                f"Could not find any files based on the paths:{os.linesep}"
                f"    - {(os.linesep + '    - ').join(map(str, templated_paths))}"
            )
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


def find_candidate_paths(
    paths: generic.Iterable[pathlib.Path],
    base_path: pathlib.Path = None
) -> generic.Sequence[pathlib.Path]:
    """
    Finds the paths to all files that seem to be like the ones on off from the paths

    Say a /path/to/directory contains:

    * some_file.txt
    * other_file.gpkg
    * serfc.nc
    * abrfc.nc
    * prvi.serfc.nc

    and I try to find "{file_path}/priv.serfc.nc", where file_path is "/path/to/directory". This will return a list containing:

    * serfc.nc
    * abrfc.nc
    * prvi.serfc.nc

    In order to give insight into why files could not be found

    :param paths: The paths to search
    :param base_path: Where to start searching for files
    :returns: A list of all paths that might have been intended by the one given
    """
    paths = list(map(pathlib.Path, paths))

    generalized_paths: list[pathlib.Path] = [
        path.parent / f"*.{path.suffix}"
        for path in paths
    ]

    possible_paths: list[pathlib.Path] = expand_paths(paths=generalized_paths, base_path=base_path)

    if not possible_paths:
        LOGGER.error(
            f"Searched within the following paths and could not find a matching path:{os.linesep}"
            f"    - {(os.linesep + '    - ').join(map(str, generalized_paths))}"
        )
    return possible_paths


def starmap_threaded(
    function: generic.Callable[[FunctionParameters], RT],
    args: generic.Iterable[ArgsAndKwargs],
    thread_count: int = None,
) -> generic.Sequence[RT]:
    """
    Eagerly call the given function with each of sequence of positional arguments within a thread pool for a
    degree of concurrency

    :param function: The function to call
    :param args: Each set of arguments to pass
    :param thread_count: The maximum amount of threads to process at once
    :returns: The result of each function call
    """
    from post_processing.configuration import settings
    from post_processing.enums import Verbosity

    if not settings.allow_threading and settings.this_is_very_verbose:
        LOGGER.warning(f"Threading is being called directly even though it is supposed to be disabled")

    if thread_count is None:
        thread_count = settings.maximum_additional_threads

    results: list[RT] = []
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=thread_count) as executor:
        future_results: list[concurrent.futures.Future[RT]] = []
        for arg in args:
            arguments_are_keyword: bool = isinstance(arg, generic.Mapping)
            arguments_are_positional: bool = isinstance(arg, generic.Sequence) and not isinstance(arg, str)
            arguments_are_positional_and_keyword: bool = (
                isinstance(arg, generic.Sequence)
                    and len(arg) == 2
                    and isinstance(arg[0], generic.Sequence) and not isinstance(arg[0], str)
                    and isinstance(arg[1], generic.Mapping)
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
    condition: generic.Callable[[T], bool],
    collection: generic.Iterable[T]
) -> tuple[generic.Sequence[T], generic.Sequence[T]]:
    """
    Split the collection into a collection that follows the condition and a collection that doesn't

    :param condition: A function telling if an encountered value was acceptable
    :param collection: The collection to split
    :returns: The collection that follows the condition and the collection of values that don't
    """
    passing: list[T] = []
    failing: list[T] = []

    for item in collection:
        if condition(item):
            passing.append(item)
        else:
            failing.append(item)

    return passing, failing


def first(
    collection: generic.Mapping[T, VT] | generic.Iterable[T],
    condition: generic.Callable[[T], bool] | generic.Callable[[T, VT], bool] = None
) -> typing.Optional[T | VT]:
    """
    Return the first element of the given collection that matches the given condition.
    Returns only the first item if there is no condition

    :param condition: The function to that determines if the item encountered is the one we want
    :param collection: The collection to check
    :returns: The first element of the given collection that matches the given condition or None
    """
    if callable(condition) and isinstance(collection, generic.Mapping):
        collection: generic.Iterator[VT] = (
            value
            for key, value in collection.items()
            if condition(key, value)
        )
    elif isinstance(collection, generic.Mapping):
        collection = iter(collection.values())
    elif callable(condition):
        collection: generic.Iterator[T] = filter(condition, collection)
    elif not isinstance(collection, generic.Iterator):
        collection: generic.Iterator[T] = iter(collection)
    return next(collection, None)

def last(
    collection: typing.Union[generic.Mapping[T, VT], generic.Iterable[T]],
    condition: typing.Union[generic.Callable[[T], bool], generic.Callable[[T, VT], bool]] = None
) -> typing.Optional[typing.Union[T, VT]]:
    """
    Finds the last item in the collection that matches the given condition.
    Returns just the last item if there is no condition

    :param collection: The collection to check
    :param condition: The function to that determines if the item encountered is the one we want
    :returns: The last value that matches the given condition or None
    """
    if callable(condition) and isinstance(collection, generic.Mapping):
        collection: generic.Sequence[VT] = [
            value
            for key, value in collection.items()
            if condition(key, value)
        ]
    elif isinstance(collection, generic.Mapping):
        collection = list(collection.values())
    elif callable(condition):
        collection: generic.Sequence[T] = [value for value in collection if condition(value)]

    return collection[-1] if collection else None


@typing.overload
def cycle_future_list(
    values: list["Future[T]"],
    *,
    block_seconds: float = 1.0,
    backoff_seconds: float = 1.0,
    exception_handler: generic.Callable[[Exception], Exception] = None,
) -> tuple[typing.Union[generic.Sequence[T], generic.Sequence[VT]], generic.Sequence[Exception]]:
    ...

@typing.overload
def cycle_future_list(
    futures: generic.Sequence["Future[T]"],
    *,
    transform: generic.Callable[[T, generic.Sequence[T]], VT],
    block_seconds: float = 1.0,
    backoff_seconds: float = 1.0,
    exception_handler: generic.Callable[[Exception], Exception] = None,
) -> tuple[typing.Union[generic.Sequence[T], generic.Sequence[VT]], generic.Sequence[Exception]]:
    ...


def cycle_future_list(
    futures: generic.Iterable["Future[T]"],
    *,
    transform: generic.Callable[[T, generic.Sequence[T]], VT] = None,
    block_seconds: float = 1.0,
    backoff_seconds: float = 1.0,
    exception_handler: generic.Callable[[Exception], Exception] = None,
) -> tuple[typing.Union[generic.Sequence[T], generic.Sequence[VT]], generic.Sequence[Exception]]:
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

    current_values: list[Future[T]] = list(futures)

    results: list[VT] = []
    last_item_id: typing.Optional[int] = None
    exceptions: list[Exception] = []

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
    futures: generic.Mapping[KT, "Future[T]"],
    *,
    block_seconds: float = 1.0,
    backoff_seconds: int = 1.0,
    exception_handler: generic.Callable[[Exception], Exception] = None,
) -> tuple[typing.Union[generic.Sequence[T], generic.Sequence[VT]], generic.Sequence[Exception]]:
    ...

@typing.overload
def cycle_future_mapping(
    futures: generic.Mapping[KT, "Future[T]"],
    *,
    transform: generic.Callable[[KT, T, generic.Sequence[T]], VT],
    block_seconds: float = 1.0,
    backoff_seconds: int = 1.0,
    exception_handler: generic.Callable[[Exception], Exception] = None,
) -> tuple[typing.Union[generic.Sequence[T], generic.Sequence[VT]], generic.Sequence[Exception]]:
    ...

def cycle_future_mapping(
    futures: generic.Mapping[KT, "Future[T]"],
    *,
    transform: generic.Callable[[KT, T, generic.Sequence[T]], VT] = None,
    block_seconds: float = 1.0,
    backoff_seconds: float = 1.0,
    exception_handler: generic.Callable[[Exception], Exception] = None,
) -> tuple[typing.Union[generic.Sequence[T], generic.Sequence[VT]], generic.Sequence[Exception]]:
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

    current_values: dict[KT, Future[T]] = dict(**futures)

    results: list[VT] = []
    last_item_id: typing.Optional[int] = None
    exceptions: list[Exception] = []

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
    futures: generic.Sequence["Future[T]"],
    *,
    block_seconds: float = 1.0,
    backoff_seconds: float = 1.0
) -> tuple[generic.Sequence[T], generic.Sequence[Exception]]:
    ...

@typing.overload
def cycle_futures(
    futures: generic.Sequence["Future[T]"],
    *,
    transform: generic.Callable[[T, generic.Sequence[T]], VT],
    block_seconds: float = 1.0,
    backoff_seconds: float = 1.0
) -> tuple[generic.Sequence[VT], generic.Sequence[Exception]]:
    ...

@typing.overload
def cycle_futures(
    futures: generic.Mapping[KT, "Future[T]"],
    *,
    block_seconds: float = 1.0,
    backoff_seconds: int = 1.0,
    exception_handler: generic.Callable[[Exception], Exception] = None,
) -> tuple[generic.Sequence[T], generic.Sequence[Exception]]:
    ...

@typing.overload
def cycle_futures(
    futures: generic.Mapping[KT, "Future[T]"],
    *,
    transform: generic.Callable[[KT, T, generic.Sequence[T]], VT],
    block_seconds: float = 1.0,
    backoff_seconds: int = 1.0,
    exception_handler: generic.Callable[[Exception], Exception] = None,
) -> tuple[generic.Sequence[VT], generic.Sequence[Exception]]:
    ...


def cycle_futures(
    futures: typing.Union[generic.Mapping[KT, "Future[T]"], generic.Sequence["Future[T]"]],
    *,
    transform: typing.Union[generic.Callable[[KT, T, generic.Sequence[T]], VT], generic.Callable[[T, generic.Sequence[T]], VT]] = None,
    block_seconds: float = 1.0,
    backoff_seconds: float = 1.0,
    exception_handler: generic.Callable[[Exception], Exception] = None,
) -> tuple[typing.Union[generic.Sequence[VT], generic.Sequence[T]], generic.Sequence[Exception]]:
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
    cycler: generic.Callable = cycle_future_mapping if isinstance(futures, generic.Mapping) else cycle_future_list

    results, exceptions = cycler(
        futures=futures,
        transform=transform,
        block_seconds=block_seconds,
        backoff_seconds=backoff_seconds,
        exception_handler=exception_handler,
    )

    assert isinstance(results, generic.Sequence)
    assert isinstance(exceptions, generic.Sequence)
    return results, exceptions


def get_property_values(obj: object) -> dict[str, typing.Any]:
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
    properties: dict[str, typing.Any] = {
        name: prop.fget(obj)
        for name, prop in inspect.getmembers(obj.__class__, lambda member: isinstance(member, property))
        if not name.startswith("_")
    }
    return properties


def flatten_iterable(
    iterable: generic.Iterable[generic.Iterable[T]],
    condition: generic.Callable[[T], bool] = None,
    return_unique: bool = False,
) -> generic.Sequence[T]:
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
        flattened_collections: set[T] = set()

        for collection in iterable:
            flattened_collections.update(filter(condition, collection))

        return list(flattened_collections)
    return [value for inner_collection in iterable for value in inner_collection]


def get_template_variables(template: str) -> generic.Sequence[str]:
    """
    Get all keyed template variables from a formatting string

    Example:
        >>> get_template_variables("It will cost ${price:.2f} to purchase a(n) {object}")
        ["price", "object"]

    :param template: The template string to get variables from
    :returns: A list of variable names
    """
    template_pattern: re.Pattern = re.compile(r"\{(?P<name>[a-zA-Z_]\w*)(:[^}]*)?}")

    matches: generic.Iterable[generic.Iterable[str]] = [
        match.groupdict().values()
        for match in template_pattern.finditer(template)
    ]

    variables: generic.Sequence[str] = flatten_iterable(iterable=matches)
    return variables

def get_cycle_files(filepath: pathlib.Path, expected_count: int = None) -> generic.Sequence[pathlib.Path]:
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

    extracted_values: dict[str, str] = regex_result.groupdict()

    raw_pattern_for_this_cycle: str = f"^nwm\.t{extracted_values['cycle']}z\.{extracted_values['configuration']}\."
    raw_pattern_for_this_cycle += f"{extracted_values['output_type']}"

    if extracted_values['member']:
        raw_pattern_for_this_cycle += f"_{extracted_values['member']}"

    raw_pattern_for_this_cycle += "\.(f|tm)\d+\."
    raw_pattern_for_this_cycle += f"{extracted_values['region']}\."
    raw_pattern_for_this_cycle += "nc$"

    pattern_for_this_cycle: re.Pattern = re.compile(raw_pattern_for_this_cycle)

    cycle_files: list[pathlib.Path] = [
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
    exceptions: generic.Iterable[Exception],
    *additional_exceptions: Exception
) -> typing.Union[Exception, ExceptionGroup]:
    """
    Condenses multiple exceptions into a single exception group containing unique errors or just a single error if it becomes one
    """
    import traceback
    all_exceptions: list[Exception] = [*exceptions, *additional_exceptions]

    if len(all_exceptions) == 0:
        exception: Exception = Exception(message)
        return exception

    exception_hashes: dict[int, Exception] = {}

    for exception in all_exceptions:
        stack_hashes: tuple[int, ...] = tuple(
            hash((frame.filename, frame.line, frame.lineno)) for frame in
            traceback.extract_tb(exception.__traceback__)
        )

        exception_hash: int = hash(stack_hashes)

        exception_hashes[exception_hash] = exception

    if len(exception_hashes) == 1:
        hash_value, exception = exception_hashes.popitem()
        exception.args = (f"{message}: {exception.args[0]}", *exception.args[1:])
        return exception

    unique_exceptions: list[Exception] = list(exception_hashes.values())
    return ExceptionGroup(message, unique_exceptions)


def is_array_like(value: object) -> bool:
    """
    Detects if something is a collection like a somewhat classical array, i.e. a series of independent values

    :param value: The object to check
    :returns: True if the value is a series of independent values
    """
    if isinstance(value, (str, bytes, generic.Mapping)):
        return False

    return isinstance(value, generic.Sequence)


def timed_function(
    logger: typing.Optional[logging.Logger] = None,
    level: typing.Optional[int] = None
) -> generic.Callable[[generic.Callable[FunctionParameters, RT]], generic.Callable[FunctionParameters, RT]]:
    """
    Logs a function timing duration if timing recording is enabled and the given log level is allowable by the logger

    :param logger: The logger to use. Defaults to the logger for the file that defines this decorator
    :param level: A custom log level to record this timing at. Defaults to the system setting 'log_level'
    :returns: A function that records the time it takes to execute this function if timing is enabled, just the function otherwise
    """
    from post_processing.configuration import settings
    _logger = logger or logging.getLogger('TIMING')
    _level = _logger.getEffectiveLevel() if level is None else level

    def decorator(func: generic.Callable[FunctionParameters, RT]) -> generic.Callable[FunctionParameters, RT]:
        """
        Decorate the function with wrapper logic

        :param func: The function to decorate
        :returns: A version of the function where timing is recorded if timing is enabled, the given function otherwise
        """
        # If timing is disabled or the given log level is below that of the level of the logger, just return the
        # function in order to remove any overhead cost
        if not settings.record_timing or not _logger.isEnabledFor(_level):
            return func

        code = func.__code__
        function_metadata: dict[str, typing.Any] = {
            "functionName": func.__name__,
            "moduleName": func.__module__,
            "path": code.co_filename,
            "lineNumber": code.co_firstlineno,
        }

        import functools
        @functools.wraps(func)
        def wrapper(*args: FunctionParameters.args, **kwargs: FunctionParameters.kwargs) -> RT:
            """
            Calls the input function with its given arguments and logs its runtime in ISO8601 duration format
            """
            import time

            start: float = time.perf_counter()
            successful: bool = False
            try:
                result: RT = func(*args, **kwargs)
                successful = True
                return result
            finally:
                seconds: float = time.perf_counter() - start

                duration_description: str = "P"
                days, seconds = divmod(seconds, 24.0 * 60.0 * 60.0)
                hours, seconds = divmod(seconds, 60.0 * 60.0)
                minutes, seconds = divmod(seconds, 60.0)

                if days:
                    duration_description += str(int(days)) + "D"

                duration_description += "T"

                if hours:
                    duration_description += str(int(hours)) + "H"

                if minutes:
                    duration_description += str(int(minutes)) + "M"

                if seconds:
                    duration_description += f"{seconds:.2f}S"

                function_description: str = f"{func.__name__}("

                if args:
                    arg_descriptions: list[str] = []

                    for arg in args:
                        is_string: bool = isinstance(arg, str)
                        value: str = str(arg)
                        if len(value) > 15:
                            value = f"(...){value[-15:]}"
                        if is_string:
                            value = f'"{value}"'
                        arg_descriptions.append(value)

                    function_description += ", ".join(arg_descriptions)

                if kwargs:
                    if args:
                        function_description += " "

                    kwarg_descriptions: list[str] = []
                    for key, value in kwargs.items():
                        is_string: bool = isinstance(value, str)
                        kwarg_value: str = str(value)
                        if len(kwarg_value) > 15:
                            kwarg_value = f"(...){kwarg_value[-15:]}"

                        if is_string:
                            kwarg_value = f'"{kwarg_value}"'
                        kwarg_descriptions.append(
                            f"{key}={kwarg_value}"
                        )

                    function_description += ", ".join(kwarg_descriptions)

                function_description += ")"
                _logger.log(
                    _level,
                    f"{function_description} | {'successful' if successful else 'failed'} | {duration_description}",
                    extra=function_metadata
                )

        return wrapper
    return decorator


class RecursiveEncoder(json.JSONEncoder):
    """
    A custom encoder that will recurse through objects and serialize based on behavior from:

    - dataclasses
    - items with `__dict__`
    - items with `__slots__`
    - `generic.Mapping`s
    - Anything that may be iterated through
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def default(self, item_to_serialize: typing.Any):
        if isinstance(item_to_serialize, (int, float, str, bool, type(None))):
            return item_to_serialize

        import numpy

        if isinstance(item_to_serialize, numpy.integer):
            return int(item_to_serialize)

        if isinstance(item_to_serialize, numpy.floating):
            return float(item_to_serialize)

        if isinstance(item_to_serialize, numpy.ndarray):
            return item_to_serialize.tolist()

        if isinstance(item_to_serialize, bytes):
            item_to_serialize = item_to_serialize.decode()

        if isinstance(item_to_serialize, (datetime, pathlib.Path)):
            return str(item_to_serialize)

        from enum import Enum
        if isinstance(item_to_serialize, Enum):
            return item_to_serialize.value

        from decimal import Decimal
        if isinstance(item_to_serialize, Decimal):
            return float(item_to_serialize)

        if isinstance(item_to_serialize, (typing.BinaryIO, typing.TextIO)):
            LOGGER.warning(f"Cannot serialize '{repr(item_to_serialize)}' (type={type(item_to_serialize)})")
            return None

        if isinstance(item_to_serialize, re.Pattern):
            return item_to_serialize.pattern

        if hasattr(item_to_serialize, "to_dict") and callable(getattr(item_to_serialize, "to_dict")):
            try:
                converted_item: dict[str, typing.Any] = item_to_serialize.to_dict()
                return self.default(converted_item)
            except:
                pass

        import inspect

        if dataclasses.is_dataclass(item_to_serialize):
            converted_dataclass: dict[str, typing.Any] = dataclasses.asdict(item_to_serialize)
            return self.default(converted_dataclass)

        if isinstance(item_to_serialize, generic.Mapping):
            return {
                str(key): self.default(value)
                for key, value in item_to_serialize.items()
                if value != item_to_serialize
            }

        if isinstance(item_to_serialize, (generic.Iterator, generic.Iterable)):
            return [
                self.default(item)
                for item in item_to_serialize
                if item != item_to_serialize
            ]

        if hasattr(item_to_serialize, '__dict__'):
            return {
                str(key): self.default(value)
                for key, value in vars(item_to_serialize).items()
                if value != item_to_serialize
            }

        if hasattr(item_to_serialize, '__slots__') and len(item_to_serialize.__slots__) > 0:
            return {
                str(key): self.default(getattr(item_to_serialize, key))
                for key in item_to_serialize.__slots__
                if hasattr(item_to_serialize, key)
                   and not inspect.isdatadescriptor(getattr(item_to_serialize.__class__, key))
                   and getattr(item_to_serialize, key) != item_to_serialize
            }

        raise TypeError(
            f"'{repr(item_to_serialize)}' (type={type(item_to_serialize)}) is not "
            f"serializable by the JSON '{self.__class__.__name__}'"
        )

def to_json(obj: object) -> str:
    """
    Convert an arbitrary object into a JSON string
    """
    return json.dumps(obj, cls=RecursiveEncoder, indent=4)

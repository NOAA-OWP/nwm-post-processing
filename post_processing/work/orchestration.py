"""
Functions and objects used to orchestrate work within single calls
"""
import concurrent.futures
import typing
import collections.abc as generic
import logging

from post_processing.utilities.logging import get_logger

LOGGER: logging.Logger = get_logger(__file__)

if typing.TYPE_CHECKING:
    from concurrent.futures import Executor
    from concurrent.futures import Future
    from post_processing.interfaces.work import PendingTaskResult

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

def shutdown_executor(executor: typing.Optional["Executor"] = None, worker_count: int | None = None):
    """
    Ensure that any passed executor is properly shutdown

    :param executor: The executor to try and shutdown. This is a noop if there is no executor
    :param worker_count: The number of workers the executor uses. Defaults to the number of CPUs as the best guess
        if none is passed. This may not be enough if the executor is oversubscribed.
    """
    if not isinstance(executor, concurrent.futures.Executor):
        LOGGER.debug(f"There was no executor to shut down")
        return

    LOGGER.debug(f"Shutting down '{executor}'")

    if worker_count is None or worker_count < 1:
        import os
        worker_count = os.cpu_count()

    from post_processing.transform.subsetting.cache import clean as remove_masks
    from post_processing.transform.reproject import clean as remove_projections
    from post_processing.utilities.netcdf import close_gateway

    # The cleanup functions don't take arguments, but the number of calls queued is based off of the number of argument
    # sequences passed. We multiply the empty sequences by the number of workers, then multiply THAT to ensure timing
    # doesn't possibly exclude any workers
    cleanup_stub_arguments: generic.Sequence[generic.Sequence] = [[]] * worker_count * 2

    #mask_removals = starmap_executor(remove_masks, args=cleanup_stub_arguments, executor=executor)
    #projection_removals = starmap_executor(remove_projections, args=cleanup_stub_arguments, executor=executor)
    close_results = starmap_executor(close_gateway, args=cleanup_stub_arguments, executor=executor)

    #LOGGER.debug(f"Removed masks '{len(mask_removals)}' times")
    #LOGGER.debug(f"Removed projections {len(projection_removals)} times")
    LOGGER.debug(f"Closed gateways '{len(close_results)}' times")

@typing.overload
def starmap_executor(
    function: generic.Callable[FunctionParameters, RT],
    args: generic.Mapping[KT, RT],
    executor: typing.Optional["Executor"],
    fallback_to_threads: bool = False,
) -> generic.Mapping[KT, RT]:
    ...

@typing.overload
def starmap_executor(
    function: generic.Callable[FunctionParameters, RT],
    args: generic.Iterable[ArgsAndKwargs],
    executor: typing.Optional["Executor"],
    fallback_to_threads: bool = False,
) -> generic.Sequence[RT]:
    ...

def starmap_executor(
    function: generic.Callable[FunctionParameters, RT],
    args: generic.Mapping[KT, ArgsAndKwargs] | generic.Iterable[ArgsAndKwargs],
    executor: typing.Optional["Executor"],
    fallback_to_threads: bool = False,
) -> generic.Mapping[KT, RT] | generic.Sequence[RT]:
    """
    Call the given function with the given arguments through the given executor and wait for the results

    :param function: The function to call
    :param args: The arguments to pass to the function
    :param executor: The executor to call the functions through
    :param fallback_to_threads: Whether to fall back to threaded parallelization if the executor is not available
    :returns: The results from each call
    """
    from post_processing.configuration import settings
    from post_processing.enums import Verbosity
    from concurrent.futures import Future

    if executor is None:
        if fallback_to_threads:
            return starmap_threaded(
                function=function,
                args=args
            )
        return starmap(
            function=function,
            args=args,
        )

    if isinstance(args, generic.Mapping):
        future_results: dict[KT, Future[RT]] = {}
    else:
        future_results: list[Future[RT]] = []

    for key, arg in (args.items() if isinstance(args, generic.Mapping) else ((None, arg) for arg in args)):
        arguments_are_keyword: bool = isinstance(arg, generic.Mapping)
        arguments_are_positional: bool = isinstance(arg, generic.Sequence) and not isinstance(arg, str)
        arguments_are_positional_and_keyword = (
            isinstance(arg, generic.Sequence)
                and len(arg) == 2
                and isinstance(arg[0], generic.Sequence) and not isinstance(arg[0], str)
                and isinstance(arg[1], generic.Mapping)
        )

        if arguments_are_positional_and_keyword:
            future_result: Future[RT] = executor.submit(
                function,
                *arg[0],
                **arg[1]
            )
        elif arguments_are_keyword:
            future_result: Future[RT] = executor.submit(
                function,
                **arg
            )
        elif arguments_are_positional:
            future_result: Future[RT] = executor.submit(
                function,
                *arg,
            )
        else:
            future_result: Future[RT] = executor.submit(
                function,
                arg
            )

        if isinstance(args, generic.Mapping):
            future_results[key] = future_result
        else:
            future_results.append(future_result)

    if settings.verbosity >= Verbosity.ALL:
        LOGGER.debug(f"{len(future_results)} jobs have been scheduled for {function}")

    results, exceptions = cycle_futures(futures=future_results)

    if exceptions:
        from post_processing.utilities.common import condense_exceptions
        raise condense_exceptions(
            f"Could not perform {function.__name__} across {len(args)} sets of arguments",
            exceptions
        )
    return results


@typing.overload
def starmap(
    function: generic.Callable[FunctionParameters, RT],
    args: generic.Mapping[KT, ArgsAndKwargs],
    thread_count: int = 0
) -> generic.Mapping[KT, RT]:
    ...

@typing.overload
def starmap(
    function: generic.Callable[FunctionParameters, RT],
    args: generic.Iterable[ArgsAndKwargs],
    thread_count: int = 0
) -> generic.Sequence[RT]:
    ...


def starmap(
    function: generic.Callable[FunctionParameters, RT],
    args: generic.Iterable[ArgsAndKwargs] | generic.Mapping[KT, ArgsAndKwargs],
    thread_count: int = 0
) -> generic.Sequence[RT] | generic.Mapping[KT, RT]:
    """
    Eagerly call the given function with each of sequence of positional arguments

    :param function: The function to call
    :param args: Each set of arguments to pass
    :param thread_count: The number of threads to use if threading is enabled
    :returns: The result of each function call
    """

    from post_processing.configuration import settings

    if not isinstance(args, generic.Iterable) or isinstance(args, (str, bytes)):
        raise TypeError(f"Arguments for starmap must be an iterable collection. Received '{args}' (type={type(args)})")

    if settings.allow_threading and thread_count is not None and thread_count > 0:
        results: generic.Mapping[KT, RT] | generic.Sequence[RT] = starmap_threaded(
            function=function,
            args=args,
            thread_count=thread_count
        )
    else:
        if isinstance(args, generic.Mapping):
            results: dict[KT, RT] = {}
        else:
            results: list[RT] = []

        for key, arg in (args.items() if isinstance(args, generic.Mapping) else ((None, arg) for arg in args)):
            if isinstance(arg, generic.Mapping):
                result: RT = function(**arg)
            elif isinstance(arg, generic.Sequence) and len(arg) == 2 and isinstance(arg[0], generic.Sequence) and isinstance(arg[1], generic.Mapping):
                result: RT = function(*arg[0], **arg[1])
            elif isinstance(arg, generic.Sequence) and not isinstance(arg, str):
                result: RT = function(*arg)
            else:
                result: RT = function(arg)

            if isinstance(results, dict):
                results[key] = result
            else:
                results.append(result)

    return results

@typing.overload
def starmap_threaded(
    function: generic.Callable[[FunctionParameters], RT],
    args: generic.Mapping[KT, ArgsAndKwargs],
    thread_count: int = None,
    *,
    thread_prefix: str = None
) -> generic.Mapping[KT, RT]:
    ...

@typing.overload
def starmap_threaded(
    function: generic.Callable[[FunctionParameters], RT],
    args: generic.Sequence[ArgsAndKwargs],
    thread_count: int = None,
    *,
    thread_prefix: str = None
) -> generic.Sequence[RT]:
    ...


def starmap_threaded(
    function: generic.Callable[[FunctionParameters], RT],
    args: generic.Iterable[ArgsAndKwargs] | generic.Mapping[KT, ArgsAndKwargs],
    thread_count: int = None,
    *,
    thread_prefix: str = None
) -> generic.Sequence[RT] | generic.Mapping[KT, RT]:
    """
    Eagerly call the given function with each of sequence of positional arguments within a thread pool for a
    degree of concurrency

    :param function: The function to call
    :param args: Each set of arguments to pass
    :param thread_count: The maximum amount of threads to process at once
    :param thread_prefix: A prefix used to identify common threads
    :returns: The result of each function call
    """
    from post_processing.configuration import settings

    if thread_prefix is None:
        import threading
        thread_prefix = f"{threading.current_thread().name}-starmap-"

    if not settings.allow_threading and settings.this_is_very_verbose:
        LOGGER.warning(f"Threading is being called directly even though it is supposed to be disabled")

    if thread_count is None:
        thread_count = settings.maximum_additional_threads
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=thread_count, thread_name_prefix=thread_prefix) as executor:
        return starmap_executor(
            function=function,
            args=args,
            executor=executor
        )


@typing.overload
def cycle_future_list(
    futures: list["PendingTaskResult[T]"],
    *,
    block_seconds: float = 1.0,
    backoff_seconds: float = 1.0,
    exception_handler: generic.Callable[[Exception], Exception] = None,
) -> tuple[typing.Union[generic.Sequence[T], generic.Sequence[VT]], generic.Sequence[Exception]]:
    ...

@typing.overload
def cycle_future_list(
    futures: generic.Sequence["PendingTaskResult[T]"],
    *,
    transform: generic.Callable[[T, generic.Sequence[T]], VT],
    block_seconds: float = 1.0,
    backoff_seconds: float = 1.0,
    exception_handler: generic.Callable[[Exception], Exception] = None,
) -> tuple[generic.Sequence[VT], generic.Sequence[Exception]]:
    ...


def cycle_future_list(
    futures: generic.Iterable["PendingTaskResult[T]"],
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
    from post_processing.interfaces.work import PendingTaskResult
    import time

    if transform is None:
        transform = lambda x, _: x
    elif not callable(transform):
        raise TypeError("transform must be callable")

    if exception_handler is None:
        exception_handler = lambda exc: exc
    elif not callable(exception_handler):
        raise ValueError(f"{exception_handler} (type={type(exception_handler)}) is not callable")

    current_values: list[PendingTaskResult[T]] = list(futures)

    results: list[VT] = []
    last_item_id: typing.Optional[int] = None
    exceptions: list[Exception] = []

    while current_values:
        value: PendingTaskResult[T] = current_values.pop(0)

        try:
            result: T = value.result(timeout=block_seconds)
            transformed_result: VT = transform(result, results)
            results.append(transformed_result)
            del value
        except (TimeoutError, concurrent.futures.TimeoutError):
            current_values.append(value)
            future_id: int = id(value)
            if future_id == last_item_id:
                time.sleep(backoff_seconds)
            last_item_id = future_id
        except Exception as e:
            processed_exception: Exception = exception_handler(e)
            exceptions.append(processed_exception)
            del value

    return results, exceptions

@typing.overload
def cycle_future_mapping(
    futures: generic.Mapping[KT, "PendingTaskResult[T]"],
    *,
    block_seconds: float = 1.0,
    backoff_seconds: int = 1.0,
    exception_handler: generic.Callable[[Exception], Exception] = None,
) -> tuple[generic.Mapping[KT, T], generic.Sequence[Exception]]:
    ...

@typing.overload
def cycle_future_mapping(
    futures: generic.Mapping[KT, "PendingTaskResult[T]"],
    *,
    transform: generic.Callable[[KT, T, generic.Sequence[T]], VT],
    block_seconds: float = 1.0,
    backoff_seconds: int = 1.0,
    exception_handler: generic.Callable[[Exception], Exception] = None,
) -> tuple[generic.Mapping[KT, VT], generic.Sequence[Exception]]:
    ...

def cycle_future_mapping(
    futures: generic.Mapping[KT, "PendingTaskResult[T]"],
    *,
    transform: generic.Callable[[KT, T, generic.Sequence[T]], VT] = None,
    block_seconds: float = 1.0,
    backoff_seconds: float = 1.0,
    exception_handler: generic.Callable[[Exception], Exception] = None,
) -> tuple[generic.Mapping[KT, T | VT], generic.Sequence[Exception]]:
    """
    Cycle through the list of values and apply and transforms as the contents are generated

    :param futures: The list of values to cycle through
    :param transform: The function to apply to each value
    :param block_seconds: The number of seconds to wait for a result
    :param backoff_seconds: The number of seconds to wait after timing out while waiting for a result that just timed out
    :param exception_handler: Special handling for exceptions
    :returns: The results from all the futures
    """
    from post_processing.interfaces.work import PendingTaskResult
    import time

    if transform is None:
        transform = lambda _, future_result, __: future_result
    elif not callable(transform):
        raise TypeError("transform must be callable")

    if exception_handler is None:
        exception_handler = lambda exc: exc
    elif not callable(exception_handler):
        raise ValueError(f"{exception_handler} (type={type(exception_handler)}) is not callable")

    current_values: list[tuple[KT, PendingTaskResult[T]]] = list(futures.items())

    results: dict[KT, VT] = {}
    last_item_id: typing.Optional[int] = None
    exceptions: list[Exception] = []


    while current_values:
        key, future = current_values.pop(0)

        try:
            result: T = future.result(timeout=block_seconds)
            transformed_result: VT = transform(key, result, results)
            results[key] = transformed_result
            del future
        except (TimeoutError, concurrent.futures.TimeoutError):
            current_values.append((key, future))
            future_id: int = id(key)
            if future_id == last_item_id:
                time.sleep(backoff_seconds)
            last_item_id = future_id
        except Exception as e:
            processed_exception: Exception = exception_handler(e)
            exceptions.append(processed_exception)
            del future

    return results, exceptions

@typing.overload
def cycle_futures(
    futures: generic.Mapping[KT, "PendingTaskResult[T]"],
    *,
    block_seconds: float = 1.0,
    backoff_seconds: int = 1.0,
    exception_handler: generic.Callable[[Exception], Exception] = None,
) -> tuple[generic.Mapping[KT, T], generic.Sequence[Exception]]:
    ...

@typing.overload
def cycle_futures(
    futures: generic.Mapping[KT, "PendingTaskResult[T]"],
    *,
    transform: generic.Callable[[KT, T, generic.Sequence[T]], VT],
    block_seconds: float = 1.0,
    backoff_seconds: int = 1.0,
    exception_handler: generic.Callable[[Exception], Exception] = None,
) -> tuple[generic.Mapping[KT, VT], generic.Sequence[Exception]]:
    ...


@typing.overload
def cycle_futures(
    futures: generic.Sequence["PendingTaskResult[T]"],
    *,
    block_seconds: float = 1.0,
    backoff_seconds: float = 1.0
) -> tuple[generic.Sequence[T], generic.Sequence[Exception]]:
    ...

@typing.overload
def cycle_futures(
    futures: generic.Sequence["PendingTaskResult[T]"],
    *,
    transform: generic.Callable[[T, generic.Sequence[T]], VT],
    block_seconds: float = 1.0,
    backoff_seconds: float = 1.0
) -> tuple[generic.Sequence[VT], generic.Sequence[Exception]]:
    ...


def cycle_futures(
    futures: typing.Union[generic.Mapping[KT, "PendingTaskResult[T]"], generic.Sequence["PendingTaskResult[T]"]],
    *,
    transform: typing.Union[generic.Callable[[KT, T, generic.Sequence[T]], VT], generic.Callable[[T, generic.Sequence[T]], VT]] = None,
    block_seconds: float = 1.0,
    backoff_seconds: float = 1.0,
    exception_handler: generic.Callable[[Exception], Exception] = None,
) -> tuple[typing.Union[generic.Mapping[KT, T], generic.Mapping[KT, VT], generic.Sequence[VT], generic.Sequence[T]], generic.Sequence[Exception]]:
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

    if not isinstance(results, (generic.Sequence, generic.Mapping)):
        raise TypeError(f"Expected results to be a sequence or map, but instead received a '{type(results)}'")

    assert isinstance(exceptions, generic.Sequence), f"Expected exceptions to be a series of errors, not a '{type(exceptions)}'"
    return results, exceptions

@typing.overload
def cycle_future(
    future: "PendingTaskResult[T]",
    *,
    block_seconds: float = 1.0,
    exception_handler: generic.Callable[[Exception], Exception] = None,
) -> tuple[typing.Optional[T], typing.Optional[Exception]]:
    ...

@typing.overload
def cycle_future(
    future: "PendingTaskResult[T]",
    *,
    transform: generic.Callable[[T], VT],
    block_seconds: float = 1.0,
    exception_handler: generic.Callable[[Exception], Exception] = None
) -> tuple[typing.Optional[VT], typing.Optional[Exception]]:
    ...

def cycle_future(
    future: "PendingTaskResult[T]",
    *,
    transform: generic.Callable[[T], VT] = None,
    block_seconds: float = 1.0,
    exception_handler: generic.Callable[[Exception], Exception] = None,
) -> tuple[typing.Optional[typing.Union[T, VT]], typing.Optional[BaseException]]:
    """
    Cycle through the list of values and apply and transforms as the contents are generated

    :param future: The list of values to cycle through
    :param transform: The function to apply to each value
    :param block_seconds: The number of seconds to wait for a result
    :param exception_handler: Special handling for exceptions
    :returns: The results from all the futures
    """
    if transform is not None and not callable(transform):
        raise TypeError("transform must be callable")

    if exception_handler is not None and not callable(exception_handler):
        raise ValueError(f"{exception_handler} (type={type(exception_handler)}) is not callable")

    while True:
        try:
            result: T = future.result(timeout=block_seconds)
            if transform is not None:
                result = transform(result)
            return result, None
        except (TimeoutError, concurrent.futures.TimeoutError):
            continue
        except Exception as exception:
            if exception_handler is not None:
                exception = exception_handler(exception)
            return None, exception
        except BaseException as exception:
            return None, exception

    return None, RuntimeError(f"Results could not be gathered from a pending task")


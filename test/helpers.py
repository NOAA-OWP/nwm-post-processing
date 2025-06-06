"""
Helper functions and variables used to standardize testing behavior
"""
import os
import signal
import typing
import pathlib
import threading
import logging
import sys

from queue import Empty
from queue import Queue

from post_processing.configuration import settings


class LogQueueEntry:
    """
    Provides a method for adding log messages that may be delayed until the logger is correctly setup
    """
    _LOG_LOCK: threading.Lock = threading.RLock()
    _QUEUE: typing.Optional[Queue["LogQueueEntry"]] = None

    @classmethod
    def get_queue(cls) -> Queue["LogQueueEntry"]:
        if cls._QUEUE is None:
            cls._QUEUE = Queue()
        return cls._QUEUE

    @classmethod
    def info(cls, name: str, message: str, *args):
        """
        Queue an info message to log
        """
        entry: LogQueueEntry = cls(
            log_name=name,
            level=logging.INFO,
            message=message,
            args=args,
        )
        cls.add_entry(entry)

    @classmethod
    def warning(cls, name: str, message: str, *args, exception: typing.Union[bool, Exception] = None):
        """
        Queue a warning message to log
        """
        entry: LogQueueEntry = cls(
            log_name=name,
            level=logging.WARNING,
            message=message,
            args=args,
            exception=exception,
        )
        cls.add_entry(entry)

    @classmethod
    def is_ready(cls) -> bool:
        """
        Whether LogQueue within the LogQueueEntry is ready to process log messages
        """
        return logging.root.hasHandlers()

    @classmethod
    def error(cls, name: str, message: str, *args, exception: typing.Union[bool, Exception] = None):
        """
        Queue an error message to log
        """
        entry: LogQueueEntry = cls(
            log_name=name,
            level=logging.ERROR,
            message=message,
            args=args,
            exception=exception,
        )
        cls.add_entry(entry)

    @classmethod
    def debug(cls, name: str, message: str, *args):
        """
        Queue a debug message to log
        """
        entry: LogQueueEntry = cls(
            log_name=name,
            level=logging.DEBUG,
            message=message,
            args=args,
        )
        cls.add_entry(entry)

    @classmethod
    def add_entry(cls, entry: "LogQueueEntry") -> None:
        cls.get_queue().put_nowait(entry)
        if cls.is_ready():
            cls.process_messages()

    @classmethod
    def process_messages(cls):
        """
        Step through each message in the queue and attempt to log them
        """
        if not cls.is_ready():
            print(f"The log queue cannot be processed - logging has not been setup yet", file=sys.stderr)
            return

        while not cls.get_queue().empty():
            with cls._LOG_LOCK:
                try:
                    entry: LogQueueEntry = cls._QUEUE.get()
                except Empty:
                    return
            if not isinstance(entry, LogQueueEntry):
                return

            logger: logging.Logger = logging.getLogger(entry.log_name)
            logger.log(entry.level, entry.message, *entry.args, exc_info=entry.exception)

    def __init__(
        self,
        log_name: str,
        level: typing.Union[str, int],
        message: str,
        args: typing.Tuple[str, ...] = None,
        exception: typing.Union[bool, Exception] = None
    ):
        self.log_name: str = log_name
        self.level = level
        self.message = message
        self.args = args or tuple()
        self.exception = exception

    def __str__(self) -> str:
        if isinstance(self.level, int):
            level: str = logging.getLevelName(self.level)
        else:
            level = self.level

        return (
            f"[{level}] {self.message}"
            f"{os.linesep + str(self.exception) if isinstance(self.exception, Exception) else ''}"
        )

class TestLogger:
    """
    A logging.Logger-like object that queues messages for logging rather than logging directly
    """
    def __init__(self, name: str):
        self.name: str = pathlib.Path(name).stem

    def info(self, message: str, *args):
        entry: LogQueueEntry = LogQueueEntry(
            log_name=self.name,
            level=logging.INFO,
            message=message,
            args=args,
        )
        LogQueueEntry.add_entry(entry)

    def warning(self, message: str, *args, exception: typing.Union[bool, Exception] = None):
        entry: LogQueueEntry = LogQueueEntry(
            log_name=self.name,
            level=logging.WARNING,
            message=message,
            args=args,
            exception=exception,
        )
        LogQueueEntry.add_entry(entry)

    def error(self, message: str, *args, exception: typing.Union[bool, Exception] = None):
        entry: LogQueueEntry = LogQueueEntry(
            log_name=self.name,
            level=logging.ERROR,
            message=message,
            args=args,
            exception=exception,
        )
        LogQueueEntry.add_entry(entry)

    def debug(self, message: str, *args):
        entry: LogQueueEntry = LogQueueEntry(
            log_name=self.name,
            level=logging.DEBUG,
            message=message,
            args=args,
        )
        LogQueueEntry.add_entry(entry)

def get_test_logging_config_path() -> pathlib.Path:
    """
    Get the path to the config json for tests. This will help prevent testing information bloating regular run information
    """
    configured_path: typing.Optional[str] = os.environ.get(f'{settings.prefix}_test_logging_config_path')

    if configured_path:
        path = pathlib.Path(configured_path)
        if path.is_file():
            return path

    test_config_path: pathlib.Path = settings.resource_path / "stream_only_log_config.json"
    if test_config_path.is_file():
        return test_config_path

    return settings.logging_config_path


def setup_logging() -> None:
    """
    Set up logging for all tests specifically in a way that's different from the default
    """
    settings.logging_config_path = get_test_logging_config_path()
    from post_processing.utilities.logging import setup_logging
    setup_logging()
    LogQueueEntry.process_messages()


def get_logger(name: str) -> TestLogger:
    """
    Get a logger for the given name and ensure that the system is ready to start handling log messages

    :param name: The name of the logger
    :return: A logger for the given name that will queue up messages for logging
    """
    setup_logging()
    return TestLogger(name=name)


def create_random_mask(
    data_path: pathlib.Path,
    coordinate_variable: str,
    output_path: pathlib.Path,
    size: int = 100,
    seed: int = 123456789
) -> typing.Sequence:
    """
    Create a random mask file that may be used for masking operations in NCO

    :param data_path: The path to a netcdf file to base the mask off of
    :param coordinate_variable: The name of the variable to mask on
    :param output_path: Where to put the result
    :param size: The number of values to include in the mask
    :param seed: The random seed value to use for consistent results
    :returns: The values included in the mask's coordinate variable
    """
    import numpy
    import xarray

    numpy.random.seed(seed=seed)

    source_data: xarray.Dataset = xarray.open_dataset(data_path)

    if coordinate_variable not in source_data.coords:
        raise ValueError(
            f"There is no coordinate in {data_path} named {coordinate_variable}"
        )

    raw_values: numpy.ndarray = source_data[coordinate_variable].values

    if size > raw_values.size:
        raise ValueError(
            f"Cannot make a mask with {size} values - there are only {raw_values.size} values in the source"
        )

    selected_values: numpy.ndarray = numpy.random.choice(raw_values, size=size, replace=False)
    
    subset: xarray.Dataset = source_data.sel(**{coordinate_variable: selected_values})

    subset = subset.drop_vars(names=[name for name in subset.variables.mapping.keys() if name != coordinate_variable])
    subset[coordinate_variable].attrs.clear()
    subset.attrs.clear()

    subset.to_netcdf(path=output_path)

    return sorted(selected_values.tolist())


SIGNAL_REGISTRY: typing.Dict[int, typing.List[typing.Callable[[int, typing.Any], typing.Any]]] = {}
"""
A mapping between signals that may be thrown and multiple handlers for them
"""


def handle_signal(signum, frame):
    """
    A general signal handler that will call all attached handlers
    """
    actions: typing.List[typing.Callable[[int, typing.Any], typing.Any]] = SIGNAL_REGISTRY.get(signum, [])

    for action in actions:
        try:
            action(signum, frame)
        except:
            import traceback
            traceback.print_exc(file=sys.stderr)


def destroy_directories_on_signal(
    signals: typing.Union[int, typing.Sequence[int]],
    temp_directories: typing.Union[str, pathlib.Path, typing.Sequence[typing.Union[str, pathlib.Path]]]
):
    """
    Schedule directories to be removed if and when the passed in signal(s) are encountered

    :param signals: The signals to schedule (such as SIGINT)
    :param temp_directories: The directories to remove when the signal is handled
    """
    if isinstance(signals, int):
        signals = [signals]

    if isinstance(temp_directories, str):
        temp_directories = [pathlib.Path(temp_directories)]

    if isinstance(temp_directories, pathlib.Path):
        temp_directories = [temp_directories]

    temp_directories: typing.Sequence[pathlib.Path] = [
        path if isinstance(path, pathlib.Path) else pathlib.Path(path)
        for path in temp_directories
    ]
    temp_directories = list(filter(lambda path: path.is_dir(), temp_directories))

    def remove_directories(signum: int, frame):
        for temporary_directory in temp_directories:
            if not temporary_directory.is_dir():
                continue
            import shutil
            shutil.rmtree(temporary_directory)

    import signal
    for signal_to_handle in signals:
        SIGNAL_REGISTRY.setdefault(signal_to_handle, []).append(remove_directories)
        signal.signal(signal_to_handle, handle_signal)

def get_temporary_directory() -> pathlib.Path:
    """
    Create a temporary directory and add a function to remove it if the application exits unexpectedly

    :returns: A path to the temporary directory. You are expected to remove this manually when operations are complete.
    """
    import tempfile
    import atexit
    path: typing.Union[str, pathlib.Path] = tempfile.mkdtemp()
    path = pathlib.Path(path)
    destroy_directories_on_signal(
        signals=[signal.SIGINT, signal.SIGQUIT, signal.SIGTERM],
        temp_directories=path
    )

    def remove_directory(directory: pathlib.Path):
        if isinstance(directory, pathlib.Path) and directory.is_dir():
            import shutil
            shutil.rmtree(directory)

    atexit.register(remove_directory, path)
    return path

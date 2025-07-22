"""
Handling for logging
"""
import logging.config
import typing
import logging
import pathlib

class OnlyErrorFilter(logging.Filter):
    """
    Only allows critical and error messages
    """
    def filter(self, record):
        return record.levelno in (logging.ERROR, logging.CRITICAL)
    

class ErrorExclusionFilter(logging.Filter):
    """
    Prevents errors from entering the log    
    """
    def filter(self, record):
        return record.levelno not in (logging.ERROR, logging.CRITICAL)


class LevelFilter(logging.Filter):
    """
    Only levels registered within the filter may be logged
    """
    def __init__(self, levels: typing.Iterable[int]) -> None:
        super().__init__()
        self.levels = set(map(get_level, levels))
        if len(self.levels) == 0:
            raise ValueError("Cannot set up a level filter - no levels defined")

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno in self.levels

def get_level(level: typing.Union[int, float, str, bytes]) -> int:
    """
    Get the proper level int based on a variety of possible input types

    Example:
        >>> get_level(20)
        20
        >>> get_level("info")
        20
        >>> get_level("DEBuG")
        10
        >>> get_level("52.1")
        52
        >>> get_level("kritical")
        ValueError(...)

    :param level: The level whose corresponding int we want to find
    :returns: The proper int level that matches the input type
    :raises ValueError: If the input type is not supported or is an invalid string
    """
    if isinstance(level, bytes):
        level = level.decode()

    if isinstance(level, float):
        return int(level)
    elif isinstance(level, int):
        return level
    elif isinstance(level, str) and level.isdigit():
        return int(float(level))
    elif isinstance(level, str):
        level = level.strip()
        parsed_level = logging.getLevelName(level=level.upper())
        if isinstance(parsed_level, str) and parsed_level.startswith("Level "):
            raise ValueError(f"Could not get an appropriate log level for: {level}")
        return parsed_level
    else:
        raise TypeError(f"Cannot create a log level from: '{level}' (type={type(level)})")

class LevelBasedFormatter(logging.Formatter):
    """
    A formatter that may have different formats per level
    """
    def __init__(
        self,
        fmt: str = None,
        datefmt: str = None,
        style="%",
        validate: bool = True,
        *,
        defaults: typing.Mapping[str, typing.Any] = None,
        level_formats: typing.Mapping[typing.Union[int, str], str] = None
    ):
        super().__init__(fmt=fmt, datefmt=datefmt, style=style, validate=validate, defaults=defaults)

        self.level_formats: typing.Mapping[int, str] = {
            get_level(level=level): level_format
            for level, level_format in (level_formats or {}).items()
        }

    def format(self, record):
        message_format: str = self.level_formats.get(record.levelno, self._fmt)
        self._style._fmt = message_format
        return super().format(record)


class JSONLogHandler(logging.Handler):
    """
    A log handler that is capable of outputting to JSON
    """
    def __init__(
        self,
        filename: typing.Union[pathlib.Path, str],
        level: int = None,
        max_bytes: int = 1024 ** 2
    ):
        """
        :param filename: Where the log should be saved
        :param level: The level to write messages to
        :param max_bytes: The largest a log may be before being rotated
        """
        super().__init__(level=level)
        self.max_bytes = max_bytes
        self.filepath = filename if isinstance(filename, pathlib.Path) else pathlib.Path(filename)

        import threading
        self.lock: threading.RLock = threading.RLock()

        with self.lock:
            self._first_record = not self.filepath.is_file() or self.size == 0
            if self._first_record:
                self.filepath.write_bytes(b'[]')

    def emit(self, record: logging.LogRecord):
        """
        Record an entry

        :param record: The log details to write
        """
        import os
        # Lock the handler so there's no possible contention between threads
        with self.lock:
            # Reset the file if we've grown too big
            if self.size >= self.max_bytes:
                self.rotate_file()

            # An empty JSON is ~3 bytes long, so if it's 5 or less, we can be sure that there's nothing recorded
            # since are messages are definitely going to be longer that 2 bytes long
            is_first_record: bool = self.size <= 5

            log_entry: str = self.format(record=record)

            # If this is the first record, we can just blindly write over everything with new content
            if is_first_record:
                self.filepath.write_text(
                    f"[{os.linesep}"
                    f"  {log_entry}"
                    f"{os.linesep}]"
                )
                return

            # Open the file as binary - text files can't do negative indexing
            with self.filepath.open('rb+') as log_file:
                import string
                # Record the binary whitespace characters to make it easier to tell if we haven't hit actual data yet
                empty_values: typing.Sequence[bytes] = [
                    value.encode() for value in string.whitespace
                ]

                # Seeking to the end of the file will move the number of positions equal to the length of the file.
                # We're going to use this as the base to move backwards by. For '[one]', this results in:
                #   log_file: ['[']['o']['n']['e'][']']['\n']\0
                #                                             ^        : \0
                length: int = log_file.seek(0, os.SEEK_END)

                # Since the length is out of the range (0 indexed), go back by 1 so we're able to read one forwards.
                # Moving to this previous position will move us to:
                #   log_file: ['[']['o']['n']['e'][']']['\n']\0
                #                                         ^           : '\n'
                current_position: int = length - 1

                while current_position >= 0:
                    # Move the current position in the buffer to what was evaluated as being a number of positions
                    # away from the end of the file
                    log_file.seek(current_position)

                    # Read the value at this position. This will move the position in the buffer forward by one
                    last_value: bytes = log_file.read(1)

                    # We know we've hit the last piece of content if this isn't whitespace
                    if last_value not in empty_values:
                        break

                    # Decrease the position. Per the example before, this updated 'current_position' will move us to:
                    #   log_file: ['[']['o']['n']['e'][']']['\n']\0
                    #                                   ^                 : ']'
                    current_position -= 1

                # `.read(N)` will move us forward by N positions. Move BACK to the last valid position to go back to
                # the position we want to start overwriting
                log_file.seek(current_position)

                data_to_write: bytes = b''

                # Since we're writing JSON, each object added needs to be separated by a ','
                if not is_first_record:
                    data_to_write += b','

                # Add a newline and space for readability's sake
                data_to_write += b'\n    '
                data_to_write += log_entry.encode()
                data_to_write += last_value

                # Write to the file. This will add all bytes in `data_to_write` to the file starting at the position
                # of `current_position` within the file buffer
                log_file.write(data_to_write)

    def format(self, record: logging.LogRecord):
        """
        Format the given log entry as a string.

        Since the output file needs to be json, convert the record to a dictionary, then to a json string
        :param record: The log details to write
        :return: The record formatted as a json entry ready to be added to the log
        """
        # NOTE: If you want added fields within the json log, add it here
        entry: typing.Dict[str, typing.Any] = {
            'timestamp': self.formatter.formatTime(record),
            'message': record.getMessage(),
            'level': record.levelname,
            'name': record.name,
            'filename': record.filename,
            'lineno': record.lineno,
            'process': record.process,
            'thread': record.thread,
        }

        # Go ahead and add anything extra here if it exists
        if hasattr(record, 'extra'):
            entry.update(record.extra)

        import json
        return json.dumps(entry)

    def rotate_file(self):
        """
        Move the current log into a new file and create a new one so the file is kept to a manageable size

        TODO: Add backup limitations so this doesn't blow up over time
        """
        # Collect the parts to generate the new file within the same directory as the current log.
        #   The new log will be named like 'name.1.json', 'name.2.json', etc.
        directory: pathlib.Path = self.filepath.parent
        log_name: str = self.filepath.name
        extension: str = self.filepath.suffix
        index: int = 1

        while (directory / f"{log_name}.{index}{extension}").exists():
            index += 1

        # Rename the file - this is equivalent to `mv`
        self.filepath.rename(directory / f"{log_name}.{index}{extension}")

        # Since the content of the log has been moved, we can go ahead and add the bare content to the log
        with self.filepath.open('wb') as log_file:
            log_file.write(b'[]')

    @property
    def size(self):
        """
        The size of the log file in bytes
        """
        return self.filepath.stat().st_size

    def __len__(self):
        return self.size


def override_log_levels(log_level_override_path: pathlib.Path = None):
    """
    Set configured overrides for log levels in order to hide unwanted messages from other libraries

    :param log_level_override_path: The path to a log level override json file
    """
    if log_level_override_path is None:
        return
    if isinstance(log_level_override_path, str):
        log_level_override_path = pathlib.Path(log_level_override_path)

    if not isinstance(log_level_override_path, pathlib.Path):
        raise TypeError(
            f"The log level override path is invalid: {log_level_override_path} (type={type(log_level_override_path)})"
        )

    if log_level_override_path.is_dir():
        logging.warning(
            f"The log level override path is invalid - it points to a directory. No log levels will be overridden. "
            f"{log_level_override_path}"
        )
        return

    if not log_level_override_path.exists():
        logging.warning(
            f"There is no log level override file at '{log_level_override_path}'. No log levels will be overridden."
        )
        return

    import json
    try:
        overrides: typing.Dict[str, typing.Union[str, int]] = json.loads(log_level_override_path.read_text())
    except json.decoder.JSONDecodeError as error:
        logging.warning(
            f"Could not deserialize the file at '{log_level_override_path}'. No log levels will be overridden. {error}"
        )
        return

    for logger_name, level in overrides.items():
        level: int = get_level(level=level)
        logging.getLogger(logger_name).setLevel(level)

    
def setup_logging(log_path: typing.Union[pathlib.Path, str] = None):
    """
    Common setup logic for log configurations
    """
    if logging.getLogger().hasHandlers():
        return

    from post_processing.configuration import settings

    if log_path is None:
        log_path = settings.logging_config_path
    elif isinstance(log_path, (str, pathlib.Path)):
        log_path = pathlib.Path(log_path)

    if log_path is not None and log_path.is_file():
        import json
        configuration: typing.Dict = json.loads(log_path.read_text())
        logging.config.dictConfig(config=configuration)
    else:
        logging.basicConfig(
            level=logging.DEBUG if settings.debug else logging.INFO,
            format=settings.log_format,
            datefmt=settings.date_format
        )

    if settings.json_log_path:
        root_logger = logging.getLogger()
        handler: JSONLogHandler = JSONLogHandler(
            filename=settings.json_log_path,
            level=root_logger.getEffectiveLevel(),
            max_bytes=settings.json_log_bytes
        )
        root_logger.addHandler(handler)

    for logger_name in settings.loggers_to_quiet:
        logger: logging.Logger = logging.getLogger(logger_name)
        logger.setLevel(logging.WARNING)

    override_log_levels(log_level_override_path=settings.log_level_override_path)

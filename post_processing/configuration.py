"""
Common settings used within and without the application
"""
import logging
import typing
import os
import pathlib

from collections import UserDict

_DEFAULT_DEBUG_SETTING: bool = False
"""
The default setting for whether or not behavior for debugging purposes is enabled. 
Make sure that this is False when deployed to testing and production.
"""
_DEFAULT_DATE_FORMAT: str = "%Y-%m-%d %H:%M:%S%z"
"""The default date format for the entire project"""

_DEFAULT_LOG_FORMAT: str = "[%(asctime)s] %(levelname)s %(name)s: %(message)s"
"""The default formatting for log messages when logging is not set up with the logging configuration"""

_DEFAULT_NETCDF_CACHE_SIZE: int = 3
"""The number of netcdf files to keep loaded"""

SENTINEL = object()


def _get_env_from_os(key: str, default: typing.Any = None) -> typing.Any:
    """
    Get the environment variable by flexible naming

    If we want 'POST_PROCESS_EXAMPLE_VARIABLE' and there is 'POST_PROCESS_EXAMPLE_VARIABLE',
    we retrieve 'POST_PROCESS_EXAMPLE_VARIABLE'. If that doesn't exist but 'POST_PROCESS_Example_Variable' exists,
    we'll retrieve 'POST_PROCESS_Example_Variable'. If 'POST_PROCESS_EXAMPLE_VARIABLE' doesn't exist, but there are
    two or more versions with different casing, we throw an error due to the questionable environment.
    Otherwise we return the default

    :param key: The name of the environment variable to retrieve
    :param default: A value to return if there is no entry with a matching name in flexible casing
    """
    if key in os.environ:
        return os.environ[key]

    candidates: typing.Sequence[typing.Any] = list({
        value
        for env_key, value in os.environ.items()
        if env_key.upper() == key.upper()
    })

    if len(candidates) > 1:
        raise OSError(f"Cannot get a value for '{key}' - multiple candidates without exact casing: {candidates}")

    return default if not candidates else candidates[0]


def _set_env(key: str, value: typing.Any):
    """
    Set the environment variable in flexible casing

    If the code says the 'post_process_example_variable' but the available value is "POST_PROCESS_EXAMPLE_VARIABLE",
    sets it as "POST_PROCESS_EXAMPLE_VARIABLE"

    :param key: The environment variable name whose value to set
    :param value: The new value of the environment variable
    """
    candidate_keys: typing.Sequence[str] = [
        env_key
        for env_key in os.environ.keys()
        if key.upper() == env_key.upper()
    ]

    if len(candidate_keys) == 1:
        os.environ[candidate_keys[0]] = value
        return
    elif len(candidate_keys) > 2:
        logging.getLogger("Settings").warning(
            f"There are multiple keys that might match '{key}'. "
            f"Using the key as given and not one of the following similar keys: {candidate_keys}"
        )

    os.environ[key] = value


def _parse_env_file(env_path: pathlib.Path) -> typing.Dict[str, typing.Any]:
    """
    Parse an .env without involving 3rd party libraries

    :param env_path: The path to the .env file. An empty dictionary is returned if it is not a file,
    raises an error if it is a directory
    :returns: A dictionary of all found variables and their values
    :raises IsADirectoryError: If the .env file path leads to a directory rather than a file
    """
    import re

    if isinstance(env_path, str):
        env_path = pathlib.Path(env_path)

    if not env_path.exists():
        return {}

    if env_path.is_dir():
        raise IsADirectoryError(f"{env_path} is a directory, not a file")

    line_pattern: re.Pattern = re.compile(
        r"^\s*(?<!#)(?P<variable_name>[A-Za-z]\w*)\s*=\s*(?P<variable_value>\"{1}[^\"]+\"{1}|'{1}[^']*'{1}|[^#\"\n]+)(#.+|\s)*$",
        re.MULTILINE,
    )

    file_text: str = env_path.read_text()

    configured_variables: typing.Dict[str, typing.Any] = {}

    for match in line_pattern.finditer(file_text):
        configured_variables[match.group("variable_name").lower()] = match.group("variable_value")

    return configured_variables


class _Settings(UserDict):
    """
    An access point for application and environment settings
    """
    def __init__(self, initial_values: typing.Mapping = None, **kwargs):
        super().__init__()

        for key, value in os.environ.items():
            self.__setitem__(key=key, item=value)

        for initial_key, initial_value in (initial_values or {}).items():
            matching_key: str = self._find_key(initial_key)
            self.__setitem__(key=matching_key, item=initial_value)

        for keyword, argument in kwargs.items():
            matching_key: str = self._find_key(keyword)
            self.__setitem__(key=matching_key, item=argument)

        env_file: pathlib.Path = self.application_path / ".env"

        configured_variables: typing.Mapping[str, typing.Any] = _parse_env_file(env_path=env_file)

        for key, value in configured_variables.items():
            matching_key: str = self._find_key(key)
            self.__setitem__(key=matching_key, item=value)

    def _find_key(self, key: str) -> str:
        """
        Find a matching case-flexible key in either these settings or in the os environment variables

        :param key: The name of the environment variable to find
        :returns: The appropriate key
        """
        matching_keys: typing.List[str] = [
            contained_key
            for contained_key in self.keys()
            if key.lower() == contained_key.lower()
        ]

        matching_keys.extend([
            os_key
            for os_key in os.environ.keys()
            if os_key not in matching_keys
               and os_key.lower() == key.lower()
        ])

        if len(matching_keys) == 1:
            return matching_keys[0]

        return key

    @property
    def base_path(self) -> pathlib.Path:
        """
        The default starting point for relative search paths
        """
        proposed_key: str = f"{self.prefix}_BASE_PATH"
        key: str = self._find_key(key=proposed_key)

        if key not in self.keys() or self.__getitem__(key=key) is None:
            base_path: pathlib.Path = pathlib.Path.cwd()
            self.__setitem__(key=key, item=base_path)

        base_path: pathlib.Path = self.__getitem__(key=key)

        if not isinstance(base_path, pathlib.Path):
            base_path = pathlib.Path(base_path)
            self.__setitem__(key=key, item=base_path)

        return base_path

    @base_path.setter
    def base_path(self, value: pathlib.Path):
        proposed_key: str = f"{self.prefix}_BASE_PATH"
        key: str = self._find_key(key=proposed_key)

        if isinstance(value, str):
            value = pathlib.Path(value)
        elif not isinstance(value, pathlib.Path):
            raise TypeError(
                f"Cannot assign '{value}' (type={type(value)}) to {self.__class__.__name__}.base_path - "
                f"it must be a pathlib.Path"
            )

        self.__setitem__(key=key, item=value)


    @property
    def prefix(self) -> str:
        """
        The prefix of important application environment parameters
        """
        return "PP"

    @property
    def allow_threading(self) -> bool:
        """
        Whether to allow multithreading
        """
        proposed_key: str = f"{self.prefix}_allow_threading"
        key: str = self._find_key(key=proposed_key)

        if key not in self.keys():
            self.__setitem__(key=key, item=False)

        stored_value: typing.Any = self.__getitem__(key=key)

        return str(stored_value).lower() in ('true', 't', '1', 'yes', 'y', 'on')

    @allow_threading.setter
    def allow_threading(self, value: bool):
        proposed_key: str = f"{self.prefix}_allow_threading"
        key: str = self._find_key(key=proposed_key)
        self.__setitem__(key=key, item=value)

    @property
    def default_netcdf_engine(self) -> str:
        """
        The netcdf engine to use by default
        """
        proposed_key: str = f"{self.prefix}_default_netcdf_engine"
        key: str = self._find_key(key=proposed_key)

        if key not in self.keys():
            import importlib.util
            if importlib.util.find_spec("h5netcdf") is None:
                self.__setitem__(key=key, item="netcdf4")
            else:
                self.__setitem__(key=key, item="h5netcdf")

        return self.__getitem__(key=key)

    @default_netcdf_engine.setter
    def default_netcdf_engine(self, value: str):
        proposed_key: str = f"{self.prefix}_default_netcdf_engine"
        key: str = self._find_key(key=proposed_key)
        self.__setitem__(key=key, item=value)

    @property
    def netcdf_cache_size(self) -> int:
        proposed_key: str = f"{self.prefix}_netcdf_cache_size"
        key: str = self._find_key(key=proposed_key)

        if key not in self.keys():
            self.__setitem__(key=key, item=_DEFAULT_NETCDF_CACHE_SIZE)

        return int(float(self.__getitem__(key=key)))

    @netcdf_cache_size.setter
    def netcdf_cache_size(self, value: int):
        proposed_key: str = f"{self.prefix}_netcdf_cache_size"
        key: str = self._find_key(key=proposed_key)
        self.__setitem__(key=key, item=value)
    
    @property
    def debug(self) -> bool:
        """
        Whether this is running in debug mode
        """
        proposed_key: str = "{prefix}_debug".format(prefix=self.prefix).lower()
        key: str = self._find_key(key=proposed_key)

        if key not in self.keys():
            self.__setitem__(key=key, item=_DEFAULT_DEBUG_SETTING)
        
        value: typing.Any = self.__getitem__(key=key)

        if isinstance(value, str):
            value = value.lower() in ("1", "o", "on", "true", "y", "yes")
            self.__setitem__(key=key, item=value)
        elif not isinstance(value, bool):
            value = bool(value)
            self.__setitem__(key=key, item=value)

        return value
    
    @property
    def date_format(self) -> str:
        """
        How dates should be formatted across the application
        """
        proposed_key: str = "{prefix}_date_format".format(prefix=self.prefix).lower()
        key: str = self._find_key(key=proposed_key)

        if key not in self.keys() or not self.__getitem__(key=key):
            self.__setitem__(key=key, item=_DEFAULT_DATE_FORMAT)

        return self.__getitem__(key=key)
    
    @property
    def log_format(self) -> str:
        """
        How logs should be formatted
        """
        proposed_key: str = "{prefix}_log_format".format(prefix=self.prefix).lower()
        key: str = self._find_key(key=proposed_key)

        if key not in self.keys() or not self.__getitem__(key=key) or not isinstance(self.__getitem__(key=key), str):
            self.__setitem__(key=key, item=_DEFAULT_LOG_FORMAT)

        return self.__getitem__(key=key)

    @property
    def lazy_load_netcdf(self) -> bool:
        """
        Whether to default to loading netcdf data lazily
        """
        proposed_key: str = "{prefix}_lazy_load_netcdf".format(prefix=self.prefix).lower()
        key: str = self._find_key(key=proposed_key)

        if key not in self.keys():
            self.__setitem__(key=key, item=False)

        return str(self.__getitem__(key=key)).lower() in ("true", "1", "t", "y", "yes", "o", "on")

    @lazy_load_netcdf.setter
    def lazy_load_netcdf(self, value: bool):
        proposed_key: str = "{prefix}_lazy_load_netcdf".format(prefix=self.prefix).lower()
        key: str = self._find_key(key=proposed_key)
        self.__setitem__(key=key, item=value)

    @property
    def application_path(self) -> pathlib.Path:
        """
        Get the root of this application
        """
        import post_processing
        return pathlib.Path(post_processing.__file__).parent.parent

    @property
    def loggers_to_quiet(self) -> typing.Sequence[str]:
        """
        The names of all loggers that may output errors but not basic INFO
        """
        proposed_key: str = "{prefix}_loggers_to_quiet".format(prefix=self.prefix).lower()
        key: str = self._find_key(key=proposed_key)
        entries: typing.Optional[typing.Sequence[str]] = self.get(key)

        if entries is None:
            entries: typing.List[str] = []
            names_from_environment: typing.Optional[str] = os.environ.get(key)

            if names_from_environment is not None:
                import re
                names: typing.Sequence[str] = re.split(r"[;,]+", names_from_environment)
                entries.extend(names)
            self.__setitem__(key, entries)

        return entries

    @loggers_to_quiet.setter
    def loggers_to_quiet(self, entries: typing.Sequence[str]) -> None:
        proposed_key: str = "{prefix}_loggers_to_quiet".format(prefix=self.prefix).lower()
        key: str = self._find_key(key=proposed_key)
        self.__setitem__(key, entries)

    @property
    def json_log_path(self) -> typing.Optional[pathlib.Path]:
        """
        The path to a json log to write to.

        No log path means no configured json log
        """
        proposed_key: str = f"{self.prefix}_json_log_path"
        key: str = self._find_key(key=proposed_key)

        if key in self.keys():
            path = self.__getitem__(key)
            if isinstance(path, str):
                path = pathlib.Path(path)
                self.__setitem__(key=key, item=path)
        elif key in os.environ:
            path = os.environ['key']

            if isinstance(path, str):
                path = pathlib.Path(path)

            self.__setitem__(key=key, item=path)
        else:
            path = None

        return path

    @json_log_path.setter
    def json_log_path(self, value: typing.Optional[pathlib.Path]):
        proposed_key: str = f"{self.prefix}_json_log_path"
        key: str = self._find_key(key=proposed_key)

        if not isinstance(value, (pathlib.Path, None)):
            value = pathlib.Path(value)

        self.__setitem__(key=key, item=value)

    @property
    def log_level_override_path(self) -> typing.Optional[pathlib.Path]:
        """
        The path to a json file that dictates log levels to override
        """
        proposed_key: str = f"{self.prefix}_log_level_override_path"
        key: str = self._find_key(key=proposed_key)

        if key not in self.keys():
            value = _get_env_from_os(key=key, default=SENTINEL)

            if value is SENTINEL:
                possible_path: pathlib.Path = self.resource_path / "log_level_override.json"
                if possible_path.is_file():
                    value = possible_path
            else:
                value = pathlib.Path(value)

            if isinstance(value, str):
                value = pathlib.Path(value)

            self.__setitem__(key=key, item=value)

        configured_value: typing.Union[pathlib.Path, object] = self.__getitem__(key=key)

        if configured_value is SENTINEL:
            return None

        return configured_value

    @log_level_override_path.setter
    def log_level_override_path(self, value: typing.Optional[pathlib.Path]):
        proposed_key: str = f"{self.prefix}_log_level_override_path"
        key: str = self._find_key(key=proposed_key)
        if value is None:
            self.__setitem__(key=key, item=value)
            return

        if isinstance(value, str):
            value = pathlib.Path(value)

        if not isinstance(value, pathlib.Path):
            raise TypeError(
                f"Cannot set the log level override path - it must be none or a pathlib.Path and instead was: "
                f"{value} (type={type(value)})"
            )

        if value.is_dir():
            raise ValueError(
                f"Cannot set the log level override path to {value} - it is a directory, not a file as required"
            )

        if not value.is_file():
            raise FileNotFoundError(
                f"Cannot set the log level override path to {value} - it is not a file"
            )

        self.__setitem__(key=key, item=value)

    @property
    def json_log_level(self) -> int:
        """
        The log level of the optional json logger
        """
        proposed_key: str = f"{self.prefix}_json_log_level"
        key: str = self._find_key(key=proposed_key)

        log_level: typing.Union[str, int, None] = self.get(key)

        if log_level is None:
            log_level: str = os.environ.get(key, "INFO")
            self.__setitem__(key=key, item=log_level)

        if isinstance(log_level, str) and log_level.isdigit():
            log_level = int(log_level)
        elif isinstance(log_level, str):
            log_level = logging.getLevelName(level=log_level.upper())

        return log_level

    @json_log_level.setter
    def json_log_level(self, value: typing.Optional[int]):
        proposed_key: str = f"{self.prefix}_json_log_level"
        key: str = self._find_key(key=proposed_key)
        self.__setitem__(key=key, item=value)

    @property
    def json_log_maximum_bytes(self) -> int:
        """
        The maximum size of a json log
        """
        proposed_key: str = f"{self.prefix}_json_log_maximum_bytes"
        key: str = self._find_key(key=proposed_key)
        log_maximum_bytes: typing.Union[str, int, None] = self.get(key)
        if log_maximum_bytes is None:
            log_maximum_bytes: typing.Union[str, int, None] = int(float(os.environ.get(key, 1024 ** 2)))
            self.__setitem__(key=key, item=log_maximum_bytes)

        if not isinstance(log_maximum_bytes, int):
            log_maximum_bytes = int(float(log_maximum_bytes))
            self.__setitem__(key=key, item=log_maximum_bytes)

        return log_maximum_bytes

    @json_log_maximum_bytes.setter
    def json_log_maximum_bytes(self, value: typing.Optional[int]):
        proposed_key: str = f"{self.prefix}_json_log_maximum_bytes"
        key: str = self._find_key(key=proposed_key)
        self.__setitem__(key=key, item=None if value is None else int(float(value)))
    
    @property
    def resource_path(self) -> pathlib.Path:
        """
        Where to find external resources
        """
        proposed_key: str = "{prefix}_resource_path".format(prefix=self.prefix).lower()
        key: str = self._find_key(key=proposed_key)

        if key not in self.keys() or not self.__getitem__(key=key) or not isinstance(self.__getitem__(key=key), (pathlib.Path, str)):
            path: pathlib.Path = self.application_path / "resources"
            path.mkdir(exist_ok=True, parents=True)
            self.__setitem__(key=key, item=path)

        elif isinstance(self.__getitem__(key=key), str):
            self.__setitem__(key=key, item=pathlib.Path(self.__getitem__(key=key)))
        elif not isinstance(self.__getitem__(key=key), (str, pathlib.Path)):
            raise TypeError(
                "The '{key}' setting is invalid - it must be a path but was instead '{value}' (type={value_type})".format(
                    key=key.upper(),
                    value=self.__getitem__(key=key),
                    value_type=type(self.__getitem__(key=key))
                )
            )

        path = self.__getitem__(key=key)

        if not isinstance(path, pathlib.Path):
            raise TypeError(
                "Could not retrieve the resource path - its value is not a path: {path} (type={path_type})".format(
                    path=path,
                    path_type=type(path)
                )
            )
        
        if not path.is_dir():
            raise FileNotFoundError("Could not find a resources directory at '{path}'".format(path=path))
        
        return path

    @property
    def mask_path(self) -> pathlib.Path:
        """
        The path to masks bundled with the application
        """
        proposed_key: str = f"{self.prefix}_mask_path".lower()
        key: str = self._find_key(key=proposed_key)

        if key not in self.keys() or not isinstance(self.__getitem__(key=key), (pathlib.Path, str)):
            path: pathlib.Path = self.resource_path / "masks"
            self.__setitem__(key=key, item=path)

        mask_path: typing.Union[str, pathlib.Path] = self.__getitem__(key=key)
        if not isinstance(mask_path, pathlib.Path):
            mask_path: pathlib.Path = pathlib.Path(mask_path)
            self.__setitem__(key=key, item=mask_path)

        return mask_path

    @property
    def routelink_path(self) -> pathlib.Path:
        """
        The path to masks bundled with the application
        """
        proposed_key: str = f"{self.prefix}_routelink_path".lower()
        key: str = self._find_key(key=proposed_key)

        if key not in self.keys() or not isinstance(self.__getitem__(key=key), (pathlib.Path, str)):
            path: pathlib.Path = self.resource_path / "routelink"
            self.__setitem__(key=key, item=path)

        routelink_path: pathlib.Path = self.__getitem__(key=key)

        if not isinstance(routelink_path, pathlib.Path):
            routelink_path: pathlib.Path = pathlib.Path(routelink_path)
            self.__setitem__(key=key, item=routelink_path)

        return routelink_path

    @property
    def threshold_path(self) -> pathlib.Path:
        """
        The path to thresholds used for anomaly calculation
        """
        proposed_key: str = "{prefix}_threshold_path".format(prefix=self.prefix).lower()
        key: str = self._find_key(key=proposed_key)

        if key not in self.keys() or not self.__getitem__(key=key) or not isinstance(self.__getitem__(key=key), (pathlib.Path, str)):
            path: pathlib.Path = self.resource_path / "thresholds"

            self.__setitem__(key=key, item=path)

        elif isinstance(self.__getitem__(key=key), str):
            self.__setitem__(key=key, item=pathlib.Path(self.__getitem__(key=key)))
        elif not isinstance(self.__getitem__(key=key), (str, pathlib.Path)):
            raise TypeError(
                "The '{key}' setting is invalid - it must be a path but was instead '{value}' (type={value_type})".format(
                    key=key.upper(),
                    value=self.__getitem__(key=key),
                    value_type=type(self.__getitem__(key=key))
                )
            )

        path = self.__getitem__(key=key)

        if not isinstance(path, pathlib.Path):
            raise TypeError(
                "Could not retrieve the threshold path - its value is not a path: {path} (type={path_type})".format(
                    path=path,
                    path_type=type(path)
                )
            )

        if not path.is_dir():
            raise FileNotFoundError("Could not find a threshold directory at '{path}'".format(path=path))

        return path
    
    @property
    def logging_config_path(self) -> pathlib.Path:
        """
        The intended path to a logging config
        """
        proposed_key: str = "{prefix}_log_config_path".format(prefix=self.prefix).lower()
        key: str = self._find_key(key=proposed_key)

        if key not in self.keys() or not self.__getitem__(key=key) or not isinstance(self.__getitem__(key=key), (str, pathlib.Path)):
            resource_path: pathlib.Path = self.resource_path
            path = resource_path / "python_log_config.json"
            self.__setitem__(key=key, item=path)
        elif isinstance(self.__getitem__(key=key), str):
            self.__setitem__(key=key, item=pathlib.Path(self.__getitem__(key=key)))
        elif not isinstance(self.__getitem__(key=key), (str, pathlib.Path)):
            raise TypeError(
                "The '{key}' setting is invalid - it must be a path but was instead '{value}' (type={value_type})".format(
                    key=key.upper(),
                    value=self.__getitem__(key=key),
                    value_type=type(self.__getitem__(key=key))
                )
            )

        logging_config_path: pathlib.Path = self.__getitem__(key=key)

        if not isinstance(logging_config_path, pathlib.Path):
            logging_config_path: pathlib.Path = pathlib.Path(logging_config_path)
            self.__setitem__(key=key, item=logging_config_path)
        
        return logging_config_path

    @logging_config_path.setter
    def logging_config_path(self, value: pathlib.Path):
        """
        The setter for logging_config_path
        """
        proposed_key: str = "{prefix}_log_config_path".format(prefix=self.prefix).lower()
        key: str = self._find_key(key=proposed_key)

        if not isinstance(value, pathlib.Path):
            value = pathlib.Path(value)

        self.__setitem__(key=key, item=value)

    @property
    def intermediate_directory(self) -> pathlib.Path:
        """
        Where generated products that serve as input for other products should be written
        """
        proposed_key: str = "{prefix}_intermediate_directory".format(prefix=self.prefix).lower()
        key: str = self._find_key(key=proposed_key)

        if key not in self.keys() or not self.__getitem__(key=key):
            path: pathlib.Path = self.application_path / "intermediate"
            path.mkdir(parents=True, exist_ok=True)
            self.__setitem__(key=key, item=path)
        elif isinstance(self.__getitem__(key=key), str):
            self.__setitem__(key=key, item=pathlib.Path(self.__getitem__(key=key)))
        elif not isinstance(self.__getitem__(key=key), (str, pathlib.Path)):
            raise TypeError(
                "The '{key}' setting is invalid - it must be a path but was instead '{value}' (type={value_type})".format(
                    key=key.upper(),
                    value=self.__getitem__(key=key),
                    value_type=type(self.__getitem__(key=key))
                )
            )

        return self.__getitem__(key=key)

    @intermediate_directory.setter
    def intermediate_directory(self, value: pathlib.Path):
        if not isinstance(value, pathlib.Path):
            value = pathlib.Path(value)

        if not value.is_dir():
            raise NotADirectoryError(f"Cannot set the intermediate directory to '{value}' - it is not a directory")

        proposed_key: str = "{prefix}_intermediate_directory".format(prefix=self.prefix).lower()
        key: str = self._find_key(key=proposed_key)
        self.__setitem__(key=key, item=value)

    @property
    def profile_path(self) -> pathlib.Path:
        """
        The path where you should expect to find profile configurations
        """
        proposed_key: str = "{prefix}_profile_path".format(prefix=self.prefix).lower()
        key: str = self._find_key(key=proposed_key)

        if key not in self.keys() or not self.__getitem__(key=key):
            profile_path: pathlib.Path = pathlib.Path(os.environ.get(key, self.resource_path / "profiles"))
            self.__setitem__(key=key, item=profile_path)

        path: pathlib.Path = self.__getitem__(key=key)
        if not isinstance(path, pathlib.Path):
            path = pathlib.Path(path)
            self.__setitem__(key=key, item=path)

        path.mkdir(parents=True, exist_ok=True)
        return path

    @profile_path.setter
    def profile_path(self, value: pathlib.Path):
        if not isinstance(value, pathlib.Path):
            value = pathlib.Path(value)

        if not value.is_dir():
            raise NotADirectoryError(f"Cannot set the profile path to '{value}' - it is not a directory")
        proposed_key: str = "{prefix}_profile_path".format(prefix=self.prefix).lower()
        key: str = self._find_key(key=proposed_key)
        self.__setitem__(key=key, item=value)

    @property
    def maximum_additional_threads(self) -> int:
        """
        The maximum number of threads that may be spun up for additional tasks
        """
        proposed_key: str = "{prefix}_MAXIMUM_ADDITIONAL_THREADS".format(prefix=self.prefix).lower()
        key: str = self._find_key(key=proposed_key)

        if key not in self.keys() or not self.__getitem__(key=key):
            maximum_additional_threads: int = int(_get_env_from_os(key=key, default=os.cpu_count()))
            self.__setitem__(key=key, item=maximum_additional_threads)

        return int(self.__getitem__(key=key))

    @maximum_additional_threads.setter
    def maximum_additional_threads(self, value: int):
        proposed_key: str = "{prefix}_MAXIMUM_ADDITIONAL_THREADS".format(prefix=self.prefix).lower()
        key: str = self._find_key(key=proposed_key)

        self.__setitem__(key=key, item=value)

    @property
    def verbosity(self) -> int:
        """
        A numeric level to further contain messages. When comparing, the lower the value the more likely it
        should be logged and the higher the verbosity the more likely it should be printed.

        The higher the verbosity, the more verbose the application should be

        Checks should look like:

            >>> if settings.verbosity < 3:
            ...     message = "regular message"
            ... else:
            ...     message = "detailed message"
            ... logging.getLogger().debug(message)
        """
        proposed_key: str = "{prefix}_VERBOSITY".format(prefix=self.prefix).lower()
        key: str = self._find_key(key=proposed_key)

        if key not in self.keys() or not self.__getitem__(key=key):
            from post_processing.enums import Verbosity
            verbosity: int = int(_get_env_from_os(key=key, default=Verbosity.NORMAL))
            self.__setitem__(key=key, item=verbosity)

        return int(self.__getitem__(key=key))

    @verbosity.setter
    def verbosity(self, value: int):
        proposed_key: str = "{prefix}_VERBOSITY".format(prefix=self.prefix).lower()
        key: str = self._find_key(key=proposed_key)
        if isinstance(value, str):
            value = int(float(value))

        if not isinstance(value, (int, float)):
            raise TypeError(
                f"A new verbosity value must be a string, int, or float, but was '{value}' (type={type(value)})"
            )

        value = int(value)

        self.__setitem__(key=key, item=value)

    def to_dict(self) -> typing.Dict[str, typing.Any]:
        """
        Represent all settings as a dictionary
        """
        import inspect
        values: typing.Dict[str, typing.Any] = {}

        properties: typing.List[typing.Tuple[str, property]] = inspect.getmembers(
            self.__class__,
            predicate=lambda member: isinstance(member, property)
        )

        for name, prop in properties:
            property_value: typing.Any = prop.fget(self)
            values[name] = property_value

        return values


settings = _Settings()
"""Application wide settings"""

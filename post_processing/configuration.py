"""
Common settings used within and without the application
"""
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


class _Settings(UserDict):
    """
    An access point for application and environment settings
    """
    def __init__(self, initial_values: typing.Mapping = None, **kwargs):
        super().__init__()
        for key, value in os.environ.items():
            self.__setitem__(key=key.lower(), item=value)

        for initial_key, initial_value in (initial_values or {}).items():
            self.__setitem__(key=initial_key.lower(), item=initial_value)

        for keyword, argument in kwargs.items():
            self.__setitem__(key=keyword, item=argument)

    @property
    def prefix(self) -> str:
        """
        The prefix of important application environment parameters
        """
        return "PP"
    
    @property
    def debug(self) -> bool:
        """
        Whether this is running in debug mode
        """
        key: str = "{prefix}_debug".format(prefix=self.prefix).lower()
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
        key: str = "{prefix}_date_format".format(prefix=self.prefix).lower()

        if key not in self.keys() or not self.__getitem__(key=key):
            self.__setitem__(key=key, item=_DEFAULT_DATE_FORMAT)

        return self.__getitem__(key=key)
    
    @property
    def log_format(self) -> str:
        """
        How logs should be formatted
        """
        key: str = "{prefix}_log_format".format(prefix=self.prefix).lower()

        if key not in self.keys() or not self.__getitem__(key=key) or not isinstance(self.__getitem__(key=key), str):
            self.__setitem__(key=key, item=_DEFAULT_LOG_FORMAT)

        return self.__getitem__(key=key)

    @property
    def application_path(self) -> pathlib.Path:
        """
        Get the root of this application
        """
        import post_processing
        return pathlib.Path(post_processing.__file__).parent.parent

    
    @property
    def resource_path(self) -> pathlib.Path:
        """
        Where to find external resources
        """
        key: str = "{prefix}_resource_path".format(prefix=self.prefix).lower()

        if key not in self.keys() or not self.__getitem__(key=key) or not isinstance(self.__getitem__(key=key), (pathlib.Path, str)):
            path: pathlib.Path = self.application_path / "resources"
            
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
    def logging_config_path(self) -> pathlib.Path:
        """
        The intended path to a logging config
        """
        key: str = "{prefix}_log_config_path".format(prefix=self.prefix).lower()

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
        
        return self.__getitem__(key=key)

    @logging_config_path.setter
    def logging_config_path(self, value: pathlib.Path):
        """
        The setter for logging_config_path
        """
        key: str = "{prefix}_log_config_path".format(prefix=self.prefix).lower()
        self.__setitem__(key=key, item=value)

    @property
    def intermediate_directory(self) -> pathlib.Path:
        """
        Where generated products that serve as input for other products should be written
        """
        key: str = "{prefix}_intermediate_directory".format(prefix=self.prefix).lower()

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

    @property
    def output_directory(self) -> typing.Optional[pathlib.Path]:
        """
        A preconfigured location for where to put output files
        """
        key: str = "{prefix}_output_directory".format(prefix=self.prefix).lower()

        if key not in self.keys() or not self.__getitem__(key=key):
            return None
        elif isinstance(self.__getitem__(key=key), str):
            self.__setitem__(key=key, item=pathlib.Path(self.__getitem__(key=key)))
        elif not isinstance(self.__getitem__(key=key), pathlib.Path):
            raise TypeError(
                "The '{key}' setting is invalid it must be a path to a directory but was instead '{value}' (type={value_type})".format(
                    key=key.upper(),
                    value=self.__getitem__(key=key),
                    value_type=type(self.__getitem__(key=key))
                )
            )
        elif self.__getitem__(key=key).is_file():
            raise FileExistsError(
                "The '{key}' setting is invalid - it must be a path to a directory but was a path to a file".format(
                    key=key
                )
            )

        output_directory = self.__getitem__(key=key)

        if not output_directory.exists():
            output_directory.mkdir(parents=True, exist_ok=True)

        return output_directory

settings = _Settings()
"""Application wide settings"""

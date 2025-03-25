"""
Common settings used within and without the application
"""
import typing
import os
import pathlib

from collections import UserDict


_DEFAULT_DEBUG_SETTING: bool = False
_DEFAULT_DATE_FORMAT: str = "%Y-%m-%d %H:%M:%S%z"
_DEFAULT_LOG_FORMAT: str = "[%(asctime)s] %(name)s %(levelname)s %(filename)s #%(lineno)d: %(message)s"


class _Settings(UserDict):
    """
    An access point for application and environment settings
    """
    def __init__(self, initial_values: typing.Mapping = None, **kwargs):
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
            self.__setitem__(key=key, value=_DEFAULT_DATE_FORMAT)

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
    def resource_path(self) -> pathlib.Path:
        """
        Where to find external resources
        """
        key: str = "{prefix}_resource_path".format(prefix=self.prefix).lower()

        if key not in self.keys() or not self.__getitem__(key=key) or not isinstance(self.__getitem__(key=key), (pathlib.Path, str)):
            path: pathlib.Path = pathlib.Path(__file__)

            while not path.is_dir() or not path.name == "post_processing":
                path = path.parent

            path = path.parent
            path = path / "resources"
            
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

settings = _Settings()
"""Application wide settings"""
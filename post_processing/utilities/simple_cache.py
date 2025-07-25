"""
Defines a very simple cache that does not require hashing
"""
import typing
import threading
import logging
import pathlib

from datetime import datetime

LOGGER: logging.Logger = logging.getLogger(pathlib.Path(__file__).stem)

VT = typing.TypeVar("VT")
ParamSpec = typing.ParamSpec("ParamSpec")

SENTINEL = object()


class CacheEntry(typing.Generic[VT]):
    def __init__(self, cache_lock: threading.RLock, key: typing.Tuple[typing.Any, ...], value: VT) -> None:
        self._cache_lock = cache_lock
        self._key: typing.Tuple[typing.Any, ...] = key
        self._value: VT = value
        self._last_access: datetime = datetime.now()

    @property
    def key(self) -> typing.Tuple[typing.Any, ...]:
        return self._key

    @property
    def value(self) -> VT:
        with self._cache_lock:
            self._last_access = datetime.now()
            return self._value

    def release(self):
        if self._value is None:
            return

        if hasattr(self._value, "close") and callable(self._value.close):
            try:
                self._value.close()
            except:
                LOGGER.debug(f"Could not close {self.value} upon cache release")

    def __eq__(self, other: typing.Any) -> bool:
        if not isinstance(other, self.__class__):
            raise TypeError(f"Cannot compare a {self.__class__.__name__} with a {other.__class__.__name__}")

        return self._key == other._key

    def __gt__(self, other: typing.Any) -> bool:
        if not isinstance(other, self.__class__):
            raise TypeError(f"Cannot compare a {self.__class__.__name__} with a {other.__class__.__name__}")

        return self._last_access > other._last_access

    def __ge__(self, other: typing.Any) -> bool:
        return self == other or self > other

    def __lt__(self, other: typing.Any) -> bool:
        return not (self >= other)

    def __le__(self, other: typing.Any) -> bool:
        return not (self > other)


class SimpleCache(typing.Generic[VT]):
    def __init__(self, function: typing.Callable[ParamSpec, VT], *, max_size: int = 0):
        self.__lock: threading.RLock = threading.RLock()
        self.__max_size: int = max_size
        self.__function: typing.Callable[ParamSpec, VT] = function
        self.__values: typing.List[CacheEntry[VT]] = []

    def search(self, *args, **kwargs) -> typing.Optional[CacheEntry[VT]]:
        key: typing.Tuple[typing.Tuple[typing.Any, ...], typing.Mapping[str, typing.Any]] = (args, kwargs)

        for entry in self.__values:
            if key == entry.key:
                return entry

        return None

    def evict(self):
        with self.__lock:
            earliest_entry: CacheEntry[VT] = min(self.__values)
            self.__values.remove(earliest_entry)
            earliest_entry.release()

    def __call__(self, *args, **kwargs) -> VT:
        preexisting_entry: typing.Optional[CacheEntry[VT]] = self.search(*args, **kwargs)

        if preexisting_entry is not None:
            return preexisting_entry.value

        key: typing.Tuple[typing.Tuple[typing.Any, ...], typing.Mapping[str, typing.Any]] = (args, kwargs)

        result: VT = self.__function(*args, **kwargs)

        new_entry: CacheEntry[VT] = CacheEntry(cache_lock=self.__lock, key=key, value=result)

        with self.__lock:
            if not self.search(*args, **kwargs):
                self.__values.append(new_entry)

        if 0 < self.__max_size < len(self.__values):
            self.evict()

        return result

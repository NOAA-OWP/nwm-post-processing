"""
Defines a very simple cache that does not require hashing
"""
import typing
import threading
import logging
import pathlib
import dataclasses

from datetime import datetime

LOGGER: logging.Logger = logging.getLogger(pathlib.Path(__file__).stem)

VT = typing.TypeVar("VT")
ParamSpec = typing.ParamSpec("ParamSpec")

SENTINEL = object()


@dataclasses.dataclass
class KeyTuple:
    args: tuple[typing.Any, ...]
    kwargs: typing.Mapping[str, typing.Any]

    def __eq__(self, other: typing.Any) -> bool:
        if not isinstance(other, self.__class__):
            return False

        return self.args == other.args and self.kwargs == other.kwargs

    def __str__(self):
        representation: str = ""

        if self.args:
            representation += ", ".join(map(str, self.args))

        if self.kwargs:
            if self.args:
                representation += ", "

            representation += ", ".join(map(lambda x: "%s=%s" % x, self.kwargs.items()))

        return representation



class CacheEntry(typing.Generic[VT]):
    def __init__(self, cache_lock: threading.RLock, key: KeyTuple, value: VT) -> None:
        self._cache_lock = cache_lock
        self._key: KeyTuple = key
        self._value: VT = value
        self._last_access: datetime = datetime.now()

    @property
    def key(self) -> KeyTuple:
        return self._key

    @property
    def value(self) -> VT:
        with self._cache_lock:
            self._last_access = datetime.now()
            return self._value

    @property
    def last_accessed(self) -> datetime:
        return self.last_accessed

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
            return False

        return self._key == other._key

    def __gt__(self, other: typing.Any) -> bool:
        if not isinstance(other, self.__class__):
            return False

        return self._last_access > other._last_access

    def __ge__(self, other: typing.Any) -> bool:
        return self == other or self > other

    def __lt__(self, other: typing.Any) -> bool:
        return not (self >= other)

    def __le__(self, other: typing.Any) -> bool:
        return not (self > other)


class SimpleCache(typing.Generic[VT]):
    def __init__(
            self,
            function: typing.Callable[ParamSpec, VT],
            *,
            invalidator_function: typing.Callable[[CacheEntry[VT]], bool] = None,
            max_size: int = 0
    ):
        if invalidator_function is None:
            def invalidator_function(entry: CacheEntry[VT]) -> bool:
                return False

        self.__lock: threading.RLock = threading.RLock()
        self.__max_size: int = max_size
        self.__function: typing.Callable[ParamSpec, VT] = function
        self.__values: list[CacheEntry[VT]] = []
        self.__should_invalidate: typing.Callable[[CacheEntry[VT]], bool] = invalidator_function

    def search(self, *args, **kwargs) -> typing.Optional[CacheEntry[VT]]:
        key: tuple[tuple[typing.Any, ...], typing.Mapping[str, typing.Any]] = (args, kwargs)

        for entry in self.__values:
            if key == entry.key:
                return entry

        return None

    def evict(self, entry: typing.Optional[CacheEntry[VT]] = None):
        with self.__lock:
            if entry is None:
                entry: CacheEntry[VT] = min(self.__values)
            self.__values.remove(entry)
            entry.release()

    def add(self, result: VT, args: tuple = None, kwargs: typing.Mapping[str, typing.Any] = None) -> None:
        if args is None:
            args = tuple()

        if kwargs is None:
            kwargs = {}

        key: KeyTuple = KeyTuple(args=args, kwargs=kwargs)
        new_entry: CacheEntry[VT] = CacheEntry(cache_lock=self.__lock, key=key, value=result)

        with self.__lock:
            if not self.search(*args, **kwargs):
                self.__values.append(new_entry)

        if 0 < self.__max_size < len(self.__values):
            self.evict()

    def __call__(self, *args, **kwargs) -> VT:
        preexisting_entry: typing.Optional[CacheEntry[VT]] = self.search(*args, **kwargs)

        if preexisting_entry is not None and not self.__should_invalidate(preexisting_entry):
            return preexisting_entry.value
        elif preexisting_entry is not None:
            self.evict(preexisting_entry)

        result: VT = self.__function(*args, **kwargs)

        self.add(args=args, kwargs=kwargs, result=result)

        return result


def simple_cache(
        *,
        invalidator_function: typing.Callable[[CacheEntry[VT]], bool] = None,
        max_size: int = 0
) -> typing.Callable[[typing.Callable[ParamSpec, VT]], typing.Callable[ParamSpec, VT]]:
    def decorator(function: typing.Callable[ParamSpec, VT]) -> typing.Callable[ParamSpec, VT]:
        cached_function: SimpleCache[VT] = SimpleCache(function, invalidator_function=invalidator_function, max_size=max_size)
        return typing.cast(typing.Callable[ParamSpec, VT], cached_function)
    return decorator

"""
Base classes used to describe work distribution objects
"""
import dataclasses
import typing
import abc
import pathlib
import os

from post_processing.work import exceptions
from post_processing.configuration import settings
from post_processing.interfaces.work import PendingTaskResult

T = typing.TypeVar("T")


def get_pending_task_result() -> PendingTaskResult:
    """
    Get the proper implementation of a future task
    """
    from post_processing.work import communication

    if communication.COMMUNICATE_VIA_THREADS:
        import concurrent.futures
        return concurrent.futures.Future()
    if communication.COMMUNICATE_VIA_PROCESSES:
        raise NotImplementedError("Cross Process Communication has not been implemented")
    if communication.COMMUNICATE_VIA_PROCESSES:
        raise NotImplementedError("MPI Communication has not been implemented")

    raise RuntimeError("No communication strategy is available")


def get_stack(depth: int = 4) -> str:
    """
    Get a short stack trace
    """
    import traceback
    return "".join(traceback.format_stack(limit=depth)[:-1])


@dataclasses.dataclass
class DataTask(typing.Generic[T], abc.ABC):
    """
    The base class for tasks that may be scheduled through the gateway
    """
    target: typing.Union[pathlib.Path, list[pathlib.Path]]
    engine: str = dataclasses.field(default=settings.default_netcdf_engine, kw_only=True)
    kwargs: dict[str, typing.Any] = dataclasses.field(default_factory=dict, kw_only=True)
    future: PendingTaskResult[T] = dataclasses.field(default_factory=get_pending_task_result, kw_only=True)
    _stack: str = dataclasses.field(default_factory=get_stack, kw_only=True, init=False, hash=False, compare=False, repr=False)

    @classmethod
    def get_associated_error_type(cls) -> typing.Type[exceptions.GatewayError]:
        """
        The type of error to throw if the task is interrupted
        """
        return exceptions.GatewayError

    @abc.abstractmethod
    def __call__(self) -> T:
        ...

    def execute(self) -> T:
        return self()

    @property
    def explanation(self) -> str:
        """
        An explanation of what is getting called and from where
        """
        return f"'{self}' Invoked via:{os.linesep}{self._stack}"

    @property
    def status(self) -> str:
        """
        The current status of the scheduled operation
        """
        if self.future.cancelled():
            return "Cancelled"
        if self.future.running():
            return "Running"
        if self.future.done():
            return "Complete"
        return "Pending"

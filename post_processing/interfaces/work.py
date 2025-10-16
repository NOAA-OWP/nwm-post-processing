"""
Protocols for generic communication objects
"""
import typing

T = typing.TypeVar('T')

@typing.runtime_checkable
class PendingTaskResult(typing.Protocol[T]):
    """
    An abstraction for some task that may be performed outside the normal flow of operations - does not need to be
    a concurrent.futures.Future but follows the same functionality
    """
    def cancel(self):
        ...

    def cancelled(self):
        ...

    def running(self):
        ...

    def done(self):
        ...

    def add_done_callback(self, fn):
        ...

    def result(self, timeout=None) -> T:
        ...

    def exception(self, timeout=None) -> BaseException:
        ...

    def set_running_or_notify_cancel(self):
        ...

    def set_result(self, result: T):
        ...

    def set_exception(self, exception: BaseException):
        ...

@typing.runtime_checkable
class Signal(typing.Protocol):
    """
    An object that may be used as a general thread or process wide signal
    """
    def is_set(self):
        """
        Whether the signal is set
        """

    def set(self):
        """
        Change the state of the flag so that it is marked as being set
        """

    def clear(self):
        """
        Clear the set state of the signal
        """

    def wait(self, timeout=None):
        """
        Block until the signal is set to true
        """

@typing.runtime_checkable
class TaskQueue(typing.Protocol[T]):
    """
    An interface for the basic communication queue that will be used to communicate across concurrent systems
    """
    def __init__(self, maxsize: int = 0):
        """
        Constructor

        :param maxsize: The maximum number of items that may live in the queue until 'put' blocks
        """

    def get(self, block=True, timeout=None) -> T:
        """
        Pull a task out of the queue
        """

    def get_nowait(self) -> T:
        """
        Pull a task, but don't wait for one to arrive if one isn't present
        """

    def put(self, item: T, block=True, timeout=None):
        """
        Put an item into the queue
        """

    def qsize(self):
        ...

    def empty(self):
        ...

"""
Defines logic for a thread that will handle the writing of netcdf data
"""
import queue
import typing
import logging
import threading
import atexit
import os
import abc

from post_processing.utilities.logging import get_logger
from post_processing.configuration import settings
from post_processing.work.tasks.base import DataTask
from post_processing.work.tasks.base import PendingTaskResult
from post_processing.work import exceptions
from post_processing.work import communication

LOGGER: logging.Logger = get_logger(__file__)

T = typing.TypeVar('T')
FunctionParameters = typing.ParamSpec("FunctionParameters")

DEFAULT_QUEUE_LENGTH: int = int(float(os.environ.get(f"{settings.prefix}_NETCDF_QUEUE_LENGTH", 4)))
DEFAULT_WAIT_SECONDS: float = float(os.environ.get(f"{settings.prefix}_NETCDF_WAIT_SECONDS", 0.5))


class Gateway(abc.ABC):
    """
    An IO handler for Netcdf data that trips over itself when threaded within the same process
    """
    def __init__(
        self,
        queue_length: int = DEFAULT_QUEUE_LENGTH,
        wait_seconds: float = DEFAULT_WAIT_SECONDS,
    ):
        self.__wait_seconds: float = wait_seconds
        """How long to wait for a task in the queue before repolling"""
        self._should_operate: communication.Signal = communication.get_signal()
        """Whether the loop should be running"""
        self._queue: communication.TaskQueue = communication.get_queue(maxsize=queue_length)
        """A thread-safe queue of jobs to operate on"""

        atexit.register(self.shutdown)

    @property
    @abc.abstractmethod
    def running(self) -> bool:
        """Whether the gateway is up and running"""
        ...

    def enqueue(self, task: DataTask[T]) -> PendingTaskResult[T]:
        if not isinstance(task, DataTask):
            raise TypeError(
                f"Cannot enqueue '{task}' (type={type(task)}) - it must be an instance of a "
                f"{DataTask.__class__.__qualname__}"
            )

        submitted: bool = False

        while self._should_operate.is_set() and not submitted:
            try:
                self._queue.put(task, block=True, timeout=self.__wait_seconds)
                submitted = True
            except queue.Full:
                continue

        if not submitted:
            raise RuntimeError(
                f"Could not queue '{task}'. The gateway was shut down before it had a chance to submit the job"
            )

        if settings.this_is_very_verbose:
            LOGGER.debug(f"The task '{task}' has been queued")
        return task.future

    @abc.abstractmethod
    def start(self) -> None:
        """
        Start the IO thread if it is not already running
        """
        ...

    @abc.abstractmethod
    def shutdown(self, timeout: float = 5.0) -> None:
        """
        Shut down the gateway and stop polling for jobs

        :param timeout: The amount of seconds to wait for the polling thread to complete
        """
        ...

    def listen(self):
        """
        Poll the internal queue and process all IO operations that are submitted
        """
        if not self._should_operate.is_set():
            raise RuntimeError(
                f"Cannot start the job cycle for writing NetCDF files - "
                f"it has either not been initialized or has been shut down"
            )

        while self._should_operate.is_set():
            try:
                job: DataTask | None = self._queue.get(timeout=self.__wait_seconds)
            except queue.Empty:
                continue

            if job is None:
                break

            job_is_still_running: bool = job.future.set_running_or_notify_cancel()
            if not job_is_still_running:
                LOGGER.debug(f"{job} is no longer running")
                continue

            try:
                result = job.execute()
                job.future.set_result(result)
            except KeyboardInterrupt:
                self.shutdown()
                break
            except BaseException as exception:
                job.future.set_exception(exception)
            LOGGER.debug(f"{job} is complete")


class ThreadedGateway(Gateway):
    """
    An IO handler for Netcdf data that trips over itself when threaded
    """
    def __init__(
        self,
        queue_length: int = DEFAULT_QUEUE_LENGTH,
        wait_seconds: float = DEFAULT_WAIT_SECONDS,
    ):
        super().__init__(queue_length=queue_length, wait_seconds=wait_seconds)
        self.__lock: threading.RLock = threading.RLock()
        """A lock that controls start up/shutdown access"""
        self.__thread: threading.Thread | None = None
        """The thread that will handle the polling"""

        atexit.register(self.shutdown)

    @property
    def running(self) -> bool:
        """Whether the gateway is up and running"""
        with self.__lock:
            return self.__thread is not None and self.__thread.is_alive()

    def start(self) -> None:
        """
        Start the IO thread if it is not already running
        """
        with self.__lock:
            if self.__thread is None or not self.__thread.is_alive():
                self._should_operate.set()
                self.__thread = threading.Thread(
                    target=self.listen,
                    name=self.__class__.__name__
                )
                self.__thread.start()

    def shutdown(self, timeout: float = 5.0) -> None:
        """
        Shut down the gateway and stop polling for jobs

        :param timeout: The amount of seconds to wait for the polling thread to complete
        """
        with self.__lock:
            if self.__thread is not None and self.__thread.is_alive():
                LOGGER.debug(f"Shutting down {self.__class__.__name__}")
                self._should_operate.clear()

                # Try to dump all passed in jobs
                while True:
                    try:
                        job: typing.Optional[DataTask] = self._queue.get_nowait()

                        if job is None:
                            error_class: typing.Type[IOError] = exceptions.GatewayError
                        elif isinstance(job, DataTask):
                            error_class: typing.Type[IOError] = job.get_associated_error_type()
                        else:
                            error_class: typing.Type[Exception] = TypeError

                        job.future.set_exception(
                            error_class(f"The operation to write '{job.target}' was cancelled")
                        )
                    except queue.Empty:
                        break

                self.__thread.join(timeout=timeout)
                del self.__thread
                self.__thread = None

def get_gateway(queue_length: int = DEFAULT_QUEUE_LENGTH, wait_seconds: float = DEFAULT_WAIT_SECONDS) -> Gateway:
    if communication.COMMUNICATE_VIA_THREADS:
        return ThreadedGateway(queue_length=queue_length, wait_seconds=wait_seconds)
    if communication.COMMUNICATE_VIA_PROCESSES:
        raise NotImplementedError(
            "Cannot create a communication gateway - process communication has not been implemented"
        )
    if communication.COMMUNICATE_VIA_NODES:
        raise NotImplementedError(
            "Cannot create a communication gateway - node communication has not been implemented"
        )
    raise RuntimeError(
        f"Cannot create a communication gateway - no communication method has been specified"
    )

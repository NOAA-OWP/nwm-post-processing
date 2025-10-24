"""
Defines logic for a thread that will handle the writing of netcdf data
"""
import queue
import typing
import logging
import threading
import os
import abc

from post_processing.utilities.logging import get_logger
from post_processing.configuration import settings
from post_processing.work.tasks.base import DataTask, DyeTask
from post_processing.work.tasks.base import PendingTaskResult
from post_processing.work import exceptions
from post_processing.work import communication

LOGGER: logging.Logger = get_logger(__file__)

T = typing.TypeVar('T')
FunctionParameters = typing.ParamSpec("FunctionParameters")

# TODO: Make this a system setting
DEFAULT_QUEUE_LENGTH: int = int(float(os.environ.get(f"{settings.prefix}_NETCDF_QUEUE_LENGTH", 4)))

# TODO: Make this a system setting
DEFAULT_WAIT_SECONDS: float = float(os.environ.get(f"{settings.prefix}_NETCDF_WAIT_SECONDS", 0.5))


class Gateway(abc.ABC):
    """
    An IO handler for Netcdf data that trips over itself when threaded within the same process
    """
    def __init__(
        self,
        queue_length: int = DEFAULT_QUEUE_LENGTH,
        wait_seconds: float = DEFAULT_WAIT_SECONDS,
        run_unqueable_tasks: bool = True,
    ):
        self.__wait_seconds: float = wait_seconds
        """How long to wait for a task in the queue before repolling"""
        self._should_operate: communication.Signal = communication.get_signal()
        """Whether the loop should be running"""
        self._queue: communication.TaskQueue = communication.get_queue(maxsize=queue_length)
        """A thread-safe queue of jobs to operate on"""
        self._run_unqueable_tasks: bool = run_unqueable_tasks

    @property
    @abc.abstractmethod
    def running(self) -> bool:
        """Whether the gateway is up and running"""
        ...

    @abc.abstractmethod
    def can_queue_task(self, task: DataTask[T] | None) -> bool:
        ...
    # This is essentially concurrent.futures.Executor.submit. This entire thing can probably be represented by an Executor
    def enqueue(self, task: DataTask[T] | None) -> PendingTaskResult[T]:
        if task is not None and not isinstance(task, DataTask):
            raise TypeError(
                f"Cannot enqueue '{task}' (type={type(task)}) - it must be an instance of a "
                f"{DataTask.__class__.__qualname__}"
            )

        submitted: bool = False

        while self._should_operate.is_set() and not submitted:
            if not self.can_queue_task(task=task):
                if self._run_unqueable_tasks:
                    LOGGER.warning(f"Cannot queue '{task}' - running directly instead")
                    task = self.execute_task(task=task, dye_encountered=True)
                    return task.future
                else:
                    raise RuntimeError(f"Cannot queue '{task}' within {self.__class__.__qualname__}")
            try:
                if task is None:
                    LOGGER.debug(f"Enqueue 'None' in the gateway - this will close it.")
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

        dye_encountered: bool = False
        empty_encounters: int = 0
        empty_encounter_threshold: int = 10

        while self._should_operate.is_set():
            try:
                job: DataTask | None = self._queue.get(timeout=self.__wait_seconds)
                empty_encounters = 0
            except queue.Empty:
                if dye_encountered:
                    empty_encounters += 1
                    if empty_encounters % empty_encounter_threshold == 0:
                        LOGGER.info(f"{empty_encounters} polls without a job")
                continue

            if job is None:
                break

            if isinstance(job, DyeTask):
                dye_encountered = True
                LOGGER.info(f"First encountered the dye. The queue size is: {self._queue.qsize()}")
                continue

            try:
                self.execute_task(task=job, dye_encountered=dye_encountered)
            except KeyboardInterrupt:
                self.shutdown()
                break
            except BaseException as exception:
                job.future.set_exception(exception)
            if settings.this_is_very_verbose:
                LOGGER.debug(f"Completed: {job}")

    def execute_task(self, task: DataTask[T], dye_encountered: bool = False) -> DataTask[T]:
        task_is_still_running: bool = task.future.set_running_or_notify_cancel()
        if not task_is_still_running:
            LOGGER.debug(f"{task} is no longer running")
            return task
        if dye_encountered:
            LOGGER.debug(f"Executing '{task}'")

        result: T = task.execute()
        task.future.set_result(result)
        return task


class ThreadedGateway(Gateway):
    """
    An IO handler for Netcdf data that trips over itself when threaded
    """

    def can_queue_task(self, task: DataTask[T] | None) -> bool:
        if not isinstance(self.__thread, threading.Thread) or not self.__thread.is_alive():
            raise RuntimeError(f"Cannot queue '{task}' within {self.__class__.__qualname__} - it is not running")
        current_thread: threading.Thread = threading.current_thread()
        if current_thread.ident == self.__thread.ident:
            return False
        in_gateway_thread: bool = current_thread.ident == self.__thread.ident
        if in_gateway_thread:
            LOGGER.debug(f"Cannot queue '{task}' in '{self}' from within the gateway thread")
        return not in_gateway_thread

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
        if settings.this_is_very_verbose:
            LOGGER.debug(f"Waiting for the lock in the gateway to shutdown")
        with self.__lock:
            if self.__thread is not None and self.__thread.is_alive():
                if settings.this_is_verbose:
                    LOGGER.debug(f"Shutting down {self.__class__.__name__}")
                self._should_operate.clear()
                try:
                    self.enqueue(None)
                except:
                    LOGGER.debug(f"Could not enqueue 'None' - {self.__class__.__name__} must be already shutting down")

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

                        if job is None:
                            message = f"The {self.__class__.__name__} was instructed to close"
                        else:
                            message = f"The operation '{job}' was cancelled"

                        job.future.set_exception(
                            error_class(message)
                        )
                    except queue.Empty:
                        break

                if settings.this_is_verbose:
                    LOGGER.debug(f"Waiting for the gateway thread to close")
                self.__thread.join(timeout=timeout)
                if self.__thread.is_alive():
                    LOGGER.warning(f"The gateway thread has either joined or timed out: {self.__thread}")
                del self.__thread
                self.__thread = None
            else:
                if settings.this_is_verbose:
                    LOGGER.debug(f"No thread found to shut down")

def get_gateway(queue_length: int = DEFAULT_QUEUE_LENGTH, wait_seconds: float = DEFAULT_WAIT_SECONDS) -> Gateway:
    """
    Get the appropriate gateway type for the configured type of concurrency

    :param queue_length: The number of items allowed in the queue. Threads will block when there are too many items
    in order to reduce pressure
    :param wait_seconds: The amount of seconds to wait for the polling thread to complete
    :returns: The appropriate gateway implementation
    """
    if communication.COMMUNICATE_VIA_THREADS:
        return ThreadedGateway(queue_length=queue_length, wait_seconds=wait_seconds)
    if communication.COMMUNICATE_VIA_PROCESSES:
        raise NotImplementedError(
            "Cannot create a communication gateway - process communication has not been implemented"
        )
    if communication.COMMUNICATE_VIA_NODES:
        # this is going to be the mpi4py.futures executor object
        raise NotImplementedError(
            "Cannot create a communication gateway - node communication has not been implemented"
        )
    raise RuntimeError(
        f"Cannot create a communication gateway - no communication method has been specified"
    )

"""
Defines logic for a thread that will handle the writing of netcdf data
"""
import queue
import typing
import collections.abc as generic
import pathlib
import logging
import threading
import dataclasses
import atexit
import os

# TODO: Evaluate collecting save operations and report to logs when save time extends beyond the 90th-95th percentile
#from collections import Counter

from queue import Queue
from concurrent.futures import Future

import xarray

from post_processing.utilities.logging import get_logger
from post_processing.configuration import settings

LOGGER: logging.Logger = get_logger(__file__)


DEFAULT_QUEUE_LENGTH: int = int(float(os.environ.get(f"{settings.prefix}_NETCDF_QUEUE_LENGTH", 8)))
DEFAULT_WAIT_SECONDS: float = float(os.environ.get(f"{settings.prefix}_NETCDF_WAIT_SECONDS", 0.5))
TMP_FILE_SUFFIX: str = ".incomplete"

class OperationCancelledByGatewayError(IOError):
    """General purpose error for when an operation failed or was cancelled"""

class WriteCancelledByGatewayError(OperationCancelledByGatewayError):
    """Exception for when NetCDF Writing was cancelled"""

class LoadCanceledByGatewayError(OperationCancelledByGatewayError):
    """Exception for when NetCDF Loading was cancelled"""

@dataclasses.dataclass
class SaveTask:
    """
    The information needed to save and tell the caller that writing is complete
    """
    target: pathlib.Path
    data: xarray.Dataset
    write_kwargs: dict[str, typing.Any] = dataclasses.field(default_factory=dict)
    future: Future[pathlib.Path] = dataclasses.field(default_factory=Future)

    def __str__(self):
        return f"Save to {self.target}: {self.status}"

    @property
    def status(self) -> str:
        if self.future.cancelled():
            return "Cancelled"
        if self.future.running():
            return "Running"
        if self.future.done():
            return "Complete"
        return "Pending"


@dataclasses.dataclass
class LoadTask:
    """
    The information needed to load xarray data from disk
    """
    target: pathlib.Path | generic.Sequence[pathlib.Path]
    full_load: bool = dataclasses.field(default=False)
    engine: str = dataclasses.field(default=settings.default_netcdf_engine)
    load_kwargs: dict[str, typing.Any] = dataclasses.field(default_factory=dict)
    future: Future[xarray.Dataset] = dataclasses.field(default_factory=Future)

    def __str__(self):
        return f"Load {self.target}: {self.status}"

    @property
    def status(self) -> str:
        if self.future.cancelled():
            return "Cancelled"
        if self.future.running():
            return "Running"
        if self.future.done():
            return "Complete"
        return "Pending"

class NetcdfGateway:
    """
    An IO handler for Netcdf data that trips over itself when threaded
    """
    def __init__(
        self,
        queue_length: int = DEFAULT_QUEUE_LENGTH,
        wait_seconds: float = DEFAULT_WAIT_SECONDS,
    ):
        self.__wait_seconds: float = wait_seconds
        """How long to wait for a task in the queue before repolling"""
        self.__should_operate: threading.Event = threading.Event()
        """Whether the loop should be running"""
        self.__queue: Queue[SaveTask | LoadTask] = Queue(maxsize=queue_length)
        """A thread-safe queue of jobs to operate on"""
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

    def load(
        self,
        target: pathlib.Path,
        load_kwargs: dict[str, typing.Any] = None,
        full_load: bool = False,
        engine: str = settings.default_netcdf_engine,
    ) -> Future[xarray.Dataset]:
        """
        Submit a job to load data from disk

        :param target: Where to find the data to load
        :param load_kwargs: Extra keyword arguments to feed the low level loader function
        :param full_load: Whether to load the entire netcdf dataset into memory and kill the connection
        :param engine: What engine to use to load the netcdf file
        """
        if self.__thread is None:
            raise RuntimeError(
                f"Cannot load Netcdf data - the Gateway has not been opened yet"
            )

        if load_kwargs is None:
            load_kwargs = {}

        submitted: bool = False
        task: LoadTask = LoadTask(
            target=target,
            load_kwargs=load_kwargs,
            full_load=full_load,
            engine=engine,
        )

        while self.__should_operate and not submitted:
            try:
                self.__queue.put(task, block=True, timeout=self.__wait_seconds)
                submitted = True
            except queue.Full:
                continue

        if not submitted:
            raise RuntimeError(
                f"Could not load data from {target} - the gateway was shut down before it had a chance to submit the job"
            )
        return task.future


    def save(
        self,
        dataset: xarray.Dataset,
        target: pathlib.Path,
        write_kwargs: dict[str, typing.Any] = None
    ) -> Future[pathlib.Path]:
        """
        Submit a task to the writer asking it to save a netcdf dataset

        :param dataset: The data to save
        :param target: Where to save the data
        :param write_kwargs: Low level arguments for how to save the data
        :return: The future result of the save
        """
        if self.__thread is None:
            raise RuntimeError(
                f"Cannot save netcdf data to {target} - the writer has not been started"
            )

        submitted: bool = False
        task: SaveTask = SaveTask(
            target=target,
            write_kwargs=write_kwargs,
            data=dataset
        )

        while self.__should_operate.is_set() and not submitted:
            try:
                self.__queue.put(task, block=True, timeout=self.__wait_seconds)
                submitted = True
            except queue.Full:
                continue

        if not submitted:
            raise RuntimeError(
                f"Could not write data to {target} - the gateway was shut down before it had a chance to submit the job"
            )
        LOGGER.debug(f"The task '{task}' has been queued")
        return task.future

    def start(self) -> None:
        """
        Start the IO thread if it is not already running
        """
        with self.__lock:
            if self.__thread is None or not self.__thread.is_alive():
                self.__should_operate.set()
                self.__thread = threading.Thread(
                    target=self._run_job_cycle,
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
                self.__should_operate.clear()

                # Try to dump all passed in jobs
                while True:
                    try:
                        job: SaveTask | LoadTask | None = self.__queue.get_nowait()

                        if job is None:
                            error_class: typing.Type[IOError] = OperationCancelledByGatewayError
                        elif isinstance(job, LoadTask):
                            error_class: typing.Type[IOError] = OperationCancelledByGatewayError
                        elif isinstance(job, SaveTask):
                            error_class: typing.Type[IOError] = OperationCancelledByGatewayError
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

    def _run_job_cycle(self):
        """
        Poll the internal queue and process all IO operations that are submitted
        """
        if not self.__should_operate.is_set():
            raise RuntimeError(
                f"Cannot start the job cycle for writing NetCDF files - "
                f"it has either not been initialized or has been shut down"
            )

        while self.__should_operate.is_set():
            try:
                job: SaveTask | LoadTask | None = self.__queue.get(timeout=self.__wait_seconds)
            except queue.Empty:
                continue

            if job is None:
                break

            job_is_still_running: bool = job.future.set_running_or_notify_cancel()
            if not job_is_still_running:
                continue

            try:
                if isinstance(job, LoadTask):
                    result: xarray.Dataset = self.__load(
                        target=job.target,
                        load_kwargs=job.load_kwargs,
                        full_load=job.full_load,
                        engine=job.engine
                    )
                elif isinstance(job, SaveTask):
                    result: pathlib.Path = self.__write(
                        dataset=job.data,
                        target=job.target,
                        write_kwargs=job.write_kwargs
                    )
                else:
                    raise ValueError(
                        f"Cannot process '{job}' (type={type(job)}) - handling for it does not exist"
                    )
                job.future.set_result(result)
            except KeyboardInterrupt:
                self.shutdown()
                break
            except BaseException as exception:
                job.future.set_exception(exception)

    def __load(
        self,
        target: pathlib.Path | generic.Sequence[pathlib.Path],
        load_kwargs: dict[str, typing.Any] = None,
        full_load: bool = False,
        chunks: str | dict[str, typing.Any] | None = "auto",
        engine: str = settings.default_netcdf_engine
    ) -> xarray.Dataset:
        """
        Load a netcdf file from disk

        :param target: Where the data to load exists
        :param load_kwargs: Additional arguments to feed to the lower level loading functions
        :param full_load: Whether to load all data and immediately close the netcdf file
        :param chunks: How to read chunks of data from the targetted netcdf file
        :param engine: What engine to use to read netcdf data
        :returns: The loaded dataset
        """
        if load_kwargs is None:
            load_kwargs = {}

        if "chunks" in load_kwargs:
            chunks = load_kwargs.pop("chunks")

        if isinstance(target, generic.Sequence) and len(target) == 1:
            target = target[0]

        import time
        if isinstance(target, (pathlib.Path, str)):
            maximum_retries: int = 5
            attempts: int = 0
            last_exception: typing.Optional[Exception] = None
            while attempts < maximum_retries:
                try:
                    if settings.this_is_very_verbose:
                        LOGGER.debug(f"Loading '{target}'", stack_info=True)

                    dataset: xarray.Dataset = xarray.open_dataset(target, engine=engine, chunks=chunks, **load_kwargs)
                    if full_load:
                        dataset = dataset.load()
                        dataset.close()
                    return dataset

                    # NOTE: It would be safer to load everything in full and move on, but that adds a significant
                    # performance cost. For now, full loads won't be performed by default.
                except Exception as e:
                    last_exception = e
                    last_exception.args = (f"Could not load data at '{target}'. {e.args[0]}", *e.args[1:])
                attempts += 1
                LOGGER.error(
                    f"Failed to load {target}{' due to ' + str(last_exception) if last_exception else ''}. "
                    f"Waiting and trying again..."
                )
                time.sleep(1)

            if last_exception is None:
                raise RuntimeError(f"Could not load '{target}'")
            raise last_exception
        else:
            # Your IDE may complain about the `data` parameter - it is a false positive. A sequence of paths is fine
            dataset: xarray.Dataset = xarray.open_mfdataset(
                paths=target,
                chunks=chunks,
                combine="by_coords",
                engine=engine,
                **load_kwargs
            )
            if full_load:
                dataset = dataset.load()
                dataset.close()
            return dataset


    def __write(
        self,
        dataset: xarray.Dataset,
        target: pathlib.Path,
        write_kwargs: dict[str, typing.Any] = None
    ) -> pathlib.Path:
        """
        Write a netcdf file to disk

        :param dataset: The data to write to disk
        :param target: Where to write the data
        :param write_kwargs: Additional arguments for the writer
        :returns: The path to the written object
        """
        if write_kwargs is None:
            write_kwargs = {}

        target.parent.mkdir(parents=True, exist_ok=True)
        temporary_output_path: pathlib.Path = target.parent / f"{target.name}{TMP_FILE_SUFFIX}"
        try:
            dataset.to_netcdf(temporary_output_path, **write_kwargs)
            os.replace(temporary_output_path, target)
        finally:
            temporary_output_path.unlink(missing_ok=True)
        LOGGER.debug(f"Wrote data to '{target}'")
        return target



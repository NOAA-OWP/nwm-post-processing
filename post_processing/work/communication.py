"""
Classes, functions, and objects used to communicate across threads, processes, and nodes
"""
from post_processing.interfaces.work import Signal
from post_processing.interfaces.work import TaskQueue

# TODO: Make this a system setting
COMMUNICATE_VIA_THREADS: bool = True

# TODO: Make this a system setting
COMMUNICATE_VIA_PROCESSES: bool = False

# TODO: Make this a system setting
COMMUNICATE_VIA_NODES: bool = False

def get_signal(state: bool = False) -> Signal:
    """
    Get a runtime appropriate Signal instance

    Currently only implemented for threading

    :param state: Whether the signal should be set or not
    :return: An object to use like a basic boolean signal
    """
    if COMMUNICATE_VIA_THREADS:
        import threading
        signal = threading.Event()
    elif COMMUNICATE_VIA_PROCESSES:
        import multiprocessing
        signal = multiprocessing.Event()
    elif COMMUNICATE_VIA_NODES:
        raise NotImplementedError("MPI Communication has not been implemented")
    else:
        raise ValueError("No communication approach has been signified")
    if state:
        signal.set()
    return signal


def get_queue(maxsize: int = 0) -> TaskQueue:
    """
    Get a task queue for the given concurrency approach

    :param maxsize: The maximum number of tasks that may be held before the caller is blocked
    :returns: The proper queue implementation
    """
    if COMMUNICATE_VIA_THREADS:
        import queue
        return queue.Queue(maxsize=maxsize)
    if COMMUNICATE_VIA_PROCESSES:
        import multiprocessing
        return multiprocessing.Queue(maxsize=maxsize)
    if COMMUNICATE_VIA_NODES:
        raise NotImplementedError("MPI Communication has not been implemented")
    raise ValueError("No communication approach has been signified")

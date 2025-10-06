"""
Tasks classes concerned with writing data
"""
import logging
import pathlib
import typing
import dataclasses
import os
import gc

import xarray

from post_processing.work import exceptions
from post_processing.utilities.logging import get_logger
from post_processing.work.tasks import base

LOGGER: logging.Logger = get_logger(__file__)
TMP_FILE_SUFFIX: str = ".incomplete"

@dataclasses.dataclass
class SaveTask(base.DataTask[pathlib.Path]):
    """
    The information needed to save and tell the caller that writing is complete
    """

    def __call__(self) -> pathlib.Path:
        """
        Write a netcdf file to disk
        :returns: The path to the written object
        """
        kwargs = self.kwargs.copy() or {}

        self.target.parent.mkdir(parents=True, exist_ok=True)
        temporary_output_path: pathlib.Path = self.target.parent / f"{self.target.name}{TMP_FILE_SUFFIX}"
        try:
            self.dataset.compute().to_netcdf(temporary_output_path, **kwargs)
            if self.close:
                self.dataset.close()
                del self.dataset

                # Call the garbage collector directly to try and collection the removed dataset in attempt to
                # preempt its destruction in an unmanaged thread
                gc.collect()
            os.replace(temporary_output_path, self.target)
        finally:
            temporary_output_path.unlink(missing_ok=True)
        return self.target

    dataset: xarray.Dataset
    close: bool = dataclasses.field(default=True)

    @classmethod
    def get_associated_error_type(cls) -> typing.Type[exceptions.GatewayError]:
        return exceptions.WriteCancelledByGatewayError

    def __str__(self):
        return f"Save to {self.target}: {self.status}"

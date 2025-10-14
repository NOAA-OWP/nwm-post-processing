"""
Tasks classes concerned with writing data
"""
import logging
import pathlib
import typing
import dataclasses
import os
import collections.abc as generic

import xarray

from post_processing.work import exceptions
from post_processing.utilities.logging import get_logger
from post_processing.work.tasks import base
from post_processing.interfaces.aliases import DatasetFunction

VariableParameters = typing.ParamSpec("VariableParameters")

LOGGER: logging.Logger = get_logger(__file__)
TMP_FILE_SUFFIX: str = ".incomplete"

def _write_to_disk(dataset: xarray.Dataset, target: pathlib.Path, **write_arguments) -> None:
    kwargs = write_arguments or {}
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_output_path: pathlib.Path = target.parent / f"{target.name}{TMP_FILE_SUFFIX}"
    try:
        dataset.compute().to_netcdf(temporary_output_path, **kwargs)
        os.replace(temporary_output_path, target)
    finally:
        temporary_output_path.unlink(missing_ok=True)

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
        _write_to_disk(dataset=self.dataset, target=self.target, **self.kwargs)
        return self.target

    dataset: xarray.Dataset
    close: bool = dataclasses.field(default=True)

    @classmethod
    def get_associated_error_type(cls) -> typing.Type[exceptions.GatewayError]:
        return exceptions.WriteCancelledByGatewayError

    def __str__(self):
        return f"Save to {self.target}: {self.status}"

@dataclasses.dataclass
class OperateOnDatasetTask(base.DataTask[pathlib.Path], typing.Generic[VariableParameters]):
    function: DatasetFunction[VariableParameters, "xarray.Dataset"]
    output_path: pathlib.Path
    read_arguments: typing.Dict[str, typing.Any] = dataclasses.field(default_factory=dict)
    write_arguments: typing.Dict[str, typing.Any] = dataclasses.field(default_factory=dict)

    def __call__(self) -> pathlib.Path:
        from post_processing.work.tasks.reading import _load
        read_kwargs: dict[str, typing.Any] = self.read_arguments.copy() if isinstance(self.read_arguments, generic.Mapping) else {}
        function_kwargs: dict[str, typing.Any] = self.kwargs.copy() if isinstance(self.kwargs, generic.Mapping) else {}
        write_arguments: dict[str, typing.Any] = self.write_arguments.copy() if isinstance(self.write_arguments, generic.Mapping) else {}

        if 'chunks' in read_kwargs:
            del read_kwargs['chunks']

        with _load(target=self.target, engine=self.engine, full_load=False, chunks="auto", load_kwargs=read_kwargs) as loaded_data:
            altered_data: xarray.Dataset = self.function(loaded_data, **function_kwargs)
            _write_to_disk(altered_data, target=self.output_path, **write_arguments)
            altered_data.close()
            del altered_data

        return self.output_path

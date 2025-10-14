"""
Common aliases to use throughout the application
"""
import typing
import collections.abc as generic

if typing.TYPE_CHECKING:
    import xarray

T = typing.TypeVar("T")
KT = typing.TypeVar("KT")
VT = typing.TypeVar("VT")
VariableParameters = typing.ParamSpec("VariableParameters")

DataArrayFunction = generic.Callable[typing.Concatenate["xarray.DataArray", VariableParameters], T]
DatasetFunction = generic.Callable[typing.Concatenate["xarray.Dataset", VariableParameters], T]




@typing.runtime_checkable
class DatasetMutator(typing.Protocol[VariableParameters]):
    def __call__(self, dataset: "xarray.Dataset", **kwargs: VariableParameters.kwargs) -> "xarray.Dataset":
        ...

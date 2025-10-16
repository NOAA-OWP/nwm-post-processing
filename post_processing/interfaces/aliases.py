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
"""
Represents a function whose first input is an xarray data array but may have varying positional and keyword arguments, 
and returns some value of a generic type
"""
DatasetFunction = generic.Callable[typing.Concatenate["xarray.Dataset", VariableParameters], T]
"""
Represents a function whose first input is an xarray Dataset, but may have varying positional and keyword arguments, 
and returns some value of a generic type
"""

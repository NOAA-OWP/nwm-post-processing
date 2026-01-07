"""
Provides the means and access to use masks for subsetting
"""
import logging
import pathlib
import threading
import collections.abc as generic
import dataclasses
import atexit

import xarray
import numpy

FILE_PATH: pathlib.Path = pathlib.Path(__file__)

LOGGER: logging.Logger = logging.getLogger(
    f"{FILE_PATH.parent.parent.name}.{FILE_PATH.parent.name}.{FILE_PATH.stem}"
)

def format_bytes(byte_count: int) -> str:
    """
    Convert a byte count into a human-readable string using decimal units.

    Examples
    --------
    438          -> "438B"
    9904154454   -> "9.9GB"
    """
    units = ["B", "KB", "MB", "GB", "TB", "PB", "EB"]

    value = float(byte_count)
    unit_index = 0

    # Walk up units until the value would drop below 1 in the next unit
    while value >= 1000.0 and unit_index < len(units) - 1:
        value /= 1000.0
        unit_index += 1

    if unit_index == 0:
        # For plain bytes, do not show decimals
        return f"{int(value)}{units[unit_index]}"

    # For KB and above, use up to one decimal, strip trailing .0
    numeric_text = f"{value:.1f}".rstrip("0").rstrip(".")

    return f"{numeric_text}{units[unit_index]}"



def describe_variable(name: str, data: xarray.DataArray | xarray.Variable) -> str:
    size_descriptions: list[str] = []

    for dimension, count in data.sizes.items():
        size_descriptions.append(f"{dimension}={count}")

    return f"{name}({', '.join(size_descriptions)})"


@dataclasses.dataclass
class MaskKey:
    path: pathlib.Path
    variable_name: str

    def __hash__(self) -> int:
        return hash((self.path, self.variable_name))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, MaskKey):
            return False
        return hash(self) == hash(other)

@dataclasses.dataclass
class MaskSourceMetadata:
    path: pathlib.Path
    variables: generic.Sequence[str]
    variable_names: generic.Sequence[str]
    dimensions: generic.Sequence[str]

class _MaskProvider:
    """
    A means of loading and storing critical data used for data masking
    """
    def __init__(self):
        self.__lock: threading.RLock = threading.RLock()
        self.__masks: dict[MaskKey, numpy.ndarray] = {}
        self.__mask_metadata: dict[pathlib.Path, MaskSourceMetadata] = {}
        self.__sizes: dict[pathlib.Path, dict[str, int]] = {}
        self.__variables: dict[pathlib.Path, generic.Mapping[str, str]] = {}

    @property
    def nbytes(self) -> int:
        """
        The number of bytes in the arrays currently loaded
        """
        with self.__lock:
            if not self.__masks:
                return 0

            count: int = sum(map(lambda array: array.nbytes, self.__masks.values()))
            return count

    def clean(self):
        """
        Free all possible memory from the provider
        """
        with self.__lock:
            keys: list[MaskKey] = list(self.__masks.keys())
            paths: list[pathlib.Path] = list(self.__mask_metadata.keys())
            for mask_key in keys:
                mask = self.__masks.pop(mask_key)
                mask[...] = 0
                del mask
            for path in list(self.__sizes.keys()):
                self.__sizes.pop(path)
            for path in list(self.__variables.keys()):
                self.__variables.pop(path)
            for path in list(self.__mask_metadata.keys()):
                self.__mask_metadata.pop(path)

    def __load_mask(self, path: pathlib.Path, variable: str):
        from post_processing.utilities.netcdf import load
        key: MaskKey = MaskKey(path=path, variable_name=variable)

        with self.__lock:
            if key in self.__masks and path in self.__mask_metadata:
                return

            # TODO: Use a Variable specific load function for this
            with load(target=path, full_load=True, load_kwargs=dict(chunks=None)) as mask_source:
                if variable not in mask_source:
                    raise KeyError(f"'{variable}' is not a variable within '{path}'. It may not be used as a mask")

                mask_variable: xarray.DataArray = mask_source[variable]

                if len(mask_variable.shape) == 1:
                    mask_values: numpy.ndarray = mask_variable.data
                    mask: numpy.ndarray = numpy.unique(mask_values)
                else:
                    mask: numpy.ndarray = mask_variable.data.copy()
                    mask = numpy.where(mask == 0, numpy.False_, numpy.True_)

                if mask.shape[0] == 1:
                    mask = numpy.squeeze(mask, axis=0)

                self.__masks[key] = mask
                self.__mask_metadata[path] = MaskSourceMetadata(
                    path=path,
                    variable_names=[*mask_source.coords.keys(), *mask_source.data_vars.keys()],
                    variables=[
                        f"{variable.name}({variable.sizes})"
                        for variable in [*mask_source.coords.values(),  *mask_source.data_vars.values()]
                    ],
                    dimensions=[
                        f"{dimension}={length}"
                        for dimension, length in mask_source.sizes.items()
                    ],
                )
                LOGGER.debug(
                    f"The mask at '{path}::{variable}' has been loaded into the mask provider. "
                    f"The provider now weighs in at {format_bytes(self.nbytes)}"
                )

    def get_mask(self, path: pathlib.Path, variable: str) -> numpy.ndarray:
        key: MaskKey = MaskKey(path=path, variable_name=variable)

        if key in self.__masks:
            return self.__masks[key]

        self.__load_mask(path=path, variable=variable)
        return self.__masks[key]

    def get_variables(self, path: pathlib.Path, variable: str) -> generic.Sequence[str]:
        if path not in self.__mask_metadata:
            self.__load_mask(path=path, variable=variable)
        return self.__mask_metadata[path].variables

    def get_dimensions(self, path: pathlib.Path, variable: str) -> generic.Sequence[str]:
        if path not in self.__mask_metadata:
            self.__load_mask(path=path, variable=variable)
        return self.__mask_metadata[path].dimensions

MASK_PROVIDER = _MaskProvider()

@atexit.register
def clean():
    MASK_PROVIDER.clean()

"""
Helper functions and objects used to standardize file IO operations
"""
import typing
import logging
import pathlib
from threading import RLock

from functools import lru_cache

if typing.TYPE_CHECKING:
    import xarray


LOGGER: logging.Logger = logging.getLogger(pathlib.Path(__file__).stem)
SAVED_FILES: typing.List[pathlib.Path] = []
SAVED_FILE_LOCK: RLock = RLock()

def record_saved_file(path: pathlib.Path):
    with SAVED_FILE_LOCK:
        if path in SAVED_FILES:
            LOGGER.warning(f"File '{path}' has now been saved to {len(list(filter(lambda p: p == path, SAVED_FILES)))} times")
        SAVED_FILES.append(path)

def load_netcdf(
    path: typing.Union[pathlib.Path, str, typing.Sequence[typing.Union[pathlib.Path, str]]],
    engine: typing.Union[str, typing.Literal["h5netcdf", "zarr", "netcdf4"]] = "h5netcdf",
    chunks: typing.Union[typing.Mapping[str, typing.Any], typing.Literal['auto']] = 'auto',
    **kwargs
) -> "xarray.Dataset":
    """
    Load a thread-safe, lazy netcdf file

    :param path: path to netcdf file
    :param engine: The engine to use to load the netcdf data into memory
    :param chunks: The chunks to load into memory
    :param kwargs: Keyword arguments to pass to xarray.open_dataset. See: https://docs.xarray.dev/en/stable/generated/xarray.open_dataset.html
    """
    if engine is None:
        engine = "h5netcdf"

    engine = engine.strip().lower()

    if engine not in ("h5netcdf", "zarr", "netcdf4"):
        raise ValueError(f"{engine} is not a supported engine - only 'h5netcdf' and 'zarr' are supported")

    try:
        import dask
        has_dask = True
    except ImportError:
        has_dask = False

    if has_dask and chunks is None:
        LOGGER.warning(
            f"Attempting to open a netcdf file without chunking - "
            f"lazy loading will not be supported and you are more at risk of out-of-memory errors"
        )
    elif not has_dask and chunks not in (None, 'auto'):
        LOGGER.warning(
            "Chunking as requested when loading Netcdf data but Dask is not available. "
            "Lazy loading via chunking is not supported."
        )
        chunks = None

    import xarray

    if isinstance(path, typing.Sequence) and len(path) == 1:
        path = path[0]

    if isinstance(path, (pathlib.Path, str)):
        maximum_retries: int = 5
        attempts: int = 0
        dataset: typing.Optional[xarray.Dataset] = None
        last_exception: typing.Optional[Exception] = None
        while attempts < maximum_retries and dataset is None:
            try:
                dataset: xarray.Dataset = xarray.open_dataset(path, engine=engine, chunks=chunks, **kwargs)
                break
            except Exception as e:
                last_exception = e
                last_exception.args = (f"Could not load data at '{path}'. {e.args[0]}", *e.args[1:])
            attempts += 1
            LOGGER.error(f"Failed to load {path}. Waiting and trying again...")
            import time
            time.sleep(1)
        if dataset is None:
            raise (last_exception or RuntimeError(f"Could not load '{path}'"))
    else:
        # Your IDE may complain about the `data` parameter - it is a false positive. A sequence of paths is fine
        dataset: xarray.Dataset = xarray.open_mfdataset(
            paths=path,
            chunks=chunks,
            combine="by_coords",
            engine=engine,
            **kwargs
        )

    return dataset


def load_metadata(
    path: typing.Union[pathlib.Path, str, typing.Sequence[typing.Union[pathlib.Path, str]]],
    engine: typing.Union[str, typing.Literal["h5netcdf", "zarr", "netcdf4"]] = "h5netcdf"
) -> typing.Dict[str, typing.Any]:
    """
    Get the metadata attached to a netcdf file and its variables

    Variable attributes will be prefixed by the variable name. If variable 'streamflow' has an attribute named
    'standard_name', the value will be found at 'streamflow.standard_name'

    :param path: The path (or paths) to the netcdf data to inspect
    :param engine: The engine that will load and interpret the data
    :returns: A dictionary containing all the attributes in it and on its variables. Variable attributes will be prefixed by the variable name
    """
    if isinstance(path, pathlib.Path):
        path = [path]

    metadata: typing.Dict[str, typing.Any] = {}
    for input_path in path:
        source: xarray.Dataset = load_netcdf(path=input_path, engine=engine)
        metadata.update({
            str(key): format_attribute_value(value)
            for key, value in source.attrs.items()
        })

        for coordinate_name, coordinate_data in source.coords.items():
            metadata.update(_get_variable_metadata(variable=coordinate_data))

        for variable_name, variable_data in source.data_vars.items():
            metadata.update(_get_variable_metadata(variable=variable_data))

    return metadata


def _get_variable_metadata(variable: "xarray.DataArray") -> typing.Dict[str, typing.Any]:
    import numpy
    metadata = {
        f"{variable.name}.{attribute_name}": format_attribute_value(attribute_value)
        for attribute_name, attribute_value in variable.attrs.items()
    }
    if variable.shape == (1,):
        if isinstance(variable.values, typing.Iterable):
            value: typing.Any = variable.values[0]
        else:
            value: typing.Any = variable.values

        if isinstance(value, numpy.datetime64):
            from datetime import datetime
            value: datetime = value.astype('datetime64[ms]').item()
            metadata[f"{variable.name}__date"] = value.strftime("%Y%m%d")
            metadata[f"{variable.name}__hour"] = value.hour
            metadata[f"{variable.name}__minute"] = value.minute
            metadata[f"{variable.name}__second"] = value.second
            metadata[f"{variable.name}__day"] = value.day
            metadata[f"{variable.name}__month"] = value.month
            metadata[f"{variable.name}__year"] = value.year
        else:
            metadata[str(variable.name)] = value.item()
    return metadata


def format_attribute_value(value: typing.Any) -> typing.Any:
    import numpy
    if isinstance(value, numpy.datetime64):
        value = value.astype('datetime64[ms]').item()
    elif isinstance(value, numpy.ndarray):
        value = value.tolist()
    elif hasattr(value, "item"):
        try:
            value = value.item()
        except:
            value = str(value)
    return value



def save_netcdf(
    path: typing.Union[str, pathlib.Path],
    dataset: "xarray.Dataset",
    engine: typing.Literal["h5netcdf", "scipy"] = "h5netcdf",
    **kwargs
) -> bool:
    """
    Safely save an xarray dataset to netcdf. Only saves locally.

    :param path: The path to where the data should be saved
    :param dataset: The data to save
    :param engine: The name of the netcdf engine to use to write the data
    :param kwargs: Arguments to pass to the xarray.Dataset.to_netcdf function. See: https://docs.xarray.dev/en/stable/generated/xarray.Dataset.to_netcdf.html
    :returns: Whether the netcdf file that was supposed to be saved exists
    """
    if isinstance(path, str):
        path = pathlib.Path(path)

    if isinstance(path, pathlib.Path):
        path.parent.mkdir(parents=True, exist_ok=True)
    else:
        raise TypeError(f"{path} (type={type(path)}) is not a valid path. It must be a str or pathlib.Path")

    if engine not in ("h5netcdf", "netcdf4"):
        raise ValueError(f"{engine} is not a supported engine - only 'h5netcdf' and 'netcdf4' are supported")

    dataset.to_netcdf(path=path, engine=engine, **kwargs)
    LOGGER.debug(f"Saved netcdf data to: {path}")
    record_saved_file(path=path)
    return path.is_file()

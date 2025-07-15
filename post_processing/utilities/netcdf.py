"""
Helper functions and objects used to standardize file IO operations
"""
import typing
import logging
import pathlib

if typing.TYPE_CHECKING:
    import xarray


LOGGER: logging.Logger = logging.getLogger(pathlib.Path(__file__).stem)


def load_netcdf(
    path: typing.Union[pathlib.Path, str, typing.Sequence[typing.Union[pathlib.Path, str]]],
    engine: typing.Literal["h5netcdf", "scipy", "zarr"] = "h5netcdf",
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

    if engine not in ("h5netcdf", "zarr", "scipy"):
        raise ValueError(f"{engine} is not a supported engine - only 'h5netcdf', 'zarr' and 'scipy' are supported")

    if engine == "scipy":
        LOGGER.warning(
            f"Opening the following netcdf file via the scipy engine - "
            f"C extensions and lazy evaluation will not be employed: {path}"
        )

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

    if isinstance(path, (pathlib.Path, str)):
        dataset: xarray.Dataset = xarray.open_dataset(path, engine=engine, chunks=chunks, **kwargs)
    else:
        # Your IDE may complain about the `data` parameter - it is a false positive. A sequence of paths is fine
        dataset: xarray.Dataset = xarray.open_mfdataset(
            paths=path,
            chunks=chunks,
            concat_dim="by_coords",
            engine=engine,
            **kwargs
        )

    return dataset


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

    if engine not in ("h5netcdf", "scipy"):
        raise ValueError(f"{engine} is not a supported engine - only 'h5netcdf' and 'scipy' are supported")

    dataset.to_netcdf(path=path, engine=engine, **kwargs)

    return path.is_file()

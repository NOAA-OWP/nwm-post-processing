#!/usr/bin/env python3
"""
Unit tests for data reprojection
"""
import typing
import collections.abc as generic
import unittest
import pathlib
import logging
import os

from concurrent.futures import ThreadPoolExecutor

from datetime import timedelta
from datetime import datetime

import xarray
import rasterio
import numpy

from post_processing.configuration import settings
from post_processing.transform import reproject
from post_processing.utilities import netcdf

LOGGER: logging.Logger = logging.getLogger()
LOGGER.setLevel(logging.DEBUG)

TIME_LENGTH: int = 8
TIME_OFFSET: numpy.timedelta64 = numpy.timedelta64(timedelta(hours=1))
START_TIME: numpy.datetime64 = numpy.datetime64(datetime(year=2025, month=1, day=1))

def array_to_netcdf(
    array: xarray.DataArray,
    time_variable: xarray.DataArray,
    path: pathlib.Path,
    projection: reproject.Projection
):
    """
    Convert an xarray DataArray to a Dataset and save it to disk

    :param array: The data to convert
    :param time_variable: The variable to use for time
    :param path: The path to save the data to
    :param projection: Projection data that will help build the CRS variable
    """
    overall_dataset: xarray.Dataset = xarray.Dataset(
        coords={
            "x": projection.x_values,
            "y": projection.y_values,
            "time": time_variable
        },
        data_vars={
            array.name: array,
            projection.crs_variable_name: xarray.DataArray(
                name=projection.crs_variable_name,
                data=b"",
                dims=tuple(),
                attrs=projection.crs_attributes.copy()
            )
        }
    )

    overall_dataset.to_netcdf(path=path)


class ProjectionPath:
    Mercator = settings.resource_path / "projections" / "mercator.nc"
    Lambert = settings.resource_path / "projections" / "sphere_lambert.nc"


def generate_sample_dataarray(
    name: str,
    time_array: xarray.DataArray,
    projection_path: ProjectionPath,
    seed: int = 1234,
    projection_x_variable: str = "x",
    projection_y_variable: str = "y",
    projection_crs_variable: str = "crs",
) -> xarray.DataArray:
    """
    Generate a sample dataarray for testing
    """
    random_number_generator: numpy.random.Generator = numpy.random.default_rng(seed=seed)
    projection_dataset: xarray.Dataset = netcdf.load(target=projection_path, full_load=True)
    x_variable: xarray.DataArray = projection_dataset[projection_x_variable].load()
    y_variable: xarray.DataArray = projection_dataset[projection_y_variable].load()
    crs_variable: xarray.DataArray = projection_dataset[projection_crs_variable].load()
    projection_dataset.close()

    shape: tuple[int, int, int] = (
        time_array.shape[0],
        y_variable.shape[0],
        x_variable.shape[0],
    )

    data: numpy.typing.NDArray[numpy.float32] = random_number_generator.uniform(
        low=0.0,
        high=800.0,
        size=shape
    ).astype(numpy.float32)

    new_array: xarray.DataArray = xarray.DataArray(
        name=name,
        dims=(time_array.name, y_variable.name, x_variable.name),
        coords=[time_array, y_variable, x_variable],
        data=data,
        attrs={
            "grid_mapping": crs_variable.name,
            "units": "m3 s-1",
            "long_name": f"Test Variable {name}"
        }
    )

    new_array.encoding["_FillValue"] = numpy.int32(-99900)
    new_array.encoding["dtype"] = numpy.int32
    new_array.encoding['complevel'] = numpy.uint8(3)
    new_array.encoding['zlib'] = numpy.uint8(1)
    new_array.encoding['scale_factor'] = numpy.float32(0.01)

    return new_array


class ReprojectionTest(unittest.TestCase):
    mercator_projection: reproject.Projection
    lambert_projection: reproject.Projection
    time: xarray.DataArray
    lambert_arrays: dict[str, xarray.DataArray]
    mercator_arrays: dict[str, xarray.DataArray]

    @classmethod
    def setUpClass(cls) -> None:
        cls.mercator_projection: reproject.Projection = reproject.Projection.load_file(path=ProjectionPath.Mercator)
        cls.lambert_projection: reproject.Projection = reproject.Projection.load_file(path=ProjectionPath.Lambert)
        cls.lambert_arrays = {}
        cls.mercator_arrays = {}

        cls.time: xarray.DataArray = xarray.DataArray(
            name="time",
            dims=("time",),
            data=[
                START_TIME + (offset_count * TIME_OFFSET)
                for offset_count in range(TIME_LENGTH)
            ],
            attrs={
                "long_name": "valid output time",
                "standard_time": "time"
            }
        )

        # Numbers chose randomly - length is short due to encountered SEGKILLs
        lambert_seeds: list[int] = [6825, 5530, 5130]
        mercator_seeds: list[int] = [5860, 6474, 7005]

        with ThreadPoolExecutor() as thread_pool:
            array_generation_args: list[list] = [[]] * 4
            array_generation_args[0] = [
                f"lambert_{number}" for number in range(1, len(lambert_seeds) + 1)
            ]
            array_generation_args[1] = [cls.time] * len(lambert_seeds)
            array_generation_args[2] = [cls.lambert_projection.path] * len(lambert_seeds)
            array_generation_args[3] = lambert_seeds

            start: datetime = datetime.now()
            arrays: generic.Iterator[xarray.DataArray] = thread_pool.map(
                generate_sample_dataarray,
                *array_generation_args
            )

            for array in arrays:
                cls.lambert_arrays[str(array.name)] = array

            print(f"Lambert data generated in {datetime.now() - start}")

            array_generation_args[0] = [
                f"mercator_{number}" for number in range(1, len(mercator_seeds) + 1)
            ]
            array_generation_args[2] = [cls.mercator_projection.path] * len(mercator_seeds)
            array_generation_args[3] = mercator_seeds

            start: datetime = datetime.now()
            arrays: generic.Iterator[xarray.DataArray] = thread_pool.map(
                generate_sample_dataarray,
                *array_generation_args
            )

            for array in arrays:
                cls.mercator_arrays[str(array.name)] = array

            print(f"Mercator data generated in {datetime.now() - start}")

    def test_lambert_to_mercator(self):
        """
        Test that arbitrary data can be transformed from the lambert projection to the mercator projection
        """
        start = datetime.now()
        mercator_array: xarray.DataArray = reproject.reproject_variable(
            source_variable=self.lambert_arrays['lambert_1'],
            input_projection=self.lambert_projection,
            target_projection=self.mercator_projection,
        )
        print(f"Lambert data has been converted to mercator after {datetime.now() - start}")
        #array_to_netcdf(
        #    array=lambert_array,
        #    time_variable=self.time,
        #    path=pathlib.Path.home() / "lambert_test.nc",
        #    projection=self.lambert,
        #)
        #array_to_netcdf(
        #    array=mercator_array,
        #    time_variable=self.time,
        #    path=pathlib.Path.home() / "mercator_test.nc",
        #    projection=self.mercator,
        #)
        self.assertTrue(True)

    def test_lambert_to_lambert(self):
        """
        Test that arbitrary data can maintain a projection for lambert
        """
        ...

    def test_mercator_to_lambert(self):
        """
        Test that arbitrary data can be transformed from the mercator projection to the lambert projection
        """
        start = datetime.now()
        lambert_array: xarray.DataArray = reproject.reproject_variable(
            source_variable=self.mercator_arrays['mercator_1'],
            input_projection=self.mercator_projection,
            target_projection=self.lambert_projection,
        )
        print(f"Mercator converted to lambert in {datetime.now() - start}")

    def test_mercator_to_mercator(self):
        """
        Test that arbitrary data can maintain a projection for mercator
        """
        ...

    def test_reproject_dataset(self):
        overall_dataset: xarray.Dataset = xarray.Dataset(
            coords={
                "x": self.lambert_projection.x_values,
                "y": self.lambert_projection.y_values,
                "time": self.time
            },
            data_vars={
                **self.lambert_arrays,
                self.lambert_projection.crs_variable_name: xarray.DataArray(
                    name=self.lambert_projection.crs_variable_name,
                    data=b"",
                    dims=tuple(),
                    attrs=self.lambert_projection.crs_attributes.copy()
                ),
            }
        )

        start = datetime.now()
        print(f"Saving the lambert dataset for later testing...")
        netcdf.write(target=pathlib.Path.home() / "lambert_test.nc", dataset=overall_dataset)
        print(f"It took {datetime.now() - start} to save the lambert dataset")
        start = datetime.now()
        reprojected_data: xarray.Dataset = reproject.reproject_dataset(
            dataset=overall_dataset,
            reprojection_dataset_path=self.mercator_projection.path,
            crs_variable_name=self.lambert_projection.crs_variable_name,
            reprojection_crs_variable_name=self.mercator_projection.crs_variable_name,
            projection_string_attribute="esri_pe_string",
            x_coordinate_name=self.lambert_projection.x_values.name,
            y_coordinate_name=self.lambert_projection.y_values.name,
            reprojection_string_attribute="esri_pe_string",
            reprojection_x_coordinate_name=self.mercator_projection.x_values.name,
            reprojection_y_coordinate_name=self.mercator_projection.y_values.name,
        )
        print(f"It took {datetime.now() - start} to reproject the dataset to mercator")


if __name__ == '__main__':
    unittest.main()

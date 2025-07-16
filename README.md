# ![](https://raw.githubusercontent.com/NOAA-OWP/nwm-post-processing/doc/noaa_nws_owp_combo_log.png) National Water Model Post Processing

**National Water Model Post Processing** takes [National Water Model](https://water.noaa.gov/about/nwm) data, 
combines the output, and subsets them based on region in order to make model data easier to work with

### Technology stack 
  - `nco`: Netcdf Operators
  - `pandas`
  - `xarray`
  - `dask`
  - `h5netcdf`
  - NetCDF
  - JSON

### Status
Pre-Alpha


## Dependencies

### Languages

This library is overwhelming written in Python with a focus on [Xarray](https://docs.xarray.dev/en/stable/) and 
[Dask](https://docs.dask.org/en/stable/). Native applications and code, such as from 
[NCO](https://nco.sourceforge.net/), may be called via the `subprocess` module to take advantage of vast performance improvements.

### Libraries

The main third party libraries used are [Xarray](https://docs.xarray.dev/en/stable/),
[Dask](https://docs.dask.org/en/stable/) and [Pandas](https://pandas.pydata.org/docs/). Targeted deployment environments
run the risk of being highly restricted in terms of what libraries are permissable. As a result, keep reliance on 
third party libraries to an absolute minimum. `httpx` may be 100x more effective and easier to maintain than 
`requests` or your own networking code, but approval for use within primary deployment environments 
(not exclusive to, but featuring US federal High Performance Computing environments) may not be worth it.

## Installation

See [INSTALL](INSTALL.md).

## Configuration

### Python Configuration

The [configuration](post_processing/configuration.py) module provides a common entry point for accessing python related 
environment and application settings. Reference it in order to know what all is available and add to it as more options are needed.
The `post_processing.configuration.settings` is functionally equivalent to `os.environ` as well.

### Python Logs

Python logging is defined, by default, within [python_log_config.json](./resources/python_log_config.json).
It follows the basic python logging dictionary configuration. An alternative logging configuration may be used by
setting the `PP_LOG_CONFIG_PATH` environment variable (case-insensitive). Log levels may be overriden by the 
[log_level_override.json](resources/log_level_override.json) file in order to do things such as preventing loggers 
from bloating output files. As with `PP_LOG_CONFIG_PATH`, the `PP_LOG_LEVEL_OVERRIDE_PATH` environment variable will 
allow you to change where the override file to use lives.

### Profiles

Processing is performed based on configured profiles for model configuration, output type, member, and region. 
In order for processing to be performed, there must be a profile with matching the desired configuration, 
output type, member, and region. For example:

**Initial example for analysis assim for channel_rt for alaska:** 

```json
{
    "operations": [
        {
            "operation": "echo",
            "message": "An output for 'nwm.t<date><cycle>z.analysis_assim.channel_rt.tm00.alaska.nc' should be generated, as defined in {source_file}.",
            "level": "error"
        },
        {
            "operation": "echo",
            "message": "An output for 'nwm.t<date><cycle>z.analysis_assim.channel_rt.tm01.alaska.nc' should be generated, as defined in {source_file}.",
            "level": "error"
        },
        {
            "operation": "echo",
            "message": "An output for 'nwm.t<date><cycle>z.analysis_assim.channel_rt.tm02.alaska.nc' should be generated, as defined in {source_file}.",
            "level": "error"
        }
    ],
    "configuration": "analysis_assim",
    "output_type": "channel_rt",
    "region": "alaska",
    "member": null
}
```

## Usage

Ensure that the installation instructions are followed. The `post-process` command will not be available otherwise.

Given a desired cycle of `00`, a desired model output type of `channel_rt`, a desired configuration of 
`short_range`, a directory containing model output at `/path/to/input`, and a directory to put results at 
`/path/to/output/`, processing may be performed via the following invocations:

```shell
post-process /path/to/input/nwm.t00z.short_range.channel_rt.f001.nc /path/to/output
```

```shell
./post_processing/__main__.py /path/to/input/nwm.t00z.short_range.channel_rt.f007.nc /path/to/output
```

```shell
python3 -m post_processing /path/to/input/nwm.t00z.short_range.channel_rt.f018.nc /path/to/output
```

Notice the fluctuating `f00#` value. `python3 -m post_processing`, `./post_processing/__main__.py`, and 
`post-process` all execute the code in [`__main__.py`](post_processing/__main__.py), and `__main__.py` will find all 
common `f001` and `tm00` files so the specific member file given is not as important as just providing _any_ NWM 
output to be processed. This relies on NCEP naming standards, which matches on:

`<model>.<cycle>.<configuration>.<output type>.<forecast hour>.<location>.nc`

In the context of the National Water Model, that is equivalent to:

`nwm.t<cycle>z.<configuration>.<output type <member>>.<forecast or tminus hour>.<region>.nc`

### Examples

- nwm.t02z.short_range.channel_rt.conus.nc
- nwm.t06z.medium_range_blend.channel_rt.conus.nc
- nwm.t12z.long_range.channel_rt_3.conus.nc
- nwm.t12z.analysis_assim_no_da.channel_rt.tm0145.hawaii.nc

### Naming Quirks

Hawaii forecast valid times are sub-hourly, so where you'd generally see `f008` or `tm02` 
for forecast hour 8 or t-minus 2, you may see `f00330` or `tm0245` for forecast hour 3 and minute 30 or 
t-minus 2 hours and 45 minutes.

### Forecast Hour vs T-Minus

National Water Model output will include `f##`, indicating a forecast hour for model runs whose value run forward 
in time, or `tm##`, indicating a duration into the past. These terms are consolidated as the word `frame` within the 
code base. This name comes from the `frame` terminology in video or animations, where each `frame` is a series of 
values at a given time.

## How to test the software

From the root of the project, type:

```shell
python -m unittest discover -s test -p "test_*.py"
```

To run the unit tests on the python code. This command tells the active python version to use the `unittest` testing library
(as opposed to something like pytest) and discover unit tests under every file matching the glob of "test_*.py" 
under the `./test` directory

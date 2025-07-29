# National Water Model Post Processing

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

This library is overwhelmingly written in Python with a focus on [Xarray](https://docs.xarray.dev/en/stable/) and 
[Dask](https://docs.dask.org/en/stable/). Native applications and code, such as from 
[NCO](https://nco.sourceforge.net/), may be called via the `subprocess` module to take advantage of vast performance improvements.

### Libraries

The main third party libraries used are [Xarray](https://docs.xarray.dev/en/stable/),
[Dask](https://docs.dask.org/en/stable/) and [Pandas](https://pandas.pydata.org/docs/). Targeted deployment environments
run the risk of being highly restricted in terms of what libraries are permissable. As a result, keep reliance on 
third party libraries to an absolute minimum. `httpx` may be 100x more effective and easier to maintain than 
`requests` or your own networking code, but approval for use within primary deployment environments 
(not exclusive to, but featuring US federal High Performance Computing environments) may not be worth it.

## Concurrency

Concurrency is highly limited. Multiprocessing is not desired since it is generally not acceptable within some 
deployment environments since each spawned process must be accounted for and threading may cause I/O issues due to 
the implementation of NetCDF4 and HDF5, both of which are not just not thread-safe, but are in fact thread-dangerous. 
`PP_allow_threads` may be set to `true` to allow a limited degree of multithreading, but use it wisely. In the future, 
the use of a `dask` cluster process may alleviate some of these issues.

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

**analysis_assim for channel_rt for Alaska:** 

```json
{
    "operations": [
        {
            "operation": "drop",
            "exclude": true,
            "fields": ["feature_id", "time", "reference_time", "streamflow", "crs"]
        },
        {
            "operation": "save",
            "filename_pattern": "nwm.t{reference_time__date}{cycle}z.{Configuration}.{ModelOutputType}.tm{tminus}.{Region}.nc",
            "directory": "{output_path}/WMS/",
            "return_new_paths": false
        },
        {
            "operation": "on_each",
            "comment": "Add upstreamflow to each input file by calling 'post_processing.transform.calculate_upstream_flow.calculate_upstream_flow' on each",
            "on_each": [
                {
                    "operation": "function",
                    "function_name": "post_processing.transform.calculate_upstream_flow.calculate_upstream_flow",
                    "comment": "This should end up calling `calculate_upstream_flow(input_path=data, output_path=data, routelink_path='{application_path}/RouteLink_CONUS.nc')`",
                    "kwargs": {
                        "routelink_path": "{resource_path}/routelink/RouteLink_AK.nc",
                        "routelink_from_variable": "from",
                        "output_path": "{work_directory}/upstream.{input_name}"
                    },
                    "argument_mapping": {
                        "input_path": "data"
                    }
                }
            ]
        },
        {
            "operation": "rename",
            "mapping": {
                "feature_id": "station_id"
            }
        },
        {
            "operation": "rename",
            "rename_variable": false,
            "mapping": {
                "feature_id": "station"
            }
        },
        {
            "operation": "extract",
            "dimension": "station_id",
            "mask_coordinate": "feature_id",
            "masks": [
                "resources/masks/alaska.aprfc.nc"
            ],
            "identifier_pattern": "^(?P<region>\\w+(\\.(?P<rfc>[MmAaLlCcNnPpWwOoSsHh][BbPpNnMmAaCcEeWwHhRrGg]([Rr][Ff][Cc]|[Vv][Ii])))?)",
            "output_pattern": "nwm.t{reference_time__date}{cycle}z.{Configuration}.{ModelOutputType}.{frame}.{region}.nc",
            "each": [
                {
                    "operation": "save",
                    "filename_pattern": "{file_name}",
                    "identifier_pattern": "(?P<rfc>[A-Za-z]{2}[Rr][Ff][Cc])",
                    "directory": "{output_path}/RFC/{RFC}/"
                }
            ]
        }
    ],
    "configuration": "analysis_assim",
    "output_type": "channel_rt",
    "region": "alaska",
    "member": null
}
```

### How do I use this Profile?

Calling `post-process /path/to/input/nwm.t06z.analysis_assim.channel_rt.tm01.alaska.nc /path/to/output`
will cause this application to deconstruct the input file. From this input, the application will pull apart 
`/path/to/input/nwm.t06z.analysis_assim.channel_rt.tm01.alaska.nc` to detect `t06z`, `analysis_assim`, `channel_rt`, 
no ensemble member identifier, and `alaska`. Using those details, the application will look through its 
[profile directory](./resources/profiles/) for all profiles whose `"configuration"` is `"analysis_assim"`, 
whose `"output_type"` is `"channel_rt"`, whose `"member"` is `null`, and whose `"region"` is `"alaska"`. These are case-insensitive - it will 
match on anything like `"configuration": "Analysis_Assim"` or `"configuration": "aNaLySiS_AsSiM"`. The application 
will read this document to determine what to do based on the contents of its `"operations"` value.

> **NOTE**
> 
> The application relies on metadata encoded in the filename, not the file path itself. In this example, giving it the path 
> `/path/to/input/nwm.t06z.analysis_assim.channel_rt.tm00.alaska.nc` or 
> `/path/to/input/nwm.t06z.analysis_assim.channel_rt.tm02.alaska.nc` (and possibly even the non-existent 
> `/path/to/input/nwm.t06z.analysis_assim.channel_rt.tm99.alaska.nc`) will yield the **exact** same result since the 
> **exact** same data is encoding into the filename. 

### What will this Profile do?

At first glance, this may look intimidating, but taken a step at a time, it's not too bad. The *only* branching 
paths may come from an `"operation": "branch"` object, of which there are none in this configuration. Expect to see 
those within the CONUS files that will have separate **WMS** and **RFC** output.

#### `"operations"`

1. **"operation": "drop"**

   This operation removes variables from the Netcdf files. The following fields are specified:
      1. `"feature_id"`
      2. `"time"`
      3. `"reference_time"`
      4. `"streamflow"`
      5. `"crs"`
   
   _Normally_, this will remove those variables, but the option `"exclude": true` is present, which tells the 
   application "Drop variables, but _exclude_ these variables from the drop". Why is this useful? We don't have to 
   keep track of everything that will ever be there. We are interested in keeping those variables and those variables 
   only. When this is done, the result will be saved to a temporary `{work_directory}` and hand off all created paths.
2. **"operation": "save"**

   By default, most operations write to the temporary `{work_directory}`. In this case, I want _just_ the individual files with 
   _just_ those variables saved under `"/path/to/output/WMS/` with filenames that look like "nwm", their date in 
   "%Y%m%d" form with the cycle (`06` in this case), prepended by "t" and suffixed by "z", their 
   [`Configuration`](./post_processing/enums.py) value, their [`ModelOutputType`](./post_processing/enums.py) value, 
   their "t-minus" value prepended by "tm", and their [`Region`](./post_processing/enums.py) value. Given a shared `reference_time` datetime 
   value of `2025-07-28T06:00:00-00:00`, files will be saved as:

    | Input Path | Output Path |
    |-----------|-------------|
    | `/path/to/input/nwm.t06z.analysis_assim.channel_rt.tm00.alaska.nc` | `/path/to/output/nwm.t2025072806z.analysis_assim.channel_rt.tm00.alaska.nc` |
    | `/path/to/input/nwm.t06z.analysis_assim.channel_rt.tm01.alaska.nc` | `/path/to/output/nwm.t2025072806z.analysis_assim.channel_rt.tm01.alaska.nc` |
    | `/path/to/input/nwm.t06z.analysis_assim.channel_rt.tm02.alaska.nc` | `/path/to/output/nwm.t2025072806z.analysis_assim.channel_rt.tm02.alaska.nc` |


   The `"return_new_paths": false` configuration tells the application to _not_ feed those three newly created paths 
   to the next operation, meaning that the next operation will get the exact same paths as this one

3. **"operation": "on_each"**

    `"operation": "on_each"` tells the application to perform all operations detailed under its `"on_each"` 
    list separately, within individual threads, if threading is enabled.
    
    **on_each**:

    1. **"operation": "function"**
       
        This tells the application to call a function on each input file separately. This will call the function 
        with the **complete** name of "[post_processing.transform.calculate_upstream_flow.calculate_upstream_flow](post_processing/transform/calculate_upstream_flow.py)". 
        I can call just about any function here. I tell it to use the keyword arguments:

        * `"routelink_path": "{resource_path}/routelink/RouteLink_AK.nc"`
       
          Tells the application to feed the path stemming from the application's `{resource_path}` setting, to the 
          `routelink` directory and to use the file `"RouteLink_AK.nc"`, and feed it into the `"routelink_path"` 
          parameter. I could also use a path of `"{routelink_path}/RouteLink_AK.nc"` since `"{routelink_path}"` 
          is set to `"{resources_path}/routelink"` by default. I could also dictate the full path of 
          `"/path/to/application/resources/routelink/RouteLink_AK.nc"` and forego any variable expansion. I can even use
          `"resources/routelink/RouteLink_AK.nc"`, though that is a little riskier if the application is somehow 
          called somewhere other than the directory right before `"resources"`.
        * `"routelink_from_variable": "from"`
       
          Tells the application the name of the variable within the routelink that dictates _where_ a reach came from. 
          In the official routelink files, this will the netcdf variable `"from"`. If my routelink is instead a netcdf 
          file with a column named `"source_id"` instead of `"from"`, I can dictate `"source_id"` here, instead.
       
        * `"output_path": "{work_directory}/upstream.{input_name}"`

          Tells the application to store the output of this application within the process's temporary `{work_directory}` 
          with a filename prepended by "`upstream.`" If my input files were:
   
          1. `/path/to/intermediate/directory/12345/nwm.t06z.analysis_assim.channel_rt.tm00.alaska.nc`
          2. `/path/to/intermediate/directory/12345/nwm.t06z.analysis_assim.channel_rt.tm01.alaska.nc`
          3. `/path/to/intermediate/directory/12345/nwm.t06z.analysis_assim.channel_rt.tm02.alaska.nc`

          This will save the output of `post_processing.transform.calculate_upstream_flow.calculate_upstream_flow` as:
   
          1. `/path/to/intermediate/directory/12345/upstream.nwm.t06z.analysis_assim.channel_rt.tm00.alaska.nc`
          2. `/path/to/intermediate/directory/12345/upstream.nwm.t06z.analysis_assim.channel_rt.tm01.alaska.nc`
          3. `/path/to/intermediate/directory/12345/upstream.nwm.t06z.analysis_assim.channel_rt.tm02.alaska.nc`

        Using a distinct filename helps avoid NetCDF I/O conflicts, as simultaneous read/write access may fail.

    Lastly, I map local arguments to function inputs rather than giving specific values that I know ahead of time. 
    Since I know the function at `post_processing.transform.calculate_upstream_flow.calculate_upstream_flow`, I know it 
    expects the path to the file to be worked on to be named `"input_path"`. `"on_each"` has a `data` variable that 
    holds the path and feeds it into each called function. I want _that_ to be the value fed into 
    `post_processing.transform.calculate_upstream_flow.calculate_upstream_flow` as `input_path`, so I set the mapping 
    here.

    `post_processing.transform.calculate_upstream_flow.calculate_upstream_flow` returns the path it has written to and 
    it is the last operation in the `"on_each"` list, so its result will get collected with all other calls to 
    `post_processing.transform.calculate_upstream_flow.calculate_upstream_flow` and pass those on to the next operation.
    In summary, **on_each** received:

    1. `{work_directory}/nwm.t06z.analysis_assim.channel_rt.tm00.alaska.nc`
    2. `{work_directory}/nwm.t06z.analysis_assim.channel_rt.tm01.alaska.nc`
    3. `{work_directory}/nwm.t06z.analysis_assim.channel_rt.tm02.alaska.nc`

    And outputs:

    1. `{work_directory}/upstream.nwm.t06z.analysis_assim.channel_rt.tm00.alaska.nc`
    2. `{work_directory}/upstream.nwm.t06z.analysis_assim.channel_rt.tm01.alaska.nc`
    3. `{work_directory}/upstream.nwm.t06z.analysis_assim.channel_rt.tm02.alaska.nc`

3. **"operation": "rename"**

    Next, the application will rename variables to their mapped strings. In this case, it will rename the NetCDF 
    variable named "feature_id" to "station_id". Information about the output name isn't given, but we could if we 
    wanted that level of control. Exerting that control can help avoid possible I/O issues. This will pass along the 
    paths to all three files with their renamed variables.
4. **"operation": "rename"**

    Next, the application will perform `rename` again, but, this time, the `"rename_operation"` value is set to 
    `false`. If `"rename_operation"` is `false`, what is being renamed? Why, it's a NetCDF _dimension_. This will rename 
    all dimensions within the mapping. There's only one mapping here, so it will rename the "feature_id" dimension to 
    "station". This will pass along the path to all three files with their renamed dimensions.
5. **"operation": "extract"**

    Lastly, the application will extract/subset values based on different masks. The path to each mask is given 
    within the `"masks"` collection. These can be absolute paths, relative paths, templated strings, or globs. 
    In this case, the one mask in use is at the relative path `"resources/masks/alaska.aprfc.nc"`. 
 
   > **WARNING**
   > 
   > A safer path would be `"{mask_path}/alaska.aprfc"` since it will allow me to move my masks around without 
   > breaking the reference.

    I tell it to use the `"identifier_pattern"` of 
    `^(?P<region>\\w+(\\.(?P<rfc>[MmAaLlCcNnPpWwOoSsHh][BbPpNnMmAaCcEeWwHhRrGg]([Rr][Ff][Cc]|[Vv][Ii])))?)`
    to find variable names to use in metadata from the filenames in the masks. In this case, a `"region"` of 
    "alaska.aprfc" and an `"rfc"` value of "aprfc" will be extracted and I will be able to use this in filename 
    templates. Capitalization matters, here. There should already be a metadata value for `Region`. The `Region` 
    value will be "alaska", the `region` value here will be "alaska.aprfc". I can change the parameter capture in the 
    `"identifier_pattern"` to name that parameter differently. If I wanted to name that parameter "state_and_rfc" in 
    order to keep it clearer, I can just change that pattern to: 
    `^(?P<state_and_rfc>\\w+(\\.(?P<rfc>[MmAaLlCcNnPpWwOoSsHh][BbPpNnMmAaCcEeWwHhRrGg]([Rr][Ff][Cc]|[Vv][Ii])))?)`

    I tell it to use the `"dimension"` of `"station_id"` in the input and the `"mask_coordinate"` of `"feature_id"` 
    in the mask to identify what should be included in the extracted data. The following operations should be 
    performed on values where the `"station_id"` value in the input matches the `"feature_id"` value in the mask. 
    Note: despite the name,`"dimension"` here may also refer to a variable, 
    which will be the case here since `"feature_id"` was renamed to`"station_id"`.

    > **WARNING**
    >
    > Since `feature_id(feature_id)` was renamed to `station_id(station)` within the NetCDF, `station_id` is no 
   > longer considered a coordinate. This will end up logging a message warning you that a non-coordinate is in use 
   > and the operation will be slower as a result. To avoid this, you can rename the fields _after_ the pertinent 
   > data is extracted. In some cases, this is performed in the operations under the `"each"` list. 

    The `"output_pattern"` states that files should be saved to the temporary `{work_directory}` as "nwm", followed 
    by a "t", the `reference_time` of the data in "%Y%m%d" format, followed by the cycle, followed by "z", then its 
    `post_processing.enums.Configuration` value, then its `post_processing.enums.ModelOutputType` value, followed by 
    the `frame` of the output (as in "tm00", "tm02", "f00015", etc), followed by the `region` value retrieved by the
    `"identifier_pattern"`, the file extension "nc", all joined by a period. This will result in:

    1. `{work_directory}/nwm.t2025072806z.analysis_assim.channel_rt.tm00.alaska.aprfc.nc`
    2. `{work_directory}/nwm.t2025072806z.analysis_assim.channel_rt.tm01.alaska.aprfc.nc`
    3. `{work_directory}/nwm.t2025072806z.analysis_assim.channel_rt.tm02.alaska.aprfc.nc`

    Each of these file paths will then be passed through the operations under "each". In this case, there is only one: 
    `"operation": "save"`. This configuration tells the application find an identifier named "rfc" with the pattern 
    `(?P<rfc>[A-Za-z]{2}[Rr][Ff][Cc])`. There is logic with the save function that will identify the `rfc` value and 
    bind another value to the available metadata: `RFC` (rather than `rfc` as just identified) to the proper value of 
    `post_processing.enums.RFC`. In this case, `RFC` is an enumeration of RFC abbreviations. This will make the 
    metadata `"RFC": "AP"` available. The configuration then tells the application to save each file, named as is, to 
    the output path, followed by "RFC", followed by the new `RFC` value, resulting in:

    1. `/path/to/output/RFC/AP/nwm.t2025072806z.analysis_assim.channel_rt.tm00.alaska.aprfc.nc`
    2. `/path/to/output/RFC/AP/nwm.t2025072806z.analysis_assim.channel_rt.tm01.alaska.aprfc.nc`
    3. `/path/to/output/RFC/AP/nwm.t2025072806z.analysis_assim.channel_rt.tm02.alaska.aprfc.nc`

### How can I track what is happening as it operates?

There are two different operations that allow you to track what is occurring: `"echo"` and `"peek"`

#### echo

The `"echo"` operation allows you to simply log a message (with metadata for templating) at the given level. 

The following configuration:

```json
{
    "operation": "echo", 
    "message": "This is a message for a {Configuration} process"
}
```

Will yield:

```
[2025-07-29 15:31:05-0500] INFO profile: This is a message for a analysis_assim process
```

in the logs, while the following:

```json
{
    "operation": "echo",
    "message": "This is some sort of error message",
    "level": "ERROR"
}
```

will yield the following in the logs:

```
[2025-07-29 17:29:05-0500] ERROR profile profile.py #349: This is some sort of error message
```

#### peek

The peek operation is special in that it will allow you to see:

1. A summary of what is going on and what has occurred
2. The input passed into the peek operation
3. Available metadata values that may be inserted into strings

Inserting:

```json
{
    "operation": "peek"
}
```

as the second operation will yield data in the logs like:

```
[2025-07-29 11:12:40-0500] INFO profile: 
Profile:             channel_rt data for the analysis_assim configuration over alaska
Process identifier:  1593379657318272320
Work directory:      /path/to/nwm-post-processing/intermediate/1593379657318272320
Previous Operations: 
    - 1: Drop all data variables except feature_id, time, reference_time, streamflow, crs
Files:
    - /path/to/nwm-post-processing/intermediate/1593379657318272320/1nwm.t00z.analysis_assim.channel_rt.tm02.alaska.nc
    - /path/to/nwm-post-processing/intermediate/1593379657318272320/1nwm.t00z.analysis_assim.channel_rt.tm01.alaska.nc
    - /path/to/nwm-post-processing/intermediate/1593379657318272320/1nwm.t00z.analysis_assim.channel_rt.tm00.alaska.nc


[2025-07-29 11:12:41-0500] INFO profile: 

/path/to/nwm-post-processing/intermediate/1593379657318272320/1nwm.t00z.analysis_assim.channel_rt.tm02.alaska.nc:

<xarray.Dataset> Size: 6MB
Dimensions:         (feature_id: 391528, reference_time: 1, time: 1)
Coordinates:
  * feature_id      (feature_id) int64 3MB 11 12 ... 75005400047364
  * reference_time  (reference_time) datetime64[ns] 8B 2025-07-24T21:00:00
  * time            (time) datetime64[ns] 8B 2025-07-24T22:00:00
Data variables:
    crs             |S1 1B ...
    streamflow      (feature_id) float64 3MB ...
Attributes: (12/19)
    TITLE:                      OUTPUT FROM NWM v3.0
    featureType:                timeSeries
    proj4:                      +proj=stere +lat_0=90 +lat_ts=60 +lon_0=-135 ...
    model_initialization_time:  2025-07-24_21:00:00
    station_dimension:          feature_id
    model_output_valid_time:    2025-07-24_22:00:00
    ...                         ...
    model_configuration:        analysis_and_assimilation
    dev_OVRTSWCRT:              1
    dev_NOAH_TIMESTEP:          3600
    dev_channel_only:           0
    dev_channelBucket_only:     0
    dev:                        dev_ prefix indicates development/internal me...
    
...
[2025-07-29 11:12:41-0500] INFO profile: 
    - TITLE: OUTPUT FROM NWM v3.0
    - featureType: timeSeries
    - proj4: +proj=stere +lat_0=90 +lat_ts=60 +lon_0=-135 +x_0=0 +y_0=0 +R=6370000 +units=m +no_defs
    - model_initialization_time: 2025-07-24_21:00:00
    - station_dimension: feature_id
    - model_output_valid_time: 2025-07-25_00:00:00
    - model_total_valid_times: 3
    - stream_order_output: 1
    - cdm_datatype: Station
    - Conventions: CF-1.6
    - code_version: v5.3.0-alpha1
    - NWM_version_number: v3.0
    - model_output_type: channel_rt
    - model_configuration: analysis_and_assimilation
    - dev_OVRTSWCRT: 1
    - dev_NOAH_TIMESTEP: 3600
    - dev_channel_only: 0
    - dev_channelBucket_only: 0
    - dev: dev_ prefix indicates development/internal meta data
    - time.long_name: valid output time
    - time.standard_name: time
    - time.valid_min: 29223240
    - time.valid_max: 29223360
    - time__date: 20250725
    - time__hour: 0
    - time__minute: 0
    - time__second: 0
    - time__day: 25
    - time__month: 7
    - time__year: 2025
    - reference_time.long_name: model initialization time
    - reference_time.standard_name: forecast_reference_time
    - reference_time__date: 20250724
    - reference_time__hour: 21
    - reference_time__minute: 0
    - reference_time__second: 0
    - reference_time__day: 24
    - reference_time__month: 7
    - reference_time__year: 2025
    - feature_id.long_name: Reach ID
    - feature_id.comment: NHDPlusv2 ComIDs within CONUS, arbitrary Reach IDs outside of CONUS
    - feature_id.cf_role: timeseries_id
    - crs.transform_name: latitude longitude
    - crs.grid_mapping_name: latitude longitude
    - crs.esri_pe_string: GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137.0,298.257223563]],PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]];-400 -400 1000000000;-100000 10000;-100000 10000;8.98315284119521E-09;0.001;0.001;IsHighPrecision
    - crs.spatial_ref: GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137.0,298.257223563]],PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]];-400 -400 1000000000;-100000 10000;-100000 10000;8.98315284119521E-09;0.001;0.001;IsHighPrecision
    - crs.long_name: CRS definition
    - crs.longitude_of_prime_meridian: 0.0
    - crs._CoordinateAxes: latitude longitude
    - crs.semi_major_axis: 6378137.0
    - crs.semi_minor_axis: 6356752.5
    - crs.inverse_flattening: 298.2572326660156
    - streamflow.long_name: River Flow
    - streamflow.units: m3 s-1
    - streamflow.grid_mapping: crs
    - streamflow.valid_range: [0, 5000000]
    - nudge.long_name: Amount of stream flow alteration
    - nudge.units: m3 s-1
    - nudge.grid_mapping: crs
    - nudge.valid_range: [-5000000, 5000000]
    - velocity.long_name: River Velocity
    - velocity.units: m s-1
    - velocity.grid_mapping: crs
    - velocity.valid_range: [0, 5000000]
    - qSfcLatRunoff.long_name: Runoff from terrain routing
    - qSfcLatRunoff.units: m3 s-1
    - qSfcLatRunoff.grid_mapping: crs
    - qSfcLatRunoff.valid_range: [0, 2000000000]
    - qBucket.long_name: Flux from gw bucket
    - qBucket.units: m3 s-1
    - qBucket.grid_mapping: crs
    - qBucket.valid_range: [0, 2000000000]
    - qBtmVertRunoff.long_name: Runoff from bottom of soil to bucket
    - qBtmVertRunoff.units: m3
    - qBtmVertRunoff.grid_mapping: crs
    - qBtmVertRunoff.valid_range: [0, 20000000]
    - application_path: /path/to/nwm-post-processing
    - intermediate_directory: /path/to/nwm-post-processing/intermediate
    - logging_config_path: /path/to/nwm-post-processing/resources/python_log_config.json
    - mask_path: /path/to/nwm-post-processing/resources/masks
    - profile_path: /path/to/nwm-post-processing/resources/profiles
    - resource_path: /path/to/nwm-post-processing/resources
    - routelink_path: /path/to/nwm-post-processing/resources/routelink
    - threshold_path: /path/to/nwm-post-processing/resources/thresholds
    - output_path: /path/to/output
    - ModelOutputType: channel_rt
    - Region: alaska
    - Configuration: analysis_assim
    - member: None
    - cycle: 00
    - stage: 2
```

`"peek"` has 3 options:

- `"show_summary": [true|false]`: This tells the operation to log the summary of what has been performed so far. The default is `true`
- `"show_state": [true|false]`: This tells the operation to log the current state of all the function's inputs. The default is `true`
- `"show_metadata": [true|false]`: This tells the operation to log all available metadata values. This will not show 
the available metadata in every operation, only those immediately accessible to the `peek` operation. It is safe to 
assume that any metadata available within the peek operation will be available within a regular operation. 
The default is `true`.

## Usage

Ensure that the installation instructions are followed. The `post-process` command will not be available otherwise.

Given a desired cycle of `00`, a desired model output type of `channel_rt`, a desired configuration of 
`short_range`, a directory containing model output at `/path/to/input`, and a directory to put results at 
`/path/to/output/`, processing may be performed via the following invocations:

```shell
post-process /path/to/input/nwm.t00z.short_range.channel_rt.f001.conus.nc /path/to/output
```

```shell
python3 -m post_processing /path/to/input/nwm.t00z.short_range.channel_rt.f018.conus.nc /path/to/output
```

> **Warning**
> 
> Calling `python post_processing/__main__.py /path/to/input/nwm.t00z.short_range.channel_rt.f018.conus.nc /path/to/output`
> will not work. This invocation is not compatible with namespaces and nested packages with not know what the `post_processing` 
> package is.

Notice the fluctuating `f00#` value. `python3 -m post_processing` and 
`post-process` both execute the code in [`__main__.py`](post_processing/__main__.py), and `__main__.py` will find all 
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

#### Summary Mode

Entering the following command:

```shell
post-process --summarize /path/to/nwm.t00z.analysis_assim.channel_rt.tm00.alaska.nc /path/to/output
```

will print a summary of what to expect rather than actually running the profile. Expect to see output like:

```
ChannelRouting generated for AnalysisAssimilation across Alaska:
=====================================================================
1: Drop all data variables except feature_id, time, reference_time, streamflow, crs

2: Save files to {output_path}/WMS/ with the following file name pattern: nwm.t{reference_time__date}{cycle}z.{Configuration}.{ModelOutputType}.tm{tminus}.{Region}.nc

3: Perform the following on each file and each file alone:

-----------------------------------------------------------------------------------------
3.1: Call post_processing.transform.calculate_upstream_flow.calculate_upstream_flow(
    routelink_path={resource_path}/routelink/RouteLink_AK.nc,
    routelink_from_variable=from,
    output_path={work_directory}/upstream.{input_name},
    input_path=data
)
-----------------------------------------------------------------------------------------


4: Rename the variable 'feature_id' to 'station_id'

5: Rename the dimension 'feature_id' to 'station'

6: Extract data by location based on the 'station_id' dimension in the input and the 'feature_id' dimension within:
    - /path/to/nwm-post-processing/resources/masks/alaska.aprfc.nc
And save the results to files named like: nwm.t{reference_time__date}{cycle}z.{Configuration}.{ModelOutputType}.{frame}.{region}.nc
```

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

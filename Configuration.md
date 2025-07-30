# National Water Model Post Processing

---

> **Note**
> 
> Information valid as of 7/30/2025
> 
> Settings may have been added or removed and best practices may have changed since

## Configuration

Post Processing for the National Water Model is intended to be as friendly to use in both friendly, antagonistic, 
and highly constrained environments. As with most data intensive applications, Post Processing relies on configurations and data
and that requires the system to know where data is, how to interpret it, and how to plan to use it. An unregulated 
system with relatively few resources, like a laptop, will need to behave very differently than when run on a highly 
regulated supercomputer. To bridge this gap, many settings are open and available to modifiable before and sometimes 
even during runtime.

## Necessity

Ideally, you'd never have to know about or deal with _any_ configuration settings. It is only when constraints force 
different behavior when you might need to make changes. All settings are optional to configure.

## Danger

There should be very little application-side risk involved with these settings. Most of these settings affect how data 
is read and interpreted. The entities most responsible for writing will come from product profiles. 
The worst things these settings should do is maybe result in IO conflicts due to allowed threading or the inability to 
find data. It should be relatively difficult to inflict any sort of data-loss by tuning these.

## In Code

In code, settings make be accessed via the `settings` object in `post_processing.configuration`. This handles 
everything from loading a local `.env` file to collecting and interpreting environment variables.

Say you want to create a date and print it in a fashion uniform to the entire system. That will look like:

```python
from post_processing.configuration import settings
from datetime import datetime

print(datetime.now().strftime(settings.date_format))
```

## In your environment

There are three ways to set variables in your environment:

1. Manually setting environment variables
    There is nothing stopping me from hitting `PP_LOG_LEVEL=DEBUG` before running the application. Likewise, 
    there is nothing stopping me from setting that in any sort of calling script. It's a perfectly normal approach, 
    though hard to track and reproduce. It is recommended that you try to make other options work before trying this 
    approach.
2. An `environment.sh` `source` script
    Linux shell scripting grants the ability to "source" a file by calling `source file.sh` or `. file.sh`. Provided 
    with the application are three different files that may be `source`d that have different values and purposes.
    * `environment.sh` - A template for different settings that may be used. Simply uncomment keys, change values, then source the file before running the application
    * `hpc_environment.sh` - A template `source` file that is intended for high performance computing environments
    * `dev_environment.sh` - A template `source` file that is intended for small scale development environments
   
    If you find yourself having to consistently make configuration changes to get the application to work, consider using and commiting an `environment.sh` file, permitting that it does not contain sensitive information
3. A `.env` File
    The code will try to find a `.env` file with specialized settings within the application directory when starting up. These settings should be **your** settings within **your** environment. Do not share this. It is recommended that you apply changes here when you just need to configure something for yourself.

### environment.sh **vs** .env

Both the environment.sh file and the .env file are execllent places to put import configuration values, but their use 
cases aren't interchangeable. 

#### Variable Expansion

The .env file does not expand variables. If I configure:

```shell
PP_VARIABLE="/home/${USER}/some/directory"
```

within my .env file, the value of PP_VARIABLE won't be `"/home/christophertubbs/some/directory"`, it will be the 
literal `"/home/${USER}/some/directory"`. If I instead define:

```shell
export PP_VARIABLE="/home/${USER}/some/directory"
```

within my environment.sh file of choice, the value of PP_VARIABLE, at runtime, will be 
`"/home/christophertubbs/some/directory"`.

#### Privacy

`.env` files are, traditionally, custom tailored to _**you**_: the user. Any sort of important variables specific 
to **you** should live within this file and not within one of the files in the repository. Similarly, any variable 
that refers to anything resembling PII or deployment system attributes should be kept within the `.env` rather than an 
environment.sh. 

```shell
export PP_MASK_PATH=/path/to/deployment/resources/masks
```

is a good candidate for an `environment.sh` file.

```shell
export PP_MASK_PATH=/home/christophertubbs/Downloads/backup/
```

Is not.

---

## Variable Precedence

Values are resolved by recording first the current environment variables (accessible via `env` in bash or 
`os.environ` in Python), then from the `.env` file. If a value is defined in the `.env`, that will be used above all 
else. Sourcing an `environment.sh` file just loads the current environment with the correct variables. Even those 
will be overridden by the contents of `.env`.  

---

## Settings

### Core


| Setting                    | Variable                        | Default                                               | Purpose                                                                            | Effect                                                                                                                                                                                                                               |
|----------------------------|---------------------------------|-------------------------------------------------------|------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| allow_threading            | `PP_ALLOW_THREADING`            | `False`                                               | Whether or not to allow multithreading                                             | May offer potential performance benefits - do **NOT** set to `True` in HPC environments. May cause IO Conflicts                                                                                                                      |
| default_netcdf_engine      | `PP_DEFAULT_NETCDF_ENGINE`      | `"netcdf4"`                                           | What engine to use when opening a NetCDF file                                      | The two supported engines are `netcdf4` and `h5netcdf`. `netcdf4` will spend the majority of its time in C code while `h5netcdf` uses more python operations, then relies on HDF5. Both may resolve _and_ cause their own IO issues. |
| netcdf_cache_size          | `PP_NETCDF_CACHE_SIZE`          | `3`                                                   | The number of NetCDF files that may be cached at once                              | Some NetCDF files may be cached for quick(er) access. Caching is only available when lazy loading is turned off.                                                                                                                     |
| debug                      | `PP_DEBUG`                      | `False`                                               | Defines whether the application is running in 'debug' mode                         | Debug mode is a flagged mode where the developers should lock warnings and functionality behind as well as enabling/disabling debug messages.                                                                                        |
| log_format                 | `PP_LOG_FORMAT`                 | `"[%(asctime)s] %(levelname)s %(name)s: %(message)s"` | A common format for python logging messages                                        | Logs will use this format when not told differently from a logging config file                                                                                                                                                       |
| lazy_load_netcdf           | `PP_LAZY_LOAD_NETCDF`           | `False`                                               | Whether to allow lazy loading of netcdf data through Dask                          | Using Dask to lazy load data results if far less memory use but may result in IO issues due to lazy execution and release                                                                                                            |
| maximum_additional_threads | `PP_MAXIMUM_ADDITIONAL_THREADS` | `python -c "import os; print(os.cpu_count())"`        | The default maximum number of allow extra threads                                  | Only used if threading is allowed                                                                                                                                                                                                    |


### Logging


| Setting                    | Variable                        | Default                                               | Purpose                                                                            | Effect                                                                                                                                                                                                                               |
|----------------------------|---------------------------------|-------------------------------------------------------|------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| date_format                | `PP_DATE_FORMAT`                | `"%Y-%m-%d %H:%M:%S%z"`                               | The format that dates will be printed as                                           | Without extra configuration, this will be what all dates logged or printed will look like in order to serve a common standard                                                                                                        |
| log_format                 | `PP_LOG_FORMAT`                 | `"[%(asctime)s] %(levelname)s %(name)s: %(message)s"` | A common format for python logging messages                                        | Logs will use this format when not told differently from a logging config file                                                                                                                                                       |
| loggers_to_quiet           | `PP_LOGGERS_TO_QUIET`           |                                                       | A comma and semi-colon separated list of logger names to quiet                     | These loggers will only output errors and critical statements. Useful for quieting chatty third party libraries                                                                                                                      |
| json_log_path              | `PP_JSON_LOG_PATH`              |                                                       | Where to write a JSON log to                                                       | A JSON log will optionally enable the logging of messages as objects for analysis                                                                                                                                                    |
| log_level_override_path    | `PP_LOG_LEVEL_OVERRIDE_PATH`    |                                                       | The path to a JSON file that dictates specialized log levels for different loggers | Helpful if you want certain loggers to output debug messages but not others; more fine-tuned than `loggers_to_quiet`                                                                                                                 |
| json_log_level             | `PP_JSON_LOG_LEVEL`             | `INFO`                                                | What level of messages to log to the optional JSON logger                          | This allows the JSON logger to be either chattier or quieter than the standard log                                                                                                                                                   |
| json_log_maximum_bytes     | `PP_JSON_LOG_MAXIMUM_BYTES`     | `1048576` (1MiB)                                      | How large JSON logs may be                                                         | Messages will be lost if the file grows too large - increase this value to allow the log to grow larger                                                                                                                              |
| verbosity                  | `PP_VERBOSITY`                  | `0`                                                   | An extra parameter of verbosity to use for finer tuned message                     | Rarely used but allows the developer to offer different details per the verbosity level. The higher the level, the more verbose debug messages should be                                                                             |


### Paths

| Setting                    | Variable                        | Default                                               | Purpose                                                                            | Effect                                                                                                                                                                                                                               |
|----------------------------|---------------------------------|-------------------------------------------------------|------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| resource_path              | `PP_RESOURCE_PATH`              | `./resources`                                         | Where to find supplemental data used to create products, such as masks             | Best used as a good shortcut. This setting may be ignored completely by the Profiles which might use it                                                                                                                              |
| mask_path                  | `PP_MASK_PATH`                  | `${PP_RESOURCE_PATH}/masks`                           | Where to find mask files                                                           | Best used as a shortcut. This setting may be ignored completely by the Profiles that might use it                                                                                                                                    |
| routelink_path             | `PP_ROUTELINK_PATH`             | `${PP_RESOURCE_PATH}/routelink`                       | Where to find routelinks                                                           | Best used as a shortcut. This setting may be ignored completely by the Profiles that might use it                                                                                                                                    |
| threshold_path             | `PP_THRESHOLD_PATH`             | `${PP_RESOURCE_PATH}/thresholds`                      | Where to find statistical threshold files for anomaly calculations                 | Best used as a shortcut. This setting may be ignored completely by the Profiles that might use it                                                                                                                                    |
| logging_config_path        | `PP_LOGGING_CONFIG_PATH`        | `${PP_RESOURCE_PATH}/python_log_config.json`          | Where to find a JSON configuration for logging                                     | Configure all of your log settings here                                                                                                                                                                                              |
| intermediate_directory     | `PP_INTERMEDIATE_DIRECTORY`     | `./intermediate`                                      | Where generated/incomplete products are stored temporarily                         | Used instead of /tmp to ensure that all IO is kept on the same disk as the rest of the data                                                                                                                                          |
| profile_path               | `PP_PROFILE_PATH`               | `${PP_RESOURCE_PATH}/profiles`                        | Where to find product Profiles                                                     | The most important directory configuration. If a profile is not in this directory, it will not run                                                                                                                                   |

## Best Practices

### Favor settings over profile configurations

These settings can and **should** be used within Profiles. Favor referencing the settings within your Profile 
configurations over putting in full or partial paths. `"{mask_path}/serfc.nc"` is more likely to be usable across all 
environments, whereas `"resources/masks/serfc.nc"` may work in dev but not prod and vice versa. 

### Capitalization

Environment variables in this application are case-insensitive. You do not need to remember that the setting is 
`PP_ROUTELINK_PATH` instead of `PP_RouteLink_Path`

### References

Try to build paths in Profiles by referring to the above paths rather than using partial, relative, or absolute paths. 
Try to use paths like "{mask_path}/my.mask.nc" rather than "resources/masks/my.mask.nc", 
"{resouce_path}/masks/my.mask.nc", "/path/to/application/resources/masks/my.mask.nc", as the path becomes more 
portable and easier to reuse. The more specific the path the less likely it is usable within other environments. 
It's easier to change the setting `PP_RESOURCE_PATH` or `pp_mask_path` than it is to modify 80+ files. 

### Scenarios

> **I want to use route link files from `/lfs/scratch/development/owp/whereever/` not from `./resources/routelink`**
> 
> Just set PP_ROUTELINK_PATH in your .env to /lfs/scratch/development/owp/whereever/, set the value in an 
> "environment.sh" file and source it, ala `. environment.sh`, manually script out 
> `export PP_ROUTELINK_PATH=/lfs/scratch/development/owp/whereever/`, or modify your [Profiles](resources/profiles) (not recommended)

> **I want to store log messages as JSON objects**
> 
> Just assign a path to PP_JSON_LOG_PATH in your .env, an environment.sh file, or manually script it

> **I need to reduce messages that go to STDOUT to only Errors or Criticals**
> 
> Modify the JSON file at PP_LOGGING_CONFIG_PATH by removing the `"stdout"` handler from the list at `root.handlers`

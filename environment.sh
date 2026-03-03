#!/usr/bin/env bash
######################################################################################
#
# A store for optional environment variable configuration
#
#   Uncomment and configure variables prior to sourcing prior to calling post-process
#
######################################################################################

export PP_APP_PATH="$(dirname $(realpath ${BASH_SOURCE[0]}))"

# An override for Whether to run in DEBUG mode
#export PP_debug="False"

# An override for The format with which to print dates
#export PP_date_format="%Y-%m-%d %H:%M:%S%z"

# An override for The default format for log messages
#export PP_log_format="[%(asctime)s] %(levelname)s %(name)s: %(message)s"

# An override for The path to a file that details specific loggers to keep quiet and only log errors
#export PP_loggers_to_quiet="${PP_APP_PATH}/resources/log_level_override"

# An override for The path to a json log configuration
#export PP_json_log_path=

# An override for The path to a log level override path to give granular control over different logger's log levels
#export PP_log_level_override_path="${PP_APP_PATH}/resources/log_level_override.json"

# An override for The log level for a JSON log
#export PP_json_log_level=INFO

# An override for The maximum size of a json log before it rotates, in bytes
#export PP_json_log_maximum_bytes=

# An override for Where static resources are
#export PP_resource_path="${PP_APP_PATH}/resources/"

# An override for The path to statistical thresholds
#export PP_threshold_path="${PP_APP_PATH}/resources/thresholds/"

# An override for where to find masks
#export PP_mask_path="${PP_APP_PATH}/resources/masks"

# An override for where to find routelinks
#export PP_routelink_path="${PP_APP_PATH}/resources/routelink"

# An override for The path to the application log configuration
#export PP_log_config_path="${PP_APP_PATH}/resources/python_log_config.json"

# An override for Where to store intermediate files
#export PP_intermediate_directory="${PP_APP_PATH}/intermediate"

# An override for where to find profiles
#export PP_profile_path="${PP_APP_PATH}/resources/profiles/"

# An override for the maximum number of threads that may be used
#export PP_MAXIMUM_ADDITIONAL_THREADS=3

# An override for how verbose messages may be on a very granular level
#export PP_VERBOSITY=0

# An override for what netcdf engine to use by default
#export PP_default_netcdf_engine=h5netcdf

# An override for whether or not threads may be used
#export PP_allow_threading=True

# An override for how large the netcdf cache may be
#export PP_netcdf_cache_size=3

# An override for whether to lazy load netcdf data
#export PP_lazy_load_netcdf=True

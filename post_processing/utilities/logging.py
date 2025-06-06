"""
Handling for logging
"""
import logging.config
import typing
import logging
import pathlib

class OnlyErrorFilter(logging.Filter):
    """
    Only allows critical and error messages
    """
    def filter(self, record):
        return record.levelno in (logging.WARNING, logging.ERROR, logging.CRITICAL)
    

class ErrorExclusionFilter(logging.Filter):
    """
    Prevents errors from entering the log    
    """
    def filter(self, record):
        return record.levelno not in (logging.ERROR, logging.CRITICAL)
    
    
def setup_logging(log_path: typing.Union[pathlib.Path, str] = None):
    """
    Common setup logic for log configurations
    """
    if logging.getLogger().hasHandlers():
        return

    from post_processing.configuration import settings

    if log_path is None:
        log_path = settings.logging_config_path
    elif isinstance(log_path, (str, pathlib.Path)):
        log_path = pathlib.Path(log_path)

    if log_path is not None and log_path.is_file():
        import json
        configuration: typing.Dict = json.loads(log_path.read_text())
        logging.config.dictConfig(config=configuration)
    else:
        logging.basicConfig(
            level=logging.DEBUG if settings.debug else logging.INFO,
            format=settings.log_format,
            datefmt=settings.date_format
        )

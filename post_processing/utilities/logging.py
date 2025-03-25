"""
Handling for logging
"""
import logging.config
import typing
import logging

class OnlyErrorFilter(logging.Filter):
    """
    Only allows critical and error messages
    """
    def filter(self, record):
        return record.levelno in (logging.ERROR, logging.CRITICAL)
    

class ErrorExclusionFilter(logging.Filter):
    """
    Prevents errors from entering the log    
    """
    def filter(self, record):
        return record.levelno not in (logging.ERROR, logging.CRITICAL)
    
    
def setup_logging():
    """
    Common setup logic for log configurations
    """
    from post_processing.configuration import settings

    if settings.logging_config_path.is_file():
        import json
        configuration: typing.Dict = json.loads(settings.logging_config_path.read_text())
        logging.config.dictConfig(config=configuration)
    else:
        logging.basicConfig(
            level=logging.DEBUG if settings.debug else logging.INFO,
            format=settings.log_format,
            datefmt=settings.date_format
        )
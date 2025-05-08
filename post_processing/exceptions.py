"""
Defines common exceptions that may be reused
"""
import typing
import os

class ArgumentValidationException(Exception):
    """Occurs when arguments for an application are not valid"""
    def __init__(self, application_name: str, *message: str, messages: typing.Iterable[str]):
        self.application_name: str = application_name
        self.messages: typing.List[str] = list(message) + list(messages)
        separator: str = f"{os.linesep}    -"
        core_message: str = f"Arguments for {application_name} were invalid:{separator}{separator.join(self.messages)}"
        super().__init__(core_message)


class EditingDataException(Exception):
    """Occurs when operations are attempted on a dataset that is not in the correct mode for modification or access"""
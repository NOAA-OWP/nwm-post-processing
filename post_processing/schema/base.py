"""
Base classes for data models
"""
from __future__ import annotations
import typing
import dataclasses
import pathlib
import logging

import collections.abc as generic

T = typing.TypeVar("T")

MEMBER_FIELD_KEY: typing.Final[str] = "__IS_MEMBER__"
INIT_FUNCTION_KEY: typing.Final[str] = "__INIT_FUNCTION__"
LOAD_ORDER_KEY: typing.Final[str] = "__LOAD_ORDER__"


LOGGER: logging.Logger = logging.getLogger(pathlib.Path(__file__).stem)


def member(
    *,
    default: T = dataclasses.MISSING,
    default_factory: typing.Callable[[], T] = dataclasses.MISSING,
    metadata: dict = None,
    kw_only: bool = False,
    init_function: typing.Callable[[], typing.Any] = None,
    load_order: int = 0
) -> dataclasses.Field:
    """
    Create a member specific field
    """
    if metadata is None:
        metadata = {}

    metadata[MEMBER_FIELD_KEY] = True
    metadata[LOAD_ORDER_KEY] = load_order

    if callable(default_factory):
        metadata[INIT_FUNCTION_KEY] = init_function

    if dataclasses.MISSING not in (default, default_factory):
        raise ValueError(
            f"Cannot create a field - both a default value and a default factory cannot be specified - "
            f"choose one or the other"
        )
    elif default == dataclasses.MISSING and default_factory == dataclasses.MISSING:
        raise ValueError(f"An initial value must be given if a member variable is to be added")

    if default_factory != dataclasses.MISSING:
        if not callable(default_factory):
            raise TypeError(
                f"Cannot use '{default_factory}' (type={type(default_factory)} as the default factory as it is not callable"
            )
        field = dataclasses.field(
            default_factory=default_factory,
            metadata=metadata,
            init=False,
            compare=False,
            repr=False,
            kw_only=kw_only
        )
    else:
        field = dataclasses.field(
            default=default,
            metadata=metadata,
            init=False,
            compare=False,
            repr=False,
            kw_only=kw_only
        )

    return field


@dataclasses.dataclass
class BaseModel:
    """
    A base class for post-processing model objects
    """
    _raw_configuration: typing.Optional[str] = member(default=None, kw_only=True)

    @property
    def raw_configuration(self) -> typing.Optional[str]:
        return self._raw_configuration

    def __post_init__(self):
        self._validate()
        self.__load_members__()

    def __load_members__(self):
        loaders_and_order: list[tuple[typing.Callable[[], typing.Any], int]] = [
            (field.metadata[INIT_FUNCTION_KEY], field.metadata.get(LOAD_ORDER_KEY, 0))
            for field in get_fields(self)
            if not field.init
                and MEMBER_FIELD_KEY in field.metadata
                and isinstance(field.metadata.get(INIT_FUNCTION_KEY), typing.Callable)
        ]

        loaders: generic.Iterable[generic.Callable[[], typing.Any]] = map(
            lambda pair: pair[0],
            sorted(loaders_and_order, key=lambda pair: pair[1])
        )

        for loader in loaders:
            loader()

    @classmethod
    def from_dict(cls: typing.Type[ModelType], **kwargs: typing.Any) -> ModelType:
        """
        Load the model from a dictionary.

        :param kwargs: Keyword arguments
        :return: A newly constructed model
        """
        type_hints: dict[str, typing.Any] = typing.get_type_hints(cls)
        initial_values: dict[str, typing.Any] = {}

        for field in get_fields(cls):  # type: dataclasses.Field
            if field.name in kwargs:
                value: typing.Union[dict, typing.Any] = kwargs[field.name]
                expected_type: typing.Optional[typing.Type] = type_hints.get(field.name)

                if dataclasses.is_dataclass(expected_type) and isinstance(value, dict):
                    initial_values[field.name] = expected_type(**value)
                elif expected_type is None:
                    initial_values[field.name] = value
                else:
                    try:
                        initial_values[field.name] = expected_type(value)
                    except (TypeError, ValueError):
                        initial_values[field.name] = value
            elif field.default is not dataclasses.MISSING or field.default_factory is not dataclasses.MISSING:
                # The constructor of the dataclass will handle this
                continue
            else:
                raise ValueError(f"Missing a value for '{field.name}' - cannot construct a {cls.__qualname__}")

        try:
            instance: ModelType = cls(**initial_values)
        except Exception as e:
            import json
            import os
            from post_processing.utilities.common import to_json
            try:
                if isinstance(e, ExceptionGroup):
                    submessages: set[str] = {
                        getattr(exception, "message", str(exception))
                        for exception in e.exceptions
                    }
                    message: str = (
                        f"Could not construct a {cls.__qualname__} due to the following errors:{os.linesep}"
                        f"    - {(os.linesep + '    - ').join(submessages)}{os.linesep}"
                    )
                else:
                    message: str = (
                        f"Could not construct a {cls.__qualname__} from the following configuration:{os.linesep}"
                        f"{to_json(kwargs)}{os.linesep*2}"
                        f"Due to: {e}"
                    )
                raise RuntimeError(message) from e
            except Exception as json_exception:
                LOGGER.error(
                    f"Could not serialize the inputs to create a {cls.__qualname__}",
                    exc_info=json_exception
                )
                raise
        return instance

    @classmethod
    def from_json(
        cls: typing.Type[ModelType],
        path_or_buffer: typing.Union[pathlib.Path, str, typing.IO]
    ) -> ModelType:
        """
        Load model data from a JSON file

        :param path_or_buffer: Path or buffer or file-like object
        :return: A newly constructed model
        """
        import json

        try:
            if isinstance(path_or_buffer, (pathlib.Path, str)):
                with open(path_or_buffer, "r") as json_file:
                    text: str = json_file.read()
            elif isinstance(path_or_buffer, typing.IO):
                text = path_or_buffer.read()
            data = json.loads(text)

            deserialized_model: ModelType = cls.from_dict(**data)
            deserialized_model._raw_configuration = text
        except Exception as e:
            raise Exception(f"Could not load the configuration from {path_or_buffer}: {e}") from e
        return deserialized_model

    def _validate(self):
        """
        Validate and/or transform values on the model
        """
        pass

    def __getstate__(self):
        member_fields: set[str] = {
            field.name
            for field in dataclasses.fields(type(self))
            if not field.init and MEMBER_FIELD_KEY in field.metadata
        }
        vanilla_state: dict[str, typing.Any] = {
            key: value
            for key, value in super().__getstate__().items()
            if key not in member_fields
        }
        return vanilla_state

    def __setstate__(self, state):
        for key, value in state.items():
            if hasattr(self, key):
                setattr(self, key, value)
            else:
                raise ValueError(f"Cannot set the '{key}' value on a '{type(self)}' - that field does not exist")
        self.__load_members__()

    def to_dict(self) -> typing.Mapping[str, typing.Any]:
        """
        Convert the model to a dictionary ready for serialization

        Ensures that only fields needed to create the model are returned and not private state

        :returns: A mapping between public field name and field value for the model
        """
        fields = [
            field
            for field in dataclasses.fields(self)
            if field.init
                and MEMBER_FIELD_KEY not in field.metadata
                and not field.name.startswith("_")
        ]

        values: dict[str, typing.Any] = {}

        for field in fields:
            value: typing.Any = getattr(self, field.name)

            if hasattr(value, "to_dict") and callable(getattr(value, "to_dict")):
                value = value.to_dict()

            if value != field.default:
                values[field.name] = to_dict(obj=value)

        return values

ModelType = typing.TypeVar("ModelType", bound=BaseModel)


def get_fields(class_or_instance) -> typing.Sequence[dataclasses.Field]:
    """
    Get fields from a class that are meant to be internal runtime state, not recognizable, serializable values

    :param class_or_instance: Either a dataclass class or instance
    :return: A list of fields, or an empty list
    """
    fields = [
        field
        for field in dataclasses.fields(class_or_instance=class_or_instance)
        if not field.metadata or not field.metadata.get(MEMBER_FIELD_KEY, None)
    ]

    return fields

def to_dict(obj: typing.Any) -> typing.Union[dict[str, typing.Any], typing.Any]:
    """
    Convert a dataclass into a dictionary while excluding member fields

    :param obj: The dataclass to convert
    :return: The converted dictionary
    """
    if not dataclasses.is_dataclass(obj=obj):
        return obj

    dictionary: dict[str, typing.Any] = {
        field.name: getattr(obj, field.name)
        for field in get_fields(obj)
    }

    return dictionary

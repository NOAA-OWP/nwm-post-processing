"""
Base classes for data models
"""
from __future__ import annotations
import typing
import dataclasses
import pathlib
import logging
import enum

import collections.abc as generic

T = typing.TypeVar("T")

MEMBER_FIELD_KEY: typing.Final[str] = "__IS_MEMBER__"
INIT_FUNCTION_KEY: typing.Final[str] = "__INIT_FUNCTION__"
LOAD_ORDER_KEY: typing.Final[str] = "__LOAD_ORDER__"


LOGGER: logging.Logger = logging.getLogger(pathlib.Path(__file__).stem)


def member(
    *,
    default: T = dataclasses.MISSING,
    default_factory: generic.Callable[[], T] = dataclasses.MISSING,
    metadata: dict = None,
    init_function: generic.Callable[["BaseModel"], typing.Any] = None,
    load_order: int = 0,
    description: str = None
) -> dataclasses.Field[T]:
    """
    Create a member specific field

    This field will be internal to the class and meant to not be serialized or used by an outside entity whatsoever

    :param default: The default value to use for this field. If neither a default nor default_factory is given, the
    default for the field will be 'None'. Mutually exclusive to 'default_factory'
    :param default_factory: An optional function to use to generate the value for this field. Useful for creating
    collections. Mutually exclusive to 'default'
    :param metadata: General metadata to attach to the field for later reference
    :param init_function: Optional function to use to generate the initial value for this field. This function
    will receive the instance being created, so the given function may be self-referencing. Equate this to a member
    method passing 'self'
    :param load_order: An ordering key informing what order the to call this field's init function. If this field
    relies on another member field for initialization, set this load order number higher than the other's
    :param description: Optional description to attach to the field describing what it is for
    :returns: A field for a dataclass
    """
    if metadata is None:
        metadata = {}

    if isinstance(description, str) and len(description) > 0:
        metadata['description'] = description

    if default == dataclasses.MISSING and default_factory == dataclasses.MISSING:
        default = None

    metadata[MEMBER_FIELD_KEY] = True
    metadata[LOAD_ORDER_KEY] = load_order

    if callable(init_function):
        metadata[INIT_FUNCTION_KEY] = init_function

    if dataclasses.MISSING not in (default, default_factory):
        raise ValueError(
            f"Cannot create a field - both a default value and a default factory cannot be specified - "
            f"choose one or the other"
        )

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
            kw_only=True
        )
    else:
        field = dataclasses.field(
            default=default,
            metadata=metadata,
            init=False,
            compare=False,
            repr=False,
            kw_only=True
        )

    return field

def postprocessing_model(
    cls: typing.Optional[typing.Type[T]] = None,
    /,
    *,
    generate_init: bool = True,
    generate_repr: bool = True,
    comparable: bool = True,
    unsafe_hash: bool = False,
    frozen: bool = False
) -> generic.Callable[[typing.Type[T]], typing.Type[T]] | typing.Type[T]:
    """
    Wrapper for `dataclasses.dataclass` that ensures that subclasses may insert optional fields

    :param cls: The class to directly wrap
    :param generate_init: Whether to generate an __init__ function
    :param generate_repr: Whether to generate a __repr__ function
    :param comparable: Whether to generate comparison functions
    :param unsafe_hash: Whether to force the generation of a __hash__ function
    :param frozen: Whether created instances should pretend to be immutable
    """
    decorator: generic.Callable[[typing.Type[T]], typing.Type[T]] = dataclasses.dataclass(
        kw_only=True,
        init=generate_init,
        repr=generate_repr,
        eq=comparable,
        order=comparable,
        unsafe_hash=unsafe_hash,
        frozen=frozen
    )

    if cls is None:
        return decorator

    return decorator(cls)


@postprocessing_model
class BaseModel:
    """
    A base class for post-processing model objects
    """
    _raw_configuration: typing.Optional[str] = member()

    @classmethod
    def get_model_fields(cls) -> generic.Sequence[dataclasses.Field]:
        """
        Get all dataclass fields for this model
        """
        return list(getattr(cls, "__dataclass_fields__"))

    @property
    def raw_configuration(self) -> typing.Optional[str]:
        return self._raw_configuration

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        # did subclass override __post_init__?
        if "__post_init__" in cls.__dict__:
            msg = (
                f"{cls.__name__} overrides __post_init__, which breaks Base dataclass "
                f"initialization. Override _validate() instead."
            )

            from post_processing.configuration import settings
            if settings.debug:
                # Raise this as an error in debug mode to ensure it gets the attention it needs
                raise RuntimeError(msg)
            else:
                import warnings
                warnings.warn(msg, category=UserWarning, stacklevel=2)

    def __post_init__(self):
        self._validate()
        self.__load_members__()

    def __load_members__(self):
        loaders_and_order: list[tuple[generic.Callable[[], typing.Any], int]] = [
            (field.metadata[INIT_FUNCTION_KEY], field.metadata.get(LOAD_ORDER_KEY, 0))
            for field in get_fields(self)
            if not field.init
                and MEMBER_FIELD_KEY in field.metadata
                and isinstance(field.metadata.get(INIT_FUNCTION_KEY), generic.Callable)
        ]

        loaders: generic.Iterable[generic.Callable[[BaseModel], typing.Any]] = map(
            lambda pair: pair[0],
            sorted(loaders_and_order, key=lambda pair: pair[1])
        )

        for loader in loaders:
            loader(self)

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
                expected_type: typing.Optional[generic.Callable[[typing.Any], typing.Any]] = type_hints.get(field.name)

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
            for field in self.get_model_fields()
            if not field.init and MEMBER_FIELD_KEY in field.metadata
        }
        vanilla_state: dict[str, typing.Any] = {
            key: value
            for key, value in typing.cast(dict, super().__getstate__()).items()
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

    def to_dict(self) -> generic.Mapping[str, typing.Any]:
        """
        Convert the model to a dictionary ready for serialization

        Ensures that only fields needed to create the model are returned and not private state

        :returns: A mapping between public field name and field value for the model
        """
        fields = [
            field
            for field in self.get_model_fields()
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


def get_fields(class_or_instance) -> generic.Sequence[dataclasses.Field]:
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
        if hasattr(obj, "to_dict") and callable(obj.to_dict):
            return obj.to_dict()
        if isinstance(obj, enum.Enum):
            return obj.value
        return obj

    dictionary: dict[str, typing.Any] = {
        field.name: getattr(obj, field.name)
        for field in get_fields(obj)
    }

    return dictionary

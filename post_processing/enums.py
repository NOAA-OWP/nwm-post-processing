"""
Enumerations used to describe data
"""
import typing
import enum


class PostProcessingEnumeration(enum.Enum):
    """
    Abstract class defining common methods and behavior for application specific enums
    """
    @classmethod
    def from_string(cls, value: str):
        """
        Get an instance of this enum based off of its value
        """
        for member in cls:
            if member.value == value:
                return member
        raise ValueError(f"'{value}' is not a valid value within {cls.__name__}")
    
    @classmethod
    def pattern(cls) -> str:
        """
        Get a regular expression demonstrating how to extract this value from a string
        """
        return rf"(?P<{cls.__name__}>{'|'.join([member.value for member in cls])})"


class ModelOutputType(PostProcessingEnumeration):
    ChannelRouting = "channel_rt"
    Forcing = "forcing"
    Land = "land"


class Configuration(PostProcessingEnumeration):
    ShortRange = "short_range"
    LongRange = "long_range"
    MediumRange = "medium_range"
    MediumRangeNoDA = "medium_range_no_da"
    MediumRangeBlend = "medium_range_blend"
    MediumRangeNFDF = "medium_range_nfdf"
    AnalysisAssimilation = "analysis_assim"
    ExtendedAnalysisAssimilation = "analysis_assim_extend"
    ExtendedAnalysisAssimilationNoDA = "analysis_assim_extend_no_da"
    LongAnalysisAssimilation = "analysis_assimilation_long"
    LongAnalysisAssimilationNoDA = "analysis_assimilation_long_no_da"


class Region(PostProcessingEnumeration):
    ABRFC = "abrfc"
    APRFC = "aprfc"
    CBRFC = "cbrfc"
    CNRFC = "cnrfc"
    LMRFC = "lmrfc"
    MARFC = "marfc"
    MBRFC = "mbrfc"
    NCRFC = "ncrfc"
    NERFC = "nerfc"
    """Norteast River Forecast Center"""
    NWRFC = "nwrfc"
    """Nortwest River Forecast Center"""
    OHRFC = "ohrfc"
    """Ohio River Valley River Forecast Center"""
    SERFC = "serfc"
    """Southeast River Forecast Center"""
    WGRFC = "wgrfc"
    AlaskaAPRFC = "alaska.aprfc"
    Alaska = "alaska"
    HawaiiAPRFC = "hawaii.aprfc"
    Hawaii = "hawaii"
    PuertoRico = "puertorico"

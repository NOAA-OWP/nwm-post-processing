"""
Enumerations used to describe data
"""
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
    """
    Enumerates what type of data was modeled
    """
    ChannelRouting = "channel_rt"
    Forcing = "forcing"
    Land = "land"


class Configuration(PostProcessingEnumeration):
    """
    Enumerates the different ways a model may be configured for forecast/simulation length and input parameters
    """
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
    """Enumerates the different ways Regions/RFCs/General areas may be represented within strings"""
    ABRFC = "abrfc"
    """Arkansas Red-Basin River Forecast Center"""
    APRFC = "aprfc"
    """Alaska Pacific River Forecast Center"""
    CBRFC = "cbrfc"
    """Colorado Basin River Forecast Center"""
    CNRFC = "cnrfc"
    """California Nevada River Forecast Center"""
    LMRFC = "lmrfc"
    """Lower Mississippi River Forecast Center"""
    MARFC = "marfc"
    """Mid-Atlantic River Forecast Center"""
    MBRFC = "mbrfc"
    """Missouri Basin River Forecast Center"""
    NCRFC = "ncrfc"
    """North Central River Forecast Center"""
    NERFC = "nerfc"
    """Norteast River Forecast Center"""
    NWRFC = "nwrfc"
    """Nortwest River Forecast Center"""
    OHRFC = "ohrfc"
    """Ohio River Valley River Forecast Center"""
    SERFC = "serfc"
    """Southeast River Forecast Center"""
    WGRFC = "wgrfc"
    """West Gulf River Forecast Center"""
    AlaskaAPRFC = "alaska.aprfc"
    """The state of Alaska in relation to APRFC"""
    Alaska = "alaska"
    """The state of Alaska"""
    HawaiiAPRFC = "hawaii.aprfc"
    """The state of hawaii in relation to APRFC"""
    Hawaii = "hawaii"
    """The state of Hawaii"""
    PuertoRico = "puertorico"
    """The territory of Puerto Rico"""
    PuertoRicoSERFC = "puertorico.serfc"
    """The territory of Puerto Rico in relation to SERFC"""
    CONUS = "conus"
    """Continental United States"""


class RFC(enum.Enum):
    """
    Enumerates the ways that River Forecast Centers may be declared with their 2 character abbreviation
    """
    ABRFC = "AB"
    """Arkansas Red-Basin River Forecast Center"""
    APRFC = "AP"
    """Alaska-Pacific River Forecast Center"""
    CBRFC = "CB"
    """Colorado Basin River Forecast Center"""
    CNRFC = "CN"
    """California Nevada River Forecast Center"""
    LMRFC = "LM"
    """Lower Mississippi River Forecast Center"""
    MARFC = "MA"
    """Mid-Atlantic River Forecast Center"""
    MBRFC = "MB"
    """Missouri Basin River Forecast Center"""
    NCRFC = "NC"
    """North Central River Forecast Center"""
    NERFC = "NE"
    """Norteast River Forecast Center"""
    NWRFC = "NW"
    """Nortwest River Forecast Center"""
    OHRFC = "OH"
    """Ohio River Valley River Forecast Center"""
    SERFC = "SE"
    """Southeast River Forecast Center"""
    WGRFC = "WG"
    """West Gulf River Forecast Center"""
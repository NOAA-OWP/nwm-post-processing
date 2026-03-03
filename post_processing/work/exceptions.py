#!/usr/bin/env python3
"""
Defines exceptions used with work distribution
"""

class GatewayError(IOError):
    """General purpose error for when an operation failed or was cancelled"""

class WriteCancelledByGatewayError(GatewayError):
    """Exception for when NetCDF Writing was cancelled"""

class LoadCanceledByGatewayError(GatewayError):
    """Exception for when NetCDF Loading was cancelled"""

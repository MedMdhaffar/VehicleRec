"""DB1 construction interfaces; legacy SQL dependencies load only on demand."""
from .core import UUID5Policy, FrozenJSON
from .validation import ContractValidation
from .natural import NaturalBuilder, NaturalBundle, NaturalPlateBuilder

__all__ = ['RowBuilder', 'UUID5Policy', 'Construction', 'FactResult', 'FrozenJSON',
           'Dimension', 'ResolvedModel', 'RunResolution', 'MediaResolution', 'Resolutions',
           'ContractValidation', 'NaturalBuilder', 'NaturalBundle', 'NaturalPlateBuilder']


def __getattr__(name):
    if name in {'RowBuilder', 'Construction', 'FactResult'}:
        from . import builder
        return getattr(builder, name)
    if name in {'Dimension', 'ResolvedModel', 'RunResolution', 'MediaResolution', 'Resolutions'}:
        from . import records
        return getattr(records, name)
    raise AttributeError(name)

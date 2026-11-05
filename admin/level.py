"""Administrative division levels shared by the repository tables.

The values are the strings written to the level column: 0 country, 1 province,
2 prefecture, 3 county, 4 township.
"""

from enum import StrEnum


class Level(StrEnum):
    NATION = '0'
    PROVINCE = '1'
    PREFECTURE = '2'
    COUNTY = '3'
    TOWNSHIP = '4'

    @classmethod
    def from_code(cls, code: str) -> 'Level':
        """Return the level for an administrative code.

        Province-administered county-level units use six digits and stay COUNTY.
        """
        if code == '00':
            return cls.NATION
        if len(code) == 2:
            return cls.PROVINCE
        if len(code) == 4:
            return cls.PREFECTURE
        if len(code) == 6:
            return cls.COUNTY
        if len(code) == 9:
            return cls.TOWNSHIP
        raise ValueError(f'Invalid code length: {len(code)}')

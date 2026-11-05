"""Shared type and generic-name rules for Hong Kong, Macau and Taiwan pages.

These regions do not follow the mainland administrative classification (Taiwan
has no prefecture level, a province-administered city is a 市, and below a
county comes a 縣轄市), so page names and types stay in traditional Chinese.
The citypopulation.de and dsec.gov.mo update scripts use these functions to
convert page spellings into the repository format.
"""

# Types whose generic part is not the type name itself; None means the name has
# no generic part (Hong Kong's 選區 are like 寶翠, 衛城). Every other type uses
# the type name as the generic.
SUFFIX_EXCEPTIONS = {
    '直轄市': '市', '市轄區': '區', '縣轄市': '市', '區議會': '區', '選區': None,
}


def suffix_of(type_):
    """Generic part for a type: the type itself unless SUFFIX_EXCEPTIONS says otherwise."""
    return SUFFIX_EXCEPTIONS.get(type_, type_)


def split_name(name, type_):
    """Split a page name into the repository's 「专名:通名」.

    Two names lack or shorten the generic part, so it is restored first:
        堂區    the page writes 堂區 as 堂 (花地瑪堂, 聖安多尼堂…); the trailing
                堂 is dropped before 堂區 is appended. 大堂 keeps its 堂, since
                dropping it would leave only 大.
        區議會  the 區 is missing from 深水埗 (the other 17 have it) and is appended.
    """
    suffix = suffix_of(type_)
    if suffix is None:
        return name
    if type_ == '堂區' and name.endswith('堂') and len(name) >= 3:
        name = name[:-1]
    if name.endswith(suffix) and len(name) > len(suffix):
        return f'{name[:-len(suffix)]}:{suffix}'
    return f'{name}:{suffix}'


def proper_of(name):
    """Return the proper-name part of 「专名:通名」."""
    return name.split(':')[0]

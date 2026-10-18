"""Chinese zodiac from a birth date, using the Chinese New Year date of each year.

Chinese New Year (CNY) moves between Jan 21 and Feb 20, so someone born before it
belongs to the previous zodiac year. This module uses a local table of CNY dates for
1900-2035 (no internet, no AI). The table was cross-checked with an independent
astronomical calculation: see tools/verify_cny.py.
"""
from datetime import date

MIN_YEAR, MAX_YEAR = 1900, 2035

ZODIACS = ["rat", "ox", "tiger", "rabbit", "dragon", "snake",
           "horse", "goat", "monkey", "rooster", "dog", "pig"]
ZODIAC_MY = {"rat": "ကြွက်", "ox": "နွား", "tiger": "ကျား", "rabbit": "ယုန်",
             "dragon": "နဂါး", "snake": "မြွေ", "horse": "မြင်း", "goat": "ဆိတ်",
             "monkey": "မျောက်", "rooster": "ကြက်", "dog": "ခွေး", "pig": "ဝက်"}
ZODIAC_EMOJI = {"rat": "🐭", "ox": "🐮", "tiger": "🐯", "rabbit": "🐰", "dragon": "🐲",
                "snake": "🐍", "horse": "🐴", "goat": "🐐", "monkey": "🐵",
                "rooster": "🐔", "dog": "🐶", "pig": "🐷"}

# year -> (month, day) of Chinese New Year
_CNY_TEXT = """
1900 1-31|1901 2-19|1902 2-8|1903 1-29|1904 2-16|1905 2-4|1906 1-25|1907 2-13|1908 2-2|1909 1-22
1910 2-10|1911 1-30|1912 2-18|1913 2-6|1914 1-26|1915 2-14|1916 2-3|1917 1-23|1918 2-11|1919 2-1
1920 2-20|1921 2-8|1922 1-28|1923 2-16|1924 2-5|1925 1-24|1926 2-13|1927 2-2|1928 1-23|1929 2-10
1930 1-30|1931 2-17|1932 2-6|1933 1-26|1934 2-14|1935 2-4|1936 1-24|1937 2-11|1938 1-31|1939 2-19
1940 2-8|1941 1-27|1942 2-15|1943 2-5|1944 1-25|1945 2-13|1946 2-2|1947 1-22|1948 2-10|1949 1-29
1950 2-17|1951 2-6|1952 1-27|1953 2-14|1954 2-3|1955 1-24|1956 2-12|1957 1-31|1958 2-18|1959 2-8
1960 1-28|1961 2-15|1962 2-5|1963 1-25|1964 2-13|1965 2-2|1966 1-21|1967 2-9|1968 1-30|1969 2-17
1970 2-6|1971 1-27|1972 2-15|1973 2-3|1974 1-23|1975 2-11|1976 1-31|1977 2-18|1978 2-7|1979 1-28
1980 2-16|1981 2-5|1982 1-25|1983 2-13|1984 2-2|1985 2-20|1986 2-9|1987 1-29|1988 2-17|1989 2-6
1990 1-27|1991 2-15|1992 2-4|1993 1-23|1994 2-10|1995 1-31|1996 2-19|1997 2-7|1998 1-28|1999 2-16
2000 2-5|2001 1-24|2002 2-12|2003 2-1|2004 1-22|2005 2-9|2006 1-29|2007 2-18|2008 2-7|2009 1-26
2010 2-14|2011 2-3|2012 1-23|2013 2-10|2014 1-31|2015 2-19|2016 2-8|2017 1-28|2018 2-16|2019 2-5
2020 1-25|2021 2-12|2022 2-1|2023 1-22|2024 2-10|2025 1-29|2026 2-17|2027 2-6|2028 1-26|2029 2-13
2030 2-3|2031 1-23|2032 2-11|2033 1-31|2034 2-19|2035 2-8
"""


def _load():
    table = {}
    for item in _CNY_TEXT.replace("\n", "|").split("|"):
        item = item.strip()
        if not item:
            continue
        y, md = item.split()
        m, d = md.split("-")
        table[int(y)] = (int(m), int(d))
    return table


CNY = _load()
assert len(CNY) == MAX_YEAR - MIN_YEAR + 1


def cny_date(year):
    m, d = CNY[year]
    return date(year, m, d)


def zodiac_year(d):
    """The zodiac year (not the calendar year) a date belongs to."""
    if not isinstance(d, date) or not (MIN_YEAR <= d.year <= MAX_YEAR):
        raise ValueError("unsupported date")
    return d.year if d >= cny_date(d.year) else d.year - 1


def zodiac_for(d):
    """Return the zodiac key ('rat'...'pig'). Raises ValueError if unsupported."""
    return ZODIACS[(zodiac_year(d) - 4) % 12]


BURMESE_DIGITS = str.maketrans("၀၁၂၃၄၅၆၇၈၉", "0123456789")


def parse_birthdate(text, today=None):
    """Parse DD-MM-YYYY (also / or . and Burmese digits). Returns a date.
    Raises ValueError with a short reason code: format | date | range | future."""
    s = (text or "").strip().translate(BURMESE_DIGITS)
    for sep in ("/", ".", " "):
        s = s.replace(sep, "-")
    parts = s.split("-")
    if len(parts) != 3 or not all(p.isdigit() for p in parts) or len(parts[2]) != 4:
        raise ValueError("format")
    dd, mm, yy = (int(p) for p in parts)
    try:
        d = date(yy, mm, dd)
    except ValueError:
        raise ValueError("date")
    if not (MIN_YEAR <= yy <= MAX_YEAR):
        raise ValueError("range")
    if today and d > today:
        raise ValueError("future")
    return d

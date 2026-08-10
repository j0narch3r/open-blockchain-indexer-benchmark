"""Generate a faithful small San Francisco street network as OSM-shaped GeoJSON.

Coordinates are derived from rotated local metric frames fitted to well-known
real intersections (see sf_graph.notes.md).  Nodes are created by intersecting
street centre-lines, so every shared node is byte-identical by construction.
"""

from __future__ import annotations

import json
import math
from typing import Iterable

# Degrees per metre near 37.77 N.
LON_M = 1.0 / 88000.0
LAT_M = 1.0 / 111000.0

Pt = tuple[float, float]


class Frame:
    """Local metric frame.  +x runs along the 'east-west' streets at `bearing`."""

    def __init__(self, lon0: float, lat0: float, bearing: float) -> None:
        b = math.radians(bearing)
        b2 = math.radians(bearing - 90.0)
        self.o = (lon0, lat0)
        self.ex = (math.sin(b) * LON_M, math.cos(b) * LAT_M)
        self.ey = (math.sin(b2) * LON_M, math.cos(b2) * LAT_M)

    def pt(self, x: float, y: float) -> Pt:
        return (
            self.o[0] + x * self.ex[0] + y * self.ey[0],
            self.o[1] + x * self.ex[1] + y * self.ey[1],
        )


class Line:
    __slots__ = ("p", "d")

    def __init__(self, p: Pt, d: Pt) -> None:
        self.p = p
        self.d = d


def line_through(a: Pt, b: Pt) -> Line:
    return Line(a, (b[0] - a[0], b[1] - a[1]))


def vline(f: Frame, x: float) -> Line:
    """A north-south street of frame `f` at constant x."""
    return Line(f.pt(x, 0.0), f.ey)


def hline(f: Frame, y: float) -> Line:
    """An east-west street of frame `f` at constant y."""
    return Line(f.pt(0.0, y), f.ex)


def isect(a: Line, b: Line) -> Pt:
    det = a.d[0] * (-b.d[1]) - a.d[1] * (-b.d[0])
    if abs(det) < 1e-14:
        raise ValueError("parallel streets do not intersect")
    rx = b.p[0] - a.p[0]
    ry = b.p[1] - a.p[1]
    t = (rx * (-b.d[1]) - ry * (-b.d[0])) / det
    return (a.p[0] + t * a.d[0], a.p[1] + t * a.d[1])


def rnd(p: Pt) -> Pt:
    return (round(p[0], 6), round(p[1], 6))


def metres(a: Pt, b: Pt) -> float:
    dx = (b[0] - a[0]) / LON_M
    dy = (b[1] - a[1]) / LAT_M
    return math.hypot(dx, dy)


# --------------------------------------------------------------------------
# registries
# --------------------------------------------------------------------------

STREETS: dict[str, Line] = {}
NAMES: dict[str, str] = {}
NODES: dict[tuple[str, str], Pt] = {}
FREE: dict[str, Pt] = {}
FEATS: list[dict] = []


def street(sid: str, name: str, ln: Line) -> str:
    STREETS[sid] = ln
    NAMES[sid] = name
    return sid


def node(a: str, b: str) -> Pt:
    key = (a, b) if a <= b else (b, a)
    if key not in NODES:
        NODES[key] = rnd(isect(STREETS[key[0]], STREETS[key[1]]))
    return NODES[key]


def free(tag: str, lon: float, lat: float) -> Pt:
    """A hand-placed node (for curved ways).  Reused by tag."""
    if tag not in FREE:
        FREE[tag] = rnd((lon, lat))
    return FREE[tag]


def way(name: str, highway: str, coords: Iterable[Pt], **props: object) -> None:
    cs = [list(c) for c in coords]
    ded: list[list[float]] = []
    for c in cs:
        if not ded or ded[-1] != c:
            ded.append(c)
    if len(ded) < 2:
        raise ValueError(f"degenerate way {name}")
    p: dict[str, object] = {"name": name, "highway": highway}
    p.update({k: v for k, v in props.items() if v is not None})
    FEATS.append(
        {"type": "Feature", "properties": p,
         "geometry": {"type": "LineString", "coordinates": ded}}
    )


def order(sid: str, crosses: list[str]) -> list[str]:
    ln = STREETS[sid]

    def key(c: str) -> float:
        p = node(sid, c)
        return (p[0] - ln.p[0]) * ln.d[0] + (p[1] - ln.p[1]) * ln.d[1]

    return sorted(crosses, key=key)


def chain(sid: str, highway: str, crosses: list[str], name: str | None = None,
          sort: bool = True, **props: object) -> None:
    """Emit one feature per block of street `sid` between consecutive crossings."""
    cs = order(sid, crosses) if sort else list(crosses)
    nm = name or NAMES[sid]
    for a, b in zip(cs, cs[1:]):
        way(nm, highway, [node(sid, a), node(sid, b)], **props)


def polychain(name: str, highway: str, pts: list[Pt], **props: object) -> None:
    """Emit one feature per consecutive pair of an explicit polyline."""
    for a, b in zip(pts, pts[1:]):
        way(name, highway, [a, b], **props)


# ==========================================================================
# FRAMES
# ==========================================================================
# Frame A: the Western Addition / Haight / Panhandle / downtown-north grid.
#   Fitted to Haight & Ashbury and Haight & Fillmore -> the grid runs 082/352,
#   i.e. about 8 degrees off cardinal, which matches Broadway's real drift too.
A = Frame(-122.446800, 37.769900, 82.0)

# Frame M: the Mission / Castro grid.  Fitted to 16th & Mission, 16th & Potrero
# and 24th & Mission -> runs 086.4/356.4.
M = Frame(-122.419300, 37.765100, 86.4)

# Frame S: SoMa, parallel/perpendicular to Market Street (bearing 045.94).
S = Frame(-122.435200, 37.762700, 45.94)

# Frame N: Nob Hill / Russian Hill / North Beach / Telegraph Hill / Pacific
# Heights.  Frame A was accurate in the Haight but accumulated ~200 m of error
# 4.5 km away at Telegraph Hill, so the north-east gets its own frame, fitted
# to Lombard & Hyde (origin, exact), California & Powell and Coit Tower.
# Same 082/352 grid bearing - the tilt is a real property of the SF grid.
N = Frame(-122.418900, 37.802000, 82.0)

# ---- Frame N offsets: x metres east of Hyde, y metres north of Lombard ----
NX = {
    "larkin": -123, "hyde": 0, "leavenworth": 123, "jones": 246,
    "taylor": 370, "mason": 493, "powell": 616, "stockton": 762,
    "grant": 908, "kearny": 1054, "montgomery": 1200, "sansome": 1346,
}
NY = {
    "lombard": 0, "greenwich": -92, "filbert": -184, "union": -276,
    "green": -368, "vallejo": -460, "broadway": -553, "pacific": -668,
    "jackson": -783, "washington": -898, "clay": -1013, "sacramento": -1128,
    "california": -1241, "pine": -1354, "bush": -1467, "sutter": -1580,
    "post": -1693, "geary": -1806, "ofarrell": -1919, "ellis": -2032,
    "eddy": -2145, "turk": -2258, "golden_gate": -2371, "mcallister": -2484,
    "fulton": -2597,
}

# ---- Frame A x offsets (metres east of Ashbury Street) -------------------
AX = {
    "ninth_ave": -1880, "transverse": -1700, "fifth_ave": -1300,
    "arguello": -1010, "presidio": -230, "stow_lake": -1250,
    "kezar": -720,
    "stanyan": -568, "shrader": -426, "cole": -284, "clayton": -142,
    "ashbury": 0, "masonic": 142, "central": 284, "lyon": 426, "baker": 568,
    "broderick": 710, "divisadero": 852, "scott": 994, "pierce": 1136,
    "steiner": 1240, "fillmore": 1425, "webster": 1559, "buchanan": 1701,
    "laguna": 1843, "octavia": 1985, "gough": 2127, "franklin": 2269,
    "vanness": 2411, "polk": 2553, "larkin": 2695, "hyde": 2837,
    "leavenworth": 2979, "jones": 3121, "taylor": 3263, "mason": 3405,
    "powell": 3547, "stockton": 3689, "grant": 3831, "kearny": 3973,
    "montgomery": 4115, "sansome": 4257,
}
# ---- Frame A y offsets (metres north of Haight Street) -------------------
AY = {
    "carl": -520, "lincoln": -440, "frederick": -361, "mlk": -250,
    "duboce": -245, "waller": -110, "haight": 0, "middle_e": 110,
    "page": 115, "oak": 230, "panhandle": 287, "fell": 345, "jfk": 550,
    "ggp_mup": 620, "fulton": 690, "mcallister": 805, "turk": 1035,
    "geary": 1495, "sutter": 1725, "pine": 1955, "california": 2070,
    "clay": 2300, "jackson": 2530, "broadway": 2760, "vallejo": 2875,
    "green": 2990, "union": 3105, "filbert": 3220, "greenwich": 3335,
    "lombard": 3450,
}
# ---- Frame M x offsets (metres east of Mission Street) -------------------
MX = {
    "castro": -1440, "noe": -1262, "sanchez": -1040, "church": -850,
    "dolores": -676, "guerrero": -456, "valencia": -203, "mission": 0,
    "capp": 106, "svn": 238, "folsom": 520, "harrison": 644,
    "bryant": 864, "potrero": 1050, "vermont": 1155, "kansas": 1260,
    "rhode_island": 1365, "de_haro": 1470, "carolina": 1575,
    "wisconsin": 1680, "arkansas": 1785, "connecticut": 1890,
    "missouri": 1995, "texas": 2100, "mississippi": 2205,
    "pennsylvania": 2310, "indiana": 2415, "minnesota": 2520,
    "tennessee": 2625, "third": 2730,
}
# ---- Frame M y offsets (metres north of 16th Street) ---------------------
MY = {
    "14th": 356, "15th": 178, "16th": 0, "17th": -178, "18th": -356,
    "19th": -534, "20th": -712, "21st": -890, "22nd": -1068,
    "23rd": -1246, "24th": -1424, "25th": -1602, "26th": -1780,
    "clipper": -1725,
}
# ---- Frame S offsets ------------------------------------------------------
SX = {
    "11th": 1761, "10th": 1997, "9th": 2232, "8th": 2468, "7th": 2704,
    "6th": 2939, "5th": 3175, "4th": 3410, "3rd": 3646, "2nd": 3881,
    "1st": 4117, "embarcadero": 4565,
}
SY = {
    "market": 0, "mission_soma": -105, "howard": -320, "folsom_soma": -545,
    "harrison_soma": -760, "bryant_soma": -975, "brannan": -1200,
    "townsend": -1420,
}

# ==========================================================================
# STREET CENTRE-LINES
# ==========================================================================

# --- Frame A north-south streets ---
for _sid, _nm in [
    ("ninth_ave", "9th Avenue"), ("transverse", "Transverse Drive"),
    ("fifth_ave", "5th Avenue"), ("arguello", "Arguello Boulevard"),
    ("presidio", "Presidio Avenue"), ("stow_lake", "Stow Lake Drive"),
    ("kezar", "Kezar Drive"), ("stanyan", "Stanyan Street"),
    ("shrader", "Shrader Street"), ("cole", "Cole Street"),
    ("clayton", "Clayton Street"), ("ashbury", "Ashbury Street"),
    ("masonic", "Masonic Avenue"), ("central", "Central Avenue"),
    ("lyon", "Lyon Street"), ("baker", "Baker Street"),
    ("broderick", "Broderick Street"), ("divisadero", "Divisadero Street"),
    ("scott", "Scott Street"), ("pierce", "Pierce Street"),
    ("steiner", "Steiner Street"), ("fillmore", "Fillmore Street"),
    ("webster", "Webster Street"), ("buchanan", "Buchanan Street"),
    ("laguna", "Laguna Street"), ("octavia", "Octavia Street"),
    ("gough", "Gough Street"), ("franklin", "Franklin Street"),
    ("vanness", "Van Ness Avenue"), ("polk", "Polk Street"),
]:
    street(_sid, _nm, vline(A, AX[_sid]))

# --- Frame A east-west streets ---
for _sid, _nm in [
    ("carl", "Carl Street"), ("lincoln", "Lincoln Way"),
    ("frederick", "Frederick Street"), ("mlk", "Martin Luther King Junior Drive"),
    ("waller", "Waller Street"),
    ("haight", "Haight Street"), ("middle_e", "Middle Drive East"),
    ("page", "Page Street"), ("oak", "Oak Street"),
    ("panhandle", "Panhandle Path"), ("fell", "Fell Street"),
    ("jfk", "John F. Kennedy Drive"),
    ("ggp_mup", "Golden Gate Park Multi-Use Path"),
]:
    street(_sid, _nm, hline(A, AY[_sid]))

# --- Frame N north-south streets ---
for _sid, _nm in [
    ("larkin", "Larkin Street"), ("hyde", "Hyde Street"),
    ("leavenworth", "Leavenworth Street"), ("jones", "Jones Street"),
    ("taylor", "Taylor Street"), ("mason", "Mason Street"),
    ("powell", "Powell Street"), ("stockton", "Stockton Street"),
    ("grant", "Grant Avenue"), ("kearny", "Kearny Street"),
    ("montgomery", "Montgomery Street"), ("sansome", "Sansome Street"),
]:
    street(_sid, _nm, vline(N, NX[_sid]))

# --- Frame N east-west streets ---
for _sid, _nm in [
    ("fulton", "Fulton Street"), ("mcallister", "McAllister Street"),
    ("golden_gate", "Golden Gate Avenue"), ("turk", "Turk Street"),
    ("eddy", "Eddy Street"), ("ellis", "Ellis Street"),
    ("ofarrell", "O'Farrell Street"), ("geary", "Geary Boulevard"),
    ("post", "Post Street"), ("sutter", "Sutter Street"),
    ("bush", "Bush Street"), ("pine", "Pine Street"),
    ("california", "California Street"), ("sacramento", "Sacramento Street"),
    ("clay", "Clay Street"), ("washington", "Washington Street"),
    ("jackson", "Jackson Street"), ("pacific", "Pacific Avenue"),
    ("broadway", "Broadway"), ("vallejo", "Vallejo Street"),
    ("green", "Green Street"), ("union", "Union Street"),
    ("filbert", "Filbert Street"), ("greenwich", "Greenwich Street"),
    ("lombard", "Lombard Street"),
]:
    street(_sid, _nm, hline(N, NY[_sid]))

# --- Frame M north-south streets ---
for _sid, _nm in [
    ("castro", "Castro Street"), ("noe", "Noe Street"),
    ("sanchez", "Sanchez Street"), ("church", "Church Street"),
    ("dolores", "Dolores Street"), ("guerrero", "Guerrero Street"),
    ("valencia", "Valencia Street"), ("mission", "Mission Street"),
    ("capp", "Capp Street"), ("svn", "South Van Ness Avenue"),
    ("folsom", "Folsom Street"), ("harrison", "Harrison Street"),
    ("bryant", "Bryant Street"), ("potrero", "Potrero Avenue"),
    ("vermont", "Vermont Street"), ("kansas", "Kansas Street"),
    ("rhode_island", "Rhode Island Street"), ("de_haro", "De Haro Street"),
    ("carolina", "Carolina Street"), ("wisconsin", "Wisconsin Street"),
    ("arkansas", "Arkansas Street"), ("connecticut", "Connecticut Street"),
    ("missouri", "Missouri Street"), ("texas", "Texas Street"),
    ("mississippi", "Mississippi Street"),
    ("pennsylvania", "Pennsylvania Avenue"), ("indiana", "Indiana Street"),
    ("minnesota", "Minnesota Street"), ("tennessee", "Tennessee Street"),
    ("third", "3rd Street"),
]:
    street(_sid, _nm, vline(M, MX[_sid]))

# --- Frame M east-west streets ---
for _sid, _nm in [
    ("14th", "14th Street"), ("15th", "15th Street"), ("16th", "16th Street"),
    ("17th", "17th Street"), ("18th", "18th Street"), ("19th", "19th Street"),
    ("20th", "20th Street"), ("21st", "21st Street"), ("22nd", "22nd Street"),
    ("23rd", "23rd Street"), ("24th", "24th Street"), ("25th", "25th Street"),
    ("26th", "26th Street"), ("clipper", "Clipper Street"),
]:
    street(_sid, _nm, hline(M, MY[_sid]))

# --- Frame S ---
for _sid, _nm in [
    ("s11th", "11th Street"), ("s10th", "10th Street"), ("s9th", "9th Street"),
    ("s8th", "8th Street"), ("s7th", "7th Street"), ("s6th", "6th Street"),
    ("s5th", "5th Street"), ("s4th", "4th Street"), ("s3rd", "3rd Street"),
    ("s2nd", "2nd Street"), ("s1st", "1st Street"),
    ("embarcadero", "The Embarcadero"),
]:
    street(_sid, _nm, vline(S, SX[_sid.replace("s", "", 1) if _sid.startswith("s") and _sid[1].isdigit() else _sid]))
for _sid, _nm in [
    ("market", "Market Street"), ("mission_soma", "Mission Street"),
    ("howard", "Howard Street"), ("folsom_soma", "Folsom Street"),
    ("harrison_soma", "Harrison Street"), ("bryant_soma", "Bryant Street"),
    ("brannan", "Brannan Street"), ("townsend", "Townsend Street"),
]:
    street(_sid, _nm, hline(S, SY[_sid]))

# --- streets that do not belong to any frame ---
# Duboce Avenue is not on the Western Addition grid: it runs essentially
# level (very slightly gaining latitude westward) from Castro/Divisadero east
# through Church to Market Street.  Frame A's 8-degree tilt misplaced it, so
# it gets its own line, fitted to Duboce & Market.
street("duboce", "Duboce Avenue",
       line_through((-122.429000, 37.769500), (-122.437000, 37.769750)))
street("thirteenth", "13th Street",
       line_through((-122.421500, 37.769200), (-122.406000, 37.771300)))


# ==========================================================================
# 3.  EASTERN GOLDEN GATE PARK + THE PANHANDLE
# ==========================================================================
chain("lincoln", "secondary", ["fifth_ave", "kezar", "stanyan"], bicycle="yes")
chain("mlk", "tertiary", ["transverse", "stow_lake", "kezar"], bicycle="yes")
chain("middle_e", "tertiary", ["transverse", "stow_lake", "kezar"], bicycle="yes")
chain("jfk", "tertiary", ["transverse", "stow_lake", "kezar", "stanyan"], bicycle="designated")
chain("ggp_mup", "path", ["transverse", "stow_lake", "kezar", "stanyan"],
      bicycle="designated", surface="asphalt")
chain("transverse", "tertiary", ["mlk", "middle_e", "jfk", "ggp_mup"])
chain("stow_lake", "tertiary", ["mlk", "middle_e", "jfk", "ggp_mup"],
      bicycle="yes")
chain("kezar", "tertiary", ["lincoln", "mlk", "middle_e", "jfk", "ggp_mup"],
      bicycle="yes")
chain("panhandle", "path", ["stanyan", "masonic", "central", "lyon", "baker"],
      name="Panhandle Path", bicycle="designated", surface="asphalt")

# ==========================================================================
# 4.  DUBOCE TRIANGLE / LOWER HAIGHT / HAIGHT-ASHBURY / WESTERN ADDITION
# ==========================================================================
_HAIGHT_X = ["stanyan", "shrader", "cole", "clayton", "ashbury", "masonic",
             "central", "lyon", "baker", "broderick", "divisadero", "scott",
             "pierce", "steiner", "fillmore", "webster", "buchanan", "laguna",
             "octavia", "gough"]
_OAK_X = ["stanyan", "shrader", "cole", "clayton", "ashbury", "masonic",
          "central", "lyon", "baker", "broderick", "divisadero", "scott",
          "pierce", "steiner", "fillmore", "webster", "buchanan", "laguna",
          "octavia", "gough", "franklin", "vanness", "market"]
_FELL_X = ["stanyan", "masonic", "central", "lyon", "baker", "broderick",
           "divisadero", "scott", "pierce", "steiner", "fillmore", "webster",
           "buchanan", "laguna", "octavia", "gough", "franklin", "vanness",
           "polk", "market"]
_FULTON_X = ["arguello", "stanyan", "masonic", "lyon", "baker", "broderick",
             "divisadero", "scott", "pierce", "steiner", "fillmore", "webster",
             "buchanan", "laguna", "octavia", "gough", "franklin", "vanness"]

chain("frederick", "residential",
      ["stanyan", "shrader", "cole", "clayton", "ashbury", "masonic"])
chain("carl", "residential", ["shrader", "cole", "clayton"])
chain("duboce", "residential",
      ["divisadero", "castro", "noe", "steiner", "sanchez", "fillmore",
       "church", "market"],
      bicycle="designated")
chain("waller", "residential",
      ["stanyan", "shrader", "cole", "clayton", "ashbury", "masonic", "central",
       "lyon", "baker", "broderick", "divisadero", "scott", "pierce", "steiner"],
      bicycle="yes")
chain("haight", "secondary", _HAIGHT_X, bicycle="yes")
chain("page", "residential", _HAIGHT_X, bicycle="designated")
chain("oak", "primary", _OAK_X, oneway="yes", bicycle="yes")
chain("fell", "primary", _FELL_X, oneway="yes", bicycle="yes")
chain("fulton", "secondary", _FULTON_X, bicycle="yes")

# --- north-south streets of the dense area --------------------------------
chain("stanyan", "secondary",
      ["lincoln", "frederick", "waller", "haight", "page", "oak", "fell",
       "jfk", "ggp_mup", "fulton"])
for _s in ("shrader", "cole", "clayton"):
    chain(_s, "residential", ["carl", "frederick", "waller", "haight", "page", "oak"])
chain("ashbury", "residential", ["frederick", "waller", "haight", "page", "oak"])
chain("masonic", "secondary",
      ["frederick", "waller", "haight", "page", "oak", "panhandle", "fell",
       "fulton", "turk", "geary", "california"])
chain("central", "residential",
      ["waller", "haight", "page", "oak", "panhandle", "fell"])
chain("lyon", "residential",
      ["waller", "haight", "page", "oak", "panhandle", "fell", "fulton"])
chain("baker", "residential",
      ["waller", "haight", "page", "oak", "panhandle", "fell", "fulton"])
for _s in ("scott", "pierce"):
    chain(_s, "residential", ["waller", "haight", "page", "oak", "fell", "fulton"])
chain("broderick", "residential",
      ["waller", "haight", "page", "oak", "fell", "fulton"])
chain("steiner", "residential",
      ["duboce", "waller", "haight", "page", "oak", "fell", "fulton"])
chain("divisadero", "secondary",
      ["duboce", "waller", "haight", "page", "oak", "fell", "fulton", "turk",
       "geary", "california", "broadway", "vallejo", "green"])
chain("fillmore", "secondary",
      ["duboce", "haight", "page", "oak", "fell", "fulton", "turk", "geary",
       "california", "broadway", "vallejo", "green", "filbert", "lombard"])
chain("webster", "residential",
      ["haight", "page", "oak", "fell", "fulton", "turk", "geary", "california",
       "broadway", "green"])
for _s in ("buchanan", "laguna", "octavia"):
    chain(_s, "residential", ["market", "haight", "page", "oak", "fell", "fulton"])
chain("gough", "residential",
      ["market", "haight", "page", "oak", "fell", "fulton", "turk", "geary",
       "california", "broadway", "green"])
chain("franklin", "secondary", ["market", "oak", "fell", "fulton"], oneway="yes")
chain("arguello", "secondary", ["fulton", "geary", "california"])
chain("presidio", "secondary", ["geary", "california"])
# Turk Street runs the full width of the Western Addition, halving the
# Fulton-to-Geary gap on every north-south arterial there.
chain("turk", "secondary",
      ["masonic", "lyon", "baker", "broderick", "divisadero",
       "fillmore", "webster", "gough", "vanness", "polk"])

# ==========================================================================
# 5.  BUENA VISTA PARK AND THE HAIGHT RIDGE
# ==========================================================================
bv_south = free("bv_south", -122.440300, 37.766200)
polychain("Buena Vista Avenue East", "residential", [
    node("haight", "baker"),
    free("bve1", -122.440300, 37.769200),
    free("bve2", -122.439800, 37.768200),
    free("bve3", -122.439600, 37.767200),
    bv_south,
])
polychain("Buena Vista Avenue West", "residential", [
    node("haight", "central"),
    free("bvw1", -122.443200, 37.769000),
    free("bvw2", -122.442600, 37.767800),
    free("bvw3", -122.441600, 37.766800),
    bv_south,
])
polychain("Buena Vista Avenue", "residential", [
    bv_south,
    free("bva1", -122.439000, 37.766400),
    free("bva2", -122.437500, 37.767800),
    node("duboce", "castro"),
])

# --- Corona Heights: Roosevelt Way, Levant Street, Ord Street, Vulcan ------
_17_castro = node("17th", "castro")
polychain("Roosevelt Way", "residential", [
    _17_castro,
    free("roos1", -122.438800, 37.763000),
    free("roos2", -122.440800, 37.763200),
    free("roos3", -122.442500, 37.763800),
])
way("Levant Street", "residential",
    [free("roos3", -122.442500, 37.763800), free("levant1", -122.443200, 37.762200)])
polychain("Ord Street", "residential", [
    _17_castro,
    free("ord1", -122.440500, 37.762300),
    free("ord2", -122.441700, 37.762000),
])
way("Vulcan Stairway", "steps",
    [free("ord2", -122.441700, 37.762000), free("levant1", -122.443200, 37.762200)],
    surface="concrete", bicycle="no")


# ==========================================================================
# 6.  MARKET STREET
# ==========================================================================
# Private motor vehicles are banned from Market between about 10th Street and
# the Embarcadero; bicycles are explicitly permitted.  Encoded here as
# access=no + bicycle=yes on that stretch.
_MKT_WEST = ["castro", "noe", "sanchez", "church", "dolores", "guerrero",
             "duboce", "buchanan", "laguna", "valencia", "octavia", "oak", "gough",
             "franklin", "mission", "vanness", "fell"]
_MKT_EAST = ["fell", "s11th", "s10th", "polk", "s9th", "s8th", "s7th", "s6th",
             "s5th", "powell", "s4th", "s3rd", "s2nd", "kearny", "montgomery",
             "s1st", "sansome", "embarcadero"]
chain("market", "primary", _MKT_WEST, bicycle="yes")
chain("market", "primary", _MKT_EAST, bicycle="yes", access="no")

# --- Upper Market Street: Castro up over the ridge to Portola Drive -------
mkt_castro = node("market", "castro")
mkt_clayton = free("mkt_clayton", -122.442900, 37.759000)
mkt_corbett = free("mkt_corbett", -122.441500, 37.759700)
mkt_portola = free("mkt_portola", -122.438700, 37.748400)
polychain("Market Street", "secondary", [
    mkt_castro,
    free("mkt_u1", -122.436600, 37.762000),
    free("mkt_u2", -122.439000, 37.761000),
    mkt_corbett,
    mkt_clayton,
    free("mkt_u3", -122.444500, 37.756600),
    free("mkt_u4", -122.442500, 37.752000),
    mkt_portola,
], bicycle="yes")

# ==========================================================================
# 7.  NORTHERN SPINE: Pacific Heights, Nob Hill, Russian Hill, North Beach
# ==========================================================================
_ALLNS = ["vanness", "polk", "larkin", "hyde", "leavenworth", "jones",
          "taylor", "mason", "powell", "stockton", "grant", "kearny",
          "montgomery", "sansome"]
chain("geary", "primary",
      ["arguello", "presidio", "masonic", "lyon", "baker", "broderick",
       "divisadero", "fillmore", "webster", "gough", "vanness", "polk",
       "larkin", "hyde", "jones", "taylor", "powell", "stockton", "grant",
       "kearny"], bicycle="yes")
chain("california", "secondary",
      ["arguello", "presidio", "masonic", "lyon", "baker", "broderick",
       "divisadero", "fillmore", "webster", "gough"] + _ALLNS, bicycle="yes")
chain("broadway", "secondary",
      ["lyon", "baker", "divisadero", "fillmore", "webster", "gough"] + _ALLNS)
chain("vallejo", "residential", ["baker", "divisadero", "fillmore"])
chain("green", "residential",
      ["lyon", "baker", "divisadero", "fillmore", "webster", "gough",
       "vanness", "polk", "hyde", "powell"])
chain("lombard", "residential",
      ["vanness", "polk", "larkin", "hyde", "leavenworth", "jones", "taylor",
       "mason", "powell"])

# Filbert Street.  The Hyde-to-Leavenworth block is the ~31.5% wall; it is
# tagged as the ordinary residential street it is.
chain("filbert", "residential",
      ["vanness", "polk", "larkin", "hyde", "leavenworth", "jones", "taylor",
       "mason", "powell", "stockton", "grant", "kearny", "montgomery"])

chain("vanness", "primary",
      ["market", "oak", "fell", "fulton", "turk", "geary", "california",
       "broadway", "green", "filbert", "lombard"])
chain("polk", "residential",
      ["fell", "fulton", "turk", "geary", "california", "broadway", "green",
       "filbert", "lombard"], bicycle="yes")
chain("larkin", "residential",
      ["geary", "california", "broadway", "filbert", "lombard"])
chain("hyde", "residential",
      ["geary", "california", "broadway", "green", "filbert",
       "lombard"])
chain("leavenworth", "residential",
      ["california", "broadway", "filbert", "lombard"])
for _s in ("jones", "taylor"):
    chain(_s, "residential",
          ["geary", "california", "broadway", "filbert", "lombard"])
chain("mason", "residential",
      ["california", "broadway", "filbert", "lombard"])
chain("powell", "residential",
      ["market", "geary", "california", "broadway", "green",
       "filbert", "lombard"])
for _s in ("stockton", "grant"):
    chain(_s, "residential",
          ["geary", "california", "broadway", "filbert"])
chain("kearny", "residential",
      ["market", "geary", "california", "broadway", "filbert"])
for _s in ("montgomery", "sansome"):
    chain(_s, "residential", ["market", "california", "broadway", "filbert"])

# --- Lombard's crooked block (Hyde to Leavenworth), sett-paved and one-way -
FEATS[:] = [f for f in FEATS
            if not (f["properties"]["name"] == "Lombard Street"
                    and {tuple(c) for c in f["geometry"]["coordinates"]}
                    == {node("lombard", "hyde"), node("lombard", "leavenworth")})]
way("Lombard Street", "residential",
    [node("lombard", "hyde"), node("lombard", "leavenworth")],
    oneway="yes", surface="sett")

# --- northern extensions of Baker and Lyon, and their public stairways ----
# Lyon and Baker used to be single 1380 m edges between Fulton and
# California, which a router cannot turn off.  Split at the real Pacific
# Heights cross streets; each of those streets is carried Lyon-Baker-Broderick.
_PAC_HTS = ["mcallister", "golden_gate", "eddy", "ellis", "ofarrell",
       "geary", "post", "sutter", "bush", "pine"]
for _s in _PAC_HTS:
    chain(_s, "residential", ["lyon", "baker", "broderick"])
chain("baker", "residential",
      ["fulton"] + _PAC_HTS + ["california", "broadway"])
chain("broderick", "residential", ["fulton"] + _PAC_HTS + ["california"])
way("Baker Street Steps", "steps",
    [node("baker", "broadway"), node("baker", "vallejo")],
    surface="concrete", bicycle="no")
chain("lyon", "residential",
      ["fulton"] + _PAC_HTS + ["california", "broadway"])
way("Lyon Street Steps", "steps",
    [node("lyon", "broadway"), node("lyon", "green")],
    surface="concrete", bicycle="no")

# --- Telegraph Hill: the Filbert Steps and the road up to Coit Tower ------
way("Filbert Steps", "steps",
    [node("filbert", "sansome"), node("filbert", "montgomery")],
    surface="concrete", bicycle="no")
polychain("Telegraph Hill Boulevard", "tertiary", [
    node("filbert", "kearny"),
    free("coit1", -122.406600, 37.803300),
    free("coit2", -122.405800, 37.802400),
])
way("Greenwich Steps", "steps",
    [free("coit2", -122.405800, 37.802400), free("green_st1", -122.402000, 37.803600)],
    surface="concrete", bicycle="no")
way("Greenwich Street", "residential",
    [free("green_st1", -122.402000, 37.803600), node("filbert", "sansome")])

# ==========================================================================
# 8.  FREEWAY RAMPS  (bicycles and general access prohibited)
# ==========================================================================
polychain("Central Freeway On-Ramp", "motorway_link", [
    node("market", "octavia"),
    free("cfr1", -122.423600, 37.770000),
    free("cfr2", -122.422800, 37.768600),
], bicycle="no", access="no", oneway="yes")


# ==========================================================================
# 9.  THE MISSION AND THE MISSION-TO-DOGPATCH CORRIDOR
# ==========================================================================
_MW = ["castro", "noe", "sanchez", "church", "dolores", "guerrero", "valencia",
       "mission", "svn", "folsom", "harrison", "bryant", "potrero"]
_HILL = ["vermont", "rhode_island", "de_haro", "wisconsin", "connecticut",
         "missouri", "mississippi", "pennsylvania", "indiana", "minnesota",
         "third"]

chain("14th", "residential",
      ["castro", "noe", "sanchez", "market", "church", "dolores", "guerrero",
       "valencia", "mission", "svn", "folsom", "harrison", "bryant", "potrero"])
chain("16th", "secondary", _MW + ["capp"] + _HILL, bicycle="yes")
chain("17th", "residential", _MW)
chain("18th", "residential", _MW + _HILL)
chain("19th", "residential", _MW)
chain("20th", "residential", _MW)
chain("21st", "residential", _MW)
chain("22nd", "residential",
      _MW + ["vermont", "de_haro", "wisconsin", "connecticut", "missouri",
             "mississippi", "indiana", "third", "rhode_island",
             "pennsylvania", "minnesota"])
chain("23rd", "residential", _MW)
chain("24th", "secondary", _MW + _HILL, bicycle="yes")

_EW_ALL = ["14th", "16th", "17th", "18th", "19th", "20th", "21st",
           "22nd", "23rd", "24th"]
chain("castro", "residential", ["duboce", "market"] + _EW_ALL + ["clipper"])
chain("noe", "residential", ["duboce", "market"] + _EW_ALL + ["clipper"])
chain("sanchez", "residential", ["duboce", "market"] + _EW_ALL,
      bicycle="designated")
chain("church", "secondary", ["duboce", "market"] + _EW_ALL + ["clipper"])
chain("dolores", "secondary", ["market"] + _EW_ALL + ["clipper"])
chain("guerrero", "secondary", ["market"] + _EW_ALL)
chain("valencia", "secondary", ["market"] + _EW_ALL, bicycle="designated")
chain("mission", "primary", ["market", "thirteenth"] + _EW_ALL, bicycle="yes")
for _s in ("svn", "folsom", "harrison", "bryant"):
    chain(_s, "secondary", ["thirteenth"] + _EW_ALL)
chain("potrero", "primary", ["thirteenth"] + _EW_ALL)
chain("vermont", "residential", ["16th", "18th", "22nd", "24th"])
chain("rhode_island", "residential", ["16th", "18th", "22nd", "24th"])
chain("de_haro", "residential", ["16th", "18th", "22nd", "24th"])
chain("wisconsin", "residential", ["16th", "18th", "22nd", "24th"])
chain("connecticut", "residential", ["16th", "18th", "22nd", "24th"])
chain("missouri", "residential", ["16th", "18th", "22nd", "24th"])
chain("mississippi", "residential", ["16th", "18th", "22nd", "24th"])
chain("pennsylvania", "residential", ["16th", "18th", "22nd", "24th"])
chain("indiana", "residential", ["16th", "18th", "22nd", "24th"])
chain("minnesota", "residential", ["16th", "18th", "22nd", "24th"])
chain("third", "secondary", ["16th", "18th", "22nd", "24th"], bicycle="yes")

# ==========================================================================
# 10.  SOMA
# ==========================================================================
_SNS = ["s11th", "s10th", "s9th", "s8th", "s7th", "s6th", "s5th", "s4th",
        "s3rd", "s2nd", "s1st"]
chain("mission_soma", "primary", _SNS, bicycle="yes")
chain("howard", "secondary", _SNS + ["embarcadero"], oneway="yes", bicycle="yes")
chain("folsom_soma", "secondary", _SNS + ["embarcadero"], bicycle="designated")
chain("harrison_soma", "secondary", _SNS + ["embarcadero"], oneway="yes")
chain("bryant_soma", "secondary", _SNS + ["embarcadero"], oneway="yes")
chain("brannan", "secondary",
      ["s9th", "s8th", "s7th", "s6th", "s5th", "s4th", "s3rd", "s2nd", "s1st",
       "embarcadero"])
chain("townsend", "secondary",
      ["s8th", "s7th", "s6th", "s5th", "s4th", "s3rd", "s2nd", "embarcadero"],
      bicycle="yes")
_SOMA_EW = ["market", "mission_soma", "howard", "folsom_soma", "harrison_soma",
            "bryant_soma"]
for _s in ("s11th", "s10th"):
    chain(_s, "secondary", _SOMA_EW + ["thirteenth"])
for _s in ("s9th", "s8th", "s7th", "s6th", "s5th", "s4th", "s3rd", "s2nd"):
    chain(_s, "secondary", _SOMA_EW + ["brannan", "townsend"])
chain("s1st", "secondary", _SOMA_EW + ["brannan"])
chain("embarcadero", "primary",
      ["market", "howard", "folsom_soma", "harrison_soma", "bryant_soma",
       "brannan", "townsend"], bicycle="designated")
chain("thirteenth", "secondary",
      ["mission", "svn", "folsom", "harrison", "bryant", "potrero",
       "s11th", "s10th"])
way("Otis Street", "secondary",
    [node("market", "vanness"), node("mission_soma", "s11th")], oneway="yes")
polychain("3rd Street", "secondary", [
    node("s3rd", "townsend"),
    free("mb1", -122.392300, 37.773300),
    free("mb2", -122.390800, 37.770000),
    node("third", "16th"),
], bicycle="yes")
polychain("Interstate 80 On-Ramp", "motorway_link", [
    node("bryant_soma", "s5th"),
    free("i80a", -122.400600, 37.778200),
    free("i80b", -122.398800, 37.777400),
], bicycle="no", access="no", oneway="yes")

# ==========================================================================
# 11.  TWIN PEAKS APPROACH
# ==========================================================================
tpb_south = free("tpb_south", -122.447000, 37.747000)
tpb_cross = free("tpb_cross", -122.446800, 37.754100)
tpb_north = free("tpb_north", -122.451400, 37.757400)
burnett_jct = free("burnett_jct", -122.444300, 37.747500)
burnett_north = free("burnett_north", -122.440500, 37.750800)
crown = free("crown", -122.444200, 37.759700)
cl2 = free("cl2", -122.445500, 37.760500)
gv1 = free("gv1", -122.440100, 37.749000)

polychain("Portola Drive", "primary", [
    mkt_portola,
    free("por1", -122.442000, 37.747600),
    burnett_jct,
    tpb_south,
    free("por2", -122.452000, 37.745600),
    free("por3", -122.457000, 37.744000),
], bicycle="yes")
polychain("Twin Peaks Boulevard", "tertiary", [
    tpb_south,
    free("tpb1", -122.447200, 37.749000),
    free("tpb2", -122.446600, 37.750800),
    free("tpb3", -122.447000, 37.752600),
    tpb_cross,
    free("tpb4", -122.448400, 37.755000),
    free("tpb5", -122.449500, 37.756400),
    tpb_north,
], bicycle="yes")
polychain("Twin Peaks Boulevard", "tertiary", [
    tpb_cross,
    free("tpe1", -122.446000, 37.755000),
    free("tpe2", -122.445600, 37.756200),
    free("tpe3", -122.446200, 37.757400),
    tpb_north,
], bicycle="yes")
way("Christmas Tree Point Road", "service",
    [tpb_cross, free("ctp", -122.445500, 37.754500)], bicycle="yes")
polychain("Clarendon Avenue", "secondary", [
    tpb_north,
    free("cla1", -122.449000, 37.758400),
    free("cla2", -122.446000, 37.758800),
    mkt_clayton,
])
polychain("Clayton Street", "residential", [
    node("clayton", "carl"),
    free("cls1", -122.447000, 37.763000),
    cl2,
    mkt_clayton,
])
way("Pemberton Place", "steps", [cl2, crown], surface="concrete", bicycle="no")
way("Crown Terrace", "residential", [crown, mkt_corbett])
polychain("Corbett Avenue", "secondary", [
    mkt_corbett,
    free("cor1", -122.442600, 37.757000),
    free("cor2", -122.442800, 37.754000),
    free("cor3", -122.441500, 37.751800),
    burnett_north,
], bicycle="yes")
polychain("Burnett Avenue", "residential", [
    burnett_jct,
    free("bur1", -122.443000, 37.748800),
    free("bur2", -122.441500, 37.750000),
    burnett_north,
])
way("Grand View Avenue", "residential", [burnett_north, gv1])
way("Clipper Street", "residential", [gv1, node("clipper", "castro")])
chain("clipper", "residential", ["castro", "noe", "church", "dolores"])

# ==========================================================================
# 12.  OUTPUT
# ==========================================================================
if __name__ == "__main__":
    fc = {"type": "FeatureCollection", "features": FEATS}
    with open("sf_graph.draft.geojson", "w") as fh:
        json.dump(fc, fh, indent=1)
        fh.write("\n")
    print(f"features: {len(FEATS)}")

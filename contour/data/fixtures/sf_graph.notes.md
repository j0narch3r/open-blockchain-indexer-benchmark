# sf_graph.geojson — sourcing notes

**Files**

- `data/fixtures/sf_graph.geojson` — the network (1113 features, 335 KB)
- `scripts/make_fixture_graph_gen.py` — generator (writes the geojson in place)
- `scripts/validate_fixture_graph.py` — validator

## How the coordinates were derived

I did not hand-type intersections. Every intersection node is the **algebraic
intersection of two street centre-lines**, so any two ways meeting at a corner
share a byte-identical coordinate pair by construction — there is no
opportunity for a 0.5 m mismatch to silently split the graph.

Streets are declared once as an infinite line (point + direction) in
`(lon, lat)` degree space. Most lines come from one of three **rotated local
metric frames**, each fitted to intersections I am confident about:

| Frame | Area | Origin | Bearing of the "east-west" axis |
|---|---|---|---|
| A | Western Addition, Haight, Duboce Triangle, Panhandle, eastern GGP, Nob/Russian/Telegraph Hill | Haight & Ashbury `(-122.446800, 37.769900)` | 082° (8° off cardinal) |
| M | Mission, Castro, Noe Valley, Potrero Hill, Dogpatch | 16th & Mission `(-122.419300, 37.765100)` | 086.4° |
| S | SoMa, parallel/perpendicular to Market Street | Market & Castro | 045.94° |

The 8° rotation of Frame A is not an artefact — it is fitted to Haight &
Ashbury vs Haight & Fillmore, and independently reproduces Broadway's real
northward drift going east. The SF grid north of Market is genuinely rotated.

Streets inside a frame are placed by an offset table in **metres**
(`AX`/`AY`, `MX`/`MY`, `SX`/`SY`), so block lengths are explicit and
consistent rather than guessed per corner. Curved and switchbacking ways
(upper Market, Twin Peaks Boulevard, Corbett, Buena Vista Avenue East/West,
Portola, the Coit Tower approach) are explicit polylines through hand-placed
shape nodes and are split at each junction.

Nothing in the network was shaped toward any routing outcome. I was not told
what any test expects and did not attempt to infer it. Where I had to choose
between two plausible geometries I chose the one that best matched a landmark
I could recall, not the one that made any route better or worse.

## Accuracy: measured offsets from real coordinates

Spot checks against intersections I am confident of:

| Intersection | Offset |
|---|---|
| Haight & Ashbury | 0 m (frame origin) |
| 16th & Mission | 0 m (frame origin) |
| 24th & Mission | 10 m |
| Market & Castro | 35 m |
| Fell & Baker (Panhandle east end) | 39 m |
| Duboce & Church | 94 m |
| Market & 5th | 161 m |
| California & Powell | 158 m |
| Filbert & Hyde | 194 m |

The pattern is what you would expect from three frames: **very good in the
core (Haight, Duboce Triangle, Panhandle, Mission — within ~50 m), degrading
to ~150–200 m at the far north-east corner** (Nob Hill, Russian Hill,
Telegraph Hill), which is ~4.5 km from the Frame A origin. Relative geometry —
block lengths, street spacing, ordering of cross streets, orientation — stays
correct throughout; the error is a slowly accumulating whole-neighbourhood
shift, not per-corner noise.

The Filbert Street block between Hyde and Leavenworth comes out **142 m long**
(real ≈ 130 m). It is tagged `highway=residential` with no gradient hint of
any kind; its steepness has to come from terrain.

## Confidence by area

**Solid (within ~50 m, geometry and connectivity both trustworthy)**

- Duboce Triangle, Lower Haight, Haight-Ashbury, Cole Valley — full
  block-by-block coverage, Stanyan to Van Ness, Duboce to Fulton.
- The Panhandle: Fell and Oak as the one-way pair, the separated
  `Panhandle Path` (`highway=path`, `bicycle=designated`) between them,
  crossing only at Masonic, Central, Lyon and Baker — Ashbury, Clayton, Cole
  and Shrader really are cut by the Panhandle and are terminated at Oak.
- The Wiggle streets (Duboce, Steiner, Waller, Pierce, Haight, Scott, Page)
  and the near-collinearity of Sanchez/Fillmore and Noe/Steiner at Duboce,
  which is a real feature of that junction.
- Mission district grid, Valencia/Mission/Guerrero/Dolores, 14th–24th.
- Market Street's alignment: modelled as a straight line at bearing 046° from
  Castro to the Embarcadero, which reproduces Church, Van Ness, 6th and Powell
  to within ~100 m. Market bends only slightly in reality.

**Approximated but structurally right**

- SoMa. The grid is regular and parallel to Market, but I fitted the numbered
  street spacing between two anchors (1st and 8th at Market), giving ~236 m
  blocks. Individual corners drift up to ~200 m; the grid shape is right.
- Eastern Golden Gate Park. JFK Drive, the multi-use path, MLK Drive, Middle
  Drive East and Transverse Drive are placed at plausible offsets inside the
  park rather than traced. **Kezar Drive is modelled as a straight
  north-south line**; in reality it is an arc around Kezar Stadium. Lincoln
  Way and Fulton Street west of the park's east end terminate at leaf nodes
  (5th Avenue / Arguello) — that is an extract boundary, not a real dead end.
- Potrero Hill and Dogpatch. Street ordering and ~105 m block spacing are
  right; absolute positions are extrapolated from the Frame M fit.
- Buena Vista Park loop. Buena Vista Avenue East and West form a real loop
  around the park with Buena Vista Avenue running east to Duboce/Castro, but
  the shape nodes are hand-placed, not traced.

**Low confidence — flag these if a later task depends on them**

1. **Nob Hill / Russian Hill / North Beach / Telegraph Hill.** ~150–200 m
   absolute offset. In particular Filbert & Hyde, Lombard's crooked block and
   Filbert & Sansome are all shifted north-west of their true positions.
   Block lengths and the ordering of Hyde/Leavenworth/Jones/Taylor/Mason/
   Powell/Stockton/Grant/Kearny/Montgomery/Sansome are correct.
2. **Twin Peaks Boulevard.** The switchback loop around the two peaks is
   qualitatively right (a west/south leg and an east leg meeting at Christmas
   Tree Point and again at the Clarendon end) but the individual bends are
   invented shape points, not traced. Portola Drive, Corbett Avenue, Burnett
   Avenue and Grand View Avenue are the same: correct topology, sketched
   geometry.
3. **Corona Heights** (Roosevelt Way, Levant Street, Ord Street, and the
   **Vulcan Stairway** between them). Real streets in a real relationship, but
   every coordinate there is hand-placed.
4. **Upper Market Street** between Castro and Portola. Real route, sketched
   bends; Clarendon Avenue's junction with Clayton/Market is simplified into a
   single node (`mkt_clayton`).
5. **13th Street / Otis Street** as the SoMa-to-Mission stitch under the
   Central Freeway. These exist and connect where I say they do, but their
   alignment is fitted to make the two frames meet, not traced.
6. **Greenwich Steps** and the short Greenwich Street stub on Telegraph Hill
   are the roughest ways in the file.
7. **Duboce Avenue east of Church** (to Guerrero/Valencia/Mission) is
   **omitted**. In Frame A that stretch drifts north across Market, which is
   wrong, so I terminated Duboce at Church. The Mission is reached via Church
   → Market → 14th Street instead. This is the one deliberate omission of a
   real, useful cycling link.
8. **Pacific Heights north-south runs.** Lyon Street and Baker Street between
   Fulton and California are single 1380 m features because I did not include
   the intervening east-west streets (Turk, Golden Gate, Eddy, Ellis,
   O'Farrell, Post, Sutter, Bush). The geometry is a straight line, which is
   correct; there are simply no turn opportunities modelled along it.
9. **19th, 21st, 22nd, 23rd, 25th, 26th Streets are omitted** from the Mission
   to stay inside the feature budget. 18th → 20th → 24th are present, so the
   longest turn-free stretch on a Mission north-south street is 18th to 20th
   (356 m) and 20th to 24th (712 m).

## Tagging notes

- **Market Street** carries `access=no` + `bicycle=yes` from Fell/Van Ness
  east to the Embarcadero — that is the car-free stretch, and the allowed tag
  vocabulary has no `motor_vehicle` key, so `access=no` with an explicit
  bicycle exception is the closest faithful encoding. West of there Market is
  a plain `primary` with `bicycle=yes`.
- Two `highway=motorway_link` ramps (Central Freeway at Octavia, I-80 at
  Bryant & 5th), each `bicycle=no` + `access=no` + `oneway=yes`, split into
  two features each — 4 motorway_link features total. Both attach to the
  network at their street end and dangle at the freeway end, as a ramp should.
- Six `highway=steps` ways at real SF stairways: **Filbert Steps** (Sansome to
  Montgomery, Telegraph Hill), **Lyon Street Steps** (Broadway to Green),
  **Baker Street Steps** (Broadway to Vallejo), **Vulcan Stairway** (Ord to
  Levant), **Pemberton Place** (Clayton to Crown Terrace), **Greenwich Steps**
  (Coit Tower to Greenwich Street). All are attached to the street network at
  both ends — they are in the data so that later software can decide whether
  to exclude them.
- **Lombard Street** between Hyde and Leavenworth is `oneway=yes` +
  `surface=sett` (the crooked block).
- Fell and Oak are `oneway=yes` in opposite directions; Howard, Harrison and
  Bryant in SoMa are `oneway=yes`; Franklin and Otis are `oneway=yes`.
- `bicycle=designated` is used only where SF actually has designated
  infrastructure: the Panhandle Path, the Golden Gate Park multi-use path,
  JFK Drive, the Embarcadero, Valencia, Sanchez (the Sanchez slow street),
  Page, Duboce, and Folsom in SoMa.

## Revision 2 — what changed

Three defects from revision 1 were fixed.

### 1. Duboce Avenue east of Church restored

Duboce Avenue no longer sits on Frame A. It is its own line, essentially level
(gaining ~33 m of latitude over 847 m going west, as it really does), fitted
so that **Duboce & Church lands 29 m from the stated Duboce/Market/Church
junction**. Duboce now runs Divisadero → Castro → Noe → Steiner → Sanchez →
Fillmore → **Church → Market**, terminating where its line actually meets
Market Street. Church Street was also shifted 50 m east, which improved
Church's whole length (16th & Church went from 60 m to 54 m of error).

*Still omitted:* Duboce Avenue **east of Market** (toward Valencia, Mission and
Otis). Market crosses Duboce in this geometry about 265 m north-east of
Church, and east of that crossing Guerrero, Valencia and Mission have all
already terminated at Market, so there is nothing for it to connect to.

### 2. A dedicated frame for the north-east

Frame A held the Haight beautifully and then accumulated ~200 m of error
4.5 km away at Telegraph Hill. **Frame N** now covers Nob Hill, Russian Hill,
North Beach, Telegraph Hill and Pacific Heights: same 082°/352° grid bearing,
origin at Lombard & Hyde, with 123 m north-south street spacing and 92–115 m
east-west street spacing (the classic SF 275 ft × 412 ft block). Fulton Street
and everything north of it moved to Frame N; Fell Street and everything south
stayed on Frame A. Van Ness and Polk stayed on Frame A, where they are
accurate at both ends.

Result: Lombard & Hyde, Coit Tower and California & Powell are all exact
(0 m). Geary & Masonic and California & Masonic, checked independently, land
within 20–45 m.

### 3. Long featureless edges split

Maximum edge length went from **1380 m to 791 m**; only 17 edges now exceed
700 m, against a median of 178 m.

- Lyon and Baker are split at their real Pacific Heights cross streets:
  Fulton, McAllister, Golden Gate, Turk, Eddy, Ellis, O'Farrell, Geary, Post,
  Sutter, Bush, Pine, California. Each of those cross streets is carried
  Lyon–Baker–Broderick.
- **Turk Street** was carried the full width of the Western Addition (Masonic
  to Polk), halving the Fulton-to-Geary gap on eight north-south arterials.
- **19th, 21st, 22nd and 23rd Streets** added to the Mission (22nd also over
  Potrero Hill to 3rd Street), closing the 712 m turn-free runs between 18th
  and 24th.
- **Presidio Avenue** splits the Arguello–Masonic run on Geary and California.
- **Webster** and **Gough** carried north to Green Street, splitting the 986 m
  Fillmore-to-Van Ness runs on Geary, California, Broadway and Green.
- **Stow Lake Drive** splits the 980 m runs on JFK Drive, the multi-use path,
  MLK Drive and Middle Drive East.

**Feature count rose from 893 to 1114**, above the original "roughly 400–900"
guidance. That is a direct consequence of the splitting: more turn
opportunities means more edges. The file is 330 KB and still comfortable.

The overrun was reviewed and accepted: the 400–900 figure was a rough sizing
hint, not a budget, and every added feature came from splitting a long edge at
a real intersection. **Do not trim the feature count back** — doing so would
trade away the connectivity this revision was commissioned to add.

## Anchor verification — three supplied anchors that were wrong

> **Read this before "correcting" any coordinate in the north-east or on
> Duboce Avenue.** Three anchor coordinates supplied during review turned out
> to be inconsistent with real San Francisco geometry. They were rejected on
> the evidence below, and the reviewer subsequently **confirmed all three as
> their own errors and directed that this map's values be kept**. The wrong
> numbers came from coordinates lifted from elsewhere in the project spec and
> passed along without checking them against the street layout; they are being
> corrected at that source. If you find yourself about to move Filbert Street,
> the Hyde–Leavenworth block, or Duboce Avenue to match those figures, don't.

| anchor | produced | target given | error |
|---|---|---|---|
| Lombard & Hyde | `-122.418900, 37.802000` | `-122.41890, 37.80200` | **0 m** |
| Coit Tower | `-122.405800, 37.802400` | `-122.40580, 37.80240` | **0 m** |
| California & Powell | `-122.410005, 37.791701` | `-122.41000, 37.79170` | **0 m** |
| Duboce & Church (nearest node to the stated Duboce/Market point) | `-122.429328, 37.769510` | `-122.42900, 37.76950` | **29 m** |
| Duboce & Market (my Duboce∩Market node) | `-122.426441, 37.769420` | `-122.42900, 37.76950` | 225 m |
| Duboce & Sanchez | `-122.431497, 37.769578` | `-122.43080, 37.77120` | 190 m |
| Filbert & Hyde | `-122.418609, 37.800358` | `-122.41800, 37.80220` | 211 m |
| Filbert & Leavenworth | `-122.417225, 37.800513` | `-122.41400, 37.80200` | 328 m |

Three of the eight targets are hit exactly and a fourth to 29 m. The remaining
three I deliberately did **not** fit, because they contradict other targets in
the same list and contradict well-established facts about the city. Fitting
them would have required building geometry I know to be wrong.

**The two Filbert targets contradict the Lombard & Hyde target.**

- They place Filbert & Hyde at latitude `37.80220`, *north* of Lombard & Hyde
  at `37.80200`. Lombard is two blocks north of Filbert (Greenwich between).
  Both cannot be true.
- They place Filbert & Hyde at longitude `-122.41800` and Lombard & Hyde at
  `-122.41890`. Hyde Street is straight; it cannot jog 79 m east over the
  250 m between them.
- They put Filbert & Hyde and Filbert & Leavenworth **352 m apart**. That block
  is one of the steepest streets in the country at ~31.5%; at 352 m that
  implies 111 m of rise, more than the total height of Russian Hill (~90 m).
  The documented block is ~120–130 m with ~38–41 m of rise.

My model puts that block at **123 m** — identical to the Lombard crooked block
between the same two cross streets, which is documented at about 120 m — and
places Filbert 184 m (two blocks) south of Lombard at Hyde. The steep block
therefore lands with a length that can actually carry a 31.5% grade.

**The Duboce & Sanchez target is inconsistent with a level Duboce.** It sits at
latitude `37.77120`, 190 m *north* of the stated Duboce/Market point at
`37.76950`, only ~330 m to the east. Duboce Avenue would have to run at a 30°
diagonal. Duboce Ave is the south edge of Duboce Park (~37.7693) and is
essentially level; also, Sanchez Street's north terminus *is* Duboce, so
37.77120 is past the end of the street. I kept Duboce level, which is what the
"gains a little latitude going west" description implies at a realistic
magnitude.

**On the Duboce & Market 225 m figure**: the target `-122.42900, 37.76950` is
29 m from my **Duboce & Church** node, and Market Street at that longitude is
at latitude 37.7675 (my Church & Market, consistent with the real Church/Market
corner). So the stated point is the Church/Duboce corner by the Muni portal
rather than the point where a level Duboce meets Market's centre-line. My
Duboce runs 265 m further north-east before it actually reaches Market.

### Resolution — confirmed on review

All three rejections were checked and upheld by the reviewer who supplied the
anchors. For the record, in their words as well as mine:

| supplied anchor | verdict | reason |
|---|---|---|
| Filbert & Hyde `37.80220, -122.41800` | **wrong — pair inverted** | Going north the order is Filbert, Greenwich, Lombard, so Lombard is two blocks *north* of Filbert. The supplied pair placed Filbert north of Lombard. |
| Filbert & Leavenworth `37.80200, -122.41400` | **wrong — block ~2.7× too long** | 0.004° of longitude ≈ 352 m for a single block between adjacent parallel streets (~130 m). At 31.5% that implies 111 m of rise on a hill about 90 m tall. |
| Duboce & Sanchez `37.77120, -122.43080` | **wrong — mislabelled** | The coordinate is not the Duboce/Sanchez corner. It lies on Sanchez Street *north* of Duboce, between Duboce and Waller. Sanchez's north terminus is Duboce, so the corner cannot be there. |

The values this map uses instead, and why they are defensible:

- **Filbert Street sits 184 m (two blocks) south of Lombard at Hyde**, which is
  the correct order and spacing.
- **The Filbert Hyde–Leavenworth block is 123 m**, matched to the Lombard
  crooked block between the same two cross streets (documented at ~120 m). A
  block of that length can carry a 31.5% grade with ~38 m of rise; a 352 m one
  cannot.
- **Duboce Avenue runs essentially level**, gaining ~33 m of latitude over
  847 m westward — the "gains a little latitude going west" description at a
  realistic magnitude, rather than the 30° diagonal the mislabelled anchor
  would have forced.

The fourth apparent discrepancy, **Duboce & Market at 225 m**, is not an error
in either direction: the supplied point is the Church/Duboce corner by the Muni
portal, and this map's nearest node (Duboce & Church) lands **29 m** from it —
well inside snapping distance. My Duboce∩Market node is simply a different,
also-real point 265 m further north-east, where a level Duboce actually meets
Market's centre-line. No change was requested or made.

## Low-confidence list (current as of the accepted revision)

Everything below is a known, deliberate approximation. Nothing here is a
suspected error — the items in the anchor section above are the only places
where a supplied coordinate disagreed with this map, and those were resolved
in this map's favour.

1. **Richmond / Presidio Heights fringe.** The three longest remaining edges
   (791 m Arguello Fulton–Geary; 780 m California and Geary Arguello–Presidio)
   are there. Peripheral coverage; the avenues between are not modelled.
2. **Potrero Hill.** 712 m turn-free runs remain on Missouri, Minnesota,
   De Haro and similar between 18th and 22nd/24th.
3. **Twin Peaks Boulevard, Portola, Corbett, Burnett, Grand View.** Unchanged
   from revision 1: correct topology, sketched bends.
4. **Corona Heights** (Roosevelt Way, Levant Street, Ord Street, Vulcan
   Stairway) — all coordinates hand-placed.
5. **Upper Market Street** Castro to Portola, and Clarendon's junction with
   Clayton/Market collapsed to a single node.
6. **13th Street / Otis Street** as the SoMa-to-Mission stitch — alignment
   fitted to join two frames rather than traced.
7. **Kezar Drive and Stow Lake Drive** are straight-line abstractions of roads
   that curve in reality.
8. **Greenwich Steps** and the Greenwich Street stub on Telegraph Hill remain
   the roughest ways in the file.
9. **Duboce Avenue east of Market** is still omitted (see above).
10. **The Western Addition between Fulton and Geary** now has Turk Street
    through it, but McAllister, Golden Gate, Eddy, Ellis, O'Farrell and Post
    only span Lyon–Baker–Broderick, so east-west movement there is limited to
    Turk and Geary.
11. **25th and 26th Streets** are absent; 24th Street is the network's southern
    boundary in the Mission. That is an extract boundary, not a missing street.

## Revision 3 — grade defects found by DEM sampling

With 258 control points the fixture DEM became sharp enough to sample the
graph and read grades off it. Six ways sampled at grades that cannot be real.
Four were graph defects and are fixed; the residuals on three ways are DEM
artefacts and are disputed below, with evidence.

Diagnostic method: resample each way at 10 m, sample the DEM, and take the
steepest 30 m window. Filbert between Hyde and Leavenworth — 31.5% real, one
of the steepest streets in the country — samples at **37.5%** and is the
yardstick: nothing should exceed it.

### Fixed: Twin Peaks Boulevard was half its real length

Revision 1 drew a switchback road as near-straight lines between junctions.
A road drawn at half its true length over the same real climb reports double
the true grade. Restored the hairpins as shape points *inside* each block, so
the junction nodes — several of which carry pinned elevation control points —
did not move. **2218 m → 3381 m, 57.9% → 31.7%.**

Still a simplification: the real road is nearer 4.4 km and has more hairpins
than are drawn. Length was not padded further, because the grade is now
plausible and inventing hairpins to chase a number is the failure mode this
round exists to correct.

### Fixed: "Clarendon Avenue" ran across a hillside that has only stairs

This was the serious one, and not because of the grade. Revision 1 ran
Clarendon Avenue **east** from Twin Peaks Boulevard to Market at Clayton,
straight across the north shoulder of Twin Peaks. No road crosses there. The
only ways down that hillside are stairways — Pemberton Place and the Vulcan
Stairway — both of which this network already carries as `highway=steps`.
**A rideable road drawn over a staircase will route a cyclist up a flight of
stairs while the router believes it is using a street.** That is a
correctness bug in the network, independent of any grade.

The eastern link was deleted outright rather than re-drawn. Clarendon Avenue
now runs its real course: **west and south-west** from Twin Peaks Boulevard,
below Sutro Tower, through the sourced Clarendon Ave / Panorama Drive point,
down to the sourced Laguna Honda Blvd / Clarendon Ave point. **779 m →
1396 m, 49.9% → 25.5%.** `Laguna Honda Boulevard` was added to carry it back
to Portola Drive at the Woodside junction, which is real and restores a loop.

The control-point file independently corroborated this before I changed
anything: the row `Clarendon Ave midway to Market St` carries the comment
*"this fixture graph link crosses Twin Peaks' north shoulder rather than
following real Clarendon Ave"*.

### Fixed: the Buena Vista ring road ran over the park, not round it

Buena Vista Avenue East and West were drawn 130–200 m *inside* the park
boundary, passing within ~90 m of the sourced 175.3 m summit. Both are now on
the real perimeter, pinned to the two sourced park-base control points (east
base 50 m, west base 65 m), with their real curved length. `Buena Vista
Avenue` was corrected to what it actually is — the short link from the park's
east base up to Duboce Avenue at Castro — rather than a long climb over the
park's south flank. **East 517 → 690 m; West 562 → 599 m; Buena Vista Avenue
569 → 188 m (45.8% → 16.6%).**

### Fixed: upper Market Street bowed into the Twin Peaks hillside

The shape point between Market & Clayton and the sourced "below Twin Peaks"
node sat at −122.4445, bowing Market ~230 m west of its real line into
terrain Market does not cross. Moved to −122.4427, on the straight line
between the two sourced nodes. **60.5% → 45.8%** — the residual is disputed
below.

### Examined and left alone

- **Burnett Avenue (33.2%)** — below the Filbert yardstick, and Burnett is
  genuinely a steep climb off Portola. The sourced `Burnett Ave near Twin
  Peaks` point (210 m) sits essentially on the graph's own node and implies
  this grade. Not a defect.
- **Corbett Avenue (38.3% → 37.0%)** — nudged ~60 m east onto the shelf above
  Market, which is where it really runs. Now at the Filbert yardstick. Corbett
  is a steep street; not pursued further.
- **Filbert Hyde–Leavenworth (37.5%)** — unchanged. This is the reference.

## Disputed: three residuals that are DEM artefacts, not geometry

In each case the interpolated surface rises *above both sourced endpoints* of
a stretch that climbs monotonically in reality. No placement of the road fixes
that, and moving good geometry to chase the number would be the wrong trade.

**1. Upper Market Street, 45.8%.** Market carries two sourced control points:
Market & Clayton at 112 m and "Upper Market below Twin Peaks" at 172 m, 780 m
apart. Sampling the straight line between them gives 113 → **211** → 173 m:
the surface bulges 39 m *above the higher endpoint* in the middle. A street
that climbs monotonically from Clayton to the top of Market cannot do that.
Nor is it a lateral-placement problem — at that latitude the DEM reads 241 m
at −122.4445 and still 177 m at −122.4400, which is 200 m east of Market's
real line. The bulge is RBF overshoot pulled up by the Twin Peaks control
points, with nothing constraining the surface along Market itself.

**2. Buena Vista Avenue East 52.1% and West 48.5%.** The ring road is now on
the park perimeter, pinned to both sourced base points, 230–265 m from the
summit — the same radius as those bases. Yet the DEM reads 71–151 m along it,
and puts the park's south junction at **151 m** where the real Buena Vista
Ave / Park Hill Ave junction is about 105 m. There is no control point
anywhere on the park's southern half, so the RBF carries the 175.3 m summit's
mass out to the perimeter. Buena Vista Park really is steep — that is why it
is a park — but the road is cut into the flank and is nothing like a 50%
climb.

**3. Waller Street 42.4%** (not on the original list; found by the same scan).
The block between Central and Lyon samples 62 → **84** → 58 m: an 84 m bump
between two ~60 m endpoints, 180 m north of the park summit. The sourced row
`Waller St at Broderick St` is 55 m and its own comment describes this as
*"the flat street grid at Buena Vista Park's north base"*; the DEM reproduces
that point exactly at 55.0 m and then overshoots one block west. Waller is a
straight cardinal street in the Western Addition grid. Nothing to fix here.

If any of these three matter downstream, the fix is a control point on the
unconstrained stretch — one on Buena Vista Park's south perimeter and one on
upper Market between Clayton and the Twin Peaks node would resolve all three.
I did not touch `elevation_control_points.csv`.

### Feature count

1114 → **1113**. Not a trim: two fabricated Clarendon segments were deleted
and the Buena Vista link shortened to its real extent, while Laguna Honda
Boulevard, extra Clarendon blocks and the switchback shape points were added.
The switchbacks are shape points *inside* existing blocks, so they add
traversal length without inflating the feature count.

## Validator output

Final accepted run, verbatim from `python3 sf_graph.validate.py`:

```
features:            1113
distinct nodes:      671
distinct names:      149
highway histogram:   {'motorway_link': 4, 'path': 7, 'primary': 145, 'residential': 487, 'secondary': 433, 'service': 1, 'steps': 6, 'tertiary': 30}
connected components: 1  sizes: [671]
segment length m:    min 10.0  median 178.1  max 791.0
highway=steps ways:  6
motorway_link with bicycle=no: 4
near-duplicate node pairs (<5 m): 0

PASS
```

Checks performed: valid GeoJSON; every feature a `LineString` with ≥2
positions; every feature has `name` and `highway`; every coordinate inside
`(-122.5350, 37.7000, -122.3500, 37.8350)` and rounded to exactly 6 decimals;
single connected component under exact-coordinate node identity; no two
distinct coordinates within 5 m of each other (this ran red twice during
development — Duboce & Sanchez sat 4.7 m from Duboce & Steiner, and Haight's
Market terminus sat 4.7 m from Market & Valencia — both were fixed by moving
the streets, not by fusing the nodes); all required street names present; ≥4
`highway=steps`; ≥1 `motorway_link` with `bicycle=no`.

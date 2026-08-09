# Contour — External API/Library Facts

**Verified on 2026-08-09.**

All facts below were pulled from primary sources (official GitHub repos, source code, and npm registry) on 2026-08-09, working around the fact that several official docs subdomains (`docs.graphhopper.com`, `valhalla.github.io`, `docs.expo.dev`, `docs.protomaps.com`, `tnmaccess.nationalmap.gov`) were unreachable through this session's network egress proxy. Where a docs site was blocked, I fell back to the corresponding GitHub source-of-truth repo (raw markdown, or actual Java/source implementation) and note this explicitly. Anything I could not directly confirm is called out in **RISKS / UNVERIFIED** at the bottom — do not treat those as facts.

## Pinned versions (summary table)

| Library / Service | Version verified | Verified via |
|---|---|---|
| GraphHopper | **11.0** (released 2025-10-14; master is `12.0-SNAPSHOT` unreleased) | github.com/graphhopper/graphhopper releases + pom.xml |
| Valhalla | **3.8.3** (released 2026-07-25) | github.com/valhalla/valhalla releases |
| `@maplibre/maplibre-react-native` | **11.3.6** (published 2026-06-25) | npm registry |
| MapLibre Native (Android, used by RN 11.x) | 13.2.0 | maplibre-react-native docs (getting-started.md) |
| MapLibre Native (iOS, used by RN 11.x) | 6.26.0 | maplibre-react-native docs (getting-started.md) |
| Expo SDK / `expo` | **57.0.11** (published 2026-08-06) | npm registry |
| `expo-router` | **57.0.11** | npm registry |
| `expo-location` | **57.0.8** | npm registry |
| `expo-dev-client` | **57.0.10** | npm registry |
| `pmtiles` (npm, JS/TS reference impl) | **4.4.1** (published 2026-04-08) | npm registry |
| PMTiles spec | **v3** (magic `PMTiles`, 1-byte version field = 3) | github.com/protomaps/PMTiles spec/v3/spec.md |
| USGS 3DEP | N/A (data service, not versioned software) | AWS S3 `prd-tnm` bucket, live listing |

---

## 1. GraphHopper custom models

**Current stable release: 11.0** (2025-10-14). 10.2 (2025-01-20) was the prior patch, 10.0 shipped 2024-11-04.
Source: https://github.com/graphhopper/graphhopper/releases

### Custom model JSON schema (POST /route body, key `"custom_model"`)

Top-level fields: `speed`, `priority`, `distance_influence`, `areas`, `heading_penalty`, `distance_influence`.

```json
{
  "speed": [
    { "if": "road_class == MOTORWAY", "multiply_by": "0.8" },
    { "else_if": "road_class == PRIMARY", "limit_to": "80" }
  ],
  "priority": [
    { "if": "average_slope > 8", "multiply_by": "0.6" },
    { "else": "", "multiply_by": "1" }
  ],
  "distance_influence": 70,
  "areas": {
    "type": "FeatureCollection",
    "features": [
      {
        "type": "Feature",
        "id": "custom1",
        "geometry": { "type": "Polygon", "coordinates": [[[lon, lat], ["..."]]] }
      }
    ]
  }
}
```
Source: https://github.com/graphhopper/graphhopper/blob/master/docs/core/custom-models.md

- Rule statements use `if` / `else_if` / `else` plus `multiply_by` (any section), `limit_to` (speed only — caps the value), and `do` (nested sub-rules).
- Weight formula (documented verbatim): `edge_weight = edge_distance / (speed * priority) + edge_distance * distance_influence + turn_penalty`.
- `distance_influence`: **default is `0`** when omitted from a custom model — confirmed in source (`CustomModelParser.java`, line ~120: `customModel.getDistanceInfluence() == null ? 0 : ...`), NOT the `70` figure sometimes quoted informally. `distance_influence=30` means "one extra km of detour must save 30s of travel time or you won't take it."
  Source: https://github.com/graphhopper/graphhopper/blob/master/core/src/main/java/com/graphhopper/routing/weighting/custom/CustomModelParser.java
- `areas` is GeoJSON `FeatureCollection`; each feature needs an `id`, referenced in conditions as `in_<id>` (e.g. `in_custom1`).

### Real shipped example: bike + elevation custom models

GraphHopper ships this exact bicycle custom model (`core/src/main/resources/com/graphhopper/custom_models/bike.json`):
```json
{
  "priority": [
    { "if": "true",  "multiply_by": "bike_priority" },
    { "if": "bike_network == INTERNATIONAL || bike_network == NATIONAL",  "multiply_by": "1.4" },
    { "else_if": "bike_network == REGIONAL || bike_network == LOCAL",  "multiply_by": "1.2" },
    { "if": "road_environment == FERRY", "multiply_by": "0.5" },
    { "if": "mtb_rating > 2",  "multiply_by": "0" },
    { "if": "hike_rating > 1",  "multiply_by": "0" },
    { "if": "bike_road_access == NO", "multiply_by": "0" },
    { "if": "!bike_access && (!backward_bike_access || roundabout)",  "multiply_by": "0" },
    { "else_if": "!bike_access && backward_bike_access",  "multiply_by": "0.2" }
  ],
  "speed": [
    { "if": "road_environment == FERRY", "limit_to": "ferry_speed" },
    { "else": "", "limit_to": "bike_average_speed" },
    { "if": "!bike_access && backward_bike_access", "limit_to": "6" }
  ]
}
```
And the shipped `bike_elevation.json` (an additional custom-model layer, combined via `custom_model_files: [bike.json, bike_elevation.json]`):
```json
{
  "speed": [
      { "if":      "average_slope >= 15", "limit_to": "3"},
      { "else_if": "average_slope >= 12", "limit_to": "6"},
      { "else_if": "average_slope >=  8", "multiply_by": "0.60"},
      { "else_if": "average_slope >=  4", "multiply_by": "0.90"},
      { "else_if": "average_slope <= -4", "multiply_by": "1.10"}
  ]
}
```
The exact code comment that ships with `bike.json` gives the required config recipe:
```yaml
# graph.elevation.provider: srtm   # enables elevation
# graph.encoded_values: bike_priority, bike_access, bike_network, roundabout, bike_average_speed, bike_road_access, foot_road_access, average_slope, mtb_rating, hike_rating, country, road_class, ferry_speed
# profiles:
#    - name: bike
#      custom_model_files: [bike.json, bike_avoid_private_node.json, bike_elevation.json]
```
Source: https://github.com/graphhopper/graphhopper/tree/master/core/src/main/resources/com/graphhopper/custom_models (`bike.json`, `bike_elevation.json`, `foot_elevation.json`)

### Slope-related encoded values (current release, verified from source)

All defined in `core/src/main/java/com/graphhopper/routing/ev/`:

| Encoded value | Type / bits | Range (as encoded) | Meaning | Requires elevation? |
|---|---|---|---|---|
| `average_slope` | Decimal, 5 bits, factor=1, `negateReverseDirection=true` | approx **-31 .. +31** (percent-like: `100 * elevation_change / edge_distance`; negated for reverse-direction traversal of the same edge) | Average grade of the edge | **Yes** |
| `max_slope` | Decimal, 5 bits, factor=1 | approx **0 .. 31** (unsigned magnitude) | "Maximum elevation change in m/100m" — steepest sub-segment of a longer edge (more meaningful than average on long edges) | **Yes** |
| `hike_rating` | Int, 3 bits (unsigned) | **0–7** (maps from OSM `sac_scale`) | Hiking difficulty rating | No (derived from OSM tags, not elevation) |
| `mtb_rating` | Int, 3 bits (unsigned) | **0–7** (maps from OSM `mtb:scale`) | Mountain-bike technical difficulty rating | No (derived from OSM tags) |

Sources:
- https://github.com/graphhopper/graphhopper/blob/master/core/src/main/java/com/graphhopper/routing/ev/AverageSlope.java
- https://github.com/graphhopper/graphhopper/blob/master/core/src/main/java/com/graphhopper/routing/ev/MaxSlope.java
- https://github.com/graphhopper/graphhopper/blob/master/core/src/main/java/com/graphhopper/routing/ev/HikeRating.java
- https://github.com/graphhopper/graphhopper/blob/master/core/src/main/java/com/graphhopper/routing/ev/MtbRating.java

**Elevation requirement is enforced at graph-build time.** GraphHopper.java throws explicitly if `average_slope`/`max_slope` are requested as encoded values but no elevation provider is configured:
```
"average_slope and max_slope encoded values require elevation, but no elevation provider is configured"
```
Source: https://github.com/graphhopper/graphhopper/blob/master/core/src/main/java/com/graphhopper/GraphHopper.java (method `calculateSlope()`)

**Config flag to turn on these encoded values:** `graph.encoded_values` (comma-separated list), e.g.:
```yaml
graph.encoded_values: car_access, car_average_speed, road_class, average_slope, max_slope, hike_rating, mtb_rating
```
Source: https://github.com/graphhopper/graphhopper/blob/master/config-example.yml

### Enabling elevation in `config.yml` + providers

Config key: `graph.elevation.provider` (default is effectively "off"/`noop` if the key is omitted).

**Exact provider strings accepted, verified directly from `GraphHopper.java` (`createElevationProvider`)** — this is the ground truth switch statement, more reliable than the prose docs which omit some entries:

```java
if (eleProviderStr.equalsIgnoreCase("hgt"))       -> HGTProvider
else if ("srtm")                                   -> SRTMProvider        // documented default
else if ("cgiar")                                  -> CGIARProvider
else if ("gmted")                                   -> GMTEDProvider
else if ("srtmgl1")                                 -> SRTMGL1Provider
else if ("multi")                                   -> MultiSourceElevationProvider (cgiar + gmted)
else if ("skadi")                                   -> SkadiProvider      // see section 2 below
else if ("sonny")                                   -> SonnyProvider      // Europe-only, manual download, not free
else if ("multi3")                                  -> MultiSource3ElevationProvider (cgiar + gmted + sonny)
else if ("pmtiles")                                 -> PMTilesElevationProvider
```
Source: https://github.com/graphhopper/graphhopper/blob/master/core/src/main/java/com/graphhopper/GraphHopper.java

Other elevation config keys (from `config-example.yml` and `docs/core/elevation.md`):
```yaml
graph.elevation.provider: srtm
graph.elevation.cache_dir: ./srtmprovider/
graph.elevation.dataaccess: RAM         # faster for small imports
graph.elevation.clear: true             # delete cached raw tiles after import
graph.elevation.interpolate: bilinear
graph.elevation.edge_smoothing: ramer   # or moving_average
graph.elevation.long_edge_sampling_distance: 60   # meters
graph.elevation.way_point_max_distance: 10
# pmtiles-provider-specific:
graph.elevation.pmtiles.location: /data/pmtiles.pmtiles
graph.elevation.pmtiles.zoom: 11
graph.elevation.pmtiles.terrain_encoding: terrarium   # default
```
Sources: https://github.com/graphhopper/graphhopper/blob/master/config-example.yml and https://github.com/graphhopper/graphhopper/blob/master/docs/core/elevation.md

**IMPORTANT DOCS GAP FOUND:** `docs/core/elevation.md` (the prose doc) documents `srtm`, `cgiar`, `gmted`, `sonny`, `multi`, `multi3`, `pmtiles` — it does **not** mention `skadi` or `hgt`/`srtmgl1` at all, even though they are live, working options in the source code's switch statement. This is exactly the kind of docs/code drift the build spec warned about — trust the source (`GraphHopper.java`) over the prose doc for the provider list.

### Per-request custom model vs. pre-declared profile

- A **base `profile` is always required** — you cannot send a bare `custom_model` with no `profile`. Server throws: `"The 'profile' parameter is required when you use the 'custom_model' parameter"` if `profile` is missing while `custom_model` is present.
  Source: https://github.com/graphhopper/graphhopper/blob/master/web-bundle/src/main/java/com/graphhopper/resources/RouteResource.java (line ~194-196)
- A per-request `custom_model` is sent as a top-level key in the POST `/route` JSON body, alongside `points`, `profile`, etc. It is **merged on top of** the profile's own declared custom model (if any).
- **`"ch.disable": true` must also be set in the request** whenever you send a per-request `custom_model` — Contraction Hierarchies (CH) pre-compute shortcuts for one fixed weighting, so per-request weighting changes require the flexible (non-CH) algorithm. Every official test request that sends `custom_model` also sends `"ch.disable": true`.
  Source: https://github.com/graphhopper/graphhopper/blob/master/web/src/test/java/com/graphhopper/application/resources/RouteResourceCustomModelTest.java

Example (from the official test suite, San Francisco-style HGV/priority use case adapted):
```json
{
  "points": [[11.603998, 50.014554], [11.594095, 50.023334]],
  "profile": "roads",
  "ch.disable": true,
  "custom_model": {
    "speed": [{ "if": "true", "limit_to": "car_average_speed * 0.9" }],
    "priority": [{ "if": "car_access == false || hgv == NO || max_width < 3 || max_height < 4", "multiply_by": "0" }]
  }
}
```

### Alternative routes

`algorithm=alternative_route` (top-level request param). Tuning hints are namespaced under the algorithm name, exact hint keys verified from source constants (`Parameters.Algorithms.AltRoute`):

| Hint key (in request `hints`/query params) | Default | Meaning |
|---|---|---|
| `alternative_route.max_paths` | **2** | Max number of paths to compute (best + alternatives) |
| `alternative_route.max_weight_factor` | **1.25** | Alternatives can be at most this factor heavier (weight, not distance) than the best path |
| `alternative_route.max_share_factor` | **0.6** | Max fraction of the route an alternative is allowed to share with the best path |

Source: https://github.com/graphhopper/graphhopper/blob/master/web-api/src/main/java/com/graphhopper/util/Parameters.java (`AltRoute` inner class) and https://github.com/graphhopper/graphhopper/blob/master/core/src/main/java/com/graphhopper/routing/AlternativeRoute.java

**Alternatives + custom_model:** `algorithm=alternative_route` is confirmed to work together with `ch.disable: true` in the same request (official regression test `testTurnCostsAlternativeBug`, using a non-CH profile). Architecturally, `AlternativeRoute` operates on any `Weighting` (including `CustomWeighting`), so combining explicit `custom_model` + `algorithm=alternative_route` should work — but I did **not** find an official test that explicitly combines an inline `custom_model` payload with `algorithm=alternative_route` in the same request; treat this specific combination as **mostly-but-not-100%-verified** (see risks section).
Source: https://github.com/graphhopper/graphhopper/blob/master/web/src/test/java/com/graphhopper/application/resources/RouteResourceCustomModelTest.java (`testTurnCostsAlternativeBug`)

---

## 2. Skadi elevation format

**GraphHopper does ship a working Skadi provider in the current (11.0/master) codebase — confirmed directly in source, not just docs.**

- Class: `core/src/main/java/com/graphhopper/reader/dem/SkadiProvider.java`, config value `graph.elevation.provider: skadi` (verified in the `GraphHopper.java` provider switch, see section 1).
- Default base URL (AWS Open Data Terrain Tiles mirror): `https://elevation-tiles-prod.s3.amazonaws.com/skadi/`
- Class-level Javadoc: *"Skadi contains elevation data for the entire world with 1 arc second (~30m) accuracy in SRTM format stitched together from many sources"*, citing https://github.com/tilezen/joerd
Source: https://github.com/graphhopper/graphhopper/blob/master/core/src/main/java/com/graphhopper/reader/dem/SkadiProvider.java

### Exact directory/file naming (from GraphHopper's own `SkadiProvider.getDownloadURL`)
```java
String getDownloadURL(double lat, double lon) {
    String latStr = getLatString(lat);   // e.g. "N37" or "S12"  (floor(lat), zero-padded 2 digits)
    String lonStr = getLonString(lon);   // e.g. "W123" or "E045" (floor(lon), zero-padded 3 digits)
    return latStr + "/" + latStr + lonStr + ".hgt.gz";
}
```
So for San Francisco (lat ≈37.7–37.83, lon ≈-122.53 to -122.35), the tile is:
```
https://elevation-tiles-prod.s3.amazonaws.com/skadi/N37/N37W123.hgt.gz
```
Local cache filename is the same string **lower-cased** (`n37w123`).
Source: https://github.com/graphhopper/graphhopper/blob/master/core/src/main/java/com/graphhopper/reader/dem/SkadiProvider.java

### Binary format (Skadi = SRTM-derived .hgt tiles, gzip-compressed)
- Extension: `.hgt.gz` (gzip-compressed raw `.hgt`)
- 1°×1° tiles, WGS84 (EPSG:4326)
- Data type: **16-bit signed integers**
- Byte order: **big-endian ("Motorola" byte order)**
- Row-major order (row 1 first, west→east within each row, then row 2, etc.) — **no header/trailer bytes**
- Dimensions: for 1-arc-second resolution, **3601×3601** samples per tile (confirmed independently: GraphHopper's `SkadiProvider` constructor passes `3601` as the tile-size parameter to its parent `AbstractSRTMElevationProvider`)
- Elevation units: meters, WGS84/EGM96 geoid reference
- Nodata / void value: **-32768**
- Elevation value range otherwise: -32767 to 32767 m

Sources:
- https://github.com/graphhopper/graphhopper/blob/master/core/src/main/java/com/graphhopper/reader/dem/SkadiProvider.java (constructor: `super(url, cacheDir, agent, -90, 90, 3601)`)
- https://github.com/tilezen/joerd/blob/master/docs/formats.md (format description; the "Skadi" tileset is explicitly the Tilezen/Joerd terrain-tiles project, which GraphHopper's Skadi provider fetches from)
- https://registry.opendata.aws/terrain-tiles/ (AWS Open Data hosting description)

Valhalla also consumes this exact same Skadi/terrain-tiles dataset for its own elevation ingestion (see section 3) — the two projects share the data source.

---

## 3. Valhalla

**Current release: 3.8.3** (2026-07-25). Recent history: 3.8.2 (2026-07-08), 3.8.1 (2026-07-06), 3.8.0 (2026-07-06), 3.7.0 (2026-04-29).
Source: https://github.com/valhalla/valhalla/releases

### Bicycle costing options relevant to hills (from official `valhalla-docs` repo, `turn-by-turn/api-reference.md`)

| Option | Range | Default | Meaning |
|---|---|---|---|
| `use_roads` | 0–1 | **0.5** | Cyclist's tolerance for riding alongside traffic on roads. 0 favors cycleways/paths; 1 = comfortable on roads. Penalizes higher-classification/higher-speed roads based on this factor. |
| `use_hills` | 0–1 | **0.5** | Cyclist's tolerance for hills. 0 = strongly avoid hills/steep grades (even if longer); 1 = doesn't mind hills. Applies cost penalties based on **weighted grade**, including penalizing downhills too ("what goes down must go up"). |
| `bicycle_type` | enum | **Hybrid** | `Road` (narrow tires, fast, paved), `Hybrid`/`City` (default), `Cross` (cyclo-cross, wider tires), `Mountain` (heaviest/slowest on pavement, best on rough surfaces). |
| `avoid_bad_surfaces` | 0–1 | **0.25** | 0 = no penalty for poor surfaces (speed impact only); →1 = heavier penalty for surfaces bad for the chosen `bicycle_type`; **exactly 1 = poor-surface roads (incl. start/end points) are completely disallowed.** |
| `use_ferry` | 0–1 | 0.5 | Willingness to take ferries. |
| `cycling_speed` | km/h | by `bicycle_type`: Road=25, Cross=20, Hybrid/City=18, Mountain=16 | Baseline flat-ground speed, modulated by surface + grade. |

Source: https://github.com/valhalla/valhalla-docs/blob/master/turn-by-turn/api-reference.md (Bicycle costing options section)

### Alternate routes
`"alternates": <integer>` — top-level request field, "a number denoting how many alternate routes should be provided. There may be no alternates or less alternates than the user specifies." **Not supported** for multi-point (>2 locations) routes, and **not supported** for time-dependent routes.
Source: https://github.com/valhalla/valhalla/blob/master/docs/docs/api/route/api-reference.md (line ~319)

### Elevation / Skadi config

Valhalla ingests elevation at graph-build time (`valhalla_build_tiles`), reading tiles from a local directory populated by `valhalla_build_elevation`, which itself downloads Skadi-format tiles from the same AWS mirror GraphHopper uses (`https://elevation-tiles-prod.s3.amazonaws.com/skadi/` / `https://elevation-tiles-prod.s3.us-east-1.amazonaws.com/skadi/`).

```bash
# download a bbox worth of Skadi elevation tiles
valhalla_build_elevation <minlon> <maxlon> <minlat> <maxlat> ./elevation_tiles $(nproc)

# point valhalla's config at the elevation directory
valhalla_build_config --additional-data-elevation ./elevation_tiles > config.json
```

Exact JSON config keys (from `scripts/valhalla_build_config`):
```json
{
  "additional_data": {
    "elevation": "/data/valhalla/elevation/",
    "elevation_url": null,
    "elevation_url_user_pw": null
  }
}
```
`elevation_url` is an optional HTTP fallback: if a needed tile is missing from the local `elevation` directory, Valhalla will fetch it live from this URL template (containing a `{tilePath}` placeholder) instead.

Sources:
- https://github.com/valhalla/valhalla/blob/master/scripts/valhalla_build_config (keys + descriptions, lines ~180-182, ~520-522)
- https://github.com/valhalla/valhalla/blob/master/docs/docs/concepts/elevation.md
- https://github.com/valhalla/valhalla/blob/master/docs/docs/contributing/architecture/skadi.md

---

## 4. MapLibre React Native

**Package: `@maplibre/maplibre-react-native`, current version 11.3.6** (published 2026-06-25). This is the correct current package name — confirmed via npm registry and the package's own GitHub repo (`maplibre/maplibre-react-native`).
Source: https://www.npmjs.com/package/@maplibre/maplibre-react-native and https://github.com/maplibre/maplibre-react-native

### MAJOR BREAKING RENAMES in v11 (the exact risk the build spec warned about)

v11 is **the first release supporting only React Native's New Architecture**, and it deliberately realigned its component/prop API to match MapLibre GL JS / `react-map-gl` naming. If you write code against older MapLibre RN tutorials (pre-v11, when the package was still under the `@maplibre/maplibre-react-native` or older `@react-native-mapbox-gl` naming lineage), it will not compile against 11.3.6. Confirmed from the official migration guide:

- **`MapView` → `Map`** (component renamed):
  ```diff
  -<MapView mapStyle="..." />
  +<Map mapStyle="..." />
  ```
- **`ShapeSource`/`LineLayer`/`FillLayer`/`CircleLayer` (the old per-geometry-type source/layer components) no longer exist as such.** The current API instead uses:
  - `GeoJSONSource` (for GeoJSON data — replaces `ShapeSource`) — exact import: `import { GeoJSONSource } from "@maplibre/maplibre-react-native"`
  - `VectorSource` (for vector tiles, including `pmtiles://` URLs — see section 6)
  - A single unified `Layer` component (replaces `LineLayer`/`FillLayer`/`CircleLayer`/`SymbolLayer`) that takes a MapLibre Style Spec `type` prop (`"line" | "fill" | "circle" | "symbol" | ...`) plus `paint`/`layout` objects, directly following the MapLibre Style Spec:
    ```tsx
    <Layer type="line" id="route" source="route-source" paint={{ "line-color": "#3388ff", "line-width": 4 }} />
    ```
- **`Camera` prop renames** (aligned with MapLibre GL JS / react-map-gl):
  - `centerCoordinate` → `center`
  - `zoomLevel` → `zoom`
  - `heading` → `bearing`
  - `animationDuration` → `duration`
  - `animationMode` → `easing` (values also renamed: `moveTo`→`undefined`, `linearTo`→`linear`, `easeTo`→`ease`, `flyTo`→`fly`)
  - `defaultSettings` → `initialViewState`
- **Interaction prop renames:** `scrollEnabled`→`dragPan`, `zoomEnabled`→`touchZoom` (+new `doubleTapZoom`/`doubleTapHoldZoom`), `rotateEnabled`→`touchRotate`, `pitchEnabled`→`touchPitch`.
- **`contentInset`** changed from a 4-number array to a `{top,right,bottom,left}` object.
- **Ref/imperative API renames:** `getVisibleBounds()`→`getBounds()`, `getPointInView()`→`project()`, `getCoordinateFromView()`→`unproject()`, `takeSnap()`→`createStaticMapImage()`; `queryRenderedFeaturesAtPoint()`+`queryRenderedFeaturesInRect()` unified into one `queryRenderedFeatures()` (now takes geographic coords, not pixel coords).
- **`Light` component removed** — use the `light` prop directly on `<Map>`.

Source: https://github.com/maplibre/maplibre-react-native/blob/master/docs/content/setup/migrations/v11.md

### Correct import/usage example for a route line (current API)
```tsx
import { Map, Camera, GeoJSONSource, Layer } from "@maplibre/maplibre-react-native";

<Map mapStyle="https://demotiles.maplibre.org/style.json">
  <Camera center={[-122.42, 37.77]} zoom={13} />
  <GeoJSONSource id="route" data={routeGeoJSON}>
    <Layer type="line" id="route-line" paint={{ "line-color": "#3388ff", "line-width": 4 }} />
  </GeoJSONSource>
</Map>
```
Source: https://github.com/maplibre/maplibre-react-native/blob/master/docs/content/components/sources/geo-json-source.md and layer.md

### Expo Go vs. custom dev client
**Explicitly documented as NOT compatible with Expo Go**: *"This package can't be used with 'Expo Go' — it's not part of the Expo SDK."* You must use a custom development build (`expo-dev-client`) or a bare workflow build.
Source: https://github.com/maplibre/maplibre-react-native/blob/master/docs/content/setup/expo.md

### Exact Expo config plugin setup
```bash
npx expo install @maplibre/maplibre-react-native
```
```json
{
  "expo": {
    "plugins": ["@maplibre/maplibre-react-native"]
  }
}
```
Then rebuild the native app (`npx expo prebuild` + `npx expo run:ios`/`run:android`, or an EAS development build). The plugin injects `$MLRN.post_install(installer)` into the iOS `Podfile`'s `post_install` block (required for MapLibre Native to install correctly on iOS); on Android it's used only for customizations.
Source: https://github.com/maplibre/maplibre-react-native/blob/master/docs/content/setup/expo.md

### Requirements
React Native ≥ 0.80.0 (lower versions "might work" but unsupported); **New Architecture required from v11 onward** (old/bridge architecture no longer supported); Android API Level ≥ 23.
Source: https://github.com/maplibre/maplibre-react-native/blob/master/docs/content/setup/getting-started.md

---

## 5. Expo

- **Current SDK: 57** (`expo@57.0.11`, published 2026-08-06). Source: npm registry (`registry.npmjs.org/expo`).
- **`expo-router` current major: 57** (`expo-router@57.0.11` — version numbers now track the SDK number directly). Source: npm registry.
- **`expo-dev-client@57.0.10`**. Source: npm registry.
- **`expo-location@57.0.8`**. Source: npm registry.

### Creating a custom dev client (current recommended flow)
Per the official "Introduction to development builds" doc, there are three supported build methods (cloud/EAS Build, local build via `npx expo run:ios`/`run:android`, or a GUI tool like Expo Orbit) that all produce the same development build artifact, which bundles `expo-dev-client`. After building once, iterate with:
```bash
npx expo start
```
then open the app from the physical device/simulator's Home screen or connect via QR code from the dev-client launcher screen. You only need to **rebuild** the native client when you add a library containing native code.
Source: https://github.com/expo/expo/blob/main/docs/pages/develop/development-builds/introduction.mdx and .../use-development-builds.mdx
*(Note: the actual button/command matrix for the 3 build methods on this page is rendered by a client-side React form component (`BuildMethodForm`) in the docs site and did not appear as static text in the raw source I could fetch — the standard, well-established commands are `eas build --profile development --platform ios|android` for cloud builds and `npx expo run:ios` / `npx expo run:android` for local builds, but I did not get first-hand confirmation of the exact current button copy on that page.)*

### expo-location permission API (iOS foreground + background), current (SDK 57) — confirmed verbatim from source docs

```ts
import * as Location from 'expo-location';

// Foreground ("When In Use")
const { status } = await Location.requestForegroundPermissionsAsync();

// Background ("Always") — request foreground FIRST
const { status: bgStatus } = await Location.requestBackgroundPermissionsAsync();
```
- `requestForegroundPermissionsAsync()` maps to iOS **"When In Use"** authorization.
- `requestBackgroundPermissionsAsync()` maps to iOS **"Always"** authorization. Must be requested **after** foreground permission is already granted — calling it first causes iOS to prompt for both "When In Use" and "Always" together, and background methods require a **development build** (not supported in Expo Go).
- iOS "Allow Once" caveat: iOS provides no API to distinguish "Allow Once" from "Allow While Using the App" — both report as "When In Use". If the user picked "Allow Once," a subsequent `requestBackgroundPermissionsAsync()` call **silently fails** (returns `denied`, no new prompt) — the user must manually enable background location in system Settings (`Linking.openURL('app-settings:')`).

Config plugin (`app.json`), current keys:
```json
{
  "expo": {
    "plugins": [
      ["expo-location", {
        "locationAlwaysAndWhenInUsePermission": "Allow $(PRODUCT_NAME) to use your location.",
        "locationWhenInUsePermission": "Allow $(PRODUCT_NAME) to use your location.",
        "isIosBackgroundLocationEnabled": true,
        "isAndroidBackgroundLocationEnabled": true
      }]
    ]
  }
}
```
- `isIosBackgroundLocationEnabled: true` → adds `location` to `UIBackgroundModes` in Info.plist automatically (via CNG/prebuild).
- `isAndroidBackgroundLocationEnabled: true` → adds `ACCESS_BACKGROUND_LOCATION` permission (triggers Play Store review requirement).
- `locationAlwaysPermission` (iOS `NSLocationAlwaysUsageDescription`) is **deprecated** since iOS 11 in favor of `NSLocationAlwaysAndWhenInUseUsageDescription`.
- Android auto-added permissions: `ACCESS_COARSE_LOCATION`, `ACCESS_FINE_LOCATION`; optional: `FOREGROUND_SERVICE`, `FOREGROUND_SERVICE_LOCATION` (required as of Android 14), `ACCESS_BACKGROUND_LOCATION`.

Source: https://github.com/expo/expo/blob/main/docs/pages/versions/v57.0.0/sdk/location.mdx (SDK 57 docs source, fetched directly)

---

## 6. Protomaps / pmtiles

- **Spec version: v3.** Verified from the spec file itself: fixed 7-byte magic number `"PMTiles"`, fixed 1-byte version field whose value is `3`. v3 reduced the mandatory initial request to 16 KiB (down from 512 KiB in spec v2) and removed the ~300 KB hard cap on JSON metadata that v2 had.
  Source: https://github.com/protomaps/PMTiles/blob/main/spec/v3/spec.md
- **JS/TS reference library `pmtiles` on npm: 4.4.1** (2026-04-08). Source: npm registry.

### Getting a small city-sized extract
Protomaps builds the entire OSM planet into a daily PMTiles archive at `build.protomaps.com`. Use the official `pmtiles` CLI (single Go binary, GitHub Releases of `protomaps/go-pmtiles`) to extract just a bounding box **without downloading the planet file** — the CLI reads byte ranges directly from the remote archive over HTTP:
```bash
pmtiles extract https://build.protomaps.com/<YYYYMMDD>.pmtiles sf.pmtiles \
  --bbox=-122.5350,37.7000,-122.3500,37.8350 \
  --maxzoom=15
```
`--maxzoom` trims the extract size; `--region=<geojson>` can be used instead of `--bbox` for a non-rectangular clip. A city-sized extract at reasonable max zoom is typically tens of MB, versus low-single-digit GB for a country.
Source (community-corroborated, official CLI/tool referenced but I did not get a first-hand fetch of `docs.protomaps.com` because that domain was blocked by this session's egress proxy — see risks): https://github.com/protomaps/go-pmtiles, cross-checked against https://github.com/protomaps/PMTiles README.

### HTTP range-request serving
PMTiles is designed to be served from **any static file host that supports HTTP byte-range requests** (`Range` header) — S3, Cloudflare R2, GitHub Pages, Netlify, Vercel, plain nginx — no dedicated tile server process is required. The client (browser or MapLibre Native) issues `Range: bytes=...` requests to fetch just the header/root-directory (guaranteed within the first 16 KiB per the v3 spec) and then only the specific tile byte ranges it needs; the server responds `206 Partial Content` and does not need any tile/xyz-aware logic. Requirements for a correctly-configured static host: `Accept-Ranges: bytes`, and for cross-origin browser access, `Access-Control-Allow-Origin` + `Access-Control-Allow-Headers: Range`.
Source: PMTiles v3 spec (16 KiB root-directory guarantee) at https://github.com/protomaps/PMTiles/blob/main/spec/v3/spec.md; range/CORS mechanics corroborated via https://github.com/protomaps/PMTiles/issues/272 and https://github.com/maplibre/maplibre-agent-skills/blob/main/skills/maplibre-pmtiles-patterns/SKILL.md (an official MapLibre-org skill doc).

### pmtiles:// protocol in MapLibre React Native
**Confirmed built-in — no manual protocol registration needed**, unlike MapLibre GL JS on web (where you must call `maplibregl.addProtocol('pmtiles', ...)` using the `pmtiles` npm package's `Protocol` class). MapLibre Native's C++/Android/iOS core has native `pmtiles://` support, and `@maplibre/maplibre-react-native`'s own official example code uses it directly with no setup:
```tsx
import { Layer, Map, VectorSource } from "@maplibre/maplibre-react-native";

<Map mapStyle={MAPLIBRE_DEMO_STYLE}>
  <VectorSource
    id="foursquare-10M"
    url="pmtiles://https://oliverwipfli.ch/data/foursquare-os-places-10M-2024-11-20.pmtiles"
  >
    <Layer type="circle" id="foursquare-10M" source-layer="place" paint={{ "circle-color": "red" }} />
  </VectorSource>
</Map>
```
And a full map style can itself point at a pmtiles-backed vector source in its `style.json` (also demoed directly, no extra protocol setup):
```tsx
<Map mapStyle="https://raw.githubusercontent.com/wipfli/foursquare-os-places-pmtiles/refs/heads/main/style.json" />
```
Source: https://github.com/maplibre/maplibre-react-native/blob/master/examples/shared/src/examples/protocols/PMTilesVectorSource.tsx and .../PMTilesMapStyle.tsx (official example app, live in the current repo)

Note: both official RN examples use a **remote https:// URL** with the `pmtiles://` prefix (`pmtiles://https://...`). I did **not** find an official RN example using a purely local/bundled file path (e.g. `pmtiles://file:///...` or an Android `asset://`/iOS bundle resource) — see risks section; local-file usage is very likely supported (MapLibre Native's PMTiles reader is protocol-agnostic over its I/O layer) but is unverified from official examples specifically.

---

## 7. USGS 3DEP elevation data

Target bbox: **W -122.5350, S 37.7000, E -122.3500, N 37.8350** (San Francisco).

### 1/3 arc-second (~10 m) DEM — AWS S3 `prd-tnm` bucket (confirmed via live bucket listing)
The bbox falls entirely within the standard 1°×1° USGS DEM tile `n38w123`. Listing the bucket directly (`https://prd-tnm.s3.amazonaws.com/?prefix=StagedProducts/Elevation/13/TIFF/current/n38w123/&list-type=2`) returned:
```
StagedProducts/Elevation/13/TIFF/current/n38w123/USGS_13_n38w123.tif    (222,936,410 bytes ≈ 213 MB)
StagedProducts/Elevation/13/TIFF/current/n38w123/USGS_13_n38w123.gpkg  (5,406,720 bytes)
StagedProducts/Elevation/13/TIFF/current/n38w123/USGS_13_n38w123.jpg   (7,002 bytes, browse image)
StagedProducts/Elevation/13/TIFF/current/n38w123/USGS_13_n38w123.xml   (12,160 bytes, metadata)
```
Direct download URL:
```
https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/13/TIFF/current/n38w123/USGS_13_n38w123.tif
```
General path pattern: `StagedProducts/Elevation/13/TIFF/current/{tile}/USGS_13_{tile}.tif` where `{tile}` = `n{ceil(N)}w{ceil(|W|)}` (e.g. `n38w123`). This single tile (whole 1°×1° quad, ~213 MB) fully covers the requested bbox since it's much smaller than 1°×1°; there is no smaller pre-cut tile — you'd crop it locally (e.g. with `gdal_translate -projwin`) to just the target bbox.

### 1 meter lidar DEM — AWS S3 `prd-tnm` bucket (confirmed via live bucket listing)
There is an actual USGS lidar project covering San Francisco named **`CA_SanFrancisco_B23`**. Listing `https://prd-tnm.s3.amazonaws.com/?prefix=StagedProducts/Elevation/1m/Projects/CA_SanFrancisco_B23/&list-type=2` returned 4 GeoTIFF tiles (UTM-10N, 10 km × 10 km each) that cover the bbox:
```
StagedProducts/Elevation/1m/Projects/CA_SanFrancisco_B23/TIFF/USGS_1M_10_x54y418_CA_SanFrancisco_B23.tif  (176,580,019 bytes ≈ 168 MB)
StagedProducts/Elevation/1m/Projects/CA_SanFrancisco_B23/TIFF/USGS_1M_10_x54y419_CA_SanFrancisco_B23.tif  (111,944,984 bytes ≈ 107 MB)
StagedProducts/Elevation/1m/Projects/CA_SanFrancisco_B23/TIFF/USGS_1M_10_x55y418_CA_SanFrancisco_B23.tif  (147,948,547 bytes ≈ 141 MB)
StagedProducts/Elevation/1m/Projects/CA_SanFrancisco_B23/TIFF/USGS_1M_10_x55y419_CA_SanFrancisco_B23.tif  (86,461,076 bytes ≈ 82 MB)
```
General path pattern: `StagedProducts/Elevation/1m/Projects/{ProjectName}/TIFF/USGS_1M_{utm_zone}_x{xx}y{yyy}_{ProjectName}.tif` (UTM 10 km grid tile indices `x`,`y`). This is the newest available San Francisco 1 m lidar coverage as of this check (`LastModified: 2026-02-13`).

Both listings obtained directly by querying the public S3 bucket's list API (no auth needed):
```bash
curl "https://prd-tnm.s3.amazonaws.com/?prefix=StagedProducts/Elevation/13/TIFF/current/n38w123/&list-type=2"
curl "https://prd-tnm.s3.amazonaws.com/?prefix=StagedProducts/Elevation/1m/Projects/CA_SanFrancisco_B23/&list-type=2"
```
Source: live query against `prd-tnm.s3.amazonaws.com` (AWS Open Data public bucket for USGS 3DEP), 2026-08-09.

### The National Map / TNMAccess API
There is a documented public REST API, **TNMAccess**, at `https://tnmaccess.nationalmap.gov/api/v1/`, with a `/products` endpoint supporting `bbox`, `datasets`, `prodFormats`, and other query params, letting you look up exactly which staged product files (and their S3 URLs) intersect a bounding box instead of guessing tile names. **I could not directly fetch or exercise this endpoint** — `tnmaccess.nationalmap.gov` was blocked by this session's network egress proxy (policy denial on the CONNECT, not a DNS/reachability issue) — so the exact query-parameter syntax below is reconstructed from secondary sources (search snippets, the `py3dep` Python client, USGS FAQ pages) rather than a first-hand fetch, and should be spot-checked before relying on it:
```
GET https://tnmaccess.nationalmap.gov/api/v1/products?bbox=-122.5350,37.7000,-122.3500,37.8350&datasets=National%20Elevation%20Dataset%20%28NED%29%201%2F3%20arc-second&prodFormats=GeoTIFF
```
Dataset display-name strings referenced across USGS materials: `"National Elevation Dataset (NED) 1/3 arc-second"`, `"1 meter Digital Elevation Model (DEM)"`. (I recommend confirming these exact strings by hitting the live API before using them, since I could not verify them first-hand.)
Sources: https://www.usgs.gov/faqs/does-usgs-have-apis, https://github.com/hyriver/py3dep (cross-referenced dataset naming, though py3dep itself queries an ESRI ImageServer/WMS rather than TNMAccess directly).

### COG-readiness
USGS 3DEP publishes its staged elevation GeoTIFFs as Cloud-Optimized GeoTIFFs (COGs) as a matter of program policy, which would make range-read/partial access (e.g. via `rasterio`/GDAL vsicurl) practical directly against the S3 URLs above without downloading the whole file. **I did not independently verify the COG internal tiling/overview structure of the specific `.tif` files listed above** (would require `gdalinfo`/header inspection I didn't perform) — treat "these are COGs" as likely-true-but-unverified.

---

## RISKS / UNVERIFIED

These items could not be directly confirmed from primary sources in this session and should be spot-checked before the team relies on them:

1. **GraphHopper: `custom_model` + `algorithm=alternative_route` combined in one request.** Confirmed separately that (a) `custom_model` requires `ch.disable:true`, and (b) `algorithm=alternative_route` works with `ch.disable:true`. Did not find an official test exercising both together in the same request. Architecturally should work (alternative-route search is weighting-agnostic), but UNVERIFIED as an explicit combination.

2. **GraphHopper `docs.graphhopper.com` prose docs vs. source code drift on elevation providers.** The hosted prose doc (`docs/core/elevation.md`) omits `skadi`, `hgt`, and `srtmgl1` from its provider list even though they are live in the `GraphHopper.java` source switch statement (11.0/master). Treat the prose docs site as potentially stale; I trusted source code as ground truth here.

3. **Expo "create a custom dev client" exact current UI/commands.** The specific docs page (`develop/development-builds/introduction.mdx`) renders its command matrix via a client-side React component (`BuildMethodForm`) that did not appear as static text in the raw MDX source I fetched. I'm confident in the general shape (EAS Build cloud / `npx expo run:ios`|`run:android` local / Expo Orbit) from surrounding doc text and general current Expo knowledge, but did NOT get first-hand confirmation of exact current button labels or flag syntax on that specific page.

4. **MapLibre RN `pmtiles://` with a purely local/bundled file (not a remote https URL).** Official examples in the `maplibre-react-native` repo only demonstrate `pmtiles://https://...` (remote). Local-file usage (e.g. bundling a `.pmtiles` file with the app and pointing at it via a `file://` or platform asset path) was NOT found demonstrated in an official example — likely works given MapLibre Native's protocol-agnostic reader, but UNVERIFIED.

5. **pmtiles CLI (`pmtiles extract`) exact current flag syntax and `build.protomaps.com` URL/filename convention.** `docs.protomaps.com` was blocked by the network egress proxy for this session, so this was reconstructed from search-result snippets and the `protomaps/PMTiles` GitHub README rather than a first-hand fetch of the CLI reference or the hosted-extract-service docs page. Confirm exact current flags (`--bbox`, `--maxzoom`, `--region`) and the live daily-build filename pattern before scripting against them.

6. **USGS TNMAccess API (`tnmaccess.nationalmap.gov`) exact query syntax and dataset name strings.** This domain was blocked by the network egress proxy (policy denial), so I could not fetch the live OpenAPI docs or exercise a real query. The endpoint's existence is well-documented by USGS secondary pages, but the exact `datasets=` string values and full parameter list in this document are reconstructed, not directly confirmed. The AWS S3 bucket paths and file sizes in section 7, by contrast, ARE directly verified (live bucket listing).

7. **USGS 3DEP GeoTIFFs being true Cloud-Optimized GeoTIFFs (COGs).** Stated as USGS program policy/common knowledge but not independently confirmed by inspecting the actual TIFF headers of the files found above.

8. **Valhalla `weighted grade` / elevation ingestion requiring `additional_data.elevation` to be set at tile-build time for `use_hills` to have any effect.** This is a very reasonable inference from the docs (weighted grade is described as a build-time-computed edge property) but I did not find an explicit sentence stating "`use_hills` has zero effect if you didn't build tiles with elevation data" — treat as strongly implied, not verbatim-confirmed.

No files were committed to git; this is a standalone reference file at `/home/user/open-blockchain-indexer-benchmark/.research/API_FACTS.md`.

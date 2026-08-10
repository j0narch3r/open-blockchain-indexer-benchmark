"""Validate sf_graph.draft.geojson."""

from __future__ import annotations

import json
import math
import sys
from collections import defaultdict

BBOX = (-122.5350, 37.7000, -122.3500, 37.8350)
LON_M = 1.0 / 88000.0
LAT_M = 1.0 / 111000.0
NEAR_M = 5.0

REQUIRED_NAMES = [
    "Duboce Avenue", "Church Street", "Sanchez Street", "Steiner Street",
    "Waller Street", "Pierce Street", "Scott Street", "Haight Street",
    "Fell Street", "Oak Street", "Baker Street", "Divisadero Street",
    "Webster Street", "Fillmore Street", "Market Street", "Fulton Street",
    "Lincoln Way", "Buena Vista Avenue East", "Buena Vista Avenue West",
    "Ashbury Street", "Clayton Street", "Masonic Avenue", "Central Avenue",
    "Folsom Street", "Howard Street", "Harrison Street", "Bryant Street",
    "2nd Street", "3rd Street", "4th Street", "5th Street", "6th Street",
    "7th Street", "8th Street", "The Embarcadero", "Valencia Street",
    "Mission Street", "16th Street", "24th Street", "Potrero Avenue",
    "Indiana Street", "Twin Peaks Boulevard", "Portola Drive",
    "Clipper Street", "Burnett Avenue", "Filbert Street", "Hyde Street",
    "Leavenworth Street", "Page Street", "Panhandle Path",
]


def main(path: str) -> int:
    errs: list[str] = []
    warns: list[str] = []
    fc = json.load(open(path))
    if fc.get("type") != "FeatureCollection":
        errs.append("not a FeatureCollection")
    feats = fc.get("features", [])

    names: set[str] = set()
    highways: defaultdict[str, int] = defaultdict(int)
    coords: set[tuple[float, float]] = set()
    adj: defaultdict[tuple[float, float], set[tuple[float, float]]] = defaultdict(set)
    steps = 0
    ramps_no_bike = 0
    lengths = []

    for i, f in enumerate(feats):
        g = f.get("geometry", {})
        p = f.get("properties", {})
        if f.get("type") != "Feature":
            errs.append(f"[{i}] not a Feature")
            continue
        if g.get("type") != "LineString":
            errs.append(f"[{i}] geometry is {g.get('type')}, not LineString")
            continue
        cs = g.get("coordinates", [])
        if len(cs) < 2:
            errs.append(f"[{i}] LineString has {len(cs)} positions")
            continue
        if "name" not in p:
            errs.append(f"[{i}] missing name")
        if "highway" not in p:
            errs.append(f"[{i}] missing highway")
        names.add(p.get("name", ""))
        highways[p.get("highway", "")] += 1
        if p.get("highway") == "steps":
            steps += 1
        if p.get("highway") == "motorway_link" and p.get("bicycle") == "no":
            ramps_no_bike += 1
        prev = None
        for c in cs:
            if len(c) != 2:
                errs.append(f"[{i}] position with {len(c)} values")
                continue
            lon, lat = c
            if not (BBOX[0] <= lon <= BBOX[2] and BBOX[1] <= lat <= BBOX[3]):
                errs.append(f"[{i}] {p.get('name')} coord out of bbox: {c}")
            if round(lon, 6) != lon or round(lat, 6) != lat:
                errs.append(f"[{i}] coord not rounded to 6 dp: {c}")
            t = (lon, lat)
            coords.add(t)
            if prev is not None:
                adj[prev].add(t)
                adj[t].add(prev)
                lengths.append(dist(prev, t))
            prev = t

    # --- connected components ---
    seen: set[tuple[float, float]] = set()
    comps: list[list[tuple[float, float]]] = []
    for start in adj:
        if start in seen:
            continue
        stack = [start]
        seen.add(start)
        comp = []
        while stack:
            n = stack.pop()
            comp.append(n)
            for m in adj[n]:
                if m not in seen:
                    seen.add(m)
                    stack.append(m)
        comps.append(comp)
    comps.sort(key=len, reverse=True)

    # --- near-duplicate nodes (grid-bucketed) ---
    buckets: defaultdict[tuple[int, int], list[tuple[float, float]]] = defaultdict(list)
    cell = NEAR_M * 1.5
    for c in coords:
        buckets[(int(c[0] / (LON_M * cell)), int(c[1] / (LAT_M * cell)))].append(c)
    near: list[tuple[float, tuple, tuple]] = []
    for (bx, by), _pts in buckets.items():
        cand = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                cand.extend(buckets.get((bx + dx, by + dy), []))
        for a in _pts:
            for b in cand:
                if a < b and dist(a, b) < NEAR_M:
                    near.append((dist(a, b), a, b))
    near.sort()

    missing = [n for n in REQUIRED_NAMES if n not in names]

    print(f"features:            {len(feats)}")
    print(f"distinct nodes:      {len(coords)}")
    print(f"distinct names:      {len(names)}")
    print(f"highway histogram:   {dict(sorted(highways.items()))}")
    print(f"connected components: {len(comps)}  sizes: "
          f"{[len(c) for c in comps[:6]]}")
    if lengths:
        print(f"segment length m:    min {min(lengths):.1f}  "
              f"median {sorted(lengths)[len(lengths)//2]:.1f}  max {max(lengths):.1f}")
    print(f"highway=steps ways:  {steps}")
    print(f"motorway_link with bicycle=no: {ramps_no_bike}")
    print(f"near-duplicate node pairs (<{NEAR_M:.0f} m): {len(near)}")
    for d, a, b in near[:20]:
        print(f"   {d:5.2f} m  {a}  {b}")
    if missing:
        print(f"MISSING required names: {missing}")
    if len(comps) > 1:
        for c in comps[1:]:
            print(f"   orphan component ({len(c)} nodes) e.g. {c[:3]}")

    if len(comps) != 1:
        errs.append(f"graph has {len(comps)} connected components, want 1")
    if steps < 4:
        errs.append(f"only {steps} highway=steps ways, want >= 4")
    if ramps_no_bike < 1:
        errs.append("no motorway_link with bicycle=no")
    if missing:
        errs.append(f"missing required names: {missing}")
    if near:
        errs.append(f"{len(near)} near-duplicate node pairs")

    print()
    if errs:
        print(f"FAIL ({len(errs)} problems)")
        for e in errs[:40]:
            print("  -", e)
        return 1
    print("PASS")
    return 0


def dist(a, b) -> float:
    return math.hypot((b[0] - a[0]) / LON_M, (b[1] - a[1]) / LAT_M)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "sf_graph.draft.geojson"))

# GraphHopper 11.0 routing engine for Contour.
#
# ============================================================================
# Written against documentation verified 2026-08-09; NEVER EXECUTED. The
# first real run should expect to debug it. Verify tile availability,
# checksum behaviour, and GraphHopper graph-build memory before trusting
# output. See contour/.research/API_FACTS.md and docs/DECISIONS.md.
# ============================================================================
#
# GraphHopper 11.0 is the current stable release as of 2026-08-09
# (contour/.research/API_FACTS.md, pinned-versions table — verified against
# github.com/graphhopper/graphhopper's release list and pom.xml). Built from
# the tagged `11.0` source rather than a pre-built jar: GraphHopper does not
# publish an official Docker image on Docker Hub as of the facts file's
# verification pass, and building from the release tag pins the exact
# custom-model/elevation-provider behaviour API_FACTS.md verified against
# that tag's source, not whatever `latest`/`master` happens to be later.
#
# `osmium-tool` is installed here (not as a Python dependency of
# services/api) so `scripts/clip_osm.py`'s `osmium extract` subprocess call
# has somewhere to run — see that script's module docstring. Graph-build
# memory: NOT independently sized in this session (no real OSM/3DEP data was
# ever loaded to measure it against) — `JAVA_OPTS` below is a starting
# guess, not a measured value; treat it as an outstanding gate per the
# banner above.

FROM eclipse-temurin:21-jdk-jammy AS build

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    maven \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
RUN git clone --branch 11.0 --depth 1 https://github.com/graphhopper/graphhopper.git .
RUN mvn -pl web -am clean package -DskipTests

FROM eclipse-temurin:21-jre-jammy

RUN apt-get update && apt-get install -y --no-install-recommends \
    osmium-tool \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /graphhopper
COPY --from=build /build/web/target/graphhopper-web-*.jar ./graphhopper-web.jar
COPY config/graphhopper.yml ./config.yml

# Not measured against a real graph build (no real data has been loaded in
# this environment) — a starting point per GraphHopper's own sizing
# guidance (roughly 2-3x the source .osm.pbf size for import, less for
# steady-state serving of a single-city graph), not a verified figure.
ENV JAVA_OPTS="-Xmx4g -Xms2g"

EXPOSE 8989

# GraphHopper builds the graph on first start if `graph.location` doesn't
# already contain one, then serves it — both the same command, per
# GraphHopper's own web-bundle entrypoint convention.
ENTRYPOINT ["sh", "-c", "java $JAVA_OPTS -jar graphhopper-web.jar server config.yml"]

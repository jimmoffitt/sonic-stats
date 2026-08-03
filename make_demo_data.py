"""
make_demo_data.py — Build the sanitized demo dataset for read-only deploys.

Reads the real (gitignored) processed play log and writes a copy to
data/demo/plays.parquet that's safe to commit to the public repo.

Unlike a Strava activity archive (which carries GPS traces, heart rate, and
device info), plays.parquet is already narrow — build_plays_df() only ever
carries through timestamps, track/artist/album names, and metadata derived
from them (genres, release year, duration). There's no location or biometric
data to strip. This script still enumerates a field whitelist and reports
what it kept, so a future column added to build_plays_df doesn't silently
ride along into the public demo without a deliberate decision.

Also copies data/exclusions.json (artist names + before/after/keep rules —
no play-level data) to data/demo/exclusions.json, and data/groups.json
(group name -> list of artist names, equally no play-level data) to
data/demo/groups.json, both as-is, so the demo actually demonstrates the
"Remove kid streams" filter and the Bands/Groups tab instead of leaving
them a no-op / empty.

The app switches to this dataset in demo mode (see DEMO_MODE in
src/config.py). Rerun this script after a sync to refresh the demo data,
then review and commit the result.
"""
import json
import os
import shutil

import pandas as pd

from src import config

# Every column app.py / src/process_data.py actually reads, and nothing more.
FIELD_WHITELIST = [
    'ts', 'ts_local', 'ms_played', 'minutes_played',
    'track_name', 'artist_name', 'album_name', 'track_uri', 'album_id',
    'skipped', 'full_listen', 'duration_ms',
    'release_year', 'decade', 'genres', 'country',
    'year', 'month', 'hour', 'day_of_week',
    # Spotify CDN image URLs — public artwork, same as anyone sees on
    # Spotify itself, no play-level/personal data in the URL.
    'album_image_url', 'artist_image_url',
]

# Real paths, deliberately independent of config's DEMO_MODE redirection so
# this script always reads the true files even if SPOTIFY_STATS_DEMO is set.
REAL_PLAYS_FILE = os.path.join('data', 'processed', 'plays.parquet')
REAL_EXCLUSIONS_FILE = os.path.join('data', 'exclusions.json')
DEMO_EXCLUSIONS_FILE = os.path.join('data', 'demo', 'exclusions.json')
REAL_GROUPS_FILE = os.path.join('data', 'groups.json')
DEMO_GROUPS_FILE = os.path.join('data', 'demo', 'groups.json')


def main():
    df = pd.read_parquet(REAL_PLAYS_FILE)

    dropped = [c for c in df.columns if c not in FIELD_WHITELIST]
    sanitized = df[[c for c in FIELD_WHITELIST if c in df.columns]]

    os.makedirs(config.DEMO_DIR, exist_ok=True)
    sanitized.to_parquet(config.DEMO_PLAYS_FILE, index=False)

    print(f"Wrote {len(sanitized):,} plays -> {config.DEMO_PLAYS_FILE}")
    print(f"Kept columns:    {', '.join(sanitized.columns)}")
    print(f"Dropped columns: {', '.join(dropped) or '(none)'}")
    print(f"Date range: {sanitized['ts'].min().date()} -> {sanitized['ts'].max().date()}")
    print(f"Distinct artists: {sanitized['artist_name'].nunique():,}")

    if os.path.exists(REAL_EXCLUSIONS_FILE):
        shutil.copyfile(REAL_EXCLUSIONS_FILE, DEMO_EXCLUSIONS_FILE)
        n_rules = len(json.load(open(REAL_EXCLUSIONS_FILE)).get('exclude', {}))
        print(f"Wrote {n_rules:,} exclusion rule(s) -> {DEMO_EXCLUSIONS_FILE}")
    else:
        print(f"No {REAL_EXCLUSIONS_FILE} found — demo will have no exclusion "
              f"rules (the 'Remove kid streams' toggle will be a no-op).")

    if os.path.exists(REAL_GROUPS_FILE):
        shutil.copyfile(REAL_GROUPS_FILE, DEMO_GROUPS_FILE)
        n_groups = len(json.load(open(REAL_GROUPS_FILE)))
        print(f"Wrote {n_groups:,} group(s) -> {DEMO_GROUPS_FILE}")
    else:
        print(f"No {REAL_GROUPS_FILE} found — demo will have no saved "
              f"groups (the Bands/Groups tab will start empty).")


if __name__ == '__main__':
    main()

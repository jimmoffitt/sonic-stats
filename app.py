"""
app.py — Streamlit dashboard for Spotify listening history.

Loads data/processed/plays.parquet (cached) and renders the tabs from
DESIGN.md. Aggregations come from src/process_data.py; figures from
src/charts.py. Run the pipeline first to build the parquet:

    python run_pipeline.py
    streamlit run app.py
"""
# Silence urllib3's NotOpenSSLWarning: the system Python 3.9 links against
# LibreSSL 2.8.3, which urllib3 v2 doesn't certify. Harmless for our use.
# Match by message — importing the warning class would trigger it first.
import html
import warnings

warnings.filterwarnings("ignore", message=r"urllib3 v2 only supports OpenSSL")

import fnmatch
import glob
import os
import re
import zipfile
from datetime import datetime

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

import run_pipeline
from src import charts, config, process_data as proc, story

st.set_page_config(page_title="sonic-stats", page_icon="🎵", layout="wide",
                   initial_sidebar_state="expanded")


@st.cache_data
def load_plays_cached(path, mtime):
    """Cached parquet load. `mtime` busts the cache when the file changes."""
    return proc.load_plays(path)


@st.cache_data
def filtered_plays_cached(path, mtime, excl_mtime):
    """Exclusion-filtered frame, cached so toggling tabs/filters is instant.
    `mtime`/`excl_mtime` bust the cache when the parquet or exclusions change."""
    df_all = load_plays_cached(path, mtime)
    return proc.apply_exclusions(df_all, proc.load_exclusions())


@st.cache_data
def alltime_stats_cached(path, mtime, excl_mtime, apply_excl):
    """proc.alltime_stats() is the priciest call on the Wrapped page (several
    full-archive groupbys). Cache it the same way as filtered_plays_cached —
    keyed on cheap primitives rather than the DataFrame itself — so it's not
    recomputed on every widget interaction while Wrapped is open, only when
    the underlying data or exclusion filter actually changes."""
    df = (filtered_plays_cached(path, mtime, excl_mtime) if apply_excl
          else load_plays_cached(path, mtime))
    return proc.alltime_stats(df)


@st.cache_data
def track_binges_cached(path, mtime, excl_mtime, apply_excl):
    """The full (unfiltered-by-floor) track_binges() table, cached the same
    way as alltime_stats_cached. Caching the whole table rather than a
    top-10 slice means toggling the "hide one-off curiosities" floor in the
    UI is a free in-memory filter, not a recompute."""
    df = (filtered_plays_cached(path, mtime, excl_mtime) if apply_excl
          else load_plays_cached(path, mtime))
    return proc.track_binges(df)


@st.cache_data
def artist_binges_cached(path, mtime, excl_mtime, apply_excl):
    df = (filtered_plays_cached(path, mtime, excl_mtime) if apply_excl
          else load_plays_cached(path, mtime))
    return proc.artist_binges(df)


@st.cache_data
def concert_warmups_cached(path, mtime, excl_mtime, apply_excl, spike_days,
                            cooldown_days, min_spike_hours, elevation_ratio):
    """Cached the same way as the other Binges tables. Full archive (not
    date-range-filtered), same rationale as track/artist_binges_cached. The
    tuning knobs are part of the cache key, so each combination the user
    drags the sliders to gets its own cached result rather than colliding."""
    df = (filtered_plays_cached(path, mtime, excl_mtime) if apply_excl
          else load_plays_cached(path, mtime))
    return proc.artist_concert_warmups(df, spike_days=spike_days,
                                       cooldown_days=cooldown_days,
                                       min_spike_hours=min_spike_hours,
                                       elevation_ratio=elevation_ratio)


@st.cache_data
def wrapped_story_data_cached(path, mtime, excl_mtime, apply_excl, year):
    df = (filtered_plays_cached(path, mtime, excl_mtime) if apply_excl
          else load_plays_cached(path, mtime))
    return proc.wrapped_story_data(df, year)


def metric_columns(metric):
    """Map the sidebar metric toggle to (column, human label)."""
    return ('minutes', 'Minutes') if metric == 'Minutes' else ('plays', 'Plays')


# "All time" first so it stays the default (index 0) selectbox choice.
_RANGE_PRESETS = ["All time", "Last 7 days", "Last 30 days", "This month"]


def _apply_range(df, sel):
    """Apply a date-range selection: one of _RANGE_PRESETS, or a year string.
    Presets are relative to the most recent play in `df` (not wall-clock
    'now'), so a stale sync doesn't make 'Last 7 days' look emptier than it
    actually is."""
    if df.empty or sel == "All time":
        return df
    latest = df['ts_local'].max()
    if sel == "This month":
        return df[(df['ts_local'].dt.year == latest.year) &
                  (df['ts_local'].dt.month == latest.month)]
    if sel == "Last 7 days":
        return df[df['ts_local'] >= latest - pd.Timedelta(days=7)]
    if sel == "Last 30 days":
        return df[df['ts_local'] >= latest - pd.Timedelta(days=30)]
    return df[df['year'] == int(sel)]


# Editor columns. Bounds accept a year ("2019") or a year-month ("2019-06").
_COL_ARTIST, _COL_ALL = "Artist", "All years"
_COL_BEFORE, _COL_AFTER = "Before (YYYY or YYYY-MM)", "After (YYYY or YYYY-MM)"
_COL_ONLY = "Only (years/months, csv)"
_COL_KEEP = "Keep % (blank=0)"


def _norm_token(s):
    """Normalize a bound token: '2019' -> int 2019, '2019-06' -> '2019-06', else None."""
    s = str(s).strip()
    if re.fullmatch(r"\d{4}", s):
        return int(s)
    if re.fullmatch(r"\d{4}-\d{1,2}", s):
        return s
    return None


def _norm_keep(v):
    """Parse a 'keep' percent (0..100) to a fraction in (0,1); blank/0/100 -> None."""
    if v is None or (isinstance(v, float) and v != v):  # None or NaN
        return None
    try:
        pct = float(str(v).strip().rstrip('%'))
    except ValueError:
        return None
    if pct <= 0 or pct >= 100:
        return None
    return round(pct / 100, 4)


def _exclusions_to_df(exclusions):
    """Flatten the artist-centric exclusions schema into an editable table."""
    rows = []
    for artist, rule in exclusions.get('exclude', {}).items():
        if rule is True or rule == 'all':
            rows.append({_COL_ARTIST: artist, _COL_ALL: True, _COL_BEFORE: "",
                         _COL_AFTER: "", _COL_ONLY: "", _COL_KEEP: None})
        elif isinstance(rule, dict):
            only = ",".join(str(y) for y in (rule.get('years') or []))
            keep = rule.get('keep')
            rows.append({_COL_ARTIST: artist, _COL_ALL: False,
                         _COL_BEFORE: "" if rule.get('before') is None else str(rule['before']),
                         _COL_AFTER: "" if rule.get('after') is None else str(rule['after']),
                         _COL_ONLY: only,
                         _COL_KEEP: int(round(keep * 100)) if keep else None})
    return pd.DataFrame(rows, columns=[_COL_ARTIST, _COL_ALL, _COL_BEFORE,
                                       _COL_AFTER, _COL_ONLY, _COL_KEEP])


def _df_to_exclusions(edited):
    """Rebuild the exclusions schema from the edited table."""
    rules = {}
    for _, r in edited.iterrows():
        name = str(r.get(_COL_ARTIST, "")).strip()
        if not name or name.lower() == "nan":
            continue
        keep = _norm_keep(r.get(_COL_KEEP))
        if bool(r.get(_COL_ALL)):
            rules[name] = {"keep": keep} if keep is not None else True
            continue
        rule = {}
        before = _norm_token(r.get(_COL_BEFORE, ""))
        if before is not None:
            rule['before'] = before
        after = _norm_token(r.get(_COL_AFTER, ""))
        if after is not None:
            rule['after'] = after
        only = [t for t in (_norm_token(x) for x in
                            str(r.get(_COL_ONLY, "")).split(",")) if t is not None]
        if only:
            rule['years'] = only
        if keep is not None:
            rule['keep'] = keep
        rules[name] = rule or True  # an artist with no constraints -> all years
    return {"exclude": rules}


def _page_filters(df_all, with_metric=True):
    """Period (+ optionally Plays/Minutes) control row at the top of a page
    — Date range and Rank by used to live in the sidebar, shown on every
    Analytics page whether or not that page actually used them (Rankings,
    Bands, and Wrapped never did). Rendering it inline on just the pages
    that consume it fixes that, and matches the toolbar-row style Rankings
    already uses locally for its own controls.

    Per Streamlit's own docs ("Working with widgets in multipage apps"): a
    widget's key and value are deleted from session_state the moment you
    navigate away from the page that rendered it — a plain shared `key`
    does NOT survive moving between different st.Page callables. Their
    documented fix is what's used here: the widget itself uses a
    throwaway key (`_date_range`), and its value is copied into a plain,
    non-widget session_state entry (`date_range`) on every render — that
    copy isn't tied to widget lifecycle, so it survives navigation, and
    seeds the next page's `index` when the widget is freshly instantiated
    there. Returns (range_sel, metric), metric is None when with_metric is
    False."""
    years = sorted(df_all['year'].dropna().unique().tolist(), reverse=True)
    range_opts = _RANGE_PRESETS + [str(y) for y in years]
    # Default to the current year (when present) rather than "All time" —
    # this is a single shared session_state key, so it applies uniformly
    # across every page that uses this control.
    current_year = str(datetime.now().year)
    default_range = current_year if current_year in range_opts else range_opts[0]
    range_cur = st.session_state.get('date_range', default_range)
    range_idx = range_opts.index(range_cur) if range_cur in range_opts else 0
    if with_metric:
        col1, col2 = st.columns([2, 1])
        with col1:
            range_sel = st.selectbox("Date range", range_opts,
                                     index=range_idx, key="_date_range")
        with col2:
            metric_opts = ["Plays", "Minutes"]
            metric_cur = st.session_state.get('rank_by_metric', metric_opts[0])
            metric_idx = metric_opts.index(metric_cur) if metric_cur in metric_opts else 0
            metric = st.radio("Rank by", metric_opts, horizontal=True,
                              index=metric_idx, key="_rank_by_metric")
        st.session_state['date_range'] = range_sel
        st.session_state['rank_by_metric'] = metric
        return range_sel, metric
    range_sel = st.selectbox("Date range", range_opts,
                             index=range_idx, key="_date_range")
    st.session_state['date_range'] = range_sel
    return range_sel, None


def _sidebar_options():
    """Global display/data options — always visible (not just on Analytics
    pages), since kid-stream exclusion affects every page, not only the
    Analytics tabs. Chart theming isn't a separate option here anymore — it
    follows Streamlit's own light/dark theme directly (see main()), rather
    than a manual toggle that could drift out of sync with it."""
    return st.toggle(
        "Remove kid streams?", value=True,
        help="Filter out the artists/years configured under Artist filters "
             "(e.g. shared-account years).")


def _relative_time(ts):
    """Human relative-time phrase down to the minute: 'just now', '12 min
    ago', '3h ago', '2d ago' — finer-grained than the sync-freshness phrase
    below since the latest *listen* (unlike a sync run) can be minutes old."""
    mins = (pd.Timestamp.now(tz='UTC') - pd.Timestamp(ts)).total_seconds() / 60
    if mins < 1:
        return "just now"
    if mins < 60:
        return f"{mins:.0f} min ago"
    hrs = mins / 60
    return f"{hrs:.0f}h ago" if hrs < 48 else f"{hrs / 24:.0f}d ago"


def _sidebar_data(df_all):
    """Data-freshness section in the sidebar: the most recent play (when,
    what), how current the sync is, plus a one-click Sync. Shown on every
    page so updating is always at hand."""
    st.markdown("**Data**")
    latest = df_all.loc[df_all['ts'].idxmax()]
    st.caption(f"Latest play: {latest['ts'].strftime('%Y-%m-%d %H:%M UTC')} "
               f"({_relative_time(latest['ts'])})")
    st.caption(f"🎵 {latest['track_name']} — {latest['artist_name']}")

    if config.DEMO_MODE:
        # Read-only demo build: bundled sanitized dataset, no Spotify
        # credentials on the host, so live sync is unavailable by design.
        st.caption("Demo mode — read-only sample dataset; live sync is disabled.")
        return

    # A sync just finished on the previous run — surface it now (post-rerun).
    done = st.session_state.pop('_sync_msg', None)
    if done:
        st.toast(done, icon="✅")

    last_sync = run_pipeline._read_last_sync()
    at = last_sync.get('last_sync_at')
    if at:
        hrs = (pd.Timestamp.now(tz='UTC') - pd.Timestamp(at)).total_seconds() / 3600
        when = ("just now" if hrs < 1 else
                f"{hrs:.0f}h ago" if hrs < 48 else f"{hrs / 24:.0f}d ago")
        # "Synced" alone reads as "caught up" — it's really just "the sync
        # process last ran". Spotify's own recently-played endpoint lags
        # real-time listening, so a sync often legitimately finds nothing
        # new; say so explicitly rather than leaving that gap to guesswork.
        new = last_sync.get('last_new', 0)
        detail = f"{new} new play{'s' if new != 1 else ''}" if new else "no new plays"
        st.caption(f"Synced {when} ({detail})")
    else:
        st.caption("Never synced")

    authorized = os.path.exists(config.TOKEN_FILE)
    if st.button("🔄 Sync now", disabled=not authorized, width='stretch',
                 help="Fetch your latest plays from Spotify (recently-played)."):
        with st.spinner("Syncing…"):
            try:
                res = run_pipeline.sync()
                st.cache_data.clear()
                msg = f"Synced {res['added']} new play(s) ({res['fetched']} fetched)."
                if res['capped']:
                    msg += (f" ⚠️ Hit the {config.RECENTLY_PLAYED_LIMIT}-play API "
                            "limit — older plays since your last sync may be "
                            "missing. Sync more often to avoid gaps.")
                st.session_state['_sync_msg'] = msg
                st.rerun()
            except Exception as e:
                st.error(f"Sync failed: {e}")
    if not authorized:
        st.caption("Authorize once in a terminal: `python -m src.setup_tokens`")


def _extract_gdpr_zip(uploaded_file):
    """Pull Streaming_History_Audio_*.json files out of an uploaded Spotify
    export zip and write them into data/raw/, flattening any folder structure
    Spotify wraps them in. Returns the filenames written; raises ValueError
    if the zip contains none (e.g. the smaller "Account data" export, which
    doesn't include play-by-play history)."""
    written = []
    with zipfile.ZipFile(uploaded_file) as zf:
        for info in zf.infolist():
            name = os.path.basename(info.filename)
            if not name or not fnmatch.fnmatch(name, config.GDPR_GLOB):
                continue
            with zf.open(info) as src, open(os.path.join(config.RAW_DIR, name), 'wb') as dst:
                dst.write(src.read())
            written.append(name)
    if not written:
        raise ValueError(
            f"No '{config.GDPR_GLOB}' files found in that zip. Make sure you "
            "requested **Extended streaming history** — the smaller \"Account "
            "data\" export doesn't include play-by-play history.")
    return written


def _render_import_flow(key_prefix, button_label="Build my dashboard",
                        show_popover=True):
    """Upload-a-zip -> extract -> bootstrap flow. Shared by the first-run
    onboarding screen and the Settings "re-import" section (below) so
    getting a fresh export in — first time or a re-download months later —
    never requires data/raw/ file-copying or a terminal.
    `key_prefix` keeps widget keys distinct between the two call sites."""
    if show_popover:
        with st.popover("❓ How do I get my Spotify data?"):
            st.markdown(
                "1. Go to Spotify **[Account → Privacy settings]"
                "(https://www.spotify.com/account/privacy/)**.\n"
                "2. Under **Download your data**, check **Extended streaming "
                "history** — the default \"Account data\" option is a smaller "
                "summary and isn't enough.\n"
                "3. Click **Request data** and confirm via the email Spotify "
                "sends.\n"
                "4. **Wait.** It can take a few hours to ~30 days (usually a "
                "few days). Spotify emails a download link when it's ready.\n"
                "5. Download the **.zip** and upload it below — no need to "
                "unzip it first."
            )

    uploaded = st.file_uploader(
        "Upload your Spotify export (.zip)", type="zip",
        help="The zip file Spotify emailed you a link to — drop it in as-is.",
        key=f"{key_prefix}_uploader")

    raw_ready = bool(glob.glob(os.path.join(config.RAW_DIR, config.GDPR_GLOB)))
    if uploaded is not None:
        try:
            written = _extract_gdpr_zip(uploaded)
        except (zipfile.BadZipFile, ValueError) as e:
            st.error(str(e))
            return
        st.success(f"Found {len(written)} file(s) in your export.")
        raw_ready = True

    if not raw_ready:
        st.caption("Waiting on your export upload before this dashboard can build.")
        return

    if st.button(button_label, type="primary", width='stretch',
                 key=f"{key_prefix}_build"):
        try:
            config.validate_config()
        except ValueError:
            st.error(
                "Missing Spotify API credentials (`SPOTIFY_CLIENT_ID` / "
                "`SPOTIFY_CLIENT_SECRET`), needed to enrich tracks with "
                "genres and release dates. Create a free Spotify app and "
                "save them to `.local.env` — see the README's *Getting "
                "started* step 3, then reload this page.")
            return
        with st.spinner("Building your dashboard — hundreds of enrichment "
                         "API calls, usually a few minutes…"):
            try:
                run_pipeline.bootstrap()
            except Exception as e:
                st.error(f"Bootstrap failed: {e}")
                return
        st.cache_data.clear()
        st.rerun()


def render_onboarding():
    """First-run screen, shown until data/processed/plays.parquet exists.
    Walks the user through the one part of setup that can't be automated —
    Spotify's own export request — then takes their archive straight from
    the browser (no data/raw/ file-copying, no terminal) and builds the
    dashboard in place."""
    st.title("🎵 sonic-stats")
    st.markdown("### Let's get your listening history")
    _render_import_flow(key_prefix="onboard")


def main():
    # Streamlit's default top padding (96px) leaves room well beyond the
    # 60px, opaquely-white floating header bar — trim the excess without
    # tucking content under the header itself.
    st.markdown(
        "<style>[data-testid='stMainBlockContainer'] { padding-top: 4.5rem; }</style>",
        unsafe_allow_html=True,
    )

    if not os.path.exists(config.PLAYS_FILE):
        render_onboarding()
        return

    df_all = load_plays_cached(config.PLAYS_FILE, os.path.getmtime(config.PLAYS_FILE))

    # Shared context the page callables read at render time. Populated below,
    # after the sidebar filters resolve — but before pg.run() invokes a page.
    ctx = {}

    # Page wrappers: st.Page needs zero-arg callables, so each reads from ctx.
    def _artists():  render_artists(ctx['df'])
    def _rankings(): render_rankings(ctx['df'])
    def _tracks():   render_tracks(ctx['df'])
    def _albums():   render_albums(ctx['df'])
    def _genres():   render_genres(ctx['df'])
    def _decades():  render_decades(ctx['df'])
    def _story():
        # alltime computed here, not eagerly in main(), so visiting any other
        # page doesn't pay for alltime_stats_cached() — only this page needs it.
        alltime = alltime_stats_cached(config.PLAYS_FILE,
                                       os.path.getmtime(config.PLAYS_FILE),
                                       ctx['excl_mtime'], ctx['apply_excl'])
        story_loader = lambda year: wrapped_story_data_cached(
            config.PLAYS_FILE, os.path.getmtime(config.PLAYS_FILE),
            ctx['excl_mtime'], ctx['apply_excl'], year)
        render_wrapped_story(ctx['df'], alltime, story_loader)
    def _patterns(): render_patterns(ctx['df'])
    def _bands():    render_bands(ctx['df'])
    def _binges():
        track_peaks = track_binges_cached(config.PLAYS_FILE,
                                          os.path.getmtime(config.PLAYS_FILE),
                                          ctx['excl_mtime'], ctx['apply_excl'])
        artist_peaks = artist_binges_cached(config.PLAYS_FILE,
                                            os.path.getmtime(config.PLAYS_FILE),
                                            ctx['excl_mtime'], ctx['apply_excl'])
        # Warmups depend on tunable sliders drawn inside render_concert_warmups
        # itself, so it's a loader closure rather than a precomputed table —
        # each slider combo still hits concert_warmups_cached's own cache.
        warmup_loader = lambda **kw: concert_warmups_cached(
            config.PLAYS_FILE, os.path.getmtime(config.PLAYS_FILE),
            ctx['excl_mtime'], ctx['apply_excl'], **kw)
        render_binges(track_peaks, artist_peaks, warmup_loader)
    def _artist_filters(): render_artist_filters(df_all)
    def _explore():  render_explore(ctx['df'])
    def _export():   render_export(ctx['df'])
    def _settings(): render_settings(ctx['df'])

    analytics = [
        st.Page(_artists,  title="Artists",  icon="🎸", url_path="artists", default=True),
        st.Page(_tracks,   title="Tracks",   icon="🎵", url_path="tracks"),
        st.Page(_albums,   title="Albums",   icon="💿", url_path="albums"),
        st.Page(_rankings, title="Favorite bands by year", icon="🏆", url_path="rankings"),
        st.Page(_patterns, title="Patterns", icon="🕐", url_path="patterns"),
        st.Page(_binges,   title="Binges and Concerts", icon="🔥", url_path="binges"),
        st.Page(_decades,  title="Decades",  icon="📅", url_path="decades"),
        st.Page(_genres,   title="Genres",   icon="🎼", url_path="genres"),
        st.Page(_bands,    title="Groups of Groups dude", icon="🎤", url_path="bands"),
        # Still iterating on this one — parked at the bottom rather than
        # being the first thing anyone sees.
        st.Page(_story,    title="Wrapped Story", icon="✨", url_path="wrapped"),
    ]
    tools = [
        st.Page(_artist_filters, title="Artist filters", icon="🚫",
                url_path="artist-filters"),
        st.Page(_explore,  title="Explore",  icon="🔍", url_path="explore"),
        st.Page(_export,   title="Export",   icon="📤", url_path="export"),
        st.Page(_settings, title="Settings", icon="⚙️", url_path="settings"),
    ]

    # Route via st.navigation (so the selected page survives reruns — no more
    # snap-back), but hide its built-in nav so we can build the sidebar by hand
    # and slot the Data section and options between the two nav groups.
    pg = st.navigation({"Analytics": analytics, "Tools & settings": tools},
                       position="hidden")

    with st.sidebar:
        st.title("🎵 sonic-stats")
        st.caption(f"{len(df_all):,} plays · {df_all['minutes_played'].sum()/60:,.0f} "
                  f"hours · {df_all['artist_name'].nunique():,} artists")
        _sidebar_dark = st.context.theme.type == 'dark'
        _sidebar_bg = '#262730' if _sidebar_dark else '#f0f2f6'
        # Streamlit's native sidebar open/close buttons default to a ~28px hit
        # target — fiddly to tap precisely on a phone. Enlarge both toward the
        # ~44px mobile touch-target guideline; purely cosmetic/hit-area, no
        # behavior change. The header (holding the close button) also isn't
        # sticky by default — on a phone, scrolling down through Analytics/
        # Filters/Data/Tools pushes the close button off the top of the
        # screen entirely, with no way to close the sidebar without scrolling
        # back up first. Pin it to the top of the sidebar's own scroll area.
        st.markdown(
            f"""
            <style>
            [data-testid="stSidebarCollapseButton"] button,
            [data-testid="stExpandSidebarButton"] {{
                width: 44px !important;
                height: 44px !important;
                padding: 8px !important;
            }}
            [data-testid="stSidebarCollapseButton"] [data-testid="stIconMaterial"],
            [data-testid="stExpandSidebarButton"] [data-testid="stIconMaterial"] {{
                font-size: 28px !important;
            }}
            [data-testid="stSidebarHeader"] {{
                position: sticky;
                top: 0;
                z-index: 999;
                background-color: {_sidebar_bg};
            }}
            /* Page-link labels default to a single non-wrapping line (e.g.
               "Artist filters"), which forces the sidebar to stay wide on a
               phone to avoid clipping it. Let labels wrap to a second line
               instead, so the sidebar can be narrowed without losing text. */
            [data-testid="stSidebar"] a[data-testid="stPageLink-NavLink"] {{
                height: auto !important;
                min-height: 32px;
                padding-top: 6px !important;
                padding-bottom: 6px !important;
            }}
            [data-testid="stSidebar"] a[data-testid="stPageLink-NavLink"] > span:last-child {{
                height: auto !important;
                overflow: visible !important;
                white-space: normal !important;
            }}
            [data-testid="stSidebar"] a[data-testid="stPageLink-NavLink"] p {{
                white-space: normal !important;
                word-break: break-word;
                height: auto !important;
                line-height: 1.25;
            }}
            </style>
            """,
            unsafe_allow_html=True,
        )
        st.markdown("**Analytics**")
        for p in analytics:
            st.page_link(p)
            if p.url_path == "patterns":
                # Everything below here is newer/less settled than the core
                # Artists/Tracks/Albums/Rankings/Patterns views — flag it so
                # a rough edge reads as "still evolving," not "broken."
                st.caption("🧪 *experimental & evolving below*")
        st.divider()
        _sidebar_data(df_all)
        st.divider()
        st.markdown("**Tools & settings**")
        for p in tools:
            st.page_link(p)
        apply_excl = _sidebar_options()

        # Selecting a page link on mobile leaves the sidebar covering the
        # whole screen with no obvious next step. Auto-collapse it after
        # navigation, matching typical mobile nav-drawer behavior; desktop is
        # left alone since the sidebar coexists with the content there.
        _autoclose_js = """
            <script>
            (function() {
                if (window.parent.__sonicSidebarAutoCloseAttached) return;
                window.parent.__sonicSidebarAutoCloseAttached = true;
                window.parent.document.addEventListener('click', function(e) {
                    var link = e.target.closest('[data-testid="stPageLink-NavLink"]');
                    if (!link) return;
                    if (window.parent.innerWidth > 768) return;
                    setTimeout(function() {
                        var doc = window.parent.document;
                        var sidebar = doc.querySelector('[data-testid="stSidebar"]');
                        var btn = doc.querySelector('[data-testid="stSidebarCollapseButton"] button');
                        if (btn && sidebar && sidebar.getAttribute('aria-expanded') === 'true') {
                            btn.click();
                        }
                    }, 150);
                }, true);
            })();
            </script>
            """
        # st.iframe (raw-HTML form) is components.v1.html's replacement, but
        # it only exists on Streamlit >=1.51ish — newer than what's pinned in
        # requirements.txt. Prefer it when present (e.g. Streamlit Community
        # Cloud, which warns on the deprecated call) and fall back otherwise,
        # so this doesn't break on older local installs.
        if hasattr(st, 'iframe'):
            # st.iframe's height must be a positive int (0 raises
            # StreamlitInvalidHeightError) — 1px is the smallest valid,
            # effectively-invisible size for this headless JS injection.
            st.iframe(_autoclose_js, height=1)
        else:
            components.html(_autoclose_js, height=0, scrolling=False)
    # Charts follow Streamlit's own theme directly rather than a separate
    # manual toggle, so they can't drift out of sync with the rest of the
    # app's light/dark chrome (the sidebar background above uses the same
    # signal).
    charts.set_theme(st.context.theme.type == 'dark')

    excl_mtime = (os.path.getmtime(config.EXCLUSIONS_FILE)
                  if os.path.exists(config.EXCLUSIONS_FILE) else 0)
    df = (filtered_plays_cached(config.PLAYS_FILE,
                                os.path.getmtime(config.PLAYS_FILE), excl_mtime)
          if apply_excl else df_all)

    ctx.update(df=df, excl_mtime=excl_mtime, apply_excl=apply_excl)

    # "🎵 sonic-stats" branding + totals live in the sidebar now (top of the
    # with st.sidebar: block above) — nothing renders here before pg.run(),
    # so each page's own heading is the very first thing in the main area.
    pg.run()


# --- Tab renderers ---

def render_image_banner(images):
    """A row of up to 10 thumbnails with captions underneath — the shared
    piece behind the Artists/Tracks/Albums banners. `images` is a list of
    (label, url) pairs, already filtered to non-null urls by the
    top_*_images() functions that build it. Silently renders nothing if
    there's nothing to show (e.g. a brand-new archive with no enrichment
    yet), rather than an empty row of blank tiles.

    Raw HTML/CSS grid rather than st.columns + st.image + st.caption: a
    character-count truncation on the caption (tried first) can't reliably
    prevent wrapping since rendered text width depends on the actual
    characters, not just their count, and Spotify's artist/album art isn't
    consistently square — a portrait photo mixed into a row of square ones
    throws the whole row's height, and therefore caption baseline, out of
    alignment. CSS handles both: text-overflow:ellipsis truncates however
    long the string actually renders, and object-fit:cover crops every
    tile to the same square regardless of its source aspect ratio."""
    if not images:
        return
    dark = st.context.theme.type == 'dark'
    caption_color = '#a3a8b8' if dark else '#6b7078'
    tiles = "".join(
        f'<div style="min-width:0;">'
        f'<img src="{html.escape(url)}" title="{html.escape(label)}" '
        f'style="width:100%; aspect-ratio:1; object-fit:cover; border-radius:6px; '
        f'display:block;">'
        f'<div style="font-size:12px; color:{caption_color}; margin-top:4px; '
        f'white-space:nowrap; overflow:hidden; text-overflow:ellipsis;" '
        f'title="{html.escape(label)}">{html.escape(label)}</div>'
        f'</div>'
        for label, url in images
    )
    st.markdown(
        f'<div style="display:grid; grid-template-columns:repeat({len(images)}, 1fr); '
        f'gap:12px;">{tiles}</div>',
        unsafe_allow_html=True)


def render_artists(df):
    range_sel, metric = _page_filters(df)
    value_col, value_label = metric_columns(metric)
    view = _apply_range(df, range_sel)
    st.subheader("Top artists")
    render_image_banner(proc.top_artist_images(view, n=10, metric=value_col))
    top = proc.top_artists(view, n=25, metric=value_col)
    st.plotly_chart(charts.ranked_bar(top, 'artist_name', value_col,
                                      f"Top artists by {value_label.lower()}"),
                    use_container_width=True)
    st.caption("Country choropleth requires Phase 2 MusicBrainz enrichment.")


def render_rankings(df):
    """Years-across-the-top rank chart (the Markdown table, made interactive)."""
    st.subheader("Top artists per year")

    c1, c2, c3 = st.columns([1.2, 1.2, 1])
    metric = c1.radio("Rank by", ["Minutes", "Plays"], horizontal=True, key="rank_metric")
    n = c2.slider("Artists per year", 5, 25, 10, key="rank_n")
    show_values = c3.checkbox("Show values in cells", key="rank_vals")
    metric_key = 'minutes' if metric == "Minutes" else 'plays'

    wide = proc.top_artists_wide(df, n=n, metric=metric_key, show_values=show_values)

    # Year navigation: limit which year columns are shown (the table is wide).
    # Options run newest-first so the slider reads left-to-right as 2026 → older;
    # the handles come back in that order, so min/max recover the actual range.
    years_desc = sorted((int(c) for c in wide.columns), reverse=True)
    if len(years_desc) > 1:
        sel = st.select_slider(
            "Year range", options=years_desc,
            value=(years_desc[0], years_desc[-1]))
        lo, hi = min(sel), max(sel)
        wide = wide[[c for c in wide.columns if lo <= int(c) <= hi]]

    # Present newest-first: current year on the left, older years to the right.
    wide = wide[list(reversed(wide.columns))]

    st.dataframe(wide, width='stretch', height=38 * n + 60)
    st.download_button(
        "Download Markdown table",
        proc.wide_to_markdown(wide, f"Top {n} artists per year — by {metric_key}"),
        "top_artists_by_year.md", "text/markdown")


def render_tracks(df):
    range_sel, metric = _page_filters(df)
    value_col, value_label = metric_columns(metric)
    view = _apply_range(df, range_sel)
    st.subheader("Top tracks")
    render_image_banner(proc.top_track_images(view, n=10, metric=value_col))
    top = proc.top_tracks(view, n=25, metric=value_col)
    label = top['track_name'] + " — " + top['artist_name']
    chart_df = top.assign(label=label)
    st.plotly_chart(charts.ranked_bar(chart_df, 'label', value_col,
                                      f"Top tracks by {value_label.lower()}"),
                    use_container_width=True)
    st.dataframe(top[['track_name', 'artist_name', 'plays', 'minutes']],
                width='stretch', hide_index=True)


def render_albums(df):
    range_sel, metric = _page_filters(df)
    value_col, value_label = metric_columns(metric)
    view = _apply_range(df, range_sel)
    st.subheader("Top albums")
    render_image_banner(proc.top_album_images(view, n=10, metric=value_col))
    top = proc.top_albums(view, n=25, metric=value_col)
    label = top['artist_name'].fillna('Unknown') + " — " + top['album_name'].fillna('Unknown')
    chart_df = top.assign(label=label)
    st.plotly_chart(charts.ranked_bar(chart_df, 'label', value_col,
                                      f"Top albums by {value_label.lower()}"),
                    use_container_width=True)


def render_genres(df):
    range_sel, metric = _page_filters(df)
    value_col, value_label = metric_columns(metric)
    view = _apply_range(df, range_sel)
    st.subheader("Genre families")
    st.caption("Spotify's genre tags are hundreds of narrow micro-genres "
               "('jangle pop', 'power pop', 'dream pop'...) that fragment "
               "any flat ranking — grouped here into broad families, with "
               "each family's own top micro-genres nested inside.")
    tree = proc.genre_group_treemap_data(view, metric=value_col)
    if tree.empty:
        st.info("No genre data yet — run artist enrichment.")
        return
    st.plotly_chart(charts.genre_treemap(tree, value_col,
                                         f"Genre families by {value_label.lower()}"),
                    use_container_width=True)

    st.divider()
    st.subheader("Genre family share")
    macro = proc.macro_genre_breakdown(view, metric=value_col)
    if not macro.empty:
        st.plotly_chart(charts.genre_pie(macro, value_col,
                                         f"Share of {value_label.lower()} by genre family"),
                        use_container_width=True)

    st.divider()
    st.subheader("Top genres")
    top = proc.top_genres(view, n=25, metric=value_col)
    if not top.empty:
        st.plotly_chart(charts.ranked_bar(top, 'genres', value_col,
                                          f"Top genres by {value_label.lower()}"),
                        use_container_width=True)

    st.divider()
    st.subheader("Top bands by genre")
    genre_families = proc.GENRE_MACRO_COLOR_ORDER + ["Other"]
    top10_genres = proc.top_genres(view, n=10, metric=value_col)
    genre_choice = st.selectbox(
        "Genre", ["All"] + genre_families + top10_genres['genres'].tolist(),
        key="genre_band_filter",
        help="Pick a broad genre family (e.g. 'Rock / Indie') or one of "
             "your top 10 specific genres (e.g. 'indie rock').")
    band_n = st.slider("Bands to show", 5, 25, 10, key="genre_band_n")
    genre_filter = None if genre_choice == "All" else genre_choice
    is_macro = genre_choice in genre_families
    top_bands = proc.top_artists_by_genre(view, genre_filter, n=band_n,
                                          metric=value_col, is_macro=is_macro)
    if top_bands.empty:
        st.info("No bands found for this genre in the selected range.")
    else:
        st.dataframe(top_bands.rename(columns={
            'rank': 'Rank', 'artist_name': 'Band',
            'plays': 'Plays', 'minutes': 'Minutes',
        }), width='stretch', hide_index=True)


def render_decades(df):
    range_sel, metric = _page_filters(df)
    value_col, value_label = metric_columns(metric)
    view = _apply_range(df, range_sel)
    st.subheader("Listening by release decade")
    st.caption("Release decade comes from Spotify's album metadata, which "
               "usually reflects the *edition* you streamed (a remaster or "
               "reissue) rather than the music's original release — so some "
               "of this can be skewed toward a later decade than it truly "
               "came from. The marker below shows roughly when you started "
               "using Spotify: decades at/after it are more likely "
               "real-time new-release listening; earlier decades are "
               "inherently back-catalog, and where that skew concentrates.")
    dec = proc.decade_breakdown(view)
    if dec.empty:
        st.info("No release-date data yet — run track enrichment.")
        return
    first_play_year = int(df['ts'].min().year)
    first_play_decade = first_play_year // 10 * 10
    st.plotly_chart(charts.decade_bar(dec, value_col,
                                      f"{value_label} by decade",
                                      spotify_start_decade=first_play_decade,
                                      spotify_start_year=first_play_year),
                    use_container_width=True)

    st.divider()
    st.subheader("Top bands & songs by decade")
    st.caption("Ranked within each release decade (1960s onward) by "
               f"{value_label.lower()}.")
    n = st.slider("Per decade", 5, 25, 10, key="decade_n")

    bands_wide = proc.top_artists_by_decade_wide(view, n=n, metric=value_col,
                                                 min_decade=1960)
    if bands_wide.empty:
        st.info("No release-date data from the 1960s onward yet.")
    else:
        st.markdown("**Top bands**")
        st.dataframe(bands_wide, width='stretch', height=38 * n + 60)

    songs_wide = proc.top_tracks_by_decade_wide(view, n=n, metric=value_col,
                                                min_decade=1960)
    if not songs_wide.empty:
        st.markdown("**Top songs**")
        st.dataframe(songs_wide, width='stretch', height=38 * n + 60)


_WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday',
             'Saturday', 'Sunday']


def _fmt_hour(h):
    """24h int -> friendly clock label: 0 -> '12am', 13 -> '1pm'."""
    return f"{h % 12 or 12}{'am' if h < 12 else 'pm'}"


def _alltime_stats_table(s):
    """All-time totals + records as a tidy Stat/Value frame — was a grid of
    st.metric tiles when this lived on its own 'Wrapped' page; now the top
    section of the combined Wrapped Story page, ahead of the story widget."""
    share = s['top_artist'][1] / s['total_plays'] * 100
    day, day_n = s['busiest_day']
    mon, mon_n = s['busiest_month']
    yr, yr_n = s['biggest_year']
    rows = [
        ("Total plays", f"{s['total_plays']:,}"),
        ("Total hours", f"{s['total_hours']:,.0f}"),
        ("Listening days", f"{s['listening_days']:,}"),
        ("Span", f"{s['span_years']} yrs"),
        ("Artists", f"{s['unique_artists']:,}"),
        ("Tracks", f"{s['unique_tracks']:,}"),
        ("Albums", f"{s['unique_albums']:,}"),
        ("Genres", f"{s['unique_genres']:,}"),
        ("Busiest day", f"{day} ({day_n:,} plays)"),
        ("Busiest month", f"{mon} ({mon_n:,} plays)"),
        ("Biggest year", f"{yr} ({yr_n:,} plays)"),
        ("Longest streak", f"{s['longest_streak']} days in a row"),
        ("Peak hour", f"{_fmt_hour(s['peak_hour'])}"),
        ("Top day of week", _WEEKDAYS[s['top_weekday']]),
        ("#1 artist", f"{s['top_artist'][0]} ({s['top_artist'][1]:,} plays · {share:.1f}%)"),
        ("#1 track", f"{s['top_track'][0]} ({s['top_track'][1]:,} plays)"),
        ("#1 album", f"{s['top_album'][0]} ({s['top_album'][1]:,} plays)"),
        ("#1 genre", f"{s['top_genre'][0]} ({s['top_genre'][1]:,} plays)"),
        ("Avg hours/week", f"{s['avg_hours_per_week']}"),
        ("Full-listen rate", f"{s['full_listen_rate']*100:.0f}%"),
        ("Skip rate", f"{s['skip_rate']*100:.0f}%"),
    ]
    return pd.DataFrame(rows, columns=["Stat", "Value"])


def _window_recap_table(df, window):
    """Same Total plays/Hours/Days/Top artist-track-genre recap the old
    Wrapped tab showed as metric tiles for a selected window, as a table."""
    w = _apply_range(df, window)
    if w.empty:
        return None
    top_artist = proc.top_artists(w, 1)
    top_track = proc.top_tracks(w, 1)
    top_genre = proc.top_genres(w, 1)
    rows = [
        ("Total plays", f"{len(w):,}"),
        ("Hours listened", f"{w['minutes_played'].sum()/60:,.0f}"),
        ("Listening days", f"{w['ts_local'].dt.date.nunique():,}"),
        ("Top artist", top_artist.iloc[0]['artist_name'] if len(top_artist) else "—"),
        ("Top track", top_track.iloc[0]['track_name'] if len(top_track) else "—"),
        ("Top genre", top_genre.iloc[0]['genres'] if len(top_genre) else "—"),
    ]
    return pd.DataFrame(rows, columns=["Stat", "Value"])


@st.dialog("✨ Wrapped Story", width="large")
def _story_slides_dialog(df, story_loader):
    """The story-carousel popup triggered by 'Play Wrapped Slides'. A real
    modal (st.dialog), not an inline reveal — the inline toggle this
    replaced required scrolling past two tables to notice anything had
    happened, which read as the button doing nothing."""
    st.caption("A shareable set of story-style slides for one year — use "
               "the arrow keys, tap the sides, or swipe.")
    years = sorted((int(y) for y in df['year'].dropna().unique()), reverse=True)
    if not years:
        st.info("No data yet.")
        return
    year = st.selectbox("Year", years, key="story_year")
    data = story_loader(year)
    if data is None:
        st.info("No plays in that year.")
        return
    html = story.render_story_html(data)
    components.html(html, height=760, scrolling=False)
    st.download_button(
        "⬇️ Download as HTML", data=html,
        file_name=f"wrapped_story_{year}.html", mime="text/html",
        help="A single self-contained file — data baked in, works offline, "
             "shareable outside the app.")


def render_wrapped_story(df, alltime, story_loader):
    """Combined Wrapped Story page: the plain wrapped data (all-time record
    table + a window-selectable recap table), plus a button that pops the
    swipeable story carousel (src/story.py) open in a modal. story_loader
    (year) is a closure over wrapped_story_data_cached from main(), so each
    year picked gets its own cache entry."""
    header_col, button_col = st.columns([4, 1.3], vertical_alignment="center")
    header_col.subheader("✨ Wrapped Story")
    if button_col.button("🎬 Play Wrapped Slides", use_container_width=True):
        _story_slides_dialog(df, story_loader)

    st.markdown("**🏅 All-time**")
    if not alltime:
        st.info("No data yet.")
        return
    st.dataframe(_alltime_stats_table(alltime), width='stretch', hide_index=True)

    # "Last 30 days" stays first in the list (unlike the sidebar filter,
    # which defaults to all-time) but the *default selection* is the
    # current calendar year when it's present, so this opens on "my year
    # so far" rather than just the last month.
    window_options = (["Last 30 days", "Last 7 days", "This month", "All time"] +
                      [str(y) for y in sorted(df['year'].dropna().unique(), reverse=True)])
    current_year = str(datetime.now().year)
    default_idx = window_options.index(current_year) if current_year in window_options else 0
    window = st.selectbox("Recap window", window_options, index=default_idx)
    recap = _window_recap_table(df, window)
    if recap is None:
        st.info("No plays in this window.")
    else:
        st.dataframe(recap, width='stretch', hide_index=True)


def render_patterns(df):
    range_sel, _ = _page_filters(df, with_metric=False)
    view = _apply_range(df, range_sel)
    st.subheader("When do I listen?")
    if view.empty:
        st.info("No plays in this range.")
        return
    grid = proc.patterns_heatmap(view)
    st.plotly_chart(charts.hour_dow_heatmap(grid, "Plays by hour and day of week"),
                    use_container_width=True)

    st.markdown("**Times of week**")
    top_tow = proc.top_times_of_week(view, n=5)
    lines = [
        f"{i}. **{_WEEKDAYS[int(row['day_of_week'])]} {_fmt_hour(int(row['hour']))}** — "
        f"{row['plays']:,} plays ({row['plays'] / len(view) * 100:.1f}% of all)"
        for i, row in enumerate(top_tow.to_dict('records'), start=1)
    ]
    st.markdown("\n".join(lines))

    st.markdown("**Top 5 listening hours**")
    top5 = proc.top_hours(view, n=5)
    lines = [
        f"{i}. **{_fmt_hour(int(row['hour']))}** — {row['plays']:,} plays "
        f"({row['plays'] / len(view) * 100:.1f}% of all)"
        for i, row in enumerate(top5.to_dict('records'), start=1)
    ]
    st.markdown("\n".join(lines))


def render_binges(track_peaks, artist_peaks, warmup_loader):
    """Songs/bands that hit hard for a week (or two), then faded — ranked by
    binge_score = peak hours in any 7-day window, weighted by how much of
    that track/artist's *entire* history with you happened in that one
    window. Operates on the full archive (not date-range-filtered) since
    concentration is only meaningful against the whole history."""
    st.subheader("🔥 Binges")
    st.caption("Ranked by peak hours in any 7-day window, weighted by how "
               "concentrated that listening was — a short-lived spike "
               "outranks an all-time favorite that just had one good week.")
    mode = st.radio("Show", ["Bands", "Songs"], horizontal=True)
    hide_oneoffs = st.checkbox(
        "Hide one-off curiosities (< 2 total hours)", value=True,
        help="Filters out entries with too little lifetime listening for "
             "'faded' to mean anything — e.g. tried it once, moved on.")
    floor = 2.0 if hide_oneoffs else 0.0

    peaks = artist_peaks if mode == "Bands" else track_peaks
    shown = peaks[peaks['total_hours'] >= floor].head(10)
    if shown.empty:
        st.info("Not enough data to find a binge yet.")
        return

    label = (shown['artist_name'] if mode == "Bands"
             else shown['track_name'] + " — " + shown['artist_name'])
    chart_df = shown.assign(label=label)
    st.plotly_chart(charts.ranked_bar(chart_df, 'label', 'peak_hours',
                    f"Top {mode.lower()} binges (peak hours in any 7-day window)"),
                    use_container_width=True)

    table = shown.assign(
        window=[f"{pd.Timestamp(s).date()} → {pd.Timestamp(e).date()}"
                for s, e in zip(shown['peak_start'], shown['peak_end'])],
        concentration_pct=(shown['concentration'] * 100).round(0).astype(int),
        peak_hours=shown['peak_hours'].round(1),
        total_hours=shown['total_hours'].round(1),
    )
    cols = ['track_name', 'artist_name'] if mode == "Songs" else ['artist_name']
    cols += ['peak_hours', 'window', 'plays_in_window', 'concentration_pct', 'total_hours']
    st.dataframe(table[cols].rename(columns={
        'track_name': 'Track', 'artist_name': 'Artist', 'peak_hours': 'Peak hours',
        'window': 'Peak week', 'plays_in_window': 'Plays that week',
        'concentration_pct': 'Concentration %', 'total_hours': 'Lifetime hours',
    }), width='stretch', hide_index=True)

    st.divider()
    render_concert_warmups(warmup_loader)


def render_concert_warmups(warmup_loader):
    """Bands with a 'charge up, then crash' shape: a burst of listening,
    then a sharp, temporary drop right after — often the sound of hyping up
    for a show and coming down from it. Distinct from the Binges table above
    (which just ranks the single most concentrated window) because it
    specifically requires the drop-off afterward. All four knobs below feed
    proc.artist_concert_warmups() directly (via warmup_loader, a closure
    over the cached loader from main()), so every combination is exact, not
    a client-side filter of one fixed computation."""
    st.subheader("🎫 Concert warm-up")
    st.caption("Bands that surged, then dropped off sharply right after — "
               "ranked by spike hours × how steep the drop was. A guess: "
               "this often lines up with a show. Tune the pattern below if "
               "it's not matching what you remember — your settings are "
               "saved automatically.")
    # Sliders default to the last-saved values (data/settings.json) rather
    # than fixed constants, and any change is written straight back below —
    # so the tuning persists across sessions instead of resetting every run.
    saved = proc.load_settings()['concert_warmup']
    c1, c2 = st.columns(2)
    spike_days = c1.slider("Build-up window (days)", 3, 30, saved['spike_days'],
                           key="warmup_spike_days",
                           help="How many days of build-up counts as one "
                                "'show cycle' — the window the spike is "
                                "measured over.")
    min_spike_hours = c2.slider("Minimum hours of listening in that window",
                                0.0, 20.0, saved['min_spike_hours'], step=0.5,
                                key="warmup_min_hours",
                                help="Ignore spikes below this many hours "
                                     "total — filters out one-off blips.")
    c3, c4 = st.columns(2)
    elevation_ratio = c3.slider("Elevated rotation (× your normal rate)",
                                1.0, 10.0, saved['elevation_ratio'], step=0.5,
                                key="warmup_elevation",
                                help="How far above that artist's normal "
                                     "daily rate the spike must be to count "
                                     "as genuinely 'elevated' — without this, "
                                     "an artist you always play a lot would "
                                     "trivially have a 'biggest window ever'.")
    cooldown_days = c4.slider("Drop-off window after the spike (days)",
                              1, 14, saved['cooldown_days'], key="warmup_cooldown_days",
                              help="How soon after the spike to check for "
                                   "the crash — set to 1 for a same-day/"
                                   "next-day drop-off.")

    c5, c6 = st.columns(2)
    top_n = c5.slider("Show top N", 5, 50, saved['top_n'], step=5, key="warmup_top_n",
                      help="How many ranked candidates to display.")
    rank_by_concert_night = c6.checkbox(
        "🌙 Rank by drive-home pattern first", value=saved['rank_by_concert_night'],
        key="warmup_rank_by_night",
        help="Bands with a late-night (10pm-1am) listening cluster during "
             "their spike window — the classic drove-home-from-a-show "
             "pattern, optionally backed up by a same-day 3-6pm pre-show "
             "session — are ranked above those without one, before falling "
             "back to spike hours × drop-off within each group. Doesn't "
             "remove anyone from the list, just reorders it.")

    current = {'spike_days': spike_days, 'min_spike_hours': min_spike_hours,
               'elevation_ratio': elevation_ratio, 'cooldown_days': cooldown_days,
               'top_n': top_n, 'rank_by_concert_night': rank_by_concert_night}
    if current != saved:
        settings = proc.load_settings()
        settings['concert_warmup'] = current
        proc.save_settings(settings)

    warmups = warmup_loader(spike_days=spike_days, cooldown_days=cooldown_days,
                            min_spike_hours=min_spike_hours,
                            elevation_ratio=elevation_ratio)
    if warmups.empty:
        st.info("Not enough data to find this pattern yet — try loosening "
                "the knobs above.")
        return
    if rank_by_concert_night:
        warmups = warmups.sort_values(['has_concert_night', 'warmup_score'],
                                      ascending=[False, False]).reset_index(drop=True)

    false_positives = set(proc.load_warmup_false_positives())
    legit = warmups[~warmups['artist_name'].isin(false_positives)]
    flagged = warmups[warmups['artist_name'].isin(false_positives)]

    shown = legit.head(top_n)
    if shown.empty:
        st.info("Not enough data to find this pattern yet — try loosening "
                "the knobs above, or you've flagged everything below.")
    else:
        table = _warmup_table(shown, spike_days, cooldown_days)
        table['Never seen live'] = False
        edited = st.data_editor(
            table, width='stretch', hide_index=True, key="warmup_editor",
            height=38 * len(table) + 38,
            disabled=[c for c in table.columns if c != 'Never seen live'],
            column_config={'Never seen live': st.column_config.CheckboxColumn(
                'Never seen live', help="Check if you've never actually seen "
                "this band since you started using Spotify — moves it down "
                "to False concert positives instead of ranking it as a "
                "warm-up.")})
        newly_flagged = edited.loc[edited['Never seen live'], 'Band']
        if len(newly_flagged):
            proc.save_warmup_false_positives(false_positives | set(newly_flagged))
            st.rerun()

    st.divider()
    st.markdown("**🙅 False concert positives**")
    st.caption("Bands you've marked as never actually seen live — parked "
               "here instead of ranked as a warm-up. Check **Restore** to "
               "move one back.")
    if flagged.empty:
        st.caption("None yet.")
        return
    fp_table = _warmup_table(flagged, spike_days, cooldown_days)
    fp_table['Restore'] = False
    fp_edited = st.data_editor(
        fp_table, width='stretch', hide_index=True, key="warmup_fp_editor",
        height=38 * len(fp_table) + 38,
        disabled=[c for c in fp_table.columns if c != 'Restore'],
        column_config={'Restore': st.column_config.CheckboxColumn('Restore')})
    to_restore = fp_edited.loc[fp_edited['Restore'], 'Band']
    if len(to_restore):
        proc.save_warmup_false_positives(false_positives - set(to_restore))
        st.rerun()


def _warmup_table(rows, spike_days, cooldown_days):
    """Shared display formatting for both the live Concert warm-up table and
    the False concert positives table below it."""
    table = rows.assign(
        window=[f"{pd.Timestamp(s).date()} → {pd.Timestamp(e).date()}"
                for s, e in zip(rows['spike_start'], rows['spike_end'])],
        spike_hours=rows['spike_hours'].round(1),
        cooldown_hours=rows['cooldown_hours'].round(1),
        drop_pct=(rows['drop_pct'] * 100).round(0).astype(int),
        late_night=rows['late_night_minutes'].round(0).astype(int),
        afternoon=rows['afternoon_minutes'].round(0).astype(int),
    )
    return table[['artist_name', 'spike_hours', 'window', 'cooldown_hours',
                 'drop_pct', 'late_night', 'afternoon']].rename(columns={
        'artist_name': 'Band', 'spike_hours': f'Spike hours ({spike_days}d)',
        'window': 'Spike window',
        'cooldown_hours': f'Hours after (next {cooldown_days}d)',
        'drop_pct': 'Drop %',
        'late_night': '🌙 Late night (min)',
        'afternoon': '☀️ Afternoon (min)',
    }).reset_index(drop=True)


def render_bands(df):
    """Bands tab: single-band deep dive + saved group summaries (full archive)."""
    st.subheader("🎤 Bands")
    mode = st.radio("Mode", ["Single band", "Groups"], horizontal=True,
                    index=1, key="bands_mode")
    if mode == "Single band":
        render_single_band(df)
    else:
        render_groups(df)


def render_single_band(df):
    artists = proc.list_artists(df)  # sorted by plays desc
    if not artists:
        st.info("No artists in the data.")
        return

    st.caption("Quick pick — your top artists:")
    cols = st.columns(5)
    for i, name in enumerate(artists[:10]):
        if cols[i % 5].button(name, key=f"qp_{i}", use_container_width=True):
            st.session_state['band_pick'] = name

    # Selectbox is the source of truth; quick-pick buttons seed its index.
    cur = st.session_state.get('band_pick', artists[0])
    idx = artists.index(cur) if cur in artists else 0
    artist = st.selectbox("Search artist", artists, index=idx)
    st.session_state['band_pick'] = artist

    f = proc.artist_facts(df, artist)
    sub = df[df['artist_name'] == artist]
    years = (f['last_played'] - f['first_played']).days / 365.25

    img_col, metrics_col = st.columns([1, 5], vertical_alignment="center")
    if f['image_url']:
        img_col.image(f['image_url'], width=120)
    with metrics_col:
        c = st.columns(4)
        c[0].metric("Plays", f"{f['plays']:,}")
        c[1].metric("Hours", f"{f['hours']:,.0f}")
        c[2].metric("Rank", f"#{f['rank']} / {f['total_artists']:,}")
        c[3].metric("Peak year", f"{f['peak_year']} ({f['peak_year_plays']:,})")
    st.caption(
        f"In rotation {f['first_played'].date()} → {f['last_played'].date()} "
        f"(~{years:.1f} yrs)  ·  full-listen {f['full_listen_rate']*100:.0f}%, "
        f"skip {f['skip_rate']*100:.0f}%")

    st.plotly_chart(charts.line_by_year(proc.plays_by_year(sub), 'plays',
                    f"{artist} — plays per year"), use_container_width=True)
    col1, col2 = st.columns(2)
    col1.plotly_chart(charts.ranked_bar(proc.top_tracks(sub, 10), 'track_name',
                      'plays', "Top tracks"), use_container_width=True)
    col2.plotly_chart(charts.ranked_bar(proc.top_albums(sub, 10), 'album_name',
                      'plays', "Top albums"), use_container_width=True)
    st.plotly_chart(charts.hour_dow_heatmap(proc.patterns_heatmap(sub),
                    f"{artist} — listening clock"), use_container_width=True)


def render_groups(df):
    groups = proc.load_groups()
    names = sorted(groups)

    st.markdown("**Your groups**")
    if names:
        overview = pd.DataFrame([
            {'Group': name, 'Bands': len(members), 'Members': ', '.join(members)}
            for name, members in ((n, groups[n]) for n in names)
        ])
        st.dataframe(overview, width='stretch', hide_index=True)
    else:
        st.caption("No groups saved yet — create one below.")
    st.divider()

    choice = st.selectbox("Group", ["➕ New group…"] + names)
    is_new = choice == "➕ New group…"
    cur_name = "" if is_new else choice
    cur_members = [] if is_new else groups.get(choice, [])
    artists = proc.list_artists(df)

    # Key widgets by the selected group so switching groups resets the editor,
    # while edits within a group persist across reruns.
    name = st.text_input("Group name", value=cur_name, key=f"gname_{choice}")
    members = st.multiselect(
        "Bands in this group", artists,
        default=[m for m in cur_members if m in artists],
        key=f"gmembers_{choice}",
        help="Type to filter your artists (sorted by play count).")

    c1, c2, _ = st.columns([1, 1, 4])
    if c1.button("💾 Save", disabled=not (name.strip() and members)):
        if cur_name and cur_name != name.strip():
            groups.pop(cur_name, None)  # treat a name change as a rename
        groups[name.strip()] = members
        proc.save_groups(groups)
        st.success(f"Saved '{name.strip()}' ({len(members)} bands).")
        st.rerun()
    if not is_new and c2.button("🗑 Delete"):
        groups.pop(choice, None)
        proc.save_groups(groups)
        st.success(f"Deleted '{choice}'.")
        st.rerun()

    if members:
        st.divider()
        render_group_summary(df, name.strip() or "Group", members)
    else:
        st.info("Add bands above to see a group summary.")


def render_group_summary(df, name, members):
    sub = df[df['artist_name'].isin(members)]
    if sub.empty:
        st.info("No plays found for these bands.")
        return

    total, hours = len(sub), sub['minutes_played'].sum() / 60
    share = total / len(df) * 100
    st.markdown(f"### {name}")
    c = st.columns(4)
    c[0].metric("Bands", len(members))
    c[1].metric("Plays", f"{total:,}")
    c[2].metric("Hours", f"{hours:,.0f}")
    c[3].metric("Share of all plays", f"{share:.1f}%")
    st.caption(f"{sub['ts'].min().date()} → {sub['ts'].max().date()}")

    bd = proc.group_breakdown(df, members)
    show = bd.copy()
    show['First'] = pd.to_datetime(show['first_played'], utc=True).dt.date
    show['Last'] = pd.to_datetime(show['last_played'], utc=True).dt.date
    show = show[['artist_name', 'plays', 'minutes', 'rank', 'First', 'Last']].rename(
        columns={'artist_name': 'Band', 'plays': 'Plays', 'minutes': 'Minutes',
                 'rank': 'Overall rank'})
    st.dataframe(show, hide_index=True, width='stretch')

    st.plotly_chart(charts.line_by_year(proc.plays_by_year(sub), 'plays',
                    f"{name} — plays per year"), use_container_width=True)
    st.plotly_chart(charts.ranked_bar(proc.top_tracks(sub, 15), 'track_name',
                    'plays', f"{name} — top tracks"), use_container_width=True)


def render_explore(df):
    st.subheader("Explore")
    query = st.text_input("Search track / artist / album")
    results = df
    if query:
        q = query.lower()
        mask = (
            results['track_name'].str.lower().str.contains(q, na=False)
            | results['artist_name'].str.lower().str.contains(q, na=False)
            | results['album_name'].str.lower().str.contains(q, na=False)
        )
        results = results[mask]
    cols = ['ts_local', 'track_name', 'artist_name', 'album_name',
            'minutes_played', 'full_listen']
    st.caption(f"{len(results):,} plays")
    st.dataframe(results[cols].sort_values('ts_local', ascending=False).head(1000),
                 width='stretch', hide_index=True)
    st.download_button("Download CSV", results[cols].to_csv(index=False),
                       "plays_filtered.csv", "text/csv")


def render_export(df):
    st.subheader("Export")
    st.download_button("Full play log (CSV)", df.to_csv(index=False),
                       "plays_full.csv", "text/csv")
    annual = proc.plays_by_year(df)
    st.download_button("Annual summary (CSV)", annual.to_csv(index=False),
                       "annual_summary.csv", "text/csv")
    st.dataframe(annual, width='stretch', hide_index=True)


def render_artist_filters(df_all):
    """The exclusions editor — a primary concept, so it's its own Tools & settings page
    (not buried in Settings). Powers the sidebar 'Remove kid streams?' toggle."""
    st.subheader("🚫 Artist filters")
    st.write("Drop plays that weren't really yours — e.g. a shared-account "
             "period when the kids used your profile. These rules drive the "
             "sidebar **Remove kid streams?** toggle on the Analytics pages.")
    removed = len(df_all) - len(proc.apply_exclusions(df_all, proc.load_exclusions()))
    st.caption(f"These filters currently remove **{removed:,}** plays "
               f"of {len(df_all):,}.")
    st.caption("Per row: tick **All years**, or set a **Before** / **After** "
               "bound, or a comma-separated **Only** list. Bounds take a year "
               "(`2019`) or a month (`2019-06`). **Keep %** claims only a share "
               "of that period (e.g. `50` for a 50/50 split with a kid); blank = "
               "drop all. Example — Lorde, *Before* = `2020`, *Keep %* = `50` "
               "keeps half of pre-2020 plays. Add rows with the ＋ at the bottom.")
    editor_df = _exclusions_to_df(proc.load_exclusions())
    edited = st.data_editor(
        editor_df, num_rows="dynamic", width='stretch', key="excl_editor",
        column_config={
            _COL_ARTIST: st.column_config.TextColumn(_COL_ARTIST, required=True),
            _COL_ALL: st.column_config.CheckboxColumn(_COL_ALL, default=False),
            _COL_BEFORE: st.column_config.TextColumn(_COL_BEFORE),
            _COL_AFTER: st.column_config.TextColumn(_COL_AFTER),
            _COL_ONLY: st.column_config.TextColumn(_COL_ONLY),
            _COL_KEEP: st.column_config.NumberColumn(
                _COL_KEEP, min_value=0, max_value=100, step=5, format="%d"),
        })
    if st.button("Save filters"):
        proc.save_exclusions(_df_to_exclusions(edited))
        st.success("Saved. Toggle the sidebar filter or switch pages to apply.")
    with st.expander("Raw JSON"):
        st.json(proc.load_exclusions())


def render_settings(df):
    st.subheader("Settings")
    if config.DEMO_MODE:
        st.info("Demo mode — read-only sample dataset. Live sync is disabled "
                 "on this host; run the app locally with your own Spotify "
                 "export to sync your own history.")
    settings = proc.load_settings()
    last = run_pipeline._read_last_sync()
    built = datetime.fromtimestamp(os.path.getmtime(config.PLAYS_FILE))
    st.write("**Data status**")
    st.write(f"- Plays loaded: {len(df):,}")
    st.write(f"- Date range: {df['ts'].min().date()} → {df['ts'].max().date()}")
    st.write(f"- Archive last built: {built.strftime('%Y-%m-%d %H:%M')} "
             "— from your uploaded Spotify export, plus any syncs since.")
    if not config.DEMO_MODE:
        st.write(f"- Last sync: {last.get('last_sync_at', 'never')} "
                 f"(+{last.get('last_new', 0)} new, "
                 f"{last.get('last_fetched', 0)} fetched)")
        st.write(f"- Sync authorized: {os.path.exists(config.TOKEN_FILE)}")
        last_at = last.get('last_sync_at')
        if last.get('last_fetch_capped'):
            st.warning(
                f"⚠️ The last sync hit the {config.RECENTLY_PLAYED_LIMIT}-play "
                "Spotify API limit — plays older than that batch since your "
                "previous sync may be missing. Sync more often to avoid gaps.")
        elif last_at:
            hrs = (pd.Timestamp.now(tz='UTC') - pd.Timestamp(last_at)).total_seconds() / 3600
            if hrs > 12:
                st.warning(
                    f"⚠️ {hrs:.0f}h since your last sync — heavy listening in "
                    f"that gap could exceed Spotify's {config.RECENTLY_PLAYED_LIMIT}-"
                    "play recently-played window. Sync more often to avoid gaps.")
    st.caption("Use the sidebar **🚫 Artist filters** to choose which artists "
               "to exclude." + ("" if config.DEMO_MODE else
                                 " Use **🔄 Sync now** to fetch recent plays."))

    if not config.DEMO_MODE:
        st.write("**Spotify sync latency**")
        st.caption(
            "How long a play takes to show up in Spotify's own "
            "recently-played API after you actually listened to it — this "
            "is Spotify-side lag, not a delay in this app's sync. Only "
            "plays synced since this chart was added are included.")
        latency_df = run_pipeline.sync_latency_df()
        if latency_df.empty:
            st.caption("Not enough data yet — this fills in as you sync.")
        else:
            median = latency_df['latency_minutes'].median()
            p90 = latency_df['latency_minutes'].quantile(0.9)
            st.caption(f"Median: {median:.0f} min · 90th percentile: {p90:.0f} min "
                       f"· {len(latency_df):,} play(s) measured")
            st.plotly_chart(charts.sync_latency_scatter(latency_df),
                            width='stretch')

    st.write("**Preferences**")
    st.write(f"- Timezone: {settings.get('timezone') or 'system default'}")
    st.write(f"- Full-listen threshold: {settings.get('full_listen_threshold')}")

    if not config.DEMO_MODE:
        with st.expander("📥 Re-import a fresh archive"):
            st.caption(
                "Requested a new export from Spotify since you set this up? "
                "Upload the new zip here to rebuild the dashboard from it — "
                "same one-time flow as the first-run screen, no terminal "
                "needed.")
            _render_import_flow(key_prefix="settings", button_label="Rebuild")


if __name__ == "__main__":
    main()

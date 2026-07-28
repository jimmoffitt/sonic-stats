"""
src/story.py — "Wrapped Story" carousel: a swipeable set of dark, portrait
stat cards (one idea per slide, one big bold number) in the same genre as
Instagram/Strava "year in review" stories, styled for sonic-stats rather
than copying any one brand's exact badge shape or palette.

render_story_html(data) returns one self-contained HTML string — inline
CSS/JS, no external requests — so the same function serves both the
embedded in-app page (via st.components.v1.html) and the standalone
"Download as HTML" export.
"""
import html as _html

_GREEN = '#1DB954'
_GREEN_LIGHT = '#3fe375'
_FLAME_1 = '#ff7a3d'
_FLAME_2 = '#ff3d3d'
_BG = '#0b0c0f'
_CARD_BG = '#111319'


def _esc(s):
    return _html.escape(str(s))


def _slide_cover(data):
    return f"""
    <section class="slide slide-cover active" data-slide="0">
      <div class="badge-ring">
        <div class="badge-inner">
          <div class="badge-icon">🎧</div>
          <div class="badge-line">YEAR IN</div>
          <div class="badge-line badge-line-big">MUSIC</div>
        </div>
      </div>
      <div class="cover-year">{data['year']}</div>
      <div class="cover-tag">{data['total_plays']:,} plays &middot; {data['total_hours']:,} hours.
        Here's your year.</div>
    </section>"""


def _slide_totals(data):
    rows = [
        ("Total Days Active", f"{data['listening_days']:,}"),
        ("Total Hours", f"{data['total_hours']:,}"),
        ("Total Plays", f"{data['total_plays']:,}"),
        ("Artists Played", f"{data['unique_artists']:,}"),
    ]
    rows_html = "".join(
        f'<div class="totals-row"><div class="totals-label">{_esc(label)}</div>'
        f'<div class="totals-value">{value}</div></div>'
        for label, value in rows
    )
    return f"""
    <section class="slide slide-totals" data-slide="1">
      <div class="eyebrow">🎧 {data['year']}</div>
      <div class="eyebrow-title">Your year, by the numbers</div>
      <div class="totals-stack">{rows_html}</div>
    </section>"""


def _slide_days_active(data):
    months = data['monthly']
    peak = max((m['days_active'] for m in months), default=0)
    rows_html = "".join(
        f'<div class="bar-row">'
        f'<span class="bar-label">{m["label"].upper()}</span>'
        f'<div class="bar-track"><div class="bar-fill{" bar-peak" if m["days_active"] == peak and peak > 0 else ""}" '
        f'style="width:{(m["days_active"] / peak * 100) if peak else 0:.1f}%"></div></div>'
        f'<span class="bar-value">{m["days_active"]}</span></div>'
        for m in months
    )
    return f"""
    <section class="slide slide-bars" data-slide="2">
      <div class="eyebrow">DAYS ACTIVE</div>
      <div class="big-number">{data['listening_days']:,}</div>
      <div class="bar-list">{rows_html}</div>
    </section>"""


def _slide_hours(data):
    months = data['monthly']
    hours = [m['hours'] for m in months]
    max_h = max(hours) if any(hours) else 1
    peak_i = hours.index(max(hours)) if any(hours) else 0
    w, h, pad_l, pad_r, pad_t, pad_b = 300, 160, 8, 8, 16, 24
    plot_w, plot_h = w - pad_l - pad_r, h - pad_t - pad_b
    n = len(hours)
    pts = []
    for i, v in enumerate(hours):
        x = pad_l + (plot_w * i / (n - 1) if n > 1 else 0)
        y = pad_t + plot_h - (plot_h * v / max_h if max_h else 0)
        pts.append((x, y))
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area = f"{pad_l:.1f},{pad_t + plot_h:.1f} " + poly + f" {pts[-1][0]:.1f},{pad_t + plot_h:.1f}"
    peak_x, peak_y = pts[peak_i]
    # The peak label is an absolutely-positioned HTML span, not SVG <text> —
    # this chart's viewBox is intentionally non-uniform (preserveAspectRatio
    # ="none", to fill the card width exactly), and Chrome distorts/mirrors
    # SVG text glyphs under non-uniform scaling. Percent-based CSS position
    # sidesteps that entirely.
    peak_x_pct, peak_y_pct = peak_x / w * 100, peak_y / h * 100
    labels_html = "".join(
        f'<span class="line-month{" line-month-peak" if i == peak_i else ""}">{m["label"][0]}</span>'
        for i, m in enumerate(months)
    )
    return f"""
    <section class="slide slide-hours" data-slide="3">
      <div class="eyebrow">HOURS ACTIVE</div>
      <div class="big-number">{data['total_hours']:,}</div>
      <div class="line-chart-wrap">
        <svg class="line-chart" viewBox="0 0 {w} {h}" preserveAspectRatio="none">
          <defs>
            <linearGradient id="areaFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stop-color="{_GREEN}" stop-opacity="0.35"/>
              <stop offset="100%" stop-color="{_GREEN}" stop-opacity="0"/>
            </linearGradient>
          </defs>
          <polygon points="{area}" fill="url(#areaFill)"/>
          <polyline points="{poly}" fill="none" stroke="{_GREEN}" stroke-width="2.5"
                    stroke-linejoin="round" stroke-linecap="round"/>
          <circle cx="{peak_x:.1f}" cy="{peak_y:.1f}" r="4.5" fill="{_GREEN_LIGHT}"/>
        </svg>
        <span class="line-peak-label" style="left:{peak_x_pct:.1f}%;top:{peak_y_pct:.1f}%">
          {hours[peak_i]:.0f}h</span>
      </div>
      <div class="line-months">{labels_html}</div>
    </section>"""


def _slide_streak(data):
    n = data['longest_streak']
    dots_n = min(n, 28)
    dots_html = "".join('<span class="streak-dot"></span>' for _ in range(dots_n))
    more = f'<span class="streak-more">+{n - dots_n}</span>' if n > dots_n else ""
    date_range = (f"{data['streak_start']} → {data['streak_end']}"
                  if data['streak_start'] else "")
    return f"""
    <section class="slide slide-streak" data-slide="4">
      <div class="eyebrow">LONGEST STREAK</div>
      <div class="flame-ring">
        <div class="flame-inner">
          <div class="flame-icon">🔥</div>
          <div class="flame-number">{n}</div>
          <div class="flame-unit">days</div>
        </div>
      </div>
      <div class="streak-range">{_esc(date_range)}</div>
      <div class="streak-dots">{dots_html}{more}</div>
    </section>"""


def _slide_top_artists(data):
    artists = data['top_artists']
    max_plays = max((a['plays'] for a in artists), default=1)
    medals = {1: '🥇', 2: '🥈', 3: '🥉'}
    rows_html = "".join(
        f'<div class="rank-row">'
        f'<div class="rank-badge">{medals.get(a["rank"], a["rank"])}</div>'
        f'<div class="rank-body">'
        f'<div class="rank-name">{_esc(a["artist_name"])}</div>'
        f'<div class="rank-bar-track"><div class="rank-bar-fill" '
        f'style="width:{(a["plays"] / max_plays * 100):.1f}%"></div></div>'
        f'</div>'
        f'<div class="rank-value">{a["plays"]:,}</div>'
        f'</div>'
        for a in artists
    )
    return f"""
    <section class="slide slide-artists" data-slide="5">
      <div class="eyebrow">TOP ARTISTS</div>
      <div class="rank-list">{rows_html}</div>
    </section>"""


_SLIDE_BUILDERS = [_slide_cover, _slide_totals, _slide_days_active,
                   _slide_hours, _slide_streak, _slide_top_artists]

_CSS = f"""
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; padding: 0; background: {_BG}; }}
body {{
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
  color: #f2f2f2;
  display: flex; align-items: center; justify-content: center;
  min-height: 100vh; padding: 24px 0;
}}
.story-shell {{ display: flex; flex-direction: column; align-items: center; gap: 12px; }}
.story-card {{
  position: relative;
  width: 360px; height: 640px;
  background: linear-gradient(160deg, {_CARD_BG} 0%, #0a0a0d 100%);
  border-radius: 20px; overflow: hidden;
  box-shadow: 0 20px 60px rgba(0,0,0,0.5);
  border: 1px solid rgba(255,255,255,0.06);
}}
.progress-row {{
  position: absolute; top: 12px; left: 12px; right: 12px;
  display: flex; gap: 4px; z-index: 5;
}}
.progress-seg {{
  flex: 1; height: 3px; border-radius: 2px; background: rgba(255,255,255,0.18);
  overflow: hidden;
}}
.progress-seg::after {{
  content: ''; display: block; height: 100%; width: 0; background: #fff;
  transition: width .2s ease;
}}
.progress-seg.done::after {{ width: 100%; }}
.slides {{ position: relative; width: 100%; height: 100%; }}
.slide {{
  position: absolute; inset: 0;
  display: none; flex-direction: column; align-items: center; justify-content: center;
  padding: 56px 28px 40px; text-align: center;
}}
.slide.active {{ display: flex; }}
.eyebrow {{
  font-size: 13px; letter-spacing: 2px; color: rgba(255,255,255,0.55);
  font-weight: 600; margin-bottom: 6px;
}}
.eyebrow-title {{ font-size: 20px; font-weight: 700; margin-bottom: 28px; }}
.big-number {{ font-size: 64px; font-weight: 800; line-height: 1; margin-bottom: 20px; }}

/* Cover */
.badge-ring {{
  width: 150px; height: 150px; border-radius: 50%;
  background: conic-gradient({_GREEN} 0deg, {_GREEN_LIGHT} 120deg, {_GREEN} 240deg, {_GREEN_LIGHT} 360deg);
  display: flex; align-items: center; justify-content: center;
  margin-bottom: 24px;
  box-shadow: 0 0 40px rgba(29,185,84,0.45);
}}
.badge-inner {{
  width: 122px; height: 122px; border-radius: 50%; background: {_BG};
  display: flex; flex-direction: column; align-items: center; justify-content: center;
}}
.badge-icon {{ font-size: 26px; margin-bottom: 2px; }}
.badge-line {{ font-size: 12px; letter-spacing: 1.5px; font-weight: 700; color: #ddd; }}
.badge-line-big {{ font-size: 15px; color: #fff; }}
.cover-year {{ font-size: 44px; font-weight: 800; margin-bottom: 10px; }}
.cover-tag {{ font-size: 14px; color: rgba(255,255,255,0.7); max-width: 260px; }}

/* Totals */
.totals-stack {{ width: 100%; display: flex; flex-direction: column; gap: 18px; }}
.totals-row {{ text-align: left; }}
.totals-label {{ font-size: 12px; letter-spacing: 1px; color: rgba(255,255,255,0.5); margin-bottom: 2px; }}
.totals-value {{ font-size: 32px; font-weight: 800; }}

/* Bars */
.bar-list {{ width: 100%; display: flex; flex-direction: column; gap: 8px; }}
.bar-row {{ display: flex; align-items: center; gap: 8px; }}
.bar-label {{ width: 34px; font-size: 11px; color: rgba(255,255,255,0.55); font-weight: 600; }}
.bar-track {{ flex: 1; height: 16px; border-radius: 8px; background: rgba(255,255,255,0.08); overflow: hidden; }}
.bar-fill {{ height: 100%; border-radius: 8px; background: {_GREEN}; }}
.bar-fill.bar-peak {{ background: {_GREEN_LIGHT}; box-shadow: 0 0 10px rgba(63,227,117,0.6); }}
.bar-value {{ width: 26px; text-align: right; font-size: 12px; font-weight: 700; }}

/* Hours line chart */
.line-chart-wrap {{ position: relative; width: 100%; height: 160px; }}
.line-chart {{ width: 100%; height: 100%; display: block; }}
.line-peak-label {{
  position: absolute; transform: translate(-50%, -22px);
  color: #fff; font-size: 11px; font-weight: 700; white-space: nowrap;
}}
.line-months {{ width: 100%; display: flex; justify-content: space-between; padding: 0 8px; }}
.line-month {{ font-size: 10px; color: rgba(255,255,255,0.4); font-weight: 600; }}
.line-month-peak {{ color: {_GREEN_LIGHT}; }}

/* Streak */
.flame-ring {{
  width: 150px; height: 150px; border-radius: 50%;
  background: conic-gradient({_FLAME_1} 0deg, {_FLAME_2} 180deg, {_FLAME_1} 360deg);
  display: flex; align-items: center; justify-content: center; margin-bottom: 18px;
  box-shadow: 0 0 40px rgba(255,61,61,0.4);
}}
.flame-inner {{
  width: 122px; height: 122px; border-radius: 50%; background: {_BG};
  display: flex; flex-direction: column; align-items: center; justify-content: center;
}}
.flame-icon {{ font-size: 22px; }}
.flame-number {{ font-size: 34px; font-weight: 800; line-height: 1.1; }}
.flame-unit {{ font-size: 11px; letter-spacing: 1px; color: rgba(255,255,255,0.55); }}
.streak-range {{ font-size: 13px; color: rgba(255,255,255,0.6); margin-bottom: 18px; }}
.streak-dots {{ display: flex; flex-wrap: wrap; gap: 5px; justify-content: center; max-width: 260px; }}
.streak-dot {{ width: 9px; height: 9px; border-radius: 50%; background: {_FLAME_1}; }}
.streak-more {{ font-size: 11px; color: rgba(255,255,255,0.5); align-self: center; }}

/* Top artists */
.rank-list {{ width: 100%; display: flex; flex-direction: column; gap: 14px; }}
.rank-row {{ display: flex; align-items: center; gap: 10px; }}
.rank-badge {{ width: 26px; font-size: 16px; text-align: center; }}
.rank-body {{ flex: 1; text-align: left; }}
.rank-name {{ font-size: 14px; font-weight: 700; margin-bottom: 4px; }}
.rank-bar-track {{ height: 6px; border-radius: 3px; background: rgba(255,255,255,0.08); overflow: hidden; }}
.rank-bar-fill {{ height: 100%; border-radius: 3px; background: {_GREEN}; }}
.rank-value {{ font-size: 13px; font-weight: 700; color: rgba(255,255,255,0.8); min-width: 40px; text-align: right; }}

/* Nav */
.nav-zone {{
  position: absolute; top: 0; bottom: 0; width: 50%;
  background: transparent; border: none; cursor: pointer; z-index: 4;
}}
.nav-prev {{ left: 0; }}
.nav-next {{ right: 0; }}
.hint {{ font-size: 12px; color: rgba(255,255,255,0.4); }}
@media (max-width: 400px) {{
  .story-card {{ width: 92vw; height: 164vw; max-height: 88vh; }}
}}
"""

_JS = """
(function(){
  var root = document.currentScript.closest('.story-shell');
  var slides = root.querySelectorAll('.slide');
  var segs = root.querySelectorAll('.progress-seg');
  var i = 0;
  function show(n){
    i = Math.max(0, Math.min(slides.length - 1, n));
    slides.forEach(function(s, idx){ s.classList.toggle('active', idx === i); });
    segs.forEach(function(s, idx){ s.classList.toggle('done', idx <= i); });
  }
  root.querySelector('.nav-prev').addEventListener('click', function(){ show(i - 1); });
  root.querySelector('.nav-next').addEventListener('click', function(){ show(i + 1); });
  document.addEventListener('keydown', function(e){
    if (e.key === 'ArrowRight') show(i + 1);
    if (e.key === 'ArrowLeft') show(i - 1);
  });
  var x0 = null;
  var card = root.querySelector('.story-card');
  card.addEventListener('touchstart', function(e){ x0 = e.touches[0].clientX; });
  card.addEventListener('touchend', function(e){
    if (x0 === null) return;
    var dx = e.changedTouches[0].clientX - x0;
    if (dx > 40) show(i - 1);
    else if (dx < -40) show(i + 1);
    x0 = null;
  });
  // #slide=N deep-links straight to one card, so a single slide is shareable.
  var m = /slide=(\\d+)/.exec(location.hash);
  show(m ? parseInt(m[1], 10) : 0);
})();
"""


def render_story_html(data):
    """Builds the full self-contained HTML document for one year's Wrapped
    Story. `data` is the dict from process_data.wrapped_story_data()."""
    slides_html = "".join(builder(data) for builder in _SLIDE_BUILDERS)
    progress_html = "".join(
        '<div class="progress-seg"></div>' for _ in _SLIDE_BUILDERS)
    title = f"Your {data['year']} in Music"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title)}</title>
<style>{_CSS}</style>
</head>
<body>
  <div class="story-shell">
    <div class="story-card">
      <div class="progress-row">{progress_html}</div>
      <div class="slides">{slides_html}</div>
      <button class="nav-zone nav-prev" aria-label="Previous slide"></button>
      <button class="nav-zone nav-next" aria-label="Next slide"></button>
    </div>
    <div class="hint">&larr; &rarr; or tap the sides to navigate</div>
    <script>{_JS}</script>
  </div>
</body>
</html>"""

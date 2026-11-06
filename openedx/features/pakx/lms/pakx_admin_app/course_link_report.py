"""
Public, read-only report page for a client's tracked course links. Guarded only by the client's
secret token in the URL; shows counts, never user data.
"""
import csv
import uuid
from datetime import datetime
from io import StringIO

from django.http import Http404, HttpResponse
from django.utils.html import escape
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from openedx.features.pakx.lms.overrides.waffle import course_link_tracking_enabled

from .course_links import DEFAULT_REPORT_DAYS, client_stats, parse_report_range
from .models import CourseLinkClient

RANGE_CHOICES = (7, 30, 90, 365)
CHART_WIDTH, CHART_HEIGHT = 900, 280
CHART_PAD = {'left': 44, 'right': 12, 'top': 14, 'bottom': 30}
MAX_X_LABELS = 8
STYLE = """
:root{--bg:#f4f6f9;--card:#fff;--text:#1b1f24;--muted:#5b6672;--line:#e3e8ee;--head:#f1f4f8;--link:#0b5cad;
--hits:#2f7de1;--uniq:#f2994a;--pill:#e8edf3;--on:#1b1f24;--on-text:#fff}
@media (prefers-color-scheme:dark){:root{--bg:#14171b;--card:#1d2227;--text:#e7eaee;--muted:#9aa5b1;--line:#2d353d;
--head:#262d34;--link:#6cb2ff;--pill:#262d34;--on:#e7eaee;--on-text:#14171b}}
*{box-sizing:border-box}
body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:0;padding:32px 16px 48px;background:var(--bg);
color:var(--text);line-height:1.45}
.wrap{max-width:1000px;margin:0 auto}
header{display:flex;flex-wrap:wrap;align-items:flex-end;justify-content:space-between;gap:16px;margin-bottom:24px}
h1{font-size:26px;margin:0 0 4px}h2{font-size:16px;margin:0 0 14px}
.muted{color:var(--muted);font-size:13px}
.toolbar{display:flex;flex-wrap:wrap;align-items:center;gap:8px}
.pills{display:flex;flex-wrap:wrap;gap:6px}
.pills a,.btn{display:inline-block;padding:7px 14px;border-radius:999px;background:var(--pill);color:var(--text);
font-size:13px;font-weight:600;text-decoration:none;white-space:nowrap}
.pills a.on{background:var(--on);color:var(--on-text)}
.btn{background:transparent;border:1px solid var(--line);color:var(--link)}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:16px;margin-bottom:24px}
.card,.panel{background:var(--card);border:1px solid var(--line);border-radius:10px}
.card{padding:18px 20px}
.card b{display:block;font-size:30px;line-height:1.15}
.card span{color:var(--muted);font-size:13px}
.panel{padding:20px;margin-bottom:24px}
.btn{cursor:pointer;font-family:inherit}
.chart-toolbar{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:12px;margin-bottom:10px}
.chart-toolbar select{max-width:100%;min-width:200px;padding:7px 10px;border-radius:8px;border:1px solid var(--line);
background:var(--card);color:var(--text);font-size:13px;font-family:inherit}
.legend{display:flex;flex-wrap:wrap;gap:8px}
.legend-item{display:inline-flex;align-items:center;gap:8px;padding:7px 14px;border-radius:999px;
border:1px solid var(--line);
background:var(--card);color:var(--text);font-size:13px;font-weight:600;font-family:inherit;cursor:pointer}
.legend-item i{display:inline-block;width:12px;height:12px;border-radius:3px}.legend-item strong{color:var(--muted)}
.legend-item[aria-pressed=false]{opacity:.45;text-decoration:line-through}
.range{display:flex;flex-wrap:wrap;align-items:center;gap:6px}
.range input{padding:6px 8px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--text);
font-size:13px;font-family:inherit}
.delta{display:block;margin-top:4px;font-size:12px;font-weight:600;color:var(--muted)}
.delta.up{color:#1e7e34}.delta.down{color:#c0392b}
.notice{padding:10px 14px;margin-bottom:16px;border-radius:8px;background:rgba(240,173,78,.18);color:#8a5a00;
font-size:14px;font-weight:600}
@media (prefers-color-scheme:dark){.notice{color:#f0c36d}.delta.up{color:#5cd68a}.delta.down{color:#ff8a7a}}
tr.pick{cursor:pointer}tr.pick:hover td,tr.pick:focus td{background:var(--head)}
tr.pick.sel td{background:var(--head);font-weight:600}
th.sortable{cursor:pointer;user-select:none}
th.sortable.asc:after{content:' \\25B2'}th.sortable.desc:after{content:' \\25BC'}
.hide-hits .bar-hits,.hide-unique_visitors .bar-unique_visitors{display:none}
svg{display:block;width:100%;min-width:560px;height:auto}
svg[hidden]{display:none}
.chart-scroll{overflow-x:auto}
svg text{font-size:11px;fill:var(--muted)}svg .grid{stroke:var(--line);stroke-width:1}
.empty{padding:40px 0;text-align:center;color:var(--muted)}
.table-wrap{overflow-x:auto;margin:0 -20px;padding:0 20px}
table{width:100%;border-collapse:collapse}
th,td{text-align:left;padding:12px 14px;border-bottom:1px solid var(--line);font-size:14px;vertical-align:top}
th{background:var(--head);font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}
tr:last-child td{border-bottom:0}
td.url{word-break:break-all;min-width:220px}
.copy{margin-left:6px;padding:2px 10px;border-radius:999px;border:1px solid var(--line);background:transparent;
color:var(--link);font-size:12px;font-family:inherit;cursor:pointer}
td.n,th.n{text-align:right;white-space:nowrap}
a{color:var(--link)}
.tag{display:inline-block;padding:2px 10px;border-radius:999px;background:var(--pill);font-size:11px;font-weight:700;
text-transform:uppercase;letter-spacing:.03em}
@media (max-width:600px){body{padding:20px 12px 36px}h1{font-size:22px}.panel{padding:16px}
.cards{grid-template-columns:repeat(3,1fr);gap:8px}.card{padding:12px}.card b{font-size:22px}.card span{font-size:12px}
.table-wrap{margin:0 -16px;padding:0 16px}}
"""


def _day_label(iso_date):
    day = datetime.strptime(iso_date, '%Y-%m-%d').date()
    return '{} {}'.format(day.strftime('%b'), day.day)


def _chart(series, link_id='all', hidden=False):
    """Inline SVG: daily hits and unique visitors as paired bars, with grid lines and date labels."""
    left, right = CHART_PAD['left'], CHART_PAD['right']
    top_pad, bottom = CHART_PAD['top'], CHART_PAD['bottom']
    plot_w, plot_h = CHART_WIDTH - left - right, CHART_HEIGHT - top_pad - bottom
    peak = max([1] + [max(day['hits'], day['unique_visitors']) for day in series])
    tick = -(-peak // 4)  # four equal intervals, whole numbers
    scale_max = tick * 4
    step = plot_w / max(len(series), 1)
    bar = max(min(step * 0.34, 16), 1)
    parts = ['<svg class="chart" data-link="{}" viewBox="0 0 {} {}" role="img" '
             'aria-label="Daily hits and unique visitors"{}>'.format(
                 link_id, CHART_WIDTH, CHART_HEIGHT, ' hidden' if hidden else '')]
    for i in range(5):
        y = top_pad + plot_h - plot_h * i / 4
        parts.append('<line class="grid" x1="{}" x2="{}" y1="{:.1f}" y2="{:.1f}"/>'.format(
            left, CHART_WIDTH - right, y, y
        ))
        parts.append('<text x="{}" y="{:.1f}" text-anchor="end">{}</text>'.format(left - 8, y + 4, i * tick))
    for i, day in enumerate(series):
        centre = left + i * step + step / 2
        for offset, key, colour in ((-bar - 0.5, 'hits', 'var(--hits)'), (0.5, 'unique_visitors', 'var(--uniq)')):
            value = day[key]
            if not value:
                continue  # an empty bar draws nothing; skipping keeps the page small for long periods
            bar_height = plot_h * value / scale_max
            parts.append(
                '<rect class="bar-{}" x="{:.1f}" y="{:.1f}" width="{:.1f}" height="{:.1f}" rx="1.5" fill="{}">'
                '<title>{}: {} {}</title></rect>'.format(
                    key, centre + offset, top_pad + plot_h - bar_height, bar, bar_height, colour,
                    _day_label(day['date']), value, key.replace('_', ' ')
                )
            )
    label_every = -(-len(series) // MAX_X_LABELS) if series else 1
    for i in range(0, len(series), label_every):
        parts.append('<text x="{:.1f}" y="{}" text-anchor="middle">{}</text>'.format(
            left + i * step + step / 2, CHART_HEIGHT - 8, _day_label(series[i]['date'])
        ))
    parts.append('</svg>')
    return ''.join(parts)


def _chart_block(stats):
    """Link filter, clickable legend and one chart per selection; script.js-free switching is in SCRIPT."""
    options = ['<option value="all" data-hits="{}" data-uniques="{}">All links</option>'.format(
        stats['hits'], stats['unique_visitors']
    )]
    charts = [_chart(stats['series'])]
    for link in stats['links']:
        options.append('<option value="{}" data-hits="{}" data-uniques="{}">{}</option>'.format(
            link['id'], link['hits'], link['unique_visitors'], escape(link['label'])
        ))
        charts.append(_chart(link['series'], link['id'], hidden=True))
    return (
        '<div class="chart-toolbar"><select id="chart-link" aria-label="Show chart for">{}</select>'
        '<div class="legend"><button type="button" class="legend-item" data-series="hits" aria-pressed="true">'
        '<i style="background:var(--hits)"></i>Hits <strong id="total-hits"></strong></button>'
        '<button type="button" class="legend-item" data-series="unique_visitors" aria-pressed="true">'
        '<i style="background:var(--uniq)"></i>Unique visitors <strong id="total-uniques"></strong></button></div>'
        '</div><div class="chart-scroll" id="charts">{}</div>'
    ).format(''.join(options), ''.join(charts))


SCRIPT = """
(function(){
var sel=document.getElementById('chart-link');  // absent when the period has no visits: only the chart needs it
var box=document.getElementById('charts');
function show(){
  var o=sel.options[sel.selectedIndex];
  document.getElementById('total-hits').textContent=o.getAttribute('data-hits');
  document.getElementById('total-uniques').textContent=o.getAttribute('data-uniques');
  Array.prototype.forEach.call(box.querySelectorAll('svg'),function(s){
    s.toggleAttribute('hidden',s.getAttribute('data-link')!==sel.value);
  });
}
if(sel){
  sel.addEventListener('change',show);
  Array.prototype.forEach.call(document.querySelectorAll('.legend-item'),function(b){
    b.addEventListener('click',function(){
      var on=b.getAttribute('aria-pressed')==='true';
      if(on&&document.querySelectorAll('.legend-item[aria-pressed=true]').length<2) return;
      b.setAttribute('aria-pressed',on?'false':'true');
      box.classList.toggle('hide-'+b.getAttribute('data-series'),on);
    });
  });
  show();
}
var table=document.querySelector('table'); if(!table) return;
Array.prototype.forEach.call(table.querySelectorAll('.copy'),function(b){b.addEventListener('click',function(){
  var done=function(){b.textContent='Copied';setTimeout(function(){b.textContent='Copy';},1500);};
  if(navigator.clipboard){navigator.clipboard.writeText(b.getAttribute('data-url')).then(done);}
  else{window.prompt('Copy this link',b.getAttribute('data-url'));}
});});
function keys(el){el.setAttribute('tabindex','0');el.addEventListener('keydown',function(e){
  if(e.key==='Enter'||e.key===' '){e.preventDefault();el.click();}});}
Array.prototype.forEach.call(table.querySelectorAll('th.sortable'),function(th){
  th.setAttribute('role','button');keys(th);
  th.addEventListener('click',function(){
    var i=Array.prototype.indexOf.call(th.parentNode.children,th), dir=th.classList.contains('desc')?1:-1;
    Array.prototype.forEach.call(th.parentNode.children,function(c){c.classList.remove('asc','desc');});
    th.classList.add(dir===1?'asc':'desc');
    var body=table.tBodies[0], rows=Array.prototype.slice.call(body.rows);
    rows.sort(function(a,b){return dir*(Number(a.cells[i].textContent)-Number(b.cells[i].textContent));});
    rows.forEach(function(r){body.appendChild(r);});
  });
});
if(!sel) return;
function mark(){Array.prototype.forEach.call(table.querySelectorAll('tr.pick'),function(r){
  r.classList.toggle('sel',r.getAttribute('data-link')===sel.value);});}
Array.prototype.forEach.call(table.querySelectorAll('tr.pick'),function(r){
  keys(r);
  r.addEventListener('click',function(e){
    if(e.target.closest('a,button')) return;
    sel.value=r.getAttribute('data-link'); sel.dispatchEvent(new Event('change'));
  });
});
sel.addEventListener('change',mark); mark();
})();
"""


def _delta(current, previous):
    """Change against the previous period as a small badge; empty when there is nothing to compare."""
    if not previous:
        return ''
    change = round((current - previous) * 100.0 / previous)
    if change == 0:
        return '<span class="delta">no change vs previous period</span>'
    return '<span class="delta {}">{} {}% vs previous period</span>'.format(
        'up' if change > 0 else 'down', '&#9650;' if change > 0 else '&#9660;', abs(change)
    )


def _html(stats, days_param, query):
    previous = stats['previous'] or {}
    pills = ''.join(
        '<a href="?days={0}"{1}>{0} days</a>'.format(days, ' class="on"' if days == days_param else '')
        for days in RANGE_CHOICES
    )
    rows = ''.join(
        '<tr class="pick" data-link="{id}"><td><span class="tag">{kind}</span></td>'
        '<td class="url"><span>{url}</span> <button type="button" class="copy" data-url="{url}">Copy</button></td>'
        '<td title="{course_id}">{course}</td>'
        '<td class="n">{hits}</td><td class="n">{uniques}</td></tr>'.format(
            id=link['id'], kind='Alias' if link['kind'] == 'alias' else 'UTM',
            url=escape(link['url']), course=escape(link['course_name'] or link['course_id']),
            course_id=escape(link['course_id']),
            hits=link['hits'], uniques=link['unique_visitors'],
        )
        for link in stats['links']
    ) or '<tr><td colspan="5" class="empty">No links yet.</td></tr>'
    chart = _chart_block(stats) if stats['hits'] else '<div class="empty">No visits in this period.</div>'
    notice = '' if course_link_tracking_enabled() else (
        '<div class="notice">Visit tracking is currently paused, so new visits are not being counted.</div>'
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{name} - course link report</title>'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="robots" content="noindex,nofollow"><style>{style}</style></head><body><div class="wrap">'
        '<header><div><h1>{name}</h1><div class="muted">Link visits, {start} to {end} (Pakistan time) '
        '&middot; updated {updated}</div></div>'
        '<div class="toolbar"><div class="pills">{pills}</div>'
        '<form class="range" method="get"><input type="date" name="from" value="{start}" max="{end}" '
        'aria-label="From"><input type="date" name="to" value="{end}" aria-label="To">'
        '<button type="submit" class="btn">Apply</button></form>'
        '<button type="button" class="btn" onclick="location.reload()">Refresh</button>'
        '<a class="btn" href="?{query}&amp;format=csv">Download CSV</a></div></header>{notice}'
        '<div class="cards"><div class="card"><b>{hits}</b><span>Total hits</span>{hits_delta}</div>'
        '<div class="card"><b>{uniques}</b><span>Unique visitors</span>{uniques_delta}</div>'
        '<div class="card"><b>{links}</b><span>Links</span></div></div>'
        '<section class="panel"><h2>Visits per day</h2>{chart}</section>'
        '<section class="panel"><h2>Links</h2><div class="table-wrap"><table><thead>'
        '<tr><th>Type</th><th>Link</th><th>Course</th><th class="n sortable">Hits</th>'
        '<th class="n sortable">Unique</th></tr></thead><tbody>{rows}</tbody></table></div></section>'
        '<div class="muted">A hit is a page load. A unique visitor is a distinct browser; a browser that comes '
        'back on another day is still counted once in the total.{compare_note}</div></div>'
        '<script>{script}</script></body></html>'
    ).format(
        name=escape(stats['client']['name']), style=STYLE, start=stats['from'], end=stats['to'], hits=stats['hits'],
        uniques=stats['unique_visitors'], links=len(stats['links']), pills=pills, query=query, chart=chart,
        rows=rows, script=SCRIPT, notice=notice, updated=stats['generated_at'][11:16],
        hits_delta=_delta(stats['hits'], previous.get('hits')),
        uniques_delta=_delta(stats['unique_visitors'], previous.get('unique_visitors')),
        compare_note=(
            ' Changes compare with the previous period of the same length ({} to {}).'.format(
                previous['from'], previous['to']
            ) if previous else ''
        ),
    )


def _csv(stats):
    out = StringIO()
    writer = csv.writer(out)
    writer.writerow([
        'date', 'link_type', 'link_value', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content', 'course',
        'hits', 'unique_visitors',
    ])
    for link in stats['links']:
        for day in link['series']:
            writer.writerow([
                day['date'], link['kind'], link['value'], link['utm_medium'], link['utm_campaign'],
                link['utm_term'], link['utm_content'], link['course_id'], day['hits'], day['unique_visitors'],
            ])
    return out.getvalue()


@require_GET
@never_cache
def course_link_report(request, token):
    """Read-only visit report for the client owning this token (404 if unknown or switched off)."""
    try:
        token = uuid.UUID(token)
    except ValueError:
        raise Http404
    client = CourseLinkClient.objects.filter(report_token=token, is_report_enabled=True).first()
    if client is None:
        raise Http404
    date_from, date_to, days = request.GET.get('from'), request.GET.get('to'), request.GET.get('days')
    try:
        start, end = parse_report_range(date_from, date_to, days)
        days_param = None if (date_from or date_to) and not days else int(days or DEFAULT_REPORT_DAYS)
    except ValueError:
        return HttpResponse('Invalid range.', status=400)
    query = 'days={}'.format(days_param) if days_param else 'from={}&amp;to={}'.format(start, end)
    is_csv = request.GET.get('format') == 'csv'
    stats = client_stats(client, start, end, with_previous=not is_csv)
    if is_csv:
        response = HttpResponse(_csv(stats), content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="course-link-report-{}-{}.csv"'.format(start, end)
    else:
        response = HttpResponse(_html(stats, days_param, query))
    response['X-Robots-Tag'] = 'noindex, nofollow'
    response['Referrer-Policy'] = 'no-referrer'
    return response

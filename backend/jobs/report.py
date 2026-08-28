"""A single-file offline Jobs report with no external dependencies."""
import html
import json
from backend.jobs import analytics


def render(db) -> str:
    rows = [dict(row) for row in db.execute(
        """SELECT a.status, a.applied_at, a.created_at, j.company, j.title,
                  j.source, j.location, j.url
           FROM applications a JOIN jobs j ON j.id=a.job_id
           ORDER BY COALESCE(a.applied_at,a.created_at) DESC"""
    ).fetchall()]
    funnel = analytics.funnel_metrics(db)
    counts = {row['status']: row['count'] for row in db.execute(
        'SELECT status, COUNT(*) count FROM applications GROUP BY status'
    ).fetchall()}
    stages = ['draft', 'ready', 'submitted', 'acknowledged', 'interview', 'offer',
              'rejected', 'withdrawn', 'ghosted']
    max_count = max(counts.values(), default=1)
    bars = ''.join(
        f'<g transform="translate(0,{i*28})"><text x="0" y="16">{html.escape(stage)}</text>'
        f'<rect x="100" y="3" height="18" width="{280*counts.get(stage,0)/max_count:.1f}" />'
        f'<text x="390" y="16">{counts.get(stage,0)}</text></g>'
        for i, stage in enumerate(stages)
    )
    data = json.dumps(rows).replace('</', '<\\/')
    return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Lunaschal Jobs report</title><style>
body{{font:14px system-ui,sans-serif;margin:0;background:#101114;color:#eee}}main{{max-width:1100px;margin:auto;padding:24px}}
h1{{margin-top:0}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}}.card,section{{background:#191b20;border:1px solid #30333b;border-radius:10px;padding:14px}}.big{{font-size:28px;font-weight:700}}section{{margin-top:16px;overflow:auto}}input,select{{background:#101114;color:#eee;border:1px solid #444;border-radius:6px;padding:9px;margin:0 8px 10px 0}}table{{width:100%;border-collapse:collapse}}th,td{{padding:8px;text-align:left;border-bottom:1px solid #30333b}}a{{color:#83b9ff}}svg{{min-width:440px}}rect{{fill:#568bd8}}svg text{{fill:#ddd;font-size:12px}}
</style></head><body><main><h1>Jobs report</h1><div class="cards">
<div class="card"><div class="big">{len(rows)}</div>Applications</div>
<div class="card"><div class="big">{funnel['sent']}</div>Sent</div>
<div class="card"><div class="big">{round(funnel['responseRate']*100)}%</div>Response rate</div>
<div class="card"><div class="big">{funnel['averageResponseDays'] if funnel['averageResponseDays'] is not None else '—'}</div>Average response days</div></div>
<section><h2>Funnel</h2><svg viewBox="0 0 440 {len(stages)*28}" role="img" aria-label="Applications by status">{bars}</svg></section>
<section><h2>Applications</h2><input id="q" placeholder="Search company or title"><select id="status"><option value="">All statuses</option>{''.join(f'<option>{s}</option>' for s in stages)}</select><select id="source"><option value="">All sources</option></select>
<table><thead><tr><th>Company</th><th>Role</th><th>Status</th><th>Source</th><th>Location</th><th>Applied</th></tr></thead><tbody id="rows"></tbody></table></section>
<script>const DATA={data};const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
const source=document.querySelector('#source');[...new Set(DATA.map(x=>x.source))].sort().forEach(x=>source.insertAdjacentHTML('beforeend',`<option>${{esc(x)}}</option>`));
function draw(){{const q=document.querySelector('#q').value.toLowerCase(),s=document.querySelector('#status').value,src=source.value;document.querySelector('#rows').innerHTML=DATA.filter(x=>(!q||`${{x.company}} ${{x.title}}`.toLowerCase().includes(q))&&(!s||x.status===s)&&(!src||x.source===src)).map(x=>`<tr><td>${{x.url?`<a href="${{esc(x.url)}}">${{esc(x.company)}}</a>`:esc(x.company)}}</td><td>${{esc(x.title)}}</td><td>${{esc(x.status)}}</td><td>${{esc(x.source)}}</td><td>${{esc(x.location)}}</td><td>${{x.applied_at?new Date(x.applied_at*1000).toLocaleDateString():'—'}}</td></tr>`).join('')}}
document.querySelectorAll('input,select').forEach(x=>x.addEventListener('input',draw));draw();</script></main></body></html>'''

"""Event workflow, ownership checks and atomic room reservations."""
import calendar
import json
from datetime import datetime, timedelta, timezone
from flask import Blueprint, g, request, render_template, redirect, url_for, flash, abort
from .auth import login_required
from .db import get_db

bp = Blueprint('events', __name__)
STATUS = {'pending':'Pending Review','reviewed':'Awaiting Approval','approved':'Approved','changes':'Changes Requested','rejected':'Rejected'}
VENUES = ['Innovation Room','Main Auditorium','Computer Lab 1','College Atrium']
FIELDS = ['title','organizer','kind','description','date','start','end','venue','attendees']
RIYADH = timezone(timedelta(hours=3))
MONTHS = ['January','February','March','April','May','June','July','August','September','October','November','December']


def now():
    return datetime.now(RIYADH)


def validate_event(form):
    data = {key:form.get(key,'').strip() for key in FIELDS}
    if not 3 <= len(data['title']) <= 100 or not 2 <= len(data['organizer']) <= 90:
        raise ValueError('Enter an event title of 3-100 characters and an organizer name of 2-90 characters.')
    if not 10 <= len(data['description']) <= 1500:
        raise ValueError('Description must be between 10 and 1500 characters.')
    if data['kind'] not in ['club','faculty'] or data['venue'] not in VENUES:
        raise ValueError('Select a valid organizer type and venue.')
    try:
        start = datetime.strptime(data['date']+' '+data['start'],'%Y-%m-%d %H:%M').replace(tzinfo=RIYADH)
        end = datetime.strptime(data['date']+' '+data['end'],'%Y-%m-%d %H:%M').replace(tzinfo=RIYADH)
        data['attendees'] = int(data['attendees'])
    except (ValueError, TypeError):
        raise ValueError('Check the date, time and expected attendance.')
    if start <= now() or end <= start:
        raise ValueError('Choose a future start time and an end time after it on the same day.')
    if not 1 <= data['attendees'] <= 10000:
        raise ValueError('Expected attendance must be between 1 and 10000.')
    # Canonical text values make SQLite comparisons reliable.
    data.update(date=start.strftime('%Y-%m-%d'), start=start.strftime('%H:%M'), end=end.strftime('%H:%M'))
    return data


def get_event(event_id):
    event = get_db().execute('SELECT * FROM events WHERE id=?',(event_id,)).fetchone()
    if event is None:
        abort(404)
    return event


def can_view(event):
    return g.user['role'] != 'requester' or event['owner_id'] == g.user['id']


def conflict(event):
    return get_db().execute("SELECT id,title FROM events WHERE status='approved' AND id<>? AND date=? AND venue=? AND start<? AND end>? LIMIT 1", (event['id'],event['date'],event['venue'],event['end'],event['start'])).fetchone()


def require_version(event):
    # A review must refer to the exact proposal the reviewer saw.
    if request.form.get('version') != str(event['version']):
        abort(409)


def save_revision(db, event_id):
    event = db.execute('SELECT * FROM events WHERE id=?', (event_id,)).fetchone()
    db.execute('INSERT OR IGNORE INTO revisions(event_id,actor_id,version,proposal) VALUES(?,?,?,?)',
               (event_id, event['owner_id'], event['version'], json.dumps({k:event[k] for k in FIELDS})))


@bp.get('/')
@login_required
def dashboard():
    conditions, params = [], []
    if g.user['role'] == 'requester':
        conditions.append('owner_id=?')
        params.append(g.user['id'])
    where = ' WHERE '+' AND '.join(conditions) if conditions else ''
    rows = get_db().execute('SELECT * FROM events'+where+' ORDER BY created_at DESC,id DESC',params).fetchall()
    chosen = request.args.get('status','all')
    query = request.args.get('q', '').strip()[:100]
    action_status = {'requester':'changes', 'coordinator':'pending', 'approver':'reviewed'}[g.user['role']]
    attention = sum(e['status'] == action_status for e in rows)
    if chosen != 'all' and chosen not in STATUS:
        abort(400)
    stats = {key:sum(e['status']==key for e in rows) for key in STATUS}
    return render_template('dashboard.html', events=[e for e in rows if (chosen=='all' or e['status']==chosen) and (not query or query.casefold() in (e['title']+' '+e['organizer']+' '+e['venue']).casefold())],query=query,attention=attention,action_status=action_status,stats=stats,total=len(rows),chosen=chosen,view='requests')


@bp.route('/events/new', methods=['GET','POST'])
@login_required
def create():
    if g.user['role'] != 'requester':
        abort(403)
    if request.method == 'POST':
        try:
            data = validate_event(request.form)
            with get_db() as db:
                cursor = db.execute('INSERT INTO events(owner_id,title,organizer,kind,description,date,start,end,venue,attendees) VALUES(?,?,?,?,?,?,?,?,?,?)', [g.user['id']]+[data[k] for k in FIELDS])
                event_id = cursor.lastrowid
                save_revision(db, event_id)
                db.execute('INSERT INTO history(event_id,actor_id,action) VALUES(?,?,?)',(event_id,g.user['id'],'pending'))
            flash('Request sent to the event coordinator.', 'success')
            return redirect(url_for('events.detail',event_id=event_id))
        except ValueError as error:
            flash(str(error),'error')
    return render_template('event_form.html', event=request.form, editing=False, venues=VENUES,today=now().date(),view='requests')


@bp.get('/events/<int:event_id>')
@login_required
def detail(event_id):
    event = get_event(event_id)
    if not can_view(event):
        abort(403)
    history = get_db().execute('SELECT h.*,u.name FROM history h JOIN users u ON u.id=h.actor_id WHERE h.event_id=? ORDER BY h.id',(event_id,)).fetchall()
    revisions = [dict(row, proposal=json.loads(row['proposal'])) for row in get_db().execute('SELECT * FROM revisions WHERE event_id=? ORDER BY version DESC',(event_id,))]
    return render_template('detail.html',revisions=revisions,event=event,history=history,conflict=conflict(event),view='requests')


@bp.route('/events/<int:event_id>/edit', methods=['GET','POST'])
@login_required
def edit(event_id):
    db = get_db()
    if request.method == 'POST':
        db.execute('BEGIN IMMEDIATE')
    event = get_event(event_id)
    if g.user['role'] != 'requester' or event['owner_id'] != g.user['id']:
        abort(403)
    if event['status'] not in ['pending','changes']:
        abort(409)
    if request.method == 'POST':
        try:
            require_version(event)
            data = validate_event(request.form)
            with db:
                save_revision(db, event_id)
                db.execute("UPDATE events SET "+','.join(k+'=?' for k in FIELDS)+",status='pending',version=version+1 WHERE id=?",[data[k] for k in FIELDS]+[event_id])
                save_revision(db, event_id)
                db.execute('INSERT INTO history(event_id,actor_id,action,note) VALUES(?,?,?,?)',(event_id,g.user['id'],'pending','Request edited and resubmitted'))
            flash('Request updated and returned for review.','success')
            return redirect(url_for('events.detail',event_id=event_id))
        except ValueError as error:
            db.rollback()
            flash(str(error),'error')
    return render_template('event_form.html',event=request.form if request.method=='POST' else event,editing=True,venues=VENUES,today=now().date(),view='requests')


@bp.post('/events/<int:event_id>/decision')
@login_required
def decision(event_id):
    db = get_db()
    # Serialize state check + conflict check + update. Two simultaneous approvals
    # cannot reserve the same room and time after both reading an old state.
    db.execute('BEGIN IMMEDIATE')
    try:
        event = get_event(event_id)
        role, state = g.user['role'], event['status']
        allowed = ['reviewed','changes'] if role=='coordinator' and state=='pending' else ['approved','changes','rejected'] if role=='approver' and state=='reviewed' else []
        action, note = request.form.get('action',''),request.form.get('note','').strip()
        if not allowed:
            abort(403)
        require_version(event)
        if action not in allowed:
            abort(400)
        if len(note)>500 or (action in ['changes','rejected'] and not note):
            raise ValueError('Provide a reason for rejection or changes, up to 500 characters.')
        if action in ['reviewed','approved']:
            if datetime.fromisoformat(event['date']+'T'+event['start']).replace(tzinfo=RIYADH)<=now():
                raise ValueError('The event date has passed. Return the request for a new date.')
            if conflict(event):
                raise ValueError('There is a conflict with an approved event at the same venue and time. Return the request for changes.')
        db.execute('UPDATE events SET status=?,version=version+1 WHERE id=?',(action,event_id))
        db.execute('INSERT INTO history(event_id,actor_id,action,note) VALUES(?,?,?,?)',(event_id,g.user['id'],action,note))
        db.commit()
        flash('Decision saved and request status updated.','success')
    except ValueError as error:
        db.rollback()
        flash(str(error),'error')
    except Exception:
        db.rollback()
        raise
    return redirect(url_for('events.detail',event_id=event_id))


@bp.get('/calendar')
@login_required
def calendar_view():
    try:
        selected = datetime.strptime(request.args.get('month',now().strftime('%Y-%m')),'%Y-%m')
        if not 2000 <= selected.year <= 2100:
            raise ValueError()
    except ValueError:
        abort(400)
    month = selected.strftime('%Y-%m')
    # Calendar deliberately exposes only approved public event summaries.
    rows = get_db().execute("SELECT title,date,start,end,venue FROM events WHERE status='approved' AND date LIKE ? ORDER BY date,start",(month+'-%',)).fetchall()
    by_day = {}
    for event in rows:
        by_day.setdefault(int(event['date'][-2:]),[]).append(event)
    prev = (selected-timedelta(days=1)).strftime('%Y-%m')
    nxt = (selected.replace(day=28)+timedelta(days=4)).strftime('%Y-%m')
    return render_template('calendar.html',weeks=calendar.Calendar(firstweekday=6).monthdayscalendar(selected.year,selected.month),by_day=by_day,month_title=MONTHS[selected.month-1]+' '+str(selected.year),previous=prev if selected.year>2000 or selected.month>1 else None,following=nxt if selected.year<2100 or selected.month<12 else None,today=now().date(),selected_month=month,view='calendar')

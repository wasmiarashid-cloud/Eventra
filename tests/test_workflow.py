"""Exercise permissions and workflow boundaries through real HTTP requests."""
from datetime import date,timedelta
import re
import pytest
from werkzeug.security import generate_password_hash,check_password_hash
from eventra import create_app
from eventra.db import get_db,init_db

@pytest.fixture
def app(tmp_path):
    app=create_app({'TESTING':True,'SECRET_KEY':'test-only-secret','DATABASE':str(tmp_path/'test.sqlite'),'WTF_CSRF_ENABLED':False})
    with app.app_context():
        init_db()
        db=get_db()
        for name,role in [('owner','requester'),('stranger','requester'),('coord','coordinator'),('boss','approver')]:
            db.execute('INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,?)',(name,name+'@example.com',generate_password_hash('ExamplePass123!'),role))
        db.commit()
    return app

@pytest.fixture
def client(app):
    return app.test_client()

def login(client,user='owner'):
    return client.post('/login',data={'email':user+'@example.com','password':'ExamplePass123!'})

def data(**changes):
    result=dict(title='Sample programming workshop',organizer='Computing Club',kind='club',description='A workshop to learn programming fundamentals.',date=(date.today()+timedelta(days=10)).isoformat(),start='10:00',end='12:00',venue='Innovation Room',attendees='30')
    result.update(changes)
    return result

def create(client,**changes):
    login(client)
    response=client.post('/events/new',data=data(**changes))
    assert response.status_code==302
    return int(response.location.rsplit('/',1)[1])

def status(app,event_id):
    with app.app_context():
        return get_db().execute('SELECT status FROM events WHERE id=?',(event_id,)).fetchone()[0]

def decision(client,event_id,action,who='coord',note=''):
    login(client,who)
    page = client.get(f'/events/{event_id}').data.decode()
    match = re.search(r'name="version" value="(\d+)"', page)
    return client.post(f'/events/{event_id}/decision',data={'action':action,'note':note,'version':match.group(1) if match else '1'})

def test_complete_workflow_persists_and_audits(app,client):
    eid=create(client)
    assert status(app,eid)=='pending'
    assert decision(client,eid,'reviewed').status_code==302
    assert decision(client,eid,'approved','boss').status_code==302
    assert status(app,eid)=='approved'
    login(client)
    assert 'Sample programming'.encode() in client.get('/calendar?month='+data()['date'][:7]).data
    assert client.get(f'/events/{eid}/edit').status_code==409
    with app.app_context():
        assert get_db().execute('SELECT COUNT(*) FROM history WHERE event_id=?',(eid,)).fetchone()[0]==3
    second_app=create_app(dict(app.config))
    assert second_app.config['DATABASE']==app.config['DATABASE']
    with second_app.app_context():
        assert get_db().execute('SELECT status FROM events WHERE id=?',(eid,)).fetchone()[0]=='approved'

def test_requester_cannot_escalate(app,client):
    client.post('/register',data={'name':'New User','email':'new@example.com','password':'ExamplePass123!','role':'approver'})
    with app.app_context():
        u=get_db().execute('SELECT * FROM users WHERE email=?',('new@example.com',)).fetchone()
        assert u['role']=='requester'
        assert u['password_hash']!='ExamplePass123!'
        assert check_password_hash(u['password_hash'],'ExamplePass123!')
    eid=create(client)
    assert decision(client,eid,'approved',who='owner').status_code==403
    assert decision(client,eid,'approved',who='boss').status_code==403
    assert status(app,eid)=='pending'

def test_other_requester_cannot_read_or_edit(app,client):
    eid=create(client)
    login(client,'stranger')
    assert client.get(f'/events/{eid}').status_code==403
    assert client.post(f'/events/{eid}/edit',data=data(title='Attempted change')).status_code==403
    assert 'Sample programming'.encode() not in client.get('/').data

def test_revisions_return_to_review(app,client):
    eid=create(client)
    decision(client,eid,'changes',note='Please explain the audience')
    assert status(app,eid)=='changes'
    login(client)
    response=client.post(f'/events/{eid}/edit',data=data(version='2',description='Updated information about the target audience for the workshop.'))
    assert response.status_code==302
    assert status(app,eid)=='pending'

def test_reason_required_and_rejection_terminal(app,client):
    eid=create(client)
    decision(client,eid,'changes')
    assert status(app,eid)=='pending'
    decision(client,eid,'reviewed')
    decision(client,eid,'rejected','boss')
    assert status(app,eid)=='reviewed'
    decision(client,eid,'rejected','boss','Outside the college scope')
    assert status(app,eid)=='rejected'
    assert decision(client,eid,'approved','boss').status_code==403

@pytest.mark.parametrize('changes',[{'end':'09:00'},{'attendees':'0'},{'attendees':'1.5'},{'date':'2020-01-01'},{'venue':'Unknown'},{'kind':'admin'},{'title':' '},{'date':'bad'}])
def test_server_validates_inputs(app,client,changes):
    login(client)
    client.post('/events/new',data=data(**changes))
    with app.app_context():
        assert get_db().execute('SELECT COUNT(*) FROM events').fetchone()[0]==0

def test_conflict_rechecked_at_approval(app,client):
    first=create(client)
    second=create(client,start='11:00',end='13:00')
    decision(client,first,'reviewed')
    decision(client,second,'reviewed')
    decision(client,first,'approved','boss')
    response=decision(client,second,'approved','boss')
    assert response.status_code==302
    assert status(app,second)=='reviewed'
    assert 'conflict'.encode() in client.get(response.location).data
    third=create(client,start='12:00',end='13:00')
    decision(client,third,'reviewed')
    decision(client,third,'approved','boss')
    assert status(app,third)=='approved'

def test_same_time_different_venue_allowed(app,client):
    for venue in ['Innovation Room','College Atrium']:
        eid=create(client,venue=venue)
        decision(client,eid,'reviewed')
        decision(client,eid,'approved','boss')
        assert status(app,eid)=='approved'

def test_csrf_is_required_and_valid_token_works(app,client):
    app.config['WTF_CSRF_ENABLED']=True
    assert login(client).status_code==400
    page=client.get('/login').data.decode()
    token=re.search(r'name="csrf_token" value="([^"]+)"',page).group(1)
    assert client.post('/login',data={'email':'owner@example.com','password':'ExamplePass123!','csrf_token':token}).status_code==302
    assert client.post('/events/new',data=data()).status_code==400
    assert client.post('/logout').status_code==400

def test_unauthenticated_and_calendar_privacy(app,client):
    assert client.get('/').status_code==302
    eid=create(client)
    login(client,'stranger')
    assert 'Sample programming'.encode() not in client.get('/calendar?month='+data()['date'][:7]).data
    assert client.get('/calendar?month=invalid').status_code==400

def test_all_html_pages_render_and_escape(app,client):
    assert client.get('/login').status_code==200
    assert client.get('/register').status_code==200
    eid=create(client,title='<script>alert(1)</script>')
    for path in ['/', '/events/new',f'/events/{eid}',f'/events/{eid}/edit','/calendar']:
        response=client.get(path)
        assert response.status_code==200
        assert b'<script>alert(1)</script>' not in response.data
        assert "frame-ancestors 'none'" in response.headers['Content-Security-Policy']
    assert client.get('/not-found').status_code==404

def test_init_db_preserves_existing_data(app):
    with app.app_context():
        init_db()
        assert get_db().execute('SELECT COUNT(*) FROM users').fetchone()[0]==4


def test_stale_review_cannot_approve_unseen_edits(app, client):
    eid = create(client)
    login(client)
    assert client.post(f'/events/{eid}/edit', data=data(version='1', title='Revised workshop title')).status_code == 302
    login(client, 'coord')
    response = client.post(f'/events/{eid}/decision', data={'version':'1', 'action':'reviewed'})
    assert response.status_code == 409
    assert status(app, eid) == 'pending'
    with app.app_context():
        assert get_db().execute('SELECT COUNT(*) FROM history WHERE event_id=?', (eid,)).fetchone()[0] == 2


def test_stale_edit_preserves_newer_proposal(app, client):
    eid = create(client)
    assert client.post(f'/events/{eid}/edit', data=data(version='1', title='First updated proposal')).status_code == 302
    assert client.post(f'/events/{eid}/edit', data=data(version='1', title='Stale proposal overwrite')).status_code == 409
    with app.app_context():
        assert get_db().execute('SELECT title FROM events WHERE id=?', (eid,)).fetchone()[0] == 'First updated proposal'
        rows = get_db().execute('SELECT proposal FROM revisions WHERE event_id=? ORDER BY version', (eid,)).fetchall()
        assert len(rows) == 2
        assert 'Sample programming workshop' in rows[0][0]
        assert 'First updated proposal' in rows[1][0]


def test_search_respects_owner_scope(app, client):
    create(client)
    assert b'Sample programming workshop' in client.get('/?q=computing').data
    assert b'Sample programming workshop' not in client.get('/?q=unmatched').data
    login(client, 'stranger')
    assert b'Sample programming workshop' not in client.get('/?q=computing').data


def test_calendar_boundary_navigation(client):
    login(client)
    assert b'Previous month' not in client.get('/calendar?month=2000-01').data
    assert b'Next month' not in client.get('/calendar?month=2100-12').data


def test_missing_version_rejected(client):
    eid = create(client)
    assert client.post(f'/events/{eid}/edit', data=data()).status_code == 409
    login(client, 'coord')
    assert client.post(f'/events/{eid}/decision', data={'action':'reviewed'}).status_code == 409


def test_simultaneous_approvals_reserve_only_once(app, client):
    from concurrent.futures import ThreadPoolExecutor
    first = create(client)
    second = create(client)
    decision(client, first, 'reviewed')
    decision(client, second, 'reviewed')
    def approve(eid):
        local_client = app.test_client()
        with local_client.session_transaction() as session:
            session['user_id'] = 4
        return local_client.post(f'/events/{eid}/decision', data={'action':'approved', 'version':'2'}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(approve, [first, second])) == [302, 302]
    assert sorted([status(app, first), status(app, second)]) == ['approved', 'reviewed']


def test_legacy_database_migration_preserves_records(tmp_path):
    import sqlite3
    database = tmp_path / 'legacy.sqlite'
    # Build the old schema without depending on an external project checkout.
    with sqlite3.connect(database) as db:
        db.executescript('''CREATE TABLE users(id INTEGER PRIMARY KEY,name TEXT,email TEXT,password_hash TEXT,role TEXT);
        CREATE TABLE events(id INTEGER PRIMARY KEY,owner_id INTEGER,title TEXT,organizer TEXT,kind TEXT,description TEXT,date TEXT,start TEXT,end TEXT,venue TEXT,attendees INTEGER,status TEXT,created_at TEXT);
        INSERT INTO users VALUES(1,'Original','original@example.com','hash','requester');
        INSERT INTO events VALUES(1,1,'Original event','Club','club','Original description','2030-01-01','10:00','11:00','Innovation Room',30,'pending','2026-01-01');''')
    upgraded = create_app({'TESTING':True,'SECRET_KEY':'test','DATABASE':str(database)})
    with upgraded.app_context():
        init_db()
        init_db()
        row = get_db().execute('SELECT title,version FROM events WHERE id=1').fetchone()
        assert tuple(row) == ('Original event',1)

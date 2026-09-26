"""One SQLite connection per request; all SQL values use bound parameters."""
import sqlite3
import click
from datetime import date, timedelta
from flask import current_app, g
from werkzeug.security import generate_password_hash


def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(current_app.config['DATABASE'], timeout=15)
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA foreign_keys=ON')
    return g.db


def close_db(_error=None):
    connection = g.pop('db', None)
    if connection is not None:
        connection.close()


def init_db():
    with current_app.open_resource('schema.sql') as schema:
        get_db().executescript(schema.read().decode('utf-8'))
    db = get_db()
    if 'version' not in {row['name'] for row in db.execute('PRAGMA table_info(events)')}:
        db.execute('ALTER TABLE events ADD COLUMN version INTEGER NOT NULL DEFAULT 1')
    db.commit()


def init_app(app):
    app.teardown_appcontext(close_db)

    @app.cli.command('init-db')
    def init_command():
        """Create missing tables without deleting existing records."""
        init_db()
        click.echo('Database initialized. Existing records preserved.')

    @app.cli.command('create-user')
    @click.option('--name', prompt=True)
    @click.option('--email', prompt=True)
    @click.option('--role', type=click.Choice(['requester', 'coordinator', 'approver']), prompt=True)
    @click.password_option()
    def create_user(name, email, role, password):
        """Create a staff account explicitly; public registration cannot set roles."""
        from .auth import valid_identity
        error = valid_identity(name.strip(), email.strip().lower(), password)
        if error:
            raise click.ClickException(error)
        try:
            with get_db() as db:
                db.execute('INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,?)',
                           (name.strip(), email.strip().lower(), generate_password_hash(password), role))
        except sqlite3.IntegrityError:
            raise click.ClickException('Email is already registered.')
        click.echo('User created.')

    @app.cli.command('seed-demo')
    @click.password_option()
    def seed_demo(password):
        """Populate an empty LOCAL database with three demo accounts and sample events."""
        if len(password) < 12 or len(password) > 128:
            raise click.ClickException('Use a demo password between 12 and 128 characters.')
        db = get_db()
        if db.execute('SELECT COUNT(*) FROM users').fetchone()[0]:
            raise click.ClickException('Demo seeding requires an empty database; nothing was changed.')
        with db:
            for role, name in [('requester','Computing Club'), ('coordinator','Event Coordinator'), ('approver','Approver')]:
                db.execute('INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,?)',
                           (name, role + '@example.com', generate_password_hash(password), role))
            rows = [
                ('Introduction to AI Workshop','AI Club','club','Innovation Room','pending',3,'10:00','12:00'),
                ('Student Projects Showcase','Faculty Member - Demo','faculty','Main Auditorium','reviewed',5,'11:00','13:00'),
                ('Python Programming Essentials','Computing Club','club','Computer Lab 1','approved',2,'10:00','12:00'),
                ('Student Clubs Open Day','Student Clubs Office','club','College Atrium','approved',7,'09:00','14:00'),
                ('User Experience Design Workshop','Computing Club','club','Innovation Room','changes',9,'12:00','14:00'),
            ]
            for title, organizer, kind, venue, status, offset, start, end in rows:
                cursor = db.execute('INSERT INTO events(owner_id,title,organizer,kind,description,date,start,end,venue,attendees,status) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (1,title,organizer,kind,'A sample college event for sharing knowledge and developing skills.',(date.today()+timedelta(days=offset)).isoformat(),start,end,venue,60,status))
                event_id = cursor.lastrowid
                db.execute('INSERT INTO history(event_id,actor_id,action,note) VALUES(?,?,?,?)', (event_id,1,'pending','Sample request'))
                if status in ['reviewed','approved']:
                    db.execute('INSERT INTO history(event_id,actor_id,action) VALUES(?,?,?)',(event_id,2,'reviewed'))
                if status == 'approved':
                    db.execute('INSERT INTO history(event_id,actor_id,action) VALUES(?,?,?)',(event_id,3,'approved'))
                if status == 'changes':
                    db.execute('INSERT INTO history(event_id,actor_id,action,note) VALUES(?,?,?,?)',(event_id,2,'changes','Please clarify the target audience.'))
        click.echo('Local demo ready: requester@example.com, coordinator@example.com, approver@example.com')

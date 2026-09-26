"""Session authentication. New public accounts always have requester permissions."""
import re
import sqlite3
from functools import wraps
from flask import Blueprint, g, request, session, redirect, url_for, render_template, flash, abort
from werkzeug.security import check_password_hash, generate_password_hash
from .db import get_db

bp = Blueprint('auth', __name__)
ROLES = {'requester':'Requester', 'coordinator':'Event Coordinator', 'approver':'Approver'}


def valid_identity(name, email, password):
    if not 2 <= len(name) <= 80:
        return 'Name must be between 2 and 80 characters.'
    if len(email) > 254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
        return 'Enter a valid email address.'
    if not 12 <= len(password) <= 128:
        return 'Password must be between 12 and 128 characters.'
    return None


@bp.before_app_request
def load_user():
    user_id = session.get('user_id')
    g.user = get_db().execute('SELECT id,name,email,role FROM users WHERE id=?', (user_id,)).fetchone() if user_id else None


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for('auth.login'))
        return view(*args, **kwargs)
    return wrapped


@bp.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        name, email, password = request.form.get('name','').strip(), request.form.get('email','').strip().lower(), request.form.get('password','')
        error = valid_identity(name,email,password)
        if not error:
            try:
                with get_db() as db:
                    db.execute('INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,?)', (name,email,generate_password_hash(password),'requester'))
                flash('Account created. You can now sign in.', 'success')
                return redirect(url_for('auth.login'))
            except sqlite3.IntegrityError:
                error = 'This email address is already registered.'
        flash(error, 'error')
    return render_template('auth.html', register=True)


@bp.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email','').strip().lower()
        password = request.form.get('password','')
        user = get_db().execute('SELECT * FROM users WHERE email=?',(email,)).fetchone()
        if user and len(password) <= 128 and check_password_hash(user['password_hash'], password):
            session.clear()
            session['user_id'] = user['id']
            session.permanent = True
            return redirect(url_for('events.dashboard'))
        flash('Incorrect email address or password.', 'error')
    return render_template('auth.html', register=False)


@bp.post('/logout')
@login_required
def logout():
    session.clear()
    return redirect(url_for('auth.login'))

"""Eventra application factory: configure first, then register independent modules."""
import os
from datetime import timedelta
from flask import Flask, render_template
from flask_wtf.csrf import CSRFProtect, CSRFError
from dotenv import load_dotenv

csrf = CSRFProtect()

def create_app(test_config=None):
    load_dotenv()
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.getenv('SECRET_KEY'),
        DATABASE=os.path.join(app.instance_path, 'eventra.sqlite'),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_SECURE=os.getenv('COOKIE_SECURE') == '1',
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        MAX_CONTENT_LENGTH=64 * 1024,
    )
    if test_config:
        app.config.update(test_config)
    key = app.config.get('SECRET_KEY')
    if not app.config.get('TESTING') and (not key or len(key) < 32 or key.startswith('replace-')):
        raise RuntimeError('Set a random SECRET_KEY of at least 32 characters in .env. See README.')
    os.makedirs(app.instance_path, exist_ok=True)
    csrf.init_app(app)
    from . import db, auth, events
    db.init_app(app)
    app.register_blueprint(auth.bp)
    app.register_blueprint(events.bp)
    app.context_processor(lambda: dict(status_labels=events.STATUS, role_labels=auth.ROLES))

    @app.after_request
    def response_headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'self'"
        if response.mimetype == 'text/html':
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.errorhandler(CSRFError)
    def csrf_error(error):
        return render_template('error.html', code=400, message='This form has expired. Refresh the page and try again.'), 400

    for code, message in [(400, 'Invalid request data.'), (403, 'You do not have permission to perform this action.'), (404, 'The page or request was not found.'), (409, 'The request status has changed. Refresh the page before trying again.'), (413, 'The request exceeds the allowed size.')]:
        def handler(error, code=code, message=message):
            return render_template('error.html', code=code, message=message), code
        app.register_error_handler(code, handler)
    return app

import os
from dotenv import load_dotenv
from flask import Flask, render_template, redirect, url_for, request
from database import init_db

# Load environment variables from .env file
load_dotenv()

def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get('SECRET_KEY', 'dev_secret_key_change_in_production'),
        DATABASE=os.path.join(app.root_path, 'shop_catalog.db'),
        TURSO_DATABASE_URL=os.environ.get('TURSO_DATABASE_URL'),
        TURSO_AUTH_TOKEN=os.environ.get('TURSO_AUTH_TOKEN'),
        UPLOAD_FOLDER=os.path.join(app.root_path, 'static', 'uploads'),
        IMAGE_FOLDER=os.path.join(app.root_path, 'static', 'images'),
        QR_FOLDER=os.path.join(app.root_path, 'static', 'qrs'),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        PERMANENT_SESSION_LIFETIME=86400,
    )

    if test_config is None:
        app.config.from_pyfile('config.py', silent=True)
    else:
        app.config.from_mapping(test_config)

    # Ensure directories exist
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(app.config['IMAGE_FOLDER'], exist_ok=True)
    os.makedirs(app.config['QR_FOLDER'], exist_ok=True)

    import database
    app.teardown_appcontext(database.close_connection)

    # Initialize DB (if not exists)
    init_db(app)

    # Register blueprints
    from routes import manager, shop, admin
    app.register_blueprint(manager.bp)
    app.register_blueprint(shop.bp)
    app.register_blueprint(admin.bp)

    @app.template_filter('image_url')
    def image_url_filter(image_path):
        if not image_path or image_path == 'placeholder.jpg':
            return url_for('static', filename='images/placeholder.jpg')
        if image_path.startswith('http://') or image_path.startswith('https://'):
            return image_path
        return url_for('static', filename='images/' + image_path)

    app.jinja_env.globals['image_url'] = image_url_filter

    @app.before_request
    def check_blocked_ip():
        from services.qr_service import get_client_ip
        from database import get_db
        client_ip = get_client_ip(request)
        db = get_db()
        blocked = db.execute('SELECT ip_id FROM tbl_blocked_ips WHERE ip_address = ?', (client_ip,)).fetchone()
        if blocked:
            return render_template('403.html', ip_address=client_ip), 403

    @app.after_request
    def set_security_headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        response.headers['X-XSS-Protection'] = '1; mode=block'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        return response

    @app.route('/')
    def index():
        return redirect(url_for('manager.login'))

    @app.route('/scan/<shop_slug>')
    def root_scan(shop_slug):
        return redirect(url_for('shop.scan', shop_slug=shop_slug))

    return app

# WSGI Application instance for Gunicorn / Render
app = create_app()


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)

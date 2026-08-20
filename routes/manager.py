from flask import Blueprint, render_template, request, redirect, url_for, session, current_app, flash, jsonify, Response
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from database import get_db
from datetime import datetime
from collections import defaultdict
import sqlite3
import re
import os
import io
import csv
import urllib.parse

from PIL import Image
from services.upload_service import (
    process_csv_upload, process_image_upload,
    generate_next_sku, upload_product_image_file
)
from services.qr_service import get_shop_base_url, get_local_ip, generate_shop_qr, parse_user_agent

bp = Blueprint('manager', __name__, url_prefix='/manager')

# ── Role maps ─────────────────────────────────────────────────────────────────
_ROLE_LABEL = {'Order_Only': 'Order Manager', 'Stock_Only': 'Stock Manager', 'Full': 'Full Assistant'}
_ROLE_ICON  = {'Order_Only': '📦', 'Stock_Only': '📊', 'Full': '⭐'}
_ROLE_BADGE = {
    'Order_Only': 'bg-amber-100 text-amber-900 border-amber-300',
    'Stock_Only': 'bg-blue-100 text-blue-900 border-blue-300',
    'Full':       'bg-purple-100 text-purple-900 border-purple-300',
}

# ── Auth guard helper ─────────────────────────────────────────────────────────
def _require_manager():
    """Return redirect if not logged in, else return manager_id."""
    mid = session.get('manager_id')
    if mid is None:
        return None, redirect(url_for('manager.login'))
    return mid, None


# ── RBAC before_request ───────────────────────────────────────────────────────
@bp.before_request
def check_staff_rbac():
    if not session.get('is_staff'):
        return
    role     = session.get('staff_role', 'Order_Only')
    endpoint = request.endpoint

    if role == 'Order_Only':
        allowed = {'manager.orders', 'manager.update_order_status',
                   'manager.logout', 'manager.login',
                   'manager.staff_login', 'manager.staff_shop_qr'}
        if endpoint and endpoint.startswith('manager.') and endpoint not in allowed:
            flash('Access Restricted: Order Manager accounts are restricted to order operations.', 'error')
            return redirect(url_for('manager.orders'))

    elif role == 'Stock_Only':
        allowed = {'manager.bulk_stock', 'manager.logout',
                   'manager.login', 'manager.staff_login', 'manager.staff_shop_qr'}
        if endpoint and endpoint.startswith('manager.') and endpoint not in allowed:
            flash('Access Restricted: Stock Manager accounts cannot access the dashboard.', 'error')
            return redirect(url_for('manager.bulk_stock'))

    elif role == 'Full':
        disallowed = {'manager.staff', 'manager.add_staff', 'manager.delete_staff',
                      'manager.toggle_staff_status', 'manager.update_staff_role', 'manager.reports'}
        if endpoint and endpoint in disallowed:
            flash('Access Restricted: Assistant accounts cannot manage staff or view financial reports.', 'error')
            return redirect(url_for('manager.dashboard'))


# ── Context processor ─────────────────────────────────────────────────────────
@bp.context_processor
def inject_staff_shop_context():
    # Skip DB call for static files or when no session exists
    manager_id = session.get('manager_id')
    if not manager_id:
        return {'current_shop': None, 'current_shop_qr': None, 'logged_account': None}

    # Skip for non-manager endpoints (static assets etc.)
    endpoint = request.endpoint or ''
    if not endpoint.startswith('manager.') and endpoint not in ('static',):
        return {'current_shop': None, 'current_shop_qr': None, 'logged_account': None}

    db = get_db()
    current_shop = db.execute(
        'SELECT manager_id, shop_name, shop_slug, email FROM tbl_managers WHERE manager_id = ?',
        (manager_id,)
    ).fetchone()

    if not current_shop:
        return {'current_shop': None, 'current_shop_qr': None, 'logged_account': None}

    # Ensure QR exists (lazy-generate, no blocking)
    qr_file = f"{current_shop['shop_slug']}.png"
    qr_path = os.path.join(current_app.config['QR_FOLDER'], qr_file)
    if not os.path.exists(qr_path):
        try:
            generate_shop_qr(current_shop['shop_slug'], current_app.config['QR_FOLDER'])
        except Exception:
            pass

    if session.get('is_staff'):
        staff_role = session.get('staff_role', 'Order_Only')
        staff_user = session.get('staff_username', 'Staff Member')
        logged_account = {
            'is_staff':      True,
            'username':      staff_user,
            'display_name':  staff_user,
            'email_or_user': f"@{staff_user}",
            'role_label':    _ROLE_LABEL.get(staff_role, staff_role),
            'role_code':     staff_role,
            'role_icon':     _ROLE_ICON.get(staff_role, '👤'),
            'badge_class':   _ROLE_BADGE.get(staff_role, ''),
            'shop_name':     current_shop['shop_name'],
            'shop_slug':     current_shop['shop_slug'],
        }
    else:
        logged_account = {
            'is_staff':      False,
            'username':      current_shop['shop_name'],
            'display_name':  current_shop['shop_name'],
            'email_or_user': current_shop['email'],
            'role_label':    'Store Owner / Manager',
            'role_code':     'Owner',
            'role_icon':     '👑',
            'badge_class':   'bg-emerald-100 text-emerald-900 border-emerald-300',
            'shop_name':     current_shop['shop_name'],
            'shop_slug':     current_shop['shop_slug'],
        }

    return {
        'current_shop':     current_shop,
        'current_shop_qr':  qr_file,
        'logged_account':   logged_account,
    }


# ── Register ──────────────────────────────────────────────────────────────────
@bp.route('/register', methods=('GET', 'POST'))
def register():
    if request.method == 'POST':
        shop_name    = request.form.get('shop_name', '').strip()
        email        = request.form.get('email', '').strip()
        password     = request.form.get('password', '').strip()
        phone_number = request.form.get('phone_number', '').strip()
        shop_slug    = re.sub(r'[^a-z0-9]+', '-', shop_name.lower()).strip('-')

        error = None
        if not all([shop_name, email, password, phone_number]):
            error = 'All fields are required.'
        elif not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email):
            error = 'Please enter a valid email address.'
        elif len(password) < 6:
            error = 'Password must be at least 6 characters long.'
        elif not re.match(r'^\d{10}$', re.sub(r'\D', '', phone_number)):
            error = 'Please enter a valid 10-digit phone number.'
        elif not shop_slug:
            error = 'Shop name must contain valid letters or numbers.'

        if error is None:
            db = get_db()
            try:
                db.execute(
                    "INSERT INTO tbl_managers (shop_name, shop_slug, email, password_hash, phone_number) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (shop_name, shop_slug, email, generate_password_hash(password), phone_number),
                )
                db.commit()
                generate_shop_qr(shop_slug, current_app.config['QR_FOLDER'])
            except (sqlite3.IntegrityError, ValueError) as e:
                error = (f"Shop name '{shop_name}' is already taken."
                         if 'shop_slug' in str(e)
                         else f"Email '{email}' is already registered.")
            else:
                flash('Registration successful! Please log in.', 'success')
                return redirect(url_for('manager.login'))

        flash(error)

    return render_template('manager/register.html')


# ── Login ─────────────────────────────────────────────────────────────────────
@bp.route('/login', methods=('GET', 'POST'))
def login():
    if request.method == 'POST':
        login_input = request.form.get('email', '').strip()
        password    = request.form.get('password', '').strip()
        db = get_db()
        error = None

        manager = db.execute('SELECT * FROM tbl_managers WHERE email = ?', (login_input,)).fetchone()
        if manager:
            if check_password_hash(manager['password_hash'], password):
                if manager['is_suspended'] == 1:
                    error = 'Your account has been suspended by the administrator.'
                else:
                    session.clear()
                    session['manager_id'] = manager['manager_id']
                    session['is_staff']   = False
                    session['staff_role'] = 'Full'
                    return redirect(url_for('manager.dashboard'))
            else:
                error = 'Incorrect password.'
        else:
            staff = db.execute('SELECT * FROM tbl_staff_accounts WHERE username = ?', (login_input,)).fetchone()
            if staff and check_password_hash(staff['password_hash'], password):
                if 'is_active' in staff.keys() and staff['is_active'] == 0:
                    error = f'Staff account "{staff["username"]}" has been disabled by store manager.'
                else:
                    parent = db.execute('SELECT * FROM tbl_managers WHERE manager_id = ?', (staff['manager_id'],)).fetchone()
                    if parent and parent['is_suspended'] == 1:
                        error = 'Manager account is suspended.'
                    else:
                        now_str = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
                        db.execute('UPDATE tbl_staff_accounts SET last_active = ? WHERE staff_id = ?', (now_str, staff['staff_id']))
                        db.commit()
                        session.clear()
                        session['manager_id']    = staff['manager_id']
                        session['staff_id']      = staff['staff_id']
                        session['is_staff']      = True
                        session['staff_role']    = staff['role']
                        session['staff_username'] = staff['username']
                        if staff['role'] == 'Order_Only':
                            return redirect(url_for('manager.orders'))
                        elif staff['role'] == 'Stock_Only':
                            return redirect(url_for('manager.bulk_stock'))
                        return redirect(url_for('manager.dashboard'))
            else:
                error = 'Incorrect email/username or password.'

        flash(error)

    return render_template('manager/login.html')


# ── Staff Login ───────────────────────────────────────────────────────────────
@bp.route('/staff_login/<shop_slug>', methods=('GET', 'POST'))
def staff_login(shop_slug):
    db = get_db()
    manager = db.execute(
        'SELECT * FROM tbl_managers WHERE shop_slug = ? AND is_suspended = 0', (shop_slug,)
    ).fetchone()

    if not manager:
        flash('Shop not found or account is suspended.', 'error')
        return redirect(url_for('manager.login'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        error = None

        if not username or not password:
            error = 'Username and password are required.'
        else:
            staff = db.execute(
                'SELECT * FROM tbl_staff_accounts WHERE username = ? AND manager_id = ?',
                (username, manager['manager_id'])
            ).fetchone()

            if staff and check_password_hash(staff['password_hash'], password):
                if 'is_active' in staff.keys() and staff['is_active'] == 0:
                    error = f'Staff account "{staff["username"]}" has been disabled by store manager.'
                else:
                    now_str = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
                    db.execute('UPDATE tbl_staff_accounts SET last_active = ? WHERE staff_id = ?', (now_str, staff['staff_id']))
                    db.commit()
                    session.clear()
                    session['manager_id']    = staff['manager_id']
                    session['staff_id']      = staff['staff_id']
                    session['is_staff']      = True
                    session['staff_role']    = staff['role']
                    session['staff_username'] = staff['username']
                    session['staff_shop_slug'] = manager['shop_slug']
                    flash(f'Logged in as staff ({staff["username"]}) for {manager["shop_name"]}.', 'success')
                    if staff['role'] == 'Order_Only':
                        return redirect(url_for('manager.orders'))
                    elif staff['role'] == 'Stock_Only':
                        return redirect(url_for('manager.bulk_stock'))
                    return redirect(url_for('manager.dashboard'))
            else:
                error = f'Incorrect staff username or password for {manager["shop_name"]}.'

        flash(error, 'error')

    return render_template('manager/staff_login.html', manager=manager)


# ── Logout ────────────────────────────────────────────────────────────────────
@bp.route('/logout')
def logout():
    shop_slug = session.get('staff_shop_slug') if session.get('is_staff') else None
    session.clear()
    if shop_slug:
        return redirect(url_for('manager.staff_login', shop_slug=shop_slug))
    return redirect(url_for('manager.login'))


# ── Dashboard ─────────────────────────────────────────────────────────────────
@bp.route('/dashboard')
def dashboard():
    manager_id, guard = _require_manager()
    if guard:
        return guard

    db      = get_db()
    manager = db.execute('SELECT * FROM tbl_managers WHERE manager_id = ?', (manager_id,)).fetchone()
    if manager is None:
        session.clear()
        return redirect(url_for('manager.login'))

    products = db.execute(
        'SELECT * FROM tbl_products WHERE manager_id = ? ORDER BY product_id DESC', (manager_id,)
    ).fetchall()

    shop_base_url = get_shop_base_url(request)
    shop_url  = f"{shop_base_url}/shop/{manager['shop_slug']}"
    scan_url  = f"{shop_base_url}/scan/{manager['shop_slug']}"

    # Ensure QR file exists
    qr_filename = f"{manager['shop_slug']}.png"
    qr_filepath = os.path.join(current_app.config['QR_FOLDER'], qr_filename)
    if not os.path.exists(qr_filepath):
        generate_shop_qr(manager['shop_slug'], current_app.config['QR_FOLDER'], shop_base_url)

    # Analytics — combined into a single query for visitor session stats
    now_str = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    today   = datetime.utcnow().strftime('%Y-%m-%d')

    visitor_stats = db.execute('''
        SELECT
            COUNT(*) AS total_scans,
            SUM(CASE WHEN expires_at > ? THEN 1 ELSE 0 END) AS active_visitors,
            SUM(CASE WHEN DATE(created_at) = ? THEN 1 ELSE 0 END) AS today_scans
        FROM tbl_visitor_sessions
        WHERE manager_id = ?
    ''', (now_str, today, manager_id)).fetchone()

    total_scans     = visitor_stats['total_scans']    or 0
    active_visitors = visitor_stats['active_visitors'] or 0
    today_scans     = visitor_stats['today_scans']    or 0

    raw_scans = db.execute('''
        SELECT visit_id, session_token, ip_address, user_agent, created_at, expires_at,
               CASE WHEN expires_at > ? THEN 'Active' ELSE 'Expired' END as status
        FROM tbl_visitor_sessions
        WHERE manager_id = ?
        ORDER BY visit_id DESC LIMIT 15
    ''', (now_str, manager_id)).fetchall()

    recent_scans = [{
        'visit_id':       s['visit_id'],
        'session_token':  (s['session_token'][:8] + '...') if s['session_token'] else 'N/A',
        'ip_address':     s['ip_address'] or '127.0.0.1',
        'device_info':    parse_user_agent(s['user_agent']),
        'user_agent_raw': s['user_agent'] or '',
        'created_at':     s['created_at'],
        'status':         s['status'],
    } for s in raw_scans]

    low_stock_products = db.execute(
        "SELECT name, stock_qty FROM tbl_products "
        "WHERE manager_id = ? AND stock_qty <= 5 AND status = 'Active' ORDER BY stock_qty ASC",
        (manager_id,)
    ).fetchall()

    pending_orders = db.execute(
        "SELECT COUNT(*) as cnt FROM tbl_orders WHERE manager_id = ? AND status = 'Pending'",
        (manager_id,)
    ).fetchone()['cnt']

    return render_template(
        'manager/dashboard.html',
        manager=manager,
        products=products,
        total_scans=total_scans,
        active_visitors=active_visitors,
        today_scans=today_scans,
        recent_scans=recent_scans,
        low_stock_products=low_stock_products,
        pending_orders=pending_orders,
        local_ip=get_local_ip(),
        shop_base_url=shop_base_url,
        shop_url=shop_url,
        scan_url=scan_url,
    )


# ── Quick edit (AJAX / form) ──────────────────────────────────────────────────
@bp.route('/product/<int:product_id>/quick_edit', methods=['POST'])
def quick_edit_product(product_id):
    manager_id, guard = _require_manager()
    if guard:
        return guard

    price_inr = request.form.get('price_inr', '').strip()
    stock_qty = request.form.get('stock_qty', '').strip()

    db = get_db()
    product = db.execute(
        'SELECT product_id FROM tbl_products WHERE product_id = ? AND manager_id = ?',
        (product_id, manager_id)
    ).fetchone()

    if not product:
        flash('Product not found.')
        return redirect(url_for('manager.dashboard'))

    fields, values = [], []
    if price_inr != '':
        fields.append('price_inr = ?')
        values.append(float(price_inr) if price_inr else 0.0)
    if stock_qty != '':
        fields.append('stock_qty = ?')
        values.append(int(stock_qty))

    if fields:
        values.append(product_id)
        db.execute(f'UPDATE tbl_products SET {", ".join(fields)} WHERE product_id = ?', values)
        db.commit()
        flash('Product updated successfully!', 'success')

    return redirect(url_for('manager.dashboard'))


# ── Setting toggles ───────────────────────────────────────────────────────────
def _toggle_manager_flag(flag_col, manager_id, db):
    """Generic toggle for boolean manager columns."""
    row = db.execute(f'SELECT {flag_col} FROM tbl_managers WHERE manager_id = ?', (manager_id,)).fetchone()
    if row:
        new_val = 0 if row[flag_col] == 1 else 1
        db.execute(f'UPDATE tbl_managers SET {flag_col} = ? WHERE manager_id = ?', (new_val, manager_id))
        db.commit()


@bp.route('/toggle_whatsapp', methods=['POST'])
def toggle_whatsapp():
    manager_id, guard = _require_manager()
    if guard: return guard
    _toggle_manager_flag('whatsapp_orders_enabled', manager_id, get_db())
    flash('WhatsApp Order settings updated.')
    return redirect(url_for('manager.dashboard'))


@bp.route('/toggle_price', methods=['POST'])
def toggle_price():
    manager_id, guard = _require_manager()
    if guard: return guard
    _toggle_manager_flag('price_mandatory', manager_id, get_db())
    flash('Price mandatory setting updated.')
    return redirect(url_for('manager.dashboard'))


@bp.route('/toggle_show_price', methods=['POST'])
def toggle_show_price():
    manager_id, guard = _require_manager()
    if guard: return guard
    _toggle_manager_flag('show_price', manager_id, get_db())
    flash('Price visibility updated.')
    return redirect(url_for('manager.dashboard'))


# ── Bulk upload (CSV) ─────────────────────────────────────────────────────────
@bp.route('/upload', methods=('GET', 'POST'))
def upload():
    manager_id, guard = _require_manager()
    if guard: return guard

    db      = get_db()
    manager = db.execute('SELECT * FROM tbl_managers WHERE manager_id = ?', (manager_id,)).fetchone()

    if manager['bulk_upload_enabled'] == 0:
        flash('Bulk upload is disabled for your account. Please contact the administrator.')
        return redirect(url_for('manager.dashboard'))

    if request.method == 'POST':
        file = request.files.get('csv_file')
        if not file or file.filename == '':
            flash('No selected file')
            return redirect(request.url)
        if file.filename.endswith('.csv'):
            try:
                count = process_csv_upload(file, manager_id)
                flash(f'Successfully imported/updated {count} products.')
            except Exception as e:
                flash(f'Error processing CSV: {str(e)}')
            return redirect(url_for('manager.dashboard'))
        flash('Please upload a valid CSV file.')

    return render_template('manager/upload.html', manager=manager)


@bp.route('/upload/image', methods=['POST'])
def upload_image():
    manager_id = session.get('manager_id')
    if manager_id is None:
        return jsonify({'error': 'Unauthorized'}), 401

    file = request.files.get('file')
    if not file or file.filename == '':
        return jsonify({'error': 'No file provided'}), 400

    success, message = process_image_upload(file, manager_id, current_app.config['IMAGE_FOLDER'])
    if success:
        return jsonify({'success': True, 'message': message})
    return jsonify({'error': message}), 400


# ── Add Product ───────────────────────────────────────────────────────────────
@bp.route('/product/add', methods=('GET', 'POST'))
def add_product():
    manager_id, guard = _require_manager()
    if guard: return guard

    db      = get_db()
    manager = db.execute('SELECT * FROM tbl_managers WHERE manager_id = ?', (manager_id,)).fetchone()
    next_sku = generate_next_sku(db, manager_id)

    if request.method == 'POST':
        name        = request.form.get('name', '').strip()
        description = request.form.get('description', '')
        price_val   = request.form.get('price_inr', '').strip()
        price_inr   = float(price_val) if price_val else 0.0
        stock_qty   = request.form.get('stock_qty', 0)
        status      = request.form.get('status', 'Active')

        if not name:
            flash("Product Name is required.", "error")
            return render_template('manager/add_product.html', manager=manager, next_sku=next_sku)

        if manager['price_mandatory'] == 1 and not price_val:
            flash("Price is required.", "error")
            return render_template('manager/add_product.html', manager=manager, next_sku=next_sku)

        image_path = 'placeholder.jpg'
        file = request.files.get('image')
        if file and file.filename:
            img_res, err = upload_product_image_file(
                file.stream, manager_id, next_sku,
                current_app.config['IMAGE_FOLDER'],
                shop_slug=manager['shop_slug']
            )
            if img_res:
                image_path = img_res
            elif err:
                flash(f"Warning: Image upload issue: {err}", "error")

        try:
            db.execute(
                "INSERT INTO tbl_products (manager_id, sku, name, description, price_inr, stock_qty, status, image_path) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (manager_id, next_sku, name, description, price_inr, stock_qty, status, image_path)
            )
            db.commit()
            flash(f'Product added successfully with permanent SKU: {next_sku}!', 'success')
            return redirect(url_for('manager.dashboard'))
        except (sqlite3.IntegrityError, ValueError):
            flash(f"Error creating product with SKU '{next_sku}'. Please try again.", 'error')

    return render_template('manager/add_product.html', manager=manager, next_sku=next_sku)


# ── Edit Product ──────────────────────────────────────────────────────────────
@bp.route('/product/<int:product_id>/edit', methods=('GET', 'POST'))
def edit_product(product_id):
    manager_id, guard = _require_manager()
    if guard: return guard

    db      = get_db()
    manager = db.execute('SELECT * FROM tbl_managers WHERE manager_id = ?', (manager_id,)).fetchone()
    product = db.execute(
        'SELECT * FROM tbl_products WHERE product_id = ? AND manager_id = ?', (product_id, manager_id)
    ).fetchone()

    if not product:
        flash('Product not found.', 'error')
        return redirect(url_for('manager.dashboard'))

    if request.method == 'POST':
        sku               = product['sku']
        name              = request.form.get('name', '').strip()
        description       = request.form.get('description', '')
        price_val         = request.form.get('price_inr', '').strip()
        price_inr         = float(price_val) if price_val else 0.0
        stock_qty         = int(request.form.get('stock_qty', 0))
        requested_status  = request.form.get('status', 'Active')

        if not name:
            flash("Product Name is required.", "error")
            return render_template('manager/edit_product.html', manager=manager, product=product)

        if manager['price_mandatory'] == 1 and not price_val:
            flash("Price is required.", "error")
            return render_template('manager/edit_product.html', manager=manager, product=product)

        status     = 'Suspended' if product['status'] == 'Suspended' else requested_status
        image_path = product['image_path']

        file = request.files.get('image')
        if file and file.filename:
            img_res, err = upload_product_image_file(
                file.stream, manager_id, sku,
                current_app.config['IMAGE_FOLDER'],
                shop_slug=manager['shop_slug']
            )
            if img_res:
                image_path = img_res
            elif err:
                flash(f"Warning: Image upload issue: {err}", "error")

        try:
            db.execute(
                "UPDATE tbl_products SET sku=?, name=?, description=?, price_inr=?, "
                "stock_qty=?, status=?, image_path=? WHERE product_id=? AND manager_id=?",
                (sku, name, description, price_inr, stock_qty, status, image_path, product_id, manager_id)
            )
            db.commit()
            flash('Product updated successfully!', 'success')
            return redirect(url_for('manager.dashboard'))
        except (sqlite3.IntegrityError, ValueError):
            flash(f"A product with SKU '{sku}' already exists.", 'error')

    return render_template('manager/edit_product.html', manager=manager, product=product)


# ── Toggle Product Status ─────────────────────────────────────────────────────
@bp.route('/product/<int:product_id>/toggle_status', methods=['POST'])
def toggle_product_status(product_id):
    manager_id, guard = _require_manager()
    if guard: return guard

    db      = get_db()
    product = db.execute(
        'SELECT status FROM tbl_products WHERE product_id = ? AND manager_id = ?', (product_id, manager_id)
    ).fetchone()

    if not product:
        flash('Product not found.', 'error')
    elif product['status'] == 'Suspended':
        flash('This product is suspended by Super Admin and cannot be activated.', 'error')
    else:
        new_status = 'Inactive' if product['status'] == 'Active' else 'Active'
        db.execute(
            'UPDATE tbl_products SET status = ? WHERE product_id = ? AND manager_id = ?',
            (new_status, product_id, manager_id)
        )
        db.commit()
        flash(f'Product status changed to {new_status}.', 'success')

    return redirect(url_for('manager.dashboard'))


# ── Delete Product ────────────────────────────────────────────────────────────
@bp.route('/product/<int:product_id>/delete', methods=['POST'])
def delete_product(product_id):
    manager_id, guard = _require_manager()
    if guard: return guard

    db      = get_db()
    product = db.execute(
        'SELECT * FROM tbl_products WHERE product_id = ? AND manager_id = ?', (product_id, manager_id)
    ).fetchone()

    if not product:
        flash('Product not found.', 'error')
        return redirect(url_for('manager.dashboard'))

    # Remove local image file if not a Cloudinary URL or placeholder
    ip = product['image_path']
    if ip and ip != 'placeholder.jpg' and not ip.startswith('http'):
        try:
            os.remove(os.path.join(current_app.config['IMAGE_FOLDER'], ip))
        except OSError:
            pass

    db.execute('DELETE FROM tbl_products WHERE product_id = ? AND manager_id = ?', (product_id, manager_id))
    db.commit()
    flash(f'Product "{product["name"]}" deleted successfully.', 'success')
    return redirect(url_for('manager.dashboard'))


# ── Orders ─────────────────────────────────────────────────────────────────────
@bp.route('/orders')
def orders():
    manager_id, guard = _require_manager()
    if guard: return guard

    db      = get_db()
    manager = db.execute('SELECT * FROM tbl_managers WHERE manager_id = ?', (manager_id,)).fetchone()

    order_rows = db.execute(
        'SELECT * FROM tbl_orders WHERE manager_id = ? ORDER BY created_at DESC', (manager_id,)
    ).fetchall()

    if not order_rows:
        return render_template('manager/orders.html', manager=manager, orders_data=[])

    # ── Eliminate N+1: single batch fetch for ALL order items ──────────────────
    order_ids    = tuple(o['order_id'] for o in order_rows)  # tuple required by LibSQL
    placeholders = ', '.join(['?'] * len(order_ids))
    all_items = db.execute(f'''
        SELECT oi.order_id, oi.product_id, oi.quantity, oi.price_at_time, p.name, p.packed_qty, p.stock_qty
        FROM tbl_order_items oi
        JOIN tbl_products p ON oi.product_id = p.product_id
        WHERE oi.order_id IN ({placeholders})
    ''', order_ids).fetchall()

    # Group items by order_id
    items_by_order = defaultdict(list)
    for item in all_items:
        items_by_order[item['order_id']].append(item)

    shop_name   = manager['shop_name']
    orders_data = []
    for o in order_rows:
        items = items_by_order[o['order_id']]

        phone_clean = re.sub(r'\D', '', o['customer_phone'] or '')
        if phone_clean and not phone_clean.startswith('91') and len(phone_clean) == 10:
            phone_clean = '91' + phone_clean

        item_names = ", ".join(f"{i['name']} (x{i['quantity']})" for i in items)

        wa_packed    = f"Hello {o['customer_name']}, your order #{o['order_id']} ({item_names}) at {shop_name} has been PACKED and is ready for dispatch! Total: \u20b9{o['total_amount']}."
        wa_delivered = f"Hello {o['customer_name']}, your order #{o['order_id']} at {shop_name} has been DELIVERED! Thank you for shopping with us."
        wa_cancelled = f"Hello {o['customer_name']}, your order #{o['order_id']} at {shop_name} has been CANCELLED and items have been unpacked back into stock."

        # Build WhatsApp links inline (avoid closure-in-loop bug)
        def _wa(text, pc=phone_clean):
            return f"https://wa.me/{pc}?text={urllib.parse.quote(text)}" if pc else "#"

        orders_data.append({
            'order':             o,
            'order_items':       items,
            'wa_packed_link':    _wa(wa_packed),
            'wa_delivered_link': _wa(wa_delivered),
            'wa_cancelled_link': _wa(wa_cancelled),
        })

    return render_template('manager/orders.html', manager=manager, orders_data=orders_data)



# ── Update Order Status ───────────────────────────────────────────────────────
@bp.route('/update_order_status', methods=['POST'])
def update_order_status():
    manager_id, guard = _require_manager()
    if guard: return guard

    order_id       = request.form.get('order_id')
    new_status     = request.form.get('status')
    payment_status = request.form.get('payment_status')

    db    = get_db()
    order = db.execute(
        'SELECT * FROM tbl_orders WHERE order_id = ? AND manager_id = ?', (order_id, manager_id)
    ).fetchone()

    if order:
        old_status = order['status']
        items      = db.execute(
            'SELECT product_id, quantity FROM tbl_order_items WHERE order_id = ?', (order_id,)
        ).fetchall()

        # ── State transition stock engine ──────────────────────────────────
        # 1. Moving INTO Packed from (Pending or Cancelled)
        if old_status in ('Pending', 'Cancelled') and new_status == 'Packed':
            for item in items:
                db.execute(
                    'UPDATE tbl_products SET stock_qty = MAX(0, stock_qty - ?), packed_qty = packed_qty + ? WHERE product_id = ?',
                    (item['quantity'], item['quantity'], item['product_id'])
                )

        # 2. Moving OUT of Packed to (Pending or Cancelled) -> Unpack & Restock
        elif old_status == 'Packed' and new_status in ('Pending', 'Cancelled'):
            for item in items:
                db.execute(
                    'UPDATE tbl_products SET stock_qty = stock_qty + ?, packed_qty = MAX(0, packed_qty - ?) WHERE product_id = ?',
                    (item['quantity'], item['quantity'], item['product_id'])
                )

        # 3. Moving from Packed to (Delivered or Completed) -> Dispatch from box
        elif old_status == 'Packed' and new_status in ('Delivered', 'Completed'):
            for item in items:
                db.execute(
                    'UPDATE tbl_products SET packed_qty = MAX(0, packed_qty - ?) WHERE product_id = ?',
                    (item['quantity'], item['product_id'])
                )

        # 4. Moving from (Delivered or Completed) to Cancelled -> Restock
        elif old_status in ('Delivered', 'Completed') and new_status == 'Cancelled':
            for item in items:
                db.execute(
                    'UPDATE tbl_products SET stock_qty = stock_qty + ? WHERE product_id = ?',
                    (item['quantity'], item['product_id'])
                )

        # 5. Direct Pending -> Delivered/Completed (bypass Packed step)
        elif old_status in ('Pending', 'Cancelled') and new_status in ('Delivered', 'Completed'):
            for item in items:
                db.execute(
                    'UPDATE tbl_products SET stock_qty = MAX(0, stock_qty - ?) WHERE product_id = ?',
                    (item['quantity'], item['product_id'])
                )

        db.execute('UPDATE tbl_orders SET status = ?, payment_status = ? WHERE order_id = ?',
                   (new_status, payment_status, order_id))
        db.commit()

        if new_status == 'Cancelled':
            flash(f'Order #{order_id} marked as Cancelled. Products unpacked and restocked back to shelf.', 'success')
        elif new_status == 'Packed':
            flash(f'Order #{order_id} marked as Packed. Stock deducted from shelf and reserved into packed dispatch.', 'success')
        else:
            flash(f'Order #{order_id} status updated to {new_status}.', 'success')
    else:
        flash('Order not found.', 'error')

    return redirect(url_for('manager.orders'))


# ── Staff Management ──────────────────────────────────────────────────────────
@bp.route('/staff')
def staff():
    manager_id, guard = _require_manager()
    if guard: return guard

    db      = get_db()
    manager = db.execute('SELECT * FROM tbl_managers WHERE manager_id = ?', (manager_id,)).fetchone()
    staff_members = db.execute(
        'SELECT * FROM tbl_staff_accounts WHERE manager_id = ? ORDER BY created_at DESC', (manager_id,)
    ).fetchall()

    return render_template('manager/staff.html', manager=manager, staff_members=staff_members)


@bp.route('/staff/add', methods=['POST'])
def add_staff():
    manager_id, guard = _require_manager()
    if guard: return guard

    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()
    role     = request.form.get('role', 'Order_Only').strip()

    if not username or not password:
        flash('Username and password are required.', 'error')
        return redirect(url_for('manager.staff'))

    db = get_db()
    try:
        db.execute(
            'INSERT INTO tbl_staff_accounts (manager_id, username, password_hash, role) VALUES (?, ?, ?, ?)',
            (manager_id, username, generate_password_hash(password), role)
        )
        db.commit()
        flash(f'Staff account "{username}" created with role {role}.', 'success')
    except (sqlite3.IntegrityError, ValueError):
        flash(f'Username "{username}" is already taken.', 'error')

    return redirect(url_for('manager.staff'))


@bp.route('/staff/<int:staff_id>/delete', methods=['POST'])
def delete_staff(staff_id):
    manager_id, guard = _require_manager()
    if guard: return guard

    db = get_db()
    db.execute('DELETE FROM tbl_staff_accounts WHERE staff_id = ? AND manager_id = ?', (staff_id, manager_id))
    db.commit()
    flash('Staff account deleted.', 'success')
    return redirect(url_for('manager.staff'))


@bp.route('/staff/<int:staff_id>/toggle_status', methods=['POST'])
def toggle_staff_status(staff_id):
    manager_id, guard = _require_manager()
    if guard: return guard

    db    = get_db()
    staff = db.execute(
        'SELECT * FROM tbl_staff_accounts WHERE staff_id = ? AND manager_id = ?', (staff_id, manager_id)
    ).fetchone()

    if not staff:
        flash('Staff account not found.', 'error')
        return redirect(url_for('manager.staff'))

    current_active = 1 if (staff['is_active'] is None or staff['is_active'] == 1) else 0
    new_status     = 0 if current_active == 1 else 1
    db.execute(
        'UPDATE tbl_staff_accounts SET is_active = ? WHERE staff_id = ? AND manager_id = ?',
        (new_status, staff_id, manager_id)
    )
    db.commit()
    flash(f'Staff account "{staff["username"]}" has been {"enabled" if new_status else "disabled"}.', 'success')
    return redirect(url_for('manager.staff'))


@bp.route('/staff/<int:staff_id>/update_role', methods=['POST'])
def update_staff_role(staff_id):
    manager_id, guard = _require_manager()
    if guard: return guard
    if session.get('is_staff'):
        return redirect(url_for('manager.login'))

    new_role = request.form.get('role', '').strip()
    if new_role not in ('Order_Only', 'Stock_Only', 'Full'):
        flash('Invalid role permission selected.', 'error')
        return redirect(url_for('manager.staff'))

    db    = get_db()
    staff = db.execute(
        'SELECT * FROM tbl_staff_accounts WHERE staff_id = ? AND manager_id = ?', (staff_id, manager_id)
    ).fetchone()

    if not staff:
        flash('Staff account not found.', 'error')
        return redirect(url_for('manager.staff'))

    db.execute(
        'UPDATE tbl_staff_accounts SET role = ? WHERE staff_id = ? AND manager_id = ?',
        (new_role, staff_id, manager_id)
    )
    db.commit()
    role_labels = {'Order_Only': 'Order Manager (📦)', 'Stock_Only': 'Stock Manager (📊)', 'Full': 'Full Manager Assistant (⭐)'}
    flash(f'Role for "{staff["username"]}" updated to {role_labels.get(new_role, new_role)}.', 'success')
    return redirect(url_for('manager.staff'))


# ── Reports ───────────────────────────────────────────────────────────────────
@bp.route('/reports')
def reports():
    manager_id, guard = _require_manager()
    if guard: return guard

    db      = get_db()
    manager = db.execute('SELECT * FROM tbl_managers WHERE manager_id = ?', (manager_id,)).fetchone()

    # KPIs — combined into one query
    kpis = db.execute('''
        SELECT
            SUM(CASE WHEN status != 'Cancelled' THEN total_amount ELSE 0 END) AS total_revenue,
            COUNT(*) AS total_orders
        FROM tbl_orders WHERE manager_id = ?
    ''', (manager_id,)).fetchone()

    total_revenue = kpis['total_revenue'] or 0
    total_orders  = kpis['total_orders']  or 0

    top_products = db.execute('''
        SELECT p.name, SUM(oi.quantity) as total_sold
        FROM tbl_order_items oi
        JOIN tbl_orders o ON oi.order_id = o.order_id
        JOIN tbl_products p ON oi.product_id = p.product_id
        WHERE o.manager_id = ? AND o.status != 'Cancelled'
        GROUP BY oi.product_id
        ORDER BY total_sold DESC LIMIT 5
    ''', (manager_id,)).fetchall()

    top_views = db.execute('''
        SELECT p.name, COUNT(v.view_id) as view_count
        FROM tbl_product_views v
        JOIN tbl_products p ON v.product_id = p.product_id
        WHERE p.manager_id = ?
        GROUP BY p.product_id
        ORDER BY view_count DESC LIMIT 5
    ''', (manager_id,)).fetchall()

    product_performance = db.execute('''
        SELECT
            p.product_id, p.name, p.image_path,
            (SELECT COUNT(*) FROM tbl_product_views WHERE product_id = p.product_id) as total_views,
            COALESCE((SELECT SUM(quantity) FROM tbl_cart_items WHERE product_id = p.product_id), 0) as in_carts,
            COALESCE((
                SELECT SUM(oi.quantity) FROM tbl_order_items oi
                JOIN tbl_orders o ON oi.order_id = o.order_id
                WHERE oi.product_id = p.product_id AND o.status != 'Cancelled'
            ), 0) as total_ordered,
            COALESCE((
                SELECT SUM(oi.quantity) FROM tbl_order_items oi
                JOIN tbl_orders o ON oi.order_id = o.order_id
                WHERE oi.product_id = p.product_id AND o.status = 'Completed'
            ), 0) as total_completed
        FROM tbl_products p
        WHERE p.manager_id = ?
        ORDER BY total_views DESC, total_ordered DESC
    ''', (manager_id,)).fetchall()

    return render_template(
        'manager/reports.html',
        manager=manager,
        total_revenue=total_revenue,
        total_orders=total_orders,
        top_products=top_products,
        top_views=top_views,
        product_performance=product_performance,
    )


# ── Bulk Stock ────────────────────────────────────────────────────────────────
@bp.route('/bulk_stock', methods=['GET', 'POST'])
def bulk_stock():
    manager_id, guard = _require_manager()
    if guard: return guard

    db      = get_db()
    manager = db.execute('SELECT * FROM tbl_managers WHERE manager_id = ?', (manager_id,)).fetchone()

    if request.method == 'POST':
        for key, value in request.form.items():
            if key.startswith('stock_'):
                product_id = key.replace('stock_', '')
                try:
                    new_qty = int(value)
                    if new_qty >= 0:
                        db.execute(
                            'UPDATE tbl_products SET stock_qty = ? WHERE product_id = ? AND manager_id = ?',
                            (new_qty, product_id, manager_id)
                        )
                except ValueError:
                    pass
        db.commit()
        flash('Inventory updated successfully.', 'success')
        return redirect(url_for('manager.bulk_stock'))

    products = db.execute(
        'SELECT product_id, sku, name, stock_qty, status FROM tbl_products WHERE manager_id = ? ORDER BY product_id DESC',
        (manager_id,)
    ).fetchall()
    return render_template('manager/bulk_stock.html', manager=manager, products=products)


# ── Download Stock Report (CSV) ───────────────────────────────────────────────
@bp.route('/download_stock_report')
def download_stock_report():
    manager_id, guard = _require_manager()
    if guard: return guard

    db      = get_db()
    manager  = db.execute('SELECT shop_name, shop_slug FROM tbl_managers WHERE manager_id = ?', (manager_id,)).fetchone()
    products = db.execute(
        'SELECT sku, name, price_inr, stock_qty, status FROM tbl_products WHERE manager_id = ? ORDER BY name ASC',
        (manager_id,)
    ).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['SKU', 'Product Name', 'Price (INR)', 'Stock Quantity', 'Status'])
    for p in products:
        writer.writerow([p['sku'], p['name'], p['price_inr'], p['stock_qty'], p['status']])

    resp = Response(output.getvalue(), mimetype='text/csv')
    resp.headers['Content-Disposition'] = f"attachment; filename={manager['shop_slug']}_stock_report.csv"
    return resp


# ── Quick Adjust Stock (AJAX delta) ──────────────────────────────────────────
@bp.route('/adjust_stock', methods=['POST'])
def adjust_stock():
    """AJAX endpoint: apply a +/- delta to product stock_qty.
    Body JSON: { product_id: int, delta: int }
    Response JSON: { success: bool, new_qty: int, name: str } or { error: str }
    """
    manager_id = session.get('manager_id')
    if manager_id is None:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'Invalid request'}), 400

    try:
        product_id = int(data.get('product_id', 0))
        delta      = int(data.get('delta', 0))
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid parameters'}), 400

    if delta == 0:
        return jsonify({'error': 'Delta cannot be zero'}), 400

    db      = get_db()
    product = db.execute(
        'SELECT product_id, name, stock_qty FROM tbl_products WHERE product_id = ? AND manager_id = ?',
        (product_id, manager_id)
    ).fetchone()

    if not product:
        return jsonify({'error': 'Product not found'}), 404

    # Apply delta, clamp at 0 (stock cannot go negative)
    new_qty = max(0, product['stock_qty'] + delta)
    db.execute(
        'UPDATE tbl_products SET stock_qty = ? WHERE product_id = ? AND manager_id = ?',
        (new_qty, product_id, manager_id)
    )
    db.commit()

    return jsonify({
        'success': True,
        'new_qty': new_qty,
        'name':    product['name'],
        'delta':   delta,
    })


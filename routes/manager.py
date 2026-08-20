from flask import Blueprint, render_template, request, redirect, url_for, session, current_app, flash
from werkzeug.security import generate_password_hash, check_password_hash
from database import get_db
import sqlite3
import re
import os

bp = Blueprint('manager', __name__, url_prefix='/manager')

@bp.before_request
def check_staff_rbac():
    if session.get('is_staff'):
        role = session.get('staff_role', 'Order_Only')
        endpoint = request.endpoint
        
        # Order_Only staff restricted endpoints
        if role == 'Order_Only':
            allowed = ['manager.orders', 'manager.update_order_status', 'manager.logout', 'manager.login', 'manager.staff_login', 'manager.staff_shop_qr']
            if endpoint and endpoint.startswith('manager.') and endpoint not in allowed:
                flash('Access Restricted: Order Manager accounts are restricted to order operations.', 'error')
                return redirect(url_for('manager.orders'))
                
        # Stock_Only staff restricted endpoints (no dashboard access)
        elif role == 'Stock_Only':
            allowed = ['manager.bulk_stock', 'manager.logout', 'manager.login', 'manager.staff_login', 'manager.staff_shop_qr']
            if endpoint and endpoint.startswith('manager.') and endpoint not in allowed:
                flash('Access Restricted: Stock Manager accounts are restricted to stock management and cannot access the dashboard.', 'error')
                return redirect(url_for('manager.bulk_stock'))

        # Full assistant staff restricted from sensitive admin/staff/financial reports
        elif role == 'Full':
            disallowed = ['manager.staff', 'manager.add_staff', 'manager.delete_staff', 'manager.toggle_staff_status', 'manager.update_staff_role', 'manager.reports']
            if endpoint and endpoint in disallowed:
                flash('Access Restricted: Assistant accounts cannot manage staff or view financial reports.', 'error')
                return redirect(url_for('manager.dashboard'))

@bp.context_processor
def inject_staff_shop_context():
    manager_id = session.get('manager_id')
    if manager_id:
        db = get_db()
        current_shop = db.execute(
            'SELECT manager_id, shop_name, shop_slug, email FROM tbl_managers WHERE manager_id = ?', (manager_id,)
        ).fetchone()

        if current_shop:
            from services.qr_service import generate_shop_qr
            qr_file = f"{current_shop['shop_slug']}.png"
            qr_path = os.path.join(current_app.config['QR_FOLDER'], qr_file)
            if not os.path.exists(qr_path):
                try:
                    generate_shop_qr(current_shop['shop_slug'], current_app.config['QR_FOLDER'])
                except Exception:
                    pass

            if session.get('is_staff'):
                staff_user = session.get('staff_username', 'Staff Member')
                staff_role = session.get('staff_role', 'Order_Only')
                role_label = 'Order Manager' if staff_role == 'Order_Only' else ('Stock Manager' if staff_role == 'Stock_Only' else 'Full Assistant')
                role_icon = '📦' if staff_role == 'Order_Only' else ('📊' if staff_role == 'Stock_Only' else '⭐')
                badge_class = 'bg-amber-100 text-amber-900 border-amber-300' if staff_role == 'Order_Only' else ('bg-blue-100 text-blue-900 border-blue-300' if staff_role == 'Stock_Only' else 'bg-purple-100 text-purple-900 border-purple-300')
                logged_account = {
                    'is_staff': True,
                    'username': staff_user,
                    'display_name': staff_user,
                    'email_or_user': f"@{staff_user}",
                    'role_label': role_label,
                    'role_code': staff_role,
                    'role_icon': role_icon,
                    'badge_class': badge_class,
                    'shop_name': current_shop['shop_name'],
                    'shop_slug': current_shop['shop_slug'],
                }
            else:
                logged_account = {
                    'is_staff': False,
                    'username': current_shop['shop_name'],
                    'display_name': current_shop['shop_name'],
                    'email_or_user': current_shop['email'],
                    'role_label': 'Store Owner / Manager',
                    'role_code': 'Owner',
                    'role_icon': '👑',
                    'badge_class': 'bg-emerald-100 text-emerald-900 border-emerald-300',
                    'shop_name': current_shop['shop_name'],
                    'shop_slug': current_shop['shop_slug'],
                }

            return {
                'current_shop': current_shop,
                'current_shop_qr': qr_file,
                'logged_account': logged_account
            }
    return {'current_shop': None, 'current_shop_qr': None, 'logged_account': None}

@bp.route('/register', methods=('GET', 'POST'))
def register():
    if request.method == 'POST':
        shop_name = request.form.get('shop_name', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '').strip()
        phone_number = request.form.get('phone_number', '').strip()
        
        # Generate slug from shop name
        shop_slug = re.sub(r'[^a-z0-9]+', '-', shop_name.lower()).strip('-')

        db = get_db()
        error = None

        if not shop_name or not email or not password or not phone_number:
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
            try:
                db.execute(
                    "INSERT INTO tbl_managers (shop_name, shop_slug, email, password_hash, phone_number) VALUES (?, ?, ?, ?, ?)",
                    (shop_name, shop_slug, email, generate_password_hash(password), phone_number),
                )
                db.commit()
                # Generate QR Code for this shop
                from services.qr_service import generate_shop_qr
                generate_shop_qr(shop_slug, current_app.config['QR_FOLDER'])

            except (sqlite3.IntegrityError, ValueError) as e:
                if 'shop_slug' in str(e):
                    error = f"Shop name '{shop_name}' is already taken."
                else:
                    error = f"Email '{email}' is already registered."
            else:
                flash('Registration successful! Please log in.', 'success')
                return redirect(url_for('manager.login'))

        flash(error)

    return render_template('manager/register.html')

@bp.route('/login', methods=('GET', 'POST'))
def login():
    if request.method == 'POST':
        login_input = request.form.get('email', '').strip()
        password = request.form.get('password', '').strip()
        db = get_db()
        error = None

        # 1. Try Manager Account by Email
        manager = db.execute(
            'SELECT * FROM tbl_managers WHERE email = ?', (login_input,)
        ).fetchone()

        if manager:
            if check_password_hash(manager['password_hash'], password):
                if manager['is_suspended'] == 1:
                    error = 'Your account has been suspended by the administrator.'
                else:
                    session.clear()
                    session['manager_id'] = manager['manager_id']
                    session['is_staff'] = False
                    session['staff_role'] = 'Full'
                    return redirect(url_for('manager.dashboard'))
            else:
                error = 'Incorrect password.'
        else:
            # 2. Try Staff Sub-Account by Username
            staff = db.execute(
                'SELECT * FROM tbl_staff_accounts WHERE username = ?', (login_input,)
            ).fetchone()

            if staff and check_password_hash(staff['password_hash'], password):
                if 'is_active' in staff.keys() and staff['is_active'] == 0:
                    error = f'Staff account "{staff["username"]}" has been disabled by store manager.'
                else:
                    manager = db.execute(
                        'SELECT * FROM tbl_managers WHERE manager_id = ?', (staff['manager_id'],)
                    ).fetchone()
                    if manager and manager['is_suspended'] == 1:
                        error = 'Manager account is suspended.'
                    else:
                        from datetime import datetime
                        now_str = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
                        db.execute('UPDATE tbl_staff_accounts SET last_active = ? WHERE staff_id = ?', (now_str, staff['staff_id']))
                        db.commit()

                        session.clear()
                        session['manager_id'] = staff['manager_id']
                        session['staff_id'] = staff['staff_id']
                        session['is_staff'] = True
                        session['staff_role'] = staff['role']
                        session['staff_username'] = staff['username']

                        if staff['role'] == 'Order_Only':
                            return redirect(url_for('manager.orders'))
                        elif staff['role'] == 'Stock_Only':
                            return redirect(url_for('manager.bulk_stock'))
                        else:
                            return redirect(url_for('manager.dashboard'))
            else:
                error = 'Incorrect email/username or password.'

        flash(error)

    return render_template('manager/login.html')

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
                    from datetime import datetime
                    now_str = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
                    db.execute('UPDATE tbl_staff_accounts SET last_active = ? WHERE staff_id = ?', (now_str, staff['staff_id']))
                    db.commit()

                    session.clear()
                    session['manager_id'] = staff['manager_id']
                    session['staff_id'] = staff['staff_id']
                    session['is_staff'] = True
                    session['staff_role'] = staff['role']
                    session['staff_username'] = staff['username']
                    session['staff_shop_slug'] = manager['shop_slug']

                    flash(f'Logged in as staff ({staff["username"]}) for {manager["shop_name"]}.', 'success')
                    if staff['role'] == 'Order_Only':
                        return redirect(url_for('manager.orders'))
                    elif staff['role'] == 'Stock_Only':
                        return redirect(url_for('manager.bulk_stock'))
                    else:
                        return redirect(url_for('manager.dashboard'))
            else:
                error = f'Incorrect staff username or password for {manager["shop_name"]}.'

        flash(error, 'error')

    return render_template('manager/staff_login.html', manager=manager)


@bp.route('/logout')
def logout():
    # If a staff member logs out, redirect them back to their shop's staff login page
    shop_slug = session.get('staff_shop_slug') if session.get('is_staff') else None
    session.clear()
    if shop_slug:
        return redirect(url_for('manager.staff_login', shop_slug=shop_slug))
    return redirect(url_for('manager.login'))

@bp.route('/dashboard')
def dashboard():
    manager_id = session.get('manager_id')
    if manager_id is None:
        return redirect(url_for('manager.login'))
        
    db = get_db()
    manager = db.execute('SELECT * FROM tbl_managers WHERE manager_id = ?', (manager_id,)).fetchone()
    if manager is None:
        session.clear()
        return redirect(url_for('manager.login'))

    products = db.execute('SELECT * FROM tbl_products WHERE manager_id = ? ORDER BY product_id DESC', (manager_id,)).fetchall()
    
    from services.qr_service import get_shop_base_url, get_local_ip, generate_shop_qr, parse_user_agent
    shop_base_url = get_shop_base_url(request)
    shop_url = f"{shop_base_url}/shop/{manager['shop_slug']}"
    scan_url = f"{shop_base_url}/scan/{manager['shop_slug']}"

    # Ensure QR file exists on disk
    qr_filename = f"{manager['shop_slug']}.png"
    qr_filepath = os.path.join(current_app.config['QR_FOLDER'], qr_filename)
    if not os.path.exists(qr_filepath):
        generate_shop_qr(manager['shop_slug'], current_app.config['QR_FOLDER'], shop_base_url)

    # Analytics queries
    from datetime import datetime
    now_str = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    total_scans = db.execute('SELECT COUNT(*) as count FROM tbl_visitor_sessions WHERE manager_id = ?', (manager_id,)).fetchone()['count']
    active_visitors = db.execute('SELECT COUNT(*) as count FROM tbl_visitor_sessions WHERE manager_id = ? AND expires_at > ?', (manager_id, now_str)).fetchone()['count']
    today_scans = db.execute("SELECT COUNT(*) as count FROM tbl_visitor_sessions WHERE manager_id = ? AND DATE(created_at) = DATE('now')", (manager_id,)).fetchone()['count']
    
    raw_scans = db.execute(
        '''SELECT visit_id, session_token, ip_address, user_agent, created_at, expires_at,
                  CASE WHEN expires_at > ? THEN 'Active' ELSE 'Expired' END as status
           FROM tbl_visitor_sessions
           WHERE manager_id = ?
           ORDER BY visit_id DESC LIMIT 15''',
        (now_str, manager_id)
    ).fetchall()

    recent_scans = []
    for s in raw_scans:
        recent_scans.append({
            'visit_id': s['visit_id'],
            'session_token': s['session_token'][:8] + '...' if s['session_token'] else 'N/A',
            'ip_address': s['ip_address'] or '127.0.0.1',
            'device_info': parse_user_agent(s['user_agent']),
            'user_agent_raw': s['user_agent'] or '',
            'created_at': s['created_at'],
            'status': s['status']
        })

    # Low stock alerts
    low_stock_products = db.execute(
        "SELECT name, stock_qty FROM tbl_products WHERE manager_id = ? AND stock_qty <= 5 AND status = 'Active' ORDER BY stock_qty ASC",
        (manager_id,)
    ).fetchall()

    # Pending orders count
    pending_orders = db.execute(
        "SELECT COUNT(*) as cnt FROM tbl_orders WHERE manager_id = ? AND status = 'Pending'",
        (manager_id,)
    ).fetchone()['cnt']

    local_ip = get_local_ip()

    return render_template('manager/dashboard.html',
                           manager=manager,
                           products=products,
                           total_scans=total_scans,
                           active_visitors=active_visitors,
                           today_scans=today_scans,
                           recent_scans=recent_scans,
                           low_stock_products=low_stock_products,
                           pending_orders=pending_orders,
                           local_ip=local_ip,
                           shop_base_url=shop_base_url,
                           shop_url=shop_url,
                           scan_url=scan_url)




@bp.route('/product/<int:product_id>/quick_edit', methods=['POST'])
def quick_edit_product(product_id):
    manager_id = session.get('manager_id')
    if manager_id is None:
        return redirect(url_for('manager.login'))

    price_inr = request.form.get('price_inr', '').strip()
    stock_qty  = request.form.get('stock_qty', '').strip()

    db = get_db()
    # make sure this product belongs to this manager
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

@bp.route('/toggle_whatsapp', methods=['POST'])
def toggle_whatsapp():
    manager_id = session.get('manager_id')
    if manager_id is None:
        return redirect(url_for('manager.login'))
        
    db = get_db()
    manager = db.execute('SELECT whatsapp_orders_enabled FROM tbl_managers WHERE manager_id = ?', (manager_id,)).fetchone()
    if manager:
        new_val = 0 if manager['whatsapp_orders_enabled'] == 1 else 1
        db.execute('UPDATE tbl_managers SET whatsapp_orders_enabled = ? WHERE manager_id = ?', (new_val, manager_id))
        db.commit()
        flash('WhatsApp Order settings updated.')
    return redirect(url_for('manager.dashboard'))

@bp.route('/toggle_price', methods=['POST'])
def toggle_price():
    manager_id = session.get('manager_id')
    if manager_id is None:
        return redirect(url_for('manager.login'))
        
    db = get_db()
    manager = db.execute('SELECT price_mandatory FROM tbl_managers WHERE manager_id = ?', (manager_id,)).fetchone()
    if manager:
        new_val = 0 if manager['price_mandatory'] == 1 else 1
        db.execute('UPDATE tbl_managers SET price_mandatory = ? WHERE manager_id = ?', (new_val, manager_id))
        db.commit()
        flash('Price mandatory setting updated.')
    return redirect(url_for('manager.dashboard'))

@bp.route('/toggle_show_price', methods=['POST'])
def toggle_show_price():
    manager_id = session.get('manager_id')
    if manager_id is None:
        return redirect(url_for('manager.login'))
        
    db = get_db()
    manager = db.execute('SELECT show_price FROM tbl_managers WHERE manager_id = ?', (manager_id,)).fetchone()
    if manager:
        new_val = 0 if manager['show_price'] == 1 else 1
        db.execute('UPDATE tbl_managers SET show_price = ? WHERE manager_id = ?', (new_val, manager_id))
        db.commit()
        flash('Price visibility updated.')
    return redirect(url_for('manager.dashboard'))

from services.upload_service import process_csv_upload, process_image_upload
from flask import jsonify

@bp.route('/upload', methods=('GET', 'POST'))
def upload():
    manager_id = session.get('manager_id')
    if manager_id is None:
        return redirect(url_for('manager.login'))
        
    db = get_db()
    manager = db.execute('SELECT * FROM tbl_managers WHERE manager_id = ?', (manager_id,)).fetchone()
    
    if manager['bulk_upload_enabled'] == 0:
        flash('Bulk upload is disabled for your account. Please contact the administrator.')
        return redirect(url_for('manager.dashboard'))
        
    if request.method == 'POST':
        if 'csv_file' not in request.files:
            flash('No file part')
            return redirect(request.url)
            
        file = request.files['csv_file']
        if file.filename == '':
            flash('No selected file')
            return redirect(request.url)
            
        if file and file.filename.endswith('.csv'):
            try:
                count = process_csv_upload(file, manager_id)
                flash(f'Successfully imported/updated {count} products.')
            except Exception as e:
                flash(f'Error processing CSV: {str(e)}')
            return redirect(url_for('manager.dashboard'))
        else:
            flash('Please upload a valid CSV file.')
            
    return render_template('manager/upload.html', manager=manager)

@bp.route('/upload/image', methods=['POST'])
def upload_image():
    manager_id = session.get('manager_id')
    if manager_id is None:
        return jsonify({'error': 'Unauthorized'}), 401
        
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
        
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
        
    success, message = process_image_upload(file, manager_id, current_app.config['IMAGE_FOLDER'])
    if success:
        return jsonify({'success': True, 'message': message})
    else:
        return jsonify({'error': message}), 400

from werkzeug.utils import secure_filename
from PIL import Image
from services.upload_service import generate_next_sku

@bp.route('/product/add', methods=('GET', 'POST'))

def add_product():
    manager_id = session.get('manager_id')
    if manager_id is None:
        return redirect(url_for('manager.login'))

    db = get_db()
    manager = db.execute('SELECT * FROM tbl_managers WHERE manager_id = ?', (manager_id,)).fetchone()
    next_sku = generate_next_sku(db, manager_id)

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        description = request.form.get('description', '')
        price_val = request.form.get('price_inr', '').strip()
        price_inr = float(price_val) if price_val else 0.0
        stock_qty = request.form.get('stock_qty', 0)
        status = request.form.get('status', 'Active')
        sku = next_sku

        if not name:
            flash("Product Name is required.", "error")
            return render_template('manager/add_product.html', manager=manager, next_sku=next_sku)
            
        if manager['price_mandatory'] == 1 and not price_val:
            flash("Price is required.", "error")
            return render_template('manager/add_product.html', manager=manager, next_sku=next_sku)

        image_path = 'placeholder.jpg'
        
        # Handle Image
        if 'image' in request.files:
            file = request.files['image']
            if file and file.filename != '':
                try:
                    img = Image.open(file.stream)
                    if img.mode in ('RGBA', 'P'):
                        img = img.convert('RGB')
                    img.thumbnail((800, 800))
                    new_filename = f"{manager_id}_{sku}.jpg"
                    save_path = os.path.join(current_app.config['IMAGE_FOLDER'], new_filename)
                    img.save(save_path, format="JPEG", quality=85)
                    image_path = new_filename
                except Exception as e:
                    flash(f"Warning: Image failed to process: {e}", "error")

        try:
            db.execute(
                "INSERT INTO tbl_products (manager_id, sku, name, description, price_inr, stock_qty, status, image_path) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (manager_id, sku, name, description, price_inr, stock_qty, status, image_path)
            )
            db.commit()
            flash(f'Product added successfully with permanent SKU: {sku}!', 'success')
            return redirect(url_for('manager.dashboard'))
        except (sqlite3.IntegrityError, ValueError):
            flash(f"Error creating product with SKU '{sku}'. Please try again.", 'error')

    return render_template('manager/add_product.html', manager=manager, next_sku=next_sku)

@bp.route('/product/<int:product_id>/edit', methods=('GET', 'POST'))
def edit_product(product_id):
    manager_id = session.get('manager_id')
    if manager_id is None:
        return redirect(url_for('manager.login'))

    db = get_db()
    manager = db.execute('SELECT * FROM tbl_managers WHERE manager_id = ?', (manager_id,)).fetchone()
    product = db.execute('SELECT * FROM tbl_products WHERE product_id = ? AND manager_id = ?', (product_id, manager_id)).fetchone()

    if not product:
        flash('Product not found.', 'error')
        return redirect(url_for('manager.dashboard'))

    if request.method == 'POST':
        sku = product['sku']  # SKU is permanent and cannot be changed
        name = request.form.get('name', '').strip()
        description = request.form.get('description', '')
        price_val = request.form.get('price_inr', '').strip()
        price_inr = float(price_val) if price_val else 0.0
        stock_qty = int(request.form.get('stock_qty', 0))
        requested_status = request.form.get('status', 'Active')

        if not name:
            flash("Product Name is required.", "error")
            return render_template('manager/edit_product.html', manager=manager, product=product)

        if manager['price_mandatory'] == 1 and not price_val:
            flash("Price is required.", "error")
            return render_template('manager/edit_product.html', manager=manager, product=product)

        # Preserve Suspended status if admin suspended it
        status = 'Suspended' if product['status'] == 'Suspended' else requested_status

        image_path = product['image_path']
        if 'image' in request.files:
            file = request.files['image']
            if file and file.filename != '':
                try:
                    img = Image.open(file.stream)
                    if img.mode in ('RGBA', 'P'):
                        img = img.convert('RGB')
                    img.thumbnail((800, 800))
                    new_filename = f"{manager_id}_{sku}.jpg"
                    save_path = os.path.join(current_app.config['IMAGE_FOLDER'], new_filename)
                    img.save(save_path, format="JPEG", quality=85)
                    image_path = new_filename
                except Exception as e:
                    flash(f"Warning: Image failed to process: {e}")

        try:
            db.execute(
                "UPDATE tbl_products SET sku=?, name=?, description=?, price_inr=?, stock_qty=?, status=?, image_path=? "
                "WHERE product_id=? AND manager_id=?",
                (sku, name, description, price_inr, stock_qty, status, image_path, product_id, manager_id)
            )
            db.commit()
            flash('Product updated successfully!', 'success')
            return redirect(url_for('manager.dashboard'))
        except (sqlite3.IntegrityError, ValueError):
            flash(f"A product with SKU '{sku}' already exists.", 'error')

    return render_template('manager/edit_product.html', manager=manager, product=product)

@bp.route('/product/<int:product_id>/toggle_status', methods=['POST'])
def toggle_product_status(product_id):
    manager_id = session.get('manager_id')
    if manager_id is None:
        return redirect(url_for('manager.login'))

    db = get_db()
    product = db.execute('SELECT status FROM tbl_products WHERE product_id = ? AND manager_id = ?', (product_id, manager_id)).fetchone()

    if not product:
        flash('Product not found.', 'error')
        return redirect(url_for('manager.dashboard'))

    if product['status'] == 'Suspended':
        flash('This product is suspended by Super Admin and cannot be activated.', 'error')
    else:
        new_status = 'Inactive' if product['status'] == 'Active' else 'Active'
        db.execute('UPDATE tbl_products SET status = ? WHERE product_id = ? AND manager_id = ?', (new_status, product_id, manager_id))
        db.commit()
        flash(f'Product status changed to {new_status}.', 'success')

    return redirect(url_for('manager.dashboard'))

@bp.route('/product/<int:product_id>/delete', methods=['POST'])
def delete_product(product_id):
    manager_id = session.get('manager_id')
    if manager_id is None:
        return redirect(url_for('manager.login'))

    db = get_db()
    product = db.execute('SELECT * FROM tbl_products WHERE product_id = ? AND manager_id = ?', (product_id, manager_id)).fetchone()

    if not product:
        flash('Product not found.', 'error')
        return redirect(url_for('manager.dashboard'))

    # Delete associated custom image file if not placeholder
    if product['image_path'] and product['image_path'] != 'placeholder.jpg':
        image_filepath = os.path.join(current_app.config['IMAGE_FOLDER'], product['image_path'])
        if os.path.exists(image_filepath):
            try:
                os.remove(image_filepath)
            except OSError:
                pass

    db.execute('DELETE FROM tbl_products WHERE product_id = ? AND manager_id = ?', (product_id, manager_id))
    db.commit()

    flash(f'Product "{product["name"]}" deleted successfully.', 'success')
    return redirect(url_for('manager.dashboard'))


# ── Order Management ─────────────────────────────────────────────────────────
@bp.route('/orders')
def orders():
    manager_id = session.get('manager_id')
    if manager_id is None:
        return redirect(url_for('manager.login'))
        
    db = get_db()
    manager = db.execute('SELECT * FROM tbl_managers WHERE manager_id = ?', (manager_id,)).fetchone()
    
    orders = db.execute('''
        SELECT * FROM tbl_orders 
        WHERE manager_id = ? 
        ORDER BY created_at DESC
    ''', (manager_id,)).fetchall()
    
    # Order items & WhatsApp link builder
    orders_data = []
    import urllib.parse
    for o in orders:
        items = db.execute('''
            SELECT oi.*, p.name, p.packed_qty, p.stock_qty 
            FROM tbl_order_items oi
            JOIN tbl_products p ON oi.product_id = p.product_id
            WHERE oi.order_id = ?
        ''', (o['order_id'],)).fetchall()

        # Build WhatsApp Notification Messages
        phone_clean = re.sub(r'\D', '', o['customer_phone'] or '')
        if phone_clean and not phone_clean.startswith('91') and len(phone_clean) == 10:
            phone_clean = '91' + phone_clean

        item_names = ", ".join([f"{item['name']} (x{item['quantity']})" for item in items])
        
        wa_text_packed = f"Hello {o['customer_name']}, your order #{o['order_id']} ({item_names}) at {manager['shop_name']} has been PACKED and is ready for dispatch! Total: ₹{o['total_amount']}."
        wa_text_delivered = f"Hello {o['customer_name']}, your order #{o['order_id']} at {manager['shop_name']} has been DELIVERED! Thank you for shopping with us."
        wa_text_cancelled = f"Hello {o['customer_name']}, your order #{o['order_id']} at {manager['shop_name']} has been CANCELLED and items have been unpacked back into stock."

        orders_data.append({
            'order': o,
            'order_items': items,
            'wa_packed_link': f"https://wa.me/{phone_clean}?text={urllib.parse.quote(wa_text_packed)}" if phone_clean else "#",
            'wa_delivered_link': f"https://wa.me/{phone_clean}?text={urllib.parse.quote(wa_text_delivered)}" if phone_clean else "#",
            'wa_cancelled_link': f"https://wa.me/{phone_clean}?text={urllib.parse.quote(wa_text_cancelled)}" if phone_clean else "#",
        })
        
    return render_template('manager/orders.html', manager=manager, orders_data=orders_data)

@bp.route('/update_order_status', methods=['POST'])
def update_order_status():
    manager_id = session.get('manager_id')
    if manager_id is None:
        return redirect(url_for('manager.login'))
        
    order_id = request.form.get('order_id')
    new_status = request.form.get('status')
    payment_status = request.form.get('payment_status')
    
    db = get_db()
    order = db.execute('SELECT * FROM tbl_orders WHERE order_id = ? AND manager_id = ?', (order_id, manager_id)).fetchone()
    
    if order:
        old_status = order['status']
        items = db.execute('SELECT product_id, quantity FROM tbl_order_items WHERE order_id = ?', (order_id,)).fetchall()

        # Active statuses: Pending, Packed, Delivered, Completed
        was_active = old_status in ('Pending', 'Packed', 'Delivered', 'Completed')
        is_active = new_status in ('Pending', 'Packed', 'Delivered', 'Completed')

        # 1. Available Stock Management (stock_qty)
        # If moving from active to Cancelled -> return items to stock
        if was_active and new_status == 'Cancelled':
            for item in items:
                db.execute('UPDATE tbl_products SET stock_qty = stock_qty + ? WHERE product_id = ?',
                           (item['quantity'], item['product_id']))
        # If moving from Cancelled to active -> re-deduct items from stock
        elif old_status == 'Cancelled' and is_active:
            for item in items:
                db.execute('UPDATE tbl_products SET stock_qty = MAX(0, stock_qty - ?) WHERE product_id = ?',
                           (item['quantity'], item['product_id']))

        # 2. Packed Stock Management (packed_qty)
        was_packed = (old_status == 'Packed')
        is_packed = (new_status == 'Packed')

        if not was_packed and is_packed:
            # Order just packed -> increment packed_qty
            for item in items:
                db.execute('UPDATE tbl_products SET packed_qty = packed_qty + ? WHERE product_id = ?',
                           (item['quantity'], item['product_id']))
        elif was_packed and not is_packed:
            # Order delivered, cancelled, or reverted to pending -> release packed_qty
            for item in items:
                db.execute('UPDATE tbl_products SET packed_qty = MAX(0, packed_qty - ?) WHERE product_id = ?',
                           (item['quantity'], item['product_id']))

        db.execute('UPDATE tbl_orders SET status = ?, payment_status = ? WHERE order_id = ?', 
                   (new_status, payment_status, order_id))
        db.commit()

        if new_status == 'Cancelled':
            flash(f'Order #{order_id} marked as Cancelled. Products unpacked and restocked back into available stock.', 'success')
        else:
            flash(f'Order #{order_id} status updated to {new_status}.', 'success')
    else:
        flash('Order not found.', 'error')
        
    return redirect(url_for('manager.orders'))

# ── Staff Sub-Accounts Management ───────────────────────────────────────────
@bp.route('/staff')
def staff():
    manager_id = session.get('manager_id')
    if manager_id is None:
        return redirect(url_for('manager.login'))
        
    db = get_db()
    manager = db.execute('SELECT * FROM tbl_managers WHERE manager_id = ?', (manager_id,)).fetchone()
    staff_members = db.execute('SELECT * FROM tbl_staff_accounts WHERE manager_id = ? ORDER BY created_at DESC', (manager_id,)).fetchall()
    
    return render_template('manager/staff.html', manager=manager, staff_members=staff_members)

@bp.route('/staff/add', methods=['POST'])
def add_staff():
    manager_id = session.get('manager_id')
    if manager_id is None:
        return redirect(url_for('manager.login'))
        
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()
    role = request.form.get('role', 'Order_Only').strip()
    
    if not username or not password:
        flash('Username and password are required.', 'error')
        return redirect(url_for('manager.staff'))
        
    db = get_db()
    try:
        db.execute('''INSERT INTO tbl_staff_accounts (manager_id, username, password_hash, role)
                      VALUES (?, ?, ?, ?)''',
                   (manager_id, username, generate_password_hash(password), role))
        db.commit()
        flash(f'Staff account "{username}" created with role {role}.', 'success')
    except (sqlite3.IntegrityError, ValueError):
        flash(f'Username "{username}" is already taken.', 'error')
        
    return redirect(url_for('manager.staff'))

@bp.route('/staff/<int:staff_id>/delete', methods=['POST'])
def delete_staff(staff_id):
    manager_id = session.get('manager_id')
    if manager_id is None:
        return redirect(url_for('manager.login'))
        
    db = get_db()
    db.execute('DELETE FROM tbl_staff_accounts WHERE staff_id = ? AND manager_id = ?', (staff_id, manager_id))
    db.commit()
    flash('Staff account deleted.', 'success')
    return redirect(url_for('manager.staff'))

@bp.route('/staff/<int:staff_id>/toggle_status', methods=['POST'])
def toggle_staff_status(staff_id):
    manager_id = session.get('manager_id')
    if manager_id is None:
        return redirect(url_for('manager.login'))

    db = get_db()
    staff = db.execute('SELECT * FROM tbl_staff_accounts WHERE staff_id = ? AND manager_id = ?', (staff_id, manager_id)).fetchone()

    if not staff:
        flash('Staff account not found.', 'error')
        return redirect(url_for('manager.staff'))

    current_active = 1 if (staff['is_active'] is None or staff['is_active'] == 1) else 0
    new_status = 0 if current_active == 1 else 1
    db.execute('UPDATE tbl_staff_accounts SET is_active = ? WHERE staff_id = ? AND manager_id = ?', (new_status, staff_id, manager_id))
    db.commit()

    status_str = 'enabled' if new_status == 1 else 'disabled'
    flash(f'Staff account "{staff["username"]}" has been {status_str}.', 'success')
    return redirect(url_for('manager.staff'))

@bp.route('/staff/<int:staff_id>/update_role', methods=['POST'])
def update_staff_role(staff_id):
    manager_id = session.get('manager_id')
    if manager_id is None or session.get('is_staff'):
        return redirect(url_for('manager.login'))

    new_role = request.form.get('role', '').strip()
    if new_role not in ('Order_Only', 'Stock_Only', 'Full'):
        flash('Invalid role permission selected.', 'error')
        return redirect(url_for('manager.staff'))

    db = get_db()
    staff = db.execute('SELECT * FROM tbl_staff_accounts WHERE staff_id = ? AND manager_id = ?', (staff_id, manager_id)).fetchone()

    if not staff:
        flash('Staff account not found.', 'error')
        return redirect(url_for('manager.staff'))

    db.execute('UPDATE tbl_staff_accounts SET role = ? WHERE staff_id = ? AND manager_id = ?', (new_role, staff_id, manager_id))
    db.commit()

    role_labels = {
        'Order_Only': 'Order Manager (📦)',
        'Stock_Only': 'Stock Manager (📊)',
        'Full': 'Full Manager Assistant (⭐)'
    }
    flash(f'Role permissions for "{staff["username"]}" updated to {role_labels.get(new_role, new_role)}.', 'success')
    return redirect(url_for('manager.staff'))

# ── Reports & Analytics ──────────────────────────────────────────────────────
@bp.route('/reports')
def reports():
    manager_id = session.get('manager_id')
    if manager_id is None:
        return redirect(url_for('manager.login'))
        
    db = get_db()
    manager = db.execute('SELECT * FROM tbl_managers WHERE manager_id = ?', (manager_id,)).fetchone()
    
    # KPI 1: Total Revenue
    total_revenue = db.execute('''
        SELECT SUM(total_amount) as total FROM tbl_orders 
        WHERE manager_id = ? AND status != 'Cancelled'
    ''', (manager_id,)).fetchone()['total'] or 0
    
    # KPI 2: Total Orders
    total_orders = db.execute('''
        SELECT COUNT(*) as cnt FROM tbl_orders WHERE manager_id = ?
    ''', (manager_id,)).fetchone()['cnt']
    
    # KPI 3: Top Selling Products
    top_products = db.execute('''
        SELECT p.name, SUM(oi.quantity) as total_sold
        FROM tbl_order_items oi
        JOIN tbl_orders o ON oi.order_id = o.order_id
        JOIN tbl_products p ON oi.product_id = p.product_id
        WHERE o.manager_id = ? AND o.status != 'Cancelled'
        GROUP BY oi.product_id
        ORDER BY total_sold DESC
        LIMIT 5
    ''', (manager_id,)).fetchall()
    
    # KPI 4: Most Viewed Products
    top_views = db.execute('''
        SELECT p.name, COUNT(v.view_id) as view_count
        FROM tbl_product_views v
        JOIN tbl_products p ON v.product_id = p.product_id
        WHERE p.manager_id = ?
        GROUP BY p.product_id
        ORDER BY view_count DESC
        LIMIT 5
    ''', (manager_id,)).fetchall()
    
    # Comprehensive Product Funnel
    product_performance = db.execute('''
        SELECT 
            p.product_id, 
            p.name, 
            p.image_path,
            (SELECT COUNT(*) FROM tbl_product_views WHERE product_id = p.product_id) as total_views,
            COALESCE((SELECT SUM(quantity) FROM tbl_cart_items WHERE product_id = p.product_id), 0) as in_carts,
            COALESCE((
                SELECT SUM(oi.quantity) 
                FROM tbl_order_items oi 
                JOIN tbl_orders o ON oi.order_id = o.order_id 
                WHERE oi.product_id = p.product_id AND o.status != 'Cancelled'
            ), 0) as total_ordered,
            COALESCE((
                SELECT SUM(oi.quantity) 
                FROM tbl_order_items oi 
                JOIN tbl_orders o ON oi.order_id = o.order_id 
                WHERE oi.product_id = p.product_id AND o.status = 'Completed'
            ), 0) as total_completed
        FROM tbl_products p
        WHERE p.manager_id = ?
        ORDER BY total_views DESC, total_ordered DESC
    ''', (manager_id,)).fetchall()
    
    return render_template('manager/reports.html', manager=manager, 
                           total_revenue=total_revenue, 
                           total_orders=total_orders,
                           top_products=top_products,
                           top_views=top_views,
                           product_performance=product_performance)

# ── Bulk Stock Management ────────────────────────────────────────────────────
@bp.route('/bulk_stock', methods=['GET', 'POST'])
def bulk_stock():
    manager_id = session.get('manager_id')
    if manager_id is None:
        return redirect(url_for('manager.login'))
        
    db = get_db()
    manager = db.execute('SELECT * FROM tbl_managers WHERE manager_id = ?', (manager_id,)).fetchone()
    
    if request.method == 'POST':
        # request.form will contain something like { 'stock_1': '10', 'stock_2': '5', ... }
        for key, value in request.form.items():
            if key.startswith('stock_'):
                product_id = key.replace('stock_', '')
                try:
                    new_qty = int(value)
                    if new_qty >= 0:
                        db.execute('UPDATE tbl_products SET stock_qty = ? WHERE product_id = ? AND manager_id = ?', 
                                  (new_qty, product_id, manager_id))
                except ValueError:
                    pass
        db.commit()
        flash('Inventory updated successfully.', 'success')
        return redirect(url_for('manager.bulk_stock'))
        
    # GET: fetch all products
    products = db.execute('SELECT product_id, sku, name, stock_qty, status FROM tbl_products WHERE manager_id = ? ORDER BY product_id DESC', (manager_id,)).fetchall()
    return render_template('manager/bulk_stock.html', manager=manager, products=products)

@bp.route('/download_stock_report')
def download_stock_report():
    manager_id = session.get('manager_id')
    if manager_id is None:
        return redirect(url_for('manager.login'))
        
    db = get_db()
    manager = db.execute('SELECT shop_name, shop_slug FROM tbl_managers WHERE manager_id = ?', (manager_id,)).fetchone()
    products = db.execute('SELECT sku, name, price_inr, stock_qty, status FROM tbl_products WHERE manager_id = ? ORDER BY name ASC', (manager_id,)).fetchall()
    
    import io
    import csv
    from flask import Response
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['SKU', 'Product Name', 'Price (INR)', 'Stock Quantity', 'Status'])
    
    for p in products:
        writer.writerow([p['sku'], p['name'], p['price_inr'], p['stock_qty'], p['status']])
        
    response = Response(output.getvalue(), mimetype='text/csv')
    response.headers['Content-Disposition'] = f"attachment; filename={manager['shop_slug']}_stock_report.csv"
    
    return response

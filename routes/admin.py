from flask import Blueprint, render_template, request, redirect, url_for, flash, session, current_app
from werkzeug.security import check_password_hash, generate_password_hash
from database import get_db
from collections import defaultdict
from datetime import datetime
from services.qr_service import get_local_ip
import os

bp = Blueprint('admin', __name__, url_prefix='/admin')

# ---------- Admin credentials (env-configurable) ----------
ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', 'admin')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'Admin@1234')   # change via env in production

def admin_required():
    """Returns redirect if admin not logged in, else None."""
    if not session.get('is_admin'):
        return redirect(url_for('admin.login'))
    return None

# ---------- Auth ----------
@bp.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('is_admin'):
        return redirect(url_for('admin.dashboard'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session['is_admin'] = True
            flash('Welcome, Admin!')
            return redirect(url_for('admin.dashboard'))
        flash('Invalid admin credentials.')

    return render_template('admin/login.html')

@bp.route('/logout')
def logout():
    session.pop('is_admin', None)
    return redirect(url_for('admin.login'))

# ---------- Dashboard ----------
@bp.route('/')
def dashboard():
    guard = admin_required()
    if guard: return guard

    db = get_db()
    managers = db.execute('SELECT * FROM tbl_managers').fetchall()
    total_products = db.execute('SELECT COUNT(*) as count FROM tbl_products').fetchone()['count']
    
    # All products with shop name, grouped by manager
    all_products = db.execute(
        '''SELECT p.*, m.shop_name, m.shop_slug
           FROM tbl_products p
           JOIN tbl_managers m ON p.manager_id = m.manager_id
           ORDER BY m.manager_id, p.product_id DESC'''
    ).fetchall()

    # Group products by manager_id for per-manager expand view
    products_by_manager = defaultdict(list)
    for p in all_products:
        products_by_manager[p['manager_id']].append(p)

    all_staff = db.execute('''
        SELECT s.*, m.shop_name, m.shop_slug
        FROM tbl_staff_accounts s
        JOIN tbl_managers m ON s.manager_id = m.manager_id
        ORDER BY s.staff_id DESC
    ''').fetchall()
    staff_by_manager = defaultdict(list)
    for s in all_staff:
        staff_by_manager[s['manager_id']].append(s)

    local_ip = get_local_ip()

    return render_template('admin/dashboard.html',
                           managers=managers,
                           total_products=total_products,
                           products_by_manager=products_by_manager,
                           staff_by_manager=staff_by_manager,
                           local_ip=local_ip)

# ---------- Manager toggles ----------
@bp.route('/manager/<int:manager_id>/toggle_status', methods=['POST'])
def toggle_status(manager_id):
    guard = admin_required()
    if guard: return guard
    db = get_db()
    manager = db.execute('SELECT is_suspended FROM tbl_managers WHERE manager_id = ?', (manager_id,)).fetchone()
    if manager:
        new_status = 0 if manager['is_suspended'] == 1 else 1
        db.execute('UPDATE tbl_managers SET is_suspended = ? WHERE manager_id = ?', (new_status, manager_id))
        db.commit()
        msg = 'Manager account suspended.' if new_status == 1 else 'Manager account activated.'
        flash(msg)
    return redirect(url_for('admin.dashboard'))

@bp.route('/manager/<int:manager_id>/toggle_whatsapp', methods=['POST'])
def toggle_whatsapp(manager_id):
    guard = admin_required()
    if guard: return guard
    db = get_db()
    manager = db.execute('SELECT whatsapp_orders_enabled FROM tbl_managers WHERE manager_id = ?', (manager_id,)).fetchone()
    if manager:
        new_val = 0 if manager['whatsapp_orders_enabled'] == 1 else 1
        db.execute('UPDATE tbl_managers SET whatsapp_orders_enabled = ? WHERE manager_id = ?', (new_val, manager_id))
        db.commit()
        flash("Manager's WhatsApp ordering status updated.")
    return redirect(url_for('admin.dashboard'))

@bp.route('/manager/<int:manager_id>/toggle_price', methods=['POST'])
def toggle_price(manager_id):
    guard = admin_required()
    if guard: return guard
    db = get_db()
    manager = db.execute('SELECT price_mandatory FROM tbl_managers WHERE manager_id = ?', (manager_id,)).fetchone()
    if manager:
        new_val = 0 if manager['price_mandatory'] == 1 else 1
        db.execute('UPDATE tbl_managers SET price_mandatory = ? WHERE manager_id = ?', (new_val, manager_id))
        db.commit()
        flash("Manager's Price Mandatory setting updated.")
    return redirect(url_for('admin.dashboard'))

@bp.route('/manager/<int:manager_id>/toggle_bulk_upload', methods=['POST'])
def toggle_bulk_upload(manager_id):
    guard = admin_required()
    if guard: return guard
    db = get_db()
    manager = db.execute('SELECT bulk_upload_enabled FROM tbl_managers WHERE manager_id = ?', (manager_id,)).fetchone()
    if manager:
        new_val = 0 if manager['bulk_upload_enabled'] == 1 else 1
        db.execute('UPDATE tbl_managers SET bulk_upload_enabled = ? WHERE manager_id = ?', (manager_id,)).fetchone()
        db.commit()
        flash("Manager's Bulk Upload permission updated.")
    return redirect(url_for('admin.dashboard'))

# ---------- Product suspend ----------
@bp.route('/product/<int:product_id>/toggle_suspend', methods=['POST'])
def toggle_product_suspend(product_id):
    guard = admin_required()
    if guard: return guard
    db = get_db()
    product = db.execute('SELECT status FROM tbl_products WHERE product_id = ?', (product_id,)).fetchone()
    if product:
        new_status = 'Active' if product['status'] == 'Suspended' else 'Suspended'
        db.execute('UPDATE tbl_products SET status = ? WHERE product_id = ?', (new_status, product_id))
        db.commit()
        flash(f'Product marked as {new_status}.')
    return redirect(url_for('admin.dashboard') + '#products')

# ---------- Insights Dashboard ----------
@bp.route('/insights')
def insights():
    guard = admin_required()
    if guard: return guard
    
    db = get_db()
    now_str = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    
    # Platform KPIs
    total_shops = db.execute('SELECT COUNT(*) as cnt FROM tbl_managers').fetchone()['cnt']

    db = get_db()
    
    total_shops = db.execute('SELECT COUNT(*) as count FROM tbl_managers').fetchone()['count']
    total_platform_revenue = db.execute("SELECT COALESCE(SUM(total_amount), 0) as total FROM tbl_orders WHERE status != 'Cancelled'").fetchone()['total']
    total_orders = db.execute('SELECT COUNT(*) as count FROM tbl_orders').fetchone()['count']
    total_products = db.execute('SELECT COUNT(*) as count FROM tbl_products').fetchone()['count']
    
    now_str = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    total_active_visitors = db.execute('SELECT COUNT(*) as count FROM tbl_visitor_sessions WHERE expires_at > ?', (now_str,)).fetchone()['count']
    
    all_shops = db.execute('''
        SELECT 
            m.manager_id, m.shop_name, m.shop_slug, m.email, m.phone_number, m.is_suspended,
            (SELECT COUNT(*) FROM tbl_products WHERE manager_id = m.manager_id) as total_products,
            (SELECT COALESCE(SUM(total_amount), 0) FROM tbl_orders WHERE manager_id = m.manager_id AND status != 'Cancelled') as revenue,
            (SELECT COUNT(*) FROM tbl_orders WHERE manager_id = m.manager_id) as total_orders,
            (SELECT COUNT(*) FROM tbl_visitor_sessions WHERE manager_id = m.manager_id AND expires_at > ?) as active_visitors,
            (SELECT COUNT(*) FROM tbl_visitor_sessions WHERE manager_id = m.manager_id) as total_scans,
            (SELECT COUNT(*) FROM tbl_orders WHERE manager_id = m.manager_id AND status = 'Pending') as pending_orders
        FROM tbl_managers m
        ORDER BY active_visitors DESC, total_scans DESC
    ''', (now_str,)).fetchall()
    
    top_shops = db.execute('''
        SELECT m.shop_name, SUM(o.total_amount) as total_revenue, COUNT(o.order_id) as order_count
        FROM tbl_managers m
        LEFT JOIN tbl_orders o ON m.manager_id = o.manager_id AND o.status != 'Cancelled'
        GROUP BY m.manager_id
        ORDER BY total_revenue DESC
        LIMIT 5
    ''').fetchall()
    
    return render_template('admin/insights.html', 
                           total_shops=total_shops,
                           total_platform_revenue=total_platform_revenue,
                           total_orders=total_orders,
                           total_products=total_products,
                           total_active_visitors=total_active_visitors,
                           all_shops=all_shops,
                           top_shops=top_shops)

# ---------- Users & Staff Directory ----------
@bp.route('/users')
def users():
    guard = admin_required()
    if guard: return guard

    db = get_db()
    
    # 1. All Managers
    managers = db.execute('''
        SELECT m.*,
               (SELECT COUNT(*) FROM tbl_products WHERE manager_id = m.manager_id) as total_products,
               (SELECT COUNT(*) FROM tbl_orders WHERE manager_id = m.manager_id) as total_orders,
               (SELECT COUNT(*) FROM tbl_staff_accounts WHERE manager_id = m.manager_id) as staff_count,
               (SELECT COUNT(*) FROM tbl_customers WHERE manager_id = m.manager_id) as customer_count
        FROM tbl_managers m
        ORDER BY m.manager_id DESC
    ''').fetchall()

    # 2. All Staff Accounts
    staff_members = db.execute('''
        SELECT s.*, m.shop_name, m.shop_slug, m.email as manager_email
        FROM tbl_staff_accounts s
        JOIN tbl_managers m ON s.manager_id = m.manager_id
        ORDER BY s.staff_id DESC
    ''').fetchall()
    staff_by_manager = defaultdict(list)
    for s in staff_members:
        staff_by_manager[s['manager_id']].append(s)

    # 3. All Customers
    customers = db.execute('''
        SELECT c.*, m.shop_name, m.shop_slug,
               (SELECT COUNT(*) FROM tbl_orders WHERE manager_id = c.manager_id AND (customer_phone = c.phone_number OR customer_phone LIKE '%' || SUBSTR(c.phone_number, -10))) as order_count,
               (SELECT COALESCE(SUM(total_amount), 0) FROM tbl_orders WHERE manager_id = c.manager_id AND (customer_phone = c.phone_number OR customer_phone LIKE '%' || SUBSTR(c.phone_number, -10)) AND status != 'Cancelled') as total_spent
        FROM tbl_customers c
        JOIN tbl_managers m ON c.manager_id = m.manager_id
        ORDER BY c.customer_id DESC
    ''').fetchall()
    customers_by_manager = defaultdict(list)
    for c in customers:
        customers_by_manager[c['manager_id']].append(c)

    # 4. All Orders with Items
    all_orders = db.execute('''
        SELECT o.*, m.shop_name, m.shop_slug, m.email as manager_email
        FROM tbl_orders o
        JOIN tbl_managers m ON o.manager_id = m.manager_id
        ORDER BY o.order_id DESC
    ''').fetchall()
    
    order_items_map = defaultdict(list)
    if all_orders:
        order_ids = tuple(o['order_id'] for o in all_orders)
        placeholders = ', '.join(['?'] * len(order_ids))
        items_rows = db.execute(f'''
            SELECT oi.*, p.name as product_name, p.sku
            FROM tbl_order_items oi
            LEFT JOIN tbl_products p ON oi.product_id = p.product_id
            WHERE oi.order_id IN ({placeholders})
        ''', order_ids).fetchall()
        for it in items_rows:
            order_items_map[it['order_id']].append(it)

    orders_with_items = []
    orders_by_manager = defaultdict(list)
    for o in all_orders:
        o_dict = dict(o)
        o_dict['order_items'] = order_items_map.get(o['order_id'], [])
        orders_with_items.append(o_dict)
        orders_by_manager[o['manager_id']].append(o_dict)

    return render_template(
        'admin/users.html',
        managers=managers,
        staff_members=staff_members,
        staff_by_manager=staff_by_manager,
        customers=customers,
        customers_by_manager=customers_by_manager,
        orders_by_manager=orders_by_manager,
        all_orders=orders_with_items
    )

# ---------- Store Orders Hub ----------
@bp.route('/orders')
def orders():
    guard = admin_required()
    if guard: return guard

    db = get_db()
    manager_id = request.args.get('manager_id', type=int)
    status_filter = request.args.get('status', '').strip()
    search_q = request.args.get('q', '').strip()

    managers = db.execute('SELECT manager_id, shop_name, shop_slug FROM tbl_managers ORDER BY shop_name ASC').fetchall()

    query = '''
        SELECT o.*, m.shop_name, m.shop_slug, m.email as manager_email, m.phone_number as manager_phone
        FROM tbl_orders o
        JOIN tbl_managers m ON o.manager_id = m.manager_id
        WHERE 1=1
    '''
    params = []

    if manager_id:
        query += ' AND o.manager_id = ?'
        params.append(manager_id)

    if status_filter and status_filter != 'All':
        query += ' AND o.status = ?'
        params.append(status_filter)

    if search_q:
        query += ' AND (o.customer_name LIKE ? OR o.customer_phone LIKE ? OR o.customer_email LIKE ? OR o.order_uuid LIKE ? OR m.shop_name LIKE ?)'
        like_q = f'%{search_q}%'
        params.extend([like_q, like_q, like_q, like_q, like_q])

    query += ' ORDER BY o.order_id DESC'
    orders_rows = db.execute(query, params).fetchall()

    # Batch fetch order items
    order_items_map = defaultdict(list)
    if orders_rows:
        order_ids = tuple(o['order_id'] for o in orders_rows)
        placeholders = ', '.join(['?'] * len(order_ids))
        items_rows = db.execute(f'''
            SELECT oi.*, p.name as product_name, p.sku
            FROM tbl_order_items oi
            LEFT JOIN tbl_products p ON oi.product_id = p.product_id
            WHERE oi.order_id IN ({placeholders})
        ''', order_ids).fetchall()
        for it in items_rows:
            order_items_map[it['order_id']].append(it)

    orders_data = []
    total_filtered_revenue = 0
    pending_count = 0
    completed_count = 0

    for o in orders_rows:
        o_dict = dict(o)
        o_dict['order_items'] = order_items_map.get(o['order_id'], [])
        orders_data.append(o_dict)
        if o['status'] != 'Cancelled':
            total_filtered_revenue += (o['total_amount'] or 0)
        if o['status'] == 'Pending':
            pending_count += 1
        elif o['status'] == 'Completed':
            completed_count += 1

    return render_template(
        'admin/orders.html',
        orders=orders_data,
        managers=managers,
        selected_manager_id=manager_id,
        selected_status=status_filter,
        search_query=search_q,
        total_revenue=total_filtered_revenue,
        pending_count=pending_count,
        completed_count=completed_count
    )

# ---------- Update Order Status ----------
@bp.route('/order/<int:order_id>/update_status', methods=['POST'])
def update_order_status(order_id):
    guard = admin_required()
    if guard: return guard
    
    new_status = request.form.get('status', 'Pending').strip()
    redirect_to = request.form.get('redirect_to', 'orders')
    
    db = get_db()
    db.execute('UPDATE tbl_orders SET status = ? WHERE order_id = ?', (new_status, order_id))
    db.commit()
    flash(f'Order #{order_id} status updated to {new_status}.', 'success')
    
    if redirect_to == 'users':
        return redirect(url_for('admin.users'))
    return redirect(url_for('admin.orders'))

# ---------- Staff Toggle ----------
@bp.route('/staff/<int:staff_id>/toggle_status', methods=['POST'])
def toggle_staff_status(staff_id):
    guard = admin_required()
    if guard: return guard
    db = get_db()
    staff = db.execute('SELECT is_active, username FROM tbl_staff_accounts WHERE staff_id = ?', (staff_id,)).fetchone()
    if staff:
        new_status = 0 if staff['is_active'] == 1 else 1
        db.execute('UPDATE tbl_staff_accounts SET is_active = ? WHERE staff_id = ?', (new_status, staff_id))
        db.commit()
        msg = f'Staff account "{staff["username"]}" disabled.' if new_status == 0 else f'Staff account "{staff["username"]}" enabled.'
        flash(msg, 'success')
    return redirect(url_for('admin.users'))

# ---------- IP & URL Security Panel ----------
@bp.route('/security')
def security():
    guard = admin_required()
    if guard: return guard
    
    db = get_db()
    from services.qr_service import parse_user_agent, get_shop_base_url
    now_str = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')

    # Fetch visitor IP & rendered URL history
    raw_logs = db.execute('''
        SELECT v.*, m.shop_name, m.shop_slug,
               CASE WHEN v.expires_at > ? THEN 'Active' ELSE 'Expired' END as session_status
        FROM tbl_visitor_sessions v
        LEFT JOIN tbl_managers m ON v.manager_id = m.manager_id
        ORDER BY v.visit_id DESC
        LIMIT 100
    ''', (now_str,)).fetchall()

    visitor_logs = []
    shop_base = get_shop_base_url(request)
    for log in raw_logs:
        v_url = log['visited_url'] or (f"/shop/{log['shop_slug']}" if log['shop_slug'] else '/')
        full_render_url = f"{shop_base}{v_url}" if not v_url.startswith('http') else v_url
        visitor_logs.append({
            'visit_id': log['visit_id'],
            'shop_name': log['shop_name'] or 'Storefront',
            'shop_slug': log['shop_slug'],
            'ip_address': log['ip_address'] or '127.0.0.1',
            'visited_url': v_url,
            'full_render_url': full_render_url,
            'device_info': parse_user_agent(log['user_agent']),
            'user_agent_raw': log['user_agent'] or '',
            'created_at': log['created_at'],
            'expires_at': log['expires_at'],
            'status': log['session_status']
        })

    # Blocked IPs
    blocked_ips = db.execute('SELECT *, created_at AS blocked_at FROM tbl_blocked_ips ORDER BY created_at DESC').fetchall()
    
    return render_template('admin/security.html',
                           visitor_logs=visitor_logs,
                           blocked_ips=blocked_ips)

@bp.route('/block_ip', methods=['POST'])
def block_ip():
    guard = admin_required()
    if guard: return guard
    
    ip_address = request.form.get('ip_address', '').strip()
    reason = request.form.get('reason', 'Administrative block').strip()
    redirect_to = request.form.get('redirect_to', 'security')
    
    if ip_address:
        db = get_db()
        db.execute('INSERT OR IGNORE INTO tbl_blocked_ips (ip_address, reason) VALUES (?, ?)',
                   (ip_address, reason))
        db.commit()
        # Invalidate blocked IP cache immediately
        cache = current_app.config.get('BLOCKED_CACHE')
        if cache:
            cache['expiry'] = 0
        flash(f'IP Address {ip_address} has been blocked.')

    if redirect_to == 'users':
        return redirect(url_for('admin.users'))
    return redirect(url_for('admin.security'))

@bp.route('/unblock_ip/<int:ip_id>', methods=['POST'])
def unblock_ip(ip_id):
    guard = admin_required()
    if guard: return guard
    
    db = get_db()
    db.execute('DELETE FROM tbl_blocked_ips WHERE ip_id = ?', (ip_id,))
    db.commit()
    # Invalidate blocked IP cache immediately
    cache = current_app.config.get('BLOCKED_CACHE')
    if cache:
        cache['expiry'] = 0
    flash('IP Address unblocked successfully.')
    return redirect(url_for('admin.security'))

from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from werkzeug.security import check_password_hash, generate_password_hash
from database import get_db
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
    from collections import defaultdict
    products_by_manager = defaultdict(list)
    for p in all_products:
        products_by_manager[p['manager_id']].append(p)

    from services.qr_service import get_local_ip
    local_ip = get_local_ip()

    return render_template('admin/dashboard.html',
                           managers=managers,
                           total_products=total_products,
                           products_by_manager=products_by_manager,
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
    from datetime import datetime
    now_str = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    
    # Platform KPIs
    total_shops = db.execute('SELECT COUNT(*) as cnt FROM tbl_managers').fetchone()['cnt']
    total_platform_revenue = db.execute("SELECT SUM(total_amount) as total FROM tbl_orders WHERE status != 'Cancelled'").fetchone()['total'] or 0
    total_orders = db.execute('SELECT COUNT(*) as cnt FROM tbl_orders').fetchone()['cnt']
    total_products = db.execute('SELECT COUNT(*) as cnt FROM tbl_products').fetchone()['cnt']
    
    # Total active visitors right now (across ALL shops)
    total_active_visitors = db.execute(
        'SELECT COUNT(*) as cnt FROM tbl_visitor_sessions WHERE expires_at > ?', (now_str,)
    ).fetchone()['cnt']
    
    # All shops with live session data
    all_shops = db.execute('''
        SELECT 
            m.shop_name,
            m.shop_slug,
            m.is_suspended,
            (SELECT COUNT(*) FROM tbl_products WHERE manager_id = m.manager_id) as product_count,
            (SELECT COUNT(*) FROM tbl_orders WHERE manager_id = m.manager_id) as total_orders,
            (SELECT COUNT(*) FROM tbl_visitor_sessions WHERE manager_id = m.manager_id AND expires_at > ?) as active_visitors,
            (SELECT COUNT(*) FROM tbl_visitor_sessions WHERE manager_id = m.manager_id) as total_scans,
            (SELECT COUNT(*) FROM tbl_orders WHERE manager_id = m.manager_id AND status = 'Pending') as pending_orders
        FROM tbl_managers m
        ORDER BY active_visitors DESC, total_scans DESC
    ''', (now_str,)).fetchall()
    
    # Top Shops by Revenue
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

# ---------- IP & URL Security Panel ----------
@bp.route('/security')
def security():
    guard = admin_required()
    if guard: return guard
    
    db = get_db()
    # Fetch visitor IP rendering history
    visitor_logs = db.execute('''
        SELECT v.*, m.shop_name
        FROM tbl_visitor_sessions v
        LEFT JOIN tbl_managers m ON v.manager_id = m.manager_id
        ORDER BY v.visit_id DESC
        LIMIT 50
    ''').fetchall()

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
    
    if ip_address:
        db = get_db()
        db.execute('INSERT OR IGNORE INTO tbl_blocked_ips (ip_address, reason) VALUES (?, ?)',
                   (ip_address, reason))
        db.commit()
        flash(f'IP Address {ip_address} has been blocked.')
    return redirect(url_for('admin.security'))

@bp.route('/unblock_ip/<int:ip_id>', methods=['POST'])
def unblock_ip(ip_id):
    guard = admin_required()
    if guard: return guard
    
    db = get_db()
    db.execute('DELETE FROM tbl_blocked_ips WHERE ip_id = ?', (ip_id,))
    db.commit()
    flash('IP Address unblocked successfully.')
    return redirect(url_for('admin.security'))

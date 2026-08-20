from flask import Blueprint, render_template, abort, redirect, url_for, request, session, jsonify
from database import get_db
from datetime import datetime, timedelta
from services.qr_service import get_client_ip, get_local_ip
import uuid

bp = Blueprint('shop', __name__, url_prefix='/shop')

def record_visitor_activity(manager_id, shop_slug, is_scan=False):
    """Records new visitor scan/visit or refreshes active sliding window (15 mins)."""
    db = get_db()
    ip_addr = get_client_ip(request)
    user_agent = request.headers.get('User-Agent', '')
    now = datetime.utcnow()
    expires = (now + timedelta(minutes=15)).strftime('%Y-%m-%d %H:%M:%S')
    
    session_key = f"visitor_token_{shop_slug}"
    existing_token = session.get(session_key)
    
    if existing_token and not is_scan:
        db.execute(
            "UPDATE tbl_visitor_sessions SET expires_at = ?, ip_address = COALESCE(NULLIF(ip_address, ''), ?) WHERE session_token = ? AND manager_id = ?",
            (expires, ip_addr, existing_token, manager_id)
        )
        db.commit()
    else:
        # New scan or first visit
        new_token = uuid.uuid4().hex
        session[session_key] = new_token
        try:
            db.execute('''
                INSERT INTO tbl_visitor_sessions (manager_id, session_token, ip_address, user_agent, expires_at)
                VALUES (?, ?, ?, ?, ?)
            ''', (manager_id, new_token, ip_addr, user_agent, expires))
            db.commit()
        except Exception as e:
            print(f"Error logging visitor session: {e}")

def get_or_create_customer_session(shop_slug):
    db = get_db()
    session_key = f"customer_session_{shop_slug}"
    
    if session_key not in session:
        session_id = str(uuid.uuid4())
        session[session_key] = session_id
        db.execute(
            'INSERT INTO tbl_customer_sessions (session_id, shop_slug) VALUES (?, ?)',
            (session_id, shop_slug)
        )
        db.commit()
    else:
        session_id = session[session_key]
        # Ensure it exists in DB (could have been cleared from DB but still in cookie)
        exists = db.execute('SELECT 1 FROM tbl_customer_sessions WHERE session_id=?', (session_id,)).fetchone()
        if not exists:
            db.execute(
                'INSERT INTO tbl_customer_sessions (session_id, shop_slug) VALUES (?, ?)',
                (session_id, shop_slug)
            )
        else:
            db.execute(
                "UPDATE tbl_customer_sessions SET last_active = CURRENT_TIMESTAMP WHERE session_id=?",
                (session_id,)
            )
        db.commit()
    
    return session[session_key]

# ── Public catalog ────────────────────────────────────────────────────────
@bp.route('/<shop_slug>')
def catalog(shop_slug):
    db = get_db()
    manager = db.execute(
        'SELECT * FROM tbl_managers WHERE shop_slug=? AND is_suspended=0',
        (shop_slug,)
    ).fetchone()
    if manager is None:
        abort(404)

    # Track visitor session
    is_scan = (request.args.get('source') == 'qr')
    record_visitor_activity(manager['manager_id'], shop_slug, is_scan=is_scan)

    products = db.execute(
        "SELECT * FROM tbl_products WHERE manager_id=? AND status='Active'",
        (manager['manager_id'],)
    ).fetchall()

    session_id = get_or_create_customer_session(shop_slug)
    
    # Get cart item count
    cart_count = db.execute(
        'SELECT SUM(quantity) as count FROM tbl_cart_items WHERE session_id=?',
        (session_id,)
    ).fetchone()['count'] or 0

    local_ip = get_local_ip()

    return render_template('shop/catalog.html', manager=manager, products=products, local_ip=local_ip, cart_count=cart_count)


# ── Track Product View ───────────────────────────────────────────────────────
@bp.route('/<shop_slug>/view_product', methods=['POST'])
def view_product(shop_slug):
    data = request.get_json()
    product_id = data.get('product_id')
    if product_id:
        db = get_db()
        session_id = get_or_create_customer_session(shop_slug)
        db.execute(
            'INSERT INTO tbl_product_views (product_id, session_id) VALUES (?, ?)',
            (product_id, session_id)
        )
        db.commit()
        return jsonify({'status': 'success'})
    return jsonify({'status': 'error', 'message': 'Missing product_id'}), 400

# ── Add to Cart ──────────────────────────────────────────────────────────────
@bp.route('/<shop_slug>/add_to_cart', methods=['POST'])
def add_to_cart(shop_slug):
    data = request.get_json()
    product_id = data.get('product_id')
    
    db = get_db()
    session_id = get_or_create_customer_session(shop_slug)
    
    # Check if item already in cart
    existing = db.execute(
        'SELECT * FROM tbl_cart_items WHERE session_id=? AND product_id=?',
        (session_id, product_id)
    ).fetchone()
    
    if existing:
        db.execute(
            'UPDATE tbl_cart_items SET quantity = quantity + 1 WHERE session_id=? AND product_id=?',
            (session_id, product_id)
        )
    else:
        db.execute(
            'INSERT INTO tbl_cart_items (session_id, product_id, quantity) VALUES (?, ?, 1)',
            (session_id, product_id)
        )
    db.commit()
    
    # Return updated cart count
    cart_count = db.execute(
        'SELECT SUM(quantity) as count FROM tbl_cart_items WHERE session_id=?',
        (session_id,)
    ).fetchone()['count'] or 0
    
    return jsonify({'status': 'success', 'cart_count': cart_count})

# ── Update Cart Item ─────────────────────────────────────────────────────────
@bp.route('/<shop_slug>/update_cart', methods=['POST'])
def update_cart(shop_slug):
    data = request.get_json()
    product_id = data.get('product_id')
    action = data.get('action') # 'increase', 'decrease', 'remove'
    
    db = get_db()
    session_id = get_or_create_customer_session(shop_slug)
    
    if action == 'increase':
        db.execute('UPDATE tbl_cart_items SET quantity = quantity + 1 WHERE session_id=? AND product_id=?', (session_id, product_id))
    elif action == 'decrease':
        # Check current qty
        item = db.execute('SELECT quantity FROM tbl_cart_items WHERE session_id=? AND product_id=?', (session_id, product_id)).fetchone()
        if item and item['quantity'] > 1:
            db.execute('UPDATE tbl_cart_items SET quantity = quantity - 1 WHERE session_id=? AND product_id=?', (session_id, product_id))
        else:
            db.execute('DELETE FROM tbl_cart_items WHERE session_id=? AND product_id=?', (session_id, product_id))
    elif action == 'remove':
        db.execute('DELETE FROM tbl_cart_items WHERE session_id=? AND product_id=?', (session_id, product_id))
        
    db.commit()
    
    return jsonify({'status': 'success'})

# ── View Cart ────────────────────────────────────────────────────────────────
@bp.route('/<shop_slug>/cart')
def view_cart(shop_slug):
    db = get_db()
    manager = db.execute('SELECT * FROM tbl_managers WHERE shop_slug=? AND is_suspended=0', (shop_slug,)).fetchone()
    if not manager:
        abort(404)
        
    session_id = get_or_create_customer_session(shop_slug)
    
    cart_items = db.execute('''
        SELECT c.*, p.name, p.price_inr, p.image_path, (c.quantity * p.price_inr) as item_total
        FROM tbl_cart_items c
        JOIN tbl_products p ON c.product_id = p.product_id
        WHERE c.session_id=?
    ''', (session_id,)).fetchall()
    
    total_amount = sum(item['item_total'] for item in cart_items)
    
    return render_template('shop/cart.html', manager=manager, cart_items=cart_items, total_amount=total_amount)

# ── Checkout ─────────────────────────────────────────────────────────────────
@bp.route('/<shop_slug>/checkout', methods=['POST'])
def checkout(shop_slug):
    db = get_db()
    manager = db.execute('SELECT * FROM tbl_managers WHERE shop_slug=? AND is_suspended=0', (shop_slug,)).fetchone()
    if not manager:
        abort(404)
        
    session_id = get_or_create_customer_session(shop_slug)
    
    customer_name = request.form.get('customer_name')
    customer_phone = request.form.get('customer_phone')
    
    # Get cart items
    cart_items = db.execute('''
        SELECT c.*, p.price_inr
        FROM tbl_cart_items c
        JOIN tbl_products p ON c.product_id = p.product_id
        WHERE c.session_id=?
    ''', (session_id,)).fetchall()
    
    if not cart_items:
        return redirect(url_for('shop.catalog', shop_slug=shop_slug))
        
    total_amount = sum(item['quantity'] * item['price_inr'] for item in cart_items)
    order_uuid = str(uuid.uuid4())
    
    # Create Order
    db.execute('''
        INSERT INTO tbl_orders (order_uuid, manager_id, customer_name, customer_phone, total_amount, status, payment_status)
        VALUES (?, ?, ?, ?, ?, 'Pending', 'Unpaid')
    ''', (order_uuid, manager['manager_id'], customer_name, customer_phone, total_amount))
    
    order_rec = db.execute('SELECT order_id FROM tbl_orders WHERE order_uuid = ?', (order_uuid,)).fetchone()
    order_id = order_rec['order_id'] if order_rec else None
    
    # Create Order Items
    for item in cart_items:
        db.execute('''
            INSERT INTO tbl_order_items (order_id, product_id, quantity, price_at_time)
            VALUES (?, ?, ?, ?)
        ''', (order_id, item['product_id'], item['quantity'], item['price_inr']))

    # Clear Cart
    db.execute('DELETE FROM tbl_cart_items WHERE session_id=?', (session_id,))
    db.commit()
    
    return redirect(url_for('shop.order_success', shop_slug=shop_slug, order_uuid=order_uuid))

# ── Order Success ────────────────────────────────────────────────────────────
@bp.route('/<shop_slug>/order/<order_uuid>')
def order_success(shop_slug, order_uuid):
    db = get_db()
    manager = db.execute('SELECT * FROM tbl_managers WHERE shop_slug=?', (shop_slug,)).fetchone()
    order = db.execute('SELECT * FROM tbl_orders WHERE order_uuid=?', (order_uuid,)).fetchone()
    
    if not order or not manager:
        abort(404)
        
    items = db.execute('''
        SELECT oi.*, p.name 
        FROM tbl_order_items oi
        JOIN tbl_products p ON oi.product_id = p.product_id
        WHERE oi.order_id=?
    ''', (order['order_id'],)).fetchall()
    
    return render_template('shop/order_success.html', manager=manager, order=order, items=items)

# ── Customer Mobile Order Tracking ─────────────────────────────────────────
@bp.route('/<shop_slug>/track-order', methods=['GET', 'POST'])
def track_order(shop_slug):
    db = get_db()
    manager = db.execute('SELECT * FROM tbl_managers WHERE shop_slug=? AND is_suspended=0', (shop_slug,)).fetchone()
    if not manager:
        abort(404)
        
    orders_found = []
    customer_phone = ''
    searched = False

    if request.method == 'POST':
        customer_phone = request.form.get('customer_phone', '').strip()
        searched = True
        if customer_phone:
            orders = db.execute('''
                SELECT * FROM tbl_orders 
                WHERE manager_id=? AND (customer_phone = ? OR customer_phone LIKE ?)
                ORDER BY created_at DESC
            ''', (manager['manager_id'], customer_phone, f'%{customer_phone[-10:]}%')).fetchall()

            for o in orders:
                items = db.execute('''
                    SELECT oi.*, p.name, p.image_path
                    FROM tbl_order_items oi
                    JOIN tbl_products p ON oi.product_id = p.product_id
                    WHERE oi.order_id = ?
                ''', (o['order_id'],)).fetchall()
                orders_found.append({
                    'order': o,
                    'order_items': items
                })

    return render_template('shop/track_order.html', manager=manager, orders_found=orders_found, searched=searched, customer_phone=customer_phone)

# ── QR Scan alias ────────────────────────────────────────────────────────────
@bp.route('/scan/<shop_slug>')
def scan(shop_slug):
    db = get_db()
    manager = db.execute('SELECT manager_id FROM tbl_managers WHERE shop_slug=? AND is_suspended=0', (shop_slug,)).fetchone()
    
    if manager:
        record_visitor_activity(manager['manager_id'], shop_slug, is_scan=True)
        
    return redirect(url_for('shop.catalog', shop_slug=shop_slug, source='qr'))


import qrcode
import os
import socket
import re

def get_local_ip():
    """Detects active network IPs prioritizing Mobile Hotspot (192.168.137.x) or Wi-Fi."""
    try:
        hostname = socket.gethostname()
        all_ips = socket.gethostbyname_ex(hostname)[2]
        # Prioritize Hotspot IP if active
        for ip in all_ips:
            if ip.startswith('192.168.137.'):
                return ip
        # Otherwise return first LAN IP
        for ip in all_ips:
            if not ip.startswith('127.'):
                return ip
    except Exception:
        pass

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def get_client_ip(request):
    """Extracts real client IP, considering Render, Cloudflare, Nginx, and load balancers."""
    if not request:
        return "127.0.0.1"
    
    # Priority 1: Cloudflare Connecting IP
    cf_connecting_ip = request.headers.get('CF-Connecting-IP')
    if cf_connecting_ip:
        return cf_connecting_ip.strip()

    # Priority 2: X-Real-IP (Render / Nginx reverse proxies)
    x_real_ip = request.headers.get('X-Real-IP')
    if x_real_ip:
        return x_real_ip.strip()

    # Priority 3: X-Forwarded-For (Standard reverse proxy chain)
    x_forwarded_for = request.headers.get('X-Forwarded-For')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
        
    return request.remote_addr or "127.0.0.1"

def parse_user_agent(ua_string):
    """Parses a User-Agent string into a clean, human-readable Device and Browser label."""
    if not ua_string:
        return "📱 Mobile / Web Visitor"
        
    ua = ua_string.lower()
    
    # Device / OS Detection
    device = "💻 Desktop"
    if "iphone" in ua:
        device = "🍎 iPhone"
    elif "ipad" in ua:
        device = "🍎 iPad"
    elif "android" in ua:
        device = "📱 Android"
    elif "windows" in ua:
        device = "💻 Windows"
    elif "macintosh" in ua or "mac os" in ua:
        device = "💻 macOS"
    elif "linux" in ua:
        device = "💻 Linux"
    elif "mobile" in ua:
        device = "📱 Mobile"
        
    # Browser Detection (specific browsers checked BEFORE generic Chrome/Safari)
    browser = "Browser"
    if "comet" in ua or "perplexity" in ua:
        browser = "Comet Browser"
    elif "arc/" in ua or "arc " in ua:
        browser = "Arc Browser"
    elif "brave" in ua:
        browser = "Brave"
    elif "edg" in ua or "edge" in ua:
        browser = "Edge"
    elif "opr" in ua or "opera" in ua:
        browser = "Opera"
    elif "vivaldi" in ua:
        browser = "Vivaldi"
    elif "samsungbrowser" in ua:
        browser = "Samsung Internet"
    elif "ucbrowser" in ua or "ubrowser" in ua:
        browser = "UC Browser"
    elif "duckduckgo" in ua:
        browser = "DuckDuckGo"
    elif "whatsapp" in ua:
        browser = "WhatsApp Browser"
    elif "instagram" in ua:
        browser = "Instagram App"
    elif "fbav" in ua or "facebook" in ua:
        browser = "Facebook App"
    elif "firefox" in ua or "fxios" in ua:
        browser = "Firefox"
    elif "chrome" in ua or "crios" in ua:
        browser = "Chrome"
    elif "safari" in ua and "chrome" not in ua and "crios" not in ua:
        browser = "Safari"
        
    return f"{device} • {browser}"

def get_shop_base_url(request=None):
    """Determines canonical shop base URL."""
    render_url = os.environ.get('RENDER_EXTERNAL_URL')
    if render_url:
        return render_url.rstrip('/')
    
    if request:
        return request.host_url.rstrip('/')
        
    host_ip = get_local_ip()
    return f"http://{host_ip}:5000"

def generate_shop_qr(shop_slug, qr_folder, base_url=None):
    """Generate permanent QR code for the shop."""
    if not base_url:
        base_url = get_shop_base_url()
    else:
        base_url = base_url.rstrip('/')

    url = f"{base_url}/scan/{shop_slug}"
    
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(url)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    filepath = os.path.join(qr_folder, f"{shop_slug}.png")
    img.save(filepath)
    return f"{shop_slug}.png"


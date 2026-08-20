import csv
import os
import re
from PIL import Image
from database import get_db
from werkzeug.utils import secure_filename
import io
import cloudinary
import cloudinary.uploader

def is_cloudinary_configured():
    """Check if Cloudinary credentials or CLOUDINARY_URL are configured."""
    url = os.environ.get('CLOUDINARY_URL')
    cloud_name = os.environ.get('CLOUDINARY_CLOUD_NAME')
    api_key = os.environ.get('CLOUDINARY_API_KEY')
    api_secret = os.environ.get('CLOUDINARY_API_SECRET')
    return bool((url and url.strip()) or (cloud_name and api_key and api_secret))

def init_cloudinary():
    """Initialize Cloudinary SDK config."""
    if is_cloudinary_configured():
        url = os.environ.get('CLOUDINARY_URL')
        if url and url.strip():
            cloudinary.config(cloudinary_url=url.strip())
        else:
            cloudinary.config(
                cloud_name=os.environ.get('CLOUDINARY_CLOUD_NAME').strip(),
                api_key=os.environ.get('CLOUDINARY_API_KEY').strip(),
                api_secret=os.environ.get('CLOUDINARY_API_SECRET').strip(),
                secure=True
            )

def upload_product_image_file(file_obj, manager_id, sku_or_id, local_image_folder=None, shop_slug=None):
    """Uploads a product image to Cloudinary (preferred) or saves locally as fallback.
    
    Cloudinary folder structure:
      - shops/<shop_slug>/products/  (when shop_slug provided — recommended)
      - shops/<manager_id>/products/ (fallback when shop_slug not available)
    
    Returns: (image_path_or_url, error_message)
    """
    # 1. Try Cloudinary first
    if is_cloudinary_configured():
        try:
            init_cloudinary()
            clean_id    = re.sub(r'[^a-zA-Z0-9_-]', '_', str(sku_or_id)).strip('_')
            folder_name = re.sub(r'[^a-z0-9_-]', '-', str(shop_slug or manager_id).lower()).strip('-')
            res = cloudinary.uploader.upload(
                file_obj,
                folder=f"shops/{folder_name}/products",
                public_id=f"prod_{clean_id}",
                overwrite=True,
                resource_type="image",
                transformation=[
                    {'width': 800, 'height': 800, 'crop': 'limit'},
                    {'quality': 'auto', 'fetch_format': 'auto'}
                ]
            )
            secure_url = res.get('secure_url') or res.get('url')
            if secure_url:
                return secure_url, None
        except Exception as e:
            print(f"Cloudinary upload error: {e}. Falling back to local storage.")
            if hasattr(file_obj, 'seek'):
                file_obj.seek(0)

    # 2. Local fallback if Cloudinary is not configured or fails
    if local_image_folder:
        try:
            img = Image.open(file_obj)
            if img.mode in ('RGBA', 'P'):
                img = img.convert('RGB')
            img.thumbnail((800, 800))
            clean_id     = re.sub(r'[^a-zA-Z0-9_-]', '_', str(sku_or_id)).strip('_')
            new_filename = f"{manager_id}_{clean_id}.jpg"
            save_path    = os.path.join(local_image_folder, new_filename)
            img.save(save_path, format="JPEG", quality=85)
            return new_filename, None
        except Exception as e:
            return None, f"Image processing failed: {str(e)}"

    return None, "No storage provider available for image"

def upload_shop_qr_to_cloudinary(shop_slug, target_url):
    """Uploads shop QR code directly to Cloudinary in the 'qrs' folder.
    Returns: (secure_url, error_message)
    """
    if is_cloudinary_configured():
        try:
            init_cloudinary()
            from services.qr_service import generate_qr_image_bytes
            img_bytes = generate_qr_image_bytes(target_url)
            clean_slug = re.sub(r'[^a-zA-Z0-9_-]', '_', str(shop_slug)).strip('_')

            res = cloudinary.uploader.upload(
                img_bytes,
                folder="qrs",
                public_id=f"qr_{clean_slug}",
                overwrite=True,
                resource_type="image"
            )
            secure_url = res.get('secure_url') or res.get('url')
            if secure_url:
                return secure_url, None
        except Exception as e:
            print(f"Cloudinary QR upload error: {e}")
            return None, str(e)
    return None, "Cloudinary is not configured"

def generate_next_sku(db, manager_id):
    """Generates the next sequential SKU for the manager, e.g. SKU-1-001, SKU-1-002.
    Finds the highest current sequence number and increments continuously.
    """
    rows = db.execute("SELECT sku FROM tbl_products WHERE manager_id = ?", (manager_id,)).fetchall()
    highest_seq = 0
    for r in rows:
        sku_val = str(r['sku'] or '').strip()
        nums = re.findall(r'\d+', sku_val)
        if nums:
            try:
                # Use the last numeric portion as sequence
                val = int(nums[-1])
                if val > highest_seq:
                    highest_seq = val
            except ValueError:
                pass
                
    seq = max(highest_seq + 1, len(rows) + 1, 1)
    while True:
        candidate = f"SKU-{manager_id}-{seq:03d}"
        exists = db.execute("SELECT product_id FROM tbl_products WHERE manager_id = ? AND sku = ?", (manager_id, candidate)).fetchone()
        if not exists:
            return candidate
        seq += 1

def process_csv_upload(file_stream, manager_id):
    """Processes bulk CSV product uploads.
    Always auto-generates continuous sequential SKUs to maintain system consistency.
    """
    raw_content = file_stream.stream.read()
    try:
        decoded = raw_content.decode("utf-8-sig")
    except Exception:
        decoded = raw_content.decode("latin1", errors="ignore")
        
    stream = io.StringIO(decoded, newline=None)
    csv_input = csv.DictReader(stream)
    
    db = get_db()
    count = 0
    
    for row in csv_input:
        # Normalize keys (strip whitespace, lowercase lookup)
        row_norm = {k.strip().lower(): v.strip() for k, v in row.items() if k and v}
        
        name = row_norm.get('name') or row_norm.get('product_name') or row_norm.get('title') or row_norm.get('product') or ''
        if not name:
            continue

        # Always assign the continuous sequential SKU
        sku = generate_next_sku(db, manager_id)

        description = row_norm.get('description') or row_norm.get('desc') or row_norm.get('details') or ''
        
        # Price parsing
        price_raw = row_norm.get('price_inr') or row_norm.get('price') or row_norm.get('mrp') or '0'
        price_clean = re.sub(r'[^\d.]', '', price_raw)
        try:
            price_inr = float(price_clean) if price_clean else 0.0
        except ValueError:
            price_inr = 0.0
            
        # Stock parsing
        stock_raw = row_norm.get('stock_qty') or row_norm.get('stock') or row_norm.get('quantity') or row_norm.get('qty') or '0'
        stock_clean = re.sub(r'[^\d]', '', stock_raw)
        try:
            stock_qty = int(stock_clean) if stock_clean else 0
        except ValueError:
            stock_qty = 0
            
        # Status parsing
        status_val = row_norm.get('status', 'Active').capitalize()
        status = 'Active' if status_val not in ('Active', 'Inactive', 'Suspended') else status_val
        
        try:
            db.execute(
                "INSERT INTO tbl_products (manager_id, sku, name, description, price_inr, stock_qty, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (manager_id, sku, name, description, price_inr, stock_qty, status)
            )
            count += 1
            db.commit()
        except Exception as e:
            print(f"Error inserting product '{name}' ({sku}): {e}")
                
    return count

def process_image_upload(file, manager_id, image_folder):
    if not file:
        return False, "No file provided"

    filename = secure_filename(file.filename)
    if not filename:
        return False, "Invalid filename"

    file_identifier, _ext = os.path.splitext(filename)
    file_identifier = file_identifier.strip()

    db = get_db()

    # Fetch product by SKU, product_id, or name
    product = db.execute(
        """SELECT p.product_id, p.sku, m.shop_slug
           FROM tbl_products p
           JOIN tbl_managers m ON p.manager_id = m.manager_id
           WHERE p.manager_id = ? AND (
               p.sku = ?
               OR p.sku = ?
               OR p.sku LIKE ?
               OR p.product_id = ?
               OR LOWER(p.name) = ?
           )
           LIMIT 1""",
        (manager_id, file_identifier,
         f"SKU-{manager_id}-{file_identifier}",
         f"%{file_identifier}",
         file_identifier, file_identifier.lower())
    ).fetchone()

    if not product:
        return False, f"No matching product found for: {file_identifier}"

    # Upload via unified image uploader (Cloudinary / local fallback)
    img_result, err = upload_product_image_file(
        file.stream, manager_id, product['sku'],
        image_folder, shop_slug=product['shop_slug']
    )
    if err or not img_result:
        return False, err or "Failed to upload image"

    db.execute(
        "UPDATE tbl_products SET image_path = ? WHERE product_id = ?",
        (img_result, product['product_id'])
    )
    db.commit()
    return True, f"Image attached to {product['sku']}"


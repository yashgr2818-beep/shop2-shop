import csv
import os
import re
from PIL import Image
from database import get_db
from werkzeug.utils import secure_filename
import io

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
        
    # Strip extension to get SKU or identifier
    file_identifier, ext = os.path.splitext(filename)
    file_identifier = file_identifier.strip()
    
    db = get_db()
    # Check if identifier matches SKU, candidate formatted SKU, product_id, or name
    product = db.execute(
        """SELECT product_id, sku FROM tbl_products 
           WHERE manager_id = ? AND (
               sku = ? 
               OR sku = ? 
               OR sku LIKE ?
               OR product_id = ?
               OR LOWER(name) = ?
           )
           LIMIT 1""", 
        (manager_id, file_identifier, f"SKU-{manager_id}-{file_identifier}", f"%{file_identifier}", file_identifier, file_identifier.lower())
    ).fetchone()
    
    if not product:
        return False, f"No matching product found for: {file_identifier}"
        
    # Process image with Pillow
    try:
        img = Image.open(file.stream)
        
        # Convert to RGB if it's RGBA or P
        if img.mode in ('RGBA', 'P'):
            img = img.convert('RGB')
            
        # Resize/Compress
        img.thumbnail((800, 800))
        
        # Save compressed image
        new_filename = f"{manager_id}_{product['sku']}.jpg"
        save_path = os.path.join(image_folder, new_filename)
        img.save(save_path, format="JPEG", quality=85)
        
        # Update DB
        db.execute(
            "UPDATE tbl_products SET image_path = ? WHERE product_id = ?",
            (new_filename, product['product_id'])
        )
        db.commit()
        
        return True, f"Image attached to {product['sku']}"
    except Exception as e:
        return False, f"Image processing failed: {str(e)}"


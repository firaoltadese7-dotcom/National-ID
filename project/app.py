from flask import Flask, request, send_file, render_template_string, redirect, url_for, flash, session
import fitz  # PyMuPDF
from PIL import Image, ImageDraw, ImageFont
import os, uuid, random, re, shutil, json, hashlib, sqlite3, time
import pytesseract
from datetime import datetime, timedelta
from ethiopian_date import EthiopianDateConverter
from functools import wraps
import qrcode
from io import BytesIO
import base64

app = Flask(__name__)
app.secret_key = 'free_service_secret_key_2024'  # Secret key free version

# 1. Foldaroota
UPLOAD_FOLDER = "uploads"
IMG_FOLDER = "extracted_images"
CARD_FOLDER = "cards"
DB_PATH = "database.db"
FONT_PATH = "fonts/AbyssinicaSIL-Regular.ttf"
TEMPLATE_PATH = "static/id_card_template.png"

# FREE SERVICE - NO PAYMENT REQUIRED
FREE_MODE = True  # Hardcoded FREE mode

for folder in [UPLOAD_FOLDER, IMG_FOLDER, CARD_FOLDER]:
    os.makedirs(folder, exist_ok=True)

# 2. DATABASE SETUP - FREE VERSION
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Users table
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  username TEXT UNIQUE NOT NULL,
                  email TEXT UNIQUE NOT NULL,
                  password TEXT NOT NULL,
                  phone TEXT,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  is_active INTEGER DEFAULT 1,
                  free_cards_generated INTEGER DEFAULT 0)''')  # Changed from balance
    
    # FREE Transactions table - for tracking only
    c.execute('''CREATE TABLE IF NOT EXISTS free_transactions
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER NOT NULL,
                  cards_generated INTEGER DEFAULT 1,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY (user_id) REFERENCES users (id))''')
    
    # Cards generated table
    c.execute('''CREATE TABLE IF NOT EXISTS cards_generated
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER NOT NULL,
                  card_path TEXT NOT NULL,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY (user_id) REFERENCES users (id))''')
    
    # Password reset tokens
    c.execute('''CREATE TABLE IF NOT EXISTS password_resets
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER NOT NULL,
                  token TEXT UNIQUE NOT NULL,
                  expires_at TIMESTAMP NOT NULL,
                  used INTEGER DEFAULT 0,
                  FOREIGN KEY (user_id) REFERENCES users (id))''')
    
    conn.commit()
    
    # Check if free_cards_generated column exists, if not add it
    try:
        c.execute("SELECT free_cards_generated FROM users LIMIT 1")
    except sqlite3.OperationalError:
        # Column doesn't exist, add it
        print("Adding free_cards_generated column to users table...")
        c.execute("ALTER TABLE users ADD COLUMN free_cards_generated INTEGER DEFAULT 0")
    
    # Check if old tables exist and remove them if needed
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='transactions'")
    if c.fetchone():
        print("Dropping old transactions table...")
        c.execute("DROP TABLE transactions")
    
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='cards_generated'")
    if c.fetchone():
        # Check if cards_generated has transaction_id column (old schema)
        c.execute("PRAGMA table_info(cards_generated)")
        columns = [col[1] for col in c.fetchall()]
        if 'transaction_id' in columns:
            print("Dropping old cards_generated table...")
            c.execute("DROP TABLE cards_generated")
            # Recreate with new schema
            c.execute('''CREATE TABLE IF NOT EXISTS cards_generated
                         (id INTEGER PRIMARY KEY AUTOINCREMENT,
                          user_id INTEGER NOT NULL,
                          card_path TEXT NOT NULL,
                          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                          FOREIGN KEY (user_id) REFERENCES users (id))''')
    
    conn.commit()
    conn.close()

init_db()

# 3. FREE PRICING - ALL ZERO
PRICING = {
    1: 0,
    30: 0,
    50: 0,
    100: 0,
    200: 0,
    500: 0
}

# 4. HELPER FUNCTIONS
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(password, hashed):
    return hash_password(password) == hashed

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def clear_old_files():
    """Foldaroota qulqulleessuu"""
    for folder in [UPLOAD_FOLDER, IMG_FOLDER, CARD_FOLDER]:
        for filename in os.listdir(folder):
            file_path = os.path.join(folder, filename)
            try:
                if os.path.isfile(file_path):
                    os.remove(file_path)
            except Exception as e:
                print(f"Error deleting {file_path}: {e}")

def generate_transaction_id():
    return f"FREE_{uuid.uuid4().hex[:8].upper()}_{int(time.time())}"

def save_user_uploaded_image(uploaded_file):
    if not uploaded_file or uploaded_file.filename == '':
        return None
    
    unique_id = uuid.uuid4().hex[:5]
    filename = uploaded_file.filename.lower()
    
    if filename.endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff')):
        ext = 'png'
        if filename.endswith('.jpg') or filename.endswith('.jpeg'):
            ext = 'jpg'
        elif filename.endswith('.gif'):
            ext = 'gif'
        elif filename.endswith('.bmp'):
            ext = 'bmp'
        elif filename.endswith('.tiff'):
            ext = 'tiff'
        
        img_name = f"page2_img0_{unique_id}.{ext}"
        save_path = os.path.join(IMG_FOLDER, img_name)
        uploaded_file.save(save_path)
        
        try:
            img = Image.open(save_path).convert("RGBA")
            datas = img.getdata()
            newData = []
            for item in datas:
                if item[0] > 220 and item[1] > 220 and item[2] > 220:
                    newData.append((255, 255, 255, 0))
                else:
                    newData.append(item)
            img.putdata(newData)
            
            png_path = os.path.join(IMG_FOLDER, f"page2_img0_{unique_id}.png")
            img.save(png_path, "PNG")
            
            if ext != 'png':
                os.remove(save_path)
                save_path = png_path
            
            return save_path
        except Exception as e:
            print(f"Error processing uploaded image: {e}")
            return save_path
    
    return None

def prepare_images_for_card(extracted_images, user_photo_path):
    image_paths = []
    
    if extracted_images and len(extracted_images) > 0:
        image_paths.append(extracted_images[0])
    else:
        image_paths.append(None)
    
    image_paths.append(user_photo_path)
    image_paths.append(None)
    image_paths.append(None)
    
    return image_paths

# 5. PDF PROCESSING FUNCTIONS
def extract_all_images(pdf_path):
    doc = fitz.open(pdf_path)
    image_paths = []
    
    for page_index in range(len(doc)):
        page = doc[page_index]
        image_list = page.get_images(full=True)
        
        for img_index, img in enumerate(image_list):
            xref = img[0]
            base_image = doc.extract_image(xref)
            image_bytes = base_image["image"]
            ext = base_image["ext"]
            
            img_name = f"page{page_index+1}_img{img_index}_{uuid.uuid4().hex[:5]}.{ext}"
            path = os.path.join(IMG_FOLDER, img_name)
            
            with open(path, "wb") as f:
                f.write(image_bytes)
            image_paths.append(path)
            
    doc.close()
    return image_paths

def extract_pdf_data(pdf_path, image_paths):
    doc = fitz.open(pdf_path)
    page = doc[0]
    full_text = page.get_text("text")

    fin_matches = re.findall(r"\b\d{4}\s\d{4}\s\d{4}\b", full_text)
    fin_number = fin_matches[-1].strip() if fin_matches else None

    if not fin_number:
        for path in image_paths:
            if "page1_img3" in os.path.basename(path):
                try:
                    img = Image.open(path).convert('L')
                    image_text = pytesseract.image_to_string(img)
                    img_fin = re.findall(r"\b\d{4}\s\d{4}\s\d{4}\b", image_text)
                    if img_fin:
                        fin_number = img_fin[0].strip()
                        break
                except:
                    pass

    if not fin_number: fin_number = "Hin Argamne"

    fan_matches = re.findall(r"\b\d{4}\s\d{4}\s\d{4}\s\d{4}\b", full_text)
    fan_number = fan_matches[0].replace(" ", "") if fan_matches else "Hin Argamne"

    fullname_text = page.get_textbox(fitz.Rect(50, 360, 300, 372)).strip()
    fullname_fixed = fullname_text.replace("| ", "\n")
    
    dob_text = page.get_textbox(fitz.Rect(50, 430, 300, 435)).strip()
    sex_text = page.get_textbox(fitz.Rect(50, 500, 300, 510)).strip()
    nationality_text = page.get_textbox(fitz.Rect(50, 560, 300, 575)).strip()
    
    region_text = page.get_textbox(fitz.Rect(50, 400, 300, 410)).strip()
    region_fixed = region_text.replace("| ", "\n")
    
    zone_text = page.get_textbox(fitz.Rect(50, 460, 400, 470)).strip()
    zone_fixed = zone_text.replace("| ", "\n")
    
    woreda_text = page.get_textbox(fitz.Rect(50, 527, 300, 537)).strip()
    woreda_fixed = woreda_text.replace("| ", "\n")

    data = {
        "fullname": fullname_fixed,
        "dob": dob_text,
        "sex": sex_text,
        "nationality": nationality_text,
        "phone": page.get_textbox(fitz.Rect(50, 600, 300, 625)).strip(),
        "region": region_fixed,
        "zone": zone_fixed,
        "woreda": woreda_fixed,
        "fan": page.get_textbox(fitz.Rect(350, 100, 500, 120)).strip(),
    }
    doc.close()
    return data

def generate_card(data, image_paths, fin_number):
    card = Image.open(TEMPLATE_PATH).convert("RGBA")
    draw = ImageDraw.Draw(card)

    now = datetime.now()
    gc_issued = now.strftime("%d/%m/%Y")
    eth_issued_obj = EthiopianDateConverter.to_ethiopian(now.year, now.month, now.day)
    ec_issued = f"{eth_issued_obj.day:02d}/{eth_issued_obj.month:02d}/{eth_issued_obj.year}"
    
    gc_expiry = now.replace(year=now.year + 8).strftime("%d/%m/%Y")
    ec_expiry = f"{eth_issued_obj.day:02d}/{eth_issued_obj.month:02d}/{eth_issued_obj.year + 8}"
    expiry_full = f"{gc_expiry} | {ec_expiry}"

    # Original photo
    if len(image_paths) > 0 and image_paths[0] is not None:
        try:
            original_photo = Image.open(image_paths[0]).convert("RGBA")
            datas = original_photo.getdata()
            newData = []
            for item in datas:
                if item[0] > 220 and item[1] > 220 and item[2] > 220:
                    newData.append((255, 255, 255, 0))
                else:
                    newData.append(item)
            original_photo.putdata(newData)
            
            p_large = original_photo.resize((310, 400))
            card.paste(p_large, (65, 200), p_large)
            
            p_small = original_photo.resize((100, 135))
            card.paste(p_small, (800, 450), p_small)
        except Exception as e:
            print(f"Error processing original photo: {e}")

    # New photo
    if len(image_paths) > 1 and image_paths[1] is not None:
        try:
            new_photo = Image.open(image_paths[1]).convert("RGBA")
            datas = new_photo.getdata()
            newData = []
            for item in datas:
                if item[0] > 220 and item[1] > 220 and item[2] > 220:
                    newData.append((255, 255, 255, 0))
                else:
                    newData.append(item)
            new_photo.putdata(newData)
            
            new_resized = new_photo.resize((530, 550))
            card.paste(new_resized, (1550, 30), new_resized)
        except Exception as e:
            print(f"Error processing new photo: {e}")

    # FIN number
    try:
        fin_font = ImageFont.truetype(FONT_PATH, 25)
    except:
        fin_font = ImageFont.load_default()
    
    draw.text((1265, 545), fin_number, fill="black", font=fin_font)

    # Other text
    try:
        font = ImageFont.truetype(FONT_PATH, 37)
        small_multiline = ImageFont.truetype(FONT_PATH, 28)
        small = ImageFont.truetype(FONT_PATH, 32)
        iss_font = ImageFont.truetype(FONT_PATH, 25)
        sn_font = ImageFont.truetype(FONT_PATH, 26) 
    except:
        font = small = iss_font = sn_font = ImageFont.load_default()

    draw.text((405, 170), data["fullname"], fill="black", font=font, spacing=8)
    draw.text((405, 305), data["dob"], fill="black", font=small)
    draw.text((405, 375), data["sex"], fill="black", font=small)
    draw.text((1130, 165), data["nationality"], fill="black", font=small)
    draw.text((1130, 235), data["region"], fill="black", font=small_multiline, spacing=5)
    draw.text((1130, 315), data["zone"], fill="black", font=small_multiline, spacing=5)
    draw.text((1130, 390), data["woreda"], fill="black", font=small_multiline, spacing=5)
    draw.text((1130, 65), data["phone"], fill="black", font=small)
    draw.text((470, 500), data["fan"], fill="black", font=small)
    draw.text((405, 440), expiry_full, fill="black", font=small)
    draw.text((1930, 595), f" {random.randint(10000000, 99999999)}", fill="black", font=sn_font)

    def draw_rotated_text(canvas, text, position, angle, font, color):
        text_bbox = font.getbbox(text)
        txt_img = Image.new("RGBA", (text_bbox[2], text_bbox[3] + 10), (255, 255, 255, 0))
        d = ImageDraw.Draw(txt_img)
        d.text((0, 0), text, fill=color, font=font)
        rotated = txt_img.rotate(angle, expand=True)
        canvas.paste(rotated, position, rotated)

    draw_rotated_text(card, gc_issued, (13, 120), 90, iss_font, "black")
    draw_rotated_text(card, ec_issued, (13, 390), 90, iss_font, "black")

    out_path = os.path.join(CARD_FOLDER, f"id_{uuid.uuid4().hex[:6]}.png")
    card.convert("RGB").save(out_path)
    return out_path

# 6. ROUTES - FREE VERSION
@app.route('/')
def home():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']
        confirm_password = request.form['confirm_password']
        phone = request.form.get('phone', '')
        
        if password != confirm_password:
            flash('Passwords do not match!', 'error')
            return redirect(url_for('signup'))
        
        hashed_password = hash_password(password)
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        try:
            c.execute("INSERT INTO users (username, email, password, phone) VALUES (?, ?, ?, ?)",
                     (username, email, hashed_password, phone))
            conn.commit()
            flash('Account created successfully! Please login.', 'success')
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash('Username or email already exists!', 'error')
            return redirect(url_for('signup'))
        finally:
            conn.close()
    
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Sign Up - FREE ID Card Service</title>
        <style>
            body { font-family: Arial; max-width: 400px; margin: 50px auto; padding: 20px; background: #f0f7ff; }
            .form-group { margin-bottom: 15px; }
            label { display: block; margin-bottom: 5px; }
            input { width: 100%; padding: 10px; box-sizing: border-box; border: 1px solid #ddd; border-radius: 5px; }
            button { background: #27ae60; color: white; padding: 12px 20px; border: none; border-radius: 5px; cursor: pointer; width: 100%; font-size: 16px; }
            .error { color: red; background: #ffebee; padding: 10px; border-radius: 5px; margin-bottom: 10px; }
            .success { color: green; background: #e8f5e9; padding: 10px; border-radius: 5px; margin-bottom: 10px; }
            .free-banner { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 15px; border-radius: 10px; text-align: center; margin-bottom: 20px; }
            .free-banner h2 { margin: 0; }
        </style>
    </head>
    <body>
        <div class="free-banner">
            <h2>🎉 FREE ID CARD SERVICE</h2>
            <p>No payment required - Generate unlimited ID cards!</p>
        </div>
        
        <h2>Sign Up</h2>
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                {% for category, message in messages %}
                    <div class="{{ category }}">{{ message }}</div>
                {% endfor %}
            {% endif %}
        {% endwith %}
        <form method="POST">
            <div class="form-group">
                <label>Username:</label>
                <input type="text" name="username" required>
            </div>
            <div class="form-group">
                <label>Email:</label>
                <input type="email" name="email" required>
            </div>
            <div class="form-group">
                <label>Password:</label>
                <input type="password" name="password" required>
            </div>
            <div class="form-group">
                <label>Confirm Password:</label>
                <input type="password" name="confirm_password" required>
            </div>
            <div class="form-group">
                <label>Phone (optional):</label>
                <input type="text" name="phone">
            </div>
            <button type="submit">Sign Up</button>
        </form>
        <p style="text-align: center; margin-top: 20px;">Already have an account? <a href="/login">Login</a></p>
    </body>
    </html>
    ''')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT id, password FROM users WHERE username = ? AND is_active = 1", (username,))
        user = c.fetchone()
        conn.close()
        
        if user and verify_password(password, user[1]):
            session['user_id'] = user[0]
            session['username'] = username
            flash('Login successful!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid username or password!', 'error')
            return redirect(url_for('login'))
    
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Login - FREE ID Card Service</title>
        <style>
            body { font-family: Arial; max-width: 400px; margin: 50px auto; padding: 20px; background: #f0f7ff; }
            .form-group { margin-bottom: 15px; }
            label { display: block; margin-bottom: 5px; }
            input { width: 100%; padding: 10px; box-sizing: border-box; border: 1px solid #ddd; border-radius: 5px; }
            button { background: #3498db; color: white; padding: 12px 20px; border: none; border-radius: 5px; cursor: pointer; width: 100%; font-size: 16px; }
            .error { color: red; background: #ffebee; padding: 10px; border-radius: 5px; margin-bottom: 10px; }
            .success { color: green; background: #e8f5e9; padding: 10px; border-radius: 5px; margin-bottom: 10px; }
            .free-banner { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 15px; border-radius: 10px; text-align: center; margin-bottom: 20px; }
            .free-banner h2 { margin: 0; }
        </style>
    </head>
    <body>
        <div class="free-banner">
            <h2>🎉 FREE ID CARD SERVICE</h2>
            <p>Generate ID cards without any payment!</p>
        </div>
        
        <h2>Login</h2>
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                {% for category, message in messages %}
                    <div class="{{ category }}">{{ message }}</div>
                {% endfor %}
            {% endif %}
        {% endwith %}
        <form method="POST">
            <div class="form-group">
                <label>Username:</label>
                <input type="text" name="username" required>
            </div>
            <div class="form-group">
                <label>Password:</label>
                <input type="password" name="password" required>
            </div>
            <button type="submit">Login</button>
        </form>
        <p style="text-align: center; margin-top: 20px;">
            Don't have an account? <a href="/signup">Sign Up</a><br>
            <a href="/forgot-password">Forgot Password?</a>
        </p>
    </body>
    </html>
    ''')

@app.route('/dashboard')
@login_required
def dashboard():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Get user info
    c.execute("SELECT username, email, phone, free_cards_generated FROM users WHERE id = ?", (session['user_id'],))
    user = c.fetchone()
    
    # Get cards generated count
    c.execute("SELECT COUNT(*) FROM cards_generated WHERE user_id = ?", (session['user_id'],))
    total_cards = c.fetchone()[0]
    
    # Get recent card generations
    c.execute('''SELECT card_path, created_at FROM cards_generated 
                 WHERE user_id = ? ORDER BY created_at DESC LIMIT 5''',
              (session['user_id'],))
    recent_cards = c.fetchall()
    
    conn.close()
    
    # Create recent cards HTML
    recent_cards_html = ""
    if recent_cards:
        for card in recent_cards:
            filename = os.path.basename(card[0])
            recent_cards_html += f'''
                <tr>
                    <td>{filename}</td>
                    <td>{card[1]}</td>
                    <td><a href="/download-card/{filename}" target="_blank">Download</a></td>
                </tr>
            '''
    else:
        recent_cards_html = '<tr><td colspan="3">No cards generated yet</td></tr>'
    
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Dashboard - FREE ID Card Service</title>
        <style>
            body { font-family: Arial; max-width: 1200px; margin: 0 auto; padding: 20px; background: #f9f9f9; }
            .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 30px; }
            .user-info { background: white; padding: 20px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); margin-bottom: 20px; }
            .free-banner { background: linear-gradient(135deg, #27ae60 0%, #2ecc71 100%); color: white; padding: 20px; border-radius: 10px; margin-bottom: 20px; text-align: center; }
            .stats { display: flex; justify-content: space-between; margin: 20px 0; }
            .stat-card { flex: 1; padding: 20px; background: white; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); margin: 0 10px; text-align: center; }
            .stat-value { font-size: 32px; font-weight: bold; color: #27ae60; }
            .stat-label { color: #666; margin-top: 10px; }
            .btn { padding: 12px 24px; color: white; text-decoration: none; border-radius: 5px; display: inline-block; margin: 5px; }
            .btn-primary { background: #3498db; }
            .btn-success { background: #27ae60; }
            .btn-warning { background: #f39c12; }
            .recent-cards { background: white; padding: 20px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); margin-top: 20px; }
            table { width: 100%; border-collapse: collapse; margin-top: 10px; }
            th, td { padding: 12px; text-align: left; border-bottom: 1px solid #ddd; }
            th { background: #f8f9fa; }
        </style>
    </head>
    <body>
        <div class="free-banner">
            <h1>🎉 FREE ID CARD GENERATION SERVICE</h1>
            <p>Generate unlimited ID cards without any payment!</p>
        </div>
        
        <div class="header">
            <h2>Welcome, {{ username }}!</h2>
            <div>
                <a href="/generate" class="btn btn-success">Generate New ID Card</a>
                <a href="/logout" class="btn btn-warning">Logout</a>
            </div>
        </div>
        
        <div class="stats">
            <div class="stat-card">
                <div class="stat-value">{{ total_cards }}</div>
                <div class="stat-label">Total Cards Generated</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">FREE</div>
                <div class="stat-label">Service Type</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">Unlimited</div>
                <div class="stat-label">Cards Remaining</div>
            </div>
        </div>
        
        <div class="user-info">
            <h3>Account Information</h3>
            <p><strong>Email:</strong> {{ email }}</p>
            <p><strong>Phone:</strong> {{ phone or 'Not provided' }}</p>
            <p><strong>Account Created:</strong> Free Service User</p>
        </div>
        
        <div style="text-align: center; margin: 30px 0;">
            <a href="/generate" class="btn btn-success" style="font-size: 18px; padding: 15px 30px;">
                🚀 Generate FREE ID Card Now
            </a>
        </div>
        
        <div class="recent-cards">
            <h3>Recent Cards Generated</h3>
            {% if total_cards > 0 %}
            <table>
                <tr>
                    <th>File Name</th>
                    <th>Generated Date</th>
                    <th>Action</th>
                </tr>
                {{ recent_cards_html|safe }}
            </table>
            {% else %}
            <p style="text-align: center; color: #666; padding: 20px;">
                No cards generated yet. Click the button above to generate your first FREE ID card!
            </p>
            {% endif %}
        </div>
        
        <div style="background: #e8f4f8; padding: 20px; border-radius: 10px; margin-top: 30px;">
            <h3>📝 How to Generate FREE ID Cards:</h3>
            <ol>
                <li>Click "Generate New ID Card" button</li>
                <li>Upload your PDF file (from government system)</li>
                <li>Upload your cropped photo (white background removed)</li>
                <li>Enter your 12-digit FIN number</li>
                <li>Click "Generate ID Card" - It's FREE!</li>
                <li>Download your generated ID card</li>
            </ol>
            <p><strong>Note:</strong> This is a FREE service. No payment is required at any stage.</p>
        </div>
    </body>
    </html>
    ''', username=user[0], email=user[1], phone=user[2], 
       total_cards=total_cards, recent_cards_html=recent_cards_html)

@app.route('/generate', methods=['GET', 'POST'])
@login_required
def generate():
    if request.method == 'POST':
        # FREE SERVICE - No payment check needed
        
        # Process the card generation
        pdf = request.files.get("pdf")
        user_photo = request.files.get("photo")
        fin_number = request.form.get("fin_number", "")
        
        errors = []
        
        if not pdf or pdf.filename == '':
            errors.append("PDF Fayilaa filachuun barbaachisaadha!")
        
        if not user_photo or user_photo.filename == '':
            errors.append("Suura Ashaaraa Crop Ta'e Qofa filachuun barbaachisaadha!")
        
        if not fin_number:
            errors.append("FIN Lakkoofsaa galchuu barbaachisaadha!")
        elif not fin_number.isdigit() or len(fin_number) != 12:
            errors.append("FIN Lakkoofsaan dijiitii 12 qofa ta'uu qaba!")
        
        if errors:
            error_message = "<br>".join(errors)
            return f'''
            <div style="text-align: center; margin-top: 50px; font-family: sans-serif;">
                <h2 style="color: #e74c3c;">Error!</h2>
                <div style="color: #c0392b; background-color: #fadbd8; padding: 20px; border-radius: 10px; display: inline-block;">
                    {error_message}
                </div>
                <br><br>
                <a href="/generate" style="padding: 10px 20px; background: #3498db; color: white; text-decoration: none; border-radius: 5px;">Try Again</a>
            </div>
            ''', 400
        
        pdf_path = os.path.join(UPLOAD_FOLDER, f"temp_{uuid.uuid4().hex[:5]}.pdf")
        pdf.save(pdf_path)
        
        try:
            extracted_images = extract_all_images(pdf_path)
            data = extract_pdf_data(pdf_path, extracted_images)
            user_photo_path = save_user_uploaded_image(user_photo)
            
            if not user_photo_path:
                return "Suura Ashaaraa Crop Ta'e Qofa save godhuu keessatti dogoggora ta'e", 400
            
            final_image_paths = prepare_images_for_card(extracted_images, user_photo_path)
            card_path = generate_card(data, final_image_paths, fin_number)
            
            # Record the card generation
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("INSERT INTO cards_generated (user_id, card_path) VALUES (?, ?)",
                     (session['user_id'], card_path))
            
            # Update free cards count
            c.execute("UPDATE users SET free_cards_generated = free_cards_generated + 1 WHERE id = ?",
                     (session['user_id'],))
            
            # Record in free transactions
            c.execute("INSERT INTO free_transactions (user_id) VALUES (?)",
                     (session['user_id'],))
            
            conn.commit()
            conn.close()
            
            return send_file(card_path, mimetype='image/png', as_attachment=True, download_name="Fayda_Card.png")
            
        except Exception as e:
            return f"Error: {str(e)}", 500
    
    # GET request - show form
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Generate FREE ID Card</title>
        <style>
            body { font-family: Arial; max-width: 800px; margin: 0 auto; padding: 20px; background: #f0f7ff; }
            .form-container { background: white; padding: 30px; border-radius: 15px; box-shadow: 0 4px 20px rgba(0,0,0,0.1); }
            .form-group { margin-bottom: 25px; padding: 20px; background: #f8f9fa; border-radius: 10px; }
            label { display: block; margin-bottom: 10px; font-weight: bold; font-size: 16px; }
            input { width: 100%; padding: 12px; box-sizing: border-box; border: 2px solid #ddd; border-radius: 8px; font-size: 16px; }
            input:focus { border-color: #3498db; outline: none; }
            button { background: linear-gradient(135deg, #27ae60 0%, #2ecc71 100%); color: white; padding: 15px 40px; border: none; border-radius: 8px; cursor: pointer; width: 100%; font-size: 18px; font-weight: bold; }
            button:hover { background: linear-gradient(135deg, #219653 0%, #27ae60 100%); }
            .free-badge { background: #e74c3c; color: white; padding: 5px 15px; border-radius: 20px; font-size: 14px; font-weight: bold; display: inline-block; margin-left: 10px; }
            .note { background: #e8f4f8; padding: 20px; border-radius: 10px; margin-top: 30px; }
            .step-guide { background: #fff3cd; padding: 20px; border-radius: 10px; margin-bottom: 30px; }
            .step { display: flex; align-items: center; margin-bottom: 15px; }
            .step-number { background: #3498db; color: white; width: 30px; height: 30px; border-radius: 50%; display: flex; align-items: center; justify-content: center; margin-right: 15px; }
        </style>
    </head>
    <body>
        <div style="text-align: center; margin-bottom: 30px;">
            <h1 style="color: #27ae60;">🎉 Generate FREE ID Card</h1>
            <p style="font-size: 18px; color: #666;">No payment required - Completely FREE service!</p>
        </div>
        
        <div class="step-guide">
            <h3>📋 Step-by-Step Guide:</h3>
            <div class="step">
                <div class="step-number">1</div>
                <div>Upload PDF file from government system</div>
            </div>
            <div class="step">
                <div class="step-number">2</div>
                <div>Upload cropped photo (white background removed)</div>
            </div>
            <div class="step">
                <div class="step-number">3</div>
                <div>Enter your 12-digit FIN number</div>
            </div>
            <div class="step">
                <div class="step-number">4</div>
                <div>Click "Generate FREE ID Card" button</div>
            </div>
        </div>
        
        <div class="form-container">
            <form method="POST" enctype="multipart/form-data" onsubmit="return validateForm()">
                <div class="form-group">
                    <label for="pdf">PDF Fayilaa (Mandatory) <span class="free-badge">FREE</span></label>
                    <input type="file" name="pdf" id="pdf" accept=".pdf" required>
                    <small style="color: #666;">PDF file from government system containing your information</small>
                </div>
                
                <div class="form-group">
                    <label for="photo">Suura Ashaaraa Crop Ta'e Qofa (Mandatory) <span class="free-badge">FREE</span></label>
                    <input type="file" name="photo" id="photo" accept="image/*" required>
                    <small style="color: #666;">Suuraa ashaaraa crop ta'e qofa filadhu (background white ta'ee dhiisu)</small>
                </div>
                
                <div class="form-group">
                    <label for="fin_number">FIN Lakkoofsaa (Mandatory) <span class="free-badge">FREE</span></label>
                    <input type="text" name="fin_number" id="fin_number" 
                           pattern="\\d{12}" 
                           title="Digitii 12 qofa galchuu qabda" 
                           placeholder="123456789012" maxlength="12" required>
                    <div id="fin_error" style="color: red; display: none; margin-top: 10px; padding: 10px; background: #ffebee; border-radius: 5px;">
                        FIN Lakkoofsaan dijiitii 12 qofa ta'uu qaba!
                    </div>
                </div>
                
                <button type="submit">
                    🚀 Generate FREE ID Card
                </button>
            </form>
        </div>
        
        <div class="note">
            <h3>📝 Important Information:</h3>
            <p>✅ <strong>FREE SERVICE:</strong> No payment required at any stage</p>
            <p>✅ <strong>UNLIMITED CARDS:</strong> Generate as many ID cards as you need</p>
            <p>✅ <strong>INSTANT GENERATION:</strong> Get your ID card immediately</p>
            <p>✅ <strong>NO TRANSACTION ID:</strong> No need for payment verification</p>
            <p>✅ <strong>SECURE:</strong> Your data is processed securely</p>
            <br>
            <p><strong>Note:</strong> This service extracts information from government PDF files and generates ID cards in the standard format.</p>
        </div>
        
        <div style="text-align: center; margin-top: 30px;">
            <a href="/dashboard" style="color: #3498db; text-decoration: none; font-size: 16px;">
                ← Back to Dashboard
            </a>
        </div>
        
        <script>
            function validateForm() {
                const finInput = document.getElementById('fin_number');
                const finError = document.getElementById('fin_error');
                
                if (finInput.value.length !== 12 || !/^\\d+$/.test(finInput.value)) {
                    finError.style.display = 'block';
                    finInput.focus();
                    return false;
                } else {
                    finError.style.display = 'none';
                }
                return true;
            }
            
            // Real-time validation
            document.getElementById('fin_number').addEventListener('input', function(e) {
                const finError = document.getElementById('fin_error');
                if (this.value.length !== 12 || !/^\\d+$/.test(this.value)) {
                    finError.style.display = 'block';
                } else {
                    finError.style.display = 'none';
                }
            });
        </script>
    </body>
    </html>
    ''')

@app.route('/download-card/<filename>')
@login_required
def download_card(filename):
    """Download a previously generated card"""
    card_path = os.path.join(CARD_FOLDER, filename)
    if os.path.exists(card_path):
        return send_file(card_path, mimetype='image/png', as_attachment=True, download_name=filename)
    else:
        flash('Card not found!', 'error')
        return redirect(url_for('dashboard'))

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form['email']
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT id FROM users WHERE email = ?", (email,))
        user = c.fetchone()
        
        if user:
            token = uuid.uuid4().hex
            expires_at = datetime.now() + timedelta(hours=1)
            c.execute("INSERT INTO password_resets (user_id, token, expires_at) VALUES (?, ?, ?)",
                     (user[0], token, expires_at))
            conn.commit()
            flash(f'Password reset link has been sent (demo token: {token})', 'success')
        else:
            flash('Email not found!', 'error')
        conn.close()
        
        return redirect(url_for('forgot_password'))
    
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Forgot Password</title>
        <style>
            body { font-family: Arial; max-width: 400px; margin: 50px auto; padding: 20px; }
            .form-group { margin-bottom: 15px; }
            label { display: block; margin-bottom: 5px; }
            input { width: 100%; padding: 10px; box-sizing: border-box; }
            button { background: #f39c12; color: white; padding: 12px 20px; border: none; border-radius: 5px; cursor: pointer; width: 100%; }
        </style>
    </head>
    <body>
        <h2>Forgot Password</h2>
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                {% for category, message in messages %}
                    <div class="{{ category }}">{{ message }}</div>
                {% endfor %}
            {% endif %}
        {% endwith %}
        <form method="POST">
            <div class="form-group">
                <label>Email:</label>
                <input type="email" name="email" required>
            </div>
            <button type="submit">Send Reset Link</button>
        </form>
        <p><a href="/login">Back to Login</a></p>
    </body>
    </html>
    ''')

@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT user_id, expires_at FROM password_resets WHERE token = ? AND used = 0", (token,))
    reset = c.fetchone()
    
    if not reset:
        conn.close()
        flash('Invalid or expired reset token!', 'error')
        return redirect(url_for('login'))
    
    if datetime.now() > datetime.fromisoformat(reset[1]):
        conn.close()
        flash('Reset token has expired!', 'error')
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        password = request.form['password']
        confirm_password = request.form['confirm_password']
        
        if password != confirm_password:
            flash('Passwords do not match!', 'error')
            return redirect(url_for('reset_password', token=token))
        
        hashed_password = hash_password(password)
        c.execute("UPDATE users SET password = ? WHERE id = ?", (hashed_password, reset[0]))
        c.execute("UPDATE password_resets SET used = 1 WHERE token = ?", (token,))
        conn.commit()
        conn.close()
        
        flash('Password reset successful! Please login.', 'success')
        return redirect(url_for('login'))
    
    conn.close()
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Reset Password</title>
        <style>
            body { font-family: Arial; max-width: 400px; margin: 50px auto; padding: 20px; }
            .form-group { margin-bottom: 15px; }
            label { display: block; margin-bottom: 5px; }
            input { width: 100%; padding: 10px; box-sizing: border-box; }
            button { background: #27ae60; color: white; padding: 12px 20px; border: none; border-radius: 5px; cursor: pointer; width: 100%; }
        </style>
    </head>
    <body>
        <h2>Reset Password</h2>
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                {% for category, message in messages %}
                    <div class="{{ category }}">{{ message }}</div>
                {% endfor %}
            {% endif %}
        {% endwith %}
        <form method="POST">
            <div class="form-group">
                <label>New Password:</label>
                <input type="password" name="password" required>
            </div>
            <div class="form-group">
                <label>Confirm New Password:</label>
                <input type="password" name="confirm_password" required>
            </div>
            <button type="submit">Reset Password</button>
        </form>
    </body>
    </html>
    ''')

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out successfully!', 'success')
    return redirect(url_for('login'))

if __name__ == "__main__":
    # Clear old files on startup
    clear_old_files()
    
    print("🎉 FREE ID Card Service Started!")
    print("✅ No payment required - Completely FREE")
    print("✅ Access at: http://localhost:5000")
    print("✅ Default port: 5000")
    
    app.run(debug=True, host='0.0.0.0', port=5000)
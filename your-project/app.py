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
app.secret_key = os.environ.get('SECRET_KEY', 'free_service_secret_key_2024')

# 1. Foldaroota
UPLOAD_FOLDER = "uploads"
IMG_FOLDER = "extracted_images"
CARD_FOLDER = "cards"
DB_PATH = os.path.join(os.getcwd(), "database.db")
FONT_PATH = "fonts/AbyssinicaSIL-Regular.ttf"
TEMPLATE_PATH = "static/id_card_template.png"

# FREE SERVICE - NO PAYMENT REQUIRED
FREE_MODE = True

for folder in [UPLOAD_FOLDER, IMG_FOLDER, CARD_FOLDER]:
    os.makedirs(folder, exist_ok=True)

# Tesseract setup
try:
    pytesseract.pytesseract.tesseract_cmd = '/usr/bin/tesseract'
except:
    try:
        pytesseract.pytesseract.tesseract_cmd = 'tesseract'
    except:
        pass

# 2. DATABASE SETUP
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  username TEXT UNIQUE NOT NULL,
                  email TEXT UNIQUE NOT NULL,
                  password TEXT NOT NULL,
                  phone TEXT,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  is_active INTEGER DEFAULT 1,
                  free_cards_generated INTEGER DEFAULT 0)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS free_transactions
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER NOT NULL,
                  cards_generated INTEGER DEFAULT 1,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY (user_id) REFERENCES users (id))''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS cards_generated
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER NOT NULL,
                  card_path TEXT NOT NULL,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY (user_id) REFERENCES users (id))''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS password_resets
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER NOT NULL,
                  token TEXT UNIQUE NOT NULL,
                  expires_at TIMESTAMP NOT NULL,
                  used INTEGER DEFAULT 0,
                  FOREIGN KEY (user_id) REFERENCES users (id))''')
    
    conn.commit()
    conn.close()

init_db()

# 3. HELPER FUNCTIONS
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
    for folder in [UPLOAD_FOLDER, IMG_FOLDER, CARD_FOLDER]:
        for filename in os.listdir(folder):
            file_path = os.path.join(folder, filename)
            try:
                if os.path.isfile(file_path):
                    if os.path.getmtime(file_path) < time.time() - 3600:
                        os.remove(file_path)
            except:
                pass

def save_user_uploaded_image(uploaded_file):
    if not uploaded_file or uploaded_file.filename == '':
        return None
    
    unique_id = uuid.uuid4().hex[:5]
    ext = 'png'
    img_name = f"page2_img0_{unique_id}.{ext}"
    save_path = os.path.join(IMG_FOLDER, img_name)
    
    try:
        uploaded_file.save(save_path)
        img = Image.open(save_path).convert("RGBA")
        datas = img.getdata()
        newData = []
        for item in datas:
            if item[0] > 220 and item[1] > 220 and item[2] > 220:
                newData.append((255, 255, 255, 0))
            else:
                newData.append(item)
        img.putdata(newData)
        img.save(save_path, "PNG")
        return save_path
    except Exception as e:
        print(f"Error: {e}")
        return save_path if os.path.exists(save_path) else None

def prepare_images_for_card(extracted_images, user_photo_path):
    image_paths = []
    if extracted_images and len(extracted_images) > 0:
        image_paths.append(extracted_images[0])
    else:
        image_paths.append(None)
    image_paths.append(user_photo_path)
    return image_paths + [None, None]

# 4. PDF PROCESSING
def extract_all_images(pdf_path):
    try:
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
    except Exception as e:
        print(f"PDF Error: {e}")
        return []

def extract_pdf_data(pdf_path, image_paths):
    try:
        doc = fitz.open(pdf_path)
        page = doc[0]
        full_text = page.get_text("text")
        
        fin_matches = re.findall(r"\b\d{4}\s\d{4}\s\d{4}\b", full_text)
        fin_number = fin_matches[-1].strip() if fin_matches else "Not Found"
        
        fan_matches = re.findall(r"\b\d{4}\s\d{4}\s\d{4}\s\d{4}\b", full_text)
        fan_number = fan_matches[0].replace(" ", "") if fan_matches else "Not Found"
        
        data = {
            "fullname": page.get_textbox(fitz.Rect(50, 360, 300, 372)).strip().replace("| ", "\n"),
            "dob": page.get_textbox(fitz.Rect(50, 430, 300, 435)).strip(),
            "sex": page.get_textbox(fitz.Rect(50, 500, 300, 510)).strip(),
            "nationality": page.get_textbox(fitz.Rect(50, 560, 300, 575)).strip(),
            "phone": page.get_textbox(fitz.Rect(50, 600, 300, 625)).strip(),
            "region": page.get_textbox(fitz.Rect(50, 400, 300, 410)).strip().replace("| ", "\n"),
            "zone": page.get_textbox(fitz.Rect(50, 460, 400, 470)).strip().replace("| ", "\n"),
            "woreda": page.get_textbox(fitz.Rect(50, 527, 300, 537)).strip().replace("| ", "\n"),
            "fan": fan_number,
        }
        doc.close()
        return data
    except Exception as e:
        print(f"Extract Error: {e}")
        return {
            "fullname": "Not Found", "dob": "Not Found", "sex": "Not Found",
            "nationality": "Not Found", "phone": "Not Found", "region": "Not Found",
            "zone": "Not Found", "woreda": "Not Found", "fan": "Not Found"
        }

def generate_card(data, image_paths, fin_number):
    try:
        # Try to open template, if not use blank
        try:
            card = Image.open(TEMPLATE_PATH).convert("RGBA")
        except:
            card = Image.new("RGB", (2100, 1500), color="white")
        
        draw = ImageDraw.Draw(card)
        
        # Dates
        now = datetime.now()
        gc_issued = now.strftime("%d/%m/%Y")
        try:
            eth_issued_obj = EthiopianDateConverter.to_ethiopian(now.year, now.month, now.day)
            ec_issued = f"{eth_issued_obj.day:02d}/{eth_issued_obj.month:02d}/{eth_issued_obj.year}"
            ec_expiry = f"{eth_issued_obj.day:02d}/{eth_issued_obj.month:02d}/{eth_issued_obj.year + 8}"
        except:
            ec_issued = "01/01/2016"
            ec_expiry = "01/01/2024"
        
        gc_expiry = now.replace(year=now.year + 8).strftime("%d/%m/%Y")
        expiry_full = f"{gc_expiry} | {ec_expiry}"
        
        # Photos
        for i, img_path in enumerate(image_paths[:2]):
            if img_path and os.path.exists(img_path):
                try:
                    img = Image.open(img_path).convert("RGBA")
                    datas = img.getdata()
                    newData = []
                    for item in datas:
                        if item[0] > 220 and item[1] > 220 and item[2] > 220:
                            newData.append((255, 255, 255, 0))
                        else:
                            newData.append(item)
                    img.putdata(newData)
                    
                    if i == 0:  # Original
                        p_large = img.resize((310, 400))
                        card.paste(p_large, (65, 200), p_large)
                        p_small = img.resize((100, 135))
                        card.paste(p_small, (800, 450), p_small)
                    else:  # New
                        new_resized = img.resize((530, 550))
                        card.paste(new_resized, (1550, 30), new_resized)
                except:
                    pass
        
        # Fonts
        try:
            font = ImageFont.truetype(FONT_PATH, 37)
            small = ImageFont.truetype(FONT_PATH, 32)
            small_multiline = ImageFont.truetype(FONT_PATH, 28)
            fin_font = ImageFont.truetype(FONT_PATH, 25)
            iss_font = ImageFont.truetype(FONT_PATH, 25)
            sn_font = ImageFont.truetype(FONT_PATH, 26)
        except:
            font = small = small_multiline = fin_font = iss_font = sn_font = ImageFont.load_default()
        
        # Text
        draw.text((1265, 545), fin_number, fill="black", font=fin_font)
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
        
        out_path = os.path.join(CARD_FOLDER, f"id_{uuid.uuid4().hex[:6]}.png")
        card.convert("RGB").save(out_path)
        return out_path
    except Exception as e:
        print(f"Card Gen Error: {e}")
        # Create simple card if error
        simple_card = Image.new("RGB", (2100, 1500), color="white")
        out_path = os.path.join(CARD_FOLDER, f"id_{uuid.uuid4().hex[:6]}.png")
        simple_card.save(out_path)
        return out_path

# 5. ROUTES
@app.route('/')
def home():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head>
        <title>FREE ID Card Generator</title>
        <style>
            body { font-family: Arial; text-align: center; padding: 50px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; }
            .container { max-width: 800px; margin: 0 auto; background: rgba(255,255,255,0.1); padding: 40px; border-radius: 20px; }
            h1 { font-size: 48px; margin-bottom: 20px; }
            p { font-size: 20px; margin-bottom: 30px; }
            .btn { display: inline-block; padding: 15px 30px; background: #27ae60; color: white; text-decoration: none; border-radius: 10px; font-size: 18px; margin: 10px; }
            .features { display: flex; justify-content: center; gap: 30px; margin: 40px 0; }
            .feature { background: rgba(255,255,255,0.2); padding: 20px; border-radius: 10px; flex: 1; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🎉 FREE ID CARD GENERATOR</h1>
            <p>Generate professional ID cards completely FREE - No payment required!</p>
            
            <div class="features">
                <div class="feature">
                    <h3>✅ 100% FREE</h3>
                    <p>No charges, no subscriptions</p>
                </div>
                <div class="feature">
                    <h3>⚡ Instant</h3>
                    <p>Generate cards in seconds</p>
                </div>
                <div class="feature">
                    <h3>🔒 Secure</h3>
                    <p>Your data is safe</p>
                </div>
            </div>
            
            <div>
                <a href="/signup" class="btn">Get Started FREE</a>
                <a href="/login" class="btn" style="background: #3498db;">Login</a>
            </div>
            
            <div style="margin-top: 40px; background: rgba(0,0,0,0.2); padding: 20px; border-radius: 10px;">
                <h3>How it works:</h3>
                <p>1. Sign up for FREE account<br>
                2. Upload PDF and photo<br>
                3. Get your ID card instantly!</p>
            </div>
        </div>
    </body>
    </html>
    ''')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form['username'][:50]
        email = request.form['email'][:100]
        password = request.form['password']
        confirm_password = request.form['confirm_password']
        
        if password != confirm_password:
            flash('Passwords do not match!', 'error')
            return redirect(url_for('signup'))
        
        if len(password) < 6:
            flash('Password must be at least 6 characters!', 'error')
            return redirect(url_for('signup'))
        
        hashed_password = hash_password(password)
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        try:
            c.execute("INSERT INTO users (username, email, password) VALUES (?, ?, ?)",
                     (username, email, hashed_password))
            conn.commit()
            flash('Account created! Please login.', 'success')
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
        <title>Sign Up - FREE</title>
        <style>
            body { font-family: Arial; max-width: 400px; margin: 50px auto; padding: 20px; }
            .form-group { margin-bottom: 15px; }
            label { display: block; margin-bottom: 5px; }
            input { width: 100%; padding: 10px; box-sizing: border-box; border: 1px solid #ddd; border-radius: 5px; }
            button { background: #27ae60; color: white; padding: 12px 20px; border: none; border-radius: 5px; cursor: pointer; width: 100%; font-size: 16px; }
            .error { color: red; background: #ffebee; padding: 10px; border-radius: 5px; margin-bottom: 10px; }
            .success { color: green; background: #e8f5e9; padding: 10px; border-radius: 5px; margin-bottom: 10px; }
        </style>
    </head>
    <body>
        <h2>Sign Up - FREE</h2>
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
                <input type="text" name="username" required maxlength="50">
            </div>
            <div class="form-group">
                <label>Email:</label>
                <input type="email" name="email" required maxlength="100">
            </div>
            <div class="form-group">
                <label>Password (min 6 chars):</label>
                <input type="password" name="password" required minlength="6">
            </div>
            <div class="form-group">
                <label>Confirm Password:</label>
                <input type="password" name="confirm_password" required>
            </div>
            <button type="submit">Create FREE Account</button>
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
        <title>Login</title>
        <style>
            body { font-family: Arial; max-width: 400px; margin: 50px auto; padding: 20px; }
            .form-group { margin-bottom: 15px; }
            label { display: block; margin-bottom: 5px; }
            input { width: 100%; padding: 10px; box-sizing: border-box; border: 1px solid #ddd; border-radius: 5px; }
            button { background: #3498db; color: white; padding: 12px 20px; border: none; border-radius: 5px; cursor: pointer; width: 100%; font-size: 16px; }
            .error { color: red; background: #ffebee; padding: 10px; border-radius: 5px; margin-bottom: 10px; }
            .success { color: green; background: #e8f5e9; padding: 10px; border-radius: 5px; margin-bottom: 10px; }
        </style>
    </head>
    <body>
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
    c.execute("SELECT username, email FROM users WHERE id = ?", (session['user_id'],))
    user = c.fetchone()
    
    c.execute("SELECT COUNT(*) FROM cards_generated WHERE user_id = ?", (session['user_id'],))
    total_cards = c.fetchone()[0]
    
    c.execute('''SELECT card_path, created_at FROM cards_generated 
                 WHERE user_id = ? ORDER BY created_at DESC LIMIT 5''',
              (session['user_id'],))
    recent_cards = c.fetchall()
    conn.close()
    
    recent_html = ""
    if recent_cards:
        for card in recent_cards:
            filename = os.path.basename(card[0])
            recent_html += f'''
                <tr>
                    <td>{filename}</td>
                    <td>{card[1][:19]}</td>
                    <td><a href="/download-card/{filename}" target="_blank">Download</a></td>
                </tr>
            '''
    else:
        recent_html = '<tr><td colspan="3">No cards generated yet</td></tr>'
    
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Dashboard</title>
        <style>
            body { font-family: Arial; max-width: 1000px; margin: 0 auto; padding: 20px; }
            .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 30px; }
            .user-info { background: #f8f9fa; padding: 20px; border-radius: 10px; margin-bottom: 20px; }
            .stats { display: flex; gap: 20px; margin: 20px 0; }
            .stat-card { flex: 1; padding: 20px; background: white; border: 1px solid #ddd; border-radius: 10px; text-align: center; }
            .stat-value { font-size: 32px; font-weight: bold; color: #27ae60; }
            .btn { padding: 10px 20px; color: white; text-decoration: none; border-radius: 5px; display: inline-block; }
            .btn-success { background: #27ae60; }
            .btn-primary { background: #3498db; }
            .btn-warning { background: #f39c12; }
            table { width: 100%; border-collapse: collapse; margin-top: 20px; }
            th, td { padding: 12px; text-align: left; border-bottom: 1px solid #ddd; }
            th { background: #f8f9fa; }
        </style>
    </head>
    <body>
        <div class="header">
            <h2>Welcome, {{ username }}!</h2>
            <div>
                <a href="/generate" class="btn btn-success">Generate New Card</a>
                <a href="/logout" class="btn btn-warning">Logout</a>
            </div>
        </div>
        
        <div class="stats">
            <div class="stat-card">
                <div class="stat-value">{{ total_cards }}</div>
                <div>Total Cards</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">FREE</div>
                <div>Service Type</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">Unlimited</div>
                <div>Cards Remaining</div>
            </div>
        </div>
        
        <div class="user-info">
            <h3>Account Info</h3>
            <p><strong>Email:</strong> {{ email }}</p>
            <p><strong>Status:</strong> Active - FREE Service</p>
        </div>
        
        <div style="text-align: center; margin: 30px 0;">
            <a href="/generate" class="btn btn-success" style="font-size: 18px; padding: 15px 30px;">
                🚀 Generate FREE ID Card
            </a>
        </div>
        
        <div>
            <h3>Recent Cards</h3>
            <table>
                <tr>
                    <th>File Name</th>
                    <th>Generated Date</th>
                    <th>Action</th>
                </tr>
                {{ recent_html|safe }}
            </table>
        </div>
    </body>
    </html>
    ''', username=user[0], email=user[1], total_cards=total_cards, recent_html=recent_html)

@app.route('/generate', methods=['GET', 'POST'])
@login_required
def generate():
    if request.method == 'POST':
        pdf = request.files.get("pdf")
        user_photo = request.files.get("photo")
        fin_number = request.form.get("fin_number", "")
        
        errors = []
        if not pdf or pdf.filename == '':
            errors.append("PDF file is required!")
        if not user_photo or user_photo.filename == '':
            errors.append("Photo is required!")
        if not fin_number or len(fin_number) != 12 or not fin_number.isdigit():
            errors.append("Valid 12-digit FIN number is required!")
        
        if errors:
            return "<br>".join(errors), 400
        
        pdf_path = os.path.join(UPLOAD_FOLDER, f"temp_{uuid.uuid4().hex[:5]}.pdf")
        pdf.save(pdf_path)
        
        try:
            extracted_images = extract_all_images(pdf_path)
            data = extract_pdf_data(pdf_path, extracted_images)
            user_photo_path = save_user_uploaded_image(user_photo)
            
            if not user_photo_path:
                return "Error saving photo", 400
            
            final_image_paths = prepare_images_for_card(extracted_images, user_photo_path)
            card_path = generate_card(data, final_image_paths, fin_number)
            
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("INSERT INTO cards_generated (user_id, card_path) VALUES (?, ?)",
                     (session['user_id'], card_path))
            c.execute("UPDATE users SET free_cards_generated = free_cards_generated + 1 WHERE id = ?",
                     (session['user_id'],))
            c.execute("INSERT INTO free_transactions (user_id) VALUES (?)",
                     (session['user_id'],))
            conn.commit()
            conn.close()
            
            return send_file(card_path, mimetype='image/png', as_attachment=True, download_name="ID_Card.png")
            
        except Exception as e:
            return f"Error: {str(e)}", 500
    
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Generate ID Card</title>
        <style>
            body { font-family: Arial; max-width: 600px; margin: 0 auto; padding: 20px; }
            .form-group { margin-bottom: 20px; }
            label { display: block; margin-bottom: 5px; font-weight: bold; }
            input { width: 100%; padding: 10px; box-sizing: border-box; }
            button { background: #27ae60; color: white; padding: 15px; border: none; border-radius: 5px; cursor: pointer; width: 100%; font-size: 18px; }
        </style>
    </head>
    <body>
        <h2>Generate FREE ID Card</h2>
        <form method="POST" enctype="multipart/form-data">
            <div class="form-group">
                <label>PDF File:</label>
                <input type="file" name="pdf" accept=".pdf" required>
            </div>
            <div class="form-group">
                <label>Photo (PNG with transparent background):</label>
                <input type="file" name="photo" accept="image/*" required>
            </div>
            <div class="form-group">
                <label>12-digit FIN Number:</label>
                <input type="text" name="fin_number" pattern="\\d{12}" title="12 digits only" required>
            </div>
            <button type="submit">Generate FREE Card</button>
        </form>
        <p style="text-align: center; margin-top: 20px;">
            <a href="/dashboard">← Back to Dashboard</a>
        </p>
    </body>
    </html>
    ''')

@app.route('/download-card/<filename>')
@login_required
def download_card(filename):
    card_path = os.path.join(CARD_FOLDER, filename)
    if os.path.exists(card_path):
        return send_file(card_path, mimetype='image/png', as_attachment=True, download_name=filename)
    else:
        flash('Card not found!', 'error')
        return redirect(url_for('dashboard'))

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out!', 'success')
    return redirect(url_for('login'))

# 6. ERROR HANDLERS
@app.errorhandler(404)
def not_found(e):
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head><title>404</title></head>
    <body style="text-align: center; padding: 50px;">
        <h1>Page Not Found</h1>
        <p><a href="/">Go Home</a></p>
    </body>
    </html>
    '''), 404

@app.errorhandler(500)
def server_error(e):
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head><title>500</title></head>
    <body style="text-align: center; padding: 50px;">
        <h1>Server Error</h1>
        <p>Please try again later.</p>
        <p><a href="/">Go Home</a></p>
    </body>
    </html>
    '''), 500

# 7. STARTUP
if __name__ == "__main__":
    clear_old_files()
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Server starting on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
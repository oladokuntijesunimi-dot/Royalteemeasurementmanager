from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_file
from dotenv import load_dotenv
import sqlite3, hashlib, os, io, json
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.enums import TA_CENTER, TA_LEFT

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY')
if not app.secret_key:
    raise RuntimeError('SECRET_KEY is missing. Set it in the .env file.')

DB = os.getenv('DB', 'royaltee.db')
ADMIN_USERNAME = os.getenv('ADMIN_USERNAME', 'admin')
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD')
if not ADMIN_PASSWORD:
    raise RuntimeError('ADMIN_PASSWORD is missing. Set it in the .env file.')


def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()


def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS customers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        phone TEXT,
        date TEXT,
        notes TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS measurements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER NOT NULL,
        garment_type TEXT NOT NULL,
        measurements TEXT NOT NULL,
        FOREIGN KEY(customer_id) REFERENCES customers(id)
    )''')

    existing = c.execute('SELECT id FROM users WHERE username = ?', (ADMIN_USERNAME,)).fetchone()
    if existing:
        c.execute(
            'UPDATE users SET password = ? WHERE username = ?',
            (hash_pw(ADMIN_PASSWORD), ADMIN_USERNAME)
        )
    else:
        c.execute(
            'INSERT INTO users (username, password) VALUES (?, ?)',
            (ADMIN_USERNAME, hash_pw(ADMIN_PASSWORD))
        )
    conn.commit()
    conn.close()


init_db()

def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return redirect(url_for('dashboard'))

@app.route('/login', methods=['GET','POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form.get('username','').strip()
        password = request.form.get('password','')
        conn = get_db()
        user = conn.execute('SELECT * FROM users WHERE username=? AND password=?',
                            (username, hash_pw(password))).fetchone()
        conn.close()
        if user:
            session['user_id'] = user['id']
            session['username'] = user['username']
            return redirect(url_for('dashboard'))
        error = 'Invalid username or password.'
    return render_template('login.html', error=error)

@app.route('/register', methods=['GET','POST'])
def register():
    error = None
    if request.method == 'POST':
        username = request.form.get('username','').strip()
        password = request.form.get('password','')
        confirm = request.form.get('confirm','')
        if not username or not password:
            error = 'All fields are required.'
        elif password != confirm:
            error = 'Passwords do not match.'
        else:
            conn = get_db()
            try:
                conn.execute('INSERT INTO users (username, password) VALUES (?,?)',
                             (username, hash_pw(password)))
                conn.commit()
                conn.close()
                return redirect(url_for('login'))
            except:
                conn.close()
                error = 'Username already taken.'
    return render_template('register.html', error=error)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    conn = get_db()
    uid = session['user_id']
    customers = conn.execute(
        'SELECT * FROM customers WHERE user_id=? ORDER BY created_at DESC LIMIT 6', (uid,)).fetchall()
    total = conn.execute('SELECT COUNT(*) FROM customers WHERE user_id=?', (uid,)).fetchone()[0]
    blouse_count = conn.execute(
        "SELECT COUNT(*) FROM measurements m JOIN customers c ON m.customer_id=c.id WHERE c.user_id=? AND m.garment_type='blouse'", (uid,)).fetchone()[0]
    gown_count = conn.execute(
        "SELECT COUNT(*) FROM measurements m JOIN customers c ON m.customer_id=c.id WHERE c.user_id=? AND m.garment_type='gown'", (uid,)).fetchone()[0]
    other_count = conn.execute(
        "SELECT COUNT(*) FROM measurements m JOIN customers c ON m.customer_id=c.id WHERE c.user_id=? AND m.garment_type IN ('skirt','trouser')", (uid,)).fetchone()[0]
    # Get garment types per customer
    customer_data = []
    for cust in customers:
        types = conn.execute('SELECT garment_type FROM measurements WHERE customer_id=?', (cust['id'],)).fetchall()
        customer_data.append({'customer': dict(cust), 'types': [t['garment_type'] for t in types]})
    conn.close()
    return render_template('dashboard.html', customer_data=customer_data,
                           total=total, blouse_count=blouse_count,
                           gown_count=gown_count, other_count=other_count,
                           username=session['username'])

@app.route('/customers')
@login_required
def customers():
    conn = get_db()
    uid = session['user_id']
    q = request.args.get('q','').strip()
    if q:
        rows = conn.execute(
            "SELECT * FROM customers WHERE user_id=? AND (name LIKE ? OR phone LIKE ?) ORDER BY created_at DESC",
            (uid, f'%{q}%', f'%{q}%')).fetchall()
    else:
        rows = conn.execute('SELECT * FROM customers WHERE user_id=? ORDER BY created_at DESC', (uid,)).fetchall()
    customer_data = []
    for cust in rows:
        types = conn.execute('SELECT garment_type FROM measurements WHERE customer_id=?', (cust['id'],)).fetchall()
        customer_data.append({'customer': dict(cust), 'types': [t['garment_type'] for t in types]})
    conn.close()
    return render_template('customers.html', customer_data=customer_data, q=q, username=session['username'])

@app.route('/customer/new', methods=['GET','POST'])
@login_required
def new_customer():
    if request.method == 'POST':
        uid = session['user_id']
        name = request.form.get('name','').strip()
        if not name:
            return render_template('form.html', error='Name is required.', customer=None, measurements={}, username=session['username'])
        phone = request.form.get('phone','')
        date = request.form.get('date', datetime.now().strftime('%Y-%m-%d'))
        notes = request.form.get('notes','')
        conn = get_db()
        cur = conn.execute('INSERT INTO customers (user_id,name,phone,date,notes) VALUES (?,?,?,?,?)',
                           (uid, name, phone, date, notes))
        cid = cur.lastrowid
        for garment in ['blouse','skirt','gown','trouser']:
            fields = get_garment_fields(garment)
            meas = {f: request.form.get(f'{garment}_{f}','') for f in fields}
            if any(v for v in meas.values()):
                conn.execute('INSERT INTO measurements (customer_id,garment_type,measurements) VALUES (?,?,?)',
                             (cid, garment, json.dumps(meas)))
        conn.commit()
        conn.close()
        return redirect(url_for('view_customer', cid=cid))
    return render_template('form.html', customer=None, measurements={}, error=None, username=session['username'])

@app.route('/customer/<int:cid>/edit', methods=['GET','POST'])
@login_required
def edit_customer(cid):
    conn = get_db()
    cust = conn.execute('SELECT * FROM customers WHERE id=? AND user_id=?', (cid, session['user_id'])).fetchone()
    if not cust:
        conn.close(); return redirect(url_for('customers'))
    if request.method == 'POST':
        name = request.form.get('name','').strip()
        phone = request.form.get('phone','')
        date = request.form.get('date','')
        notes = request.form.get('notes','')
        conn.execute('UPDATE customers SET name=?,phone=?,date=?,notes=? WHERE id=?', (name,phone,date,notes,cid))
        for garment in ['blouse','skirt','gown','trouser']:
            fields = get_garment_fields(garment)
            meas = {f: request.form.get(f'{garment}_{f}','') for f in fields}
            existing = conn.execute('SELECT id FROM measurements WHERE customer_id=? AND garment_type=?', (cid, garment)).fetchone()
            if any(v for v in meas.values()):
                if existing:
                    conn.execute('UPDATE measurements SET measurements=? WHERE customer_id=? AND garment_type=?',
                                 (json.dumps(meas), cid, garment))
                else:
                    conn.execute('INSERT INTO measurements (customer_id,garment_type,measurements) VALUES (?,?,?)',
                                 (cid, garment, json.dumps(meas)))
            elif existing:
                conn.execute('DELETE FROM measurements WHERE customer_id=? AND garment_type=?', (cid, garment))
        conn.commit()
        conn.close()
        return redirect(url_for('view_customer', cid=cid))
    meas_rows = conn.execute('SELECT * FROM measurements WHERE customer_id=?', (cid,)).fetchall()
    measurements = {r['garment_type']: json.loads(r['measurements']) for r in meas_rows}
    conn.close()
    return render_template('form.html', customer=dict(cust), measurements=measurements, error=None, username=session['username'])

@app.route('/customer/<int:cid>')
@login_required
def view_customer(cid):
    conn = get_db()
    cust = conn.execute('SELECT * FROM customers WHERE id=? AND user_id=?', (cid, session['user_id'])).fetchone()
    if not cust:
        conn.close(); return redirect(url_for('customers'))
    meas_rows = conn.execute('SELECT * FROM measurements WHERE customer_id=?', (cid,)).fetchall()
    measurements = {r['garment_type']: json.loads(r['measurements']) for r in meas_rows}
    conn.close()
    labels = get_all_labels()
    return render_template('view.html', customer=dict(cust), measurements=measurements, labels=labels, username=session['username'])

@app.route('/customer/<int:cid>/delete', methods=['POST'])
@login_required
def delete_customer(cid):
    conn = get_db()
    conn.execute('DELETE FROM measurements WHERE customer_id=?', (cid,))
    conn.execute('DELETE FROM customers WHERE id=? AND user_id=?', (cid, session['user_id']))
    conn.commit(); conn.close()
    return redirect(url_for('customers'))

@app.route('/customer/<int:cid>/pdf')
@login_required
def export_pdf(cid):
    conn = get_db()
    cust = conn.execute('SELECT * FROM customers WHERE id=? AND user_id=?', (cid, session['user_id'])).fetchone()
    if not cust:
        conn.close(); return redirect(url_for('customers'))
    meas_rows = conn.execute('SELECT * FROM measurements WHERE customer_id=?', (cid,)).fetchall()
    measurements = {r['garment_type']: json.loads(r['measurements']) for r in meas_rows}
    conn.close()
    labels = get_all_labels()
    pdf_buffer = generate_pdf(dict(cust), measurements, labels)
    return send_file(pdf_buffer, mimetype='application/pdf',
                     download_name=f"{cust['name'].replace(' ','_')}_measurements.pdf",
                     as_attachment=True)

def generate_pdf(cust, measurements, labels):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                             rightMargin=2*cm, leftMargin=2*cm,
                             topMargin=2*cm, bottomMargin=2*cm)
    gold = colors.HexColor('#C9A84C')
    dark = colors.HexColor('#1A1A2E')
    mid = colors.HexColor('#2D2D44')
    light_gold = colors.HexColor('#F5E6C8')
    story = []
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('title', fontName='Helvetica-Bold', fontSize=22,
                                  textColor=dark, alignment=TA_CENTER, spaceAfter=4)
    sub_style = ParagraphStyle('sub', fontName='Helvetica', fontSize=10,
                                textColor=colors.HexColor('#888888'), alignment=TA_CENTER, spaceAfter=12)
    section_style = ParagraphStyle('section', fontName='Helvetica-Bold', fontSize=12,
                                    textColor=gold, spaceBefore=14, spaceAfter=6)
    body_style = ParagraphStyle('body', fontName='Helvetica', fontSize=10,
                                 textColor=colors.HexColor('#444444'), spaceAfter=4)

    story.append(Paragraph("ROYALTEE STITCHES", title_style))
    story.append(Paragraph("Customer Measurement Record", sub_style))
    story.append(HRFlowable(width='100%', thickness=1.5, color=gold))
    story.append(Spacer(1, 0.4*cm))

    # Customer info
    info_data = [
        ['Customer Name', cust['name'], 'Phone', cust['phone'] or '—'],
        ['Date', cust['date'] or '—', 'Notes', cust['notes'] or '—'],
    ]
    info_table = Table(info_data, colWidths=[3.5*cm, 6*cm, 2.5*cm, 5*cm])
    info_table.setStyle(TableStyle([
        ('FONTNAME', (0,0), (-1,-1), 'Helvetica'),
        ('FONTNAME', (0,0), (0,-1), 'Helvetica-Bold'),
        ('FONTNAME', (2,0), (2,-1), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 10),
        ('TEXTCOLOR', (0,0), (0,-1), dark),
        ('TEXTCOLOR', (2,0), (2,-1), dark),
        ('BACKGROUND', (0,0), (0,-1), light_gold),
        ('BACKGROUND', (2,0), (2,-1), light_gold),
        ('ROWBACKGROUNDS', (0,0), (-1,-1), [colors.white, colors.HexColor('#FAFAFA')]),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#DDDDDD')),
        ('PADDING', (0,0), (-1,-1), 6),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 0.5*cm))

    garment_titles = {'blouse':'Blouse Measurements','skirt':'Skirt Measurements',
                      'gown':'Gown Measurements','trouser':'Trouser Measurements'}
    for garment, title in garment_titles.items():
        meas = measurements.get(garment, {})
        entries = [(labels[garment].get(k,''), v) for k,v in meas.items() if v and v != '0']
        if not entries: continue
        story.append(Paragraph(title, section_style))
        rows = []
        row = []
        for i, (label, val) in enumerate(entries):
            row.append(label)
            row.append(f'{val}"')
            if len(row) == 4:
                rows.append(row); row = []
        if row:
            while len(row) < 4: row.append('')
            rows.append(row)
        t = Table(rows, colWidths=[4.5*cm, 2*cm, 4.5*cm, 2*cm])
        t.setStyle(TableStyle([
            ('FONTNAME', (0,0), (-1,-1), 'Helvetica'),
            ('FONTNAME', (0,0), (0,-1), 'Helvetica-Bold'),
            ('FONTNAME', (2,0), (2,-1), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,-1), 9.5),
            ('TEXTCOLOR', (1,0), (1,-1), colors.HexColor('#333333')),
            ('TEXTCOLOR', (3,0), (3,-1), colors.HexColor('#333333')),
            ('ROWBACKGROUNDS', (0,0), (-1,-1), [colors.white, colors.HexColor('#FAFAFA')]),
            ('GRID', (0,0), (-1,-1), 0.3, colors.HexColor('#E0E0E0')),
            ('PADDING', (0,0), (-1,-1), 6),
        ]))
        story.append(t)

    story.append(Spacer(1, 0.8*cm))
    story.append(HRFlowable(width='100%', thickness=0.5, color=colors.HexColor('#CCCCCC')))
    story.append(Paragraph("Royaltee Stitches · Premium Fashion & Tailoring", 
                            ParagraphStyle('footer', fontName='Helvetica', fontSize=8,
                                           textColor=colors.HexColor('#999999'), alignment=TA_CENTER, spaceBefore=6)))
    doc.build(story)
    buf.seek(0)
    return buf

def get_garment_fields(g):
    fields = {
        'blouse': ['back','fulllen','bust','chest','rwaist','wub','bpt','nn','halflen','slvlen','rslv'],
        'skirt': ['len','hip','waist'],
        'gown': ['bust','ubust','waist','hip','back','shbpt','nn','halflen','rwaist','len','slv','rslv'],
        'trouser': ['waist','hip','thigh','knee','len','bottom','tight']
    }
    return fields.get(g, [])

def get_all_labels():
    return {
        'blouse': {'back':'Back','fulllen':'Full Length','bust':'Bust','chest':'Chest',
                   'rwaist':'Round Waist','wub':'Waist Under Bust','bpt':'Burst Point',
                   'nn':'N–N','halflen':'Half Length','slvlen':'Sleeve Length','rslv':'Round Sleeve'},
        'skirt': {'len':'Length','hip':'Hip','waist':'Waist'},
        'gown': {'bust':'Bust','ubust':'Under-Bust','waist':'Waist','hip':'Hip','back':'Back',
                 'shbpt':'Shoulder to Bust Pt','nn':'Nipple to Nipple','halflen':'Half Length',
                 'rwaist':'Round Waist','len':'Gown Length','slv':'Sleeve','rslv':'Round Sleeve'},
        'trouser': {'waist':'Waist','hip':'Hip','thigh':'Thigh (Lap)','knee':'Knee',
                    'len':'Length','bottom':'Bottom','tight':'Tight'}
    }

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 10000)), debug=False)

# app.py
import os
import functools
import uuid
import secrets
import smtplib
import json
import random
import string
import csv
import markdown2
from io import StringIO
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, flash, g, send_from_directory, send_file, jsonify
from sqlalchemy import create_engine, text
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib import colors
import io

# --- Load environment variables from .env file ---
from dotenv import load_dotenv
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'awwalu-devs-super-secret-key-change-in-production')

# ========================
# AUTO-INITIALIZE DATABASE ON FIRST RUN
# ========================
def init_db_if_needed():
    """Create database tables if they don't exist."""
    try:
        from sqlalchemy import text
        conn = get_db()
        # Check if users table exists
        result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='users'"))
        if not result.fetchone():
            print("⚠️ Database not found. Creating tables...")
            import init_db
            init_db.init_db()
            print("✅ Database created successfully!")
        else:
            print("✅ Database already exists.")
    except Exception as e:
        print(f"⚠️ Database check failed: {e}")
        # Try to initialize anyway
        try:
            import init_db
            init_db.init_db()
            print("✅ Database created via fallback!")
        except Exception as init_error:
            print(f"❌ Failed to create database: {init_error}")

# Run initialization
with app.app_context():
    init_db_if_needed()

# --- Custom Jinja2 filter for JSON parsing ---
@app.template_filter('from_json')
def from_json(value):
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value

# --- File Upload Config ---
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'py', 'js', 'html', 'css', 'txt', 'pdf', 'zip', 'rar', 'png', 'jpg', 'jpeg', 'gif'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# --- Email Config ---
MAIL_SERVER = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
MAIL_PORT = int(os.getenv('MAIL_PORT', 587))
MAIL_USERNAME = os.getenv('MAIL_USERNAME', '')
MAIL_PASSWORD = os.getenv('MAIL_PASSWORD', '')
MAIL_DEFAULT_SENDER = os.getenv('MAIL_DEFAULT_SENDER', 'noreply@awwaludevs.com')
APP_BASE_URL = os.getenv('APP_BASE_URL', 'http://localhost:5000')

def send_email(to_email, subject, body):
    if not MAIL_USERNAME or not MAIL_PASSWORD:
        print("⚠️ Email credentials not set. Check your .env file.")
        return False
    try:
        msg = MIMEMultipart()
        msg['From'] = MAIL_DEFAULT_SENDER
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))
        server = smtplib.SMTP(MAIL_SERVER, MAIL_PORT)
        server.starttls()
        server.login(MAIL_USERNAME, MAIL_PASSWORD)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        print(f"❌ Email send error: {e}")
        return False

# --- Database Setup ---
DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///portal.db')
connect_args = {"check_same_thread": False} if 'sqlite' in DATABASE_URL else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)

def get_db():
    if 'db' not in g:
        g.db = engine.connect()
    return g.db

def execute_query(query, params=None, fetch_one=False, fetch_all=False, commit=False):
    conn = get_db()
    try:
        if commit:
            conn.execute(text(query), params or {})
            conn.commit()
            return None
        result = conn.execute(text(query), params or {})
        if fetch_one:
            row = result.fetchone()
            return dict(row._mapping) if row else None
        if fetch_all:
            rows = result.fetchall()
            return [dict(row._mapping) for row in rows]
        return None
    except Exception as e:
        conn.rollback()
        raise e

@app.teardown_appcontext
def close_db(exception):
    db = g.pop('db', None)
    if db is not None:
        db.close()

@app.route('/init_db')
def init_db_route():
    """Manual database initialization (for Render deployment)."""
    try:
        import init_db
        init_db.init_db()
        return "✅ Database initialized successfully! <a href='/'>Go to Home</a>"
    except Exception as e:
        return f"❌ Error: {str(e)}"

# --- Helper functions ---
def get_admin_id():
    admin = execute_query("SELECT id FROM users WHERE is_admin = 1 LIMIT 1", fetch_one=True)
    return admin['id'] if admin else None

def get_admin_email():
    admin = execute_query("SELECT email, username FROM users WHERE is_admin = 1 LIMIT 1", fetch_one=True)
    if admin:
        return admin['email'] if admin['email'] else admin['username']
    return None

def create_notification(message, link=None):
    admin_id = get_admin_id()
    if admin_id:
        execute_query("INSERT INTO notifications (user_id, message, link) VALUES (:uid, :msg, :link)",
                      {"uid": admin_id, "msg": message, "link": link}, commit=True)

def create_user_notification(user_id, message, link=None):
    if user_id:
        execute_query("INSERT INTO notifications (user_id, message, link) VALUES (:uid, :msg, :link)",
                      {"uid": user_id, "msg": message, "link": link}, commit=True)

def send_user_email(user_id, subject, body):
    user = execute_query("SELECT email, username, full_name FROM users WHERE id = :id", {"id": user_id}, fetch_one=True)
    if not user:
        return False
    recipient = user['email'] if user['email'] else user['username']
    personal_body = f"Hello {user['full_name']},\n\n{body}\n\nRegards,\nAwwalu Devs Team"
    return send_email(recipient, subject, personal_body)

def log_activity(user_id, action, details=None):
    ip = request.remote_addr
    execute_query("""
        INSERT INTO activity_logs (user_id, action, details, ip_address)
        VALUES (:uid, :act, :det, :ip)
    """, {"uid": user_id, "act": action, "det": details, "ip": ip}, commit=True)

def generate_certificate_pdf(user, course, code):
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter
    c.setStrokeColorRGB(0.2, 0.2, 0.8)
    c.setLineWidth(5)
    c.rect(40, 40, width-80, height-80)
    c.setFont("Helvetica-Bold", 24)
    c.drawCentredString(width/2, height-100, "Certificate of Completion")
    c.setFont("Helvetica", 16)
    c.drawCentredString(width/2, height-140, "This certifies that")
    c.setFont("Helvetica-Bold", 20)
    c.drawCentredString(width/2, height-180, user['full_name'])
    c.setFont("Helvetica", 16)
    c.drawCentredString(width/2, height-220, "has successfully completed")
    c.setFont("Helvetica-Bold", 18)
    c.drawCentredString(width/2, height-260, course['name'])
    c.setFont("Helvetica", 12)
    c.drawCentredString(width/2, height-300, f"Certificate Code: {code}")
    c.drawCentredString(width/2, height-330, f"Issued on: {datetime.now().strftime('%B %d, %Y')}")
    c.save()
    buffer.seek(0)
    return buffer

def send_course_emails(course_id, subject, body, exclude_user=None):
    students = execute_query("""
        SELECT username, full_name, email FROM users
        WHERE course_id = :cid AND is_admin = 0 AND email_notifications = 1
    """, {"cid": course_id}, fetch_all=True)
    for s in students:
        if exclude_user and s['username'] == exclude_user:
            continue
        recipient = s['email'] if s['email'] else s['username']
        personal_body = f"Hello {s['full_name']},\n\n{body}\n\nRegards,\nAwwalu Devs Team"
        send_email(recipient, subject, personal_body)

def send_announcement_emails(course_id, title, content, exclude_user=None):
    if course_id:
        students = execute_query("""
            SELECT username, full_name, email FROM users
            WHERE course_id = :cid AND is_admin = 0 AND email_notifications = 1
        """, {"cid": course_id}, fetch_all=True)
        course_name = execute_query("SELECT name FROM courses WHERE id = :id", {"id": course_id}, fetch_one=True)['name']
    else:
        students = execute_query("""
            SELECT username, full_name, email FROM users
            WHERE is_admin = 0 AND email_notifications = 1
        """, fetch_all=True)
        course_name = "All Courses"
    subject = f"📢 New Announcement: {title}"
    body = f"A new announcement has been posted for {course_name}.\n\n{content}\n\nLog in to view: {APP_BASE_URL}/dashboard"
    for s in students:
        if exclude_user and s['username'] == exclude_user:
            continue
        recipient = s['email'] if s['email'] else s['username']
        personal_body = f"Hello {s['full_name']},\n\n{body}\n\nRegards,\nAwwalu Devs Team"
        send_email(recipient, subject, personal_body)

def sync_note_tags(note_id, tag_ids):
    execute_query("DELETE FROM note_tags WHERE note_id = :nid", {"nid": note_id}, commit=True)
    if tag_ids:
        for tid in tag_ids:
            execute_query("INSERT INTO note_tags (note_id, tag_id) VALUES (:nid, :tid)",
                          {"nid": note_id, "tid": tid}, commit=True)

# --- Context Processor for Global Variables ---
@app.context_processor
def inject_global_vars():
    unread_count = 0
    avatar = None
    if 'user_id' in session:
        count = execute_query("SELECT COUNT(*) as cnt FROM notifications WHERE user_id = :uid AND is_read = 0",
                              {"uid": session['user_id']}, fetch_one=True)
        unread_count = count['cnt'] if count else 0
        user = execute_query("SELECT avatar FROM users WHERE id = :id", {"id": session['user_id']}, fetch_one=True)
        avatar = user['avatar'] if user else None
    return {
        'unread_count': unread_count,
        'get_admin_id': get_admin_id,
        'user_avatar': avatar
    }

# --- Auth Decorators ---
def login_required(f):
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in first.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('is_admin'):
            flash('Admin access required.', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function

# ========================
# PUBLIC ROUTES
# ========================
@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = execute_query("SELECT * FROM users WHERE username = :username",
                             {"username": username}, fetch_one=True)
        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['full_name'] = user['full_name']
            session['is_admin'] = bool(user['is_admin'])
            log_activity(user['id'], 'login', 'User logged in')
            flash(f'Welcome back, {user["full_name"]}!', 'success')
            if session['is_admin']:
                return redirect(url_for('admin_panel'))
            else:
                if user.get('course_id'):
                    session['course_id'] = user['course_id']
                    return redirect(url_for('dashboard'))
                else:
                    return redirect(url_for('select_course'))
        else:
            flash('Invalid username or password.', 'danger')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']
        confirm_password = request.form['confirm_password']
        full_name = request.form['full_name'].strip()
        email = request.form.get('email', '').strip()
        course_id = request.form['course_id']
        phone = request.form.get('phone', '').strip()
        dob = request.form.get('dob', '').strip()
        bio = request.form.get('bio', '').strip()

        if password != confirm_password:
            flash('Passwords do not match.', 'danger')
            return redirect(url_for('register'))

        existing = execute_query("SELECT id FROM users WHERE username = :u", {"u": username}, fetch_one=True)
        if existing:
            flash('Username already taken.', 'danger')
            return redirect(url_for('register'))

        if email:
            existing_email = execute_query("SELECT id FROM users WHERE email = :e", {"e": email}, fetch_one=True)
            if existing_email:
                flash('Email already registered.', 'danger')
                return redirect(url_for('register'))

        hashed = generate_password_hash(password)

        execute_query("""
            INSERT INTO users (username, password, full_name, email, course_id, phone, dob, bio)
            VALUES (:u, :p, :f, :e, :cid, :ph, :d, :b)
        """, {
            "u": username,
            "p": hashed,
            "f": full_name,
            "e": email if email else None,
            "cid": course_id,
            "ph": phone if phone else None,
            "d": dob if dob else None,
            "b": bio if bio else None
        }, commit=True)

        user = execute_query("SELECT * FROM users WHERE username = :u", {"u": username}, fetch_one=True)

        # Avatar upload
        if 'avatar' in request.files:
            avatar_file = request.files['avatar']
            if avatar_file and avatar_file.filename != '' and allowed_file(avatar_file.filename):
                ext = avatar_file.filename.rsplit('.', 1)[1].lower()
                avatar_name = f"avatar_{user['id']}.{ext}"
                avatar_path = os.path.join(app.config['UPLOAD_FOLDER'], avatar_name)
                avatar_file.save(avatar_path)
                execute_query("UPDATE users SET avatar = :a WHERE id = :id",
                              {"a": f"/uploads/{avatar_name}", "id": user['id']}, commit=True)
                flash('Profile picture uploaded successfully!', 'success')
            else:
                flash('No profile picture uploaded or invalid file type.', 'info')

        session['user_id'] = user['id']
        session['username'] = user['username']
        session['full_name'] = user['full_name']
        session['is_admin'] = False
        session['course_id'] = int(course_id) if course_id else None

        log_activity(user['id'], 'register', 'New student registered')
        create_notification(f"New student registered: {full_name} ({username})", url_for('view_students'))
        admin_email = get_admin_email()
        if admin_email:
            subject = "📝 New Student Registration"
            body = f"A new student, {full_name} ({username}), has registered.\n\nLog in to view students: {APP_BASE_URL}/admin/students"
            send_email(admin_email, subject, body)

        flash(f'Welcome to Awwalu Devs, {full_name}!', 'success')
        return redirect(url_for('dashboard'))

    courses = execute_query("SELECT * FROM courses", fetch_all=True)
    return render_template('register.html', courses=courses)

@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        if not email:
            flash('Please enter your email address.', 'danger')
            return redirect(url_for('forgot_password'))
        user = execute_query("SELECT * FROM users WHERE LOWER(email) = LOWER(:email)", {"email": email}, fetch_one=True)
        if not user:
            flash('No account found with that email address.', 'danger')
            return redirect(url_for('forgot_password'))
        token = secrets.token_urlsafe(32)
        expiry = datetime.utcnow() + timedelta(hours=24)
        execute_query("UPDATE users SET reset_token = :t, reset_token_expiry = :e WHERE id = :id",
                      {"t": token, "e": expiry.isoformat(), "id": user['id']}, commit=True)
        reset_link = f"{APP_BASE_URL}/reset_password/{token}"
        body = f"""Hello {user['full_name']},

You requested to reset your password for your Awwalu Devs account.

Click the link below to reset your password (valid for 24 hours):
{reset_link}

If you did not request this, please ignore this email.

Regards,
Awwalu Devs Team
"""
        success = send_email(user['email'], "Password Reset Request", body)
        if success:
            flash('Password reset link sent to your email!', 'success')
        else:
            flash(f'Email could not be sent. Use this link to reset your password: <a href="{reset_link}" target="_blank">{reset_link}</a>', 'warning')
            print("\n" + "=" * 60)
            print("🔑 RESET LINK (copy this):")
            print(reset_link)
            print("=" * 60 + "\n")
        return redirect(url_for('login'))
    return render_template('forgot_password.html')

@app.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    user = execute_query("SELECT * FROM users WHERE reset_token = :t", {"t": token}, fetch_one=True)
    if not user:
        flash('Invalid or expired reset token.', 'danger')
        return redirect(url_for('login'))
    expiry = datetime.fromisoformat(user['reset_token_expiry'])
    if datetime.utcnow() > expiry:
        flash('Reset token has expired. Please request a new one.', 'danger')
        return redirect(url_for('forgot_password'))
    if request.method == 'POST':
        password = request.form['password']
        confirm = request.form['confirm_password']
        if password != confirm:
            flash('Passwords do not match.', 'danger')
            return redirect(request.url)
        hashed = generate_password_hash(password)
        execute_query("UPDATE users SET password = :p, reset_token = NULL, reset_token_expiry = NULL WHERE id = :id",
                      {"p": hashed, "id": user['id']}, commit=True)
        flash('Password updated successfully! Please log in.', 'success')
        return redirect(url_for('login'))
    return render_template('reset_password.html', token=token)

# ========================
# STUDENT ROUTES
# ========================
@app.route('/select_course', methods=['GET', 'POST'])
@login_required
def select_course():
    if session.get('is_admin'):
        return redirect(url_for('admin_panel'))
    user = execute_query("SELECT course_id FROM users WHERE id = :id", {"id": session['user_id']}, fetch_one=True)
    if user and user.get('course_id'):
        session['course_id'] = user['course_id']
        return redirect(url_for('dashboard'))
    courses = execute_query("SELECT * FROM courses", fetch_all=True)
    if request.method == 'POST':
        course_id = request.form.get('course_id')
        if not course_id or course_id == 'None':
            flash('Please select a valid course.', 'warning')
            return redirect(url_for('select_course'))
        execute_query("UPDATE users SET course_id = :cid WHERE id = :uid",
                      {"cid": course_id, "uid": session['user_id']}, commit=True)
        session['course_id'] = int(course_id)
        return redirect(url_for('dashboard'))
    return render_template('select_course.html', courses=courses)

@app.route('/courses')
@login_required
def courses_list():
    if session.get('is_admin'):
        return redirect(url_for('admin_panel'))
    all_courses = execute_query("SELECT * FROM courses", fetch_all=True)
    current_course_id = session.get('course_id')
    return render_template('courses.html', courses=all_courses, current_course_id=current_course_id)

@app.route('/switch_course/<int:course_id>', methods=['POST'])
@login_required
def switch_course(course_id):
    if session.get('is_admin'):
        flash('Admins cannot switch courses.', 'warning')
        return redirect(url_for('admin_panel'))
    course = execute_query("SELECT id FROM courses WHERE id = :id", {"id": course_id}, fetch_one=True)
    if not course:
        flash('Course not found.', 'danger')
        return redirect(url_for('courses_list'))
    execute_query("UPDATE users SET course_id = :cid WHERE id = :uid",
                  {"cid": course_id, "uid": session['user_id']}, commit=True)
    session['course_id'] = course_id
    flash('Switched to new course!', 'success')
    return redirect(url_for('dashboard'))

@app.route('/dashboard')
@login_required
def dashboard():
    if session.get('is_admin'):
        return redirect(url_for('admin_panel'))
    course_id = session.get('course_id')
    if not course_id:
        return redirect(url_for('select_course'))
    course = execute_query("SELECT * FROM courses WHERE id = :id", {"id": course_id}, fetch_one=True)
    all_tags = execute_query("SELECT * FROM tags ORDER BY name", fetch_all=True)
    tag_filter = request.args.get('tag')
    search_query = request.args.get('q', '').strip()
    if tag_filter:
        notes = execute_query("""
            SELECT n.* FROM notes n
            JOIN note_tags nt ON n.id = nt.note_id
            WHERE nt.tag_id = :tid AND n.course_id = :cid AND n.status = 'published'
            AND (n.publish_at IS NULL OR n.publish_at <= datetime('now'))
            ORDER BY n.sort_order ASC, n.id ASC
        """, {"tid": tag_filter, "cid": course_id}, fetch_all=True)
    else:
        notes = execute_query("""
            SELECT * FROM notes
            WHERE course_id = :id AND status = 'published'
            AND (publish_at IS NULL OR publish_at <= datetime('now'))
            ORDER BY sort_order ASC, id ASC
        """, {"id": course_id}, fetch_all=True)
    read_notes = execute_query("SELECT note_id FROM read_notes WHERE user_id = :uid",
                               {"uid": session['user_id']}, fetch_all=True)
    read_ids = [r['note_id'] for r in read_notes] if read_notes else []
    if search_query:
        notes = [n for n in notes if search_query.lower() in n['title'].lower() or search_query.lower() in n['content'].lower()]
    total = len(notes)
    read_count = sum(1 for n in notes if n['id'] in read_ids)
    progress = int((read_count / total) * 100) if total > 0 else 0
    announcements = execute_query("""
        SELECT * FROM announcements
        WHERE course_id IS NULL OR course_id = :cid
        ORDER BY created_at DESC
    """, {"cid": course_id}, fetch_all=True)
    certificate = None
    certificate_pending = False
    if total > 0 and read_count == total:
        certificate = execute_query("SELECT * FROM certificates WHERE student_id = :uid AND course_id = :cid",
                                    {"uid": session['user_id'], "cid": course_id}, fetch_one=True)
        certificate_pending = certificate is None
    quizzes = execute_query("SELECT * FROM quizzes WHERE course_id = :cid ORDER BY created_at DESC",
                            {"cid": course_id}, fetch_all=True)
    for q in quizzes:
        attempt = execute_query("SELECT score, total_questions FROM quiz_attempts WHERE student_id = :uid AND quiz_id = :qid",
                                {"uid": session['user_id'], "qid": q['id']}, fetch_one=True)
        q['attempted'] = attempt is not None
        if attempt:
            q['score'] = attempt['score']
            q['total_questions'] = attempt['total_questions']
    badges = execute_query("SELECT * FROM badges WHERE user_id = :uid", {"uid": session['user_id']}, fetch_all=True)
    return render_template('dashboard.html', course=course, notes=notes, read_ids=read_ids,
                           progress=progress, total=total, read_count=read_count,
                           announcements=announcements, certificate=certificate,
                           certificate_pending=certificate_pending, quizzes=quizzes,
                           search_query=search_query, badges=badges,
                           all_tags=all_tags, tag_filter=tag_filter)

@app.route('/mark_read/<int:note_id>', methods=['POST'])
@login_required
def mark_read(note_id):
    if session.get('is_admin'):
        flash('Admins cannot track reading progress.', 'info')
        return redirect(url_for('dashboard'))
    existing = execute_query("SELECT id FROM read_notes WHERE user_id = :uid AND note_id = :nid",
                             {"uid": session['user_id'], "nid": note_id}, fetch_one=True)
    if not existing:
        execute_query("INSERT INTO read_notes (user_id, note_id) VALUES (:uid, :nid)",
                      {"uid": session['user_id'], "nid": note_id}, commit=True)
        log_activity(session['user_id'], 'mark_read', f'Marked note {note_id} as read')
        flash('Progress updated!', 'success')
    return redirect(url_for('dashboard'))

@app.route('/quiz/<int:quiz_id>')
@login_required
def take_quiz(quiz_id):
    if session.get('is_admin'):
        flash('Admins cannot take quizzes.', 'warning')
        return redirect(url_for('admin_panel'))
    attempt = execute_query("SELECT id FROM quiz_attempts WHERE student_id = :uid AND quiz_id = :qid",
                            {"uid": session['user_id'], "qid": quiz_id}, fetch_one=True)
    if attempt:
        flash('You have already completed this quiz.', 'info')
        return redirect(url_for('dashboard'))
    quiz = execute_query("SELECT * FROM quizzes WHERE id = :id", {"id": quiz_id}, fetch_one=True)
    if not quiz:
        flash('Quiz not found.', 'danger')
        return redirect(url_for('dashboard'))
    questions = execute_query("SELECT * FROM quiz_questions WHERE quiz_id = :qid ORDER BY id",
                              {"qid": quiz_id}, fetch_all=True)
    if not questions:
        flash('This quiz has no questions yet.', 'warning')
        return redirect(url_for('dashboard'))
    return render_template('take_quiz.html', quiz=quiz, questions=questions)

@app.route('/submit_quiz/<int:quiz_id>', methods=['POST'])
@login_required
def submit_quiz(quiz_id):
    if session.get('is_admin'):
        flash('Admins cannot submit quizzes.', 'warning')
        return redirect(url_for('admin_panel'))
    attempt = execute_query("SELECT id FROM quiz_attempts WHERE student_id = :uid AND quiz_id = :qid",
                            {"uid": session['user_id'], "qid": quiz_id}, fetch_one=True)
    if attempt:
        flash('You have already completed this quiz.', 'info')
        return redirect(url_for('dashboard'))
    questions = execute_query("SELECT * FROM quiz_questions WHERE quiz_id = :qid ORDER BY id",
                              {"qid": quiz_id}, fetch_all=True)
    if not questions:
        flash('No questions found.', 'danger')
        return redirect(url_for('dashboard'))
    score = 0
    total = len(questions)
    answers = []
    for q in questions:
        selected = request.form.get(f'q_{q["id"]}')
        if selected is not None:
            try:
                selected_idx = int(selected)
            except:
                selected_idx = -1
        else:
            selected_idx = -1
        answers.append(selected_idx)
        if selected_idx == q['correct_answer']:
            score += 1
    execute_query("INSERT INTO quiz_attempts (student_id, quiz_id, score, total_questions, answers) VALUES (:sid, :qid, :score, :total, :answers)",
                  {"sid": session['user_id'], "qid": quiz_id, "score": score, "total": total, "answers": json.dumps(answers)},
                  commit=True)
    log_activity(session['user_id'], 'submit_quiz', f'Quiz {quiz_id} score {score}/{total}')
    flash(f'Quiz submitted! Your score: {score}/{total}', 'success')
    return redirect(url_for('dashboard'))

@app.route('/download_certificate/<int:certificate_id>')
@login_required
def download_certificate(certificate_id):
    certificate = execute_query("SELECT * FROM certificates WHERE id = :id", {"id": certificate_id}, fetch_one=True)
    if not certificate:
        flash('Certificate not found.', 'danger')
        return redirect(url_for('dashboard'))
    if certificate['student_id'] != session['user_id'] and not session.get('is_admin'):
        flash('You do not have permission to view this certificate.', 'danger')
        return redirect(url_for('dashboard'))
    user = execute_query("SELECT * FROM users WHERE id = :id", {"id": certificate['student_id']}, fetch_one=True)
    course = execute_query("SELECT * FROM courses WHERE id = :id", {"id": certificate['course_id']}, fetch_one=True)
    if not user or not course:
        flash('User or course not found.', 'danger')
        return redirect(url_for('dashboard'))
    pdf_buffer = generate_certificate_pdf(user, course, certificate['certificate_code'])
    return send_file(pdf_buffer, as_attachment=True,
                     download_name=f"certificate_{certificate['certificate_code']}.pdf",
                     mimetype='application/pdf')

@app.route('/download_notes_pdf')
@login_required
def download_notes_pdf():
    if session.get('is_admin'):
        flash('Admins cannot download notes.', 'warning')
        return redirect(url_for('admin_panel'))
    course_id = session.get('course_id')
    if not course_id:
        flash('Please select a course first.', 'warning')
        return redirect(url_for('select_course'))
    course = execute_query("SELECT * FROM courses WHERE id = :id", {"id": course_id}, fetch_one=True)
    notes = execute_query("""
        SELECT * FROM notes
        WHERE course_id = :id AND status = 'published'
        AND (publish_at IS NULL OR publish_at <= datetime('now'))
        ORDER BY sort_order ASC, id ASC
    """, {"id": course_id}, fetch_all=True)
    if not notes:
        flash('No published notes available for download.', 'warning')
        return redirect(url_for('dashboard'))
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter,
                            rightMargin=72, leftMargin=72,
                            topMargin=72, bottomMargin=72)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('Title', parent=styles['Heading1'], alignment=TA_CENTER, fontSize=24, spaceAfter=30)
    heading_style = ParagraphStyle('Heading', parent=styles['Heading2'], fontSize=18, spaceAfter=12)
    body_style = ParagraphStyle('Body', parent=styles['Normal'], fontSize=12, spaceAfter=12)
    story = []
    story.append(Paragraph(f"Course Notes: {course['name']}", title_style))
    story.append(Spacer(1, 12))
    story.append(Paragraph(f"Generated on {datetime.now().strftime('%B %d, %Y')}", styles['Normal']))
    story.append(PageBreak())
    for note in notes:
        story.append(Paragraph(note['title'], heading_style))
        content = note['content'].replace('\n', '<br/>')
        story.append(Paragraph(content, body_style))
        story.append(Spacer(1, 12))
    doc.build(story)
    buffer.seek(0)
    return send_file(buffer, as_attachment=True,
                     download_name=f"course_notes_{course['name']}.pdf",
                     mimetype='application/pdf')

@app.route('/download_selected_notes_pdf', methods=['POST'])
@login_required
def download_selected_notes_pdf():
    if session.get('is_admin'):
        flash('Admins cannot download notes.', 'warning')
        return redirect(url_for('admin_panel'))
    course_id = session.get('course_id')
    if not course_id:
        flash('Please select a course first.', 'warning')
        return redirect(url_for('select_course'))
    note_ids = request.form.getlist('selected_notes')
    if not note_ids:
        flash('No notes selected.', 'warning')
        return redirect(url_for('dashboard'))
    note_ids = [int(nid) for nid in note_ids if nid.isdigit()]
    if not note_ids:
        flash('Invalid selection.', 'danger')
        return redirect(url_for('dashboard'))
    placeholders = ','.join(['?'] * len(note_ids))
    notes = execute_query(f"""
        SELECT * FROM notes
        WHERE id IN ({placeholders})
        AND course_id = ?
        AND status = 'published'
        AND (publish_at IS NULL OR publish_at <= datetime('now'))
        ORDER BY sort_order ASC, id ASC
    """, tuple(note_ids) + (course_id,), fetch_all=True)
    if not notes:
        flash('No valid notes found.', 'warning')
        return redirect(url_for('dashboard'))
    course = execute_query("SELECT * FROM courses WHERE id = ?", (course_id,), fetch_one=True)
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter,
                            rightMargin=72, leftMargin=72,
                            topMargin=72, bottomMargin=72)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('Title', parent=styles['Heading1'], alignment=TA_CENTER, fontSize=24, spaceAfter=30)
    heading_style = ParagraphStyle('Heading', parent=styles['Heading2'], fontSize=18, spaceAfter=12)
    body_style = ParagraphStyle('Body', parent=styles['Normal'], fontSize=12, spaceAfter=12)
    story = []
    story.append(Paragraph(f"Course Notes: {course['name']}", title_style))
    story.append(Spacer(1, 12))
    story.append(Paragraph(f"Generated on {datetime.now().strftime('%B %d, %Y')}", styles['Normal']))
    story.append(PageBreak())
    for note in notes:
        story.append(Paragraph(note['title'], heading_style))
        content = note['content'].replace('\n', '<br/>')
        story.append(Paragraph(content, body_style))
        story.append(Spacer(1, 12))
    doc.build(story)
    buffer.seek(0)
    return send_file(buffer, as_attachment=True,
                     download_name=f"selected_notes_{course['name']}.pdf",
                     mimetype='application/pdf')

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if session.get('is_admin'):
        flash('Admins, use the admin panel.', 'info')
        return redirect(url_for('admin_panel'))
    user = execute_query("SELECT * FROM users WHERE id = :id", {"id": session['user_id']}, fetch_one=True)
    if not user:
        flash('User not found. Please log in again.', 'danger')
        return redirect(url_for('logout'))
    course = None
    if user.get('course_id'):
        course = execute_query("SELECT name FROM courses WHERE id = :cid", {"cid": user['course_id']}, fetch_one=True)
    read_count = 0
    total_notes = 0
    if user.get('course_id'):
        read_count_result = execute_query("SELECT COUNT(*) as count FROM read_notes WHERE user_id = :uid",
                                          {"uid": session['user_id']}, fetch_one=True)
        read_count = read_count_result['count'] if read_count_result else 0
        total_notes_result = execute_query("SELECT COUNT(*) as count FROM notes WHERE course_id = :cid AND status = 'published'",
                                           {"cid": user['course_id']}, fetch_one=True)
        total_notes = total_notes_result['count'] if total_notes_result else 0
    certificates = execute_query("""
        SELECT c.*, co.name as course_name
        FROM certificates c
        JOIN courses co ON c.course_id = co.id
        WHERE c.student_id = :uid
        ORDER BY c.issued_at DESC
    """, {"uid": session['user_id']}, fetch_all=True)
    badges = execute_query("SELECT * FROM badges WHERE user_id = :uid", {"uid": session['user_id']}, fetch_all=True)
    if request.method == 'POST':
        old_password = request.form.get('old_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')
        if old_password and new_password:
            if not check_password_hash(user['password'], old_password):
                flash('Current password is incorrect.', 'danger')
                return redirect(url_for('profile'))
            if new_password != confirm_password:
                flash('New passwords do not match.', 'danger')
                return redirect(url_for('profile'))
            if len(new_password) < 6:
                flash('Password must be at least 6 characters.', 'danger')
                return redirect(url_for('profile'))
            hashed = generate_password_hash(new_password)
            execute_query("UPDATE users SET password = :p WHERE id = :id",
                          {"p": hashed, "id": session['user_id']}, commit=True)
            flash('Password updated successfully!', 'success')
            return redirect(url_for('dashboard'))
        email = request.form.get('email', '').strip()
        phone = request.form.get('phone', '').strip()
        dob = request.form.get('dob', '').strip()
        bio = request.form.get('bio', '').strip()
        if email:
            execute_query("UPDATE users SET email = :e WHERE id = :id",
                          {"e": email, "id": session['user_id']}, commit=True)
            flash('Email updated!', 'success')
        if phone:
            execute_query("UPDATE users SET phone = :p WHERE id = :id",
                          {"p": phone, "id": session['user_id']}, commit=True)
            flash('Phone updated!', 'success')
        if dob:
            execute_query("UPDATE users SET dob = :d WHERE id = :id",
                          {"d": dob, "id": session['user_id']}, commit=True)
        if bio:
            execute_query("UPDATE users SET bio = :b WHERE id = :id",
                          {"b": bio, "id": session['user_id']}, commit=True)
        email_notifications = 1 if request.form.get('email_notifications') else 0
        execute_query("UPDATE users SET email_notifications = :en WHERE id = :id",
                      {"en": email_notifications, "id": session['user_id']}, commit=True)
        flash('Profile updated!', 'success')
        if 'avatar' in request.files:
            avatar_file = request.files['avatar']
            if avatar_file and avatar_file.filename != '' and allowed_file(avatar_file.filename):
                ext = avatar_file.filename.rsplit('.', 1)[1].lower()
                avatar_name = f"avatar_{session['user_id']}.{ext}"
                avatar_path = os.path.join(app.config['UPLOAD_FOLDER'], avatar_name)
                avatar_file.save(avatar_path)
                execute_query("UPDATE users SET avatar = :a WHERE id = :id",
                              {"a": f"/uploads/{avatar_name}", "id": session['user_id']}, commit=True)
                flash('Avatar updated!', 'success')
            else:
                flash('Invalid file type. Allowed: png, jpg, jpeg, gif', 'danger')
        return redirect(url_for('profile'))
    return render_template('profile.html',
                           user=user,
                           course=course,
                           read_count=read_count,
                           total_notes=total_notes,
                           certificates=certificates,
                           badges=badges)

@app.route('/assignments')
@login_required
def assignments():
    if session.get('is_admin'):
        return redirect(url_for('admin_panel'))
    course_id = session.get('course_id')
    if not course_id:
        return redirect(url_for('select_course'))
    course = execute_query("SELECT * FROM courses WHERE id = :id", {"id": course_id}, fetch_one=True)
    all_assignments = execute_query("""
        SELECT * FROM assignments
        WHERE course_id = :id
        AND (publish_at IS NULL OR publish_at <= datetime('now'))
        ORDER BY due_date ASC
    """, {"id": course_id}, fetch_all=True)
    submissions = execute_query("SELECT assignment_id, file_path, grade, feedback FROM submissions WHERE student_id = :sid",
                                {"sid": session['user_id']}, fetch_all=True)
    submitted_map = {s['assignment_id']: s for s in submissions} if submissions else {}
    return render_template('assignments.html', course=course, assignments=all_assignments, submitted_map=submitted_map)

@app.route('/submit_assignment/<int:assignment_id>', methods=['POST'])
@login_required
def submit_assignment(assignment_id):
    if session.get('is_admin'):
        flash('Admins cannot submit assignments.', 'warning')
        return redirect(url_for('admin_panel'))
    existing = execute_query("SELECT id FROM submissions WHERE student_id = :sid AND assignment_id = :aid",
                             {"sid": session['user_id'], "aid": assignment_id}, fetch_one=True)
    if existing:
        flash('You already submitted this assignment!', 'info')
        return redirect(url_for('assignments'))
    if 'file' not in request.files:
        flash('No file selected.', 'danger')
        return redirect(url_for('assignments'))
    file = request.files['file']
    if file.filename == '':
        flash('No file selected.', 'danger')
        return redirect(url_for('assignments'))
    if file and allowed_file(file.filename):
        original_name = secure_filename(file.filename)
        unique_name = f"{uuid.uuid4().hex}_{original_name}"
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_name)
        file.save(file_path)
        execute_query("INSERT INTO submissions (student_id, assignment_id, file_path) VALUES (:sid, :aid, :fp)",
                      {"sid": session['user_id'], "aid": assignment_id, "fp": unique_name}, commit=True)
        user = execute_query("SELECT full_name, email FROM users WHERE id = :id", {"id": session['user_id']}, fetch_one=True)
        assignment = execute_query("SELECT title FROM assignments WHERE id = :id", {"id": assignment_id}, fetch_one=True)
        create_notification(f"{user['full_name']} submitted assignment: {assignment['title']}",
                            url_for('view_submissions', assignment_id=assignment_id))
        admin_email = get_admin_email()
        if admin_email:
            subject = f"📋 Assignment Submitted: {assignment['title']}"
            body = f"{user['full_name']} has submitted the assignment '{assignment['title']}'.\n\nView submissions: {APP_BASE_URL}/admin/submissions/{assignment_id}"
            send_email(admin_email, subject, body)
        log_activity(session['user_id'], 'submit_assignment', f'Assignment {assignment_id} submitted')
        flash('Assignment submitted successfully! 🎉', 'success')
    else:
        flash('File type not allowed.', 'danger')
    return redirect(url_for('assignments'))

@app.route('/classmates')
@login_required
def classmates():
    if session.get('is_admin'):
        flash('Admins can view all students from the admin panel.', 'info')
        return redirect(url_for('view_students'))
    students = execute_query("""
        SELECT u.full_name, u.username, u.avatar, c.name as course_name
        FROM users u
        LEFT JOIN courses c ON u.course_id = c.id
        WHERE u.is_admin = 0
        ORDER BY u.full_name ASC
    """, fetch_all=True)
    return render_template('classmates.html', students=students)

@app.route('/my_notes')
@login_required
def my_notes():
    if session.get('is_admin'):
        flash('Admins do not have private notes.', 'info')
        return redirect(url_for('admin_panel'))
    notes = execute_query("""
        SELECT sn.*, n.title as note_title, n.id as note_id, c.name as course_name
        FROM student_notes sn
        JOIN notes n ON sn.note_id = n.id
        JOIN courses c ON n.course_id = c.id
        WHERE sn.user_id = :uid
        ORDER BY sn.updated_at DESC
    """, {"uid": session['user_id']}, fetch_all=True)
    return render_template('my_notes.html', notes=notes)

@app.route('/note/<int:note_id>/private_note', methods=['POST'])
@login_required
def save_private_note(note_id):
    if session.get('is_admin'):
        flash('Admins cannot save private notes.', 'warning')
        return redirect(url_for('note_detail', note_id=note_id))
    content = request.form.get('private_note', '').strip()
    note = execute_query("SELECT id FROM notes WHERE id = :id AND status = 'published'", {"id": note_id}, fetch_one=True)
    if not note:
        flash('Note not found.', 'danger')
        return redirect(url_for('dashboard'))
    existing = execute_query("SELECT id FROM student_notes WHERE user_id = :uid AND note_id = :nid",
                             {"uid": session['user_id'], "nid": note_id}, fetch_one=True)
    if existing:
        execute_query("UPDATE student_notes SET content = :c, updated_at = CURRENT_TIMESTAMP WHERE id = :id",
                      {"c": content, "id": existing['id']}, commit=True)
        flash('Private note updated!', 'success')
    else:
        execute_query("INSERT INTO student_notes (user_id, note_id, content) VALUES (:uid, :nid, :c)",
                      {"uid": session['user_id'], "nid": note_id, "c": content}, commit=True)
        flash('Private note saved!', 'success')
    return redirect(url_for('note_detail', note_id=note_id))

@app.route('/note/<int:note_id>/delete_private_note', methods=['POST'])
@login_required
def delete_private_note(note_id):
    if session.get('is_admin'):
        flash('Admins cannot delete private notes.', 'warning')
        return redirect(url_for('note_detail', note_id=note_id))
    execute_query("DELETE FROM student_notes WHERE user_id = :uid AND note_id = :nid",
                  {"uid": session['user_id'], "nid": note_id}, commit=True)
    flash('Private note deleted.', 'info')
    return redirect(url_for('note_detail', note_id=note_id))

@app.route('/note/<int:note_id>')
@login_required
def note_detail(note_id):
    if session.get('is_admin'):
        flash('Admins cannot view note details.', 'warning')
        return redirect(url_for('admin_panel'))
    course_id = session.get('course_id')
    if not course_id:
        return redirect(url_for('select_course'))
    note = execute_query("""
        SELECT * FROM notes
        WHERE id = :id AND status = 'published' AND course_id = :cid
        AND (publish_at IS NULL OR publish_at <= datetime('now'))
    """, {"id": note_id, "cid": course_id}, fetch_one=True)
    if not note:
        flash('Note not found.', 'danger')
        return redirect(url_for('dashboard'))
    note['content_html'] = markdown2.markdown(
        note['content'],
        extras=['fenced-code-blocks']
    )
    all_discussions = execute_query("""
        SELECT d.*, u.full_name, u.is_admin
        FROM discussions d
        JOIN users u ON d.user_id = u.id
        WHERE d.note_id = :nid
        ORDER BY d.created_at ASC
    """, {"nid": note_id}, fetch_all=True) or []
    disc_dict = {d['id']: d for d in all_discussions}
    top_level = []
    for d in all_discussions:
        if d['parent_id'] is not None:
            parent = disc_dict.get(d['parent_id'])
            if parent:
                d['parent_author_name'] = parent['full_name']
            else:
                d['parent_author_name'] = None
        else:
            d['parent_author_name'] = None
        if d['parent_id'] is None:
            top_level.append(d)
        else:
            parent = disc_dict.get(d['parent_id'])
            if parent:
                if 'replies' not in parent:
                    parent['replies'] = []
                parent['replies'].append(d)
    students = execute_query("""
        SELECT username, full_name
        FROM users
        WHERE course_id = :cid AND is_admin = 0
    """, {"cid": course_id}, fetch_all=True)
    private_note = None
    if not session.get('is_admin'):
        private_note = execute_query("SELECT content FROM student_notes WHERE user_id = :uid AND note_id = :nid",
                                     {"uid": session['user_id'], "nid": note_id}, fetch_one=True)
    return render_template('note_detail.html',
                           note=note,
                           discussions=top_level,
                           students=students,
                           private_note=private_note)

@app.route('/note/<int:note_id>/add_comment', methods=['POST'])
@login_required
def note_add_comment(note_id):
    if session.get('is_admin'):
        flash('Admins cannot post comments.', 'warning')
        return redirect(url_for('admin_panel'))
    message = request.form.get('message', '').strip()
    if not message:
        flash('Comment cannot be empty.', 'danger')
        return redirect(url_for('note_detail', note_id=note_id))
    note = execute_query("SELECT course_id FROM notes WHERE id = :id AND status = 'published'", {"id": note_id}, fetch_one=True)
    if not note or note['course_id'] != session.get('course_id'):
        flash('Invalid note.', 'danger')
        return redirect(url_for('dashboard'))
    execute_query("INSERT INTO discussions (note_id, user_id, message) VALUES (:nid, :uid, :msg)",
                  {"nid": note_id, "uid": session['user_id'], "msg": message}, commit=True)
    new_id_row = execute_query("SELECT last_insert_rowid() as id", fetch_one=True)
    new_id = new_id_row['id'] if new_id_row else None
    log_activity(session['user_id'], 'note_add_comment', f'Comment on note {note_id}')
    flash('Comment posted!', 'success')
    return redirect(url_for('note_detail', note_id=note_id, new_comment=new_id, _t=datetime.now().timestamp()) + '#comment-' + str(new_id))

@app.route('/note/reply/<int:parent_id>', methods=['POST'])
@login_required
def note_add_reply(parent_id):
    if session.get('is_admin'):
        flash('Admins cannot reply.', 'warning')
        return redirect(url_for('admin_panel'))
    message = request.form.get('message', '').strip()
    if not message:
        flash('Reply cannot be empty.', 'danger')
        return redirect(url_for('dashboard'))
    parent = execute_query("SELECT note_id FROM discussions WHERE id = :id", {"id": parent_id}, fetch_one=True)
    if not parent:
        flash('Invalid parent comment.', 'danger')
        return redirect(url_for('dashboard'))
    note_id = parent['note_id']
    note = execute_query("SELECT course_id FROM notes WHERE id = :nid AND status = 'published'", {"id": note_id}, fetch_one=True)
    if not note or note['course_id'] != session.get('course_id'):
        flash('Invalid reply target.', 'danger')
        return redirect(url_for('dashboard'))
    execute_query("INSERT INTO discussions (note_id, user_id, parent_id, message) VALUES (:nid, :uid, :pid, :msg)",
                  {"nid": note_id, "uid": session['user_id'], "pid": parent_id, "msg": message}, commit=True)
    new_id_row = execute_query("SELECT last_insert_rowid() as id", fetch_one=True)
    new_id = new_id_row['id'] if new_id_row else None
    parent_comment = execute_query("SELECT user_id FROM discussions WHERE id = :pid", {"pid": parent_id}, fetch_one=True)
    if parent_comment and parent_comment['user_id'] != session['user_id']:
        user = execute_query("SELECT full_name FROM users WHERE id = :id", {"id": session['user_id']}, fetch_one=True)
        notif_msg = f"{user['full_name']} replied to your comment: {message[:50]}{'...' if len(message) > 50 else ''}"
        create_user_notification(parent_comment['user_id'], notif_msg,
                                 url_for('note_detail', note_id=note_id, new_reply=new_id) + '#comment-' + str(parent_id))
        parent_user = execute_query("SELECT id, email, email_notifications FROM users WHERE id = :id", {"id": parent_comment['user_id']}, fetch_one=True)
        if parent_user and parent_user['email_notifications'] == 1:
            send_user_email(parent_user['id'],
                            f"💬 New reply from {user['full_name']}",
                            f"{user['full_name']} replied to your comment on note '{note['title']}'.\n\nReply: {message}\n\nView: {APP_BASE_URL}/note/{note_id}")
    log_activity(session['user_id'], 'note_add_reply', f'Reply to discussion {parent_id} on note {note_id}')
    flash('Reply posted!', 'success')
    return redirect(url_for('note_detail', note_id=note_id,
                            new_reply=new_id,
                            _t=datetime.now().timestamp()) + '#comment-' + str(parent_id))

@app.route('/notifications')
@login_required
def student_notifications():
    if session.get('is_admin'):
        return redirect(url_for('view_notifications'))
    execute_query("UPDATE notifications SET is_read = 1 WHERE user_id = :uid",
                  {"uid": session['user_id']}, commit=True)
    notifications = execute_query("SELECT * FROM notifications WHERE user_id = :uid ORDER BY created_at DESC",
                                  {"uid": session['user_id']}, fetch_all=True)
    return render_template('notifications.html', notifications=notifications)

@app.route('/assignment_history')
@login_required
def assignment_history():
    if session.get('is_admin'):
        return redirect(url_for('admin_panel'))
    submissions = execute_query("""
        SELECT s.*, a.title as assignment_title, a.description, c.name as course_name
        FROM submissions s
        JOIN assignments a ON s.assignment_id = a.id
        JOIN courses c ON a.course_id = c.id
        WHERE s.student_id = :uid
        ORDER BY s.submitted_at DESC
    """, {"uid": session['user_id']}, fetch_all=True)
    return render_template('assignment_history.html', submissions=submissions)

@app.route('/student/messages')
@login_required
def student_messages():
    if session.get('is_admin'):
        return redirect(url_for('admin_messages'))
    messages = execute_query("""
        SELECT m.*, u.full_name as sender_name
        FROM messages m
        JOIN users u ON m.sender_id = u.id
        WHERE m.receiver_id = :uid OR m.sender_id = :uid
        ORDER BY m.created_at DESC
    """, {"uid": session['user_id']}, fetch_all=True)
    execute_query("UPDATE messages SET is_read = 1 WHERE receiver_id = :uid",
                  {"uid": session['user_id']}, commit=True)
    return render_template('student_messages.html', messages=messages)

@app.route('/student/send_message', methods=['POST'])
@login_required
def student_send_message():
    if session.get('is_admin'):
        flash('Admins use the admin panel.', 'warning')
        return redirect(url_for('admin_messages'))
    receiver_id = request.form['receiver_id']
    message = request.form['message'].strip()
    if not message:
        flash('Message cannot be empty.', 'danger')
        return redirect(url_for('student_messages'))
    admin_id = get_admin_id()
    if int(receiver_id) != admin_id:
        flash('You can only message the instructor.', 'danger')
        return redirect(url_for('student_messages'))
    execute_query("INSERT INTO messages (sender_id, receiver_id, message) VALUES (:sid, :rid, :msg)",
                  {"sid": session['user_id'], "rid": receiver_id, "msg": message}, commit=True)
    create_notification(f"New message from {session['full_name']}", url_for('admin_messages'))
    admin_email = get_admin_email()
    if admin_email:
        subject = f"📩 New message from {session['full_name']}"
        body = f"{session['full_name']} sent you a message:\n\n{message}\n\nReply: {APP_BASE_URL}/admin/messages"
        send_email(admin_email, subject, body)
    flash('Message sent!', 'success')
    return redirect(url_for('student_messages'))

@app.route('/discussion/upvote/<int:discussion_id>')
@login_required
def upvote_discussion(discussion_id):
    execute_query("UPDATE discussions SET upvotes = upvotes + 1 WHERE id = :id",
                  {"id": discussion_id}, commit=True)
    note = execute_query("SELECT note_id FROM discussions WHERE id = :id", {"id": discussion_id}, fetch_one=True)
    if note:
        log_activity(session['user_id'], 'upvote_discussion', f'Upvoted discussion {discussion_id}')
        return redirect(url_for('note_detail', note_id=note['note_id']) + '#comment-' + str(discussion_id))
    return redirect(url_for('dashboard'))

@app.route('/discussion/resolve/<int:discussion_id>')
@login_required
def resolve_discussion(discussion_id):
    disc = execute_query("SELECT user_id, note_id FROM discussions WHERE id = :id AND parent_id IS NULL",
                         {"id": discussion_id}, fetch_one=True)
    if not disc:
        flash('Invalid discussion.', 'danger')
        return redirect(url_for('dashboard'))
    if session['user_id'] == disc['user_id'] or session.get('is_admin'):
        execute_query("UPDATE discussions SET is_resolved = 1 WHERE id = :id",
                      {"id": discussion_id}, commit=True)
        log_activity(session['user_id'], 'resolve_discussion', f'Resolved discussion {discussion_id}')
        flash('Question marked as resolved.', 'success')
    else:
        flash('You do not have permission to resolve this.', 'danger')
    return redirect(url_for('note_detail', note_id=disc['note_id']) + '#comment-' + str(discussion_id))

# ========================
# ADMIN ROUTES
# ========================
@app.route('/admin')
@login_required
@admin_required
def admin_panel():
    filter_course = request.args.get('course_id')
    courses = execute_query("SELECT * FROM courses", fetch_all=True)
    all_tags = execute_query("SELECT * FROM tags ORDER BY name", fetch_all=True)
    total_students = execute_query("SELECT COUNT(*) as count FROM users WHERE is_admin = 0", fetch_one=True)
    total_notes = execute_query("SELECT COUNT(*) as count FROM notes WHERE status = 'published'", fetch_one=True)
    total_assignments = execute_query("SELECT COUNT(*) as count FROM assignments", fetch_one=True)
    pending_submissions = execute_query("SELECT COUNT(*) as count FROM submissions WHERE grade IS NULL", fetch_one=True)
    templates = execute_query("SELECT * FROM note_templates ORDER BY id DESC", fetch_all=True)
    admin_id = get_admin_id()
    if admin_id:
        notifications = execute_query("SELECT * FROM notifications WHERE user_id = :uid ORDER BY created_at DESC LIMIT 10",
                                      {"uid": admin_id}, fetch_all=True)
        unread_count = execute_query("SELECT COUNT(*) as count FROM notifications WHERE user_id = :uid AND is_read = 0",
                                     {"uid": admin_id}, fetch_one=True)
        unread_count = unread_count['count'] if unread_count else 0
    else:
        notifications = []
        unread_count = 0
    enrollment = execute_query("""
        SELECT c.name, COUNT(u.id) as count
        FROM courses c
        LEFT JOIN users u ON u.course_id = c.id AND u.is_admin = 0
        GROUP BY c.id
    """, fetch_all=True)
    notes_query = """
        SELECT notes.*, courses.name as course_name
        FROM notes
        JOIN courses ON notes.course_id = courses.id
    """
    assignments_query = """
        SELECT assignments.*, courses.name as course_name
        FROM assignments
        JOIN courses ON assignments.course_id = courses.id
    """
    announcements_query = """
        SELECT announcements.*, courses.name as course_name
        FROM announcements
        LEFT JOIN courses ON announcements.course_id = courses.id
    """
    params = {}
    if filter_course and filter_course != 'all':
        notes_query += " WHERE notes.course_id = :cid"
        assignments_query += " WHERE assignments.course_id = :cid"
        announcements_query += " WHERE announcements.course_id = :cid OR announcements.course_id IS NULL"
        params['cid'] = filter_course
    notes_query += " ORDER BY notes.sort_order ASC, notes.id ASC"
    assignments_query += " ORDER BY assignments.due_date ASC"
    announcements_query += " ORDER BY announcements.created_at DESC"
    notes = execute_query(notes_query, params, fetch_all=True)
    assignments = execute_query(assignments_query, params, fetch_all=True)
    announcements = execute_query(announcements_query, params, fetch_all=True)
    return render_template('admin_panel.html',
                           courses=courses,
                           notes=notes,
                           assignments=assignments,
                           announcements=announcements,
                           course_filter=filter_course,
                           total_students=total_students['count'],
                           total_notes=total_notes['count'],
                           total_assignments=total_assignments['count'],
                           pending_submissions=pending_submissions['count'],
                           notifications=notifications,
                           unread_count=unread_count,
                           enrollment=enrollment,
                           templates=templates,
                           all_tags=all_tags)

@app.route('/admin/tags')
@login_required
@admin_required
def admin_tags():
    tags = execute_query("SELECT * FROM tags ORDER BY name", fetch_all=True)
    return render_template('admin_tags.html', tags=tags)

@app.route('/admin/add_tag', methods=['POST'])
@login_required
@admin_required
def add_tag():
    name = request.form['name'].strip()
    color = request.form.get('color', '#6c63ff')
    if not name:
        flash('Tag name is required.', 'danger')
        return redirect(url_for('admin_tags'))
    try:
        execute_query("INSERT INTO tags (name, color) VALUES (:n, :c)", {"n": name, "c": color}, commit=True)
        flash('Tag added!', 'success')
    except Exception as e:
        flash(f'Error: {str(e)}', 'danger')
    return redirect(url_for('admin_tags'))

@app.route('/admin/delete_tag/<int:tag_id>')
@login_required
@admin_required
def delete_tag(tag_id):
    execute_query("DELETE FROM tags WHERE id = :id", {"id": tag_id}, commit=True)
    flash('Tag deleted.', 'info')
    return redirect(url_for('admin_tags'))

@app.route('/admin/add_note', methods=['POST'])
@login_required
@admin_required
def add_note():
    title = request.form['title']
    content = request.form['content']
    course_id = request.form['course_id']
    status = request.form.get('status', 'draft')
    cohort = request.form.get('cohort') or None
    max_order = execute_query("SELECT MAX(sort_order) as max_order FROM notes WHERE course_id = :cid",
                              {"cid": course_id}, fetch_one=True)
    sort_order = (max_order['max_order'] or 0) + 1
    execute_query("""
        INSERT INTO notes (title, content, course_id, status, sort_order, cohort)
        VALUES (:t, :c, :cid, :s, :so, :coh)
    """, {"t": title, "c": content, "cid": course_id, "s": status, "so": sort_order, "coh": cohort}, commit=True)
    new_id = execute_query("SELECT last_insert_rowid() as id", fetch_one=True)['id']
    tag_ids = request.form.getlist('tags')
    if tag_ids:
        sync_note_tags(new_id, tag_ids)
    if status == 'published':
        course = execute_query("SELECT name FROM courses WHERE id = :id", {"id": course_id}, fetch_one=True)
        subject = f"📝 New Note: {title}"
        body = f"A new note '{title}' has been published in {course['name']}.\n\nView it here: {APP_BASE_URL}/note/{new_id}"
        send_course_emails(course_id, subject, body, exclude_user=session['username'])
        flash('Note published and email sent to students!', 'success')
    else:
        flash('Note saved as draft.', 'info')
    return redirect(url_for('admin_panel', course_id=course_id))

@app.route('/admin/edit_note/<int:note_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_note(note_id):
    note = execute_query("SELECT * FROM notes WHERE id = :id", {"id": note_id}, fetch_one=True)
    if not note:
        flash('Note not found.', 'danger')
        return redirect(url_for('admin_panel', course_id=request.args.get('course_id')))
    if request.method == 'POST':
        title = request.form['title']
        content = request.form['content']
        status = request.form['status']
        cohort = request.form.get('cohort') or None
        execute_query("""
            UPDATE notes SET title = :t, content = :c, status = :s, cohort = :coh
            WHERE id = :id
        """, {"t": title, "c": content, "s": status, "coh": cohort, "id": note_id}, commit=True)
        tag_ids = request.form.getlist('tags')
        sync_note_tags(note_id, tag_ids)
        flash('Note updated!', 'success')
        return redirect(url_for('admin_panel', course_id=request.args.get('course_id')))
    note_tags = execute_query("SELECT tag_id FROM note_tags WHERE note_id = :nid", {"nid": note_id}, fetch_all=True)
    selected_tag_ids = [t['tag_id'] for t in note_tags] if note_tags else []
    all_tags = execute_query("SELECT * FROM tags ORDER BY name", fetch_all=True)
    return render_template('edit_note.html', note=note, all_tags=all_tags, selected_tag_ids=selected_tag_ids)

@app.route('/admin/delete_note/<int:note_id>')
@login_required
@admin_required
def delete_note(note_id):
    execute_query("DELETE FROM notes WHERE id = :id", {"id": note_id}, commit=True)
    flash('Note deleted.', 'info')
    return redirect(url_for('admin_panel', course_id=request.args.get('course_id')))

@app.route('/admin/toggle_note/<int:note_id>')
@login_required
@admin_required
def toggle_note(note_id):
    note = execute_query("SELECT status, course_id, title FROM notes WHERE id = :id", {"id": note_id}, fetch_one=True)
    if note:
        new_status = 'draft' if note['status'] == 'published' else 'published'
        execute_query("UPDATE notes SET status = :status WHERE id = :id",
                      {"status": new_status, "id": note_id}, commit=True)
        flash(f'Note status: {new_status}', 'success')
        if new_status == 'published':
            course = execute_query("SELECT name FROM courses WHERE id = :id", {"id": note['course_id']}, fetch_one=True)
            subject = f"📝 New Note: {note['title']}"
            body = f"A new note '{note['title']}' has been published in {course['name']}.\n\nView it here: {APP_BASE_URL}/note/{note_id}"
            send_course_emails(note['course_id'], subject, body, exclude_user=session['username'])
    return redirect(url_for('admin_panel', course_id=request.args.get('course_id')))

@app.route('/admin/publish_all/<int:course_id>')
@login_required
@admin_required
def publish_all(course_id):
    execute_query("UPDATE notes SET status = 'published' WHERE course_id = :cid",
                  {"cid": course_id}, commit=True)
    course = execute_query("SELECT name FROM courses WHERE id = :id", {"id": course_id}, fetch_one=True)
    subject = f"📢 New Content Published: {course['name']}"
    body = f"All notes for {course['name']} have been published. Log in to view them.\n\n{APP_BASE_URL}/dashboard"
    send_course_emails(course_id, subject, body, exclude_user=session['username'])
    flash('All notes for this course published!', 'success')
    return redirect(url_for('admin_panel', course_id=course_id))

@app.route('/admin/scheduled')
@login_required
@admin_required
def admin_scheduled():
    filter_course = request.args.get('course_id')
    courses = execute_query("SELECT * FROM courses", fetch_all=True)
    params = {}
    notes_query = """
        SELECT notes.*, courses.name as course_name
        FROM notes
        JOIN courses ON notes.course_id = courses.id
        WHERE notes.status = 'scheduled'
    """
    assignments_query = """
        SELECT assignments.*, courses.name as course_name
        FROM assignments
        JOIN courses ON assignments.course_id = courses.id
        WHERE assignments.publish_at IS NOT NULL AND assignments.publish_at > datetime('now')
    """
    if filter_course and filter_course != 'all':
        notes_query += " AND notes.course_id = :cid"
        assignments_query += " AND assignments.course_id = :cid"
        params['cid'] = filter_course
    notes_query += " ORDER BY notes.publish_at ASC"
    assignments_query += " ORDER BY assignments.publish_at ASC"
    scheduled_notes = execute_query(notes_query, params, fetch_all=True)
    scheduled_assignments = execute_query(assignments_query, params, fetch_all=True)
    return render_template('admin_scheduled.html',
                           courses=courses,
                           scheduled_notes=scheduled_notes,
                           scheduled_assignments=scheduled_assignments,
                           course_filter=filter_course)

@app.route('/admin/publish_now/<int:note_id>')
@login_required
@admin_required
def publish_now(note_id):
    note = execute_query("SELECT course_id, title FROM notes WHERE id = :id", {"id": note_id}, fetch_one=True)
    if not note:
        flash('Note not found.', 'danger')
        return redirect(url_for('admin_scheduled'))
    execute_query("UPDATE notes SET status = 'published', publish_at = NULL WHERE id = :id",
                  {"id": note_id}, commit=True)
    course_id = note['course_id']
    course = execute_query("SELECT name FROM courses WHERE id = :id", {"id": course_id}, fetch_one=True)
    subject = f"📝 New Note: {note['title']}"
    body = f"A new note '{note['title']}' has been published in {course['name']}.\n\nView it here: {APP_BASE_URL}/note/{note_id}"
    send_course_emails(course_id, subject, body, exclude_user=session['username'])
    flash('Note published now!', 'success')
    return redirect(url_for('admin_scheduled', course_id=request.args.get('course_id')))

@app.route('/admin/publish_assignment_now/<int:assignment_id>')
@login_required
@admin_required
def publish_assignment_now(assignment_id):
    assignment = execute_query("SELECT course_id, title FROM assignments WHERE id = :id", {"id": assignment_id}, fetch_one=True)
    if not assignment:
        flash('Assignment not found.', 'danger')
        return redirect(url_for('admin_scheduled'))
    execute_query("UPDATE assignments SET publish_at = NULL WHERE id = :id", {"id": assignment_id}, commit=True)
    course = execute_query("SELECT name FROM courses WHERE id = :id", {"id": assignment['course_id']}, fetch_one=True)
    subject = f"📋 New Assignment: {assignment['title']}"
    body = f"A new assignment '{assignment['title']}' has been published in {course['name']}.\n\nView it here: {APP_BASE_URL}/assignments"
    send_course_emails(assignment['course_id'], subject, body, exclude_user=session['username'])
    flash('Assignment published now!', 'success')
    return redirect(url_for('admin_scheduled', course_id=request.args.get('course_id')))

@app.route('/admin/schedule_note', methods=['POST'])
@login_required
@admin_required
def schedule_note():
    title = request.form['title']
    content = request.form['content']
    course_id = request.form['course_id']
    publish_at = request.form['publish_at']
    execute_query("""
        INSERT INTO notes (title, content, course_id, status, publish_at)
        VALUES (:t, :c, :cid, 'scheduled', :pa)
    """, {"t": title, "c": content, "cid": course_id, "pa": publish_at}, commit=True)
    flash('Note scheduled!', 'success')
    return redirect(url_for('admin_scheduled', course_id=course_id))

@app.route('/admin/reorder_notes', methods=['POST'])
@login_required
@admin_required
def reorder_notes():
    data = request.get_json()
    course_id = data.get('course_id')
    note_ids = data.get('note_ids', [])
    if not note_ids:
        return jsonify({'error': 'No notes provided'}), 400
    for idx, note_id in enumerate(note_ids):
        execute_query("UPDATE notes SET sort_order = :so WHERE id = :id AND course_id = :cid",
                      {"so": idx, "id": note_id, "cid": course_id}, commit=True)
    log_activity(session['user_id'], 'reorder_notes', f'Reordered notes for course {course_id}')
    return jsonify({'success': True})

@app.route('/admin/progress')
@login_required
@admin_required
def admin_progress():
    students = execute_query("""
        SELECT u.id, u.full_name, u.username, u.course_id, c.name as course_name,
               (SELECT COUNT(*) FROM notes WHERE course_id = u.course_id AND status = 'published') as total_notes,
               (SELECT COUNT(*) FROM read_notes WHERE user_id = u.id) as read_count,
               (SELECT COUNT(*) FROM assignments WHERE course_id = u.course_id) as total_assignments,
               (SELECT COUNT(*) FROM submissions WHERE student_id = u.id) as submitted_count,
               (SELECT AVG(grade) FROM submissions WHERE student_id = u.id AND grade IS NOT NULL) as avg_grade
        FROM users u
        LEFT JOIN courses c ON u.course_id = c.id
        WHERE u.is_admin = 0
        ORDER BY u.created_at DESC
    """, fetch_all=True)
    for s in students:
        s['read_progress'] = int((s['read_count'] / s['total_notes']) * 100) if s['total_notes'] > 0 else 0
        s['assignment_progress'] = int((s['submitted_count'] / s['total_assignments']) * 100) if s['total_assignments'] > 0 else 0
        s['avg_grade'] = int(s['avg_grade']) if s['avg_grade'] else None
    return render_template('admin_progress.html', students=students)

@app.route('/admin/export_grades/<int:assignment_id>')
@login_required
@admin_required
def export_grades(assignment_id):
    assignment = execute_query("SELECT * FROM assignments WHERE id = :id", {"id": assignment_id}, fetch_one=True)
    if not assignment:
        flash('Assignment not found.', 'danger')
        return redirect(url_for('admin_panel'))
    submissions = execute_query("""
        SELECT u.full_name, u.username, s.file_path, s.grade, s.feedback, s.submitted_at
        FROM submissions s
        JOIN users u ON s.student_id = u.id
        WHERE s.assignment_id = :aid
        ORDER BY u.full_name ASC
    """, {"aid": assignment_id}, fetch_all=True)
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(['Student', 'Username', 'File', 'Grade', 'Feedback', 'Submitted At'])
    for sub in submissions:
        writer.writerow([sub['full_name'], sub['username'], sub['file_path'], sub['grade'] or '', sub['feedback'] or '', sub['submitted_at']])
    output.seek(0)
    return send_file(StringIO(output.getvalue()),
                     mimetype='text/csv',
                     as_attachment=True,
                     download_name=f"grades_{assignment['title']}.csv")

@app.route('/admin/messages')
@login_required
@admin_required
def admin_messages():
    students = execute_query("SELECT id, full_name, username FROM users WHERE is_admin = 0 ORDER BY full_name", fetch_all=True)
    messages = execute_query("""
        SELECT m.*, u.full_name as sender_name
        FROM messages m
        JOIN users u ON m.sender_id = u.id
        WHERE m.receiver_id = :uid OR m.sender_id = :uid
        ORDER BY m.created_at DESC
    """, {"uid": session['user_id']}, fetch_all=True)
    return render_template('admin_messages.html', students=students, messages=messages)

@app.route('/admin/send_message', methods=['POST'])
@login_required
@admin_required
def send_message():
    receiver_id = request.form['receiver_id']
    message = request.form['message'].strip()
    if not message:
        flash('Message cannot be empty.', 'danger')
        return redirect(url_for('admin_messages'))
    execute_query("INSERT INTO messages (sender_id, receiver_id, message) VALUES (:sid, :rid, :msg)",
                  {"sid": session['user_id'], "rid": receiver_id, "msg": message}, commit=True)
    create_user_notification(receiver_id, f"New message from Instructor", url_for('student_messages'))
    receiver = execute_query("SELECT id, email_notifications FROM users WHERE id = :id", {"id": receiver_id}, fetch_one=True)
    if receiver and receiver['email_notifications'] == 1:
        send_user_email(receiver_id,
                        "📩 New message from Instructor",
                        f"You have received a new message from your instructor:\n\n{message}\n\nView: {APP_BASE_URL}/student/messages")
    flash('Message sent!', 'success')
    return redirect(url_for('admin_messages'))

@app.route('/admin/activity_logs')
@login_required
@admin_required
def activity_logs():
    logs = execute_query("""
        SELECT l.*, u.full_name
        FROM activity_logs l
        JOIN users u ON l.user_id = u.id
        ORDER BY l.created_at DESC
        LIMIT 100
    """, fetch_all=True)
    return render_template('activity_logs.html', logs=logs)

# ===== FIXED BULK ACTIONS =====
@app.route('/admin/bulk_delete_notes', methods=['POST'])
@login_required
@admin_required
def bulk_delete_notes():
    ids_str = request.form.get('note_ids', '').strip()
    if not ids_str:
        flash('No notes selected.', 'warning')
        return redirect(url_for('admin_panel', course_id=request.args.get('course_id')))
    note_ids = [int(nid) for nid in ids_str.split(',') if nid.isdigit()]
    if note_ids:
        ids_joined = ','.join(map(str, note_ids))
        execute_query(f"DELETE FROM notes WHERE id IN ({ids_joined})", commit=True)
        flash(f'{len(note_ids)} notes deleted.', 'info')
    else:
        flash('Invalid selection.', 'danger')
    return redirect(url_for('admin_panel', course_id=request.args.get('course_id')))

@app.route('/admin/bulk_publish_notes', methods=['POST'])
@login_required
@admin_required
def bulk_publish_notes():
    ids_str = request.form.get('note_ids', '').strip()
    if not ids_str:
        flash('No notes selected.', 'warning')
        return redirect(url_for('admin_panel', course_id=request.args.get('course_id')))
    note_ids = [int(nid) for nid in ids_str.split(',') if nid.isdigit()]
    if note_ids:
        ids_joined = ','.join(map(str, note_ids))
        execute_query(f"UPDATE notes SET status = 'published' WHERE id IN ({ids_joined})", commit=True)
        flash(f'{len(note_ids)} notes published.', 'success')
    else:
        flash('Invalid selection.', 'danger')
    return redirect(url_for('admin_panel', course_id=request.args.get('course_id')))

@app.route('/admin/bulk_delete_students', methods=['POST'])
@login_required
@admin_required
def bulk_delete_students():
    student_ids = request.form.getlist('student_ids')
    if not student_ids:
        flash('No students selected.', 'warning')
        return redirect(url_for('view_students'))
    student_ids = [int(sid) for sid in student_ids if sid.isdigit()]
    if session['user_id'] in student_ids:
        flash('You cannot delete yourself.', 'danger')
        student_ids.remove(session['user_id'])
    if student_ids:
        ids_joined = ','.join(map(str, student_ids))
        execute_query(f"DELETE FROM users WHERE id IN ({ids_joined})", commit=True)
        flash(f'{len(student_ids)} students deleted.', 'info')
    return redirect(url_for('view_students'))

# ===== END FIXED BULK ACTIONS =====

@app.route('/admin/students')
@login_required
@admin_required
def view_students():
    students = execute_query("""
        SELECT u.id, u.username, u.full_name, u.created_at, c.name as course_name
        FROM users u
        LEFT JOIN courses c ON u.course_id = c.id
        WHERE u.is_admin = 0
        ORDER BY u.created_at DESC
    """, fetch_all=True)
    return render_template('students.html', students=students)

@app.route('/admin/delete_student/<int:student_id>')
@login_required
@admin_required
def delete_student(student_id):
    if student_id == session['user_id']:
        flash('You cannot delete yourself!', 'danger')
        return redirect(url_for('view_students'))
    execute_query("DELETE FROM users WHERE id = :id", {"id": student_id}, commit=True)
    flash('Student removed.', 'info')
    return redirect(url_for('view_students'))

@app.route('/admin/courses')
@login_required
@admin_required
def admin_courses():
    courses = execute_query("SELECT * FROM courses ORDER BY id", fetch_all=True)
    return render_template('admin_courses.html', courses=courses)

@app.route('/admin/add_course', methods=['POST'])
@login_required
@admin_required
def add_course():
    name = request.form['name'].strip()
    description = request.form['description'].strip()
    color = request.form['color'].strip()
    if not name:
        flash('Course name is required.', 'danger')
        return redirect(url_for('admin_courses'))
    try:
        execute_query("INSERT INTO courses (name, description, color) VALUES (:n, :d, :c)",
                      {"n": name, "d": description, "c": color}, commit=True)
        flash(f'Course "{name}" created!', 'success')
    except Exception as e:
        flash(f'Error: {str(e)}', 'danger')
    return redirect(url_for('admin_courses'))

@app.route('/admin/edit_course/<int:course_id>', methods=['POST'])
@login_required
@admin_required
def edit_course(course_id):
    name = request.form['name'].strip()
    description = request.form['description'].strip()
    color = request.form['color'].strip()
    if not name:
        flash('Course name is required.', 'danger')
        return redirect(url_for('admin_courses'))
    try:
        execute_query("UPDATE courses SET name = :n, description = :d, color = :c WHERE id = :id",
                      {"n": name, "d": description, "c": color, "id": course_id}, commit=True)
        flash('Course updated!', 'success')
    except Exception as e:
        flash(f'Error: {str(e)}', 'danger')
    return redirect(url_for('admin_courses'))

@app.route('/admin/delete_course/<int:course_id>')
@login_required
@admin_required
def delete_course(course_id):
    notes = execute_query("SELECT COUNT(*) as count FROM notes WHERE course_id = :id", {"id": course_id}, fetch_one=True)
    assignments = execute_query("SELECT COUNT(*) as count FROM assignments WHERE course_id = :id", {"id": course_id}, fetch_one=True)
    if notes['count'] > 0 or assignments['count'] > 0:
        flash('Cannot delete this course because it has notes or assignments. Delete them first.', 'danger')
        return redirect(url_for('admin_courses'))
    execute_query("DELETE FROM courses WHERE id = :id", {"id": course_id}, commit=True)
    flash('Course deleted.', 'info')
    return redirect(url_for('admin_courses'))

@app.route('/admin/add_assignment', methods=['POST'])
@login_required
@admin_required
def add_assignment():
    title = request.form['title']
    description = request.form['description']
    due_date = request.form['due_date']
    course_id = request.form['course_id']
    publish_at = request.form.get('publish_at')
    cohort = request.form.get('cohort') or None
    execute_query("""
        INSERT INTO assignments (title, description, due_date, publish_at, course_id, cohort)
        VALUES (:t, :d, :due, :pa, :cid, :coh)
    """, {"t": title, "d": description, "due": due_date, "pa": publish_at if publish_at else None,
          "cid": course_id, "coh": cohort}, commit=True)
    flash('Assignment created!', 'success')
    return redirect(url_for('admin_panel', course_id=course_id))

@app.route('/admin/edit_assignment/<int:assignment_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_assignment(assignment_id):
    assignment = execute_query("SELECT * FROM assignments WHERE id = :id", {"id": assignment_id}, fetch_one=True)
    if not assignment:
        flash('Assignment not found.', 'danger')
        return redirect(url_for('admin_panel', course_id=request.args.get('course_id')))
    if request.method == 'POST':
        title = request.form['title']
        description = request.form['description']
        due_date = request.form['due_date']
        publish_at = request.form.get('publish_at')
        cohort = request.form.get('cohort') or None
        execute_query("""
            UPDATE assignments
            SET title = :t, description = :d, due_date = :due, publish_at = :pa, cohort = :coh
            WHERE id = :id
        """, {"t": title, "d": description, "due": due_date, "pa": publish_at if publish_at else None,
              "coh": cohort, "id": assignment_id}, commit=True)
        flash('Assignment updated!', 'success')
        return redirect(url_for('admin_panel', course_id=request.args.get('course_id')))
    return render_template('edit_assignment.html', assignment=assignment)

@app.route('/admin/delete_assignment/<int:assignment_id>')
@login_required
@admin_required
def delete_assignment(assignment_id):
    execute_query("DELETE FROM assignments WHERE id = :id", {"id": assignment_id}, commit=True)
    flash('Assignment deleted.', 'info')
    return redirect(url_for('admin_panel', course_id=request.args.get('course_id')))

@app.route('/admin/submissions/<int:assignment_id>')
@login_required
@admin_required
def view_submissions(assignment_id):
    assignment = execute_query("SELECT * FROM assignments WHERE id = :id", {"id": assignment_id}, fetch_one=True)
    if not assignment:
        flash('Assignment not found.', 'danger')
        return redirect(url_for('admin_panel', course_id=request.args.get('course_id')))
    submissions = execute_query("""
        SELECT submissions.*, users.full_name, users.username
        FROM submissions
        JOIN users ON submissions.student_id = users.id
        WHERE submissions.assignment_id = :aid
        ORDER BY submissions.submitted_at DESC
    """, {"aid": assignment_id}, fetch_all=True)
    return render_template('submissions_list.html', assignment=assignment, submissions=submissions)

@app.route('/admin/grade_submission/<int:submission_id>', methods=['POST'])
@login_required
@admin_required
def grade_submission(submission_id):
    grade = request.form.get('grade')
    feedback = request.form.get('feedback')
    if grade:
        try:
            grade = int(grade)
        except:
            grade = None
    execute_query("UPDATE submissions SET grade = :g, feedback = :f WHERE id = :id",
                  {"g": grade, "f": feedback, "id": submission_id}, commit=True)
    flash('Grade and feedback saved!', 'success')
    sub = execute_query("SELECT assignment_id FROM submissions WHERE id = :id", {"id": submission_id}, fetch_one=True)
    return redirect(url_for('view_submissions', assignment_id=sub['assignment_id']))

@app.route('/uploads/<filename>')
@login_required
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/admin/add_announcement', methods=['POST'])
@login_required
@admin_required
def add_announcement():
    title = request.form['title']
    content = request.form['content']
    course_id = request.form.get('course_id')
    if not course_id or course_id == '':
        course_id = None
    execute_query("INSERT INTO announcements (title, content, course_id) VALUES (:t, :c, :cid)",
                  {"t": title, "c": content, "cid": course_id}, commit=True)
    send_announcement_emails(course_id, title, content, exclude_user=session['username'])
    flash('Announcement posted! Emails sent to students.', 'success')
    return redirect(url_for('admin_panel', course_id=request.args.get('course_id')))

@app.route('/admin/delete_announcement/<int:announcement_id>')
@login_required
@admin_required
def delete_announcement(announcement_id):
    execute_query("DELETE FROM announcements WHERE id = :id", {"id": announcement_id}, commit=True)
    flash('Announcement deleted.', 'info')
    return redirect(url_for('admin_panel', course_id=request.args.get('course_id')))

@app.route('/admin/notifications')
@login_required
@admin_required
def view_notifications():
    admin_id = get_admin_id()
    if not admin_id:
        flash('Admin not found.', 'danger')
        return redirect(url_for('admin_panel'))
    execute_query("UPDATE notifications SET is_read = 1 WHERE user_id = :uid",
                  {"uid": admin_id}, commit=True)
    notifications = execute_query("SELECT * FROM notifications WHERE user_id = :uid ORDER BY created_at DESC",
                                  {"uid": admin_id}, fetch_all=True)
    return render_template('notifications.html', notifications=notifications)

@app.route('/admin/quizzes')
@login_required
@admin_required
def admin_quizzes():
    quizzes = execute_query("""
        SELECT quizzes.*, courses.name as course_name
        FROM quizzes
        JOIN courses ON quizzes.course_id = courses.id
        ORDER BY quizzes.id DESC
    """, fetch_all=True)
    for q in quizzes:
        count = execute_query("SELECT COUNT(*) as cnt FROM quiz_questions WHERE quiz_id = :qid", {"qid": q['id']}, fetch_one=True)
        q['question_count'] = count['cnt'] if count else 0
    return render_template('admin_quizzes.html', quizzes=quizzes)

@app.route('/admin/add_quiz', methods=['GET', 'POST'])
@login_required
@admin_required
def add_quiz():
    if request.method == 'POST':
        title = request.form['title']
        description = request.form['description']
        course_id = request.form['course_id']
        execute_query("INSERT INTO quizzes (title, description, course_id) VALUES (:t, :d, :cid)",
                      {"t": title, "d": description, "cid": course_id}, commit=True)
        result = execute_query("SELECT last_insert_rowid() as id", fetch_one=True)
        if result and result['id']:
            flash('Quiz created! Now add questions.', 'success')
            return redirect(url_for('edit_quiz', quiz_id=result['id']))
        else:
            flash('Error creating quiz.', 'danger')
            return redirect(url_for('admin_quizzes'))
    courses = execute_query("SELECT * FROM courses", fetch_all=True)
    return render_template('add_quiz.html', courses=courses)

@app.route('/admin/edit_quiz/<int:quiz_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_quiz(quiz_id):
    quiz = execute_query("SELECT * FROM quizzes WHERE id = :id", {"id": quiz_id}, fetch_one=True)
    if not quiz:
        flash('Quiz not found.', 'danger')
        return redirect(url_for('admin_quizzes'))
    if request.method == 'POST':
        title = request.form['title']
        description = request.form['description']
        execute_query("UPDATE quizzes SET title = :t, description = :d WHERE id = :id",
                      {"t": title, "d": description, "id": quiz_id}, commit=True)
        flash('Quiz updated!', 'success')
        return redirect(url_for('admin_quizzes'))
    questions = execute_query("SELECT * FROM quiz_questions WHERE quiz_id = :qid ORDER BY id",
                              {"qid": quiz_id}, fetch_all=True)
    return render_template('edit_quiz.html', quiz=quiz, questions=questions)

@app.route('/admin/add_question/<int:quiz_id>', methods=['POST'])
@login_required
@admin_required
def add_question(quiz_id):
    question_text = request.form['question_text'].strip()
    options_text = request.form.get('options', '')
    correct = request.form.get('correct_answer')
    options = [line.strip() for line in options_text.split('\n') if line.strip()]
    if len(options) < 2:
        flash('Please provide at least 2 options.', 'danger')
        return redirect(url_for('edit_quiz', quiz_id=quiz_id))
    try:
        correct_index = int(correct)
    except ValueError:
        flash('Correct answer must be a number (index).', 'danger')
        return redirect(url_for('edit_quiz', quiz_id=quiz_id))
    if correct_index < 0 or correct_index >= len(options):
        flash(f'Correct answer index must be between 0 and {len(options)-1}.', 'danger')
        return redirect(url_for('edit_quiz', quiz_id=quiz_id))
    execute_query("""
        INSERT INTO quiz_questions (quiz_id, question_text, options, correct_answer)
        VALUES (:qid, :qt, :opts, :corr)
    """, {
        "qid": quiz_id,
        "qt": question_text,
        "opts": json.dumps(options),
        "corr": correct_index
    }, commit=True)
    flash('Question added!', 'success')
    return redirect(url_for('edit_quiz', quiz_id=quiz_id))

@app.route('/admin/delete_question/<int:question_id>')
@login_required
@admin_required
def delete_question(question_id):
    q = execute_query("SELECT quiz_id FROM quiz_questions WHERE id = :id", {"id": question_id}, fetch_one=True)
    if q:
        execute_query("DELETE FROM quiz_questions WHERE id = :id", {"id": question_id}, commit=True)
        flash('Question deleted.', 'info')
        return redirect(url_for('edit_quiz', quiz_id=q['quiz_id']))
    flash('Question not found.', 'danger')
    return redirect(url_for('admin_quizzes'))

@app.route('/admin/delete_quiz/<int:quiz_id>')
@login_required
@admin_required
def delete_quiz(quiz_id):
    execute_query("DELETE FROM quizzes WHERE id = :id", {"id": quiz_id}, commit=True)
    flash('Quiz deleted.', 'info')
    return redirect(url_for('admin_quizzes'))

@app.route('/admin/certificates')
@login_required
@admin_required
def admin_certificates():
    students = execute_query("""
        SELECT u.id, u.full_name, u.username, u.course_id, c.name as course_name,
               (SELECT COUNT(*) FROM notes WHERE course_id = u.course_id AND status = 'published') as total_notes,
               (SELECT COUNT(*) FROM read_notes WHERE user_id = u.id AND note_id IN 
                   (SELECT id FROM notes WHERE course_id = u.course_id AND status = 'published')) as read_count,
               (SELECT id FROM certificates WHERE student_id = u.id AND course_id = u.course_id) as certificate_id,
               (SELECT certificate_code FROM certificates WHERE student_id = u.id AND course_id = u.course_id) as certificate_code
        FROM users u
        LEFT JOIN courses c ON u.course_id = c.id
        WHERE u.is_admin = 0 AND u.course_id IS NOT NULL
        ORDER BY u.course_id, u.full_name
    """, fetch_all=True)
    courses = execute_query("SELECT * FROM courses ORDER BY id", fetch_all=True)
    return render_template('admin_certificates.html', students=students, courses=courses)

@app.route('/admin/issue_certificate/<int:student_id>/<int:course_id>')
@login_required
@admin_required
def issue_certificate(student_id, course_id):
    existing = execute_query("SELECT id FROM certificates WHERE student_id = :sid AND course_id = :cid",
                             {"sid": student_id, "cid": course_id}, fetch_one=True)
    if existing:
        flash('Certificate already issued.', 'info')
        return redirect(url_for('admin_certificates'))
    total_notes = execute_query("SELECT COUNT(*) as count FROM notes WHERE course_id = :cid AND status = 'published'",
                                {"cid": course_id}, fetch_one=True)
    read_notes = execute_query("""
        SELECT COUNT(*) as count
        FROM read_notes
        WHERE user_id = :sid
        AND note_id IN (SELECT id FROM notes WHERE course_id = :cid AND status = 'published')
    """, {"sid": student_id, "cid": course_id}, fetch_one=True)
    if read_notes['count'] < total_notes['count']:
        flash('Student has not completed all lessons.', 'warning')
        return redirect(url_for('admin_certificates'))
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=16))
    execute_query("INSERT INTO certificates (student_id, course_id, certificate_code) VALUES (:sid, :cid, :code)",
                  {"sid": student_id, "cid": course_id, "code": code}, commit=True)
    cert = execute_query("SELECT id FROM certificates WHERE student_id = :sid AND course_id = :cid",
                         {"sid": student_id, "cid": course_id}, fetch_one=True)
    student = execute_query("SELECT full_name, username FROM users WHERE id = :id", {"id": student_id}, fetch_one=True)
    create_notification(f"🎓 Certificate issued for {student['full_name']} (Course ID: {course_id})",
                        url_for('download_certificate', certificate_id=cert['id']) if cert else None)
    send_user_email(student_id,
                    f"🎓 Certificate of Completion for {student['full_name']}",
                    f"Congratulations! You have earned a certificate for completing the course.\n\nDownload it here: {APP_BASE_URL}/download_certificate/{cert['id']}")
    existing_badge = execute_query("SELECT id FROM badges WHERE user_id = :uid AND course_id = :cid",
                                   {"uid": student_id, "cid": course_id}, fetch_one=True)
    if not existing_badge:
        course = execute_query("SELECT name FROM courses WHERE id = :id", {"id": course_id}, fetch_one=True)
        badge_name = f"{course['name']} Master"
        execute_query("""
            INSERT INTO badges (user_id, course_id, badge_name, badge_icon)
            VALUES (:uid, :cid, :bn, :bi)
        """, {"uid": student_id, "cid": course_id, "bn": badge_name, "bi": "🏆"}, commit=True)
    flash('Certificate issued successfully!', 'success')
    return redirect(url_for('admin_certificates'))

@app.route('/admin/revoke_certificate/<int:certificate_id>')
@login_required
@admin_required
def revoke_certificate(certificate_id):
    cert = execute_query("SELECT * FROM certificates WHERE id = :id", {"id": certificate_id}, fetch_one=True)
    if not cert:
        flash('Certificate not found.', 'danger')
        return redirect(url_for('admin_certificates'))
    execute_query("DELETE FROM certificates WHERE id = :id", {"id": certificate_id}, commit=True)
    student = execute_query("SELECT full_name FROM users WHERE id = :id", {"id": cert['student_id']}, fetch_one=True)
    create_notification(f"❌ Certificate revoked for {student['full_name']} (Course ID: {cert['course_id']})")
    flash('Certificate revoked successfully.', 'info')
    return redirect(url_for('admin_certificates'))

@app.route('/admin/discussions')
@login_required
@admin_required
def admin_discussions():
    all_discussions = execute_query("""
        SELECT d.*, 
               u.full_name as user_name, 
               u.is_admin,
               n.title as note_title, 
               c.name as course_name
        FROM discussions d
        JOIN users u ON d.user_id = u.id
        JOIN notes n ON d.note_id = n.id
        JOIN courses c ON n.course_id = c.id
        ORDER BY d.created_at ASC
    """, fetch_all=True) or []
    notes_dict = {}
    for d in all_discussions:
        note_id = d['note_id']
        if note_id not in notes_dict:
            notes_dict[note_id] = {
                'note_title': d['note_title'],
                'course_name': d['course_name'],
                'comments': []
            }
        notes_dict[note_id]['comments'].append(d)
    for note_id, data in notes_dict.items():
        flat = data['comments']
        comment_map = {c['id']: c for c in flat}
        top_level = []
        for c in flat:
            if c['parent_id'] is not None:
                parent = comment_map.get(c['parent_id'])
                if parent:
                    c['parent_author_name'] = parent['user_name']
                else:
                    c['parent_author_name'] = None
            else:
                c['parent_author_name'] = None
            if c['parent_id'] is None:
                top_level.append(c)
            else:
                parent = comment_map.get(c['parent_id'])
                if parent:
                    if 'replies' not in parent:
                        parent['replies'] = []
                    parent['replies'].append(c)
        data['top_level'] = top_level
    notes_list = [{'note_id': nid, 'note_title': data['note_title'],
                   'course_name': data['course_name'],
                   'top_level': data['top_level']}
                  for nid, data in notes_dict.items()]
    return render_template('admin_discussions.html', notes=notes_list)

@app.route('/admin/delete_discussion/<int:discussion_id>')
@login_required
@admin_required
def delete_discussion(discussion_id):
    execute_query("DELETE FROM discussions WHERE id = :id", {"id": discussion_id}, commit=True)
    flash('Comment deleted.', 'info')
    return redirect(url_for('admin_discussions'))

@app.route('/admin/reply_discussion/<int:discussion_id>', methods=['POST'])
@login_required
@admin_required
def admin_reply_discussion(discussion_id):
    message = request.form.get('message', '').strip()
    if not message:
        flash('Reply cannot be empty.', 'danger')
        return redirect(url_for('admin_discussions'))
    parent = execute_query("SELECT note_id FROM discussions WHERE id = :id", {"id": discussion_id}, fetch_one=True)
    if not parent:
        flash('Invalid discussion.', 'danger')
        return redirect(url_for('admin_discussions'))
    note_id = parent['note_id']
    execute_query("""
        INSERT INTO discussions (note_id, user_id, parent_id, message)
        VALUES (:nid, :uid, :pid, :msg)
    """, {"nid": note_id, "uid": session['user_id'], "pid": discussion_id, "msg": message}, commit=True)
    parent_comment = execute_query("SELECT user_id FROM discussions WHERE id = :pid", {"pid": discussion_id}, fetch_one=True)
    if parent_comment and parent_comment['user_id'] != session['user_id']:
        notif_msg = f"Instructor replied to your comment: {message[:50]}{'...' if len(message) > 50 else ''}"
        create_user_notification(parent_comment['user_id'], notif_msg,
                                 url_for('note_detail', note_id=note_id) + '#comment-' + str(discussion_id))
        parent_user = execute_query("SELECT id, email_notifications FROM users WHERE id = :id", {"id": parent_comment['user_id']}, fetch_one=True)
        if parent_user and parent_user['email_notifications'] == 1:
            send_user_email(parent_user['id'],
                            "📩 Instructor replied to your comment",
                            f"An instructor replied to your comment:\n\n{message}\n\nView: {APP_BASE_URL}/note/{note_id}")
    flash('Reply posted as Instructor!', 'success')
    return redirect(url_for('admin_discussions'))

@app.route('/admin/clear_discussions/<int:note_id>', methods=['POST'])
@login_required
@admin_required
def clear_discussions(note_id):
    note = execute_query("SELECT id FROM notes WHERE id = :id", {"id": note_id}, fetch_one=True)
    if not note:
        flash('Note not found.', 'danger')
        return redirect(url_for('admin_panel'))
    try:
        count_before = execute_query("SELECT COUNT(*) as cnt FROM discussions WHERE note_id = :nid", {"nid": note_id}, fetch_one=True)
        execute_query("DELETE FROM discussions WHERE note_id = :nid", {"nid": note_id}, commit=True)
        count_after = execute_query("SELECT COUNT(*) as cnt FROM discussions WHERE note_id = :nid", {"nid": note_id}, fetch_one=True)
        flash(f'Discussions cleared. ({count_before["cnt"]} comments removed)', 'success')
        print(f"✅ Cleared {count_before['cnt']} discussions for note {note_id}")
    except Exception as e:
        flash(f'Error: {str(e)}', 'danger')
        print(f"❌ Error clearing discussions: {e}")
    return redirect(url_for('admin_panel', course_id=request.args.get('course_id')))

@app.route('/admin/add_template', methods=['POST'])
@login_required
@admin_required
def add_template():
    name = request.form['name'].strip()
    title = request.form['title'].strip()
    content = request.form['content'].strip()
    if not name or not title or not content:
        flash('All fields are required.', 'danger')
        return redirect(url_for('admin_panel'))
    execute_query("INSERT INTO note_templates (name, title, content) VALUES (:n, :t, :c)",
                  {"n": name, "t": title, "c": content}, commit=True)
    flash('Template saved!', 'success')
    return redirect(url_for('admin_panel'))

@app.route('/admin/delete_template/<int:template_id>')
@login_required
@admin_required
def delete_template(template_id):
    execute_query("DELETE FROM note_templates WHERE id = :id", {"id": template_id}, commit=True)
    flash('Template deleted.', 'info')
    return redirect(url_for('admin_panel'))

@app.route('/admin/get_template/<int:template_id>')
@login_required
@admin_required
def get_template(template_id):
    template = execute_query("SELECT * FROM note_templates WHERE id = :id", {"id": template_id}, fetch_one=True)
    if not template:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(template)

# ========================
# ADMIN: ANALYTICS DASHBOARD
# ========================
@app.route('/admin/analytics')
@login_required
@admin_required
def admin_analytics():
    enrollment = execute_query("""
        SELECT c.name, COUNT(u.id) as count
        FROM courses c
        LEFT JOIN users u ON u.course_id = c.id AND u.is_admin = 0
        GROUP BY c.id
    """, fetch_all=True)
    student_progress = execute_query("""
        SELECT 
            u.full_name,
            COUNT(DISTINCT n.id) as total_notes,
            COUNT(DISTINCT rn.note_id) as read_notes,
            (COUNT(DISTINCT rn.note_id) * 1.0 / COUNT(DISTINCT n.id)) * 100 as progress
        FROM users u
        JOIN courses c ON u.course_id = c.id
        JOIN notes n ON n.course_id = c.id AND n.status = 'published'
        LEFT JOIN read_notes rn ON rn.user_id = u.id AND rn.note_id = n.id
        WHERE u.is_admin = 0
        GROUP BY u.id
        HAVING total_notes > 0
        ORDER BY progress DESC
        LIMIT 10
    """, fetch_all=True)
    quiz_performance = execute_query("""
        SELECT 
            q.title,
            AVG(qa.score * 1.0 / qa.total_questions) * 100 as avg_score
        FROM quizzes q
        JOIN quiz_attempts qa ON q.id = qa.quiz_id
        GROUP BY q.id
        ORDER BY avg_score DESC
    """, fetch_all=True)
    recent_activity = execute_query("""
        SELECT l.*, u.full_name
        FROM activity_logs l
        JOIN users u ON l.user_id = u.id
        ORDER BY l.created_at DESC
        LIMIT 10
    """, fetch_all=True)
    completion_rates = execute_query("""
        SELECT 
            c.name,
            COUNT(DISTINCT u.id) as total_students,
            COUNT(DISTINCT cert.student_id) as completed_students,
            (COUNT(DISTINCT cert.student_id) * 1.0 / COUNT(DISTINCT u.id)) * 100 as completion_rate
        FROM courses c
        LEFT JOIN users u ON u.course_id = c.id AND u.is_admin = 0
        LEFT JOIN certificates cert ON cert.course_id = c.id AND cert.student_id = u.id
        GROUP BY c.id
        HAVING total_students > 0
    """, fetch_all=True)
    assignment_stats = execute_query("""
        SELECT 
            a.title,
            COUNT(DISTINCT s.student_id) as submissions,
            COUNT(DISTINCT u.id) as total_students,
            (COUNT(DISTINCT s.student_id) * 1.0 / COUNT(DISTINCT u.id)) * 100 as submission_rate
        FROM assignments a
        JOIN courses c ON a.course_id = c.id
        LEFT JOIN users u ON u.course_id = c.id AND u.is_admin = 0
        LEFT JOIN submissions s ON s.assignment_id = a.id
        GROUP BY a.id
        ORDER BY a.due_date ASC
    """, fetch_all=True)
    return render_template('admin_analytics.html',
                           enrollment=enrollment,
                           student_progress=student_progress,
                           quiz_performance=quiz_performance,
                           recent_activity=recent_activity,
                           completion_rates=completion_rates,
                           assignment_stats=assignment_stats)

# ========================
# STUDENT LEADERBOARD
# ========================
@app.route('/leaderboard')
@login_required
def leaderboard():
    if session.get('is_admin'):
        flash('Admins can view the leaderboard.', 'info')
    students = execute_query("""
        SELECT 
            u.id,
            u.full_name,
            u.username,
            c.name as course_name,
            (SELECT AVG(qa.score * 1.0 / qa.total_questions) * 100 FROM quiz_attempts qa WHERE qa.student_id = u.id) as avg_quiz_score,
            (SELECT AVG(s.grade) FROM submissions s WHERE s.student_id = u.id) as avg_assignment_grade,
            (SELECT COUNT(*) FROM activity_logs l WHERE l.user_id = u.id) as total_activities,
            (SELECT COUNT(*) FROM read_notes rn WHERE rn.user_id = u.id) as notes_read,
            (SELECT COUNT(*) FROM certificates cert WHERE cert.student_id = u.id) as certificates_earned
        FROM users u
        LEFT JOIN courses c ON u.course_id = c.id
        WHERE u.is_admin = 0
        ORDER BY avg_quiz_score DESC, avg_assignment_grade DESC, total_activities DESC
        LIMIT 20
    """, fetch_all=True)
    return render_template('leaderboard.html', students=students)

# ========================
# ADMIN: BULK EMAIL TO STUDENTS
# ========================
@app.route('/admin/bulk_email', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_bulk_email():
    courses = execute_query("SELECT * FROM courses", fetch_all=True)
    if request.method == 'POST':
        subject = request.form.get('subject', '').strip()
        body = request.form.get('body', '').strip()
        course_id = request.form.get('course_id')
        send_to = request.form.get('send_to')
        if not subject or not body:
            flash('Please fill in both subject and body.', 'danger')
            return redirect(url_for('admin_bulk_email'))
        if send_to == 'course' and course_id:
            students = execute_query("""
                SELECT username, email, full_name FROM users
                WHERE course_id = :cid AND is_admin = 0 AND email_notifications = 1
            """, {"cid": course_id}, fetch_all=True)
            course_name = execute_query("SELECT name FROM courses WHERE id = :id", {"id": course_id}, fetch_one=True)['name']
        else:
            students = execute_query("""
                SELECT username, email, full_name FROM users
                WHERE is_admin = 0 AND email_notifications = 1
            """, fetch_all=True)
            course_name = "All Courses"
        if not students:
            flash('No students with email notifications enabled found.', 'warning')
            return redirect(url_for('admin_bulk_email'))
        sent_count = 0
        for s in students:
            recipient = s['email'] if s['email'] else s['username']
            personal_body = f"Hello {s['full_name']},\n\n{body}\n\nRegards,\nAwwalu Devs Team"
            if send_email(recipient, subject, personal_body):
                sent_count += 1
        flash(f'Bulk email sent to {sent_count} students.', 'success')
        return redirect(url_for('admin_panel'))
    return render_template('admin_bulk_email.html', courses=courses)

# ========================
# CODE EXECUTION (Piston API)
# ========================
@app.route('/run_code', methods=['POST'])
@login_required
def run_code():
    data = request.get_json()
    language = data.get('language')
    code = data.get('code')
    stdin_input = data.get('stdin', '')
    if not language or not code:
        return jsonify({'error': 'Missing language or code'}), 400
    if language == 'python':
        return fallback_run_python(code, stdin_input)
    import requests
    payload = {"language": language, "source": code, "stdin": stdin_input}
    try:
        response = requests.post('https://emkc.org/api/v2/piston/execute', json=payload, timeout=10)
        if response.status_code == 200:
            result = response.json()
            output = result.get('output', '')
            return jsonify({'output': output})
        else:
            return jsonify({'error': f'API error: {response.status_code}'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def fallback_run_python(code, stdin_input=''):
    import subprocess, tempfile, os, time
    fd, path = tempfile.mkstemp(suffix='.py', text=True)
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(code)
        result = subprocess.run(['python', path], capture_output=True, text=True, timeout=5, input=stdin_input)
        output = result.stdout or result.stderr
        return jsonify({'output': output})
    except subprocess.TimeoutExpired:
        return jsonify({'error': 'Execution timed out (5s)'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        for _ in range(3):
            try:
                os.unlink(path)
                break
            except OSError:
                time.sleep(0.1)

# ========================
# ADVANCED SEARCH
# ========================
@app.route('/search')
@login_required
def advanced_search():
    query = request.args.get('q', '').strip()
    search_type = request.args.get('type', 'all')
    course_id = session.get('course_id')
    results = {}
    if not query:
        return render_template('search.html', results=results, query=query)
    if search_type in ('all', 'notes'):
        notes_query = """
            SELECT 'note' as type, n.id, n.title, n.content, c.name as course_name, NULL as extra
            FROM notes n
            JOIN courses c ON n.course_id = c.id
            WHERE n.status = 'published' AND n.course_id = :cid
            AND (n.title LIKE :q OR n.content LIKE :q)
            ORDER BY n.date_posted DESC
        """
        results['notes'] = execute_query(notes_query, {"q": f"%{query}%", "cid": course_id}, fetch_all=True)
    if search_type in ('all', 'discussions'):
        discussions_query = """
            SELECT 'discussion' as type, d.id, d.message as title, n.title as context, c.name as course_name, 
                   u.full_name as author, d.created_at as date
            FROM discussions d
            JOIN notes n ON d.note_id = n.id
            JOIN courses c ON n.course_id = c.id
            JOIN users u ON d.user_id = u.id
            WHERE n.course_id = :cid AND d.message LIKE :q
            ORDER BY d.created_at DESC
        """
        results['discussions'] = execute_query(discussions_query, {"q": f"%{query}%", "cid": course_id}, fetch_all=True)
    if search_type in ('all', 'assignments'):
        assignments_query = """
            SELECT 'assignment' as type, a.id, a.title, a.description, c.name as course_name, a.due_date
            FROM assignments a
            JOIN courses c ON a.course_id = c.id
            WHERE a.course_id = :cid AND (a.title LIKE :q OR a.description LIKE :q)
            ORDER BY a.due_date ASC
        """
        results['assignments'] = execute_query(assignments_query, {"q": f"%{query}%", "cid": course_id}, fetch_all=True)
    return render_template('search.html', results=results, query=query, search_type=search_type)

# ========================
# EXPORT ALL DATA
# ========================
@app.route('/admin/export_all')
@login_required
@admin_required
def export_all_data():
    import json
    from datetime import datetime
    data = {}
    tables = ['courses', 'users', 'notes', 'assignments', 'submissions',
              'quizzes', 'quiz_questions', 'quiz_attempts', 'certificates',
              'discussions', 'messages', 'notifications', 'announcements', 'tags', 'note_tags']
    for table in tables:
        rows = execute_query(f"SELECT * FROM {table}", fetch_all=True)
        for row in rows:
            for key, value in row.items():
                if isinstance(value, datetime):
                    row[key] = value.isoformat()
        data[table] = rows
    json_data = json.dumps(data, indent=2, default=str)
    return send_file(
        io.BytesIO(json_data.encode()),
        mimetype='application/json',
        as_attachment=True,
        download_name=f"awwalu_devs_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )

@app.route('/profile/<username>')
@login_required
def public_profile(username):
    user = execute_query("SELECT * FROM users WHERE username = :u", {"u": username}, fetch_one=True)
    if not user:
        flash('User not found.', 'danger')
        return redirect(url_for('dashboard'))
    course = execute_query("SELECT * FROM courses WHERE id = :cid", {"cid": user['course_id']}, fetch_one=True)
    badges = execute_query("SELECT * FROM badges WHERE user_id = :uid", {"uid": user['id']}, fetch_all=True)
    certificates = execute_query("""
        SELECT c.*, co.name as course_name
        FROM certificates c
        JOIN courses co ON c.course_id = co.id
        WHERE c.student_id = :uid
    """, {"uid": user['id']}, fetch_all=True)
    read_count = execute_query("SELECT COUNT(*) as cnt FROM read_notes WHERE user_id = :uid", {"uid": user['id']}, fetch_one=True)
    total_notes = 0
    if user['course_id']:
        total_notes = execute_query("SELECT COUNT(*) as cnt FROM notes WHERE course_id = :cid AND status = 'published'", {"cid": user['course_id']}, fetch_one=True)['cnt']
    return render_template('public_profile.html',
                           user=user,
                           course=course,
                           badges=badges,
                           certificates=certificates,
                           read_count=read_count['cnt'] if read_count else 0,
                           total_notes=total_notes)

@app.route('/schedule')
@login_required
def schedule():
    course_id = session.get('course_id')
    if not course_id:
        return redirect(url_for('select_course'))
    assignments = execute_query("""
        SELECT * FROM assignments
        WHERE course_id = :cid AND due_date >= date('now')
        AND (cohort IS NULL OR cohort = (SELECT cohort FROM users WHERE id = :uid))
        ORDER BY due_date ASC
    """, {"cid": course_id, "uid": session['user_id']}, fetch_all=True)
    quizzes = execute_query("""
        SELECT * FROM quizzes
        WHERE course_id = :cid
        ORDER BY created_at DESC
    """, {"cid": course_id}, fetch_all=True)
    notes = execute_query("""
        SELECT * FROM notes
        WHERE course_id = :cid AND publish_at > datetime('now') AND status = 'scheduled'
        AND (cohort IS NULL OR cohort = (SELECT cohort FROM users WHERE id = :uid))
        ORDER BY publish_at ASC
    """, {"cid": course_id, "uid": session['user_id']}, fetch_all=True)
    return render_template('schedule.html', assignments=assignments, quizzes=quizzes, notes=notes)

@app.route('/admin/send_digest')
@login_required
@admin_required
def send_digest():
    students = execute_query("""
        SELECT id, full_name, email, username FROM users
        WHERE is_admin = 0 AND email_notifications = 1
    """, fetch_all=True)
    sent = 0
    for s in students:
        read_count = execute_query("SELECT COUNT(*) as cnt FROM read_notes WHERE user_id = :uid", {"uid": s['id']}, fetch_one=True)
        total_notes = execute_query("SELECT COUNT(*) as cnt FROM notes WHERE course_id = (SELECT course_id FROM users WHERE id = :uid) AND status = 'published'", {"uid": s['id']}, fetch_one=True)
        progress = int((read_count['cnt'] / total_notes['cnt']) * 100) if total_notes['cnt'] > 0 else 0
        upcoming = execute_query("""
            SELECT title, due_date FROM assignments
            WHERE course_id = (SELECT course_id FROM users WHERE id = :uid)
            AND due_date >= date('now')
            AND (cohort IS NULL OR cohort = (SELECT cohort FROM users WHERE id = :uid))
            ORDER BY due_date ASC
            LIMIT 3
        """, {"uid": s['id']}, fetch_all=True)
        body = f"Your progress: {progress}%\n\nUpcoming deadlines:\n"
        for u in upcoming:
            body += f"- {u['title']} (due {u['due_date']})\n"
        subject = "Weekly Digest – Awwalu Devs"
        send_email(s['email'] if s['email'] else s['username'], subject, body)
        sent += 1
    flash(f'Digest sent to {sent} students.', 'success')
    return redirect(url_for('admin_panel'))

@app.route('/save_widgets', methods=['POST'])
@login_required
def save_widgets():
    data = request.get_json()
    settings = json.dumps(data)
    execute_query("UPDATE users SET settings = :s WHERE id = :id",
                  {"s": settings, "id": session['user_id']}, commit=True)
    return jsonify({'success': True})

@app.route('/admin/cohorts', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_cohorts():
    if request.method == 'POST':
        student_id = request.form['student_id']
        cohort = request.form['cohort']
        execute_query("UPDATE users SET cohort = :c WHERE id = :id",
                      {"c": cohort if cohort else None, "id": student_id}, commit=True)
        flash('Cohort updated.', 'success')
        return redirect(url_for('admin_cohorts'))
    students = execute_query("""
        SELECT u.id, u.full_name, u.username, u.cohort, c.name as course_name
        FROM users u
        LEFT JOIN courses c ON u.course_id = c.id
        WHERE u.is_admin = 0
        ORDER BY u.full_name
    """, fetch_all=True)
    return render_template('admin_cohorts.html', students=students)

@app.route('/edit_profile', methods=['GET', 'POST'])
@login_required
def edit_profile():
    if session.get('is_admin'):
        flash('Admins, use the admin panel.', 'info')
        return redirect(url_for('admin_panel'))
    
    user = execute_query("SELECT * FROM users WHERE id = :id", {"id": session['user_id']}, fetch_one=True)
    if not user:
        flash('User not found. Please log in again.', 'danger')
        return redirect(url_for('logout'))
    
    if request.method == 'POST':
        # Update profile fields
        email = request.form.get('email', '').strip()
        phone = request.form.get('phone', '').strip()
        dob = request.form.get('dob', '').strip()
        bio = request.form.get('bio', '').strip()
        
        if email:
            execute_query("UPDATE users SET email = :e WHERE id = :id",
                          {"e": email, "id": session['user_id']}, commit=True)
        if phone:
            execute_query("UPDATE users SET phone = :p WHERE id = :id",
                          {"p": phone, "id": session['user_id']}, commit=True)
        if dob:
            execute_query("UPDATE users SET dob = :d WHERE id = :id",
                          {"d": dob, "id": session['user_id']}, commit=True)
        if bio:
            execute_query("UPDATE users SET bio = :b WHERE id = :id",
                          {"b": bio, "id": session['user_id']}, commit=True)
        
        # Email notifications toggle
        email_notifications = 1 if request.form.get('email_notifications') else 0
        execute_query("UPDATE users SET email_notifications = :en WHERE id = :id",
                      {"en": email_notifications, "id": session['user_id']}, commit=True)
        
        # Avatar upload
        if 'avatar' in request.files:
            avatar_file = request.files['avatar']
            if avatar_file and avatar_file.filename != '' and allowed_file(avatar_file.filename):
                ext = avatar_file.filename.rsplit('.', 1)[1].lower()
                avatar_name = f"avatar_{session['user_id']}.{ext}"
                avatar_path = os.path.join(app.config['UPLOAD_FOLDER'], avatar_name)
                avatar_file.save(avatar_path)
                execute_query("UPDATE users SET avatar = :a WHERE id = :id",
                              {"a": f"/uploads/{avatar_name}", "id": session['user_id']}, commit=True)
                flash('Avatar updated!', 'success')
            else:
                flash('Invalid file type. Allowed: png, jpg, jpeg, gif', 'danger')
        
        flash('Profile updated successfully!', 'success')
        return redirect(url_for('profile'))
    
    return render_template('edit_profile.html', user=user)

# ========================
# LOGOUT
# ========================
@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out.', 'info')
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
# init_db.py
import os
import json
from sqlalchemy import create_engine, text
from werkzeug.security import generate_password_hash

DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///portal.db')
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if 'sqlite' in DATABASE_URL else {})

def init_db():
    with engine.connect() as conn:
        print("Dropping existing tables...")
        conn.execute(text("DROP TABLE IF EXISTS note_tags"))
        conn.execute(text("DROP TABLE IF EXISTS tags"))
        conn.execute(text("DROP TABLE IF EXISTS student_notes"))
        conn.execute(text("DROP TABLE IF EXISTS messages"))
        conn.execute(text("DROP TABLE IF EXISTS activity_logs"))
        conn.execute(text("DROP TABLE IF EXISTS badges"))
        conn.execute(text("DROP TABLE IF EXISTS discussions"))
        conn.execute(text("DROP TABLE IF EXISTS note_templates"))
        conn.execute(text("DROP TABLE IF EXISTS notifications"))
        conn.execute(text("DROP TABLE IF EXISTS quiz_attempts"))
        conn.execute(text("DROP TABLE IF EXISTS quiz_questions"))
        conn.execute(text("DROP TABLE IF EXISTS quizzes"))
        conn.execute(text("DROP TABLE IF EXISTS certificates"))
        conn.execute(text("DROP TABLE IF EXISTS read_notes"))
        conn.execute(text("DROP TABLE IF EXISTS submissions"))
        conn.execute(text("DROP TABLE IF EXISTS assignments"))
        conn.execute(text("DROP TABLE IF EXISTS notes"))
        conn.execute(text("DROP TABLE IF EXISTS users"))
        conn.execute(text("DROP TABLE IF EXISTS courses"))
        conn.execute(text("DROP TABLE IF EXISTS announcements"))
        conn.commit()

        # --- 1. Courses ---
        print("Creating courses...")
        conn.execute(text("""
            CREATE TABLE courses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                description TEXT,
                color TEXT DEFAULT '#6c63ff'
            )
        """))

        # --- 2. Users (with new columns: phone, dob, bio) ---
        print("Creating users...")
        conn.execute(text("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                full_name TEXT NOT NULL,
                email TEXT,
                phone TEXT,
                dob TEXT,
                bio TEXT,
                avatar TEXT,
                is_admin INTEGER DEFAULT 0,
                course_id INTEGER,
                reset_token TEXT,
                reset_token_expiry TIMESTAMP,
                email_notifications INTEGER DEFAULT 1,
                cohort TEXT,
                settings TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE SET NULL
            )
        """))

        # --- 3. Notes (NEW: publish_at, sort_order, cohort) ---
        print("Creating notes...")
        conn.execute(text("""
            CREATE TABLE notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                course_id INTEGER NOT NULL,
                status TEXT DEFAULT 'draft',
                publish_at TIMESTAMP,
                sort_order INTEGER DEFAULT 0,
                cohort TEXT,
                date_posted TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE CASCADE
            )
        """))

        # --- 4. Assignments (NEW: publish_at, cohort) ---
        print("Creating assignments...")
        conn.execute(text("""
            CREATE TABLE assignments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                due_date TEXT NOT NULL,
                publish_at TIMESTAMP,
                cohort TEXT,
                course_id INTEGER NOT NULL,
                FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE CASCADE
            )
        """))

        # --- 5. Submissions (NEW: grade, feedback) ---
        print("Creating submissions...")
        conn.execute(text("""
            CREATE TABLE submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                assignment_id INTEGER NOT NULL,
                file_path TEXT,
                grade INTEGER,
                feedback TEXT,
                submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (student_id) REFERENCES users (id) ON DELETE CASCADE,
                FOREIGN KEY (assignment_id) REFERENCES assignments (id) ON DELETE CASCADE,
                UNIQUE(student_id, assignment_id)
            )
        """))

        # --- 6. Read Notes ---
        print("Creating read_notes...")
        conn.execute(text("""
            CREATE TABLE read_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                note_id INTEGER NOT NULL,
                read_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
                FOREIGN KEY (note_id) REFERENCES notes (id) ON DELETE CASCADE,
                UNIQUE(user_id, note_id)
            )
        """))

        # --- 7. Announcements ---
        print("Creating announcements...")
        conn.execute(text("""
            CREATE TABLE announcements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                course_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE CASCADE
            )
        """))

        # --- 8. Quizzes ---
        print("Creating quizzes...")
        conn.execute(text("""
            CREATE TABLE quizzes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT,
                course_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE CASCADE
            )
        """))

        # --- 9. Quiz Questions ---
        print("Creating quiz_questions...")
        conn.execute(text("""
            CREATE TABLE quiz_questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                quiz_id INTEGER NOT NULL,
                question_text TEXT NOT NULL,
                options TEXT NOT NULL,
                correct_answer INTEGER NOT NULL,
                FOREIGN KEY (quiz_id) REFERENCES quizzes (id) ON DELETE CASCADE
            )
        """))

        # --- 10. Quiz Attempts ---
        print("Creating quiz_attempts...")
        conn.execute(text("""
            CREATE TABLE quiz_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                quiz_id INTEGER NOT NULL,
                score INTEGER,
                total_questions INTEGER,
                answers TEXT,
                completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (student_id) REFERENCES users (id) ON DELETE CASCADE,
                FOREIGN KEY (quiz_id) REFERENCES quizzes (id) ON DELETE CASCADE,
                UNIQUE(student_id, quiz_id)
            )
        """))

        # --- 11. Certificates ---
        print("Creating certificates...")
        conn.execute(text("""
            CREATE TABLE certificates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                course_id INTEGER NOT NULL,
                certificate_code TEXT UNIQUE NOT NULL,
                issued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (student_id) REFERENCES users (id) ON DELETE CASCADE,
                FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE CASCADE
            )
        """))

        # --- 12. Notifications ---
        print("Creating notifications...")
        conn.execute(text("""
            CREATE TABLE notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                message TEXT NOT NULL,
                link TEXT,
                is_read INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            )
        """))

        # --- 13. Note Templates ---
        print("Creating note_templates...")
        conn.execute(text("""
            CREATE TABLE note_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))

        # --- 14. Discussions (NEW: upvotes, is_resolved) ---
        print("Creating discussions...")
        conn.execute(text("""
            CREATE TABLE discussions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                note_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                parent_id INTEGER,
                message TEXT NOT NULL,
                upvotes INTEGER DEFAULT 0,
                is_resolved INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (note_id) REFERENCES notes (id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
                FOREIGN KEY (parent_id) REFERENCES discussions (id) ON DELETE CASCADE
            )
        """))

        # --- 15. Messages (Private inbox) ---
        print("Creating messages...")
        conn.execute(text("""
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_id INTEGER NOT NULL,
                receiver_id INTEGER NOT NULL,
                message TEXT NOT NULL,
                is_read INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (sender_id) REFERENCES users (id) ON DELETE CASCADE,
                FOREIGN KEY (receiver_id) REFERENCES users (id) ON DELETE CASCADE
            )
        """))

        # --- 16. Activity Logs ---
        print("Creating activity_logs...")
        conn.execute(text("""
            CREATE TABLE activity_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                details TEXT,
                ip_address TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            )
        """))

        # --- 17. Badges ---
        print("Creating badges...")
        conn.execute(text("""
            CREATE TABLE badges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                course_id INTEGER NOT NULL,
                badge_name TEXT NOT NULL,
                badge_icon TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
                FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE CASCADE,
                UNIQUE(user_id, course_id)
            )
        """))

        # --- 18. Student Private Notes ---
        print("Creating student_notes...")
        conn.execute(text("""
            CREATE TABLE student_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                note_id INTEGER NOT NULL,
                content TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
                FOREIGN KEY (note_id) REFERENCES notes (id) ON DELETE CASCADE,
                UNIQUE(user_id, note_id)
            )
        """))

        # --- 19. Tags ---
        print("Creating tags...")
        conn.execute(text("""
            CREATE TABLE tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                color TEXT DEFAULT '#6c63ff',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))

        # --- 20. Note Tags ---
        print("Creating note_tags...")
        conn.execute(text("""
            CREATE TABLE note_tags (
                note_id INTEGER NOT NULL,
                tag_id INTEGER NOT NULL,
                FOREIGN KEY (note_id) REFERENCES notes (id) ON DELETE CASCADE,
                FOREIGN KEY (tag_id) REFERENCES tags (id) ON DELETE CASCADE,
                PRIMARY KEY (note_id, tag_id)
            )
        """))

        conn.commit()

        # --- Insert Default Courses ---
        print("Inserting default courses...")
        courses = [
            ('Python 101', 'Learn Python from scratch.', '#306998'),
            ('JavaScript', 'Master JS for web dev.', '#f7df1e'),
            ('HTML & CSS', 'Build beautiful websites.', '#e34c26')
        ]
        for name, desc, color in courses:
            conn.execute(text("INSERT INTO courses (name, description, color) VALUES (:name, :desc, :color)"),
                         {"name": name, "desc": desc, "color": color})
        conn.commit()

        # --- Get Course IDs ---
        python_id = conn.execute(text("SELECT id FROM courses WHERE name = 'Python 101'")).fetchone()[0]
        js_id = conn.execute(text("SELECT id FROM courses WHERE name = 'JavaScript'")).fetchone()[0]
        html_id = conn.execute(text("SELECT id FROM courses WHERE name = 'HTML & CSS'")).fetchone()[0]

        # --- Insert Sample Tags ---
        print("Inserting sample tags...")
        sample_tags = [
            ('Python', '#306998'),
            ('JavaScript', '#f7df1e'),
            ('HTML/CSS', '#e34c26'),
            ('Functions', '#ff6b6b'),
            ('Loops', '#4ecdc4'),
            ('Variables', '#45b7d1'),
            ('OOP', '#96ceb4')
        ]
        for name, color in sample_tags:
            conn.execute(text("INSERT INTO tags (name, color) VALUES (:name, :color)"),
                         {"name": name, "color": color})
        conn.commit()

        # --- Insert Pre-Made Notes (with sort_order) ---
        print("Inserting 30 pre-made notes (drafts)...")
        python_notes = [
            ("1. Intro to Python", "```python\nprint('Hello World')\n```\nPython is a high-level, interpreted language."),
            ("2. Variables", "```python\nname = 'Alice'\nage = 25\n```\nStrings, Integers, Floats, Booleans."),
            ("3. String Manipulation", "```python\nname.upper()\nname.lower()\n```\nConcatenation and f-strings."),
            ("4. Lists", "```python\nmy_list = [1, 2, 3]\nmy_list.append(4)\n```\nOrdered collections."),
            ("5. Dictionaries", "```python\nstudent = {'name': 'John', 'age': 20}\n```\nKey-value pairs."),
            ("6. Conditionals", "```python\nif x > 5:\n    print('Big')\nelse:\n    print('Small')\n```"),
            ("7. Loops", "```python\nfor i in range(5):\n    print(i)\n```"),
            ("8. Functions", "```python\ndef greet(name):\n    return f'Hello {name}'\n```"),
            ("9. File I/O", "```python\nwith open('file.txt', 'r') as f:\n    data = f.read()\n```"),
            ("10. Error Handling", "```python\ntry:\n    x = 1 / 0\nexcept ZeroDivisionError:\n    print('Error!')\n```")
        ]
        for idx, (title, content) in enumerate(python_notes):
            conn.execute(text("""
                INSERT INTO notes (title, content, course_id, status, sort_order)
                VALUES (:t, :c, :cid, 'draft', :so)
            """), {"t": title, "c": content, "cid": python_id, "so": idx})

        js_notes = [
            ("1. Intro to JS", "```javascript\nconsole.log('Hello World');\n```\nJavaScript powers the web."),
            ("2. Variables", "```javascript\nlet name = 'Alice';\nconst age = 25;\n```"),
            ("3. Functions", "```javascript\nfunction greet(name) {\n    return `Hello ${name}`;\n}\n```"),
            ("4. Objects", "```javascript\nlet car = {brand: 'Toyota', year: 2020};\n```"),
            ("5. Arrays", "```javascript\nlet arr = [1, 2, 3];\narr.push(4);\n```"),
            ("6. DOM Manipulation", "```javascript\ndocument.getElementById('btn').click();\n```"),
            ("7. Events", "```javascript\nbtn.addEventListener('click', handler);\n```"),
            ("8. Loops", "```javascript\nfor (let i = 0; i < 5; i++) {\n    console.log(i);\n}\n```"),
            ("9. Arrow Functions", "```javascript\nconst greet = (name) => `Hello ${name}`;\n```"),
            ("10. ES6 Features", "```javascript\nconst [a, b] = [1, 2];\n```\nDestructuring, spread, classes.")
        ]
        for idx, (title, content) in enumerate(js_notes):
            conn.execute(text("""
                INSERT INTO notes (title, content, course_id, status, sort_order)
                VALUES (:t, :c, :cid, 'draft', :so)
            """), {"t": title, "c": content, "cid": js_id, "so": idx})

        html_notes = [
            ("1. HTML Structure", "```html\n<!DOCTYPE html>\n<html>\n<head>\n    <title>My Page</title>\n</head>\n<body>\n    <h1>Hello</h1>\n</body>\n</html>\n```"),
            ("2. Text Formatting", "```html\n<h1>Heading</h1>\n<p>Paragraph</p>\n<strong>Bold</strong>\n```"),
            ("3. Links & Images", "```html\n<a href='https://google.com'>Link</a>\n<img src='pic.jpg' alt='Pic'>\n```"),
            ("4. Lists & Tables", "```html\n<ul><li>Item</li></ul>\n<table><tr><td>Data</td></tr></table>\n```"),
            ("5. Forms", "```html\n<form>\n    <input type='text' name='name'>\n    <button>Submit</button>\n</form>\n```"),
            ("6. Intro to CSS", "```css\nbody {\n    background-color: blue;\n}\n```"),
            ("7. The Box Model", "```css\ndiv {\n    margin: 10px;\n    padding: 20px;\n    border: 1px solid black;\n}\n```"),
            ("8. Flexbox", "```css\n.container {\n    display: flex;\n    justify-content: center;\n}\n```"),
            ("9. CSS Grid", "```css\n.grid {\n    display: grid;\n    grid-template-columns: 1fr 1fr;\n}\n```"),
            ("10. Responsive Design", "```css\n@media (max-width: 600px) {\n    body { font-size: 14px; }\n}\n```")
        ]
        for idx, (title, content) in enumerate(html_notes):
            conn.execute(text("""
                INSERT INTO notes (title, content, course_id, status, sort_order)
                VALUES (:t, :c, :cid, 'draft', :so)
            """), {"t": title, "c": content, "cid": html_id, "so": idx})

        conn.commit()

        # --- Users ---
        print("Creating users...")
        admin_pass = generate_password_hash('admin123')
        conn.execute(text("""
            INSERT INTO users (username, password, full_name, email, is_admin, course_id)
            VALUES (:u, :p, :f, :e, :a, NULL)
        """), {"u": "admin", "p": admin_pass, "f": "Head Instructor", "e": "admin@awwaludevs.com", "a": 1})
        student_pass = generate_password_hash('student123')
        conn.execute(text("""
            INSERT INTO users (username, password, full_name, email, is_admin, course_id)
            VALUES (:u, :p, :f, :e, :a, :cid)
        """), {"u": "alice", "p": student_pass, "f": "Alice Student", "e": "alice@example.com", "a": 0, "cid": python_id})
        conn.commit()

        # --- Insert sample note template ---
        print("Inserting sample note template...")
        conn.execute(text("""
            INSERT INTO note_templates (name, title, content)
            VALUES ('Lesson Template', 'Lesson Title', 'Write your lesson content here.\n\n```python\n# Example code\nprint("Hello")\n```')
        """))

        # --- Insert sample announcement ---
        print("Inserting sample announcement...")
        conn.execute(text("""
            INSERT INTO announcements (title, content, course_id)
            VALUES ('Welcome to Awwalu Devs!', 'Welcome all students. This is your learning portal. Stay tuned for updates.', NULL)
        """))

        conn.commit()

        print("\n" + "=" * 70)
        print("✅ DATABASE FULLY UPGRADED WITH ALL TABLES!")
        print("   - Courses, Users, Notes, Assignments, Submissions")
        print("   - Quizzes, Quiz Questions, Quiz Attempts")
        print("   - Certificates, Notifications, Announcements")
        print("   - Read Notes, Discussions (Q&A), Note Templates")
        print("   - Messages, Activity Logs, Badges")
        print("   - Student Private Notes, Tags, Note Tags")
        print("   - User columns: email, phone, dob, bio, avatar, cohort, settings, email_notifications")
        print("   - Notes & Assignments: cohort column")
        print("=" * 70)
        print("👤 Admin: admin | Pass: admin123")
        print("👤 Student: alice | Pass: student123 (enrolled in Python 101)")
        print("📁 Don't forget to create an 'uploads/' folder!")
        print("📧 For password reset, set SMTP env variables.")
        print("🏷️ Sample tags created: Python, JavaScript, HTML/CSS, Functions, Loops, Variables, OOP")
        print("=" * 70)

if __name__ == "__main__":
    init_db()

from flask import Flask, render_template, request, jsonify, session
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from werkzeug.exceptions import HTTPException
from pathlib import Path
from threading import Timer
from io import BytesIO
import sqlite3
import os
import json
import re
import webbrowser

app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / "instance"
INSTANCE_DIR.mkdir(exist_ok=True)
DATABASE = INSTANCE_DIR / "career_path_ai.db"

app.secret_key = os.environ.get(
    "CAREER_SECRET_KEY",
    "career-path-ai-development-key-change-before-deployment"
)

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    MAX_CONTENT_LENGTH=5 * 1024 * 1024
)


# JSON ERROR HANDLING

@app.errorhandler(HTTPException)
def handle_http_error(error):
    if request.path.startswith("/api/"):
        return jsonify({
            "success": False,
            "message": error.description
        }), error.code
    return error


@app.errorhandler(Exception)
def handle_unexpected_error(error):
    app.logger.exception("Unexpected application error")
    if request.path.startswith("/api/"):
        return jsonify({
            "success": False,
            "message": "An unexpected server error occurred. Please try again."
        }), 500
    return "An unexpected server error occurred.", 500


# DATABASE

def get_db_connection():
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def create_database():
    connection = get_db_connection()

    connection.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    """)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS skill_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE NOT NULL,
            answers TEXT NOT NULL,
            results TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    connection.commit()
    connection.close()


create_database()


# HOME

@app.route("/")
def home():
    return render_template("index.html")


# REGISTER

@app.route("/api/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or {}

    name = str(data.get("name", "")).strip()
    email = str(data.get("email", "")).strip().lower()
    password = data.get("password", "")

    if not name or not email or not password:
        return jsonify(success=False, message="Please fill in all fields."), 400

    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return jsonify(success=False, message="Please enter a valid email address."), 400

    if not isinstance(password, str) or len(password) < 6:
        return jsonify(success=False, message="Password must contain at least 6 characters."), 400

    connection = get_db_connection()

    try:
        cursor = connection.execute(
            "INSERT INTO users (name, email, password) VALUES (?, ?, ?)",
            (name, email, generate_password_hash(password))
        )
        connection.commit()
        user_id = cursor.lastrowid

        session.clear()
        session["user_id"] = user_id

        return jsonify(
            success=True,
            message="Account created successfully!",
            user={"id": user_id, "name": name, "email": email}
        )

    except sqlite3.IntegrityError:
        return jsonify(
            success=False,
            message="An account with this email already exists."
        ), 409

    finally:
        connection.close()


# LOGIN

@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}

    email = str(data.get("email", "")).strip().lower()
    password = data.get("password", "")

    if not email or not password:
        return jsonify(
            success=False,
            message="Please enter your email and password."
        ), 400

    connection = get_db_connection()

    try:
        user = connection.execute(
            "SELECT * FROM users WHERE email = ?",
            (email,)
        ).fetchone()
    finally:
        connection.close()

    if user is None or not check_password_hash(user["password"], password):
        return jsonify(
            success=False,
            message="Incorrect email or password."
        ), 401

    session.clear()
    session["user_id"] = user["id"]

    return jsonify(
        success=True,
        message="Login successful!",
        user={
            "id": user["id"],
            "name": user["name"],
            "email": user["email"]
        }
    )


# PROFILE

@app.route("/api/profile", methods=["GET"])
def profile():
    if "user_id" not in session:
        return jsonify(success=False, message="Please log in."), 401

    connection = get_db_connection()

    try:
        user = connection.execute(
            "SELECT id, name, email FROM users WHERE id = ?",
            (session["user_id"],)
        ).fetchone()
    finally:
        connection.close()

    if user is None:
        session.clear()
        return jsonify(success=False, message="User not found."), 401

    return jsonify(success=True, user=dict(user))


# SKILL DISCOVERY

SKILL_MAPPING = {
    "problem_solving": {
        "logic": "Analytical Thinking",
        "puzzles": "Problem Solving",
        "debugging": "Problem Solving",
        "planning": "Strategic Thinking"
    },
    "creativity": {
        "design": "Creative Thinking",
        "ideas": "Creative Thinking",
        "writing": "Communication",
        "building": "Innovation"
    },
    "working_style": {
        "independent": "Self Learning",
        "team": "Teamwork",
        "leadership": "Leadership",
        "organizing": "Organization"
    },
    "interests": {
        "technology": "Technical Thinking",
        "data": "Analytical Thinking",
        "people": "Communication",
        "business": "Business Understanding"
    },
    "learning": {
        "practice": "Practical Learning",
        "reading": "Research",
        "experiments": "Experimentation",
        "discussion": "Communication"
    },
    "activities": {
        "coding": "Programming",
        "presenting": "Communication",
        "analyzing": "Analytical Thinking",
        "creating": "Creative Thinking"
    }
}


def calculate_skill_results(answers):
    scores = {}

    for question, answer in answers.items():
        skill = SKILL_MAPPING[question][answer]
        scores[skill] = scores.get(skill, 0) + 1

    if not scores:
        return []

    maximum = max(scores.values())

    results = [
        {"skill": skill, "score": round(score / maximum * 100)}
        for skill, score in scores.items()
    ]

    results.sort(key=lambda item: item["score"], reverse=True)
    return results


@app.route("/api/skill-discovery", methods=["POST"])
def save_skill_discovery():
    if "user_id" not in session:
        return jsonify(success=False, message="Please log in first."), 401

    data = request.get_json(silent=True) or {}
    answers = data.get("answers")

    if not isinstance(answers, dict):
        return jsonify(success=False, message="Invalid assessment answers."), 400

    if set(answers.keys()) != set(SKILL_MAPPING.keys()):
        return jsonify(success=False, message="Please answer all six questions."), 400

    for question, answer in answers.items():
        if not isinstance(answer, str) or answer not in SKILL_MAPPING[question]:
            return jsonify(
                success=False,
                message="Please select a valid answer for every question."
            ), 400

    results = calculate_skill_results(answers)
    connection = get_db_connection()

    try:
        connection.execute("""
            INSERT INTO skill_profiles (user_id, answers, results)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id)
            DO UPDATE SET
                answers = excluded.answers,
                results = excluded.results,
                created_at = CURRENT_TIMESTAMP
        """, (
            session["user_id"],
            json.dumps(answers),
            json.dumps(results)
        ))
        connection.commit()
    finally:
        connection.close()

    return jsonify(
        success=True,
        message="Your skill discovery results are ready!",
        results=results
    )


@app.route("/api/skill-discovery", methods=["GET"])
def get_skill_discovery():
    if "user_id" not in session:
        return jsonify(success=False, message="Please log in first."), 401

    connection = get_db_connection()

    try:
        profile = connection.execute(
            "SELECT results FROM skill_profiles WHERE user_id = ?",
            (session["user_id"],)
        ).fetchone()
    finally:
        connection.close()

    if profile is None:
        return jsonify(success=True, completed=False, results=[])

    return jsonify(
        success=True,
        completed=True,
        results=json.loads(profile["results"])
    )


# RESUME ANALYSIS

TECHNICAL_SKILLS = [
    "python", "java", "c++", "c", "sql", "html", "css",
    "javascript", "react", "flask", "django", "machine learning",
    "deep learning", "pandas", "numpy", "excel", "power bi",
    "tableau", "mongodb", "mysql", "git", "github",
    "data analysis", "data structures", "algorithms",
    "scikit-learn", "tensorflow", "pytorch", "aws", "docker"
]

SOFT_SKILLS = [
    "communication", "leadership", "teamwork",
    "problem solving", "critical thinking",
    "time management", "adaptability",
    "creativity", "collaboration", "team player"
]

ROLE_SKILLS = {
    "Software Developer": [
        "Data Structures", "Object-Oriented Programming",
        "Git", "SQL", "Problem Solving"
    ],
    "Data Analyst": [
        "Python", "SQL", "Statistics", "Excel", "Power BI"
    ],
    "Machine Learning Engineer": [
        "Python", "Statistics", "Machine Learning",
        "NumPy", "Pandas"
    ],
    "UI/UX Designer": [
        "Figma", "Wireframing", "Prototyping", "User Research"
    ],
    "Business Analyst": [
        "Excel", "SQL", "Communication", "Data Visualisation"
    ]
}


def analyse_resume_text(content, filename, target_role):
    text = content.lower()
    word_count = len(content.split())

    technical_found = [
        skill for skill in TECHNICAL_SKILLS
        if re.search(
            r"(?<![a-z0-9])" + re.escape(skill) + r"(?![a-z0-9])",
            text
        )
    ]

    soft_found = [skill for skill in SOFT_SKILLS if skill in text]

    sections = {
        "Contact Information": bool(
            re.search(r"\b[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}\b", content)
        ),
        "Education": any(
            term in text for term in
            ["education", "university", "college", "b.tech", "bachelor", "degree"]
        ),
        "Technical Skills": any(
            term in text for term in ["skills", "technologies", "technical"]
        ),
        "Projects": "project" in text,
        "Experience": any(
            term in text for term in ["experience", "internship", "employment"]
        ),
        "Certifications": any(
            term in text for term in ["certification", "certificate", "courses"]
        )
    }

    section_points = sum(sections.values()) / len(sections) * 45
    skill_points = min(len(technical_found) * 2.5 + len(soft_found) * 2, 30)
    length_points = 15 if word_count >= 250 else 10 if word_count >= 120 else 5
    detail_points = 10 if any(
        term in text for term in
        ["achieved", "developed", "created", "implemented", "%"]
    ) else 4

    score = min(100, round(
        section_points + skill_points + length_points + detail_points
    ))

    strengths = []
    improvements = []

    if technical_found:
        strengths.append(
            f"Your resume highlights {len(technical_found)} technical skills, "
            f"including {', '.join(technical_found[:5])}."
        )
    else:
        improvements.append(
            "Add a technical skills section with programming languages, tools and technologies you genuinely know."
        )

    if soft_found:
        strengths.append(
            "You have included professional skills such as "
            + ", ".join(soft_found[:4]) + "."
        )
    else:
        improvements.append(
            "Include relevant soft skills and demonstrate them through real examples."
        )

    if sections["Projects"]:
        strengths.append("Your resume includes a projects section.")
    else:
        improvements.append(
            "Add two or three academic or personal projects. Explain your role, tools used and outcome."
        )

    if sections["Education"]:
        strengths.append("Your educational background is included.")
    else:
        improvements.append(
            "Add your degree, institution, graduation year and relevant coursework."
        )

    if sections["Experience"]:
        strengths.append("Your resume mentions experience or internships.")
    else:
        improvements.append(
            "If you are a fresher, include internships, hackathons, volunteering or practical project experience."
        )

    if sections["Certifications"]:
        strengths.append("You have included certifications or courses.")
    else:
        improvements.append(
            "Include relevant certifications or completed courses if you have them."
        )

    if not sections["Contact Information"]:
        improvements.append(
            "Add a professional email address and appropriate contact details."
        )

    if word_count < 120:
        improvements.append(
            "Your resume appears short. Add meaningful details about your projects, education and achievements."
        )

    if not any(
        term in text for term in
        ["achieved", "developed", "created", "implemented"]
    ):
        improvements.append(
            "Use action words such as developed, designed, implemented and analysed to explain your contributions."
        )

    role_skills = ROLE_SKILLS.get(
        target_role, ROLE_SKILLS["Software Developer"]
    )
    recommended = [skill for skill in role_skills if skill.lower() not in text]

    if score >= 80:
        rating = "Strong Foundation"
    elif score >= 60:
        rating = "Good Progress"
    elif score >= 40:
        rating = "Needs Improvement"
    else:
        rating = "Beginner Resume"

    return {
        "success": True,
        "filename": filename,
        "score": score,
        "rating": rating,
        "word_count": word_count,
        "technical_skills": technical_found,
        "soft_skills": soft_found,
        "sections": sections,
        "strengths": strengths,
        "improvements": improvements,
        "recommended_skills": recommended,
        "target_role": target_role
    }


@app.route("/api/analyse-resume", methods=["POST"])
def analyse_resume():
    if "user_id" not in session:
        return jsonify(success=False, message="Your session has expired. Please log in again."), 401

    uploaded_file = request.files.get("resume")
    target_role = request.form.get("target_role", "Software Developer")

    if target_role not in ROLE_SKILLS:
        target_role = "Software Developer"

    if not uploaded_file or not uploaded_file.filename:
        return jsonify(success=False, message="Please select a resume."), 400

    filename = secure_filename(uploaded_file.filename)
    extension = Path(filename).suffix.lower()

    if extension not in [".pdf", ".txt"]:
        return jsonify(success=False, message="Only PDF and TXT files are supported."), 400

    file_bytes = uploaded_file.read()

    if not file_bytes:
        return jsonify(success=False, message="The selected file is empty."), 400

    if extension == ".txt":
        content = file_bytes.decode("utf-8-sig", errors="ignore")
    else:
        try:
            from pypdf import PdfReader
            reader = PdfReader(BytesIO(file_bytes))

            if reader.is_encrypted:
                return jsonify(
                    success=False,
                    message="This PDF is password-protected. Please upload an unlocked PDF."
                ), 400

            content = "\n".join(
                page.extract_text() or "" for page in reader.pages
            )

        except ImportError:
            return jsonify(
                success=False,
                message="PDF support is missing. In your terminal run: py -m pip install pypdf"
            ), 500
        except Exception:
            app.logger.exception("Could not read uploaded PDF")
            return jsonify(
                success=False,
                message="Could not read this PDF. Try another text-based PDF or upload a TXT file."
            ), 400

    if not content.strip():
        return jsonify(
            success=False,
            message="No readable text found. Please use a text-based PDF or TXT resume."
        ), 400

    try:
        result = analyse_resume_text(content, filename, target_role)
        return jsonify(result)
    except Exception:
        app.logger.exception("Resume analysis failed")
        return jsonify(
            success=False,
            message="Could not analyse this resume. Please try another file."
        ), 500


# LOGOUT

@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify(success=True, message="Logged out successfully.")


# HEALTH CHECK

@app.route("/api/health")
def health():
    return jsonify(
        success=True,
        message="Career Path AI backend is running."
    )


# ROUTE CHECK

@app.route("/api/routes")
def route_check():
    routes = [
        {
            "route": str(rule),
            "methods": sorted(list(rule.methods))
        }
        for rule in app.url_map.iter_rules()
    ]
    return jsonify(success=True, routes=routes)


# START APPLICATION AND OPEN BROWSER AUTOMATICALLY

if __name__ == "__main__":
    Timer(
        1.5,
        lambda: webbrowser.open("http://127.0.0.1:5000/")
    ).start()

    app.run(
        host="127.0.0.1",
        port=5000,
        debug=False,
        use_reloader=False
    )

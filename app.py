import os
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

app = Flask(__name__, static_folder='static', template_folder='templates')
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-change-me')

# --- DB SQLite (fichier) ---
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(BASE_DIR, 'wp_challenge.sqlite3')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    nationality = db.Column(db.String(100))
    is_admin = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)



BASE_DIR = os.path.abspath(os.path.dirname(__file__))

app = Flask(__name__, static_folder='static', template_folder='templates')
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-change-me')

# --- DB config: SQLite locale / fichier dans le conteneur Render ---
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(BASE_DIR, 'wp_challenge.sqlite3')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- Modèles ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    nationality = db.Column(db.String(100))
    is_admin = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

ADMIN_EMAILS = {'renaud.debry@ecf-cerca.fr', 'westpistards@gmail.com'}

def current_user():
    uid = session.get('user_id')
    return User.query.get(uid) if uid else None

# --- Routes publiques ---
@app.get("/")
def index():
    # pas encore de Round ici : on passe des listes vides
    return render_template("index.html", user=current_user(), open_rounds=[])

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        nat = (request.form.get("nationality") or "").strip()
        if not email:
            flash("Email obligatoire", "error")
            return redirect(url_for("register"))
        user = User.query.filter_by(email=email).first()
        if user:
            flash("Ce mail est déjà inscrit. Connecte-toi.", "info")
            return redirect(url_for("login"))
        user = User(email=email, nationality=nat, is_admin=(email in ADMIN_EMAILS))
        db.session.add(user); db.session.commit()
        session["user_id"] = user.id
        flash("Inscription réussie !", "success")
        return redirect(url_for("index"))
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        user = User.query.filter_by(email=email).first()
        if not user:
            flash("Aucun compte pour cet email. Inscris-toi.", "error")
            return redirect(url_for("register"))
        session["user_id"] = user.id
        flash("Connecté.", "success")
        return redirect(url_for("index"))
    return render_template("login.html")

@app.get("/logout")
def logout():
    session.pop("user_id", None)
    flash("Déconnecté.", "info")
    return redirect(url_for("index"))

@app.get("/rounds")
def rounds_list():
    # placeholder pour l’instant
    return render_template("rounds.html", rounds=[], user=current_user())

@app.get("/submit")
def submit_time():
    # placeholder pour l’instant
    return render_template("submit_time.html", rounds=[])

@app.get("/profile")
def profile():
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    # pas encore de chronos => on affiche juste le profil
    return render_template("profile.html", user=user, laps=[], ms_to_string=lambda x: x)

from flask import request, abort

@app.get("/__init_db")
def __init_db():
    token = request.args.get("token")
    if token != app.config.get("SECRET_KEY"):
        abort(403)
    with app.app_context():
        db.create_all()
    return "DB OK"

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(host="0.0.0.0", port=5000, debug=True)


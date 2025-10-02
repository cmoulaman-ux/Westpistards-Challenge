import os
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, flash, render_template_string
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
    return """
<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>WP Challenge</title>
  <style>
    :root{ --bg:#f9fafb; --card:#ffffff; --text:#111827; --muted:#6b7280; --primary:#2563eb; --border:#e5e7eb; }
    *{ box-sizing:border-box; } body{ margin:0; font-family:system-ui,-apple-system,Segoe UI,Roboto,Ubuntu; background:var(--bg); color:var(--text); }
    .container{ max-width:980px; margin:0 auto; padding:16px; }
    .nav{ display:flex; justify-content:space-between; align-items:center; }
    .brand{ font-weight:700; text-decoration:none; color:var(--text); }
    nav a{ margin-left:12px; text-decoration:none; color:var(--text); }
    .grid2{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }
    .card{ background:var(--card); border:1px solid var(--border); border-radius:12px; padding:16px; }
    .btn{ background:var(--primary); color:white; border:none; padding:10px 14px; border-radius:8px; cursor:pointer; text-decoration:none; display:inline-block; }
    .btn.outline{ background:white; color:var(--primary); border:1px solid var(--primary); }
    .muted{ color:var(--muted); }
  </style>
</head>
<body>
  <header class="container">
    <div class="nav">
      <div><a class="brand" href="/">WP Challenge</a></div>
      <nav>
        <a href="/rounds">Manches</a>
        <a href="/register">Inscription</a>
        <a href="/login">Connexion</a>
      </nav>
    </div>
  </header>
  <main class="container">
    <h1>Bienvenue sur WP Challenge</h1>
    <p>Entre tes chronos, partage ton lien YouTube et grimpe au classement !</p>
    <section class="card">
      <h2>Manches ouvertes</h2>
      <p class="muted">Aucune manche ouverte pour le moment.</p>
    </section>
    <section class="grid2" style="margin-top:16px">
      <div class="card">
        <h3>Inscription</h3>
        <p><a class="btn" href="/register">Créer mon compte</a></p>
      </div>
      <div class="card">
        <h3>Connexion</h3>
        <p><a class="btn outline" href="/login">Me connecter</a></p>
      </div>
    </section>
  </main>
  <footer class="container muted">© 2025 westpistards</footer>
</body>
</html>
    """

# --- Mise en page inline réutilisable pour toutes les pages ---
def PAGE(inner_html: str) -> str:
    return f"""
<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>WP Challenge</title>
  <style>
    :root{{ --bg:#f9fafb; --card:#ffffff; --text:#111827; --muted:#6b7280; --primary:#2563eb; --border:#e5e7eb; }}
    *{{ box-sizing:border-box; }} body{{ margin:0; font-family:system-ui,-apple-system,Segoe UI,Roboto,Ubuntu; background:var(--bg); color:var(--text); }}
    .container{{ max-width:980px; margin:0 auto; padding:16px; }}
    .nav{{ display:flex; justify-content:space-between; align-items:center; }}
    .brand{{ font-weight:700; text-decoration:none; color:var(--text); }}
    nav a{{ margin-left:12px; text-decoration:none; color:var(--text); }}
    .card{{ background:var(--card); border:1px solid var(--border); border-radius:12px; padding:16px; }}
    .form{{ display:grid; gap:12px; max-width:460px; background:var(--card); border:1px solid var(--border); border-radius:12px; padding:16px; }}
    .form input{{ width:100%; padding:10px; border:1px solid var(--border); border-radius:8px; }}
    .btn{{ background:var(--primary); color:white; border:none; padding:10px 14px; border-radius:8px; cursor:pointer; text-decoration:none; display:inline-block; }}
    .btn.outline{{ background:white; color:var(--primary); border:1px solid var(--primary); }}
    .muted{{ color:var(--muted); }}
  </style>
</head>
<body>
  <header class="container">
    <div class="nav">
      <div><a class="brand" href="/">WP Challenge</a></div>
      <nav>
        <a href="/rounds">Manches</a>
        <a href="/register">Inscription</a>
        <a href="/login">Connexion</a>
      </nav>
    </div>
  </header>
  <main class="container">
    {inner_html}
  </main>
  <footer class="container muted">© 2025 westpistards</footer>
</body>
</html>
"""

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        nat = (request.form.get("nationality") or "").strip()
        if not email:
            return render_template_string(PAGE("""
                <h1>Inscription</h1>
                <p class="muted">Email obligatoire.</p>
                <form method="post" class="form">
                  <label>Email
                    <input type="email" name="email" required>
                  </label>
                  <label>Nationalité
                    <input type="text" name="nationality" placeholder="FR, BE, ...">
                  </label>
                  <button class="btn" type="submit">S'inscrire</button>
                </form>
            """)), 400
        user = User.query.filter_by(email=email).first()
        if user:
            # déjà inscrit -> redirige vers login
            return redirect(url_for("login"))
        is_admin = email in {"renaud.debry@ecf-cerca.fr","westpistards@gmail.com"}
        user = User(email=email, nationality=nat, is_admin=is_admin)
        db.session.add(user); db.session.commit()
        session["user_id"] = user.id
        return redirect(url_for("index"))

    # GET -> page stylée
    return render_template_string(PAGE("""
        <h1>Inscription</h1>
        <form method="post" class="form">
          <label>Email
            <input type="email" name="email" required>
          </label>
          <label>Nationalité
            <input type="text" name="nationality" placeholder="FR, BE, ...">
          </label>
          <button class="btn" type="submit">S'inscrire</button>
        </form>
        <p class="muted" style="margin-top:12px;">Déjà inscrit ? <a href="/login">Connexion</a></p>
    """))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        user = User.query.filter_by(email=email).first()
        if not user:
            # pas de compte -> renvoi vers inscription
            return redirect(url_for("register"))
        session["user_id"] = user.id
        return redirect(url_for("index"))

    # GET -> page stylée
    return render_template_string(PAGE("""
        <h1>Connexion (sans mot de passe)</h1>
        <form method="post" class="form">
          <label>Email
            <input type="email" name="email" required>
          </label>
          <button class="btn" type="submit">Se connecter</button>
        </form>
        <p class="muted" style="margin-top:12px;">Pas encore de compte ? <a href="/register">Inscription</a></p>
    """))


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


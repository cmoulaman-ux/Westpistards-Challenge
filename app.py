import os
from datetime import datetime
from flask import Flask, request, redirect, url_for, session, render_template_string

# --- App ---
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'please-change-me')

# --- DB (SQLite) ---
try:
    from flask_sqlalchemy import SQLAlchemy
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(BASE_DIR, 'wp_challenge.sqlite3')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    db = SQLAlchemy(app)
except Exception:
    db = None

# --- Modèle minimal ---
if db:
    class User(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        email = db.Column(db.String(255), unique=True, nullable=False)
        nationality = db.Column(db.String(100))
        is_admin = db.Column(db.Boolean, default=False)
        created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    class Round(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        name = db.Column(db.String(200), nullable=False)
        status = db.Column(db.String(20), default='open')  # open | closed
        created_at = db.Column(db.DateTime, default=datetime.utcnow)


ADMIN_EMAILS = {'renaud.debry@ecf-cerca.fr', 'westpistards@gmail.com'}
# --- Helpers utilisateur ---
def current_user():
    if not db:
        return None
    uid = session.get("user_id")
    return db.session.get(User, uid) if uid else None

def is_admin(user):
    return bool(user and user.is_admin)


# --- Layout inline réutilisable ---
def PAGE(inner_html: str) -> str:
    return f"""
<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
  <title>WP Challenge</title>
  <style>
    :root{{ --bg:#f9fafb; --card:#ffffff; --text:#111827; --muted:#6b7280; --primary:#2563eb; --danger:#dc2626; --border:#e5e7eb; }}
    *{{ box-sizing:border-box; }} body{{ margin:0; font-family:system-ui,-apple-system,Segoe UI,Roboto,Ubuntu; background:var(--bg); color:var(--text); }}
    .container{{ max-width:980px; margin:0 auto; padding:16px; }}
    .nav{{ display:flex; justify-content:space-between; align-items:center; }}
    .brand{{ font-weight:700; text-decoration:none; color:var(--text); }}
    nav a{{ margin-left:12px; text-decoration:none; color:var(--text); }}
    .grid2{{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
    .card{{ background:var(--card); border:1px solid var(--border); border-radius:12px; padding:16px; }}
    .form{{ display:grid; gap:12px; max-width:460px; }}
    .form input, .form select, .form textarea{{ width:100%; padding:10px; border:1px solid var(--border); border-radius:8px; }}
    .btn{{ background:var(--primary); color:white; border:none; padding:10px 14px; border-radius:8px; cursor:pointer; text-decoration:none; display:inline-block; }}
    .btn.outline{{ background:white; color:var(--primary); border:1px solid var(--primary); }}
    .btn.danger{{ background:var(--danger); }}
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
        <a href="/logout">Déconnexion</a>
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

# --- Pages ---
@app.get("/")
def index():
    return PAGE("""
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
    """)

@app.route("/register", methods=["GET", "POST"])
def register():
    if not db:
        return PAGE("<h1>Erreur</h1><p class='muted'>DB non initialisée.</p>")
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        nat = (request.form.get("nationality") or "").strip()
        if not email:
            return PAGE("<h1>Inscription</h1><p class='muted'>Email obligatoire.</p>")
        u = User.query.filter_by(email=email).first()
        if u:
            return redirect(url_for("login"))
        is_admin = email in ADMIN_EMAILS
        u = User(email=email, nationality=nat, is_admin=is_admin)
        db.session.add(u); db.session.commit()
        session["user_id"] = u.id
        return redirect(url_for("index"))
    return PAGE("""
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
    """)

@app.route("/login", methods=["GET", "POST"])
def login():
    if not db:
        return PAGE("<h1>Erreur</h1><p class='muted'>DB non initialisée.</p>")
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        u = User.query.filter_by(email=email).first()
        if not u:
            return redirect(url_for("register"))
        session["user_id"] = u.id
        return redirect(url_for("index"))
    return PAGE("""
      <h1>Connexion (sans mot de passe)</h1>
      <form method="post" class="form">
        <label>Email
          <input type="email" name="email" required>
        </label>
        <button class="btn" type="submit">Se connecter</button>
      </form>
      <p class="muted" style="margin-top:12px;">Pas encore de compte ? <a href="/register">Inscription</a></p>
    """)

@app.get("/logout")
def logout():
    session.pop("user_id", None)
    return redirect(url_for("index"))

@app.get("/rounds")
def rounds_list():
    if not db:
        return PAGE("<h1>Manches</h1><p class='muted'>DB non dispo.</p>")
    rounds = Round.query.order_by(Round.created_at.desc()).all()
    if not rounds:
        html = "<h1>Manches</h1><p class='muted'>Aucune manche pour l’instant.</p>"
    else:
        items = "".join(
            f"<li class='card'><strong>{r.name}</strong> — "
            f"<span class='muted'>{'ouverte' if r.status=='open' else 'clôturée'}</span></li>"
            for r in rounds
        )
        html = f"<h1>Manches</h1><ul class='cards'>{items}</ul>"
    # petit lien admin si connecté admin
    u = current_user()
    if is_admin(u):
        html += "<p style='margin-top:12px'><a class='btn' href='/admin/rounds'>Admin : créer une manche</a></p>"
    return PAGE(html)
@app.route("/admin/rounds", methods=["GET", "POST"])
def admin_rounds():
    if not db:
        return PAGE("<h1>Admin</h1><p class='muted'>DB non dispo.</p>")
    u = current_user()
    if not is_admin(u):
        return PAGE("<h1>Accès refusé</h1><p class='muted'>Réservé aux administrateurs.</p>"), 403

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        if not name:
            return PAGE("<h1>Admin — Manches</h1><p class='muted'>Nom obligatoire.</p>"), 400
        r = Round(name=name, status="open")
        db.session.add(r); db.session.commit()
        return redirect(url_for("admin_rounds"))

    rounds = Round.query.order_by(Round.created_at.desc()).all()
    items = "".join(
        f"<li class='card'><strong>{r.name}</strong> — <span class='muted'>{'ouverte' if r.status=='open' else 'clôturée'}</span></li>"
        for r in rounds
    ) or "<p class='muted'>Aucune manche pour l’instant.</p>"

    return PAGE(f"""
      <h1>Admin — Manches</h1>
      <form method="post" class="form">
        <label>Nom de la manche
          <input type="text" name="name" placeholder="Ex: Manche 1 — Circuit X" required>
        </label>
        <button class="btn" type="submit">Créer la manche</button>
      </form>
      <h2 style="margin-top:16px;">Liste</h2>
      <ul class="cards">{items}</ul>
    """)


# --- Init DB temporaire (si besoin) ---
@app.get("/__init_db")
def __init_db():
    if not db:
        return "DB non dispo", 500
    from flask import request, abort
    token = request.args.get("token")
    if token != app.config.get("SECRET_KEY"):
        abort(403)
    db.create_all()
    return "DB OK"

# --- Run local ---
if __name__ == "__main__":
    if db:
        with app.app_context():
            db.create_all()
    app.run(host="0.0.0.0", port=5000, debug=True)

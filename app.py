import os
from datetime import datetime
from flask import Flask, request, redirect, url_for, session, render_template_string, Response
from flask_sqlalchemy import SQLAlchemy

# --- App ---
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
app = Flask(__name__)

# --- Secrets & DB config ---
# SECRET_KEY vient des variables d'environnement Render (fallback seulement pour dev local)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-only-change-me")

# DATABASE_URL (Render → Environment)
DATABASE_URL = os.getenv("DATABASE_URL", "")

# Driver SQLAlchemy pour Psycopg 3
# Render donne souvent "postgres://..."; on veut "postgresql+psycopg://..."
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

# Forcer SSL sur Render si manquant
if DATABASE_URL and "sslmode=" not in DATABASE_URL:
    sep = "&" if "?" in DATABASE_URL else "?"
    DATABASE_URL = f"{DATABASE_URL}{sep}sslmode=require"

# Utiliser Postgres si dispo, sinon SQLite local pour le dev
app.config["SQLALCHEMY_DATABASE_URI"] = (
    DATABASE_URL or f"sqlite:///{os.path.join(BASE_DIR, 'wp_challenge.sqlite3')}"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# --- DB ---
db = SQLAlchemy(app)

# (optionnel) créer les tables si absentes; n'efface rien si elles existent
with app.app_context():
    try:
        db.create_all()
    except Exception as e:
        app.logger.error(f"DB init error: {e}")





# --- Modèle minimal ---
if db:
    class User(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        email = db.Column(db.String(255), unique=True, nullable=False)
        nationality = db.Column(db.String(100))
        is_admin = db.Column(db.Boolean, default=False)
        created_at = db.Column(db.DateTime, default=datetime.utcnow)
        pseudo = db.Column(db.String(80))

    class Round(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        name = db.Column(db.String(200), nullable=False)
        status = db.Column(db.String(20), default='open')  # open | closed
        created_at = db.Column(db.DateTime, default=datetime.utcnow)
        plan_data = db.Column(db.LargeBinary)      # contenu binaire (image/PDF)
        plan_mime = db.Column(db.String(120))      # ex: image/png, application/pdf
        plan_name = db.Column(db.String(255))      # nom de fichier d'origine


    class TimeEntry(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
        round_id = db.Column(db.Integer, db.ForeignKey('round.id'), nullable=False)

        # temps brut en millisecondes (on convertira le format saisi ensuite)
        raw_time_ms = db.Column(db.Integer, nullable=False)

        # 1 pénalité = +1000 ms
        penalties = db.Column(db.Integer, default=0)

        bike = db.Column(db.String(120))
        youtube_link = db.Column(db.String(500))
        note = db.Column(db.Text)

        status = db.Column(db.String(20), default='pending')  # pending | approved | rejected
        created_at = db.Column(db.DateTime, default=datetime.utcnow)

        # relations pratiques
        user = db.relationship('User', backref='time_entries', lazy=True)
        round = db.relationship('Round', backref='time_entries', lazy=True)




ADMIN_EMAILS = {'renaud.debry@ecf-cerca.fr', 'westpistards@gmail.com'}

# --- Helpers utilisateur ---
def current_user():
    if not db:
        return None
    uid = session.get("user_id")
    return db.session.get(User, uid) if uid else None

def is_admin(user):
    return bool(user and user.is_admin)

def display_name(user):
    return (user.pseudo or user.email) if user else "—"


# --- Helpers temps (parse/format) ---
def parse_time_to_ms(s: str) -> int:
    """
    Accepte :
      - "m:s.ms"   ex: "1:23.456"
      - "mm:ss"    ex: "01:23"
      - "ss.ms"    ex: "83.456"
      - "ss"       ex: "83"  (secondes entières)
    Retourne le temps en millisecondes (int). Lève ValueError si invalide.
    """
    s = (s or "").strip()
    if not s:
        raise ValueError("Temps vide")
    if ":" in s:
        # formats avec minutes:secondes(.ms)
        parts = s.split(":")
        if len(parts) != 2:
            raise ValueError("Format temps invalide (attendu mm:ss[.ms])")
        m_str, rest = parts
        if "." in rest:
            sec_str, ms_str = rest.split(".", 1)
            ms_str = (ms_str + "000")[:3]  # normalise milli en 3 chiffres
            minutes = int(m_str)
            seconds = int(sec_str)
            millis = int(ms_str)
        else:
            minutes = int(m_str)
            seconds = int(rest)
            millis = 0
        if seconds >= 60 or minutes < 0 or seconds < 0:
            raise ValueError("Minutes/secondes invalides")
        return minutes * 60000 + seconds * 1000 + millis
    else:
        # pas de ":" → soit "ss.ms" soit "ss"
        if "." in s:
            sec_str, ms_str = s.split(".", 1)
            ms_str = (ms_str + "000")[:3]
            seconds = float(f"{sec_str}.{ms_str}")
            millis = int(round(float(seconds) * 1000))
            return millis
        # entier en secondes
        seconds = int(s)
        if seconds < 0:
            raise ValueError("Temps négatif invalide")
        return seconds * 1000

def ms_to_str(ms: int) -> str:
    ms = int(ms)
    m = ms // 60000
    s = (ms % 60000) // 1000
    milli = ms % 1000
    return f"{m}:{s:02d}.{milli:03d}"

def final_time_ms(raw_ms: int, penalties: int) -> int:
    return int(raw_ms) + max(0, int(penalties or 0)) * 1000


# --- Layout inline réutilisable ---
def PAGE(inner_html: str) -> str:
    # Nav ultra-safe (aucune 500 si la DB / session pose problème)
    try:
        u = current_user()
        isadm = is_admin(u)
    except Exception:
        u = None
        isadm = False

    nav_right = '<a href="/rounds">Manches</a>'
    if u:
        nav_right += ' <a href="/profile">Mon profil</a>'
        if isadm:
            nav_right += ' <a href="/admin/rounds">Admin Manches</a>'
            nav_right += ' <a href="/admin/times">Admin Chronos</a>'
        nav_right += ' <a href="/logout">Déconnexion</a>'
    else:
        nav_right += ' <a href="/register">Inscription</a>'
        nav_right += ' <a href="/login">Connexion</a>'

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
    .nav{{ display:flex; justify-content:space-between; align-items:center; gap:12px; flex-wrap:wrap; }}
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
    .row{{ display:flex; gap:8px; align-items:center; }}
    .cards{{ list-style:none; padding:0; display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
    .table{{ width:100%; border-collapse:collapse; background:var(--card); border:1px solid var(--border); }}
    .table th, .table td{{ padding:10px; border-bottom:1px solid var(--border); }}
    .table th{{ text-align:left; background:#f3f4f6; }}
  </style>
</head>
<body>
  <header class="container">
    <div class="nav">
      <div><a class="brand" href="/">WP Challenge</a></div>
      <nav>{nav_right}</nav>
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
        return PAGE("<h1>Inscription</h1><p class='muted'>DB non dispo.</p>"), 500

    if request.method == "POST":
        try:
            email = (request.form.get("email") or "").strip().lower()
            nat = (request.form.get("nationality") or "").strip()
            pseudo = (request.form.get("pseudo") or "").strip()

            if not email:
                return PAGE("<h1>Inscription</h1><p class='muted'>Email obligatoire.</p>"), 400
            if not pseudo:
                return PAGE("<h1>Inscription</h1><p class='muted'>Pseudo obligatoire.</p>"), 400

            u = User.query.filter_by(email=email).first()
            if u:
                return redirect(url_for("login"))

            is_admin = email in ADMIN_EMAILS
            u = User(email=email, nationality=nat, is_admin=is_admin, pseudo=pseudo)
            db.session.add(u)
            db.session.commit()
            session["user_id"] = u.id
            return redirect(url_for("profile"))
        except Exception as e:
            # Message explicite au lieu d'une 500
            return PAGE(f"<h1>Inscription</h1><p class='muted'>Erreur DB : {e}</p><p>Essaie de (re)créer les tables : <code>/__init_db?token=please-change-me</code></p>"), 500

    return PAGE("""
      <h1>Inscription</h1>
      <form method="post" class="form">
        <label>Email
          <input type="email" name="email" required>
        </label>
        <label>Nationalité
          <input type="text" name="nationality" placeholder="FR, BE, ...">
        </label>
        <label>Pseudo (affiché au classement)
          <input type="text" name="pseudo" placeholder="Ton pseudo (obligatoire)" required>
        </label>
        <button class="btn" type="submit">S'inscrire</button>
      </form>
      <p class="muted" style="margin-top:12px;">Déjà inscrit ? <a href="/login">Connexion</a></p>
    """)




@app.route("/login", methods=["GET", "POST"])
def login():
    if not db:
        return PAGE("<h1>Connexion</h1><p class='muted'>DB non dispo.</p>"), 500

    if request.method == "POST":
        try:
            email = (request.form.get("email") or "").strip().lower()
            if not email:
                return PAGE("<h1>Connexion</h1><p class='muted'>Email obligatoire.</p>"), 400

            u = User.query.filter_by(email=email).first()
            if not u:
                # pas de compte -> renvoi vers inscription
                return redirect(url_for("register"))

            session["user_id"] = u.id
            return redirect(url_for("profile"))
        except Exception as e:
            return PAGE(f"<h1>Connexion</h1><p class='muted'>Erreur DB : {e}</p><p>Essaie de (re)créer les tables : <code>/__init_db?token=please-change-me</code></p>"), 500

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
            f"<li class='card'><a href='/rounds/{r.id}'><strong>{r.name}</strong></a> — "
            f"<span class='muted'>{'ouverte' if r.status=='open' else 'clôturée'}</span></li>"
            for r in rounds
        )
        html = f"<h1>Manches</h1><ul class='cards'>{items}</ul>"
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
        db.session.add(r)
        db.session.commit()
        return redirect(url_for("admin_rounds"))

    rounds = Round.query.order_by(Round.created_at.desc()).all()

    def row_html(r):
        status_label = "ouverte" if r.status == "open" else "clôturée"
        return f"""
        <li class="card">
          <div class="row" style="justify-content:space-between;">
            <div><strong>{r.name}</strong> — <span class="muted">{status_label}</span></div>
            <div class="row">
              <form method="post" action="/admin/rounds/{r.id}/close">
                <button class="btn outline" {'disabled' if r.status=='closed' else ''} type="submit">Clôturer</button>
              </form>
              <form method="post" action="/admin/rounds/{r.id}/open">
                <button class="btn outline" {'disabled' if r.status=='open' else ''} type="submit">Rouvrir</button>
              </form>
              <form method="post" action="/admin/rounds/{r.id}/delete" onsubmit="return confirm('Supprimer définitivement cette manche ?');">
                <button class="btn danger" type="submit">Supprimer</button>
              </form>
            </div>
          </div>
        </li>
        """

    items = "".join(row_html(r) for r in rounds) or "<p class='muted'>Aucune manche pour l’instant.</p>"

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


@app.post("/admin/rounds/<int:round_id>/close")
def admin_round_close(round_id):
    if not db:
        return PAGE("<h1>Admin</h1><p class='muted'>DB non dispo.</p>")
    u = current_user()
    if not is_admin(u):
        return PAGE("<h1>Accès refusé</h1><p class='muted'>Réservé aux administrateurs.</p>"), 403
    r = db.session.get(Round, round_id)
    if not r:
        return PAGE("<h1>Erreur</h1><p class='muted'>Manche introuvable.</p>"), 404
    r.status = "closed"
    db.session.commit()
    return redirect(url_for("admin_rounds"))

@app.post("/admin/rounds/<int:round_id>/open")
def admin_round_open(round_id):
    if not db:
        return PAGE("<h1>Admin</h1><p class='muted'>DB non dispo.</p>")
    u = current_user()
    if not is_admin(u):
        return PAGE("<h1>Accès refusé</h1><p class='muted'>Réservé aux administrateurs.</p>"), 403
    r = db.session.get(Round, round_id)
    if not r:
        return PAGE("<h1>Erreur</h1><p class='muted'>Manche introuvable.</p>"), 404
    r.status = "open"
    db.session.commit()
    return redirect(url_for("admin_rounds"))

@app.post("/admin/rounds/<int:round_id>/delete")
def admin_round_delete(round_id):
    if not db:
        return PAGE("<h1>Admin</h1><p class='muted'>DB non dispo.</p>")
    u = current_user()
    if not is_admin(u):
        return PAGE("<h1>Accès refusé</h1><p class='muted'>Réservé aux administrateurs.</p>"), 403
    r = db.session.get(Round, round_id)
    if not r:
        return PAGE("<h1>Erreur</h1><p class='muted'>Manche introuvable.</p>"), 404
    db.session.delete(r)
    db.session.commit()
    return redirect(url_for("admin_rounds"))

@app.get("/admin/times")
def admin_times():
    if not db:
        return PAGE("<h1>Admin — Chronos</h1><p class='muted'>DB non dispo.</p>")
    u = current_user()
    if not is_admin(u):
        return PAGE("<h1>Accès refusé</h1><p class='muted'>Réservé aux administrateurs.</p>"), 403

    # Montrer d'abord les pending, puis approved puis rejected
    entries = (
        TimeEntry.query
        .order_by(
            db.case((TimeEntry.status == "pending", 0), (TimeEntry.status == "approved", 1), else_=2),
            TimeEntry.created_at.desc()
        )
        .all()
    )

    if not entries:
        return PAGE("<h1>Admin — Chronos</h1><p class='muted'>Aucun chrono pour le moment.</p>")

    def row(e):
        final_ms_val = final_time_ms(e.raw_time_ms, e.penalties)
        yt = f"<a href='{e.youtube_link}' target='_blank' rel='noopener'>Vidéo</a>" if e.youtube_link else "—"
        return f"""
        <tr>
          <td>{e.id}</td>
          <td>{display_name(e.user)}</td>
          <td>{e.round.name}</td>
          <td>{ms_to_str(e.raw_time_ms)}</td>
          <td>{e.penalties}</td>
          <td><strong>{ms_to_str(final_ms_val)}</strong></td>
          <td>{yt}</td>
          <td>{e.status}</td>
          <td>
            <form method="post" action="/admin/times/{e.id}/approve" style="display:inline;">
              <button class="btn" type="submit">Valider</button>
            </form>
            <form method="post" action="/admin/times/{e.id}/reject" style="display:inline;">
              <button class="btn danger" type="submit">Refuser</button>
            </form>
          </td>
        </tr>
        """



    rows = "".join(row(e) for e in entries)
    table = (
        "<table class='table'>"
        "<thead>"
        "<tr>"
        "<th>ID</th><th>Pilote</th><th>Manche</th><th>Brut</th>"
        "<th>Pén.</th><th>Final</th><th>YouTube</th><th>Statut</th><th>Actions</th>"
        "</tr>"
        "</thead>"
        f"<tbody>{rows}</tbody>"
        "</table>"
    )

    return PAGE(
        "<h1>Admin — Chronos</h1>"
        + table +
        "<p style='margin-top:12px'><a class='btn outline' href='/rounds'>Voir les manches</a></p>"
    )


@app.post("/admin/times/<int:time_id>/approve")
def admin_time_approve(time_id):
    if not db:
        return PAGE("<h1>Admin</h1><p class='muted'>DB non dispo.</p>")
    u = current_user()
    if not is_admin(u):
        return PAGE("<h1>Accès refusé</h1><p class='muted'>Réservé aux administrateurs.</p>"), 403
    e = db.session.get(TimeEntry, time_id)
    if not e:
        return PAGE("<h1>Erreur</h1><p class='muted'>Chrono introuvable.</p>"), 404
    e.status = "approved"
    db.session.commit()
    return redirect(url_for("admin_times"))

@app.post("/admin/times/<int:time_id>/reject")
def admin_time_reject(time_id):
    if not db:
        return PAGE("<h1>Admin</h1><p class='muted'>DB non dispo.</p>")
    u = current_user()
    if not is_admin(u):
        return PAGE("<h1>Accès refusé</h1><p class='muted'>Réservé aux administrateurs.</p>"), 403
    e = db.session.get(TimeEntry, time_id)
    if not e:
        return PAGE("<h1>Erreur</h1><p class='muted'>Chrono introuvable.</p>"), 404
    e.status = "rejected"
    db.session.commit()
    return redirect(url_for("admin_times"))

@app.get("/__selftest")
def __selftest():
    try:
        out = []
        out.append(f"db: {'OK' if db else 'MISSING'}")
        try:
            count = Round.query.count()
            out.append(f"Round.count: {count}")
        except Exception as e:
            out.append(f"Round.query ERROR: {e.__class__.__name__}: {e}")
        u = current_user()
        out.append(f"current_user: {u.email if u else 'None'}")
        out.append(f"is_admin: {is_admin(u)}")
        return PAGE("<pre>" + "\n".join(out) + "</pre>")
    except Exception as e:
        return PAGE(f"<pre>SELFTEST FAIL: {e.__class__.__name__}\n{e}</pre>"), 500


# --- Run local ---
@app.route("/submit", methods=["GET", "POST"])
def submit_time():
    if not db:
        return PAGE("<h1>Soumettre un chrono</h1><p class='muted'>DB non dispo.</p>"), 500
    u = current_user()
    if not u:
        return redirect(url_for("login"))

    # Récupère les manches ouvertes
    open_rounds = Round.query.filter_by(status="open").order_by(Round.created_at.desc()).all()

    if request.method == "POST":
        if not open_rounds:
            return PAGE("<h1>Soumettre un chrono</h1><p class='muted'>Aucune manche ouverte pour le moment.</p>"), 400

        round_id = request.form.get("round_id")
        time_input = request.form.get("time_input")
        penalties = request.form.get("penalties") or "0"
        bike = (request.form.get("bike") or "").strip()
        youtube_link = (request.form.get("youtube_link") or "").strip()
        note = (request.form.get("note") or "").strip()

        # validations
        try:
            r_id = int(round_id)
            r = db.session.get(Round, r_id)
            if not r or r.status != "open":
                return PAGE("<h1>Soumettre un chrono</h1><p class='muted'>Manche invalide ou clôturée.</p>"), 400
        except Exception:
            return PAGE("<h1>Soumettre un chrono</h1><p class='muted'>Manche invalide.</p>"), 400

        try:
            raw_ms = parse_time_to_ms(time_input)
        except Exception as e:
            return PAGE(f"<h1>Soumettre un chrono</h1><p class='muted'>Format de temps invalide : {str(e)}</p>"), 400

        try:
            pen = int(penalties)
            if pen < 0:
                pen = 0
        except Exception:
            pen = 0

        entry = TimeEntry(
            user_id=u.id,
            round_id=r.id,
            raw_time_ms=raw_ms,
            penalties=pen,
            bike=bike,
            youtube_link=youtube_link,
            note=note,
            status="pending",
        )
        db.session.add(entry)
        db.session.commit()
        return redirect(url_for("profile"))

    # GET → formulaire (construction sans triple quotes)
    if not open_rounds:
        return PAGE("<h1>Soumettre un chrono</h1><p class='muted'>Aucune manche ouverte pour le moment.</p>")

    opts = "".join([f"<option value='{r.id}'>{r.name}</option>" for r in open_rounds])

    html_lines = []
    html_lines.append("<h1>Soumettre un chrono</h1>")
    html_lines.append('<form method="post" class="form">')
    html_lines.append("  <label>Manche")
    html_lines.append('    <select name="round_id" required>')
    html_lines.append(f"      {opts}")
    html_lines.append("    </select>")
    html_lines.append("  </label>")
    html_lines.append("  <label>Temps (mm:ss.mmm, mm:ss, ss.mmm ou ss)")
    html_lines.append('    <input type="text" name="time_input" placeholder="1:23.456 ou 83.456" required>')
    html_lines.append("  </label>")
    html_lines.append("  <label>Pénalités (1 pénalité = +1s)")
    html_lines.append('    <input type="number" name="penalties" min="0" step="1" value="0">')
    html_lines.append("  </label>")
    html_lines.append("  <label>Moto (facultatif)")
    html_lines.append('    <input type="text" name="bike" placeholder="Marque / Modèle">')
    html_lines.append("  </label>")
    html_lines.append("  <label>Lien YouTube (facultatif)")
    html_lines.append('    <input type="url" name="youtube_link" placeholder="https://...">')
    html_lines.append("  </label>")
    html_lines.append("  <label>Note (facultatif)")
    html_lines.append('    <textarea name="note" rows="3" placeholder="Remarque libre..."></textarea>')
    html_lines.append("  </label>")
    html_lines.append('  <button class="btn" type="submit">Envoyer</button>')
    html_lines.append("</form>")

    return PAGE("\n".join(html_lines))






@app.get("/profile")
def profile():
    if not db:
        return PAGE("<h1>Mon profil</h1><p class='muted'>DB non dispo.</p>")
    u = current_user()
    if not u:
        return redirect(url_for("login"))

    role = "Administrateur" if u.is_admin else "Pilote"
    pseudo = u.pseudo or "—"
    nationality = u.nationality or "—"
    email = u.email

    # Récupère tous les chronos de l'utilisateur
    entries = (
        TimeEntry.query
        .filter_by(user_id=u.id)
        .order_by(TimeEntry.created_at.desc())
        .all()
    )

    # Construit le tableau des chronos (une seule table avec la colonne Statut)
    if not entries:
        chronos_html = "<p class='muted'>Aucun chrono pour l’instant.</p>"
    else:
        def row(e):
            raw = ms_to_str(e.raw_time_ms)
            final_ms_val = final_time_ms(e.raw_time_ms, e.penalties)
            final_s = ms_to_str(final_ms_val)
            yt = f"<a href='{e.youtube_link}' target='_blank' rel='noopener'>Vidéo</a>" if e.youtube_link else "—"
            created = e.created_at.strftime("%Y-%m-%d %H:%M") if getattr(e, "created_at", None) else "—"
            return (
                "<tr>"
                f"<td>{e.round.name}</td>"
                f"<td>{raw}</td>"
                f"<td>{e.penalties}</td>"
                f"<td><strong>{final_s}</strong></td>"
                f"<td>{e.bike or '—'}</td>"
                f"<td>{yt}</td>"
                f"<td>{e.status}</td>"
                f"<td>{created}</td>"
                "</tr>"
            )

        rows = "".join(row(e) for e in entries)
        chronos_html = (
            "<table class='table'>"
            "<thead><tr>"
            "<th>Manche</th><th>Brut</th><th>Pén.</th><th>Final</th>"
            "<th>Moto</th><th>YouTube</th><th>Statut</th><th>Ajouté</th>"
            "</tr></thead>"
            f"<tbody>{rows}</tbody>"
            "</table>"
        )

    # Liens admin (uniquement si admin)
    admin_links = ""
    if is_admin(u):
        admin_links = (
            "<div class='row' style='gap:8px; margin-top:8px;'>"
            "<a class='btn' href='/admin/rounds'>Admin — Manches</a>"
            "<a class='btn outline' href='/admin/times'>Admin — Chronos</a>"
            "</div>"
        )

    # Actions pilote
    actions_html = (
        "<div class='row' style='gap:8px;'>"
        "<a class='btn' href='/submit'>Soumettre un chrono</a>"
        "<a class='btn outline' href='/logout'>Se déconnecter</a>"
        "</div>"
    )

    # Page finale (sans triple guillemets)
    html = []
    html.append("<h1>Mon profil</h1>")
    html.append("<section class='card'>")
    html.append("<h2 style='margin-top:0;'>Infos pilote</h2>")
    html.append(f"<p><strong>Pseudo :</strong> {pseudo}</p>")
    html.append(f"<p><strong>Email :</strong> {email}</p>")
    html.append(f"<p><strong>Nationalité :</strong> {nationality}</p>")
    html.append(f"<p><strong>Rôle :</strong> {role}</p>")
    html.append(admin_links)
    html.append("</section>")
    html.append("<section style='margin-top:16px;' class='card'>")
    html.append("<h2 style='margin-top:0;'>Mes actions</h2>")
    html.append(actions_html)
    html.append("</section>")
    html.append("<section style='margin-top:16px;' class='card'>")
    html.append("<h2 style='margin-top:0;'>Mes chronos</h2>")
    html.append(chronos_html)
    html.append("</section>")
    return PAGE("".join(html))






@app.get("/rounds/<int:round_id>")
def round_leaderboard(round_id):
    if not db:
        return PAGE("<h1>Classement</h1><p class='muted'>DB non dispo.</p>")
    r = db.session.get(Round, round_id)
    if not r:
        return PAGE("<h1>Classement</h1><p class='muted'>Manche introuvable.</p>"), 404

    try:
        # On ne prend que les chronos VALIDÉS
        entries = (
            TimeEntry.query
            .filter_by(round_id=r.id, status="approved")
            .order_by(TimeEntry.created_at.desc())
            .all()
        )

        if not entries:
            return PAGE(f"<h1>{r.name}</h1><p class='muted'>Aucun chrono validé pour le moment.</p>")

        # Sécuriser le calcul du final et ignorer tout enregistrement corrompu
        safe_entries = []
        finals = []
        for e in entries:
            try:
                raw_ms = int(e.raw_time_ms or 0)
                pen = int(e.penalties or 0)
                fm = final_time_ms(raw_ms, pen)
                safe_entries.append((e, fm))
                finals.append(fm)
            except Exception:
                # on skippe cette entrée si problème de données
                continue

        if not safe_entries:
            return PAGE(f"<h1>{r.name}</h1><p class='muted'>Aucun chrono exploitable.</p>")

        best = min(finals) if finals else 0

        def row(i, e, fm):
            # % vs leader (leader = 100)
            pct = (fm / best * 100.0) if fm > 0 and best > 0 else 0.0
            # nom + nationalité
            name = display_name(getattr(e, "user", None)) if hasattr(e, "user") else "—"
            nat = "—"
            if hasattr(e, "user") and getattr(e.user, "nationality", None):
                nat = (e.user.nationality or "—").upper()
            # lien vidéo
            yt = f"<a target=\"_blank\" rel=\"noopener\" href=\"{e.youtube_link}\">Vidéo</a>" if (e.youtube_link or "").strip() else "—"
            return (
                "<tr>"
                f"<td>{i}</td>"
                f"<td>{name}</td>"
                f"<td>{nat}</td>"
                f"<td>{ms_to_str(e.raw_time_ms)}</td>"
                f"<td>{e.penalties}</td>"
                f"<td><strong>{ms_to_str(fm)}</strong></td>"
                f"<td>{pct:.2f}%</td>"
                f"<td>{e.bike or '—'}</td>"
                f"<td>{yt}</td>"
                "</tr>"
            )

        # trier par temps final croissant
        safe_entries.sort(key=lambda t: t[1])
        rows = "".join(row(i + 1, e, fm) for i, (e, fm) in enumerate(safe_entries))

        table = (
            "<table class='table'>"
            "<thead><tr>"
            "<th>#</th><th>Pilote</th><th>Nation</th><th>Brut</th><th>Pén.</th><th>Final</th>"
            "<th>% du meilleur</th><th>Moto</th><th>YouTube</th>"
            "</tr></thead>"
            f"<tbody>{rows}</tbody>"
            "</table>"
        )

        return PAGE(f"<h1>{r.name}</h1>" + table)



    except Exception as e:
        # Dernier filet de sécurité : on affiche l'erreur lisiblement au lieu d'une 500 blanche
        return PAGE(f"<h1>{r.name}</h1><p class='muted'>Erreur : {e.__class__.__name__}: {e}</p>"), 500



@app.get("/__migrate_add_pseudo")
def __migrate_add_pseudo():
    if not db:
        return "DB non dispo", 500
    from flask import request, abort
    token = request.args.get("token")
    if token != app.config.get("SECRET_KEY"):
        abort(403)

    # 1) Lister les colonnes existantes de 'user'
    try:
        res = db.session.execute(db.text("PRAGMA table_info(user)")).all()
        # res = liste de lignes (cid, name, type, notnull, dflt_value, pk)
        cols = [row[1] for row in res]
    except Exception as e:
        return f"PRAGMA error: {e.__class__.__name__}: {e}", 500

    # 2) Si 'pseudo' existe déjà → OK
    if "pseudo" in cols:
        return "OK: colonne 'pseudo' déjà présente"

    # 3) Sinon, on tente de l'ajouter
    try:
        db.session.execute(db.text("ALTER TABLE user ADD COLUMN pseudo VARCHAR(80)"))
        db.session.commit()
        return "OK: colonne 'pseudo' ajoutée"
    except Exception as e:
        # Si jamais SQLite renvoie 'duplicate column' malgré le test, on renvoie OK
        if "duplicate column" in str(e).lower():
            return "OK: colonne 'pseudo' déjà présente (via erreur)."
        return f"Erreur migration: {e.__class__.__name__}: {e}", 500







if __name__ == "__main__":
    if db:
        with app.app_context():
            db.create_all()
    app.run(host="0.0.0.0", port=5000, debug=True)

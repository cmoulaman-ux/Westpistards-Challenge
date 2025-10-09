import os
from datetime import datetime
from flask import Flask, request, redirect, url_for, session, render_template_string, Response
from flask_sqlalchemy import SQLAlchemy
import csv, io
from flask import Response
from flask import send_from_directory
from werkzeug.utils import secure_filename




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
        closes_at = db.Column(db.DateTime, nullable=True)
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

class Announcement(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        content = db.Column(db.Text, nullable=False)         # le texte du bandeau (HTML simple permis)
        is_active = db.Column(db.Boolean, default=True)      # affiché ou non
        created_at = db.Column(db.DateTime, default=datetime.utcnow)



# Emails admin (en minuscules)
ADMIN_EMAILS = {
    "renaud.debry@ecf-cerca.fr",
    "westpistards@gmail.com",
    "c.moulaman@gmail.com",
}

# --- Helpers utilisateur ---
def current_user():
    if not db:
        return None
    uid = session.get("user_id")
    if not uid:
        return None
    u = db.session.get(User, uid)
    if not u:
        return None
    # Auto-élévation : si l’email est dans ADMIN_EMAILS, on pose le flag en base
    email = (u.email or "").strip().lower()
    if email in ADMIN_EMAILS and not getattr(u, "is_admin", False):
        u.is_admin = True
        db.session.commit()
    return u


def is_admin(u):
    if not u:
        return False
    email = (u.email or "").strip().lower()
    # admin si le flag DB est vrai OU si l’email est dans la liste
    return bool(getattr(u, "is_admin", False) or email in ADMIN_EMAILS)

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
def PAGE(inner_html):
    u = current_user() if db else None

    # --- NAV DROITE (simple et claire) ---
    nav_parts = []
    # Public
    nav_parts.append("<a href='/static/docs/Reglement_WestPistardsChallenge.pdf' target='_blank' rel='noopener'>Règlement</a>")
    nav_parts.append("<a href='/rounds'>Manches</a>")
    nav_parts.append(
        "<a href='https://www.facebook.com/west.pistards' target='_blank' rel='noopener' title='Ouvrir notre page Facebook'>Facebook</a>"
    )



    # Connexion / Profil
    if u:
        nav_parts.append("<a href='/profile'>Profil</a>")
        # 👇 plus de lien Admin ici (tu gères l’admin depuis le profil)
        nav_parts.append("<a href='/logout'>Déconnexion</a>")
    else:
        nav_parts.append("<a href='/register'>Inscription</a>")
        nav_parts.append("<a href='/login'>Connexion</a>")

    nav_right = " ".join(nav_parts)


    return f"""
<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <link rel="icon" type="image/png" href="/static/img/icon-192.png">
  <title>WP Challenge</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
  <header class="container">
    <div class="nav">
      <div>
        <a class="brand" href="/" title="Retour à l’accueil" aria-label="Retour à l’accueil">
          🏠 <span>WP Challenge</span>
        </a>
      </div>
      <nav>{nav_right}</nav>
    </div>
  </header>
  <main class="container">
    {inner_html}
  </main>
  <footer class="container muted">
    <a href="/credits">Crédits photo & vidéo</a><br>
    <a href="/privacy">Politique et Confidentialité</a><br>
    © 2025 westpistards
  </footer>
  <!-- Bandeau d'annonce (haut) : défile & pause au centre -->
  <script>
    document.addEventListener('DOMContentLoaded', function () {{
      // Respect des préférences d'accessibilité
      if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) {{
        return;
      }}

      const track = document.querySelector('.banner .marquee [data-marquee]');
      if (!track) return;

      const container = track.closest('.marquee') || track.parentElement;

      function setupAnimation() {{
        // Annule toute anim en cours (resize, re-init)
        track.getAnimations && track.getAnimations().forEach(a => a.cancel());

        // Mesures
        const W = container.clientWidth;
        const T = track.scrollWidth;

        // Si le texte tient, on le centre statiquement
        if (!W || !T || T <= W) {{
          track.style.transform = 'translateX(' + ((W - T) / 2) + 'px)';
          return;
        }}

        // Positions (px)
        const startX  = W;                 // totalement hors-champ à droite
        const centerX = (W - T) / 2;       // parfaitement centré
        const endX   = -T;                 // hors-champ à gauche

        // Vitesse constante (px/s) et pause au centre (ms)
        const SPEED_PX_PER_SEC = 110;      // ajuste si besoin (plus grand = plus vite)
        const PAUSE_MS = 1800;             // ~1.8s de pause

        const d1 = startX - centerX;       // distance jusqu'au centre
        const d2 = centerX - endX;         // distance jusqu'à la sortie gauche
        const t1 = d1 / SPEED_PX_PER_SEC;  // en secondes
        const t2 = d2 / SPEED_PX_PER_SEC;  // en secondes
        const pause = PAUSE_MS / 1000;

        const total = t1 + pause + t2;

        // Keyframes avec pause au centre (même transform sur deux offsets)
        const kf = [
          {{ transform: 'translateX(' + startX  + 'px)' }},
          {{ transform: 'translateX(' + centerX + 'px)', offset: (t1 / total) }},
          {{ transform: 'translateX(' + centerX + 'px)', offset: ((t1 + pause) / total) }},
          {{ transform: 'translateX(' + endX    + 'px)' }}
        ];

        const anim = track.animate(kf, {{
          duration: total * 1000,
          iterations: Infinity,
          easing: 'linear'
        }});

        // Pour Safari : applique immédiatement la 1re frame
        track.style.transform = 'translateX(' + startX + 'px)';
      }}

      // Initialise une fois les polices chargées (mesures fiables)
      if (document.fonts && document.fonts.ready) {{
        document.fonts.ready.then(setupAnimation);
      }} else {{
        setTimeout(setupAnimation, 50);
      }}

      // Réadapte sur resize (debounce léger)
      let to = null;
      window.addEventListener('resize', function () {{
        clearTimeout(to);
        to = setTimeout(setupAnimation, 150);
      }});
    }});
  </script>





</body>
</html>
"""




# --- Pages ---
@app.get("/")
def index():
    open_list_html = "<p class='muted'>Aucune manche ouverte pour le moment.</p>"
    countdown_script = ""  # on l'ajoutera si au moins une manche a une deadline

    if db:
        open_rounds = (
            Round.query.filter_by(status="open")
            .order_by(Round.created_at.desc())
            .all()
        )
        if open_rounds:
            items = []
            for r in open_rounds:
                date_str = r.created_at.strftime('%d/%m/%Y')
                # Si une date de clôture existe, on ajoute une pastille avec data-deadline
                if getattr(r, "closes_at", None):
                    deadline_iso = r.closes_at.isoformat(sep=" ", timespec="minutes")
                    cd = (
                        f"<span class='cd-badge' "
                        f"data-deadline='{deadline_iso}' "
                        f"id='cd-{r.id}'>calcul...</span>"
                    )
                    items.append(
                        f"<li class='row' style='justify-content:space-between;'>"
                        f"  <span><a href='/rounds/{r.id}'>{r.name}</a> {cd}</span>"
                        f"  <span class='muted'>{date_str}</span>"
                        f"</li>"
                    )
                else:
                    # pas de clôture : pas de pastille
                    items.append(
                        f"<li class='row' style='justify-content:space-between;'>"
                        f"  <span><a href='/rounds/{r.id}'>{r.name}</a></span>"
                        f"  <span class='muted'>{date_str}</span>"
                        f"</li>"
                    )

            open_list_html = (
                "<ul style='list-style:none; padding-left:0; margin:0;'>"
                + "".join(items) +
                "</ul>"
            )

            # Script unique pour mettre à jour toutes les pastilles .cd-badge
            countdown_script = """
            <script>
            (function(){
              function z(n){ return n<10 ? ('0'+n) : n; }
              function tick(){
                const now = new Date();
                document.querySelectorAll('.cd-badge').forEach(el=>{
                  const dl = el.getAttribute('data-deadline');
                  if(!dl) return;
                  const deadline = new Date(dl.replace(' ','T'));
                  let diff = Math.floor((deadline - now)/1000);
                  el.classList.remove('cd-warn','cd-danger');
                  if(diff <= 0){
                    el.textContent = 'Clôturée';
                    el.classList.add('cd-danger');
                    return;
                  }
                  const d = Math.floor(diff/86400); diff %= 86400;
                  const h = Math.floor(diff/3600);  diff %= 3600;
                  const m = Math.floor(diff/60);
                  // Affichage compact : si d>0 on montre 'dj HH:MM', sinon 'HH:MM'
                  el.textContent = (d>0 ? (d+'j ') : '') + z(h)+':'+z(m);
                  const totalSec = (deadline - now)/1000;
                  if (totalSec < 3600) el.classList.add('cd-danger');   // < 1h
                  else if (totalSec < 86400) el.classList.add('cd-warn'); // < 24h
                });
              }
              tick();
              setInterval(tick, 30000); // maj toutes les 30s (suffisant pour la home)
            })();
            </script>
            """


     # Liens partenaires (remplace par tes vraies pages FB)
    partners_html = """
    <section class="card" style="margin-top:24px;">
      <h2>Partenaires</h2>
      <p class="muted" style="margin-top:-4px;">Merci à nos partenaires pour leur soutien.</p>

      <div class="logo-grid" style="margin-top:12px;">
        <a class="partner" href="https://www.coneaddict.com" target="_blank" rel="noopener" title="Cone Addict">
          <img src="/static/img/partners/partner1.jpg" alt="Partenaire 1">
        </a>
        <a class="partner" href="https://www.instagram.com/lou_etheve/" target="_blank" rel="noopener" title="Sellerie Lou Ethève">
          <img src="/static/img/partners/partner2.jpg" alt="Partenaire 2">
        </a>
        <a class="partner" href="https://www.facebook.com/share/17Jn1CirtS" target="_blank" rel="noopener" title="ECF">
          <img src="/static/img/partners/ecf.jpg" alt="ECF">
        </a>
      </div>
    </section>
    """
    networks_html = """
    <section class="card" style="margin-top:24px;">
      <h2>Les réseaux du gymkhana français</h2>
      <p class="muted" style="margin-top:-4px;">Où pratiquer en France — associations et collectifs.</p>

      <div class="networks-grid">
        <!-- Nouveau logo en premier -->
        <a class="netw" href="https://www.facebook.com/west.pistards" target="_blank" rel="noopener" title="WP">
          <img src="/static/img/assos/wp.jpg" alt="WP">
        </a>
        <a class="netw" href="https://www.instagram.com/maniacones360/" target="_blank" rel="noopener" title="Mania">
          <img src="/static/img/assos/mania3.jpg" alt="Mania">
        </a>
        <a class="netw" href="https://www.facebook.com/perfectionnement.maniabilite.gymkhana.moto" target="_blank" rel="noopener" title="Angle">
          <img src="/static/img/assos/angle2.png" alt="Angle">
        </a>
        <a class="netw" href="https://www.facebook.com/SpectacleMotoGymkhana" target="_blank" rel="noopener" title="Gravity">
          <img src="/static/img/assos/gravity2.png" alt="Gravity">
        </a>
      </div>
      <p class="muted center" style="margin-top:8px;">
        Vous souhaitez faire apparaître votre asso ou collectif ? Contactez-nous via la page WestPistards.
      </p>
    </section>
    """



    # Bandeau d'annonces (affiche la plus récente active)
    banner_html = ""
    if db:
        ann = Announcement.query.filter_by(is_active=True).order_by(Announcement.created_at.desc()).first()
        if ann:
            msg = ann.content  # peut contenir <strong> etc.
            banner_html = f"""
            <div class="banner" aria-live="polite">
              <div class="marquee" role="marquee" aria-label="Annonce défilante">
              <div class="marquee-track" style="animation-duration: 30s;">
                  {msg}
                </div>
              </div>
            </div>

            """


    # --- Rendu de la page d'accueil ---
    return PAGE(f"""
    <section class="bg-hero">
      <div class="bg-hero-yt">
        <iframe
          src="https://www.youtube.com/embed/videoseries?list=PLPm1klG-O5duFLlins-RZ4E1g3NOYH9qU&autoplay=1&mute=1&controls=0&rel=0&loop=1&playlist=PLPm1klG-O5duFLlins-RZ4E1g3NOYH9qU&modestbranding=1&playsinline=1&enablejsapi=1&origin=https://westpistards-challenge.onrender.com"
          title="Background video"
          allow="autoplay; encrypted-media; fullscreen; picture-in-picture"
          frameborder="0">
        </iframe>

      </div>
      <div class="bg-hero-overlay"></div>
      <div class="hero-content">
        <!-- Petit texte d’accroche optionnel dans la vidéo -->
        <h2 style="margin:0;">French Gymkhana Cup</h2>
      </div>
    </section>

      <div class="hero-row blend">
        <img class="logo-secondary" src="/static/img/logo_motogymkhana.jpg" alt="Moto Gymkhana">
        <h1 class="hero-title">Bienvenue sur WP Challenge</h1>
        <img class="logo-main" src="/static/img/logo_challenge.png" alt="WP Challenge">
      </div>

      <p>Entre tes chronos, partage ton lien YouTube et grimpe au classement !</p>

      {banner_html}

      <section class="card">
        <div class="row" style="justify-content:space-between; align-items:center; gap:12px; flex-wrap:wrap; margin-bottom:8px;">
          <h2 style="margin:0;">Manches ouvertes</h2>
          <a class="btn outline" href="/trace/download" title="Télécharger le tracé">
            <span class="i">⬇️</span> Télécharger le tracé
          </a>
        </div>

        {open_list_html}
      </section>

      {countdown_script}

      {partners_html}

      {networks_html}

    <!-- Bandeau photo bas de page -->
    <!-- Bandeau photos défilantes (bas de page) -->
    <section class="marquee-photos" aria-label="Photos WestPistards">
      <div class="marquee-track" style="animation-duration: 20s;">
        <!-- Rangée A -->
        <img src="/static/img/bottom_gallery/imageun.jpg" alt="WestPistards 1" loading="lazy">
        <img src="/static/img/bottom_gallery/imagedeux.jpg" alt="WestPistards 2" loading="lazy">
        <img src="/static/img/bottom_gallery/imagetrois.jpg" alt="WestPistards 3" loading="lazy">
        <img src="/static/img/bottom_gallery/imagequatre.jpg" alt="WestPistards 4" loading="lazy">
        <img src="/static/img/bottom_gallery/imagecinq.jpg" alt="WestPistards 5" loading="lazy">
        <img src="/static/img/bottom_gallery/imagesix.jpg" alt="WestPistards 6" loading="lazy">
        <img src="/static/img/bottom_gallery/imagesept.jpg" alt="WestPistards 7" loading="lazy">
        <img src="/static/img/bottom_gallery/imagehuit.jpg" alt="WestPistards 8" loading="lazy">
        <!-- Rangée B (duplication pour boucle fluide) -->
        <img src="/static/img/bottom_gallery/imageun.jpg" alt="" aria-hidden="true" loading="lazy">
        <img src="/static/img/bottom_gallery/imagedeux.jpg" alt="" aria-hidden="true" loading="lazy">
        <img src="/static/img/bottom_gallery/imagetrois.jpg" alt="" aria-hidden="true" loading="lazy">
        <img src="/static/img/bottom_gallery/imagequatre.jpg" alt="" aria-hidden="true" loading="lazy">
        <img src="/static/img/bottom_gallery/imagecinq.jpg" alt="" aria-hidden="true" loading="lazy">
        <img src="/static/img/bottom_gallery/imagesix.jpg" alt="" aria-hidden="true" loading="lazy">
        <img src="/static/img/bottom_gallery/imagesept.jpg" alt="" aria-hidden="true" loading="lazy">
        <img src="/static/img/bottom_gallery/imagehuit.jpg" alt="" aria-hidden="true" loading="lazy">
      </div>
    </section>



    """)






@app.route("/register", methods=["GET", "POST"])
def register():
    if not db:
        return PAGE("<h1>Inscription</h1><p class='muted'>DB non dispo.</p>")

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        nationality = (request.form.get("nationality") or "").strip().upper()
        pseudo = (request.form.get("pseudo") or "").strip()

        if not email or not nationality:
            return PAGE("<h1>Inscription</h1><p class='muted'>Email et nationalité sont obligatoires.</p>"), 400

        # Si l’utilisateur existe déjà, on le réutilise
        u = User.query.filter_by(email=email).first()
        if not u:
            u = User(email=email, nationality=nationality, pseudo=pseudo or None)
            db.session.add(u)
            db.session.commit()
        else:
            # mettre à jour le pseudo/nationalité si fournis
            if pseudo:
                u.pseudo = pseudo
            if nationality:
                u.nationality = nationality
            db.session.commit()

        session["user_id"] = u.id
        return redirect(url_for("profile"))

    # GET -> formulaire
    return PAGE(f"""
      <h1>Inscription</h1>
      <form method="post" class="form">
        <label>Adresse e-mail
          <input type="email" name="email" required>
        </label>
        <label>Nationalité
          <input type="text" name="nationality" placeholder="FR, BE, ..." required>
        </label>
        <label>Pseudo (affiché dans les classements)
          <input type="text" name="pseudo" placeholder="Votre pseudo public">
        </label>
        <button class="btn" type="submit">Valider</button>
      </form>
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
        return PAGE("<h1>Admin</h1><p class='muted'>DB non dispo.</p>"), 500
    u = current_user()
    if not is_admin(u):
        return PAGE("<h1>Accès refusé</h1><p class='muted'>Réservé aux administrateurs.</p>"), 403

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        if not name:
            return PAGE("<h1>Admin &mdash; Manches</h1><p class='muted'>Nom obligatoire.</p>"), 400

        # Lecture optionnelle de la date de clôture (datetime-local HTML5)
        closes_at_val = (request.form.get("closes_at") or "").strip()
        closes_at_dt = None
        if closes_at_val:
            # Format attendu: YYYY-MM-DDTHH:MM (ex: 2025-10-05T18:00)
            try:
                from datetime import datetime
                closes_at_dt = datetime.fromisoformat(closes_at_val)
            except Exception:
                # On ignore silencieusement si invalide, ou on pourrait renvoyer une erreur
                closes_at_dt = None

        r = Round(name=name, status="open")
        if hasattr(Round, "closes_at"):
            r.closes_at = closes_at_dt

        f = request.files.get("plan")
        if f and f.filename:
            if (f.mimetype or "").startswith("image/"):
                data = f.read()
                r.plan_data = data
                r.plan_mime = f.mimetype
                r.plan_name = secure_filename(f.filename)
            else:
                return PAGE("<h1>Admin &mdash; Manches</h1><p class='muted'>Seules les images sont acceptées (PNG/JPG).</p>"), 400

        db.session.add(r)
        db.session.commit()
        return redirect(url_for("admin_rounds"))


    rounds = Round.query.order_by(Round.created_at.desc()).all()

    def row_html(r):
        status_label = "ouverte" if r.status == "open" else "clôturée"
        close_info = ""
        if hasattr(r, "closes_at") and r.closes_at:
            close_info = f" &middot; <span class='muted'>clôture: {r.closes_at.strftime('%d/%m/%Y %H:%M')}</span>"
        view_btn = f"<a class='icon-btn' href='/rounds/{r.id}/plan' target='_blank' title='Voir l’image du plan'><span class='i'>🖼️</span></a>" if getattr(r, "plan_data", None) else ""
        return f"""
        <li class="card">
          <div class="row" style="justify-content:space-between;">
            <div><strong>{r.name}</strong> &mdash; <span class="muted">{status_label}</span>{close_info}</div>
            <div class="row">
              {view_btn}
              <a class="icon-btn green" href="/admin/rounds/{r.id}/export.csv" title="Télécharger CSV" aria-label="Télécharger CSV">
                <span class="i">⬇️</span>
              </a>
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
      <h1>Admin &mdash; Manches</h1>
      <form method="post" class="form" enctype="multipart/form-data">
        <label>Nom de la manche
          <input type="text" name="name" placeholder="Ex: Manche 1 — Circuit X" required>
        </label>
        <label>Date/heure de clôture (optionnel)
          <input type="datetime-local" name="closes_at">
        </label>
        <label>Image de la manche (PNG/JPG, optionnel)
          <input type="file" name="plan" accept="image/*">
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

    # Supprimer d'abord les chronos liés (évite l'erreur de contrainte)
    try:
        from sqlalchemy import delete
        db.session.execute(delete(TimeEntry).where(TimeEntry.round_id == round_id))
        db.session.delete(r)
        db.session.commit()
        return redirect(url_for("admin_rounds"))
    except Exception as e:
        db.session.rollback()
        return PAGE(f"<h1>Erreur</h1><p class='muted'>Suppression impossible : {e.__class__.__name__}: {e}</p>"), 500


@app.get("/admin/times")
def admin_times():
    if not db:
        return PAGE("<h1>Admin</h1><p class='muted'>DB non dispo.</p>"), 500
    u = current_user()
    if not is_admin(u):
        return PAGE("<h1>Accès refusé</h1><p class='muted'>Réservé aux administrateurs.</p>"), 403

    # Filtre par onglet
    tab = (request.args.get("status") or "pending").lower()
    if tab not in ("pending", "approved", "rejected"):
        tab = "pending"

    q = (
        TimeEntry.query
        .join(User, User.id == TimeEntry.user_id)
        .join(Round, Round.id == TimeEntry.round_id)
    )
    q = q.filter(TimeEntry.status == tab) if tab in ("pending", "approved", "rejected") else q
    entries = q.order_by(TimeEntry.created_at.desc()).all()

    # Onglets
    def tab_link(label, key):
        cls = "btn" + ("" if tab == key else " outline")
        return f"<a class='{cls}' href='/admin/times?status={key}'>{label}</a>"

    tabs = (
        "<div class='row' style='gap:8px; margin-bottom:12px;'>"
        f"{tab_link('En attente', 'pending')}"
        f"{tab_link('Validés', 'approved')}"
        f"{tab_link('Rejetés', 'rejected')}"
        "</div>"
    )

    if not entries:
        mapping = {"pending":"en attente", "approved":"validés", "rejected":"rejetés"}
        return PAGE(f"<h1>Admin &mdash; Chronos</h1>{tabs}<p class='muted'>Aucun chrono {mapping.get(tab,'')}.</p>")

    # Lignes du tableau : actions selon statut
    def row(e):
        final_ms_val = final_time_ms(e.raw_time_ms, e.penalties)
        yt = f"<a href='{e.youtube_link}' target='_blank' rel='noopener'>Vidéo</a>" if (e.youtube_link or "").strip() else "—"
        badge = "pending" if e.status == "pending" else ("approved" if e.status == "approved" else "rejected")

        # Actions selon statut (pas de "remettre en attente")
        actions = []
        if e.status == "pending":
            actions.append(f"<form method='post' action='/admin/times/{e.id}/approve' style='display:inline;'><button class='btn' type='submit'>Valider</button></form>")
            actions.append(f"<form method='post' action='/admin/times/{e.id}/reject' style='display:inline; margin-left:6px;'><button class='btn danger' type='submit'>Rejeter</button></form>")
        elif e.status == "approved":
            actions.append(f"<form method='post' action='/admin/times/{e.id}/reject' style='display:inline;'><button class='btn danger' type='submit'>Rejeter</button></form>")
        elif e.status == "rejected":
            actions.append(f"<form method='post' action='/admin/times/{e.id}/approve' style='display:inline;'><button class='btn' type='submit'>Valider</button></form>")

        actions_html = "".join(actions)

        return f"""
        <tr>
          <td>{e.id}</td>
          <td>{display_name(e.user)}</td>
          <td>{e.round.name}</td>
          <td>{ms_to_str(e.raw_time_ms)}</td>
          <td>{e.penalties}</td>
          <td><strong>{ms_to_str(final_ms_val)}</strong></td>
          <td>{yt}</td>
          <td><span class='badge {badge}'>{e.status}</span></td>
          <td>{actions_html}</td>
        </tr>
        """

    rows_html = "".join(row(e) for e in entries)

    table = (
        "<table class='table'>"
        "<thead><tr>"
        "<th>ID</th><th>Pilote</th><th>Manche</th><th>Brut</th><th>Pén.</th><th>Final</th><th>YouTube</th><th>Statut</th><th>Actions</th>"
        "</tr></thead>"
        f"<tbody>{rows_html}</tbody>"
        "</table>"
    )

    return PAGE(f"""
      <h1>Admin &mdash; Chronos</h1>
      {tabs}
      {table}
    """)


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

    # 1) Valider ce chrono
    e.status = "approved"

    # 2) Auto-inactiver (superseded) les autres chronos validés
    db.session.flush()  # s'assure que e.id est connu
    (
        db.session.query(TimeEntry)
        .filter(
            TimeEntry.round_id == e.round_id,
            TimeEntry.user_id == e.user_id,
            TimeEntry.status == "approved",
            TimeEntry.id != e.id,
        )
        .update({TimeEntry.status: "superseded"}, synchronize_session=False)
    )

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
    html_lines.append("  <label>Temps (mm:ss.mmm, par ex 1:32.543)")
    html_lines.append('    <input type="text" name="time_input" placeholder="1:23.456" required>')
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

    # --- Section "Mes chronos" avec badges de statut ---
    # Section chronos
    if not entries:
        chronos_html = "<p class='muted'>Aucun chrono pour l’instant.</p>"
    else:
        def row(e):
            raw = ms_to_str(e.raw_time_ms)
            final_ms = final_time_ms(e.raw_time_ms, e.penalties)
            final_s = ms_to_str(final_ms)
            yt = f"<a href='{e.youtube_link}' target='_blank' rel='noopener'>Vidéo</a>" if e.youtube_link else "—"
            badge = f"<span class='badge { 'approved' if e.status=='approved' else ('rejected' if e.status=='rejected' else ('inactif' if e.status=='superseded' else 'pending')) }'>{e.status}</span>"
            actions = (
                f"<form method='post' action='/my/times/{e.id}/delete' "
                "onsubmit=\"return confirm('Supprimer ce chrono ?');\" style='display:inline;'>"
                "<button class='btn danger icon' type='submit' "
                "title='Supprimer ce chrono' aria-label='Supprimer'>×</button>"
                "</form>"
            )
            return (
                "<tr>"
                f"<td>{e.round.name}</td>"
                f"<td>{raw}</td>"
                f"<td>{e.penalties}</td>"
                f"<td><strong>{final_s}</strong></td>"
                f"<td>{e.bike or '—'}</td>"
                f"<td>{yt}</td>"
                f"<td>{badge}</td>"
                f"<td>{actions}</td>"
                "</tr>"
            )

        rows = "".join(row(e) for e in entries)
        chronos_html = (
            "<table class='table'>"
            "<thead><tr>"
            "<th>Manche</th><th>Brut</th><th>Pén.</th><th>Final</th><th>Moto</th><th>YouTube</th><th>Statut</th><th>Actions</th>"
            "</tr></thead>"
            f"<tbody>{rows}</tbody>"
            "</table>"
        )



    # Liens admin (uniquement si admin)
    # Liens admin (uniquement si admin)
    admin_links = ""
    if is_admin(u):
        admin_links = """
        <div class="row" style="gap:8px; margin-top:8px;">
          <a class="btn" href="/admin/rounds">Admin — Manches</a>
          <a class="btn outline" href="/admin/times">Admin — Chronos</a>
          <a class="btn" href="/admin/banner">Admin — Bannière</a>
          <a class="btn outline" href="/admin/users">Inscrits</a>
        </div>
        """


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

    # --- Compte à rebours (hors du if not r, et initialisé) ---
    countdown_html = ""
    if getattr(r, "closes_at", None):
        deadline_iso = r.closes_at.isoformat(sep=" ", timespec="minutes")
        deadline_human = r.closes_at.strftime("%d/%m/%Y %H:%M")
        countdown_html = f"""
        <section class="countdown-box card" id="countdown-wrap" style="margin-bottom:16px;">
          <div>
            <h2 style="margin:0;">Clôture de la manche</h2>
            <p class="countdown-meta">La manche se clôture le <strong>{deadline_human}</strong>.</p>
          </div>
          <div class="countdown" id="countdown" data-deadline="{deadline_iso}">—:—:—</div>
        </section>
        <script>
        (function(){{
          const el = document.getElementById('countdown');
          if(!el) return;
          const deadline = new Date(el.dataset.deadline.replace(' ', 'T'));
          const wrap = document.getElementById('countdown-wrap');
          function z(n){{ return n<10 ? '0'+n : n; }}
          function tick(){{
            const now = new Date();
            let diff = Math.floor((deadline - now)/1000);
            if (diff <= 0) {{
              el.textContent = 'Clôturée';
              wrap.classList.add('danger');
              return;
            }}
            const d = Math.floor(diff/86400); diff %= 86400;
            const h = Math.floor(diff/3600);  diff %= 3600;
            const m = Math.floor(diff/60);
            const s = diff % 60;
            el.textContent = (d>0 ? (d+'j ') : '') + z(h)+':'+z(m)+':'+z(s);

            const totalSec = (deadline - now)/1000;
            wrap.classList.remove('warn','danger');
            if (totalSec < 3600) wrap.classList.add('danger');      // < 1h
            else if (totalSec < 86400) wrap.classList.add('warn');  // < 24h

            setTimeout(tick, 500);
          }}
          tick();
        }})();
        </script>
        """

    # --- Bouton "Voir le plan" (si un plan existe) ---
    # --- Boutons Plan (si un plan existe) ---
    plan_btn = ""
    if getattr(r, "plan_data", None):
        plan_btn = (
            f"<div class='row' style='gap:8px;'>"
            f"<a class='btn outline' href='/rounds/{r.id}/plan' target='_blank' rel='noopener' title='Ouvrir le plan dans un nouvel onglet'>"
            f"<span class='i'>🖼️</span> Voir le plan</a>"
            f"<a class='btn' href='/rounds/{r.id}/plan?dl=1' title='Télécharger le plan'>"
            f"<span class='i'>⬇️</span> Télécharger</a>"
            f"</div>"
        )


    heading_html = f"""
      <div class="row" style="justify-content:space-between; align-items:center; gap:12px; margin-bottom:8px;">
        <h1 style="margin:0;">{r.name}</h1>
        {plan_btn}
      </div>
    """



    try:
        # On ne prend que les chronos VALIDÉS
        entries = (
            TimeEntry.query
            .filter_by(round_id=r.id, status="approved")
            .order_by(TimeEntry.created_at.desc())
            .all()
        )

       
        if not entries:
            return PAGE(f"{heading_html}{countdown_html}<p class='muted'>Aucun chrono validé pour le moment.</p>")


        # Sécuriser le calcul du final
        safe_entries, finals = [], []
        for e in entries:
            try:
                raw_ms = int(e.raw_time_ms or 0)
                pen = int(e.penalties or 0)
                fm = final_time_ms(raw_ms, pen)
                safe_entries.append((e, fm))
                finals.append(fm)
            except Exception:
                continue

        if not entries:
            return PAGE(f"{heading_html}{countdown_html}<p class='muted'>Aucun chrono validé pour le moment.</p>")



        best = min(finals) if finals else 0

        def row(i, e, fm):
            pct = (fm / best * 100.0) if fm > 0 and best > 0 else 0.0
            name = display_name(getattr(e, "user", None)) if hasattr(e, "user") else "—"
            nat = (getattr(e.user, "nationality", "") or "—").upper() if hasattr(e, "user") else "—"
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

        return PAGE(f"""
          <h1>{r.name}</h1>
          {heading_html}
          {countdown_html}
          {table}
        """)

    except Exception as e:
        return PAGE(f"<h1>{r.name}</h1><p class='muted'>Erreur: {e}</p>"), 500


@app.get("/admin/rounds/<int:round_id>/export.csv")
def admin_round_export_csv(round_id):
    if not db:
        return PAGE("<h1>Admin</h1><p class='muted'>DB non dispo.</p>"), 500
    u = current_user()
    if not is_admin(u):
        return PAGE("<h1>Accès refusé</h1><p class='muted'>Réservé aux administrateurs.</p>"), 403

    r = db.session.get(Round, round_id)
    if not r:
        return PAGE("<h1>Erreur</h1><p class='muted'>Manche introuvable.</p>"), 404

    # Récupère les chronos VALIDÉS uniquement
    entries = (
        TimeEntry.query
        .filter_by(round_id=r.id, status="approved")
        .order_by(TimeEntry.created_at.asc())
        .all()
    )
    if not entries:
        # CSV vide avec juste l’entête
        headers = ["Rang", "Pilote", "Nation", "Brut", "Pénalités", "Final", "% du meilleur", "Moto"]
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(headers)
        filename = f"resultats_round_{r.id}.csv"
        return Response(out.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename={filename}"})

    # Calcule les finals, tri par temps final croissant
    safe = []
    for e in entries:
        try:
            raw_ms = int(e.raw_time_ms or 0)
            pen = int(e.penalties or 0)
            fm = final_time_ms(raw_ms, pen)
            safe.append((e, fm))
        except Exception:
            continue

    if not safe:
        headers = ["Rang", "Pilote", "Nation", "Brut", "Pénalités", "Final", "% du meilleur", "Moto", "YouTube"]
        out = io.StringIO()
        csv.writer(out).writerow(headers)
        filename = f"resultats_round_{r.id}.csv"
        return Response(out.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename={filename}"})

    safe.sort(key=lambda t: t[1])
    best = safe[0][1] if safe else 0

    # Écriture CSV
    headers = ["Rang", "Pilote", "Nation", "Brut", "Pénalités", "Final", "% du meilleur", "Moto", "YouTube"]
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(headers)

    for i, (e, fm) in enumerate(safe, start=1):
        name = display_name(getattr(e, "user", None)) if hasattr(e, "user") else "—"
        nat = "—"
        if hasattr(e, "user") and getattr(e.user, "nationality", None):
            nat = (e.user.nationality or "—").upper()
        pct = (fm / best * 100.0) if best > 0 else 0.0
        writer.writerow([
            i,
            name,
            nat,
            ms_to_str(e.raw_time_ms),
            e.penalties,
            ms_to_str(fm),
            f"{pct:.2f}%",
            e.bike or "",
        ])

    # Nom de fichier propre
    base = f"resultats_round_{r.id}"
    try:
        # petit slug du nom
        slug = "".join(c if c.isalnum() else "_" for c in (r.name or ""))
        if slug:
            base = f"resultats_{slug}"
    except Exception:
        pass

    filename = f"{base}.csv"
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={filename}"})



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


@app.route("/admin/banner", methods=["GET", "POST"])
def admin_banner():
    if not db:
        return PAGE("<h1>Admin</h1><p class='muted'>DB non dispo.</p>")
    u = current_user()
    if not is_admin(u):
        return PAGE("<h1>Accès refusé</h1><p class='muted'>Réservé aux administrateurs.</p>"), 403

    if request.method == "POST":
        content = (request.form.get("content") or "").strip()
        is_active = True if request.form.get("is_active") == "on" else False
        if not content:
            return PAGE("<h1>Bandeau</h1><p class='muted'>Le contenu est obligatoire.</p>"), 400
        # on crée une nouvelle annonce (la plus récente active sera affichée)
        ann = Announcement(content=content, is_active=is_active)
        db.session.add(ann)
        db.session.commit()
        return redirect(url_for("admin_banner"))

    latest = Announcement.query.order_by(Announcement.created_at.desc()).limit(10).all()
    rows = "".join(
        f"<tr><td>{a.id}</td><td>{a.created_at:%d/%m/%Y %H:%M}</td><td>{'✅' if a.is_active else '—'}</td><td>{a.content}</td></tr>"
        for a in latest
    ) or "<tr><td colspan='4' class='muted'>Aucune annonce.</td></tr>"

    return PAGE(f"""
      <h1>Admin — Bandeau d'annonces</h1>
      <form method="post" class="form">
        <label>Contenu (HTML simple autorisé)
          <textarea name="content" rows="3" placeholder="Ex : <strong>WP Challenge</strong> — Manche 2 ce samedi, briefing à 9h." required></textarea>
        </label>
        <label class="row" style="gap:8px;">
          <input type="checkbox" name="is_active" checked> Activer cette annonce
        </label>
        <button class="btn" type="submit">Publier</button>
      </form>

      <h2 style="margin-top:16px;">Dernières annonces</h2>
      <table class="table">
        <thead><tr><th>#</th><th>Date</th><th>Active</th><th>Contenu</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
    """)

@app.get("/privacy")
def privacy():
    return PAGE("""
      <h1>Politique de Confidentialité</h1>
      <p>Ce site collecte uniquement les données nécessaires au bon déroulement du challenge (adresse e-mail, nationalité et chronos).</p>
      <p>Aucune donnée n’est partagée à des tiers.</p>
      <p>Pour toute demande de suppression ou de modification, contactez <a href="mailto:westpistards@gmail.com">westpistards@gmail.com</a>.</p>
    """)

@app.get("/apple-touch-icon.png")
def apple_touch_icon_root():
    # Sert l’icône iOS 180×180 depuis /static/img/
    return send_from_directory(os.path.join(app.static_folder, "img"), "icon-180.png")

@app.get("/favicon.ico")
def favicon_root():
    # Sert un favicon (on réutilise l’icône 192×192)
    return send_from_directory(os.path.join(app.static_folder, "img"), "icon-192.png")

@app.post("/my/times/<int:time_id>/delete")
def my_time_delete(time_id):
    if not db:
        return PAGE("<h1>Erreur</h1><p class='muted'>DB non dispo.</p>"), 500

    u = current_user()
    if not u:
        return redirect(url_for("login"))

    e = db.session.get(TimeEntry, time_id)
    if not e:
        return PAGE("<h1>Erreur</h1><p class='muted'>Chrono introuvable.</p>"), 404

    # Sécurité: seul le propriétaire OU un admin peut supprimer
    if e.user_id != u.id and not is_admin(u):
        return PAGE("<h1>Accès refusé</h1><p class='muted'>Action non autorisée.</p>"), 403

    try:
        db.session.delete(e)
        db.session.commit()
        return redirect(url_for("profile"))
    except Exception as ex:
        db.session.rollback()
        return PAGE(f"<h1>Erreur</h1><p class='muted'>Suppression impossible : {ex.__class__.__name__}</p>"), 500

@app.get("/credits")
def credits():
    return PAGE("""
      <h1>Crédits photo & vidéo</h1>
      <p>Photos : <a href="https://www.facebook.com/profile.php?id=100062921426923" target="_blank">Suzie Fontaine Photos</a></p>
      <p>Vidéos : <a href="https://youtu.be/5OXhfkt5BqY?si=bWjH6IVx65XOxVkQ">Merci Alaaaaaiiiiiinnnnn</a></p>
    """)

@app.get("/rounds/<int:round_id>/plan")
def round_plan(round_id):
    if not db:
        return PAGE("<h1>Erreur</h1><p class='muted'>DB non dispo.</p>"), 500
    r = db.session.get(Round, round_id)
    if not r or not getattr(r, "plan_data", None):
        return PAGE("<h1>Plan</h1><p class='muted'>Aucun plan pour cette manche.</p>")
    disposition = "attachment" if request.args.get("dl") else "inline"
    return Response(
        r.plan_data,
        mimetype=(r.plan_mime or "image/png"),
        headers={"Content-Disposition": f"{disposition}; filename=\"{r.plan_name or f'plan_{round_id}'}\""}
    )


@app.get("/trace/download")
def trace_download():
    # Sert l'image du tracé en téléchargement (Content-Disposition: attachment)
    import os
    path = os.path.join(app.static_folder or "static", "img", "traceWP.jpg")
    try:
        with open(path, "rb") as f:
            data = f.read()
    except FileNotFoundError:
        return PAGE("<h1>Tracé</h1><p class='muted'>Image introuvable.</p>"), 404
    return Response(
        data,
        mimetype="image/jpeg",
        headers={"Content-Disposition": 'attachment; filename="traceWP.jpg"'}
    )

@app.get("/admin/users")
def admin_users():
    if not db:
        return PAGE("<h1>Admin</h1><p class='muted'>DB non dispo.</p>")
    u = current_user()
    if not is_admin(u):
        return PAGE("<h1>Accès refusé</h1><p class='muted'>Réservé aux administrateurs.</p>"), 403

    users = User.query.order_by(getattr(User, "created_at", User.id).desc()).all()

    def row_html(x):
        pid = x.id
        pseudo = (getattr(x, "pseudo", None) or f"Pilote #{pid}")
        nat = (getattr(x, "nationality", None) or "—")
        dt = getattr(x, "created_at", None)
        dt_h = dt.strftime("%d/%m/%Y %H:%M") if dt else "—"
        total = db.session.query(TimeEntry).filter_by(user_id=pid).count()
        ok = db.session.query(TimeEntry).filter_by(user_id=pid, status="approved").count()

        return f"""
        <li class="card">
          <div class="row" style="justify-content:space-between; align-items:center; gap:8px;">
            <div>
              <strong>{pseudo}</strong>
              &middot; <span class="muted">Nationalité&nbsp;: {nat}</span>
              &middot; <span class="muted">Inscription&nbsp;: {dt_h}</span>
              &middot; <span class="muted">Chronos&nbsp;: {total} (validés&nbsp;: {ok})</span>
            </div>
            <div class="row" style="gap:8px;">
              <a class="btn outline" href="/admin/users/{pid}/times" title="Voir ses chronos">Voir ses chronos</a>
              <form method="post" action="/admin/users/{pid}/delete"
                    onsubmit="return confirm('Supprimer définitivement ce profil et tous ses chronos ?');"
                    style="display:inline;">
                <button class="icon-btn danger" type="submit" title="Supprimer le profil" aria-label="Supprimer le profil">
                  <span class="i">✖</span>
                </button>
              </form>
            </div>
          </div>
        </li>
        """

    rows = "\n".join(row_html(x) for x in users) if users else "<p class='muted'>Aucun inscrit pour le moment.</p>"

    return PAGE(f"""
      <h1>Inscrits</h1>
      <ul class="list">
        {rows}
      </ul>
    """)


@app.get("/admin/users/<int:user_id>/times")
def admin_user_times(user_id):
    if not db:
        return PAGE("<h1>Admin</h1><p class='muted'>DB non dispo.</p>")
    u = current_user()
    if not is_admin(u):
        return PAGE("<h1>Accès refusé</h1><p class='muted'>Réservé aux administrateurs.</p>"), 403

    pilot = db.session.get(User, user_id)
    if not pilot:
        return PAGE("<h1>Inscrits</h1><p class='muted'>Pilote introuvable.</p>"), 404

    items = (
        TimeEntry.query
        .filter_by(user_id=user_id)
        .order_by(getattr(TimeEntry, "created_at", TimeEntry.id).desc())
        .all()
    )

    def row_html(t):
        # Infos minimales et robustes (pas d’email)
        rid = getattr(t, "round_id", "—")
        st = getattr(t, "status", "—")
        dt = getattr(t, "created_at", None)
        dt_h = dt.strftime("%d/%m/%Y %H:%M") if dt else "—"
        # Libellé statut (facultatif)
        label = ("Validé" if st == "approved" else
                 "Rejeté" if st == "rejected" else
                 "Inactif" if st in ("inactive", "superseded") else
                 "En attente")
        badge = f"<span class='badge {('approved' if st=='approved' else ('rejected' if st=='rejected' else ('inactive' if st in ('inactive','superseded') else 'pending')))}'>{label}</span>"

        return f"""
        <li class="card">
          <div class="row" style="justify-content:space-between; align-items:center; gap:8px;">
            <div>
              <strong>Round #{rid}</strong>
              &middot; {badge}
              &middot; <span class="muted">{dt_h}</span>
            </div>
          </div>
        </li>
        """

    rows = "\n".join(row_html(t) for t in items) if items else "<p class='muted'>Aucun chrono pour ce pilote.</p>"

    return PAGE(f"""
      <h1>Chronos – Pilote #{pilot.id}</h1>
      <p><a class="btn outline" href="/admin/users">&larr; Retour aux inscrits</a></p>
      <ul class="list">
        {rows}
      </ul>
    """)

@app.post("/admin/users/<int:user_id>/delete")
def admin_user_delete(user_id):
    if not db:
        return PAGE("<h1>Admin</h1><p class='muted'>DB non dispo.</p>")
    u = current_user()
    if not is_admin(u):
        return PAGE("<h1>Accès refusé</h1><p class='muted'>Réservé aux administrateurs.</p>"), 403

    pilot = db.session.get(User, user_id)
    if not pilot:
        return PAGE("<h1>Inscrits</h1><p class='muted'>Pilote introuvable.</p>"), 404

    try:
        from sqlalchemy import delete
        db.session.execute(delete(TimeEntry).where(TimeEntry.user_id == user_id))
        db.session.delete(pilot)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return PAGE("<h1>Erreur</h1><p class='muted'>Suppression impossible pour le moment.</p>"), 500

    return redirect(url_for("admin_users"))



if __name__ == "__main__":
    if db:
        with app.app_context():
            db.create_all()
    app.run(host="0.0.0.0", port=5000, debug=True)

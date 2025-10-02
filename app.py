from flask import Flask, render_template

app = Flask(__name__, static_folder='static', template_folder='templates')

@app.get("/")
def index():
    # variables minimales attendues par le template
    return render_template('index.html', user=None, open_rounds=[])

@app.get("/rounds")
def rounds_list():
    return render_template('rounds.html', rounds=[], user=None)

@app.get("/register")
def register():
    return render_template('register.html')

@app.get("/login")
def login():
    return render_template('login.html')

@app.get("/profile")
def profile():
    # simple placeholder (pas connecté)
    return "Connecte-toi d'abord (placeholder)."

@app.get("/submit")
def submit_time():
    # aucune manche pour l'instant
    return render_template('submit_time.html', rounds=[])

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)

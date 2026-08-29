"""Blueprint login — routes d'authentification (login, logout).

Contient les deux routes publiques permettant à l'utilisateur de se connecter
avec un mot de passe (hashé SHA-256) et de se déconnecter (clear session).
"""

import hashlib

from flask import Blueprint, render_template, request, session, redirect, url_for

from config import MOT_DE_PASSE_HASH

login_bp = Blueprint('login', __name__)


@login_bp.route('/login', methods=['GET', 'POST'])
def login():
    erreur = None
    if request.method == 'POST':
        mdp_saisi = request.form.get('password', '')
        if hashlib.sha256(mdp_saisi.encode()).hexdigest() == MOT_DE_PASSE_HASH:
            session['connecte'] = True
            return redirect(url_for('index'))
        else:
            erreur = "Mot de passe incorrect."
    return render_template('login.html', erreur=erreur)


@login_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login.login'))

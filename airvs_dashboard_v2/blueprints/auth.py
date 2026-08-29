"""blueprints/auth.py -- Decorateurs d'authentification AIRVS.

Extraits de app.py (Phase 1.3) pour eviter les imports circulaires
lors de la migration vers Flask Blueprints.

- login_requis : protege les routes web (session Flask)
- token_requis : protege les API machine-a-machine (Bearer token)

Ce module importe uniquement des constantes depuis config.py
(pas de logique Flask route ici, donc pas de risque d'import circulaire).
"""

from functools import wraps

from flask import session, redirect, url_for, request, jsonify

from config import API_TOKEN


def login_requis(f):
    """Decorator : redirige vers /login si l'utilisateur n'est pas en session."""
    @wraps(f)
    def fonction_protegee(*args, **kwargs):
        if 'connecte' not in session:
            return redirect(url_for('login.login'))
        return f(*args, **kwargs)
    return fonction_protegee


def token_requis(f):
    """Decorator : verifie le token Bearer ou le query param ?token=.

    Utilise par les endpoints machine-a-machine appeles par le worker Ubuntu.
    Verifie :
      1. Le header Authorization: Bearer <token>
      2. Le parametre de query ?token=<token> (utile pour debug navigateur)
    """
    @wraps(f)
    def fonction_protegee_token(*args, **kwargs):
        token = None
        # 1. Header Authorization: Bearer <token>
        auth = request.headers.get('Authorization', '')
        if auth.startswith('Bearer '):
            token = auth[7:]
        # 2. Query parameter ?token=<token>
        if not token:
            token = request.args.get('token')
        if not token or token != API_TOKEN:
            return jsonify({
                'erreur': 'Token manquant ou invalide',
                'code': 'UNAUTHORIZED'
            }), 401
        return f(*args, **kwargs)
    return fonction_protegee_token

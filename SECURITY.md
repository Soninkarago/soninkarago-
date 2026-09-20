# SoninkaraGo — Sécurité opérationnelle

## Contrôles en place
- HTTPS/TLS via l’hébergement.
- Cookies de session HttpOnly, Secure, SameSite=Strict.
- Rate limiting partagé PostgreSQL sur endpoints sensibles.
- Secrets chargés depuis variables d’environnement.
- PIN chauffeurs dérivés/hachés.
- Validation HMAC des callbacks PayTech.
- Devis signés côté serveur.
- Journal d’audit des actions sensibles.
- Journal idempotent des événements de paiement.
- Accès aux pièces chauffeurs réservé à l’administration.

## Secrets
Ne jamais stocker dans GitHub : DATABASE_URL, AUTH_SECRET, ADMIN_PASSWORD, PAYTECH_API_KEY,
PAYTECH_API_SECRET, GOOGLE_MAPS_API_KEY, clés VAPID.

Rotation recommandée : tous les 90 jours et immédiatement après tout doute de compromission.

## Incidents
1. Isoler la fonctionnalité ou le compte concerné.
2. Préserver les logs et événements d’audit.
3. Révoquer/faire tourner les secrets compromis.
4. Évaluer les données/personnes concernées.
5. Notifier la CDP et/ou les personnes lorsque le cadre applicable l’exige.
6. Documenter cause, correction et prévention.

## Limite volontaire de cette version
L’authentification administrateur par mot de passe n’est pas modifiée dans V35.
Une MFA/passkey reste recommandée avant montée en charge importante.

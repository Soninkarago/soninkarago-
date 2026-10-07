# Récupération du PIN chauffeur par e-mail

La récupération reste dans l’application. Le chauffeur indique son téléphone et son adresse e-mail de secours vérifiée, reçoit un code de six chiffres et choisit son nouveau PIN. Aucun PIN n’est envoyé par e-mail.

## Configuration

Réutilise la configuration SMTP existante CONTACT_SMTP_HOST, CONTACT_SMTP_USER, CONTACT_SMTP_PASSWORD et ses paramètres TLS. Aucun prestataire SMS n’est nécessaire. Le serveur refuse le service si le transport sécurisé n’est pas configuré.

## Sécurité

Code valable dix minutes, usage unique, cinq essais maximum, limitation des demandes. Les codes sont conservés sous forme HMAC. La réponse à une demande reste identique pour une adresse inconnue. Le code est envoyé uniquement à l’adresse vérifiée du compte. La modification du PIN invalide les anciennes sessions.

## Inscription et comptes existants

Le nouveau parcours mobile vérifie l’e-mail avant l’inscription. Les comptes existants ajoutent leur e-mail de secours avec leur PIN actuel et le code reçu. Les anciennes versions restent compatibles avec l’inscription existante.

## Validation

Tests automatisés : inscription avec preuve liée au téléphone, envoi au bon destinataire, code faux ou expiré, limite d’essais, usage unique, changement de PIN et révocation des anciennes sessions. Une réception réelle d’e-mail et le parcours sur iPhone restent à vérifier après déploiement.

# SoninkaraGo — Runbook exploitation

## Objectifs
- RPO cible : 24 h maximum.
- RTO cible : 4 h maximum.
Ces objectifs doivent être alignés avec le plan Render réellement souscrit.

## Sauvegarde
1. Utiliser une sauvegarde PostgreSQL managée disponible sur le plan Render.
2. Réaliser en plus un export `pg_dump` chiffré vers un emplacement sécurisé hors dépôt GitHub.
3. Tester une restauration au minimum une fois par trimestre.
4. Journaliser la date, l’opérateur et le résultat du test.

## Restauration
- Créer une base de restauration séparée.
- Restaurer le dump.
- Exécuter les vérifications d’intégrité : nombre de chauffeurs, courses, paiements, contraintes.
- Vérifier `/api/ready`.
- Basculer seulement après validation.

## Paiements
- Consulter `/api/admin/payments/reconciliation` chaque jour pendant le lancement.
- Toute anomalie doit être rapprochée avec le tableau de bord PayTech avant correction manuelle.
- Ne jamais modifier un solde chauffeur sans trace d’audit et justificatif.

## Chauffeurs
- Vérifier les pièces applicables.
- Ajouter une note de contrôle.
- Valider seulement après le bouton « Documents vérifiés ».
- Refus : saisir un motif.

## Supervision
- `/api/health` : état de service.
- `/api/ready` : disponibilité DB + configuration critique.
- Conserver les logs Render et le journal d’audit.

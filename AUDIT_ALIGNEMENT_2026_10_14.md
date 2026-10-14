# SoninkaraGo — Corrections après comparaison Uber / Yango

14 octobre 2026

## Livrable

- Aide native rattachée à une course, avec création idempotente, brouillon sécurisé, reprise d’envoi, numéro de dossier, statut et réponse administrateur. Accessible pendant la course et depuis les nouvelles courses archivées sur cet appareil.
- Rapport de course privé calculé sur l’état serveur, avec montant total, confirmation du paiement, acompte et montant restant à confirmer. Le partage exclut les clés privées et les coordonnées téléphoniques.
- Conséquences de l’annulation récupérées avant la confirmation. Annulation automatique sans frais additionnels quand les règles existantes l’autorisent. Pour un paiement ou une course déjà acceptée, la demande est traitée par l’assistance ; la course n’est pas annoncée annulée ou remboursée après le seul envoi d’un message.
- Recherche prolongée et risque de paiement sans attribution expliqués dans le suivi. Une seconde commande n’est pas encouragée.
- Alternative explicite dans le navigateur natif sécurisé pour les étapes Wave / Orange Money qui nécessitent une autre application. L’état du serveur reste la référence du paiement.
- Code de montée pour les nouvelles réservations minicar. Confirmation du code sans modifier les états acompte / solde ; clôture protégée ; anciennes courses compatibles. Une ancienne application chauffeur qui ne sait pas gérer le code minicar est invitée à se mettre à jour avant acceptation.
- Prix, commission et gain prévu affichés séparément côté chauffeur. Solde identifié comme solde de recharge.
- Accès aux rapports et réclamations conservé séparément des récapitulatifs dans le stockage sécurisé. Nettoyage reprenable après interruption et suppression des anciens accès au-delà de 20 courses.

## Validation

159 tests serveur isolés réussis, dont cinq nouveaux contrôles sur les rapports, les droits d’accès, la reprise d’une réclamation, la réponse administrative, l’annulation et la montée minicar. Douze suites mobiles réussies, dont les scénarios de reprise et de course, réclamations, partage sans jeton et nettoyage interrompu. Syntaxe des 24 fichiers JavaScript contrôlée.

Les tests utilisent des données fictives, une base SQLite et des services simulés. Aucun paiement, SMS, client ou dossier réel utilisé. Les paiements sur iPhone, le GPS écran verrouillé, les notifications et le rendu natif restent à valider.

## Périmètre conservé

Espèces, Wave et Orange Money uniquement. Tarifs, commission et couverture nationale du Sénégal conservés. Aucun build Expo/EAS, crédit de build ou envoi TestFlight lancé.

## Limites explicites

Le propriétaire a demandé de ne pas créer de service SMS. La préparation SMS a été retirée entièrement. Le téléphone reste non vérifié. Un compte facultatif récupérable par code e-mail est ajouté dans la mise à jour suivante ; voir ACCOUNT_REFUNDS_VALIDATION.md.

La mise à jour suivante ajoute les demandes de remboursement pré-prise en charge, leur rapprochement prestataire et leur suivi ; voir ACCOUNT_REFUNDS_VALIDATION.md. Les résultats incertains et les espèces restent à vérifier par l’assistance. Les nouveaux rapports sont des rapports de course, pas des reçus bancaires ni des factures.

Les appels restent directs, avec numéros visibles. Aucun centre humain d’urgence 24 h/24, masquage des appels, délai garanti d’attribution ou garantie de disponibilité terrain n’est annoncé. La parité intégrale avec Uber n’est pas établie.

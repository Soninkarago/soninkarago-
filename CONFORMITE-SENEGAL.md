# SoninkaraGo — Dossier conformité Sénégal

Version technique : SN-2026-09-v2
Exploitant : COULIBALY ISSA (Entreprise individuelle)
Nom commercial : SONIN KARAGO
RCCM : SN.DKR.2026.A.32970
NINEA : 013329778
Adresse établissement principal : Pikine Rue 10, Villa n°55, Pikine, Dakar, Sénégal
Contact : contact@soninkarago.sn — +221 77 408 33 87

## Intégré dans cette version
- Mentions légales renseignées avec RCCM, NINEA, forme juridique, activité, adresse et contact.
- CGU et politique de confidentialité mises à jour.
- Consentement CGU/confidentialité et consentement de géolocalisation enregistrés avec version de conformité.
- Dossier chauffeur adapté par catégorie : dossier complet pour la voiture taxi ; pour Moto-taxi et 3 roues, seules les pièces légalement applicables à la catégorie et à la zone sont demandées, avec contrôle humain avant activation.
- Un chauffeur reste en attente tant que son dossier n’est pas marqué vérifié par l’admin.
- L’API refuse l’approbation d’un chauffeur dont le dossier réglementaire n’a pas été vérifié.
- Page publique pour l’exercice des droits sur les données.

## Démarches externes encore obligatoires avant exploitation commerciale
1. Accomplir les formalités CDP applicables aux traitements clients/chauffeurs, géolocalisation et éventuels transferts internationaux.
2. Obtenir/faire confirmer auprès de l’autorité sénégalaise compétente les autorisations nécessaires au modèle de transport utilisé (voiture, moto-taxi, trois-roues, interurbain).
3. Ne valider un chauffeur qu’après contrôle des justificatifs originaux/fiables et de leur validité.
4. Vérifier les obligations fiscales, facturation/reçus et conservation comptable avec le centre fiscal ou le conseil comptable.
5. Conserver la preuve des versions de CGU/politique acceptées et des consentements.
6. Réviser les textes dès changement réglementaire, tarif réglementé ou nouvelle catégorie de transport.

Aucune version logicielle ne peut, à elle seule, remplacer une autorisation administrative ou garantir l’absence totale de risque juridique.


## Tarification locale moto - référence commerciale
- Jusqu’à 2 km : 200 F CFA (trajet interne / très courte distance).
- Plus de 2 km à 15 km : 2 000 F CFA.
- Plus de 15 km à 35 km : 3 000 F CFA.
- La distance provient de l’itinéraire calculé par le service cartographique ; le tarif est fixé par SoninkaraGo et ne doit pas être présenté comme un tarif officiel/homologué.
- Exemple de référence : Moudéry → Diawara (~6–7 km) = 2 000 F CFA.


## V34 — Documents chauffeurs par catégorie
- Voiture taxi : dossier réglementaire complet obligatoire avant vérification.
- Moto-taxi / 3 roues : champs non applicables non bloquants ; toute référence fournie est contrôlée et toute date fournie doit être valide.
- Aucun chauffeur n'est activé automatiquement : la vérification manuelle de conformité reste obligatoire.


## V35 — Durcissement audit / exploitation
- Journal d’audit append-only pour actions sensibles (connexion admin, vérification/validation/refus chauffeur, suppression de compte).
- Registre idempotent des callbacks PayTech pour éviter le retraitement d’un même événement.
- Endpoint de rapprochement administrateur sur 7 jours pour détecter les anomalies de statut de paiement.
- Journal détaillé des contrôles réglementaires chauffeurs avec checklist, note, décision et horodatage.
- Health/readiness enrichi avec base de données, configuration cartographie/paiement et objectifs RPO/RTO.
- Runbook de sauvegarde/restauration et de gestion d’incident ajouté.
- L’authentification administrateur existante reste volontairement inchangée dans cette version, conformément à la décision du responsable.

## V36 — séparation stricte des catégories de transport
- Trois entrées client distinctes : Voiture classique, Villages, Minicar.
- Catégorie Villages : seulement Moto-taxi, Taxi local et 3 roues.
- Catégorie Minicar : seulement les liaisons Minicar 14 places.
- Catégorie Voiture classique : seulement la course point A → point B avec géolocalisation/adresses.
- Les options incompatibles sont masquées ET désactivées dans le sélecteur afin d’éviter les mélanges de catégories, y compris via clavier, restauration navigateur ou autofill.
- Le mot de passe administrateur n’est pas modifié.

## V37 — module publicitaire first-party
- Campagnes vendues directement par SoninkaraGo, sans régie publicitaire tierce activée.
- Emplacements : accueil client, après réservation, espace chauffeur.
- Ciblage limité à la catégorie de service (tous / urbain / villages / minicar / chauffeurs), sans profilage individuel.
- Mesure des impressions et clics avec déduplication d’impression par session/jour.
- Mention « Partenaire SoninkaraGo » visible.
- Journal d’audit des créations/changements de statut.
- Mise à jour de la politique de confidentialité.
- Toute activation future d’AdMob/AdSense impose une nouvelle revue de confidentialité et, si nécessaire, une mise à jour du dossier CDP.

## V38 — grille grands comptes
- Ajout d'une grille tarifaire interne de référence pour les annonceurs importants.
- Les nouveaux montants correspondent aux bases V37 majorées de 20 %.
- La grille sert d'aide commerciale ; le prix réellement négocié est enregistré dans chaque campagne.
- Aucun changement de profilage ou de collecte publicitaire.

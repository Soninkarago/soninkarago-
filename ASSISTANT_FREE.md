# Assistance gratuite préparée — 8 octobre 2026

État : code local préparé et testé. Non publié, non déployé, non installé sur iPhone. Aucun appel réel au fournisseur et aucun compte créé dans cette étape.

Choix à évaluer : Groq Free, modèle de production openai/gpt-oss-120b. Les quotas ne permettent pas de promettre un accès illimité ni une disponibilité permanente. Les limites effectives doivent être vérifiées dans le compte. Aucun passage automatique à un fournisseur ou abonnement payant.

Première portée : recherche sémantique parmi des rubriques d’aide SoninkaraGo approuvées. L’IA sélectionne un identifiant ; le serveur fournit le texte validé correspondant. Elle ne produit aucune décision juridique, intervention d’urgence, autorisation de transport, prix, confirmation bancaire, remboursement ou action sur une course. Elle ne constitue pas encore un agent autonome ni une base exhaustive de droit sénégalais. Les réponses doivent être révisées à chaque évolution métier ; elles ne se mettent pas automatiquement à jour.

Activation côté serveur uniquement : créer/connecter un compte Free sans moyen de paiement, vérifier le plan gratuit, activer Zero Data Retention, saisir GROQ_API_KEY dans l’environnement sécurisé du serveur, puis ASSISTANT_FREE_ACCOUNT_CONFIRMED=yes et ASSISTANT_ZDR_CONFIRMED=yes. Ces indicateurs documentent une vérification opérateur et ne vérifient pas le plan via une API. Ne jamais placer la clé dans le mobile, un commit ou le chat. Activer seulement après un test réel contrôlé de la réponse, du français, des quotas et des réglages du compte. Le fournisseur traite les questions hors du Sénégal ; vérifier les obligations applicables avant tout envoi de données personnelles. Aucun dossier, jeton de suivi, GPS ou paiement n’est envoyé automatiquement. Le filtrage des questions n’est pas une anonymisation complète.

Les rubriques fonctionnent sans clé ; elles sont explicitement nommées aide validée, pas réponses produites par IA. Les erreurs réseau et quotas proposent ces rubriques. Accord explicite requis avant transmission d’une question. Maximum 500 caractères, deux requêtes simultanées, huit par minute/IP, délai fournisseur douze secondes, aucune relance automatique, aucun outil externe. Une réponse inconnue, tronquée ou mal formée est rejetée ou remplacée par une demande de vérification. Une réponse de l’assistant ne crée pas de ticket d’assistance.

Vérifications : suite serveur 130 tests réussis (21 nouveaux tests assistant), harnais mobile 21 scénarios réussis, test de rendu React réussi (rubriques sans IA, double clic, consentement, modal native), régression navigation intégrée réussie. Fournisseur simulé uniquement. Ni essai réel Groq ni installation native iPhone prouvés.

Sources officielles consultées :
- https://console.groq.com/docs/models
- https://console.groq.com/docs/rate-limits
- https://console.groq.com/docs/your-data
- https://console.groq.com/docs/billing-faqs
- https://console.groq.com/docs/text-chat
- https://console.groq.com/docs/model/openai/gpt-oss-120b

## Deux remplaçantes préparées

Ordre : GPT-OSS 120B (Groq), GPT-OSS 20B (Groq), Gemini 2.5 Flash (Google, accord distinct obligatoire). Les deux premières dépendent du même fournisseur. Le troisième couvre une panne de Groq avec une portée réduite : Google ne reçoit que des identifiants thématiques issus d’un vocabulaire fermé, jamais le texte original de la question. Les échanges gratuits Google peuvent servir à améliorer ses produits. Ce secours exige une clé GEMINI_API_KEY, ASSISTANT_GOOGLE_FREE_CONFIRMED=yes et ASSISTANT_GOOGLE_GENERIC_ONLY_CONFIRMED=yes après vérification du compte gratuit sans facturation.

Maximum trois essais par question, sept secondes par essai, budget de boucle vingt-quatre secondes ; pas de relance du même modèle. Pause trente secondes après erreur, soixante après quota 429. La limitation est locale au processus serveur ; les quotas fournisseur restent applicables à l’ensemble du compte. Après échec des moteurs connectés : réponse indisponible, rubriques manuelles conservées. Aucun fournisseur non configuré n’est appelé. Une réponse invalide ou tronquée déclenche le moteur suivant. Le basculement a été testé avec des fournisseurs simulés ; aucun succès réel des trois IA n’est démontré.

Sources supplémentaires : https://ai.google.dev/gemini-api/docs/pricing ; https://ai.google.dev/gemini-api/terms ; https://ai.google.dev/api/generate-content .

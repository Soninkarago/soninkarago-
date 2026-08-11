# SoninkaraGo

Prêt pour GitHub et Render.

## GitHub
Crée un dépôt nommé `soninkarago` puis téléverse tous les fichiers de ce ZIP à la racine du dépôt.

## Render
Crée un Web Service depuis GitHub, choisis le dépôt `soninkarago`, puis utilise :
- Runtime : Python
- Start command : `python server.py`
- Health check : `/api/health`
- Plan : Free si proposé

## Domaine
Une fois le service en ligne, ajoute `soninkarago.sn` comme domaine personnalisé dans Render. Render donnera les DNS exacts à mettre dans le fichier de zone NETIM.

## Important
La base SQLite convient aux tests. Pour un vrai lancement, il faudra une base persistante et sécurisée. Les choix Wave et Orange Money sont affichés mais aucun paiement réel n'est encore exécuté.

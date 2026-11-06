# 💿 Music Tagger

Petit outil web (Docker) qui répare les métadonnées d'une bibliothèque musicale
à partir de **Deezer** : titres propres, numéros de piste/disque, date, genres,
label, ISRC, code-barres, artistes en featuring, et **pochette en haute
résolution** (intégrée aux fichiers + `cover.jpg` dans le dossier).

Pourquoi Deezer ? C'est le plus ouvert des trois : son API publique de recherche,
d'albums et de pistes fonctionne **sans compte ni clé**. Spotify exige des
identifiants OAuth, et YouTube Music n'a pas d'API officielle.

Formats pris en charge : FLAC, MP3, M4A/AAC, OGG Vorbis, Opus.

## Démarrage

1. Dans `docker-compose.yml`, remplace `/chemin/vers/ta/musique` par ton dossier
   et mets dans `user:` l'UID:GID du propriétaire des fichiers (`id` dans un shell).
2. Lance :

   ```bash
   docker compose up -d --build
   ```

3. Ouvre <http://localhost:8080> (ou `http://ip-du-serveur:8080`).

> ⚠️ Pas d'authentification : à garder sur ton réseau local (ou derrière ton
> reverse proxy avec auth). Ne l'expose pas tel quel sur Internet.

## Utilisation

1. **Scan** : fait automatiquement au démarrage (bouton « Rescanner » sinon).
   Un dossier = un album. Si un fichier n'a pas de tag album/artiste, ils sont
   déduits du chemin (`Artiste/Album/` ou `Artiste - Album/`), et le titre du
   nom de fichier (`01 - Titre.flac`).
2. **Tout matcher** : cherche chaque album sur Deezer, puis rapproche chaque
   fichier d'une piste (similarité du titre + durée + n° de piste). Chaque album
   reçoit un score de **confiance**.
3. **Vérifie** les albums douteux (filtre « Problèmes ») :
   - « Chercher un autre album… » : recherche libre, ou colle directement une
     URL d'album Deezer (`https://www.deezer.com/fr/album/302127`) ;
   - corrige une piste à la main avec la liste déroulante de la colonne
     « Piste Deezer ».
4. **Écrire les tags** album par album, ou **Appliquer** en lot tous les albums
   au-dessus du seuil de confiance (85 % par défaut).

### Options d'écriture

| Option | Effet |
|---|---|
| Remplacer / Compléter | « Remplacer » prend titres/artistes/album de Deezer. « Compléter » garde tes titre/artiste/album/n° actuels et ajoute seulement ce qui manque. |
| Intégrer la pochette | Image avant (type 3) intégrée dans chaque fichier. |
| Créer cover.jpg | Seulement s'il n'y a pas déjà de `cover.*` / `folder.*` dans le dossier. |
| Fichiers non rapprochés | Un fichier sans piste Deezer correspondante (piste bonus du CD…) reçoit quand même les infos d'album + la pochette. |
| Renommer | `01 - Titre.flac`, ou `1-01 - Titre.flac` pour les albums multi-disques. Désactivé par défaut. |

### Annuler

Avant chaque écriture, les tags d'origine sont sauvegardés dans
`./data/backups/`. Le bouton **Annuler (restaurer)** remet les tags texte et
les noms de fichiers d'origine, retire la pochette ajoutée et le `cover.jpg` créé.

## Tags écrits

`TITLE`, `ARTIST` (« A feat. B »), `ARTISTS` (multi-valeur), `ALBUM`,
`ALBUMARTIST`, `TRACKNUMBER`/`TRACKTOTAL`, `DISCNUMBER`/`DISCTOTAL`, `DATE`,
`GENRE` (multi-valeur), `LABEL`, `ISRC`, `BARCODE`, `RELEASETYPE`, `BPM`,
`DEEZER_ALBUM_ID`, `DEEZER_TRACK_ID` (équivalents ID3 / MP4 pour MP3 et M4A).

Les albums qui ont déjà un `DEEZER_ALBUM_ID` apparaissent comme « taggés » au
scan suivant et ne sont pas re-matchés par « Tout matcher ».

Pour Jellyfin / Navidrome / Plex : lance un rescan de la bibliothèque musicale
(dans Jellyfin, « Remplacer toutes les métadonnées ») une fois le tagging fini.

## Variables d'environnement

| Variable | Défaut | Rôle |
|---|---|---|
| `MUSIC_DIR` | `/music` | Racine de la bibliothèque dans le conteneur |
| `DATA_DIR` | `/data` | Sauvegardes des tags d'origine |
| `COVER_SIZE` | `1200` | Taille de pochette demandée à Deezer (1000 toujours dispo, retour automatique à 1000 sinon) |
| `AUTO_SCAN` | `1` | Scanner au démarrage |

## Limites

- L'API Deezer est limitée à 50 requêtes / 5 s : l'outil espace ses appels,
  compte quelques secondes par album (recherche, puis une requête par piste à l'écriture).
- Une édition spécifique de CD (bonus, remaster) peut manquer sur Deezer :
  choisis l'édition la plus proche, puis « Compléter » pour garder tes titres.
- Les correspondances sont gardées en mémoire : un redémarrage du conteneur
  oblige à relancer « Tout matcher » pour les albums pas encore écrits.

## Développement sans Docker

```bash
pip install -r requirements.txt
MUSIC_DIR=~/Musique DATA_DIR=./data uvicorn app.main:app --port 8080
```

Made with AI. Claude Opus 5.5
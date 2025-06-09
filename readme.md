# Interface de Tracking UWB

Ce projet est une application web complète pour la visualisation en temps réel de données de suivi Ultra-Wideband (UWB). Il se compose d'un backend Python (Flask) qui reçoit les données des capteurs via UDP, calcule les positions 3D, et les projette sur un plan 2D configurable. Le frontend est une interface web dynamique qui affiche la position des trackers (tags) sur une carte 2D, fournit des informations détaillées et permet la configuration complète du système.

## Fonctionnalités

*   **Visualisation Temps Réel :** Affiche la position de multiples trackers UWB sur une carte 2D avec une faible latence.
*   **Calcul de Position 3D :** Utilise la multilatération pour calculer la position 3D des trackers à partir des distances mesurées par les ancres.
*   **Projection 2D :** Projette les positions 3D sur un plan 2D (l'écran) pour une visualisation intuitive.
*   **Calibration d'Écran Assistée :** Un assistant guide l'utilisateur pour calibrer la position et les dimensions de la surface de projection en 10 points.
*   **Configuration Manuelle :** Permet de définir manuellement la taille et la position 3D de l'écran si la calibration n'est pas souhaitée.
*   **Configuration des Ancres :** Interface pour définir et sauvegarder les coordonnées 3D de chaque ancre.
*   **Interface Moderne :** UI construite avec Tailwind CSS pour un design clair et responsive.
*   **Lissage de Mouvement :** Utilise un filtre "One-Euro" pour stabiliser et fluidifier le mouvement des points à l'écran.
*   **Gestion des Trackers :** Détecte automatiquement les nouveaux trackers et supprime ceux qui deviennent inactifs.

## Prérequis

### Matériel

*   Un système de tracking UWB (ex: basé sur des puces DW1000 ou DW3000).
    *   Au moins 4 **ancres** positionnées dans l'espace.
    *   Un ou plusieurs **trackers/tags** mobiles.
*   Le code Arduino pour les ancres et les trackers est fourni dans le dossier `ESP32 DW3000 Code`.
*   Les trackers doivent être configurés pour envoyer des données JSON via UDP au serveur.

### Logiciel

*   Python 3.7+
*   Un navigateur web moderne (Chrome, Firefox, Safari, Edge).

## Installation et Lancement

### 1. Configuration des Modules UWB (Ancres et Tags)

Le code source pour les modules ESP32-UWB se trouve dans le dossier `ESP32 DW3000 Code/`. Il est basé sur le projet [esp32-uwb-positioning-system de KunYi](https://github.com/KunYi/esp32-uwb-positioning-system).

#### Prérequis Logiciels
1.  **Arduino IDE :** Téléchargez et installez la dernière version depuis le [site officiel d'Arduino](https://www.arduino.cc/en/software).
2.  **Pilotes USB :** Les modules de Makerfabs utilisent une puce CH9102. Vous devrez peut-être installer le pilote correspondant pour que votre ordinateur reconnaisse le port COM. Le pilote est généralement disponible sur le site du fabricant de la puce ou [ici](https://www.wch.cn/downloads/CH343SER_ZIP.html).
3.  **Gestionnaire de Cartes ESP32 :**
    - Dans Arduino IDE, allez dans `Fichier > Préférences`.
    - Dans le champ "URL de gestionnaire de cartes supplémentaires", ajoutez l'URL suivante : `https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json`.
    - Allez dans `Outils > Type de carte > Gestionnaire de cartes...`.
    - Recherchez "esp32" et installez le paquet "esp32 by Espressif Systems".

#### Téléversement du Code
Il y a deux programmes à téléverser : un pour les ancres et un pour le tag.

**Pour chaque Ancre :**
1.  Ouvrez le fichier `ESP32 DW3000 Code/anchor/anchor.ino` dans l'Arduino IDE.
2.  **Configuration :**
    - Modifiez les lignes suivantes pour configurer votre réseau Wi-Fi :
      ```cpp
      #define WIFI_SSID "VOTRE_SSID_WIFI"
      #define WIFI_PASSWORD "VOTRE_MOT_DE_PASSE_WIFI"
      ```
    - **Très Important :** Assignez un ID unique à chaque ancre en modifiant cette ligne. Les IDs doivent correspondre à ceux dans `config.json` (A0, A1, A2, A3, etc.).
      ```cpp
      String a_id = "A0"; // Changez en "A1", "A2", "A3" pour les autres ancres
      ```
3.  **Téléversement :**
    - Branchez une ancre à votre ordinateur.
    - Dans `Outils > Type de carte`, sélectionnez une carte ESP32 générique comme "ESP32 Dev Module".
    - Dans `Outils > Port`, sélectionnez le port COM correspondant à votre module.
    - Cliquez sur le bouton "Téléverser".

**Pour le Tag :**
1.  Ouvrez le fichier `ESP32 DW3000 Code/tag/tag.ino` dans l'Arduino IDE.
2.  **Configuration :**
    - Modifiez les mêmes lignes pour le Wi-Fi que pour les ancres.
    - Modifiez l'adresse IP du serveur pour qu'elle corresponde à l'adresse IP de l'ordinateur qui exécute le script Python.
      ```cpp
      #define UDP_SERVER_IP "192.168.1.100" // Remplacez par l'IP de votre serveur
      ```
    - Vous pouvez changer l'ID du tag si vous en utilisez plusieurs.
      ```cpp
      String t_id = "T0";
      ```
3.  **Téléversement :**
    - Suivez la même procédure que pour les ancres.

### 2. Lancement du Serveur Python

1.  **Clonez le Dépôt :**
    ```bash
    git clone <url_du_depot>
    cd <nom_du_dossier>
    ```

2.  **Créez un environnement virtuel Python :**
    C'est une bonne pratique pour isoler les dépendances du projet.
    ```bash
    python3 -m venv .venv
    ```

3.  **Activez l'environnement virtuel :**
    *   Sur macOS/Linux :
        ```bash
        source .venv/bin/activate
        ```
    *   Sur Windows :
        ```bash
        .venv\Scripts\activate
        ```

4.  **Installez les dépendances Python :**
    ```bash
    pip install -r requirements.txt
    ```

2.  **Lancez le serveur Flask :**
    ```bash
    python app.py
    ```
3.  **Ouvrez votre navigateur** et allez à l'adresse affichée dans le terminal (généralement `http://<votre_ip>:5000` ou `http://localhost:5000`).

## Fichiers de Configuration Modifiables par l'Utilisateur

### `app.py`

Certains paramètres peuvent être ajustés directement dans le code du serveur :

*   `UDP_PORT` (dans la fonction `udp_listener`) : Le port d'écoute pour les données des trackers. Doit correspondre à la configuration des trackers.
*   `processing_interval_s` (dans `data_processing_thread`) : La fréquence à laquelle le serveur envoie des mises à jour au client. Une valeur plus faible (`0.02`) est plus fluide mais plus gourmande en ressources.
*   `stale_threshold` (dans `data_processing_thread`) : Le temps en secondes avant qu'un tracker inactif ne disparaisse de l'interface.

### `templates/tracker_interface.html`

*   **Paramètres de lissage** (dans le listener de `smoothing-slider`) : Les formules qui mappent la valeur du curseur de lissage (0-100) aux paramètres `minCutoff` et `beta` du filtre peuvent être modifiées pour changer la "sensation" du lissage.
*   `calibTrackerId` (dans le listener de `record-point-btn`): L'ID du tracker à utiliser pour la calibration (par défaut "T0").

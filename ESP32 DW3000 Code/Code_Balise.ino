// Fichier: Tracker_V18_Config_Propre_et_Stable.ino
// Rôle: Basé sur la version V16 qui a fonctionné, avec une configuration
// simplifiée en haut du fichier, comme demandé. Aucune autre modification de logique.

#include "dw3000.h"
#include <WiFi.h>
#include <WiFiUdp.h>

/* ================== CONFIGURATION UTILISATEUR ================== */

// 1. DÉFINIR LE NUMÉRO DE CE TRACKER (0 pour T0, 1 pour T1, etc.)
#define TRACKER_ID_NUM 0

// 2. DÉFINIR LE NOMBRE TOTAL D'ANCRES DANS LE SYSTÈME
#define NUM_ANCHORS 4

// 3. ADRESSE IP DU SERVEUR PC
const IPAddress PC_IP(192, 168, 1, 75);

// 4. DÉLAI ENTRE LES MESURES (en ms)
#define RNG_DELAY_MS 25

// 5. IDENTIFIANTS WIFI
const char* ssid = "Jean-Luc";
const char* password = "Champagne!";

/* ============================================================== */


// --- Paramètres Techniques et Variables Globales ---
// Note: La structure ci-dessous est complexe mais nécessaire pour la stabilité.
// Elle est identique à celle de la version qui fonctionnait.

const int UDP_PORT = 16061;
const uint8_t PAN_ID[] = { 0xCA, 0xDE };
WiFiUDP udp;

// Génération de l'ID du Tag à la compilation pour la stabilité
const uint8_t TAG_ADDR[] = { 'T', '0' + TRACKER_ID_NUM };

// La liste des ancres est déclarée ici et sera remplie dans setup()
static char ANCHOR_LIST[NUM_ANCHORS][2];

// Configuration UWB Robuste
static dwt_config_t config = {
    5, DWT_PLEN_1024, DWT_PAC16, 9, 9, 1, DWT_BR_850K, DWT_PHRMODE_STD,
    DWT_PHRRATE_STD, (1025 + 16 - 16), DWT_STS_MODE_OFF, DWT_STS_LEN_64, DWT_PDOA_M0
};

// Définitions des constantes UWB
#define PIN_RST 27
#define PIN_IRQ 34
#define PIN_SS 4
#define TX_ANT_DLY 16385
#define RX_ANT_DLY 16385
#define POLL_TX_TO_RESP_RX_DLY_UUS 240
#define RESP_RX_TIMEOUT_UUS 1500
#define MAX_ANCHORS 10
#define MIN_ANCHORS_TO_SEND 2
#define ANCHOR_DATA_TIMEOUT 5000
#define MAX_VALID_DISTANCE 8.0
#define UDP_BROADCAST_INTERVAL 100
#define JSON_BUFFER_SIZE 512
#define ALL_MSG_COMMON_LEN (10)
#define ALL_MSG_SN_IDX 2
#define RESP_MSG_SRC_IDX (5)
#define RESP_MSG_SRC_LEN (2)
#define RESP_MSG_DST_IDX (7)
#define RESP_MSG_DST_LEN (2)
#define RESP_MSG_POLL_RX_TS_IDX 10
#define RESP_MSG_RESP_TX_TS_IDX 14
#define RESP_MSG_TS_LEN 4

// Structure de données et variables d'état globales
struct AnchorData { char id[2]; double distance; double tof; unsigned long timestamp; bool active; };
static AnchorData anchorArray[MAX_ANCHORS];
static int activeAnchors = 0;
static unsigned long lastBroadcastTime = 0;
static uint8_t tx_poll_msg[] = {0x41, 0x88, 0, PAN_ID[0], PAN_ID[1], TAG_ADDR[0], TAG_ADDR[1], 0, 0, 0xE0, 0, 0};
static uint8_t rx_resp_msg[] = {0x41, 0x88, 0, PAN_ID[0], PAN_ID[1], 0, 0, TAG_ADDR[0], TAG_ADDR[1], 0xE1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0};
static uint8_t frame_seq_nb = 0;
static uint8_t rx_buffer[20];
static uint32_t status_reg = 0;
static double tof;
static double distance;
static int currentAnchorIndex = 0;
extern dwt_txconfig_t txconfig_options;

// Prototypes des fonctions
static bool isExpectedFrame(const uint8_t *frame, const uint32_t len);
void updateAnchorData(const char* anchorId, double distance, double tof);
void cleanupInvalidAnchors();
void formatPositionDataToJson(char* jsonBuffer, size_t bufferSize);
void sendUdpResponse(const char* jsonData);

// ======================= DEBUT DU CODE =======================

void setup() {
    Serial.begin(115200);
    Serial.println("Initialisation du Tracker (Config Propre & Stable)...");

    // Génération automatique de la liste des ancres (fonctionne jusqu'à A9)
    for (int i = 0; i < NUM_ANCHORS; i++) {
        ANCHOR_LIST[i][0] = 'A';
        ANCHOR_LIST[i][1] = '0' + i;
    }

    // Initialisation du Wi-Fi
    WiFi.mode(WIFI_STA);
    WiFi.begin(ssid, password);
    Serial.print("Connexion au WiFi");
    while (WiFi.status() != WL_CONNECTED) { delay(500); Serial.print("."); }
    Serial.println("\nConnecté au WiFi !");
    udp.begin(UDP_PORT);

    // Initialisation du module UWB
    spiBegin(PIN_IRQ, PIN_RST);
    spiSelect(PIN_SS);
    delay(2);
    while (!dwt_checkidlerc()) { Serial.println("ERREUR IDLE"); while (1); }
    if (dwt_initialise(DWT_DW_INIT) == DWT_ERROR) { Serial.println("ERREUR INIT"); while (1); }
    dwt_setleds(DWT_LEDS_ENABLE | DWT_LEDS_INIT_BLINK);
    if (dwt_configure(&config)) { Serial.println("ERREUR CONFIG"); while (1); } // Applique la config robuste
    dwt_configuretxrf(&txconfig_options);
    dwt_setrxantennadelay(RX_ANT_DLY);
    dwt_settxantennadelay(TX_ANT_DLY);
    dwt_setrxaftertxdelay(POLL_TX_TO_RESP_RX_DLY_UUS);
    dwt_setrxtimeout(RESP_RX_TIMEOUT_UUS);
    dwt_setlnapamode(DWT_LNA_ENABLE | DWT_PA_ENABLE);

    char tagIdStr[3] = {TAG_ADDR[0], TAG_ADDR[1], 0};
    Serial.print("Tracker "); Serial.print(tagIdStr); Serial.println(" prêt.");
    for (int i = 0; i < MAX_ANCHORS; i++) { anchorArray[i].active = false; }
}

void loop() {
    // La logique de cette boucle est identique à la version qui fonctionnait,
    // utilisant des variables globales pour assurer la stabilité.
    
    // Prépare les messages pour l'ancre actuelle
    tx_poll_msg[RESP_MSG_DST_IDX] = ANCHOR_LIST[currentAnchorIndex][0];
    tx_poll_msg[RESP_MSG_DST_IDX + 1] = ANCHOR_LIST[currentAnchorIndex][1];
    rx_resp_msg[RESP_MSG_SRC_IDX] = ANCHOR_LIST[currentAnchorIndex][0];
    rx_resp_msg[RESP_MSG_SRC_IDX + 1] = ANCHOR_LIST[currentAnchorIndex][1];

    // Envoi du "Poll"
    tx_poll_msg[ALL_MSG_SN_IDX] = frame_seq_nb;
    dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_TXFRS_BIT_MASK);
    dwt_writetxdata(sizeof(tx_poll_msg), tx_poll_msg, 0);
    dwt_writetxfctrl(sizeof(tx_poll_msg), 0, 1);
    dwt_starttx(DWT_START_TX_IMMEDIATE | DWT_RESPONSE_EXPECTED);

    // Attend la réponse
    while (!((status_reg = dwt_read32bitreg(SYS_STATUS_ID)) & (SYS_STATUS_RXFCG_BIT_MASK | SYS_STATUS_ALL_RX_TO | SYS_STATUS_ALL_RX_ERR))) {};
    frame_seq_nb++;

    // Traite le résultat
    if (status_reg & SYS_STATUS_RXFCG_BIT_MASK) {
        uint32_t frame_len;
        dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_RXFCG_BIT_MASK);
        frame_len = dwt_read32bitreg(RX_FINFO_ID) & RXFLEN_MASK;
        if (frame_len <= sizeof(rx_buffer)) {
            dwt_readrxdata(rx_buffer, frame_len, 0);
            rx_buffer[ALL_MSG_SN_IDX] = 0;
            if (isExpectedFrame(rx_buffer, frame_len)) {
                // Succès, on calcule la distance
                uint32_t poll_tx_ts, resp_rx_ts, poll_rx_ts, resp_tx_ts;
                int32_t rtd_init, rtd_resp;
                float clockOffsetRatio;
                poll_tx_ts = dwt_readtxtimestamplo32();
                resp_rx_ts = dwt_readrxtimestamplo32();
                clockOffsetRatio = ((float)dwt_readclockoffset()) / (uint32_t)(1 << 26);
                resp_msg_get_ts(&rx_buffer[RESP_MSG_POLL_RX_TS_IDX], &poll_rx_ts);
                resp_msg_get_ts(&rx_buffer[RESP_MSG_RESP_TX_TS_IDX], &resp_tx_ts);
                rtd_init = resp_rx_ts - poll_tx_ts;
                rtd_resp = resp_tx_ts - poll_rx_ts;
                tof = ((rtd_init - rtd_resp * (1 - clockOffsetRatio)) / 2.0) * DWT_TIME_UNITS;
                distance = tof * SPEED_OF_LIGHT;
                char name[3] = {0};
                memcpy(name, rx_buffer + RESP_MSG_SRC_IDX, RESP_MSG_SRC_LEN);
                Serial.printf("Ancre: %s, DIST: %.2f m\n", name, distance);
                updateAnchorData(name, distance, tof);
            }
        }
    } else {
        Serial.printf("Échec mesure ancre %c%c\n", ANCHOR_LIST[currentAnchorIndex][0], ANCHOR_LIST[currentAnchorIndex][1]);
        dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_ALL_RX_TO | SYS_STATUS_ALL_RX_ERR);
    }
    
    cleanupInvalidAnchors();
    
    // Envoi du JSON après un tour complet
    if (currentAnchorIndex == (NUM_ANCHORS - 1)) {
        if (activeAnchors >= MIN_ANCHORS_TO_SEND) {
            char jsonBuffer[JSON_BUFFER_SIZE];
            formatPositionDataToJson(jsonBuffer, JSON_BUFFER_SIZE);
            sendUdpResponse(jsonBuffer);
            lastBroadcastTime = millis();
        }
    }
    
    currentAnchorIndex = (currentAnchorIndex + 1) % NUM_ANCHORS;
    delay(RNG_DELAY_MS);
}

// --- Fonctions Annexes (Structure originale) ---

static bool isExpectedFrame(const uint8_t *frame, const uint32_t len) {
    if (len < (RESP_MSG_DST_IDX + RESP_MSG_DST_LEN)) return false;
    if (memcmp(frame, rx_resp_msg, ALL_MSG_COMMON_LEN) != 0) return false;
    return true;
}

void updateAnchorData(const char* anchorId, double dist, double flight_time) {
    if (dist > MAX_VALID_DISTANCE) return;
    for (int i = 0; i < MAX_ANCHORS; i++) {
        if (anchorArray[i].active && anchorArray[i].id[0] == anchorId[0] && anchorArray[i].id[1] == anchorId[1]) {
            anchorArray[i].distance = dist;
            anchorArray[i].tof = flight_time;
            anchorArray[i].timestamp = millis();
            return;
        }
    }
    for (int i = 0; i < MAX_ANCHORS; i++) {
        if (!anchorArray[i].active) {
            strncpy(anchorArray[i].id, anchorId, 2);
            anchorArray[i].distance = dist;
            anchorArray[i].tof = flight_time;
            anchorArray[i].timestamp = millis();
            anchorArray[i].active = true;
            activeAnchors++;
            return;
        }
    }
}

void cleanupInvalidAnchors() {
    for (int i = 0; i < MAX_ANCHORS; i++) {
        if (anchorArray[i].active && (millis() - anchorArray[i].timestamp > ANCHOR_DATA_TIMEOUT)) {
            anchorArray[i].active = false;
            activeAnchors--;
        }
    }
}

void formatPositionDataToJson(char* jsonBuffer, size_t bufferSize) {
    char tagId[3] = {TAG_ADDR[0], TAG_ADDR[1], 0};
    snprintf(jsonBuffer, bufferSize, "{\"tag\":\"%s\",\"anchors\":[", tagId);
    bool firstAnchor = true;
    for (int i = 0; i < NUM_ANCHORS; i++) {
        if (anchorArray[i].active) {
            if (!firstAnchor) { strlcat(jsonBuffer, ",", bufferSize); }
            char anchorJson[128];
            snprintf(anchorJson, sizeof(anchorJson), "{\"id\":\"%c%c\",\"distance\":%.2f}", anchorArray[i].id[0], anchorArray[i].id[1], anchorArray[i].distance);
            strlcat(jsonBuffer, anchorJson, bufferSize);
            firstAnchor = false;
        }
    }
    strlcat(jsonBuffer, "]}", bufferSize);
}

void sendUdpResponse(const char* jsonData) {
    udp.beginPacket(PC_IP, UDP_PORT);
    udp.write((const uint8_t*)jsonData, strlen(jsonData));
    udp.endPacket();
}
import eventlet
eventlet.monkey_patch()

import socket
import json
import time
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO
import threading
import os
import numpy as np
from scipy.optimize import minimize
import logging
from datetime import datetime, timedelta

# --- Configuration et Initialisation ---

# Nom du fichier de configuration. Ce fichier stocke les positions des ancres et la géométrie de l'écran.
CONFIG_FILE = 'config.json'
# Nom du fichier de log pour enregistrer les événements et les erreurs de l'application.
LOG_FILE = 'app.log'

# Configuration du logging pour écrire dans un fichier et à la console.
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)

# Initialisation de l'application Flask et de l'extension SocketIO pour la communication temps-réel avec le client.
app = Flask(__name__)
# La clé secrète n'est pas critique ici car nous ne gérons pas de sessions utilisateur complexes.
app.config['SECRET_KEY'] = 'secret!'
# 'threading' est simple et suffisant pour notre cas d'usage.
socketio = SocketIO(app, async_mode='threading')

# --- Variables Globales ---

# `data_lock` est un verrou réentrant (RLock) pour protéger l'accès concurrent aux données partagées 
# depuis différents threads (thread UDP et threads des requêtes Flask).
# Un RLock peut être acquis plusieurs fois par le même thread sans causer de blocage (deadlock).
data_lock = threading.RLock()

# `latest_data` stocke les informations les plus récentes reçues des trackers (tags).
# Structure: { "tag_id": {"tag": "T0", "anchors": [...], "timestamp": ...}, ... }
latest_data = {}

# `tag_history` conserve un historique des positions 3D calculées pour chaque tag.
# Utilisé pour le lissage ou d'autres analyses temporelles si nécessaire.
# Structure: { "tag_id": [ (timestamp, [x, y, z]), ... ], ... }
tag_history = {}

# `anchor_positions` contient les coordonnées 3D de chaque ancre en MÈTRES.
# Les coordonnées sont chargées depuis `config.json` au démarrage.
# Structure: { "A0": [x, y, z], "A1": [x, y, z], ... }
anchor_positions = {}

# `screen_config` stocke la géométrie de l'écran de projection en MÈTRES.
# Ces données sont soit calculées par le processus de calibration, soit définies manuellement.
# Structure: { "origin": [x, y, z], "vec_x": [dx, dy, dz], "vec_y": [dx, dy, dz], "width_cm": w, "height_cm": h }
screen_config = {}

# `calibration_data` stocke temporairement les points 3D mesurés pendant la calibration de l'écran.
# C'est une liste de listes de coordonnées [x, y, z].
calibration_data = []

# --- System Configuration ---
UDP_PORT = 16061
# Seconds before a tracker is considered disconnected if no data is received.
TRACKER_TIMEOUT_S = 10.0

# --- Flask & SocketIO Setup ---
app.config['SECRET_KEY'] = 'a_very_secret_key_for_passive_listening!'
socketio = SocketIO(app, async_mode='eventlet')

# --- Data Storage ---
tag_data_store = {}
screen_config = {}
calibration_data = {} # Temp storage for old calibration
data_lock = threading.RLock()

# --- New Calibration Data Storage & Definition ---
# The 10 points on the screen (normalized 0.0-1.0 coords) for the user to target.
# New order: Left-to-Right for more efficient user movement.
CALIBRATION_TARGET_POINTS_UV = [
    # Left Column
    [0.05, 0.95],  # Top-Left
    [0.05, 0.5],   # Left-Mid
    [0.05, 0.05],  # Bottom-Left
    # Mid-Left Column
    [0.25, 0.75],  # Inner Top-Left Quadrant
    # Center Column
    [0.5, 0.95],   # Top-Mid
    [0.5, 0.5],    # Center
    [0.5, 0.05],   # Bottom-Mid
    # Right Column
    [0.95, 0.95],  # Top-Right
    [0.95, 0.5],   # Right-Mid
    [0.95, 0.05]   # Bottom-Right
]
calibration_measurements = [] # Stores the results of the 10-point measurement process.

# --- Configuration Management ---
def save_config(config_dict):
    """Saves the entire configuration dictionary to the config file."""
    try:
        with data_lock:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(config_dict, f, indent=4)
    except Exception as e:
        print(f"[ERROR] Could not save config to {CONFIG_FILE}: {e}")

def load_or_create_config():
    """Loads anchor and screen config from config.json or creates it."""
    global anchor_positions, screen_config
    if not os.path.exists(CONFIG_FILE):
        print(f"'{CONFIG_FILE}' not found. Creating with default values.")
        default_config = {
            "anchors": {
                "A0": {"x": 0, "y": 0, "z": 250},
                "A1": {"x": 435, "y": 250, "z": 150},
                "A2": {"x": 435, "y": 0, "z": 250},
                "A3": {"x": 0, "y": 250, "z": 150}
            },
            "screen": None # To be filled by calibration
        }
        with open(CONFIG_FILE, 'w') as f:
            json.dump(default_config, f, indent=2)
    
    with open(CONFIG_FILE, 'r') as f:
        config_data = json.load(f)
        # Load anchor positions and immediately convert them to meters for all calculations
        anchor_positions = {k: np.array(list(v.values())) / 100.0 for k, v in config_data['anchors'].items()}
        
        screen_config = config_data.get('screen')
        if screen_config:
            # Screen config is saved in cm, convert to meters on load
            screen_config['origin'] = np.array(screen_config['origin']) / 100.0
            screen_config['vec_x'] = np.array(screen_config['vec_x']) / 100.0
            screen_config['vec_y'] = np.array(screen_config['vec_y']) / 100.0
            print("Screen configuration loaded and converted to meters.")
        else:
            print("Screen is not calibrated yet.")

# --- 3D Calculation and Projection ---
def solve_3d_position(distances, anchors_in_meters):
    """Calculates the 3D position of a tag using multilateration. Assumes distances are in meters."""
    valid_anchors = []
    valid_distances = []
    for anchor_id, dist_m in distances.items():
        if dist_m is not None and anchor_id in anchors_in_meters:
            valid_anchors.append(anchors_in_meters[anchor_id])
            valid_distances.append(dist_m) # Distances are already in meters

    if len(valid_anchors) < 4:
        return None

    def error_func(p, anchors, dists):
        err = []
        for anchor, dist in zip(anchors, dists):
            err.append((np.linalg.norm(p - anchor) - dist) ** 2)
        return np.sum(err)

    initial_guess = np.mean(valid_anchors, axis=0)
    
    # Define a reasonable search area (e.g., a 20x20x20 meter box around the origin)
    bounds = [(-10, 10), (-10, 10), (-10, 10)] 
    
    result = minimize(
        error_func, 
        initial_guess, 
        args=(valid_anchors, valid_distances), 
        method='L-BFGS-B',
        bounds=bounds
    )

    if result.success:
        return result.x # Return position in meters
    
    print(f"[Solver] 3D position solve failed. Message: {result.message}")
    return None

def project_to_2d(pos_3d_m, config_in_meters):
    """Projects a 3D position (in meters) onto the calibrated screen plane."""
    if not config_in_meters or 'origin' not in config_in_meters:
        return None
    
    pos_3d = np.array(pos_3d_m)
    origin = config_in_meters['origin']
    vec_x = config_in_meters['vec_x']
    vec_y = config_in_meters['vec_y']

    # Solve the 2D system of linear equations to find u and v
    # vec_p = u * vec_x + v * vec_y
    # We solve for [u, v] using dot products
    vec_p = pos_3d - origin
    
    m_11 = np.dot(vec_x, vec_x)
    m_12 = np.dot(vec_x, vec_y)
    m_21 = m_12
    m_22 = np.dot(vec_y, vec_y)
    
    det = m_11 * m_22 - m_12 * m_21
    if abs(det) < 1e-9: # Vectors are nearly collinear, cannot solve
        return None

    inv_det = 1.0 / det
    
    d_1 = np.dot(vec_p, vec_x)
    d_2 = np.dot(vec_p, vec_y)
    
    u = inv_det * (m_22 * d_1 - m_12 * d_2)
    v = inv_det * (m_11 * d_2 - m_21 * d_1)
    
    return [u, v]

# --- Background Task: UDP Listener ---
def udp_listener():
    """Listens for incoming tracker data and processes it."""
    listen_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    listen_socket.bind(('0.0.0.0', UDP_PORT))
    print(f"[*] Passive UDP Listener started on port {UDP_PORT}")

    while True:
        try:
            data, addr = listen_socket.recvfrom(1024)
            message = json.loads(data.decode('utf-8'))
            tag_id = message.get("tag")

            if not (tag_id and "anchors" in message):
                continue

            with data_lock:
                if tag_id not in tag_data_store:
                    print(f"[+] New tracker detected: {tag_id} from {addr[0]}.")
                    tag_data_store[tag_id] = {
                        "ip": addr[0],
                        "distances": {"A0": None, "A1": None, "A2": None, "A3": None},
                        "position_3d": None,
                        "position_2d": None,
                        "status": "receiving"
                    }

                # Update distances and last_seen
                tag_data_store[tag_id]['last_seen'] = time.time()
                current_distances = tag_data_store[tag_id]['distances']
                for anchor in message["anchors"]:
                    if 'id' in anchor and anchor['id'] in current_distances:
                        current_distances[anchor['id']] = anchor['distance']

                # Calculate 3D position
                pos_3d = solve_3d_position(current_distances, anchor_positions)
                tag_data_store[tag_id]['position_3d'] = pos_3d.tolist() if pos_3d is not None else None
                
                # Update status based on solver result
                if pos_3d is None:
                    tag_data_store[tag_id]['status'] = "solver_failed"
                else:
                    tag_data_store[tag_id]['status'] = "tracking"

                # Project to 2D if possible
                if pos_3d is not None and screen_config:
                    pos_2d = project_to_2d(pos_3d, screen_config)
                    # Convert numpy floats to native python floats for JSON compatibility
                    tag_data_store[tag_id]['position_2d'] = [float(p) for p in pos_2d] if pos_2d is not None else None
                else:
                    tag_data_store[tag_id]['position_2d'] = None
                    if pos_3d is not None and not screen_config:
                        tag_data_store[tag_id]['status'] = "needs_calibration"

                # Emit update
                socketio.emit('uwb_update', {
                    "server_timestamp": time.time(),
                    "tags": tag_data_store
                })

        except (json.JSONDecodeError, KeyError):
            # Silently ignore malformed packets
            pass
        except Exception as e:
            print(f"[!] Listener Error: {e}")

# --- Background Task: Stale Tracker Cleanup ---
def cleanup_loop():
    """Periodically removes stale trackers from the data store."""
    while True:
        stale_tags_found = False
        with data_lock:
            # Create a copy of keys to allow safe modification during iteration
            stale_check_keys = list(tag_data_store.keys())
            for tag_id in stale_check_keys:
                if (time.time() - tag_data_store[tag_id].get('last_seen', 0)) > TRACKER_TIMEOUT_S:
                    print(f"[-] Tracker {tag_id} timed out. Removing.")
                    del tag_data_store[tag_id]
                    stale_tags_found = True
        
        # If a tracker was removed, notify the UI
        if stale_tags_found:
            with data_lock:
                socketio.emit('uwb_update', {
                    "server_timestamp": time.time(),
                    "tags": tag_data_store
                })
        
        time.sleep(2) # Check for stale trackers every 2 seconds


# --- API Routes for Anchor and Screen Configuration ---
@app.route('/api/anchors', methods=['GET'])
def get_anchors():
    # This route is for display/edit in cm, so we read the raw file
    with open(CONFIG_FILE, 'r') as f:
        config_data = json.load(f)
    return jsonify(config_data)

@app.route('/api/anchors', methods=['POST'])
def set_anchors():
    global anchor_positions
    new_config_data = request.get_json()
    if 'anchors' not in new_config_data:
        return jsonify({"status": "error", "message": "Invalid data"}), 400

    with data_lock:
        anchor_positions = {k: np.array(list(v.values())) for k, v in new_config_data['anchors'].items()}

    with open(CONFIG_FILE, 'r+') as f:
        config = json.load(f)
        config['anchors'] = new_config_data['anchors']
        f.seek(0)
        json.dump(config, f, indent=2)
        f.truncate()
    
    print("[Config] Anchor positions updated via API.")
    return jsonify({"status": "ok"})

@app.route('/api/screen_config/manual', methods=['POST'])
def set_screen_manual():
    """Manually sets the screen configuration based on user input."""
    global screen_config
    data = request.get_json()
    
    required_keys = ['width_cm', 'height_cm', 'origin_x', 'origin_y', 'origin_z']
    if not all(key in data and data[key] is not None for key in required_keys):
        return jsonify({"status": "error", "message": "Données invalides ou incomplètes."}), 400

    width_cm = data['width_cm']
    height_cm = data['height_cm']
    origin_cm = [data['origin_x'], data['origin_y'], data['origin_z']]
    
    # For manual setup, assume screen is flat and aligned with world axes.
    # The X vector for the screen points along the world X axis.
    # The Y vector for the screen points along the world Y axis.
    vec_x_cm = [width_cm, 0, 0]
    vec_y_cm = [0, height_cm, 0]

    manual_screen_config_cm = {
        "origin": origin_cm,
        "vec_x": vec_x_cm,
        "vec_y": vec_y_cm,
        "width_cm": width_cm,
        "height_cm": height_cm
    }

    try:
        # Save to config file and reload
        config_data = {}
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as f:
                config_data = json.load(f)
        
        config_data['screen'] = manual_screen_config_cm
        save_config(config_data)
        load_or_create_config() # Reload into global state
        
        print("[SUCCESS] Manual screen configuration saved.")
        return jsonify({"status": "ok", "config": manual_screen_config_cm})
    except Exception as e:
        print(f"[ERROR] Could not save manual screen config: {e}")
        return jsonify({"status": "error", "message": "Erreur serveur lors de la sauvegarde."}), 500

@app.route('/api/calibrate/start', methods=['POST'])
def calib_start():
    """Clears any previous calibration data and starts the 10-point process."""
    with data_lock:
        calibration_measurements.clear()
        print("[*] Starting new 10-point screen calibration.")
    return jsonify({"status": "ok", "total_steps": len(CALIBRATION_TARGET_POINTS_UV)})

@app.route('/api/calibrate/record_point', methods=['POST'])
def calib_record():
    """
    Records the position for a tracker at a specific calibration step.
    This now collects data for 5 seconds and averages the results for stability.
    """
    if not request.json or 'tracker_id' not in request.json or 'step_index' not in request.json:
        return jsonify({"status": "error", "message": "Requête invalide."}), 400

    tracker_id = request.json['tracker_id']
    step_index = request.json['step_index']

    if step_index >= len(CALIBRATION_TARGET_POINTS_UV):
        return jsonify({"status": "error", "message": "Index d'étape invalide."}), 400

    with data_lock:
        if tracker_id not in tag_data_store:
            return jsonify({"status": "error", "message": f"Tracker {tracker_id} non trouvé."}), 404
    
    print(f"[*] Starting 5-second measurement for step {step_index}...")
    
    # Collect data for 5 seconds
    collected_positions = []
    start_time = time.time()
    while time.time() - start_time < 5.0:
        with data_lock:
            # Check if tracker is still available
            if tracker_id not in tag_data_store or tag_data_store[tracker_id].get('position_3d') is None:
                socketio.sleep(0.1) # Wait a bit before retrying
                continue
            
            # Record the current 3D position
            pos_3d = tag_data_store[tracker_id]['position_3d']
            collected_positions.append(pos_3d)
        socketio.sleep(0.05) # Sample at ~20Hz

    if not collected_positions:
        return jsonify({"status": "error", "message": "Aucune donnée de position 3D reçue pendant 5s. Rapprochez le tracker."}), 500

    # Average the collected positions
    avg_pos_3d = np.mean(np.array(collected_positions), axis=0)
    print(f"[+] Measurement complete. Average position: {avg_pos_3d.tolist()}")

    with data_lock:
        # Store the known screen UV coordinate and the calculated 3D position
        measurement = {
            "uv": CALIBRATION_TARGET_POINTS_UV[step_index],
            "pos3d": avg_pos_3d.tolist()
        }
        
        # Avoid duplicate points for the same step by replacing if it exists
        found_and_replaced = False
        for i, m in enumerate(calibration_measurements):
            if m['uv'] == measurement['uv']:
                calibration_measurements[i] = measurement
                found_and_replaced = True
                break
        if not found_and_replaced:
            calibration_measurements.append(measurement)

        print(f"[+] Calibration point {len(calibration_measurements)}/{len(CALIBRATION_TARGET_POINTS_UV)} recorded for step {step_index}.")
        return jsonify({"status": "ok", "points_recorded": len(calibration_measurements)})

@app.route('/api/calibrate/calculate', methods=['POST'])
def calib_calculate():
    """
    Calculates the screen's 3D plane (origin, basis vectors) from the recorded 3D points.
    We are solving P = O + u*X + v*Y, where P, O, X, Y are 3D vectors.
    This can be rewritten as a system of linear equations.
    """
    global screen_config
    with data_lock:
        if len(calibration_measurements) < 4: # Need at least 4 non-collinear points
            return jsonify({
                "status": "error", 
                "message": f"Pas assez de points. Au moins 4 sont requis. {len(calibration_measurements)} enregistrés."
            }), 400
        
        print("[*] Starting calibration calculation with points:", json.dumps(calibration_measurements, indent=2))
        
        # We need to solve for 9 variables: O_x, O_y, O_z, X_x, X_y, X_z, Y_x, Y_y, Y_z
        # The system of equations is:
        # Px = Ox + u*Xx + v*Yx
        # Py = Oy + u*Xy + v*Yy
        # Pz = Oz + u*Xz + v*Yz
        # This can be formed into a matrix equation A*s = p
        
        num_points = len(calibration_measurements)
        # Create the large A matrix (3*num_points rows, 9 columns)
        A = np.zeros((num_points * 3, 9))
        # Create the b vector
        b = np.zeros(num_points * 3)

        for i, measurement in enumerate(calibration_measurements):
            u, v = measurement['uv']
            px, py, pz = measurement['pos3d']

            # Row for Px
            A[i*3, 0] = 1; A[i*3, 3] = u; A[i*3, 6] = v
            b[i*3] = px
            
            # Row for Py
            A[i*3+1, 1] = 1; A[i*3+1, 4] = u; A[i*3+1, 7] = v
            b[i*3+1] = py

            # Row for Pz
            A[i*3+2, 2] = 1; A[i*3+2, 5] = u; A[i*3+2, 8] = v
            b[i*3+2] = pz

        try:
            solution, residuals, rank, s = np.linalg.lstsq(A, b, rcond=None)
            
            # Extract the 3D vectors from the solution
            origin = solution[0:3]  # O_x, O_y, O_z
            vec_x = solution[3:6]   # X_x, X_y, X_z
            vec_y = solution[6:9]   # Y_x, Y_y, Y_z

            # The calculated vectors vec_x and vec_y might not be perfectly orthogonal.
            # We will NOT force them to be, to account for non-rectangular screens.
            
            # Width and height are the magnitudes (lengths) of these basis vectors.
            # Convert from meters to cm for saving and display.
            width_cm = np.linalg.norm(vec_x) * 100
            height_cm = np.linalg.norm(vec_y) * 100 # Use the raw vector length

            print(f"[+] Calibration Result: Origin={origin*100}cm, W={width_cm}cm, H={height_cm}cm")

            # Create new screen configuration object (saved in cm)
            new_screen_config_cm = {
                "origin": (origin * 100).tolist(),
                "vec_x": (vec_x * 100).tolist(),
                "vec_y": (vec_y * 100).tolist(), # Use the raw (non-orthogonal) vector
                "width_cm": width_cm,
                "height_cm": height_cm
            }
            
            # --- Save the configuration and update global state ---
            # 1. Read the whole config file
            config_to_save = {}
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'r') as f:
                    config_to_save = json.load(f)

            # 2. Update the 'screen' part
            config_to_save['screen'] = new_screen_config_cm
            
            # 3. Save the whole thing back
            save_config(config_to_save)
            
            # 4. Reload the config into the global state to ensure consistency.
            # This is the correct way to make sure all parts of the app see the new data.
            load_or_create_config()
            
            print("[SUCCESS] New screen calibration calculated and saved.")
            return jsonify({"status": "ok", "config": new_screen_config_cm})

        except np.linalg.LinAlgError as e:
            print(f"[ERROR] Linear algebra error during calibration calculation: {e}")
            return jsonify({"status": "error", "message": "Erreur de calcul. Réessayez."}), 500
        except Exception as e:
            import traceback
            print(f"[!!!] UNHANDLED EXCEPTION in calibration calculation: {e}")
            traceback.print_exc()
            return jsonify({"status": "error", "message": "Erreur inattendue du serveur."}), 500

@app.route('/api/calibrate/cancel', methods=['POST'])
def calib_cancel():
    """Cancels the calibration process and clears any collected data."""
    with data_lock:
        calibration_measurements.clear()
        print("[*] Calibration cancelled.")
    return jsonify({"status": "ok"})

@app.route('/api/screen_config', methods=['GET'])
def get_screen_config():
    global screen_config
    if screen_config and screen_config.get('origin') is not None:
        # The global screen_config is in METERS. Convert to CM for the client.
        config_for_json = {
            "origin": (np.array(screen_config['origin']) * 100).tolist(),
            "vec_x": (np.array(screen_config['vec_x']) * 100).tolist(),
            "vec_y": (np.array(screen_config['vec_y']) * 100).tolist(),
            "width_cm": screen_config['width_cm'],
            "height_cm": screen_config['height_cm']
        }
        return jsonify({
            "status": "ok",
            "config": config_for_json
        })
    else:
        return jsonify({"status": "not_found", "message": "Aucune configuration d'écran trouvée."})

# --- Flask Routes ---
@app.route('/')
def index():
    """Serves the main tracking interface."""
    return render_template('tracker_interface.html')

# --- SocketIO Events ---
@socketio.on('connect')
def handle_connect():
    """Handles new web client connections."""
    print(f"[SocketIO] Web client connected.")
    # On connection, send the latest data immediately
    with data_lock:
        socketio.emit('uwb_update', {
            "server_timestamp": time.time(),
            "tags": tag_data_store
        })

@socketio.on('disconnect')
def handle_disconnect():
    """Handles web client disconnections."""
    print(f"[SocketIO] Web client disconnected.")

# --- Main Entry Point ---
if __name__ == '__main__':
    print("[*] Starting UWB Passive Listener Server...")
    load_or_create_config() # Load config on startup
    # Start background tasks
    socketio.start_background_task(target=udp_listener)
    socketio.start_background_task(target=cleanup_loop)
    
    print("[*] Server is running. Open the web interface.")
    socketio.run(app, host='0.0.0.0', port=5001, debug=False) 
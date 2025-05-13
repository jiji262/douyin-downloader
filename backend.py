from flask import Flask, request, jsonify, send_from_directory
import yaml
import os

app = Flask(__name__)

CONFIG_PATH = './config.yml'
STATIC_FOLDER = './'  # Ensure `index.html` is in the same directory as `backend.py`

def load_config():
    """Load the YAML configuration file."""
    if not os.path.exists(CONFIG_PATH):
        return {}
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def save_config(config):
    """Save the configuration to the YAML file."""
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        yaml.dump(config, f, allow_unicode=True)

@app.route('/')
def home():
    """Serve the UI homepage."""
    return send_from_directory(STATIC_FOLDER, 'index.html')

@app.route('/api/config', methods=['GET'])
def get_config():
    """API to get the current configuration."""
    config = load_config()
    return jsonify(config)

@app.route('/api/config', methods=['POST'])
def update_config():
    """API to update the configuration."""
    new_config = request.json
    if not new_config:
        return jsonify({"error": "Invalid configuration"}), 400
    save_config(new_config)
    return jsonify({"message": "Configuration updated successfully"})

if __name__ == '__main__':
    app.run(debug=True)
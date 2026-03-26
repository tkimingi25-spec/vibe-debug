import phoenix as px
import os
from dotenv import load_dotenv

load_dotenv()

def launch_server():
    """
    Launch Arize Phoenix locally.
    Uses PHOENIX_PORT from .env (default 6006).
    """
    port = int(os.getenv("PHOENIX_PORT", 6006))
    host = os.getenv("PHOENIX_HOST", "127.0.0.1")
    print(f"Launching Arize Phoenix on {host}:{port}...")
    px.launch_app(host=host, port=port)
    print("UI is available at http://localhost:6006")
    
    # Keep the script running
    input("Press Enter to stop Phoenix server...\n")

if __name__ == "__main__":
    launch_server()

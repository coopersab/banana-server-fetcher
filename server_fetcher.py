"""
🍌 Banana Server Fetcher - Python Proxy Server
Handles Roblox API requests to prevent rate limiting
CONFIGURED FOR RAILWAY DEPLOYMENT
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import time
import json
import os
from datetime import datetime, timedelta
from threading import Lock

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# ✅ Get port from environment (Railway provides this)
PORT = int(os.environ.get("PORT", 5000))

# Configuration
ROBLOX_API_BASE = "https://games.roblox.com/v1/games"
CACHE_FILE = "server_cache.json"
CACHE_EXPIRY_MINUTES = int(os.environ.get("CACHE_EXPIRY_MINUTES", 30))
REQUEST_COOLDOWN = int(os.environ.get("REQUEST_COOLDOWN", 2))

# Global cache and state
cache = {
    "servers": {},  # {placeId: {servers: [], cursor: str, timestamp: float}}
    "last_request": 0
}
cache_lock = Lock()

def load_cache():
    """Load cache from file"""
    global cache
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r') as f:
                cache = json.load(f)
            print(f"[Cache] Loaded cache from file")
        except Exception as e:
            print(f"[Cache] Error loading cache: {e}")
            cache = {"servers": {}, "last_request": 0}

def save_cache():
    """Save cache to file"""
    try:
        with open(CACHE_FILE, 'w') as f:
            json.dump(cache, f, indent=2)
    except Exception as e:
        print(f"[Cache] Error saving cache: {e}")

def is_cache_valid(place_id):
    """Check if cache for place_id is still valid"""
    if str(place_id) not in cache["servers"]:
        return False
    
    place_cache = cache["servers"][str(place_id)]
    timestamp = place_cache.get("timestamp", 0)
    age_minutes = (time.time() - timestamp) / 60
    
    return age_minutes < CACHE_EXPIRY_MINUTES and len(place_cache.get("servers", [])) > 0

def fetch_from_roblox(place_id, cursor=None, exclude_full=False):
    """Fetch servers from Roblox API with rate limiting"""
    global cache
    
    with cache_lock:
        # Rate limiting
        time_since_last = time.time() - cache.get("last_request", 0)
        if time_since_last < REQUEST_COOLDOWN:
            wait_time = REQUEST_COOLDOWN - time_since_last
            print(f"[RateLimit] Waiting {wait_time:.2f}s...")
            time.sleep(wait_time)
        
        # Build URL
        url = f"{ROBLOX_API_BASE}/{place_id}/servers/Public?limit=100"
        url += f"&excludeFullGames={str(exclude_full).lower()}"
        if cursor:
            url += f"&cursor={cursor}"
        
        print(f"[Roblox] Fetching: {url[:100]}...")
        
        try:
            response = requests.get(url, timeout=10)
            cache["last_request"] = time.time()
            
            if response.status_code == 200:
                data = response.json()
                print(f"[Roblox] Success! Got {len(data.get('data', []))} servers")
                return data
            elif response.status_code == 429:
                print(f"[Roblox] Rate limited! Status: {response.status_code}")
                return {"error": "rate_limited", "retry_after": 60}
            else:
                print(f"[Roblox] Error: {response.status_code}")
                return {"error": f"http_error_{response.status_code}"}
                
        except requests.RequestException as e:
            print(f"[Roblox] Request failed: {e}")
            return {"error": str(e)}

def update_cache(place_id, servers, cursor=None):
    """Update cache with new server data"""
    global cache
    
    place_id_str = str(place_id)
    
    if place_id_str not in cache["servers"]:
        cache["servers"][place_id_str] = {
            "servers": [],
            "cursor": None,
            "timestamp": time.time()
        }
    
    place_cache = cache["servers"][place_id_str]
    
    # Add new servers (avoid duplicates)
    existing_ids = {s["id"] for s in place_cache["servers"]}
    new_servers = [s for s in servers if s["id"] not in existing_ids]
    
    place_cache["servers"].extend(new_servers)
    place_cache["cursor"] = cursor
    place_cache["timestamp"] = time.time()
    
    # Limit cache size (keep only 1000 most recent)
    if len(place_cache["servers"]) > 1000:
        place_cache["servers"] = place_cache["servers"][-1000:]
    
    save_cache()
    
    print(f"[Cache] Added {len(new_servers)} servers (total: {len(place_cache['servers'])})")

@app.route('/')
def home():
    """Home endpoint"""
    return jsonify({
        "status": "online",
        "service": "Banana Server Fetcher",
        "version": "1.0",
        "deployed_on": "Railway",
        "endpoints": {
            "/servers": "GET - Fetch servers for a place",
            "/cache/info": "GET - Get cache info",
            "/cache/clear": "POST - Clear cache",
            "/health": "GET - Health check"
        }
    })

@app.route('/servers', methods=['GET'])
def get_servers():
    """
    Main endpoint to get servers
    Query params:
        - placeId: Roblox place ID (required)
        - excludeFull: Exclude full servers (optional, default: false)
        - forceRefresh: Force fetch from Roblox (optional, default: false)
        - count: Number of servers to return (optional, default: 50)
    """
    place_id = request.args.get('placeId')
    exclude_full = request.args.get('excludeFull', 'false').lower() == 'true'
    force_refresh = request.args.get('forceRefresh', 'false').lower() == 'true'
    count = int(request.args.get('count', 50))
    
    if not place_id:
        return jsonify({"error": "placeId is required"}), 400
    
    try:
        place_id = int(place_id)
    except ValueError:
        return jsonify({"error": "placeId must be a number"}), 400
    
    place_id_str = str(place_id)
    
    # Check cache first
    if not force_refresh and is_cache_valid(place_id):
        print(f"[Cache] Using cached data for {place_id}")
        place_cache = cache["servers"][place_id_str]
        servers = place_cache["servers"][:count]
        
        # Remove returned servers from cache
        cache["servers"][place_id_str]["servers"] = place_cache["servers"][count:]
        save_cache()
        
        return jsonify({
            "source": "cache",
            "placeId": place_id,
            "servers": servers,
            "count": len(servers),
            "remaining": len(cache["servers"][place_id_str]["servers"]),
            "timestamp": place_cache["timestamp"]
        })
    
    # Fetch from Roblox
    print(f"[API] Cache miss or expired for {place_id}, fetching from Roblox...")
    
    cursor = None
    if place_id_str in cache["servers"]:
        cursor = cache["servers"][place_id_str].get("cursor")
    
    result = fetch_from_roblox(place_id, cursor, exclude_full)
    
    if "error" in result:
        return jsonify({
            "error": result["error"],
            "retry_after": result.get("retry_after", 60)
        }), 429 if result["error"] == "rate_limited" else 500
    
    servers = result.get("data", [])
    next_cursor = result.get("nextPageCursor")
    
    # Update cache
    update_cache(place_id, servers, next_cursor)
    
    # Return requested count
    return_servers = servers[:count]
    
    return jsonify({
        "source": "roblox",
        "placeId": place_id,
        "servers": return_servers,
        "count": len(return_servers),
        "nextCursor": next_cursor,
        "timestamp": time.time()
    })

@app.route('/cache/info', methods=['GET'])
def cache_info():
    """Get cache information"""
    info = {}
    
    for place_id, data in cache["servers"].items():
        age_minutes = (time.time() - data.get("timestamp", 0)) / 60
        info[place_id] = {
            "servers": len(data.get("servers", [])),
            "age_minutes": round(age_minutes, 2),
            "has_cursor": data.get("cursor") is not None,
            "is_valid": age_minutes < CACHE_EXPIRY_MINUTES
        }
    
    return jsonify({
        "cache": info,
        "last_request": cache.get("last_request", 0),
        "cooldown_seconds": REQUEST_COOLDOWN,
        "cache_expiry_minutes": CACHE_EXPIRY_MINUTES
    })

@app.route('/cache/clear', methods=['POST'])
def clear_cache():
    """Clear cache"""
    global cache
    
    place_id = request.args.get('placeId')
    
    if place_id:
        # Clear specific place
        if str(place_id) in cache["servers"]:
            del cache["servers"][str(place_id)]
            save_cache()
            return jsonify({"message": f"Cache cleared for place {place_id}"})
        else:
            return jsonify({"error": f"No cache for place {place_id}"}), 404
    else:
        # Clear all
        cache = {"servers": {}, "last_request": 0}
        save_cache()
        return jsonify({"message": "All cache cleared"})

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "uptime": time.time(),
        "cached_places": len(cache["servers"]),
        "port": PORT
    })

if __name__ == '__main__':
    # Load cache on startup
    load_cache()
    
    print("=" * 50)
    print("🍌 Banana Server Fetcher Started")
    print("=" * 50)
    print(f"Environment: {'Railway' if os.environ.get('RAILWAY_ENVIRONMENT') else 'Local'}")
    print(f"Port: {PORT}")
    print(f"Cache expiry: {CACHE_EXPIRY_MINUTES} minutes")
    print(f"Request cooldown: {REQUEST_COOLDOWN} seconds")
    print("=" * 50)
    
    # ✅ Run with PORT from environment, debug=False for production
    app.run(host='0.0.0.0', port=PORT, debug=False)

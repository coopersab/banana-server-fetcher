"""
🍌 Banana Server Fetcher - COMPLETE VERSION
✅ Auto-refills cache (250-500 servers)
✅ Filters full servers
✅ Rate limit protection
✅ Shuffles for variety
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import time
import json
import os
import random
from threading import Lock, Thread

app = Flask(__name__)
CORS(app)

PORT = int(os.environ.get("PORT", 5000))

# Configuration
ROBLOX_API_BASE = "https://games.roblox.com/v1/games"
CACHE_FILE = "server_cache.json"
CACHE_EXPIRY_MINUTES = int(os.environ.get("CACHE_EXPIRY_MINUTES", 45))
REQUEST_COOLDOWN = int(os.environ.get("REQUEST_COOLDOWN", 5))

MIN_CACHE_SIZE = 250
TARGET_CACHE_SIZE = 500

cache = {
    "servers": {},
    "last_request": 0,
    "last_rate_limit": 0
}
cache_lock = Lock()
fetch_in_progress = {}

def load_cache():
    global cache
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r') as f:
                cache = json.load(f)
            print(f"[Cache] Loaded cache from file")
        except Exception as e:
            print(f"[Cache] Error loading cache: {e}")
            cache = {"servers": {}, "last_request": 0, "last_rate_limit": 0}

def save_cache():
    try:
        with open(CACHE_FILE, 'w') as f:
            json.dump(cache, f, indent=2)
    except Exception as e:
        print(f"[Cache] Error saving cache: {e}")

def is_cache_valid(place_id):
    if str(place_id) not in cache["servers"]:
        return False
    
    place_cache = cache["servers"][str(place_id)]
    timestamp = place_cache.get("timestamp", 0)
    age_minutes = (time.time() - timestamp) / 60
    
    return age_minutes < CACHE_EXPIRY_MINUTES

def fetch_from_roblox(place_id, cursor=None, exclude_full=False):
    global cache
    
    with cache_lock:
        # Check if we're in cooldown from rate limit
        last_error_time = cache.get("last_rate_limit", 0)
        if time.time() - last_error_time < 60:
            wait_remaining = 60 - (time.time() - last_error_time)
            print(f"[RateLimit] Still in cooldown, {wait_remaining:.0f}s remaining")
            return {"error": "rate_limited", "retry_after": int(wait_remaining)}
        
        # Normal rate limiting
        time_since_last = time.time() - cache.get("last_request", 0)
        if time_since_last < REQUEST_COOLDOWN:
            wait_time = REQUEST_COOLDOWN - time_since_last
            print(f"[RateLimit] Waiting {wait_time:.2f}s...")
            time.sleep(wait_time)
        
        url = f"{ROBLOX_API_BASE}/{place_id}/servers/Public?limit=100"
        url += f"&excludeFullGames={str(exclude_full).lower()}"
        if cursor:
            url += f"&cursor={cursor}"
        
        print(f"[Roblox] Fetching servers...")
        
        try:
            response = requests.get(url, timeout=10)
            cache["last_request"] = time.time()
            
            if response.status_code == 200:
                data = response.json()
                cache["last_rate_limit"] = 0
                print(f"[Roblox] Success! Got {len(data.get('data', []))} servers")
                return data
            elif response.status_code == 429:
                cache["last_rate_limit"] = time.time()
                print(f"[Roblox] Rate limited! Cooling down for 60s")
                return {"error": "rate_limited", "retry_after": 60}
            else:
                print(f"[Roblox] Error: {response.status_code}")
                return {"error": f"http_error_{response.status_code}"}
                
        except requests.RequestException as e:
            print(f"[Roblox] Request failed: {e}")
            return {"error": str(e)}

def update_cache(place_id, servers, cursor=None):
    global cache
    
    place_id_str = str(place_id)
    
    if place_id_str not in cache["servers"]:
        cache["servers"][place_id_str] = {
            "servers": [],
            "cursor": None,
            "timestamp": time.time()
        }
    
    place_cache = cache["servers"][place_id_str]
    
    # ✅ FILTER AND PRIORITIZE SERVERS
    non_full_servers = []
    full_servers = []
    
    existing_ids = {s["id"] for s in place_cache["servers"]}
    
    for server in servers:
        if server["id"] in existing_ids:
            continue
            
        playing = server.get("playing", 0)
        max_players = server.get("maxPlayers", 8)  # ✅ Default to 8 for Steal a Brainrot
        
        # ✅ For 8-player servers, consider 7+ as full
        if max_players <= 10:  # Small servers (like Steal a Brainrot)
            if playing >= 7:  # 7-8 players = full
                full_servers.append(server)
            else:
                non_full_servers.append(server)
        else:  # Larger servers
            fill_percent = (playing / max_players) * 100
            if fill_percent >= 90:
                full_servers.append(server)
            else:
                non_full_servers.append(server)
    
    # ✅ SHUFFLE FOR RANDOMIZATION
    random.shuffle(non_full_servers)
    random.shuffle(full_servers)
    
    # ✅ PRIORITIZE NON-FULL SERVERS
    new_servers = non_full_servers + full_servers
    
    place_cache["servers"].extend(new_servers)
    place_cache["cursor"] = cursor
    place_cache["timestamp"] = time.time()
    
    if len(place_cache["servers"]) > TARGET_CACHE_SIZE:
        place_cache["servers"] = place_cache["servers"][:TARGET_CACHE_SIZE]
    
    save_cache()
    
    print(f"[Cache] Added {len(new_servers)} servers (non-full: {len(non_full_servers)}, full: {len(full_servers)}, total: {len(place_cache['servers'])})")
    return len(new_servers)

def background_refill_cache(place_id, exclude_full):
    place_id_str = str(place_id)
    
    if fetch_in_progress.get(place_id_str, False):
        return
    
    fetch_in_progress[place_id_str] = True
    
    try:
        print(f"[AutoRefill] Starting for {place_id}...")
        
        cursor = None
        if place_id_str in cache["servers"]:
            cursor = cache["servers"][place_id_str].get("cursor")
        
        attempts = 0
        max_attempts = 5
        
        while attempts < max_attempts:
            current_size = len(cache["servers"].get(place_id_str, {}).get("servers", []))
            
            if current_size >= TARGET_CACHE_SIZE:
                print(f"[AutoRefill] Cache full ({current_size}), stopping")
                break
            
            print(f"[AutoRefill] Fetching (current: {current_size})...")
            
            result = fetch_from_roblox(place_id, cursor, exclude_full)
            
            if "error" in result:
                print(f"[AutoRefill] Failed: {result['error']}")
                break
            
            servers = result.get("data", [])
            next_cursor = result.get("nextPageCursor")
            
            if not servers:
                print(f"[AutoRefill] No more servers")
                break
            
            added = update_cache(place_id, servers, next_cursor)
            
            if added == 0:
                if next_cursor:
                    cursor = next_cursor
                    attempts += 1
                    continue
                else:
                    break
            
            cursor = next_cursor
            
            if not cursor:
                break
            
            attempts += 1
            time.sleep(3)
        
        final_size = len(cache["servers"].get(place_id_str, {}).get("servers", []))
        print(f"[AutoRefill] Done! Cache: {final_size} servers")
        
    finally:
        fetch_in_progress[place_id_str] = False

@app.route('/')
def home():
    return jsonify({
        "status": "online",
        "service": "Banana Server Fetcher",
        "version": "3.0 - Complete",
        "features": ["Auto-refill", "Filter full servers", "Rate limit protection", "Shuffling"]
    })

@app.route('/servers', methods=['GET'])
def get_servers():
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
    
    if place_id_str not in cache["servers"]:
        cache["servers"][place_id_str] = {
            "servers": [],
            "cursor": None,
            "timestamp": time.time()
        }
    
    place_cache = cache["servers"][place_id_str]
    current_size = len(place_cache["servers"])
    
    # Auto-refill if low
    if current_size < MIN_CACHE_SIZE and is_cache_valid(place_id):
        print(f"[Cache] Low ({current_size}), auto-refilling...")
        Thread(target=background_refill_cache, args=(place_id, exclude_full), daemon=True).start()
    
    # Return from cache if available
    if current_size > 0 and is_cache_valid(place_id) and not force_refresh:
        servers = place_cache["servers"][:count]
        cache["servers"][place_id_str]["servers"] = place_cache["servers"][count:]
        save_cache()
        
        remaining = len(cache["servers"][place_id_str]["servers"])
        
        return jsonify({
            "source": "cache",
            "placeId": place_id,
            "servers": servers,
            "count": len(servers),
            "remaining": remaining
        })
    
    # Fetch fresh
    print(f"[API] Fetching fresh for {place_id}...")
    
    cursor = place_cache.get("cursor")
    result = fetch_from_roblox(place_id, cursor, exclude_full)
    
    if "error" in result:
        return jsonify({
            "error": result["error"],
            "retry_after": result.get("retry_after", 60)
        }), 429 if result["error"] == "rate_limited" else 500
    
    servers = result.get("data", [])
    next_cursor = result.get("nextPageCursor")
    
    update_cache(place_id, servers, next_cursor)
    
    return_servers = servers[:count]
    
    # Trigger refill if needed
    if len(cache["servers"][place_id_str]["servers"]) < MIN_CACHE_SIZE:
        Thread(target=background_refill_cache, args=(place_id, exclude_full), daemon=True).start()
    
    return jsonify({
        "source": "roblox",
        "placeId": place_id,
        "servers": return_servers,
        "count": len(return_servers)
    })

@app.route('/cache/info', methods=['GET'])
def cache_info():
    info = {}
    
    for place_id, data in cache["servers"].items():
        age_minutes = (time.time() - data.get("timestamp", 0)) / 60
        info[place_id] = {
            "servers": len(data.get("servers", [])),
            "age_minutes": round(age_minutes, 2),
            "is_valid": age_minutes < CACHE_EXPIRY_MINUTES,
            "fetching": fetch_in_progress.get(place_id, False)
        }
    
    return jsonify({
        "cache": info,
        "cooldown_seconds": REQUEST_COOLDOWN,
        "min_cache": MIN_CACHE_SIZE,
        "target_cache": TARGET_CACHE_SIZE
    })

@app.route('/cache/clear', methods=['POST'])
def clear_cache():
    global cache
    
    place_id = request.args.get('placeId')
    
    if place_id:
        if str(place_id) in cache["servers"]:
            del cache["servers"][str(place_id)]
            save_cache()
            return jsonify({"message": f"Cleared {place_id}"})
        return jsonify({"error": f"No cache for {place_id}"}), 404
    else:
        cache = {"servers": {}, "last_request": 0, "last_rate_limit": 0}
        save_cache()
        return jsonify({"message": "All cache cleared"})

@app.route('/health', methods=['GET'])
def health():
    total = sum(len(d.get("servers", [])) for d in cache["servers"].values())
    return jsonify({
        "status": "healthy",
        "cached_places": len(cache["servers"]),
        "total_servers": total
    })

if __name__ == '__main__':
    load_cache()
    
    print("=" * 60)
    print("🍌 Banana Server Fetcher v3.0 COMPLETE")
    print("=" * 60)
    print(f"Port: {PORT}")
    print(f"Cache expiry: {CACHE_EXPIRY_MINUTES} min")
    print(f"Request cooldown: {REQUEST_COOLDOWN}s")
    print(f"Cache size: {MIN_CACHE_SIZE}-{TARGET_CACHE_SIZE} servers")
    print("✅ Auto-refill enabled")
    print("✅ Full server filtering enabled")
    print("✅ Rate limit protection enabled")
    print("=" * 60)
    
    app.run(host='0.0.0.0', port=PORT, debug=False)

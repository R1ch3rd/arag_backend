# Enhanced cache.py with user cache clearing support
import requests
import json
import hashlib
from typing import Optional, Dict, Any, List
from .config import config

class CacheClient:
    def __init__(self):
        self.base_url = config.UPSTASH_REDIS_URL
        self.headers = {
            "Authorization": f"Bearer {config.UPSTASH_REDIS_TOKEN}"
        }
    
    def _make_request(self, command: List[str]) -> Any:
        """Make request to Upstash Redis REST API"""
        try:
            response = requests.post(
                f"{self.base_url}",
                headers=self.headers,
                json=command
            )
            if response.status_code != 200:
                print(f"Redis error: {response.status_code} - {response.text}")
                raise Exception(f"Redis error: {response.text}")
            return response.json().get('result')
        except Exception as e:
            print(f"Redis request failed: {str(e)}")
            raise
    
    def get(self, key: str) -> Optional[str]:
        """Get value from cache"""
        try:
            result = self._make_request(["GET", key])
            return json.loads(result) if result else None
        except:
            return None
    
    def set(self, key: str, value: Any, ttl: int = 3600):
        """Set value in cache with TTL"""
        try:
            json_value = json.dumps(value)
            self._make_request(["SETEX", key, str(ttl), json_value])
        except Exception as e:
            print(f"Failed to set cache key {key}: {e}")
    
    def incr(self, key: str) -> int:
        """Increment counter"""
        return self._make_request(["INCR", key])
    
    def expire(self, key: str, ttl: int):
        """Set expiration on key"""
        self._make_request(["EXPIRE", key, str(ttl)])
    
    def delete(self, key: str):
        """Delete key from cache"""
        try:
            self._make_request(["DEL", key])
        except Exception as e:
            print(f"Failed to delete cache key {key}: {e}")
    
    def keys(self, pattern: str) -> List[str]:
        """Get keys matching pattern"""
        try:
            result = self._make_request(["KEYS", pattern])
            return result if result else []
        except Exception as e:
            print(f"Failed to get keys for pattern {pattern}: {e}")
            return []
    
    def delete_multiple(self, keys: List[str]):
        """Delete multiple keys at once"""
        if not keys:
            return
        
        try:
            # Use DEL command with multiple keys
            command = ["DEL"] + keys
            self._make_request(command)
        except Exception as e:
            print(f"Failed to delete multiple keys: {e}")
    
    def clear_user_cache(self, user_id: str):
        """Clear all cache entries for a specific user"""
        try:
            # Get all user-related cache keys
            patterns = [
                f"query:{user_id}:*",
                f"rate_limit:{user_id}:*",
                f"user:{user_id}:*"
            ]
            
            all_keys = []
            for pattern in patterns:
                keys = self.keys(pattern)
                all_keys.extend(keys)
            
            if all_keys:
                # Delete all keys in batches (Upstash has limits on command size)
                batch_size = 100
                for i in range(0, len(all_keys), batch_size):
                    batch = all_keys[i:i + batch_size]
                    self.delete_multiple(batch)
                
                print(f"Cleared {len(all_keys)} cache entries for user {user_id}")
            else:
                print(f"No cache entries found for user {user_id}")
                
        except Exception as e:
            print(f"Error clearing user cache for {user_id}: {e}")
    
    def check_rate_limit(self, user_id: str, action: str, limit: int, window: int = 3600) -> bool:
        """Check if user has exceeded rate limit"""
        try:
            key = f"rate_limit:{user_id}:{action}"
            count = self.incr(key)
            
            if count == 1:
                self.expire(key, window)
            
            return count <= limit
        except Exception as e:
            print(f"Rate limit check failed for {user_id}:{action}: {e}")
            return True  # Allow request if rate limit check fails
    
    def cache_query_result(self, user_id: str, query: str, result: Dict, ttl: int = 86400):
        """Cache query result"""
        try:
            query_hash = hashlib.md5(query.encode()).hexdigest()
            key = f"query:{user_id}:{query_hash}"
            self.set(key, result, ttl)
        except Exception as e:
            print(f"Failed to cache query result for {user_id}: {e}")
    
    def get_cached_query(self, user_id: str, query: str) -> Optional[Dict]:
        """Get cached query result"""
        try:
            query_hash = hashlib.md5(query.encode()).hexdigest()
            key = f"query:{user_id}:{query_hash}"
            return self.get(key)
        except Exception as e:
            print(f"Failed to get cached query for {user_id}: {e}")
            return None
    
    def cache_session_data(self, user_id: str, session_id: str, data: Dict, ttl: int = 3600):
        """Cache session-specific data"""
        try:
            key = f"session:{user_id}:{session_id}"
            self.set(key, data, ttl)
        except Exception as e:
            print(f"Failed to cache session data: {e}")
    
    def get_cached_session_data(self, user_id: str, session_id: str) -> Optional[Dict]:
        """Get cached session data"""
        try:
            key = f"session:{user_id}:{session_id}"
            return self.get(key)
        except Exception as e:
            print(f"Failed to get cached session data: {e}")
            return None
    
    def clear_session_cache(self, user_id: str, session_id: str):
        """Clear cache for a specific session"""
        try:
            pattern = f"session:{user_id}:{session_id}*"
            keys = self.keys(pattern)
            
            if keys:
                self.delete_multiple(keys)
                print(f"Cleared {len(keys)} cache entries for session {session_id}")
        except Exception as e:
            print(f"Failed to clear session cache: {e}")
    
    def get_cache_stats(self, user_id: str) -> Dict[str, int]:
        """Get cache statistics for a user"""
        try:
            patterns = [
                f"query:{user_id}:*",
                f"rate_limit:{user_id}:*",
                f"session:{user_id}:*",
                f"user:{user_id}:*"
            ]
            
            stats = {}
            for pattern in patterns:
                cache_type = pattern.split(':')[0]
                keys = self.keys(pattern)
                stats[cache_type] = len(keys)
            
            return stats
        except Exception as e:
            print(f"Failed to get cache stats: {e}")
            return {}

cache = CacheClient()
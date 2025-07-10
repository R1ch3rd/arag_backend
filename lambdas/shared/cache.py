import requests
import json
import hashlib
from typing import Optional, Dict, Any,List
from .config import config

class CacheClient:
    def __init__(self):
        self.base_url = config.UPSTASH_REDIS_URL
        self.headers = {
            "Authorization": f"Bearer {config.UPSTASH_REDIS_TOKEN}"
        }
    
    def _make_request(self, command: List[str]) -> Any:
        """Make request to Upstash Redis REST API"""
        response = requests.post(
            f"{self.base_url}",
            headers=self.headers,
            json=command
        )
        if response.status_code != 200:
            raise Exception(f"Redis error: {response.text}")
        return response.json()['result']
    
    def get(self, key: str) -> Optional[str]:
        """Get value from cache"""
        try:
            result = self._make_request(["GET", key])
            return json.loads(result) if result else None
        except:
            return None
    
    def set(self, key: str, value: Any, ttl: int = 3600):
        """Set value in cache with TTL"""
        json_value = json.dumps(value)
        self._make_request(["SETEX", key, str(ttl), json_value])
    
    def incr(self, key: str) -> int:
        """Increment counter"""
        return self._make_request(["INCR", key])
    
    def expire(self, key: str, ttl: int):
        """Set expiration on key"""
        self._make_request(["EXPIRE", key, str(ttl)])
    
    def delete(self, key: str):
        """Delete key from cache"""
        self._make_request(["DEL", key])
    
    def check_rate_limit(self, user_id: str, action: str, limit: int, window: int = 3600) -> bool:
        """Check if user has exceeded rate limit"""
        key = f"rate_limit:{user_id}:{action}"
        count = self.incr(key)
        
        if count == 1:
            self.expire(key, window)
        
        return count <= limit
    
    def cache_query_result(self, user_id: str, query: str, result: Dict, ttl: int = 86400):
        """Cache query result"""
        query_hash = hashlib.md5(query.encode()).hexdigest()
        key = f"query:{user_id}:{query_hash}"
        self.set(key, result, ttl)
    
    def get_cached_query(self, user_id: str, query: str) -> Optional[Dict]:
        """Get cached query result"""
        query_hash = hashlib.md5(query.encode()).hexdigest()
        key = f"query:{user_id}:{query_hash}"
        return self.get(key)

cache = CacheClient()

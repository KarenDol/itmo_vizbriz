"""
Cache service for performance optimization.
Provides caching for frequently accessed data while maintaining backward compatibility.
"""

import json
import logging
from functools import wraps
from flask import current_app

logger = logging.getLogger(__name__)

class CacheService:
    """Service for caching frequently accessed data."""
    
    # Simple in-memory cache (can be replaced with Redis later)
    _cache = {}
    _cache_timeout = 300  # 5 minutes
    
    @staticmethod
    def get_cache_key(prefix, *args):
        """Generate cache key from prefix and arguments."""
        return f"{prefix}:{':'.join(str(arg) for arg in args)}"
    
    @staticmethod
    def get(key):
        """Get value from cache."""
        try:
            if key in CacheService._cache:
                entry = CacheService._cache[key]
                expires_at = entry.get('expires_at')
                
                # If expires_at is None, entry never expires (last known good value)
                if expires_at is None:
                    return entry.get('value')
                
                # Check if cache entry has expired
                if expires_at > CacheService._get_current_time():
                    return entry.get('value')
                else:
                    # Remove expired entry (but keep last known good values)
                    if not key.endswith('_last'):
                        del CacheService._cache[key]
            return None
        except Exception as e:
            logger.error(f"Error getting cache key {key}: {e}")
            return None
    
    @staticmethod
    def set(key, value, timeout=None):
        """Set value in cache. If timeout is None, entry never expires."""
        try:
            if timeout is None:
                # Store without expiration (last known good value)
                CacheService._cache[key] = {
                    'value': value,
                    'expires_at': None  # Never expires
                }
            else:
                CacheService._cache[key] = {
                    'value': value,
                    'expires_at': CacheService._get_current_time() + timeout
                }
        except Exception as e:
            logger.error(f"Error setting cache key {key}: {e}")
    
    @staticmethod
    def delete(key):
        """Delete value from cache."""
        try:
            if key in CacheService._cache:
                del CacheService._cache[key]
        except Exception as e:
            logger.error(f"Error deleting cache key {key}: {e}")
    
    @staticmethod
    def clear():
        """Clear all cache entries."""
        try:
            CacheService._cache.clear()
        except Exception as e:
            logger.error(f"Error clearing cache: {e}")
    
    @staticmethod
    def _get_current_time():
        """Get current timestamp."""
        import time
        return time.time()
    
    @staticmethod
    def cached_execution_manifest(patient_id, force_refresh=False):
        """Get cached execution manifest or load and cache it."""
        cache_key = CacheService.get_cache_key('execution_manifest', patient_id)
        
        # If force refresh, delete cache first
        if force_refresh:
            logger.info(f"Force refresh requested - deleting execution manifest cache for patient {patient_id}")
            CacheService.delete(cache_key)
            return None
        
        # Try to get from cache first
        cached_data = CacheService.get(cache_key)
        if cached_data is not None:
            logger.info(f"Cache hit for execution manifest patient {patient_id}")
            return cached_data
        
        # Load from source and cache
        try:
            from flask_app.routes.cursor_routes import get_execution_manifest
            manifest_data = get_execution_manifest(patient_id)
            
            # Handle Flask Response objects
            if hasattr(manifest_data, 'get_json'):
                manifest_data = manifest_data.get_json()
            
            # Cache the result
            CacheService.set(cache_key, manifest_data, timeout=300)  # 5 minutes
            logger.info(f"Cached execution manifest for patient {patient_id}")
            
            return manifest_data
            
        except Exception as e:
            logger.error(f"Error loading execution manifest for patient {patient_id}: {e}")
            return None
    
    @staticmethod
    def cached_canonical_data(patient_id):
        """Get cached canonical data or load and cache it."""
        cache_key = CacheService.get_cache_key('canonical_data', patient_id)
        
        # Try to get from cache first
        cached_data = CacheService.get(cache_key)
        if cached_data is not None:
            logger.info(f"Cache hit for canonical data patient {patient_id}")
            return cached_data
        
        # Load from source and cache
        try:
            from flask_app.services.performance_service import PerformanceService
            canonical_data = PerformanceService.get_canonical_data_optimized(patient_id)
            
            # Do not cache misses: caching None hides a later PatientCaseEnvelope insert for up
            # to 10 minutes (e.g. after direct sleep canonical rebuild).
            if canonical_data is not None:
                CacheService.set(cache_key, canonical_data, timeout=600)  # 10 minutes
                logger.info(f"Cached canonical data for patient {patient_id}")
            else:
                logger.info(
                    "Canonical data missing for patient %s; not caching None",
                    patient_id,
                )
            
            return canonical_data
            
        except Exception as e:
            logger.error(f"Error loading canonical data for patient {patient_id}: {e}")
            return None
    
    @staticmethod
    def cached_llm_data(patient_id, force_refresh=False):
        """Get cached LLM data (clinical and operational summaries) or load and cache it."""
        cache_key = CacheService.get_cache_key('llm_data', patient_id)
        last_known_key = CacheService.get_cache_key('llm_data_last', patient_id)
        
        # If force refresh, delete cache first but keep last known good value
        if force_refresh:
            logger.info(f"Force refresh requested - deleting LLM data cache for patient {patient_id}")
            CacheService.delete(cache_key)
            # Don't delete last_known_key - keep it as fallback
        
        # Try to get from cache first
        cached_data = CacheService.get(cache_key)
        if cached_data is not None:
            logger.info(f"Cache hit for LLM data patient {patient_id}")
            return cached_data
        
        # If cache expired, try to get last known good value
        last_known_data = CacheService.get(last_known_key)
        if last_known_data is not None:
            logger.info(f"Using last known LLM data for patient {patient_id} (cache expired)")
            return last_known_data
        
        # Return None - caller should generate and cache the data
        return None
    
    @staticmethod
    def set_llm_data(patient_id, llm_data, timeout=300):
        """Cache LLM data for a patient. Also stores as last known good value."""
        cache_key = CacheService.get_cache_key('llm_data', patient_id)
        last_known_key = CacheService.get_cache_key('llm_data_last', patient_id)
        
        # Cache with timeout (for active use)
        CacheService.set(cache_key, llm_data, timeout=timeout)
        
        # Also store as last known good value (no expiration - kept until new data arrives)
        CacheService.set(last_known_key, llm_data, timeout=None)  # No expiration
        logger.info(f"Cached LLM data for patient {patient_id} (active cache + last known)")
    
    @staticmethod
    def invalidate_patient_cache(patient_id):
        """Invalidate all cache entries for a specific patient."""
        try:
            # List of cache key prefixes to invalidate
            prefixes = ['execution_manifest', 'canonical_data', 'patient_data', 'llm_data']
            
            for prefix in prefixes:
                cache_key = CacheService.get_cache_key(prefix, patient_id)
                CacheService.delete(cache_key)
            
            logger.info(f"Invalidated cache for patient {patient_id}")
            
        except Exception as e:
            logger.error(f"Error invalidating cache for patient {patient_id}: {e}")

def cache_result(timeout=300):
    """Decorator to cache function results."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Generate cache key from function name and arguments
            cache_key = CacheService.get_cache_key(func.__name__, *args, *kwargs.values())
            
            # Try to get from cache
            cached_result = CacheService.get(cache_key)
            if cached_result is not None:
                return cached_result
            
            # Execute function and cache result
            result = func(*args, **kwargs)
            CacheService.set(cache_key, result, timeout=timeout)
            
            return result
        return wrapper
    return decorator
